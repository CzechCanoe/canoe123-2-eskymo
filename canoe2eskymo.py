"""
canoe2eskymo — naplní Eskymo ODS šablonu daty z Canoe123 XML.

Použití:
    py canoe2eskymo.py <xml> <empty.ods> <output.ods> --day 23
    py canoe2eskymo.py <xml> <empty.ods> <output.ods> --day 24 --race 51 --date 24.05.26

Skript:
  - Načte Canoe123 XML (Participants + Results)
  - Otevře ODS šablonu (Eskymo)
  - Pro každou kategorii v šabloně (C1M, C1W→c1z, K1M, K1W→k1z,
    C2M, C2W→c2z, C2X, PZK, PZC):
      * do *_sl sheetu zapíše stč (bib) a rgc (ICFId)
      * do hlavního sheetu zapíše čas1, pen1, čas2, pen2 z odpovídajícího běhu
  - Pro deblové lodě (C2*) rozdělí slepený ICFId podle `reg` sheetu
  - Volitelně přepíše datum, číslo a název závodu v sheetu `param`
  - Uloží do nového ODS souboru
"""

from __future__ import annotations

import argparse
import sys
import re
from collections import defaultdict
from xml.etree import ElementTree as ET

from odf.opendocument import load
from odf.table import Table, TableRow, TableCell
from odf.text import P
from odf import teletype


# Mapování XML ClassId → Eskymo sheet názvy (hlavní, startovka).
# Pořadí určuje pořadí zpracování (kosmetické, jen pro výstup).
CLASS_TO_SHEETS = {
    "K1M": ("k1m", "k1m_sl"),
    "K1W": ("k1z", "k1z_sl"),
    "C1M": ("c1m", "c1m_sl"),
    "C1W": ("c1z", "c1z_sl"),
    "C2M": ("c2m", "c2m_sl"),
    "C2W": ("c2z", "c2z_sl"),
    "C2X": ("c2x", "c2x_sl"),
    "PZK": ("pzk", "pzk_sl"),
    "PZC": ("pzc", "pzc_sl"),
}

# Třídy, kde se ICFId skládá ze dvou slepených RGC (deblové lodě)
DOUBLE_CLASSES = {"C2M", "C2W", "C2X"}

XML_NS = "{http://siwidata.com/Canoe123/Data.xsd}"


def parse_xml(xml_path: str):
    """Vrátí (participants_by_class, results_by_raceid_and_id)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    participants_by_class: dict[str, list[dict]] = defaultdict(list)
    results: dict[tuple[str, str], dict] = {}

    for elem in root:
        tag = elem.tag.replace(XML_NS, "")
        if tag == "Participants":
            cls = _text(elem, "ClassId")
            if cls in CLASS_TO_SHEETS:
                participants_by_class[cls].append({
                    "id": _text(elem, "Id"),
                    "class": cls,
                    "bib": _int(elem, "EventBib"),
                    "icf": _text(elem, "ICFId"),
                    "icf2": _text(elem, "ICFId2"),
                    "family": _text(elem, "FamilyName"),
                    "given": _text(elem, "GivenName"),
                    "family2": _text(elem, "FamilyName2"),
                    "given2": _text(elem, "GivenName2"),
                    "year": _year_from_birthdate(elem, "Birthdate") or _text(elem, "Year"),
                    "year2": _year_from_birthdate(elem, "Birthdate2"),
                    "club": _text(elem, "Club"),
                    "cat": _text(elem, "CatId"),
                })
        elif tag == "Results":
            race_id = _text(elem, "RaceId")
            participant_id = _text(elem, "Id")
            if not race_id or not participant_id:
                continue
            results[(race_id, participant_id)] = {
                "status": _text(elem, "Status"),
                "time_ms": _int(elem, "Time"),
                "pen": _int(elem, "Pen"),
                "total_ms": _int(elem, "Total"),
                "bib": _int(elem, "Bib"),
            }

    return participants_by_class, results


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


def _year_from_birthdate(elem, tag) -> str:
    """Z Birthdate '2004-01-01T11:00:00+01:00' vytáhne '2004'."""
    s = _text(elem, tag)
    if not s:
        return ""
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def get_sheet(doc, name: str) -> Table | None:
    for t in doc.spreadsheet.getElementsByType(Table):
        if t.getAttribute("name") == name:
            return t
    return None


def has_sheet(doc, name: str) -> bool:
    return get_sheet(doc, name) is not None


def split_repeated_cells_until(row: TableRow, target_col: int) -> None:
    """Rozbalí buňky v řádku tak, aby na pozici target_col byla samostatná buňka."""
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
    # Cíl je dál — doplnit prázdné buňky
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
    split_repeated_cells_until(row, target_col)
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


def _fmt_float(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _cell_text(cell: TableCell) -> str:
    return teletype.extractText(cell)


def load_registry(doc) -> dict:
    """Načte `reg` a `cizi` sheety.

    Vrací dict s:
      - 'rgcs': set všech Czech RGC (jako string)
      - 'by_name': (FAMILY_UPPER, GIVEN_UPPER) → list of (rgc, year_str)
      - 'foreign_by_fullname': FULLNAME_UPPER → "A…" RGC (cizi)
    """
    out = {"rgcs": set(), "by_name": defaultdict(list), "foreign_by_fullname": {}}
    sheet = get_sheet(doc, "reg")
    if sheet is not None:
        rows = list(sheet.getElementsByType(TableRow))
        for row in rows[1:]:
            cells = list(row.getElementsByType(TableCell))
            if len(cells) < 4:
                continue
            rgc = _cell_text(cells[0]).strip()
            if not rgc or not rgc.isdigit():
                continue
            family = _cell_text(cells[1]).strip().upper()
            given = _cell_text(cells[2]).strip().upper()
            year = _cell_text(cells[3]).strip()
            out["rgcs"].add(rgc)
            if family and given:
                out["by_name"][(family, given)].append((rgc, year))
    cizi = get_sheet(doc, "cizi")
    if cizi is not None:
        rows = list(cizi.getElementsByType(TableRow))
        for row in rows[1:]:
            cells = list(row.getElementsByType(TableCell))
            if len(cells) < 2:
                continue
            rgc = _cell_text(cells[0]).strip()
            fullname = _cell_text(cells[1]).strip().upper()
            if rgc and fullname:
                out["foreign_by_fullname"][fullname] = rgc
    return out


def lookup_person(family: str, given: str, year: str, registry: dict) -> str | None:
    """Vrátí RGC podle jména (a roku narození pro disambiguaci). None pokud nenalezeno."""
    fam = (family or "").strip().upper()
    giv = (given or "").strip().upper()
    if fam and giv:
        candidates = registry["by_name"].get((fam, giv), [])
        if candidates:
            if year and len(candidates) > 1:
                for rgc, ry in candidates:
                    if ry == year:
                        return rgc
                return candidates[0][0]
            return candidates[0][0]
        rgc = registry["foreign_by_fullname"].get(f"{fam} {giv}".strip())
        if rgc:
            return rgc
    if fam:
        rgc = registry["foreign_by_fullname"].get(fam)
        if rgc:
            return rgc
    return None


def add_foreigner(family: str, given: str, club: str, registry: dict,
                  new_entries: list) -> str:
    """Vygeneruje nové A* RGC pro cizince, zapíše do registru i seznamu k zápisu.

    Dedup podle 'FAMILY GIVEN' (uppercase) — opakované volání pro stejnou
    osobu vrátí stejné RGC.
    """
    fam = (family or "").strip()
    giv = (given or "").strip()
    fullname = f"{fam} {giv}".strip()
    key = fullname.upper()
    if not key:
        return ""

    existing = registry["foreign_by_fullname"].get(key)
    if existing:
        return existing
    # Možná je v cizi jen příjmení
    if fam:
        existing = registry["foreign_by_fullname"].get(fam.upper())
        if existing:
            return existing

    # Najít max existující A-RGC číslo
    max_num = 90000  # default start = A90001
    for rgc in set(registry["foreign_by_fullname"].values()):
        if rgc.startswith("A") and rgc[1:].isdigit():
            max_num = max(max_num, int(rgc[1:]))
    new_rgc = f"A{max_num + 1:05d}"

    registry["foreign_by_fullname"][key] = new_rgc
    new_entries.append({
        "rgc": new_rgc,
        "fullname": fullname,
        "club": (club or "").strip(),
    })
    return new_rgc


def split_double_icf(icf: str, p: dict, registry: dict) -> tuple[str, str] | None:
    """Rozdělí slepené ICFId (např. '108112032') na dva RGC ('1081', '12032').

    Strategie:
      1) Najít RGC1 a RGC2 podle jména pádlerů (+ rok pro disambiguaci).
         Pokud RGC1+RGC2 == icf, je to ono.
      2) Pokud selže, zkusit všechna možná dělení a vybrat to, kde jsou
         oba kusy validní RGC. Pokud máme jednoho z lookupu, preferovat
         dělení, které ho obsahuje.

    Vrátí (rgc1, rgc2) nebo None.
    """
    rgcs = registry["rgcs"]
    rgc1 = lookup_person(p.get("family", ""), p.get("given", ""), p.get("year", ""), registry)
    rgc2 = lookup_person(p.get("family2", ""), p.get("given2", ""), p.get("year2", ""), registry)

    if rgc1 and rgc2 and rgc1 + rgc2 == icf:
        return rgc1, rgc2
    if rgc1 and icf.startswith(rgc1):
        candidate = icf[len(rgc1):]
        if candidate in rgcs or candidate.startswith("A"):
            return rgc1, candidate
    if rgc2 and icf.endswith(rgc2):
        candidate = icf[:-len(rgc2)]
        if candidate in rgcs or candidate.startswith("A"):
            return candidate, rgc2

    # Zkusit všechna dělení
    candidates = []
    for i in range(1, len(icf)):
        a, b = icf[:i], icf[i:]
        if a in rgcs and b in rgcs:
            candidates.append((a, b))
    if len(candidates) == 1:
        return candidates[0]
    if rgc1:
        for a, b in candidates:
            if a == rgc1:
                return a, b
    if rgc2:
        for a, b in candidates:
            if b == rgc2:
                return a, b
    if candidates:
        return candidates[0]
    return None


def fill_startlist(doc, sheet_name: str, participants: list[dict],
                   class_id: str, registry: dict,
                   new_cizi_entries: list) -> tuple[int, list[str]]:
    """Naplní _sl sheet: bib (col 1), rgc (col 2). Vrátí (počet, varování).

    Cizí pádlerové, kteří ještě nejsou v `cizi`, se přidají do
    `new_cizi_entries` k pozdějšímu zápisu.
    """
    sheet = get_sheet(doc, sheet_name)
    if sheet is None:
        return 0, []
    rows = list(sheet.getElementsByType(TableRow))
    sorted_parts = sorted(participants, key=lambda p: (p["bib"] if p["bib"] is not None else 99999))

    warnings: list[str] = []
    count = 0
    for i, p in enumerate(sorted_parts):
        row_idx = 2 + i
        if row_idx >= len(rows):
            warnings.append(f"šablona má jen {len(rows)} řádků, vejde se {len(rows) - 2}, ne všechny ({len(sorted_parts)})")
            break
        row = rows[row_idx]
        set_cell_float(get_cell_at(row, 0), i + 1)
        if p["bib"] is not None:
            set_cell_float(get_cell_at(row, 1), p["bib"])
        rgc_value = _resolve_rgc(p, class_id, registry, new_cizi_entries, warnings)
        if rgc_value is not None:
            if isinstance(rgc_value, int):
                set_cell_float(get_cell_at(row, 2), rgc_value)
            else:
                set_cell_string(get_cell_at(row, 2), rgc_value)
        count += 1
    # Vyčistit stale id v dalších předvyplněných řádcích (např. id=1 zopakované
    # by způsobilo, že VLOOKUP duplikuje prvního závodníka).
    _clear_trailing_ids(rows, start_idx=2 + count, count=count)
    return count, warnings


def _clear_trailing_ids(rows, start_idx: int, count: int) -> None:
    """Přepíše id ve zbývajících řádcích na sekvenční pokračování N+1, N+2, ….

    Šablona má pre-fill řádky s náhodnými id (template residue). Pokud
    bychom je nechali, mohlo by dojít ke kolizi s id, které jsme zapsali.
    """
    next_id = count + 1
    for row in rows[start_idx:]:
        rep = int(row.getAttribute("numberrowsrepeated") or 1)
        if rep > 1:
            # Velký opakovaný "zbytek tabulky" — neřešíme
            break
        cells = list(row.getElementsByType(TableCell))
        if not cells:
            continue
        first_text = _cell_text(cells[0]).strip()
        if not first_text:
            continue
        try:
            int(float(first_text))
        except ValueError:
            continue
        set_cell_float(get_cell_at(row, 0), next_id)
        next_id += 1


def _resolve_rgc(p: dict, class_id: str, registry: dict,
                 new_cizi_entries: list, warnings: list[str]):
    """Vrátí hodnotu pro sloupec rgc (int nebo string), nebo None pokud chybí."""
    icf = p.get("icf", "")
    club = p.get("club", "")
    if class_id in DOUBLE_CLASSES:
        icf2 = p.get("icf2", "")
        rgc1 = _resolve_paddler(icf, p["family"], p["given"], p.get("year", ""),
                                 club, registry, new_cizi_entries)
        rgc2 = _resolve_paddler(icf2, p["family2"], p["given2"], p.get("year2", ""),
                                 club, registry, new_cizi_entries)
        if (not rgc1 or not rgc2) and icf and not icf2:
            split = split_double_icf(icf, p, registry)
            if split:
                if not rgc1:
                    rgc1 = split[0]
                if not rgc2:
                    rgc2 = split[1]
        if rgc1 and rgc2:
            return f"{rgc1} {rgc2}"
        missing = []
        if not rgc1:
            missing.append(f"'{p['family']} {p['given']}'")
        if not rgc2:
            missing.append(f"'{p['family2']} {p['given2']}'")
        warnings.append(f"bib {p['bib']} ({class_id}): nenašel jsem RGC pro {' a '.join(missing)} "
                        f"(ICFId='{icf}' ICFId2='{icf2}') — oprav ručně")
        if rgc1 or rgc2:
            return f"{rgc1 or '?'} {rgc2 or '?'}"
        return None
    # Sólo loď
    rgc = _resolve_paddler(icf, p["family"], p["given"], p.get("year", ""),
                            club, registry, new_cizi_entries)
    if rgc is None:
        warnings.append(f"bib {p['bib']} ({class_id}): nenašel jsem RGC pro "
                        f"'{p['family']} {p['given']}' (ICFId='{icf}') — oprav ručně")
        return None
    try:
        return int(rgc)
    except ValueError:
        return rgc  # cizinec ("A…")


def _resolve_paddler(icf: str, family: str, given: str, year: str,
                     club: str, registry: dict, new_cizi_entries: list) -> str | None:
    """Najít RGC pro jednoho pádlerа.

    Strategie:
      1) ICFId v `reg` → vrať ho.
      2) ICFId není číslo (např. "A12345") → vrať ho.
      3) Lookup podle jména v `reg` / `cizi` (+ rok pro disambiguaci).
      4) Pokud pořád nic a jméno máme → cizinec, který v `cizi` chybí;
         vygeneruj nové A* RGC a přidej do `new_cizi_entries`.
      5) Fallback: vrať ICFId pokud existuje, jinak None.
    """
    if icf and icf in registry["rgcs"]:
        return icf
    if icf and not icf.isdigit():
        return icf
    rgc = lookup_person(family, given, year, registry)
    if rgc:
        return rgc
    if not icf and (family or given):
        # XML nezná ICFId → cizinec, který ještě není v cizi
        return add_foreigner(family, given, club, registry, new_cizi_entries)
    return icf if icf else None


def fill_results(doc, sheet_name: str, participants: list[dict],
                 results: dict, class_id: str, day: str) -> int:
    """Naplní výsledkový sheet: čas1/pen1 (col 11/12), čas2/pen2 (col 14/15)."""
    sheet = get_sheet(doc, sheet_name)
    if sheet is None:
        return 0
    rows = list(sheet.getElementsByType(TableRow))
    sorted_parts = sorted(participants, key=lambda p: (p["bib"] if p["bib"] is not None else 99999))

    race1 = f"{class_id}_BR1_{day}"
    race2 = f"{class_id}_BR2_{day}"

    count = 0
    for i, p in enumerate(sorted_parts):
        row_idx = 2 + i
        if row_idx >= len(rows):
            break
        row = rows[row_idx]
        set_cell_float(get_cell_at(row, 0), i + 1)

        r1 = results.get((race1, p["id"]))
        r2 = results.get((race2, p["id"]))
        # Pravidlo: účastník se sem dostal (má aspoň jeden Results záznam
        # pro daný den), takže každá jízda musí mít čas nebo DNS.
        _write_run(row, r1, time_col=11, pen_col=12)
        _write_run(row, r2, time_col=14, pen_col=15)
        count += 1
    # Stejně jako u startovky — přečíslovat zbývající řádky aby nevznikla
    # duplicitní id (template má pre-fill řádky s náhodnými id).
    _clear_trailing_ids(rows, start_idx=2 + count, count=count)
    return count


def _write_run(row, result, time_col: int, pen_col: int) -> None:
    """Zapíše čas/pen pro jednu jízdu.

    Pravidla (striktně podle Canoe123 Results):
      - Záznam s Status DNS/DNF/DSQ  → text Status + pen 999
      - Záznam s časem  → čas (sec) + pen
      - Záznam bez statusu i bez času (skrytý DNS) → DNS + 999
      - Záznam neexistuje (jízda neproběhla, ale druhá ano) → DNS + 999
    """
    cell_time = get_cell_at(row, time_col)
    cell_pen = get_cell_at(row, pen_col)

    if not result:
        set_cell_string(cell_time, "DNS")
        set_cell_float(cell_pen, 999)
        return

    status = (result.get("status") or "").upper()
    time_ms = result.get("time_ms")
    pen = result.get("pen") or 0

    if status in ("DNS", "DNF", "DSQ"):
        set_cell_string(cell_time, status)
        set_cell_float(cell_pen, 999)
    elif time_ms is None or time_ms == 0:
        set_cell_string(cell_time, "DNS")
        set_cell_float(cell_pen, 999)
    else:
        set_cell_float(cell_time, time_ms / 1000.0)
        set_cell_float(cell_pen, pen)


def write_cizi_entries(doc, entries: list) -> int:
    """Vloží nové cizince do listu `cizi`. RGC do col 0, jméno do col 1,
    klub (3-pis. zkratka) do col 12.

    Vkládá nové <table-row> elementy před první "velký prázdný" repeat-row.
    """
    if not entries:
        return 0
    sheet = get_sheet(doc, "cizi")
    if sheet is None:
        return 0
    rows = list(sheet.getElementsByType(TableRow))
    # Najít první řádek s numberrowsrepeated > 1 — sem patří vsuvka.
    anchor = None
    for row in rows:
        rep = int(row.getAttribute("numberrowsrepeated") or 1)
        if rep > 1:
            anchor = row
            break
    parent = sheet
    inserted = 0
    for entry in entries:
        new_row = TableRow()
        # col 0: rgc (text, ne číslo, kvůli formátu "A…")
        cell0 = TableCell()
        cell0.setAttribute("valuetype", "string")
        cell0.addElement(P(text=entry["rgc"]))
        new_row.addElement(cell0)
        # col 1: prijmeni / fullname
        cell1 = TableCell()
        cell1.setAttribute("valuetype", "string")
        cell1.addElement(P(text=entry["fullname"]))
        new_row.addElement(cell1)
        # cols 2-11: prázdné (10 buněk)
        spacer = TableCell()
        spacer.setAttribute("numbercolumnsrepeated", "10")
        new_row.addElement(spacer)
        # col 12: oddil
        cell12 = TableCell()
        cell12.setAttribute("valuetype", "string")
        cell12.addElement(P(text=entry["club"]))
        new_row.addElement(cell12)
        if anchor is not None:
            parent.insertBefore(new_row, anchor)
        else:
            parent.addElement(new_row)
        inserted += 1
    return inserted


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
    ap = argparse.ArgumentParser()
    ap.add_argument("xml", help="Canoe123 XML soubor")
    ap.add_argument("template", help="Eskymo prázdná šablona (ODS)")
    ap.add_argument("output", help="Výstupní ODS soubor")
    ap.add_argument("--day", required=True, type=_day_arg,
                    help="Den závodu (poslední 1-2 znaky RaceId v XML, např. 4, 5, 23, 24)")
    ap.add_argument("--race", type=int, help="Číslo závodu pro param.B9 (volitelně)")
    ap.add_argument("--date", help="Datum pro param.B8, např. 23.05.26 (volitelně)")
    ap.add_argument("--name", help="Název závodu pro param.B3 (volitelně)")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    print(f"Načítám XML: {args.xml}")
    participants_by_class, results = parse_xml(args.xml)
    for cls in CLASS_TO_SHEETS:
        if cls in participants_by_class:
            print(f"  {cls}: {len(participants_by_class[cls])} účastníků v XML")
    print(f"  celkem {len(results)} výsledků")

    print(f"Otevírám šablonu: {args.template}")
    doc = load(args.template)

    update_param(doc, args.race, args.date, args.name)

    # Načti registr (pro deblové lodě a cizince)
    registry = load_registry(doc)
    if registry["rgcs"]:
        print(f"  registr: {len(registry['rgcs'])} osob, "
              f"cizinci: {len(set(registry['foreign_by_fullname'].values()))}")

    new_cizi_entries: list = []  # cizinci, kteří v cizi chybí — přidají se na konci

    for cls, (results_sheet, sl_sheet) in CLASS_TO_SHEETS.items():
        if not has_sheet(doc, sl_sheet):
            if cls in participants_by_class:
                print(f"  {cls}: sheet '{sl_sheet}' v šabloně chybí, přeskakuji")
            continue
        all_parts = participants_by_class.get(cls, [])
        if not all_parts:
            print(f"  {cls}: žádní účastníci v XML, přeskakuji")
            continue
        parts = [
            p for p in all_parts
            if (f"{cls}_BR1_{args.day}", p["id"]) in results
            or (f"{cls}_BR2_{args.day}", p["id"]) in results
        ]
        skipped = len(all_parts) - len(parts)
        n_sl, warnings = fill_startlist(doc, sl_sheet, parts, cls, registry, new_cizi_entries)
        n_res = fill_results(doc, results_sheet, parts, results, cls, args.day)
        skipped_note = f", přeskočeno {skipped} bez výsledků" if skipped else ""
        print(f"  {cls} → {sl_sheet}: {n_sl} startovka, {results_sheet}: {n_res} výsledky{skipped_note}")
        for w in warnings:
            print(f"    ! {w}")

    if new_cizi_entries:
        n_cizi = write_cizi_entries(doc, new_cizi_entries)
        print(f"  cizi: doplněno {n_cizi} nových záznamů")
        for e in new_cizi_entries:
            print(f"    + {e['rgc']}  {e['fullname']}  ({e['club']})")

    print(f"Ukládám: {args.output}")
    doc.save(args.output)
    print("Hotovo.")


if __name__ == "__main__":
    main()
