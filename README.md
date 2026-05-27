# canoe123-2-eskymo

> Převod výsledků slalomu z Canoe123 XML do Eskymo ODS šablony.

[![Tests](https://github.com/CzechCanoe/canoe123-2-eskymo/actions/workflows/test.yml/badge.svg)](https://github.com/CzechCanoe/canoe123-2-eskymo/actions/workflows/test.yml)

Skript [`canoe2eskymo.py`](canoe2eskymo.py) načte XML export z **Canoe123**
a vyplní prázdnou **Eskymo** ODS šablonu — startovku (`*_sl` sheets)
i výsledky obou jízd (`c1m`, `c1z`, `k1m`, `k1z`, `c2m`, `c2z`, `c2x`,
`pzk`, `pzc`, pokud v šabloně existují). Body (čpž / oč / om) si
Eskymo dopočítá samo jedním tlačítkem.

Cíl: ušetřit pořadateli závodu několik desítek minut ručního opisování
po každém kole a odstranit zdroj překlepů.

## Instalace

Stačí Python 3.10+ a jedna knihovna:

```bash
python -m pip install -r requirements.txt
```

## Použití

```bash
# Standardní (3. ČPŽ – den 23.05., závod 49)
python canoe2eskymo.py canoe123.xml empty.ods output.ods \
    --day 23 --race 49 --date 23.05.26 --name "3. ČPŽ"

# Druhý den téhož závodu — stejná šablona, jiné datum/číslo
python canoe2eskymo.py canoe123.xml empty.ods output_den2.ods \
    --day 24 --race 51 --date 24.05.26 --name "4. ČPŽ"
```

Argumenty:

- **`xml`**: Canoe123 export (jeden soubor s `<Participants>` + `<Results>`
  za celý závodní víkend).
- **`empty.ods`**: Prázdná Eskymo šablona (viz "Příprava šablony" níže).
- **`output.ods`**: Cesta pro vygenerovaný soubor.
- **`--day`** (povinný): poslední 1-2 znaky `RaceId` v XML — kalendářní
  den, např. `4`, `5`, `23`, `25`.
- **`--race`, `--date`, `--name`** (volitelně): přepíšou hodnoty
  v sheetu `param` (jinak zůstanou ze šablony).

## Příprava šablony (jednou před závodem)

Skript dostane prázdnou Eskymo šablonu a jen do ní zapíše startovku
a časy. Všechno ostatní si musíš v Eskymu připravit ručně:

### 1. Založit Eskymo soubor pro daný závod

V Eskymu (Český svaz kanoistů): **Nový závod** → typ slalomu (BHZ +
disciplína) → uložit jako `<něco>_empty.ods`.

Šablona musí mít minimálně tyto sheety:

| Sheet | Účel | Co s ním skript dělá |
| --- | --- | --- |
| `param` | datum, číslo závodu, název, místo, ředitel, … | volitelně přepíše `--date/--race/--name` |
| `reg` | čeští závodníci (RGC → jméno, rok, oddíl) | jen lookup (read-only) |
| `cizi` | cizinci (A* RGC → fullname, klub) | lookup + auto-add chybějících |
| `c1m`, `c1z`, `k1m`, `k1z` (+ `_sl`) | C1/K1 výsledky a startovky | povinné, pokud chceš tyto kategorie |
| `c2m`, `c2z`, `c2x` (+ `_sl`) | deble | volitelné |
| `pzk`, `pzc` (+ `_sl`) | průvodní (recreational) závody | volitelné |

Pokud Eskymo neumí přidávat sheety přímo, vezmi šablonu pro podobný
závod, který už deble/PZ má, a duplikuj.

### 2. Nastavit parametry závodu

V sheetu `param`: datum, číslo závodu, BHZ, místo, ředitel, rozhodčí,
disciplína, bodování (`bhz-č` / `čpž` / `oč` / `om`). Skript přepíše
jen `--date`, `--race`, `--name` (pokud zadáš), zbytek nech.

### 3. Importovat aktuální registr (`reg`)

V Eskymu si stáhni aktuální evidenci ČSK a importuj do `reg` sheetu.
Skript ji čte pro každý lookup jména:

- col A: RGC (číslo)
- col B: Příjmení
- col C: Jméno
- col D: Rok narození (kvůli disambiguaci, když je víc lidí stejného jména)

### 4. Cizinci (`cizi`) — volitelné

Před závodem nemusíš vyplňovat. Když skript narazí v XML na účastníka
bez `<ICFId>` (typicky cizinec), který v `cizi` chybí, **sám mu
přidělí A* RGC a doplní řádek do listu `cizi`**:

```
cizi: doplněno 3 nových záznamů
  + A90003  POSPI Rad      (RUM)
  + A90004  PECEK Mara     (RUM)
  + A90005  Söhnall Maria  (SWE)
```

Generovaná RGC navazují na maximum už existujících (`A` + 5 cifer).

**Pozor na překlepy**: pokud `cizi` má `Söhnall Linde` a XML píše
`Söhnall Linda`, skript je nepovažuje za stejnou osobu a přidá novou.
Ručně sluč nebo oprav.

### 5. Sdílení cizinců mezi dny závodu

Skript přidává cizí jen do **výstupu**, nikoli zpět do šablony. Pro
víkendový závod:

1. Den 1: `empty.ods` → skript → `den1.ods` (s doplněnými cizí).
2. Před druhým spuštěním nakopíruj řádky z `cizi` z `den1.ods` do
   `empty.ods` (nebo si dělej druhý `empty` přímo z `den1.ods`).
3. Den 2: `empty.ods` (už s cizí) → skript → `den2.ods`.

## Jak skript hledá RGC

- **Sólo loď, Czech**: použije `<ICFId>` z XML, pokud je v `reg`.
  Fallback: lookup podle jména (+ rok narození pro disambiguaci).
- **Sólo loď, cizinec**: XML obvykle nemá `<ICFId>`. Lookup podle
  jména v `cizi`. Když chybí → vygeneruje nové A* RGC a doplní `cizi`.
- **Deblová loď (C2*)**:
  - Nový formát XML (`<ICFId>` + `<ICFId2>`) — použije se přímo.
  - Starý formát (slepený `<ICFId>` jako `108112032`) — rozdělí
    podle jmen pádlerů; fallback je všechna možná dělení proti `reg`.
  - Pořadí v buňce odpovídá XML (paddler 1 první).

## DNS / DNF / DSQ — pravidla

| Co je v XML `<Results>` | Co skript zapíše do Eskyma |
| --- | --- |
| `Status=DNS/DNF/DSQ` | `DNS`/`DNF`/`DSQ` + pen `999` |
| `Status` prázdný, ale chybí `Time` (skrytý DNS) | `DNS` + pen `999` |
| `Time` + `Pen` v pořádku | čas (sec) + pen |

### Kdo je ve výstupu

Striktně podle `<Results>`:

- Účastník bez **žádného** `<Results>` záznamu pro daný den → vynechán
  (typicky odhlášení před losováním).
- Účastník s `<Results>` záznamy, byť oba DNS → zařazen
  (DNS+DNS není věcně chyba).

## Co skript NEdělá / NEMŮŽE

- **Nepočítá body** čpž / oč / om — řeší Eskymo (jedno tlačítko v Eskymu).
- **Startovní časy** (sloupce I/J v `*_sl`) — neplní.
- **Nevytváří chybějící sheety** — pokud šablona nemá `pzk_sl`, PZK
  účastníci se přeskočí a skript to zaloguje.

## Cross sprint — samostatný skript

Pro **kajak-kros / cross sprint** je samostatný skript
[`cross.py`](cross.py). Cross má jiný formát závodu (kvalifikace XT
+ pavouk XS/XF) a jiné Eskymo sheety — proto je oddělený, ať není
slalom skript zbytečně komplikovaný.

```bash
python cross.py <xml> <empty_cross.ods> <output.ods> --day 26
```

Cross skript vyplní v šabloně pro každou cross kategorii
(MX1, WX1, MX1J, WX1J, X1M-ZS, X1Z-ZM, …):

- **Q sheet** (`<class>-Q` / `<class>-indiv.`): kvalifikace seřazená
  podle XT času. Junioři v dospělé kategorii dostanou marker `jun.`.
- **F sheet** (`<class>-F` / `<class>-F-JUN`): kombinace pavouka
  a kvalifikace.
  - Top finalisté (z XER) seřazení s bib v semifinále.
  - Ne-finalisté seřazení podle XT času, bib jako `t {XT_bib}`.
  - DNS na konci.

Test fixture: [`tests/2026.troja.cross/`](tests/2026.troja.cross/).

## Otevření a kontrola výstupu

1. Otevři výstup v LibreOffice/Calc.
2. `Ctrl+Shift+F9` — přepočítá všechny vzorce.
3. Projdi `c1m`, `k1m`, atd. — zkontroluj, že jména, ročníky, oddíly
   se vyplnily ze vzorců (lookup z `reg`).
4. Pokud nějaký řádek ukazuje prázdné jméno přestože je tam RGC,
   znamená to, že RGC není v `reg` (nebo má překlep).

## Testy

Regresní testy ověřují, že chování skriptu se nemění napříč 4 reálnými
testovacími sadami (ČPŽ ČB 2026, Klatovy 2025, Letní 2025, Opava 2026):

```bash
python tests/run_tests.py            # spustí všechny
python tests/run_tests.py 2026.cb    # jeden fixture
python tests/run_tests.py -v         # ukáže i očekávané rozdíly
```

Detaily v [`tests/README.md`](tests/README.md).

## Přispívání

Pull requesty vítány. Před PR:

1. Spusť `python tests/run_tests.py` — musí projít.
2. Pokud měníš chování, popiš to v PR a aktualizuj `known_differences`
   v příslušných `tests/<fixture>/config.json`.
3. Pokud přidáváš nový závod jako testovací sadu, viz
   [`tests/README.md`](tests/README.md).

Pro AI agenty (Claude Code apod.): viz [`CLAUDE.md`](CLAUDE.md).

## Licence

MIT — viz [`LICENSE`](LICENSE).
