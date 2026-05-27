"""Regresní testy pro canoe2eskymo.

Pro každou složku v `tests/<fixture>/` s `config.json`:
  1. Spustí `canoe2eskymo.py <canoe123.xml> <empty.ods> <tmp_output.ods> + args`
  2. Porovná výstup s `manual_reference/*.ods` pomocí `compare.py`
  3. Vyfiltruje rozdíly, které jsou v `known_differences`
  4. Selže, pokud existuje nečekaný rozdíl

Použití:
    py tests/run_tests.py              # spustí všechny fixtures
    py tests/run_tests.py 2026.cb      # jen jeden fixture
    py tests/run_tests.py --verbose    # ukáže i očekávané rozdíly

Návratový kód: 0 = vše OK, 1 = regrese, 2 = chyba runneru.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

THIS_DIR = Path(__file__).parent
ROOT = THIS_DIR.parent
SCRIPT = ROOT / "canoe2eskymo.py"

sys.path.insert(0, str(THIS_DIR))
import compare  # noqa: E402


class TestFailure(Exception):
    pass


def discover_fixtures(filter_name: str | None = None) -> list[Path]:
    out = []
    for child in sorted(THIS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "config.json").exists():
            continue
        if filter_name and child.name != filter_name:
            continue
        out.append(child)
    return out


def run_script(fixture: Path, run: dict, output: Path) -> subprocess.CompletedProcess:
    xml = fixture / "canoe123.xml"
    empty = fixture / "empty.ods"
    args = run["args"]
    cmd = [
        sys.executable, str(SCRIPT), str(xml), str(empty), str(output),
        "--day", str(args["day"]),
    ]
    if "race" in args:
        cmd += ["--race", str(args["race"])]
    if "date" in args:
        cmd += ["--date", str(args["date"])]
    if "name" in args:
        cmd += ["--name", str(args["name"])]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def matches_known(diff: dict, known: dict) -> bool:
    """True pokud diff odpovídá popisu v known_differences entry."""
    if diff["type"] != known.get("type"):
        return False
    if diff["sheet"] != known.get("sheet"):
        return False
    if diff["key"] != known.get("key"):
        return False
    if diff["type"] == "value_diff":
        if known.get("field") and diff["field"] != known["field"]:
            return False
    return True


def classify_diffs(diffs: list[dict], known: list[dict]) -> tuple[list, list]:
    """Vrátí (matched_known, unexpected)."""
    matched = []
    unexpected = []
    for d in diffs:
        if any(matches_known(d, k) for k in known):
            matched.append(d)
        else:
            unexpected.append(d)
    return matched, unexpected


def run_fixture(fixture: Path, verbose: bool) -> dict:
    """Vrátí dict {ok, runs: [{id, status, …}]}."""
    config = json.loads((fixture / "config.json").read_text(encoding="utf-8"))
    fixture_result = {"ok": True, "name": config.get("name", fixture.name), "runs": []}

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for run in config.get("runs", []):
            run_id = run["id"]
            output = td_path / f"{run_id}.ods"
            proc = run_script(fixture, run, output)
            run_result = {"id": run_id, "status": "?", "stdout": proc.stdout,
                          "stderr": proc.stderr, "unexpected": [], "known_matched": []}

            if proc.returncode != 0:
                run_result["status"] = "script_failed"
                run_result["error"] = (
                    f"exit={proc.returncode}\nstderr:\n{proc.stderr}"
                )
                fixture_result["ok"] = False
                fixture_result["runs"].append(run_result)
                continue

            ref = fixture / run["manual_reference"]
            if not ref.exists():
                run_result["status"] = "no_reference"
                run_result["error"] = f"reference '{ref}' neexistuje"
                fixture_result["ok"] = False
                fixture_result["runs"].append(run_result)
                continue

            diffs = compare.compare_files(str(output), str(ref))
            known = run.get("known_differences", [])
            matched, unexpected = classify_diffs(diffs, known)

            run_result["known_matched"] = matched
            run_result["unexpected"] = unexpected

            if unexpected:
                run_result["status"] = "regression"
                fixture_result["ok"] = False
            elif matched:
                run_result["status"] = "ok_with_known_diffs"
            else:
                run_result["status"] = "ok"

            fixture_result["runs"].append(run_result)

    return fixture_result


def print_result(result: dict, verbose: bool):
    name = result["name"]
    if result["ok"]:
        n_diffs = sum(len(r["known_matched"]) for r in result["runs"])
        suffix = f" ({n_diffs} known diffs)" if n_diffs else ""
        print(f"  ✓ {name}{suffix}")
    else:
        print(f"  ✗ {name}")
    for run in result["runs"]:
        st = run["status"]
        if st == "ok":
            if verbose:
                print(f"     · {run['id']}: OK")
        elif st == "ok_with_known_diffs":
            n = len(run["known_matched"])
            if verbose:
                print(f"     · {run['id']}: OK ({n} known)")
                for d in run["known_matched"]:
                    print(f"         · {compare.format_diff(d)}")
        elif st == "regression":
            n = len(run["unexpected"])
            print(f"     · {run['id']}: REGRESE — {n} nečekaných rozdílů")
            for d in run["unexpected"][:20]:
                print(f"         ! {compare.format_diff(d)}")
            if n > 20:
                print(f"         … +{n - 20} dalších")
        elif st == "script_failed":
            print(f"     · {run['id']}: SKRIPT SELHAL")
            print(f"       {run.get('error', '')}")
        elif st == "no_reference":
            print(f"     · {run['id']}: chybí reference ({run.get('error', '')})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("fixture", nargs="?", help="Jen tento fixture (např. 2026.cb)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Ukázat i očekávané (known) rozdíly")
    args = ap.parse_args()

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    fixtures = discover_fixtures(args.fixture)
    if not fixtures:
        target = args.fixture or "tests/"
        print(f"Žádné fixtures k testování v {target}", file=sys.stderr)
        sys.exit(2)

    if not SCRIPT.exists():
        print(f"canoe2eskymo.py nenalezen na {SCRIPT}", file=sys.stderr)
        sys.exit(2)

    print(f"Spouštím {len(fixtures)} fixture(s):")
    results = []
    for fx in fixtures:
        result = run_fixture(fx, args.verbose)
        results.append(result)
        print_result(result, args.verbose)

    total = sum(len(r["runs"]) for r in results)
    failed = sum(1 for r in results for run in r["runs"]
                 if run["status"] in ("regression", "script_failed", "no_reference"))
    passed = total - failed

    print()
    print(f"Výsledek: {passed}/{total} runs prošlo")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
