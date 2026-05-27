"""Sémantické porovnání dvou Eskymo ODS souborů.

Pro každý sheet vrátí seznam rozdílů s jedním ze tří typů:
  - only_in_script: bib je ve výstupu skriptu, ne v ručním souboru
  - only_in_manual: opačně
  - value_diff: bib v obou, ale liší se rgc nebo některý čas/pen

Sjednocuje normalizaci hodnot (rgc jako string, časy s 3 desetinnými).
Lze použít jako modul (`compare_files()`) nebo CLI.
"""
from __future__ import annotations

import sys
import io
from odf.opendocument import load
from odf.table import Table, TableRow, TableCell
from odf import teletype


CLASSES = ["c1m", "c1z", "k1m", "k1z", "c2m", "c2z", "c2x", "pzk", "pzc"]


def _get_sheet(doc, name):
    for t in doc.spreadsheet.getElementsByType(Table):
        if t.getAttribute("name") == name:
            return t
    return None


def _cells_in_row(row):
    out = []
    for c in row.getElementsByType(TableCell):
        rep = int(c.getAttribute("numbercolumnsrepeated") or 1)
        for _ in range(rep):
            out.append(c)
    return out


def _cell_value(cell):
    val = cell.getAttribute("value")
    if val is not None:
        try:
            return float(val)
        except ValueError:
            return val
    return teletype.extractText(cell).strip()


def _read_startlist(doc, name):
    sheet = _get_sheet(doc, name)
    if sheet is None:
        return None
    out = {}
    for row in list(sheet.getElementsByType(TableRow))[2:]:
        c = _cells_in_row(row)
        if len(c) < 3:
            continue
        rid = _cell_value(c[0])
        bib = _cell_value(c[1])
        rgc = _cell_value(c[2])
        if bib in (None, "", 0, 0.0) and rgc in (None, "", 0, 0.0):
            continue
        bib_n = _norm(bib)
        if bib_n is None:
            continue
        out[bib_n] = {"id": _norm(rid), "rgc": _norm_rgc(rgc)}
    return out


def _read_results(doc, name, startlist):
    """Mapuje id → bib (přes startlist), pak vrací bib → časy/pen."""
    sheet = _get_sheet(doc, name)
    if sheet is None:
        return None
    if not startlist:
        return {}
    id_to_bib = {v["id"]: bib for bib, v in startlist.items()}
    out = {}
    for row in list(sheet.getElementsByType(TableRow))[2:]:
        c = _cells_in_row(row)
        if len(c) < 16:
            continue
        rid = _norm(_cell_value(c[0]))
        bib = id_to_bib.get(rid)
        if bib is None:
            continue
        out[bib] = {
            "time1": _norm(_cell_value(c[11])),
            "pen1": _norm(_cell_value(c[12])),
            "time2": _norm(_cell_value(c[14])),
            "pen2": _norm(_cell_value(c[15])),
        }
    return out


def _norm(v):
    if v is None or v == "":
        return None
    if isinstance(v, float):
        if v == int(v):
            return int(v)
        return round(v, 3)
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s.replace(",", "."))
        if f == int(f):
            return int(f)
        return round(f, 3)
    except ValueError:
        return s


def _norm_rgc(v):
    if v is None or v == "":
        return None
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(v)
    return str(v).strip()


def _diff_dict(script, manual, sheet, fields):
    diffs = []
    script_keys = set(script)
    manual_keys = set(manual)
    for key in sorted(script_keys - manual_keys, key=lambda x: (isinstance(x, str), x)):
        diffs.append({"sheet": sheet, "type": "only_in_script", "key": key})
    for key in sorted(manual_keys - script_keys, key=lambda x: (isinstance(x, str), x)):
        diffs.append({"sheet": sheet, "type": "only_in_manual", "key": key})
    for key in sorted(script_keys & manual_keys, key=lambda x: (isinstance(x, str), x)):
        for f in fields:
            sv = script[key].get(f)
            mv = manual[key].get(f)
            if sv != mv:
                diffs.append({
                    "sheet": sheet, "type": "value_diff", "key": key,
                    "field": f, "script": sv, "manual": mv,
                })
    return diffs


def compare_files(script_path: str, manual_path: str, kind: str = "slalom") -> list[dict]:
    """Vrátí list diff záznamů. Prázdný = shoda.

    kind = 'slalom' (default) nebo 'cross'.
    """
    script_doc = load(script_path)
    manual_doc = load(manual_path)
    if kind == "cross":
        return _compare_cross(script_doc, manual_doc)
    return _compare_slalom(script_doc, manual_doc)


def _compare_slalom(script_doc, manual_doc) -> list[dict]:
    all_diffs: list[dict] = []
    for cls in CLASSES:
        sl_name = cls + "_sl"
        script_sl = _read_startlist(script_doc, sl_name)
        manual_sl = _read_startlist(manual_doc, sl_name)
        if script_sl is None and manual_sl is None:
            continue
        if script_sl is None or manual_sl is None:
            all_diffs.append({
                "sheet": sl_name, "type": "sheet_existence",
                "script": script_sl is not None, "manual": manual_sl is not None,
            })
            continue
        all_diffs.extend(_diff_dict(script_sl, manual_sl, sl_name, ["rgc"]))

        script_res = _read_results(script_doc, cls, script_sl)
        manual_res = _read_results(manual_doc, cls, manual_sl)
        if script_res is None or manual_res is None:
            continue
        all_diffs.extend(_diff_dict(script_res, manual_res, cls,
                                     ["time1", "pen1", "time2", "pen2"]))
    return all_diffs


# -------- cross sheets --------

CROSS_Q_SUFFIXES = ("-Q", "-indiv.", "-indiv")
CROSS_F_SUFFIXES = ("-F", "-F-JUN")
# Cross sheet layout (z výzkumu CP3 manual outputs)
CROSS_COL_BIB = 3
CROSS_COL_RGC = 4
CROSS_COL_TIME_Q = 11
CROSS_COL_FINAL_RANK = 13


def _cross_sheet_kind(name: str) -> str | None:
    """Vrátí 'Q' nebo 'F' pokud sheet vypadá jako cross, jinak None."""
    for s in CROSS_F_SUFFIXES:
        if name.endswith(s):
            return "F"
    for s in CROSS_Q_SUFFIXES:
        if name.endswith(s):
            return "Q"
    return None


def _read_cross_sheet(doc, name: str, kind: str):
    """Vrátí dict bib → {rgc, time/final_rank}."""
    sheet = _get_sheet(doc, name)
    if sheet is None:
        return None
    value_col = CROSS_COL_TIME_Q if kind == "Q" else CROSS_COL_FINAL_RANK
    value_key = "time" if kind == "Q" else "final_rank"
    out = {}
    for row in list(sheet.getElementsByType(TableRow))[2:]:
        c = _cells_in_row(row)
        max_needed = max(CROSS_COL_BIB, CROSS_COL_RGC, value_col)
        if len(c) <= max_needed:
            continue
        bib = _cell_value(c[CROSS_COL_BIB])
        rgc = _cell_value(c[CROSS_COL_RGC])
        val = _cell_value(c[value_col])
        bib_n = _norm(bib)
        if bib_n is None:
            continue
        if bib_n in out:
            continue  # první nález vítězí
        out[bib_n] = {
            "rgc": _norm_rgc(rgc),
            value_key: _norm(val),
        }
    return out


def _compare_cross(script_doc, manual_doc) -> list[dict]:
    """Porovná cross sheety. Iteruje přes sheety v obou souborech."""
    all_diffs: list[dict] = []
    script_names = {t.getAttribute("name") for t in script_doc.spreadsheet.getElementsByType(Table)}
    manual_names = {t.getAttribute("name") for t in manual_doc.spreadsheet.getElementsByType(Table)}
    names = sorted(script_names | manual_names)
    for name in names:
        kind = _cross_sheet_kind(name)
        if kind is None:
            continue
        script_data = _read_cross_sheet(script_doc, name, kind) if name in script_names else None
        manual_data = _read_cross_sheet(manual_doc, name, kind) if name in manual_names else None
        if script_data is None and manual_data is None:
            continue
        if script_data is None or manual_data is None:
            all_diffs.append({
                "sheet": name, "type": "sheet_existence",
                "script": script_data is not None, "manual": manual_data is not None,
            })
            continue
        fields = ["rgc", "time"] if kind == "Q" else ["rgc", "final_rank"]
        all_diffs.extend(_diff_dict(script_data, manual_data, name, fields))
    return all_diffs


def format_diff(d: dict) -> str:
    if d["type"] == "value_diff":
        return (f"[{d['sheet']}] bib {d['key']} {d['field']}: "
                f"script={d['script']!r} manual={d['manual']!r}")
    if d["type"] == "only_in_script":
        return f"[{d['sheet']}] pouze v script: bib {d['key']}"
    if d["type"] == "only_in_manual":
        return f"[{d['sheet']}] pouze v manual: bib {d['key']}"
    return f"[{d['sheet']}] {d}"


def main():
    if len(sys.argv) < 3:
        print("Usage: compare.py <script_output.ods> <manual_reference.ods> [slalom|cross]")
        sys.exit(2)
    kind = sys.argv[3] if len(sys.argv) > 3 else "slalom"
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    diffs = compare_files(sys.argv[1], sys.argv[2], kind=kind)
    if not diffs:
        print("OK: žádné rozdíly")
        return
    by_sheet: dict[str, list] = {}
    for d in diffs:
        by_sheet.setdefault(d["sheet"], []).append(d)
    for sheet in sorted(by_sheet):
        ds = by_sheet[sheet]
        print(f"[{sheet}] {len(ds)} rozdílů")
        for d in ds[:20]:
            print(f"  {format_diff(d)}")
        if len(ds) > 20:
            print(f"  … +{len(ds) - 20} dalších")


if __name__ == "__main__":
    main()
