"""
cross — naplní Eskymo ODS šablonu daty z Canoe123 XML pro cross sprint.

Použití:
    py cross.py <xml> <empty.ods> <output.ods> --day 26

Skript zpracuje cross sprint výsledky (ClassId obsahuje 'X1', např. MX1,
WX1, MX1J, X1M-ZS, X1Z-ZM). Pro každou kategorii vyplní:

  - **Q sheet** (`<class>-Q` / `<class>-indiv.`): kvalifikace
    Seřazeno podle XT Rnk. Sloupce: poř., vk, jun.-marker, bib XT, rgc, čas.
    DNS na konci bez poř.

  - **F sheet** (`<class>-F` / `<class>-F-JUN`): finálová tabulka
    Top finalisté (z XER) první s bib v semifinále.
    Pak ne-finalisté seřazení podle XT času, s "t {bib_XT}" v bib sloupci.
    DNS úplně na konci.

Junior marker ('jun.' ve sloupci 2): závodník je v dospělé F/Q sheetu
junior, pokud je registrovaný taky v junior třídě (MX1J / WX1J).

Tento skript je oddělený od `canoe2eskymo.py` (slalom).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from xml.etree import ElementTree as ET

from odf.opendocument import load
from odf.table import Table, TableRow, TableCell
from odf.text import P
from odf import teletype


XML_NS = "{http://siwidata.com/Canoe123/Data.xsd}"

# Cross sprint disciplíny (DisId)
DIS_XT = "XT"    # individuálka / kvalifikace (Individual Time Trial)
DIS_XS = "XS"    # semifinále (pavouk start)
DIS_XF = "XF"    # finále
DIS_XER = "XER"  # celkové výsledky (Results)

# Sloupce v Eskymo cross sheetech (po výzkumu Troja CP3 ručních souborů)
COL_POR = 0       # pořadí
COL_VK = 1        # věk. kat.
COL_JUN = 2       # 'jun.' marker
COL_BIB = 3       # stč (bib v dané fázi)
COL_RGC = 4       # rgc / ICFId
COL_TIME_Q = 11   # čas v kvalifikaci (Q sheet) nebo XT čas pro ne-finalisty v F sheet
COL_FINAL_RANK = 13  # finále rank (XER)


# -------- XML parsing --------

def _text(elem, tag) -> str:
    el = elem.find(XML_NS + tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _int(elem, tag) -> int | None:
    s = _text(elem, tag)
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_xml(xml_path: str):
    """Vrátí (participants_by_class, results, schedules)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    participants_by_class: dict[str, list[dict]] = defaultdict(list)
    results: dict[tuple[str, str], dict] = {}
    schedules: dict[str, dict] = {}

    for elem in root:
        tag = elem.tag.replace(XML_NS, "")
        if tag == "Participants":
            cls = _text(elem, "ClassId")
            if not _is_cross_class(cls):
                continue
            participants_by_class[cls].append({
                "id": _text(elem, "Id"),
                "class": cls,
                "icf": _text(elem, "ICFId"),
                "family": _text(elem, "FamilyName"),
                "given": _text(elem, "GivenName"),
                "club": _text(elem, "Club"),
                "year": _text(elem, "Year") or _year_from_birthdate(elem, "Birthdate"),
            })
        elif tag == "Schedule":
            race_id = _text(elem, "RaceId")
            if not race_id:
                continue
            schedules[race_id] = {
                "class": _text(elem, "ClassId"),
                "dis": _text(elem, "DisId"),
                "attr": _text(elem, "AttributeId"),
            }
        elif tag == "Results":
            race_id = _text(elem, "RaceId")
            participant_id = _text(elem, "Id")
            if not race_id or not participant_id:
                continue
            results[(race_id, participant_id)] = {
                "bib": _int(elem, "Bib"),
                "time_ms": _int(elem, "Time"),
                "pen": _int(elem, "Pen"),
                "rnk": _int(elem, "Rnk") or _int(elem, "RnkOrder"),
                "status": _text(elem, "Status"),
            }
    return participants_by_class, results, schedules


def _is_cross_class(cls: str) -> bool:
    """Cross třídy mají 'X1' v názvu (MX1, WX1, MX1J, X1M-ZS, X1Z-ZM, …)."""
    return "X1" in cls and "C2X" not in cls


def _is_junior_class(cls: str) -> bool:
    """Junior třída (suffix J nebo obsahuje JUN)."""
    return cls.endswith("J") or "JUN" in cls or "ZM" in cls or "ZS" in cls and "X" in cls and False
    # Pozn.: -ZM/-ZS jsou žákovské kategorie (samostatné třídy), ne junior marker v dospělé třídě.
    # Junior marker se používá jen pokud paddler je v dospělé třídě (MX1) a zároveň junior (MX1J).


def _adult_class_of(junior_cls: str) -> str:
    """Vrátí dospělou variantu junior třídy. MX1J → MX1, WX1J → WX1."""
    if junior_cls.endswith("J"):
        return junior_cls[:-1]
    return junior_cls


def _year_from_birthdate(elem, tag) -> str:
    s = _text(elem, tag)
    if not s:
        return ""
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


# -------- ODS helpers --------

def get_sheet(doc, name: str) -> Table | None:
    for t in doc.spreadsheet.getElementsByType(Table):
        if t.getAttribute("name") == name:
            return t
    return None


def all_sheet_names(doc) -> list[str]:
    return [t.getAttribute("name") for t in doc.spreadsheet.getElementsByType(Table)]


def _split_repeated_cells_until(row: TableRow, target_col: int) -> None:
    cells = list(row.getElementsByType(TableCell))
    col_idx = 0
    for cell in cells:
        rep = int(cell.getAttribute("numbercolumnsrepeated") or 1)
        if col_idx <= target_col < col_idx + rep:
            if rep == 1:
                return
            before = target_col - col_idx
            after = rep - before - 1
            parent = cell.parentNode
            new_cells: list[TableCell] = []
            if before > 0:
                c1 = _clone_empty_like(cell)
                if before > 1:
                    c1.setAttribute("numbercolumnsrepeated", str(before))
                new_cells.append(c1)
            c2 = _clone_empty_like(cell)
            new_cells.append(c2)
            if after > 0:
                c3 = _clone_empty_like(cell)
                if after > 1:
                    c3.setAttribute("numbercolumnsrepeated", str(after))
                new_cells.append(c3)
            for nc in new_cells:
                parent.insertBefore(nc, cell)
            parent.removeChild(cell)
            return
        col_idx += rep
    missing = target_col - col_idx
    if missing > 0:
        spacer = TableCell()
        if missing > 1:
            spacer.setAttribute("numbercolumnsrepeated", str(missing))
        row.addElement(spacer)
    row.addElement(TableCell())


def _clone_empty_like(cell: TableCell) -> TableCell:
    new = TableCell()
    style = cell.getAttribute("stylename")
    if style:
        new.setAttribute("stylename", style)
    return new


def get_cell_at(row: TableRow, target_col: int) -> TableCell:
    _split_repeated_cells_until(row, target_col)
    col_idx = 0
    for cell in row.getElementsByType(TableCell):
        rep = int(cell.getAttribute("numbercolumnsrepeated") or 1)
        if col_idx == target_col:
            return cell
        col_idx += rep
    raise RuntimeError(f"Buňka na sloupci {target_col} nenalezena")


def set_cell_float(cell: TableCell, value: float) -> None:
    cell.setAttribute("valuetype", "float")
    cell.setAttribute("value", str(value))
    for child in list(cell.childNodes):
        cell.removeChild(child)
    cell.addElement(P(text=_fmt_float(value)))


def set_cell_string(cell: TableCell, value: str) -> None:
    cell.setAttribute("valuetype", "string")
    if cell.getAttribute("value"):
        cell.removeAttribute("value")
    for child in list(cell.childNodes):
        cell.removeChild(child)
    cell.addElement(P(text=value))


def clear_cell(cell: TableCell) -> None:
    for attr in ("value", "valuetype", "formula", "stringvalue", "datevalue",
                 "timevalue", "booleanvalue", "currency"):
        if cell.getAttribute(attr):
            cell.removeAttribute(attr)
    for child in list(cell.childNodes):
        cell.removeChild(child)


def _fmt_float(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _cell_text(cell: TableCell) -> str:
    return teletype.extractText(cell)


# -------- sheet name matching --------

def _permute_class(cls: str) -> str:
    """MX1 → X1M, WX1J → X1WJ. Beze změny pro jiné formáty."""
    m = re.fullmatch(r"([MW])X1(.*)", cls)
    if m:
        return f"X1{m.group(1)}{m.group(2)}"
    return cls


def find_sheet_for(doc, cls: str, sheet_type: str) -> Table | None:
    """Najde sheet pro danou třídu a typ ('Q' = kvalifikace, 'F' = finále)."""
    is_junior = cls.endswith("J")
    candidates: list[str] = []
    if is_junior:
        base = cls[:-1]
        permuted_base = _permute_class(base)
        permuted_cls = _permute_class(cls)
        if sheet_type == "Q":
            for c in (cls, permuted_cls):
                candidates += [f"{c}-Q", f"{c}-indiv.", f"{c}-indiv"]
            for c in (base, permuted_base):
                candidates += [f"{c}-Q-JUN", f"{c}-indiv.-JUN"]
        else:  # F
            for c in (base, permuted_base):
                candidates += [f"{c}-F-JUN"]
            for c in (cls, permuted_cls):
                candidates += [f"{c}-F"]
    else:
        permuted = _permute_class(cls)
        if sheet_type == "Q":
            for c in (cls, permuted):
                candidates += [f"{c}-Q", f"{c}-indiv.", f"{c}-indiv"]
        else:
            for c in (cls, permuted):
                candidates += [f"{c}-F"]
    for name in candidates:
        sheet = get_sheet(doc, name)
        if sheet is not None:
            return sheet
    return None


# -------- data prep --------

def build_junior_icf_set(participants: dict, cls: str) -> set:
    """Vrátí set ICFId, kteří jsou v junior variantě dané třídy.

    Pro cls='MX1' najde MX1J a vrátí ICFId všech v něm.
    """
    junior_cls = cls + "J"
    if junior_cls not in participants:
        return set()
    return {p["icf"] for p in participants[junior_cls] if p["icf"]}


def collect_xt_data(participants: list[dict], results: dict, class_id: str, day: str, attr: str = ""):
    """Pro každého z participants najde XT result. Vrátí list dictů."""
    suffix = f"_{attr}" if attr else ""
    race = f"{class_id}_XT_{day}{suffix}"
    rows = []
    for p in participants:
        r = results.get((race, p["id"]))
        if not r:
            continue
        rows.append({
            "icf": p["icf"],
            "family": p["family"],
            "given": p["given"],
            "bib_xt": r.get("bib"),
            "time_ms": r.get("time_ms"),
            "pen": r.get("pen") or 0,
            "status": (r.get("status") or "").upper(),
            "xt_rnk": r.get("rnk") or 99999,
        })
    return rows


def collect_finalists(participants: list[dict], results: dict, class_id: str,
                       day: str, attr: str) -> dict:
    """Vrátí dict ICFId → {bib_xs, xer_rnk} pro skutečné pavoukové finalisty.

    "Finalisté" = ti, kdo postoupili z XT do pavouka (mají XS/XF Results
    záznam). Ne všichni s XER záznamem — XER obsahuje pořadí pro VŠECHNY
    paddlery, ale je to overall ranking, ne pavoukoví finalisté.
    """
    suffix = f"_{attr}" if attr else ""
    race_xer = f"{class_id}_XER_{day}{suffix}"
    race_xs = f"{class_id}_XS_{day}{suffix}"
    out = {}
    for p in participants:
        r_xs = results.get((race_xs, p["id"]))
        if not r_xs:
            continue  # nebyl v pavoukovi
        r_xer = results.get((race_xer, p["id"]))
        out[p["icf"]] = {
            "bib_xs": r_xs.get("bib"),
            "xer_rnk": (r_xer.get("rnk") if r_xer else None) or r_xs.get("rnk") or 99999,
        }
    return out


def _has_time(d: dict) -> bool:
    return d.get("time_ms") is not None and d.get("time_ms", 0) > 0 \
        and d.get("status") not in ("DNS", "DNF", "DSQ")


# -------- sheet filling --------

def _set_jun_marker(row: TableRow, is_junior: bool) -> None:
    if is_junior:
        set_cell_string(get_cell_at(row, COL_JUN), "jun.")


def _set_bib(row: TableRow, bib, prefix: str = "") -> None:
    if bib is None:
        return
    if prefix:
        set_cell_string(get_cell_at(row, COL_BIB), f"{prefix}{bib}")
    else:
        set_cell_float(get_cell_at(row, COL_BIB), int(bib))


def _set_rgc(row: TableRow, rgc: str) -> None:
    if not rgc:
        return
    try:
        set_cell_float(get_cell_at(row, COL_RGC), int(rgc))
    except ValueError:
        set_cell_string(get_cell_at(row, COL_RGC), rgc)


def _set_time(row: TableRow, xt: dict) -> None:
    if xt.get("status") in ("DNS", "DNF", "DSQ"):
        set_cell_string(get_cell_at(row, COL_TIME_Q), xt["status"])
        return
    if _has_time(xt):
        set_cell_float(get_cell_at(row, COL_TIME_Q), xt["time_ms"] / 1000.0)


def _set_por(row: TableRow, value) -> None:
    cell = get_cell_at(row, COL_POR)
    if value is None or value == "":
        clear_cell(cell)
    else:
        set_cell_float(cell, value)


def _set_final_rank(row: TableRow, rank: int) -> None:
    set_cell_float(get_cell_at(row, COL_FINAL_RANK), rank)


def _clear_data_row(row: TableRow) -> None:
    """Vyčistí data v řádku — pro 'leftover' řádky šablony."""
    for col in (COL_POR, COL_VK, COL_JUN, COL_BIB, COL_RGC, COL_TIME_Q, COL_FINAL_RANK):
        clear_cell(get_cell_at(row, col))


def fill_q_sheet(sheet: Table, xt_rows: list[dict], junior_icfs: set) -> int:
    """Naplní Q sheet: všichni XT závodníci, sort podle Rnk; DNS na konci."""
    rows = list(sheet.getElementsByType(TableRow))
    finishers = [r for r in xt_rows if _has_time(r)]
    finishers.sort(key=lambda x: x["xt_rnk"])
    dns_rows = [r for r in xt_rows if not _has_time(r)]
    dns_rows.sort(key=lambda x: x["bib_xt"] or 99999)

    row_idx = 2
    rank = 0
    for x in finishers:
        if row_idx >= len(rows):
            break
        rank += 1
        row = rows[row_idx]
        _clear_data_row(row)
        _set_por(row, rank)
        _set_jun_marker(row, x["icf"] in junior_icfs)
        _set_bib(row, x["bib_xt"])
        _set_rgc(row, x["icf"])
        _set_time(row, x)
        row_idx += 1
    for x in dns_rows:
        if row_idx >= len(rows):
            break
        row = rows[row_idx]
        _clear_data_row(row)
        _set_por(row, None)  # DNS bez poř.
        _set_jun_marker(row, x["icf"] in junior_icfs)
        _set_bib(row, x["bib_xt"])
        _set_rgc(row, x["icf"])
        _set_time(row, x)
        row_idx += 1

    # Vyčistit zbývající řádky šablony
    for j in range(row_idx, len(rows)):
        rep = int(rows[j].getAttribute("numberrowsrepeated") or 1)
        if rep > 1:
            break
        _clear_data_row(rows[j])
    return row_idx - 2


def fill_f_sheet(sheet: Table, xt_rows: list[dict], xer_by_icf: dict,
                 junior_icfs: set) -> int:
    """Naplní F sheet: top finalisté z XER + ne-finalisté z XT + DNS."""
    rows = list(sheet.getElementsByType(TableRow))
    # Sortuj XT data pro ne-finalisty
    finalists = []
    for x in xt_rows:
        if x["icf"] in xer_by_icf:
            xer = xer_by_icf[x["icf"]]
            finalists.append({**x, **xer})
    finalists.sort(key=lambda x: x.get("xer_rnk", 99999))

    non_finalists = [x for x in xt_rows
                     if x["icf"] not in xer_by_icf and _has_time(x)]
    non_finalists.sort(key=lambda x: x["xt_rnk"])

    dns_rows = [x for x in xt_rows
                if x["icf"] not in xer_by_icf and not _has_time(x)]
    dns_rows.sort(key=lambda x: x.get("bib_xt") or 99999)

    row_idx = 2
    rank = 0
    # Finalisté
    for f in finalists:
        if row_idx >= len(rows):
            break
        rank += 1
        row = rows[row_idx]
        _clear_data_row(row)
        _set_por(row, rank)
        _set_jun_marker(row, f["icf"] in junior_icfs)
        _set_bib(row, f.get("bib_xs"))
        _set_rgc(row, f["icf"])
        _set_final_rank(row, f["xer_rnk"])
        row_idx += 1
    # Ne-finalisté
    for x in non_finalists:
        if row_idx >= len(rows):
            break
        rank += 1
        row = rows[row_idx]
        _clear_data_row(row)
        _set_por(row, rank)
        _set_jun_marker(row, x["icf"] in junior_icfs)
        _set_bib(row, x["bib_xt"], prefix="t ")
        _set_rgc(row, x["icf"])
        _set_time(row, x)
        row_idx += 1
    # DNS
    for x in dns_rows:
        if row_idx >= len(rows):
            break
        row = rows[row_idx]
        _clear_data_row(row)
        _set_por(row, None)
        _set_jun_marker(row, x["icf"] in junior_icfs)
        _set_bib(row, x["bib_xt"], prefix="t ")
        _set_rgc(row, x["icf"])
        _set_time(row, x)
        row_idx += 1

    for j in range(row_idx, len(rows)):
        rep = int(rows[j].getAttribute("numberrowsrepeated") or 1)
        if rep > 1:
            break
        _clear_data_row(rows[j])
    return row_idx - 2


def update_param(doc, race_num: int | None, date: str | None, name: str | None) -> None:
    if race_num is None and date is None and name is None:
        return
    sheet = get_sheet(doc, "param")
    if sheet is None:
        return
    rows = list(sheet.getElementsByType(TableRow))
    if name is not None and len(rows) > 2:
        set_cell_string(get_cell_at(rows[2], 1), name)
    if date is not None and len(rows) > 7:
        set_cell_string(get_cell_at(rows[7], 1), date)
    if race_num is not None and len(rows) > 8:
        set_cell_float(get_cell_at(rows[8], 1), race_num)


def _day_arg(s: str) -> str:
    if not re.fullmatch(r"\d{1,2}", s):
        raise argparse.ArgumentTypeError(f"očekávám 1-2 ciferné číslo dne, dostal jsem {s!r}")
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", help="Canoe123 XML soubor")
    ap.add_argument("template", help="Eskymo cross šablona (ODS)")
    ap.add_argument("output", help="Výstupní ODS soubor")
    ap.add_argument("--day", required=True, type=_day_arg,
                    help="Den XT (kvalifikace), např. 26.")
    ap.add_argument("--day-final", type=_day_arg,
                    help="Den XS/XF/XER, pokud jiný než --day.")
    ap.add_argument("--race", type=int, help="Číslo závodu pro param (volitelně)")
    ap.add_argument("--date", help="Datum pro param, např. 26.04.26 (volitelně)")
    ap.add_argument("--name", help="Název závodu pro param (volitelně)")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    print(f"Načítám XML: {args.xml}")
    participants, results, schedules = parse_xml(args.xml)

    if not participants:
        print("V XML jsem nenašel žádné cross sprint účastníky (ClassId obsahující 'X1').",
              file=sys.stderr)
        sys.exit(1)

    for cls, parts in sorted(participants.items()):
        print(f"  {cls}: {len(parts)} účastníků")
    print(f"  celkem {len(results)} výsledků, {len(schedules)} schedulí")

    print(f"Otevírám šablonu: {args.template}")
    doc = load(args.template)
    print(f"  sheety: {', '.join(all_sheet_names(doc))}")

    update_param(doc, args.race, args.date, args.name)

    day = args.day
    day_final = args.day_final or day

    for cls, parts in sorted(participants.items()):
        # Junioři: kontrolovat, kdo z paddler je v junior variantě
        junior_icfs = build_junior_icf_set(participants, cls)
        # Najít attr (např. JUN) pro race IDs této třídy
        attr = ""
        for sch in schedules.values():
            if sch["class"] == cls and sch.get("attr"):
                attr = sch["attr"]
                break

        sheet_q = find_sheet_for(doc, cls, "Q")
        sheet_f = find_sheet_for(doc, cls, "F")

        if sheet_q is None and sheet_f is None:
            print(f"  {cls}: žádné odpovídající sheety v šabloně, přeskakuji")
            continue

        xt_rows = collect_xt_data(parts, results, cls, day, attr)
        finalists_by_icf = collect_finalists(parts, results, cls, day_final, attr)

        if sheet_q is not None:
            n = fill_q_sheet(sheet_q, xt_rows, junior_icfs)
            print(f"  {cls} → {sheet_q.getAttribute('name')}: {n} řádků v kvalifikaci")

        if sheet_f is not None:
            n = fill_f_sheet(sheet_f, xt_rows, finalists_by_icf, junior_icfs)
            print(f"  {cls} → {sheet_f.getAttribute('name')}: {n} řádků ve finále")

    print(f"Ukládám: {args.output}")
    doc.save(args.output)
    print("Hotovo.")


if __name__ == "__main__":
    main()
