"""Vyrobí prázdné Eskymo cross šablony pro ČB žákovské kategorie.

Manipuluje content.xml přímo přes zipfile — odfpy deepcopy mu nefunguje
(cache mismatch u removeChild).
"""
import re
import shutil
import sys
import io
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent
TROJA_EMPTY = ROOT.parent.parent / "tests" / "2026.troja.cross" / "empty.ods"

CATEGORIES = ["X1M-ZM", "X1M-ZS", "X1Z-ZM", "X1Z-ZS"]


def _read_ods_content(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as z:
        return z.read("content.xml").decode("utf-8")


def _write_ods(source: Path, dest: Path, new_content_xml: str):
    """Zkopíruje source ODS do dest, přepíše content.xml."""
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(source, "r") as src, \
         zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.namelist():
            if item == "content.xml":
                dst.writestr(item, new_content_xml.encode("utf-8"))
            else:
                dst.writestr(item, src.read(item))


def _extract_sheet_xml(content: str, sheet_name: str) -> str:
    """Najde a vrátí <table:table table:name="…"> … </table:table> blok."""
    pattern = re.compile(
        r'<table:table\b[^>]*table:name="' + re.escape(sheet_name) +
        r'"[^>]*>.*?</table:table>',
        re.DOTALL,
    )
    m = pattern.search(content)
    if not m:
        raise RuntimeError(f"Nenalezen sheet '{sheet_name}' v content.xml")
    return m.group(0)


def _rename_sheet(sheet_xml: str, new_name: str, new_header_label: str = None) -> str:
    """Vrátí kopii sheet XML s změněným jménem a hlavičkou (volitelně)."""
    new_xml = re.sub(
        r'(<table:table\b[^>]*table:name=")[^"]+(")',
        r'\g<1>' + new_name + r'\g<2>',
        sheet_xml,
        count=1,
    )
    if new_header_label is not None:
        # První <text:p>…</text:p> v prvním <table:table-cell> v prvním <table:table-row>
        # je hlavička. Najít první výskyt <text:p>…</text:p> a nahradit.
        new_xml = re.sub(
            r'(<text:p[^>]*>)[^<]*(</text:p>)',
            r'\g<1>' + new_header_label + r'\g<2>',
            new_xml,
            count=1,
        )
    return new_xml


def make_template(source_sheet_name: str, output_path: Path,
                  suffix: str, label_suffix: str):
    content = _read_ods_content(TROJA_EMPTY)
    base_sheet_xml = _extract_sheet_xml(content, source_sheet_name)

    # Najít všechny <table:table…</table:table> a smazat (kromě hlaviček apod.)
    # Pak vložit nové
    # Vlastně lépe: nahradit všechny existující tabulky našimi novými
    new_sheets_xml = ""
    for cls in CATEGORIES:
        label = f"{cls} {label_suffix}".strip()
        new_sheets_xml += _rename_sheet(base_sheet_xml, f"{cls}-{suffix}", label)

    # Najít první <table:table> a poslední </table:table>; nahradit blok
    first_match = re.search(r'<table:table\b', content)
    if not first_match:
        raise RuntimeError("V content.xml nejsou žádné <table:table> elementy")
    last_match = None
    for m in re.finditer(r'</table:table>', content):
        last_match = m
    if last_match is None:
        raise RuntimeError("V content.xml nejsou žádné </table:table> elementy")

    new_content = (
        content[:first_match.start()]
        + new_sheets_xml
        + content[last_match.end():]
    )
    _write_ods(TROJA_EMPTY, output_path, new_content)
    print(f"Uloženo: {output_path}  ({len(CATEGORIES)} sheetů: {', '.join(f'{c}-{suffix}' for c in CATEGORIES)})")


if __name__ == "__main__":
    make_template("MX1-indiv.", ROOT / "cb_TT.ods", suffix="Q", label_suffix="Individuál")
    make_template("MX1-F",      ROOT / "cb_F.ods",  suffix="F", label_suffix="")
