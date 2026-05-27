"""
cross — naplní Eskymo ODS šablonu daty z Canoe123 XML pro cross sprint.

Použití:
    py cross.py <xml> <empty.ods> <output.ods> --day 26

Cross sprint má dvě klíčové race ID:
  - XT (Individual Time Trial) — kvalifikace, individuální čas
  - XER (Event Result, eliminace) — celkové pořadí, obsahuje vše:
    * pro pavoukové: bib z pavouka + finále rank
    * pro ne-pavoukové: bib jako "t N" (textově) + XT čas
    * pro DNS: bib jako "t N" + Status

Skript zpracuje cross třídy (ClassId obsahuje 'X1', např. MX1, WX1,
MX1J, X1M-ZS, X1Z-ZM). Pro každou kategorii vyplní:

  - **Q sheet** (`<class>-Q` / `<class>-indiv.`) — z XT:
    Sloupce: poř., 'jun.', stč (XT bib), rgc, čas. DNS na konci.

  - **F sheet** (`<class>-F` / `<class>-F-JUN`) — z XER:
    Pavoukoví (top N) první s XS bib a finále rankem.
    Pak ne-pavoukoví seřazení podle XT času, s "t N" v bib sloupci.
    DNS na konci.

Detekce pavoukového závodníka: `Bib` v XER je číslo (např. "3"),
ne `t N` (např. "t    3"). Canoe123 to tak označuje. RecordType
(F/SF/QF/T) ne stačí — má víc úrovní pavouka.

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

from odf.opendocument import load, OpenDocument
from odf.table import Table, TableRow, TableCell
from odf.text import P
from odf import teletype


# Monkey-patch: odfpy `remove_from_caches` občas spadne s ValueError ("x not in list")
# u dokumentů, kde load() neregistruje úplně všechny elementy do element_dict.
# Naše use case (odstranění buněk při _split_repeated_cells_until) tu chybu trigeruje
# bez reálného problému — element prostě v cache nebyl, tak ho stačí ignorovat.
_orig_remove_from_caches = OpenDocument.remove_from_caches
def _safe_remove_from_caches(self, elt):
    try:
        _orig_remove_from_caches(self, elt)
    except ValueError:
        pass
OpenDocument.remove_from_caches = _safe_remove_from_caches


XML_NS = "{http://siwidata.com/Canoe123/Data.xsd}"

# Cross sprint disciplíny (DisId)
# Skript používá jen XT (kvalifikace) a XER (eliminační event result),
# XS/XF jsou jen mezikroky které XER souhrnně reflektuje.
DIS_XT = "XT"    # Individual Time Trial — kvalifikace
DIS_XER = "XER"  # Event Result — celkové pořadí včetně pavouka

# Sloupce v Eskymo cross sheetech (po výzkumu Troja CP3 ručních souborů)
COL_POR = 0       # pořadí
COL_VK = 1        # věk. kat.
COL_JUN = 2       # 'jun.' marker
COL_BIB = 3       # stč (bib v dané fázi)
COL_RGC = 4       # rgc / ICFId
COL_NAME = 5      # jméno (FamilyName + GivenName)
COL_YEAR = 6      # rok narození
COL_CLUB = 8      # oddíl (klub)
COL_TIME_Q = 11   # čas v kvalifikaci (Q sheet) nebo XT čas pro ne-finalisty v F sheet
COL_FLT = 12      # 'FLT(7)' marker pro failed gate
COL_FINAL_RANK = 13  # finále rank (XER)
COL_BODY = 16     # body (bodový součet podle ranku, statická tabulka v šabloně)
COL_BODY_JUN = 17 # body jun. (pro juniorské pořadí v dospělé třídě)


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
            bib_raw = _text(elem, "Bib")  # může být "2" nebo "t    4" (v XER)
            results[(race_id, participant_id)] = {
                "bib_raw": _normalize_ws(bib_raw),
                "bib_int": _safe_int(bib_raw),
                "time_ms": _int(elem, "Time"),
                "pen": _int(elem, "Pen"),
                "rnk": _int(elem, "Rnk") or _int(elem, "RnkOrder"),
                "status": _text(elem, "Status"),
                "record_type": _text(elem, "RecordType"),  # F = finalist, T = z time trial
                "flt": _text(elem, "FLT"),  # "FLT(7)" pro failed gate (přesahuje 50sec penalty)
            }
    return participants_by_class, results, schedules


def _safe_int(s: str) -> int | None:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def _normalize_ws(s: str) -> str:
    """'t    4' → 't 4'."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip())


def _is_cross_class(cls: str) -> bool:
    """Cross třídy mají 'X1' v názvu (MX1, WX1, MX1J, X1M-ZS, X1Z-ZM, …)."""
    return "X1" in cls and "C2X" not in cls


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


def _clone_empty_data_row(template_row: TableRow) -> TableRow:
    """Postaví nový prázdný řádek se stejnou strukturou buněk jako šablona.

    Kopíruje pouze stylename a numbercolumnsrepeated atributy z buněk.
    Nepoužívá deepcopy kvůli odfpy element-cache problémům.
    """
    new_row = TableRow()
    style = template_row.getAttribute("stylename")
    if style:
        new_row.setAttribute("stylename", style)
    for cell in template_row.getElementsByType(TableCell):
        new_cell = TableCell()
        cstyle = cell.getAttribute("stylename")
        if cstyle:
            new_cell.setAttribute("stylename", cstyle)
        ncr = int(cell.getAttribute("numbercolumnsrepeated") or 1)
        if ncr > 1:
            new_cell.setAttribute("numbercolumnsrepeated", str(ncr))
        new_row.addElement(new_cell)
    return new_row


def _ensure_data_capacity(sheet: Table, n_data_rows: int) -> None:
    """Zaručí, že sheet má aspoň `2 + n_data_rows` vedoucích single-row TableRow elementů
    (2 = řádek hlavičky závodu + řádek záhlaví sloupců).

    Šablony klonované z Troja MX1-F mívají ~52 single data řádků (rows 2-53),
    pak řádek s `numberrowsrepeated` v řádu stovek/milionu. Kdybychom psali data
    přímo do toho repeat řádku, naše hodnoty by se zobrazily ve všech jeho
    logických řádcích — viditelná chyba.

    Tato funkce před psaním rozšíří kapacitu: vezme první repeat řádek, sníží
    jeho `numberrowsrepeated` o potřebný počet, a vloží před něj odpovídající
    počet single řádků (klonovaných z posledního single template řádku).
    """
    rows = list(sheet.getElementsByType(TableRow))
    leading_single = 0
    for r in rows:
        if int(r.getAttribute("numberrowsrepeated") or 1) == 1:
            leading_single += 1
        else:
            break
    needed = 2 + n_data_rows
    if leading_single >= needed:
        return
    if leading_single == 0 or leading_single >= len(rows):
        return
    # Klonovat preferenčně z první data-řádky (rows[2]) — Eskymo šablony mívají
    # na konci single sekce "buffer řádek" s odlišnými styly (např. row 54 v
    # MX1-indiv. má ce4 místo ce3). Klonování z rows[2] dává konzistentní styly.
    template_row = rows[2] if len(rows) > 2 else rows[leading_single - 1]
    repeat_row = rows[leading_single]
    rep = int(repeat_row.getAttribute("numberrowsrepeated") or 1)
    add_count = needed - leading_single
    if add_count > rep:
        add_count = rep
    new_rep = rep - add_count
    if new_rep > 1:
        repeat_row.setAttribute("numberrowsrepeated", str(new_rep))
    elif new_rep == 1:
        if repeat_row.getAttribute("numberrowsrepeated"):
            repeat_row.removeAttribute("numberrowsrepeated")
    parent = repeat_row.parentNode
    for _ in range(add_count):
        new_row = _clone_empty_data_row(template_row)
        parent.insertBefore(new_row, repeat_row)


# -------- sheet name matching --------

# Suffixy pro Q (kvalifikace) a F (finále) sheety. Q sheet má víc variant
# protože Eskymo šablony se historicky pojmenovávaly různě (`-Q` v novějších,
# `-indiv.` v Troja kajak-kros, `-indiv` bez tečky v některých derivátech).
Q_SHEET_SUFFIXES = ("-Q", "-indiv.", "-indiv")
F_SHEET_SUFFIXES = ("-F",)


def _permute_class(cls: str) -> str:
    """MX1 → X1M, WX1J → X1WJ. Beze změny pro jiné formáty.

    Canoe123 používá MX1/WX1 (gender-first), Eskymo cross šablony někdy
    X1M/X1W (discipline-first). Sheet lookup zkouší obě varianty.
    """
    m = re.fullmatch(r"([MW])X1(.*)", cls)
    if m:
        return f"X1{m.group(1)}{m.group(2)}"
    return cls


def _class_name_variants(cls: str) -> list[str]:
    """[cls] nebo [cls, permuted_cls] pokud permutace dává jiný název."""
    permuted = _permute_class(cls)
    return [cls] if permuted == cls else [cls, permuted]


def _sheet_candidates(cls: str, sheet_type: str) -> list[str]:
    """Vrátí všechna jména sheetů, na která se má pro danou třídu+typ kouknout,
    v pořadí preference (první nalezený vyhraje).

    Pravidla:
      - Dospělá třída (MX1, X1M-ZS, …): `<cls>-Q` / `-indiv.` pro Q, `<cls>-F` pro F.
        Plus MX1↔X1M permutace.
      - Junior třída (MX1J): nejdřív vlastní sheet (MX1J-Q, MX1J-F),
        pak fallback na dospělý sheet s JUN suffixem (MX1-F-JUN, MX1-Q-JUN).
    """
    is_junior = cls.endswith("J")
    out: list[str] = []
    cls_vars = _class_name_variants(cls)

    if sheet_type == "Q":
        out += [f"{c}{s}" for c in cls_vars for s in Q_SHEET_SUFFIXES]
        if is_junior:
            adult_vars = _class_name_variants(cls[:-1])
            out += [f"{c}-Q-JUN" for c in adult_vars]
            out += [f"{c}-indiv.-JUN" for c in adult_vars]
    else:  # F
        if is_junior:
            # Junior F: preferuj `*-F-JUN` pod dospělou třídou.
            adult_vars = _class_name_variants(cls[:-1])
            out += [f"{c}-F-JUN" for c in adult_vars]
        out += [f"{c}{s}" for c in cls_vars for s in F_SHEET_SUFFIXES]

    return out


def find_sheet_for(doc, cls: str, sheet_type: str) -> Table | None:
    """Najde sheet pro danou třídu a typ ('Q' = kvalifikace, 'F' = finále)."""
    for name in _sheet_candidates(cls, sheet_type):
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


def _row_cells_expanded(row) -> list:
    """Vrátí buňky v řádku po expanzi `numbercolumnsrepeated`.

    Bez toho je index v `getElementsByType` fyzický, ne logický (col 12
    z hlavičky může být fyzický index 7 protože uprostřed jsou
    sloučené prázdné cells).
    """
    out = []
    for c in row.getElementsByType(TableCell):
        rep = int(c.getAttribute("numbercolumnsrepeated") or 1)
        for _ in range(rep):
            out.append(c)
    return out


def load_registry(doc) -> dict:
    """Načte `reg` sheet z cross šablony (pokud existuje) — pro lookup
    RGC → zkratka oddílu (col 12), případně rok narození (col 3).

    Vrací dict RGC (string) → {family, given, year, oddil_short}.
    """
    out: dict = {}
    sheet = get_sheet(doc, "reg")
    if sheet is None:
        return out
    rows = list(sheet.getElementsByType(TableRow))
    for row in rows[1:]:
        cells = _row_cells_expanded(row)
        if len(cells) < 4:
            continue
        rgc = teletype.extractText(cells[0]).strip()
        if not rgc or not rgc.isdigit():
            continue
        family = teletype.extractText(cells[1]).strip()
        given = teletype.extractText(cells[2]).strip() if len(cells) > 2 else ""
        year = teletype.extractText(cells[3]).strip() if len(cells) > 3 else ""
        oddil_short = teletype.extractText(cells[12]).strip() if len(cells) > 12 else ""
        out[rgc] = {
            "family": family,
            "given": given,
            "year": year,
            "oddil_short": oddil_short,
        }
    return out


def _participant_info(p: dict, registry: dict | None = None) -> dict:
    """Společné info ze XML <Participants>. Pokud existuje `reg` sheet v šabloně,
    preferuje z něj zkratku oddílu (XML má často dlouhý oficiální název klubu).
    """
    year = p["year"]
    club = p["club"]
    if registry and p["icf"]:
        reg = registry.get(p["icf"])
        if reg:
            if reg["oddil_short"]:
                club = reg["oddil_short"]
            if not year and reg["year"]:
                year = reg["year"]
    return {
        "icf": p["icf"],
        "family": p["family"],
        "given": p["given"],
        "name": f"{p['family']} {p['given']}".strip(),
        "year": year,
        "club": club,
    }


def collect_xt_data(participants: list[dict], results: dict, class_id: str, day: str, attr: str = "", registry: dict | None = None):
    """Pro Q sheet — XT (Individual Time Trial) results."""
    suffix = f"_{attr}" if attr else ""
    race = f"{class_id}_XT_{day}{suffix}"
    rows = []
    for p in participants:
        r = results.get((race, p["id"]))
        if not r:
            continue
        rows.append({
            **_participant_info(p, registry),
            "bib_xt": r.get("bib_int"),
            "time_ms": r.get("time_ms"),
            "pen": r.get("pen") or 0,
            "status": (r.get("status") or "").upper(),
            "xt_rnk": r.get("rnk") or 99999,
            "flt": r.get("flt") or "",
        })
    return rows


def collect_xer_data(participants: list[dict], results: dict, class_id: str,
                     day_xer: str, day_xt: str, attr: str = "",
                     registry: dict | None = None):
    """Pro F sheet — XER (Event Result, eliminace) + XT data pro col 11.

    XER má:
      - `Bib`: pro finalisty číslo (XS bib), pro ne-finalisty 't N' formát
      - `Rnk`: celkové pořadí v eliminaci (faulted runs jsou Canoe123 řazené
        správně až za clean runs)
      - `Time`: pro ne-pavoukové = XT čas, pro pavoukové prázdné
      - `Status`: DNS/DNF/DSQ pro vypadlé

    XT (kvalifikace) data se přibalí pro vyplnění "1. jízda" sloupce v F sheetu
    — bez toho by pavoukoví (bracket) finalisté měli prázdné col 11.
    """
    suffix = f"_{attr}" if attr else ""
    race_xer = f"{class_id}_XER_{day_xer}{suffix}"
    race_xt = f"{class_id}_XT_{day_xt}{suffix}"
    rows = []
    for p in participants:
        r = results.get((race_xer, p["id"]))
        if not r:
            continue
        xt = results.get((race_xt, p["id"])) or {}
        rows.append({
            **_participant_info(p, registry),
            # XER (eliminace):
            "bib_raw": r.get("bib_raw"),
            "bib_int": r.get("bib_int"),
            "time_ms": r.get("time_ms"),
            "status": (r.get("status") or "").upper(),
            "rnk": r.get("rnk"),
            "record_type": (r.get("record_type") or "").upper(),
            "flt": r.get("flt") or "",
            # XT (kvalifikace) — pro col 11 "1. jízda" v F sheet:
            "xt_time_ms": xt.get("time_ms"),
            "xt_status": (xt.get("status") or "").upper(),
            "xt_flt": xt.get("flt") or "",
        })
    return rows


def _has_time(d: dict) -> bool:
    return d.get("time_ms") is not None and d.get("time_ms", 0) > 0 \
        and d.get("status") not in ("DNS", "DNF", "DSQ")


# -------- cell-write helpers (sdílené Q i F sheet) --------

def _set_jun_marker(row: TableRow, is_junior: bool) -> None:
    if is_junior:
        set_cell_string(get_cell_at(row, COL_JUN), "jun.")


def _set_bib(row: TableRow, bib_int=None, bib_raw: str = "") -> None:
    """Zapíše bib — int pokud bib_int, jinak bib_raw jako string."""
    cell = get_cell_at(row, COL_BIB)
    if bib_int is not None:
        set_cell_float(cell, int(bib_int))
    elif bib_raw:
        set_cell_string(cell, bib_raw)


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
    """Vyčistí všechny data sloupce (0-15) řádku — důležité pro šablonu
    vytvořenou kopií Troja, kde zůstávají časy, FLT markery, celkové časy
    a další stale data ve sloupcích, které nepřepisujeme daty z aktuálního
    závodu.

    Body sloupce (16, 17) se NEVYČISTÍ — pro finishery jsou tam statické
    hodnoty z šablony (32, 30, ..., 2 pro top 16 řádků), které správně
    odpovídají ranku podle pozice. Pro DNS / prázdné řádky zavolej
    `_clear_body_cols(row)` zvlášť.
    """
    for col in range(16):
        clear_cell(get_cell_at(row, col))


def _clear_body_cols(row: TableRow) -> None:
    """Vyčistí body sloupce (16, 17) — pro DNS řádky a trailing prázdné řádky.

    Body je v šabloně statická tabulka pozice→bod (32, 30, ..., 2). Pro
    finishery sedí, ale pro DNS / prázdné řádky bys jinak ukazoval body
    pro neexistující rank. Volej až POTÉ co je jasné, že řádek nemá
    finishera s rankem.
    """
    clear_cell(get_cell_at(row, COL_BODY))
    clear_cell(get_cell_at(row, COL_BODY_JUN))


def _set_flt(row: TableRow, flt: str) -> None:
    """Zapíše FLT marker ('FLT(7)' pro failed gate) do col 12. Pokud prázdné,
    necháme buňku vyčištěnou (clear_data_row to už udělalo)."""
    if flt:
        set_cell_string(get_cell_at(row, COL_FLT), flt)


def _set_person_info(row: TableRow, info: dict) -> None:
    """Zapíše jméno, rok narození a oddíl. Cross šablona nemá `reg` sheet,
    takže tyhle hodnoty se musí zapsat přímo (ne přes formuli)."""
    if info.get("name"):
        set_cell_string(get_cell_at(row, COL_NAME), info["name"])
    if info.get("year"):
        try:
            set_cell_float(get_cell_at(row, COL_YEAR), int(info["year"]))
        except (ValueError, TypeError):
            set_cell_string(get_cell_at(row, COL_YEAR), str(info["year"]))
    if info.get("club"):
        set_cell_string(get_cell_at(row, COL_CLUB), info["club"])


def _clear_trailing_rows(rows: list[TableRow], start_idx: int) -> None:
    """Vyčistí trailing single řádky (data + body sloupce). Zastaví u prvního
    řádku s `numberrowsrepeated > 1` — ten reprezentuje stovky/miliony prázdných
    řádků pod ním, ty se nedotýkáme.
    """
    for j in range(start_idx, len(rows)):
        rep = int(rows[j].getAttribute("numberrowsrepeated") or 1)
        if rep > 1:
            break
        _clear_data_row(rows[j])
        _clear_body_cols(rows[j])


# -------- Q sheet (XT — Individual Time Trial) --------

def _write_xt_row(row: TableRow, x: dict, rank: int | None, junior_icfs: set) -> None:
    """Zapíše jeden řádek Q sheetu. `rank=None` znamená DNS (bez poř., bez body)."""
    if rank is None:
        _clear_body_cols(row)
    _set_por(row, rank)
    _set_jun_marker(row, x["icf"] in junior_icfs)
    _set_bib(row, bib_int=x["bib_xt"])
    _set_rgc(row, x["icf"])
    _set_person_info(row, x)
    _set_time(row, x)
    if rank is not None:
        _set_flt(row, x.get("flt", ""))


def fill_q_sheet(sheet: Table, xt_rows: list[dict], junior_icfs: set,
                 clear_body: bool = False) -> int:
    """Naplní Q sheet z XT (Individual Time Trial).

    Finišeři první (seřazení podle XT Rnk), DNS na konci (bez poř., bez body).
    `clear_body=True` — vyčistí body sloupce (16, 17) pro všechny napsané řádky.
    """
    _ensure_data_capacity(sheet, len(xt_rows))
    rows = list(sheet.getElementsByType(TableRow))
    finishers = sorted((x for x in xt_rows if _has_time(x)),
                       key=lambda x: x["xt_rnk"])
    dns = sorted((x for x in xt_rows if not _has_time(x)),
                 key=lambda x: x["bib_xt"] or 99999)

    row_idx = 2
    for rank, x in enumerate(finishers, start=1):
        if row_idx >= len(rows):
            break
        _clear_data_row(rows[row_idx])
        _write_xt_row(rows[row_idx], x, rank, junior_icfs)
        if clear_body:
            _clear_body_cols(rows[row_idx])
        row_idx += 1
    for x in dns:
        if row_idx >= len(rows):
            break
        _clear_data_row(rows[row_idx])
        _write_xt_row(rows[row_idx], x, None, junior_icfs)
        row_idx += 1

    _clear_trailing_rows(rows, row_idx)
    return row_idx - 2


# -------- F sheet (XER — Event Result eliminace) --------

# Kategorie XER záznamu pro F sheet.
CAT_BRACKET = "bracket"        # postoupil do pavouka, Bib = XS číslo
CAT_NON_BRACKET = "non_bracket"  # nepostoupil, ale měl čas z XT; Bib = "t N"
CAT_DNS = "dns"                # DNS/DNF/DSQ nebo bez času


def _classify_xer_row(x: dict) -> str:
    """Vrátí CAT_BRACKET / CAT_NON_BRACKET / CAT_DNS pro XER záznam.

    Klíč je tvar `Bib`: pavoukoví mají čistě číslo (např. "3"),
    ne-pavoukoví mají "t N" string. To je nejspolehlivější signál
    (RecordType F/SF/QF/T má víc úrovní, neslouží jednoznačně).
    """
    in_bracket = x["bib_int"] is not None and not x["bib_raw"].startswith("t")
    if in_bracket:
        return CAT_BRACKET
    is_dns = x["status"] in ("DNS", "DNF", "DSQ") or x["rnk"] is None
    if is_dns or not x["time_ms"]:
        return CAT_DNS
    return CAT_NON_BRACKET


def _sort_xer_rows(xer_rows: list[dict]) -> list[tuple[dict, str]]:
    """Vrátí [(row, category), …] seřazené pro zápis do F sheetu.

    Pořadí: pavoukoví (XER Rnk 1-8) → ne-pavoukoví finišeři (XER Rnk 9+) → DNS.

    Pro ne-pavoukové NEPOUŽÍVAT sort podle `time_ms` — Canoe123 v XER Rnk
    už správně řadí faulted runs ZA clean runs (KOS s FLT(3) má time 61.04s
    ale rnk 24, až za clean runs s časy 79.46s). Sort by time by ho dal nahoru.
    """
    cats = [(x, _classify_xer_row(x)) for x in xer_rows]
    bracket = [(x, c) for x, c in cats if c == CAT_BRACKET]
    non_bracket = [(x, c) for x, c in cats if c == CAT_NON_BRACKET]
    dns = [(x, c) for x, c in cats if c == CAT_DNS]
    bracket.sort(key=lambda t: t[0]["rnk"] or 99999)
    non_bracket.sort(key=lambda t: t[0]["rnk"] or 99999)
    dns.sort(key=lambda t: t[0].get("bib_int") or 99999)
    return bracket + non_bracket + dns


def _write_xt_time_or_status(row: TableRow, xt_time_ms, xt_status: str, xt_flt: str) -> None:
    """Zapíše do "1. jízda" (col 11) XT čas nebo status. Pro F sheet — bracket
    finalisté nemají XT čas v XER, ale tu informaci chceme ukázat (vyhledali
    jsme ji v XT race).
    """
    if xt_time_ms:
        set_cell_float(get_cell_at(row, COL_TIME_Q), xt_time_ms / 1000.0)
        if xt_flt:
            _set_flt(row, xt_flt)
    elif xt_status in ("DNS", "DNF", "DSQ"):
        set_cell_string(get_cell_at(row, COL_TIME_Q), xt_status)


def _write_xer_row(row: TableRow, x: dict, category: str,
                   seq_rank: int | None, junior_icfs: set) -> None:
    """Zapíše jeden řádek F sheetu podle kategorie.

    "1. jízda" (col 11) se zapisuje z XT dat pro VŠECHNY kategorie (i bracket),
    protože XER pro bracket finalisty Time element nemá.
    """
    if seq_rank is None:
        # DNS: bez poř., bez body (template body table by dávala chybný bod).
        _clear_body_cols(row)
    else:
        _set_por(row, seq_rank)
    _set_jun_marker(row, x["icf"] in junior_icfs)

    # XT data v "1. jízda" sloupci — sdílené pro bracket / non-bracket / DNS.
    _write_xt_time_or_status(row, x.get("xt_time_ms"), x.get("xt_status", ""),
                              x.get("xt_flt", ""))

    # Bib a finále rank podle kategorie.
    if category == CAT_BRACKET:
        _set_bib(row, bib_int=x["bib_int"])
        if x["rnk"] is not None:
            _set_final_rank(row, x["rnk"])
    else:
        # Ne-pavoukoví (finišer i DNS) mají "t N" bib.
        _set_bib(row, bib_raw=x["bib_raw"])

    _set_rgc(row, x["icf"])
    _set_person_info(row, x)


def fill_f_sheet(sheet: Table, xer_rows: list[dict], junior_icfs: set,
                 clear_body: bool = False) -> int:
    """Naplní F sheet z XER (Event Result, eliminace).

    Sloupce z XER:
      - Finalisté (Bib = číslo): postoupili do pavouka, col 13 = finále rank
      - Ne-finalisté (Bib = "t N"): col 11 = XT čas (lookup z XT race)
      - DNS (Bib = "t N", Status=DNS nebo Rnk=None): poř. prázdné, XT status v col 11

    `clear_body=True` — vyčistí body sloupce (16, 17) pro všechny napsané řádky.
    """
    _ensure_data_capacity(sheet, len(xer_rows))
    rows = list(sheet.getElementsByType(TableRow))

    row_idx = 2
    seq_rank = 0
    for x, category in _sort_xer_rows(xer_rows):
        if row_idx >= len(rows):
            break
        _clear_data_row(rows[row_idx])
        if category == CAT_DNS:
            _write_xer_row(rows[row_idx], x, category, None, junior_icfs)
        else:
            seq_rank += 1
            _write_xer_row(rows[row_idx], x, category, seq_rank, junior_icfs)
        if clear_body:
            _clear_body_cols(rows[row_idx])
        row_idx += 1

    _clear_trailing_rows(rows, row_idx)
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


def update_sheet_header_date(sheet: Table, date: str) -> None:
    """Přepíše datum v hlavičce cross sheetu (row 0 col 2).

    Cross šablony nemají `param` sheet (na rozdíl od slalomu) — datum
    se zapisuje přímo do hlavičky každého výsledkového sheetu.
    """
    rows = list(sheet.getElementsByType(TableRow))
    if not rows:
        return
    set_cell_string(get_cell_at(rows[0], 2), date)


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
    ap.add_argument("--no-body", action="store_true",
                    help="Smaž body sloupce (16, 17). Pro žákovské kategorie, kde body nedávají smysl.")
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

    # Načti registr (`reg` sheet) — pro lookup zkratky oddílu.
    # Cross šablona ho nemusí mít (např. Troja jen vyplnila plain text).
    registry = load_registry(doc)
    if registry:
        print(f"  registr: {len(registry)} osob")

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

        if sheet_q is not None:
            xt_rows = collect_xt_data(parts, results, cls, day, attr, registry)
            n = fill_q_sheet(sheet_q, xt_rows, junior_icfs,
                             clear_body=args.no_body)
            if args.date:
                update_sheet_header_date(sheet_q, args.date)
            print(f"  {cls} → {sheet_q.getAttribute('name')}: {n} řádků v kvalifikaci")

        if sheet_f is not None:
            xer_rows = collect_xer_data(parts, results, cls,
                                         day_xer=day_final, day_xt=day,
                                         attr=attr, registry=registry)
            n = fill_f_sheet(sheet_f, xer_rows, junior_icfs,
                             clear_body=args.no_body)
            if args.date:
                update_sheet_header_date(sheet_f, args.date)
            print(f"  {cls} → {sheet_f.getAttribute('name')}: {n} řádků v eliminaci")

    print(f"Ukládám: {args.output}")
    doc.save(args.output)
    print("Hotovo.")


if __name__ == "__main__":
    main()
