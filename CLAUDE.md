# CLAUDE.md — orientace pro AI agenty

Tento dokument je pro AI agenty (Claude Code a podobné), kteří v tomhle
repu provádějí změny. Cíl: rychle se zorientovat, nesvarat zavedené
invarianty a vědět, jak ověřit, že úprava neporušila chování.

## Co repo dělá

Dva samostatné skripty:

- [`canoe2eskymo.py`](canoe2eskymo.py) — **slalom** (C1/K1/C2/PZ).
  Vyplní startovku a výsledky obou kol z Canoe123 XML do Eskymo ODS šablony.
- [`cross.py`](cross.py) — **kajak-kros / cross sprint** (MX1/WX1/MX1J/WX1J,
  X1M-ZS/ZM, X1Z-ZS/ZM). Vyplní kvalifikaci (XT) a finálovou tabulku
  (XS + XER) do cross Eskymo šablony.

Body si pak Eskymo dopočítá samo. Skripty jsou oddělené záměrně —
slalom a cross mají rozdílné race ID formáty, výsledkové struktury
i ODS layouty.

## Důležitá doména

- **Canoe123** — německý SW pro správu závodů kanoistiky. Exportuje
  jeden XML soubor s `<Participants>` + `<Results>` pro celý závodní
  víkend (více dnů).
- **Eskymo** — český ODS template ČSK. Skript zapisuje do `*_sl` (startovka:
  `stč`, `rgc`) a do hlavních sheetů (`time1`/`pen1`/`time2`/`pen2`).
  Eskymo má v rgc sloupci vzorec, který z `reg`/`cizi` doplní jméno,
  ročník, oddíl, věkovou kategorii, …
- **RGC** = registrační číslo závodníka (Český svaz kanoistů). Čísla pro
  Čechy, `A*` formát pro cizince.
- **BHZ** = "běh hlavního závodu" — interní označení; uvádí se v `param`.
- **DNS / DNF / DSQ** = did not start / finish / disqualified. V Eskymu
  se zapisují jako text do `time` sloupce, do `pen` = `999`.

## Klíčové invarianty (NEPORUŠIT bez vědomého rozhodnutí)

1. **Pravidlo zařazení**: závodník je ve výstupu, pokud má v XML alespoň
   jeden `<Results>` záznam pro daný den. Účastník jen v
   `<Participants>` (přihlásil se, ale Canoe123 ho do běhu nezařadil) se
   přeskočí. **DNS+DNS není věcně chyba — zařadit.**
2. **Každá zařazená jízda má buď čas nebo DNS** (`pen` 999). Nikdy
   prázdná buňka.
3. **Pořadí pádlerů v deblu odpovídá XML** (`FamilyName` první,
   `FamilyName2` druhý). Eskymo to nezatřesúje, ale konzistence
   pomáhá uživatelům.
4. **Skript NEPŘEPISUJE `reg` sheet** — bere ho jako read-only zdroj
   pravdy. **Píše do `cizi` sheetu** pouze, pokud najde cizince, který
   v něm chybí (auto-add).
5. **Generované A* RGC** vždy 5 cifer (`A90003`), navazují na maximum
   v `cizi`.
6. **Po vyplnění N účastníků se musí pre-fill řádky N+1, …, M v
   data sheetu PŘEČÍSLOVAT na unikátní id**. Šablona má často náhodně
   předvyplněné id, např. `id=1` ve 4. řádku (template residue). Bez
   přečíslování by VLOOKUP v Eskymu zobrazil **dvakrát** prvního
   závodníka. To je viditelná chyba v Excelu, ne jen v testech.

## Architektura skriptu

`canoe2eskymo.py` je jediný soubor (~25 KB). Hlavní funkce:

| Funkce | Co dělá |
| --- | --- |
| `parse_xml()` | XML → `participants_by_class`, `results` dict |
| `load_registry()` | Otevře `reg` a `cizi` sheety šablony, build name→RGC lookupy |
| `_resolve_rgc()` | Pro účastníka určí, co zapsat do `rgc` sloupce |
| `_resolve_paddler()` | Pomocná, hledá RGC jednoho pádlerа |
| `lookup_person()` | Name lookup s disambiguací podle roku |
| `split_double_icf()` | Fallback pro starý XML formát "slepený" ICFId |
| `add_foreigner()` | Generuje nové A* RGC, zapisuje do `new_cizi_entries` |
| `fill_startlist()` | Zápis do `*_sl` sheetu |
| `fill_results()` | Zápis časů do hlavního sheetu |
| `_write_run()` | Logika pro jednu jízdu (čas / DNS) |
| `_clear_trailing_ids()` | Po vyplnění N řádků přečísluje pre-fill řádky N+1, N+2, … aby nevznikla duplicita id (jinak by VLOOKUP duplikoval prvního závodníka v Eskymu) |
| `write_cizi_entries()` | Vloží nové cizince do `cizi` sheetu |
| `update_param()` | Volitelně přepíše datum/číslo/název v `param` |
| `main()` | CLI |

Volání: `main()` → načte XML → otevře ODS → `update_param()` → pro každou
třídu `fill_startlist()` + `fill_results()` → `write_cizi_entries()` → save.

## Kvirky Canoe123 XML, na které pozor

1. **Dvě formy ICFId pro deble**:
   - **Starý**: jediný `<ICFId>` jako slepenec dvou RGC, např.
     `108112032` = `1081` + `12032`. Délky variabilní (4+5, 5+5, 4+4)
     — nelze splittovat triviálně.
   - **Nový**: samostatné `<ICFId>` a `<ICFId2>`. Skript preferuje nový
     formát; starý jen jako fallback.

2. **ICFId × Id mismatch**: viděno v Letní 2025 — `<Id>23116.K1M</Id>`
   s `<ICFId>23121</ICFId>` pro ČERNÝ Vojtěch. RGC 23116 patří Štindlovi.
   **Trust ICFId, ne Id.**

3. **Cizinci nemají `<ICFId>`** (vůbec — element neexistuje). Mají
   GUID-like `<Id>638889…</Id>`. Skript je identifikuje podle absence
   ICFId a hledá / přidává do `cizi`.

4. **"Skrytý DNS"**: někdy je `<Status />` prázdný, ale chybí `<Time>`.
   To je tichý DNS — nezávodil v této jízdě. Skript zapíše DNS+999.

5. **Disambiguace podle roku**: v Letní 2025 jsou dva "Rašner Karel"
   (1976 + 2004). XML `<Birthdate2>` říká rok pádlerа, `reg` má taky
   rok. Při lookupu se musí zkontrolovat.

6. **Duplicitní jména v `cizi`**: Cizí jména v `cizi` se zapisují v
   originálním tvaru (často mixed case: `Söhnall Linde`). XML je píše
   uppercase u příjmení (`Söhnall` zde výjimečně mixed). Lookup je
   case-insensitive — porovnává v upper.

7. **Spelling drift mezi XML a `cizi`**: jeden závod měl v XML
   `Söhnall Linda`, v `cizi` `Söhnall Linde` — překlep. Skript je
   nepárová a přidá Lindu jako nové A* RGC. Lidská kontrola pak musí
   posoudit, jestli sloučit.

## Sheety v Eskymo šabloně, kterým rozumíme

| Sheet | Sloupce, které čteme | Sloupce, do kterých píšeme |
| --- | --- | --- |
| `param` | — | A8 (datum), A9 (číslo), A3 (název) — jen pokud `--date/--race/--name` |
| `reg` | A: RGC, B: příjmení, C: jméno, D: rok | nikdy |
| `cizi` | A: A* RGC, B: fullname (orig case), M: klub | append na konec dat |
| `*_sl` | — | A: id (sekvenční), B: stč, C: rgc |
| `c1m`, `c1z`, `k1m`, `k1z`, `c2m`, `c2z`, `c2x`, `pzk`, `pzc` | — | A: id, L: time1, M: pen1, O: time2, P: pen2 |

Sheety jsou volitelné — pokud šablona nemá `pzk_sl`, PZ se přeskočí
(logovaným warningem).

## Jak ověřit, že změna neporušila chování

```bash
python tests/run_tests.py
```

Pokud projde, jsi v pohodě. Pokud regrese:

- **Nečekaná**: něco se ti rozbilo, debugovat.
- **Zamýšlená změna chování**: aktualizuj `tests/<fixture>/config.json` →
  `known_differences` u příslušného běhu.

Detaily v [`tests/README.md`](tests/README.md).

## Test fixtures (současně 5)

**Slalom (4):**

1. `tests/2026.cb/` — ČPŽ ČB, kompletní 1.7.1 šablona, bez deblů ani PZ
   v šabloně. Test, že skript správně **přeskočí** chybějící sheety.
2. `tests/2025.klatovske/` — Klatovské slalomy ve Strakonicích. Plný
   rozsah: PZ, C2*, kompletní cizi.
3. `tests/2025.letni/` — Letní 2025 ČB. Plný rozsah včetně **chyb v
   ručních souborech**, **2 stejných jmen v reg**, **překlepu Linda/Linde**.
   Stress test pro lookup logiku.
4. `tests/2026.opava/` — ČPŽ Opava, jen C1/K1, **bez** PZ ani C2*.
   Test, že skript nehlásí absurdní warningy, když XML má víc tříd než
   šablona.

**Cross (1):**

5. `tests/2026.troja.cross/` — ČP3 Troja, kajak-kros. Senior MX1/WX1
   + Junior MX1J/WX1J s 'jun.' markerem v MX1-F. Test pro `cross.py`.
   `config.json` má `"kind": "cross"`.

## Cross sprint — XER = eliminační event result

Canoe123 cross sprint má 4 race ID disciplíny: XT (Time Trial), XS
(Semifinal), XF (Final), XER (Event Result). **XER je golden source**
— Canoe123 do něj sám vše agreguje:

- `<Bib>` v XER:
  - **číslo** (např. "3") = byl v pavoukovi, je to XS bib
  - **"t N"** (např. "t    3") = nebyl v pavoukovi, je to XT bib s prefixem
- `<Rnk>` v XER = celkové pořadí
- `<Time>` v XER (pro ne-pavoukové) = XT čas
- `<Status>` v XER = DNS/DNF/DSQ pokud platí
- `<RecordType>` v XER má víc úrovní (F/SF/QF/T podle hloubky pavouka)
  — **nepoužívej**, místo toho detekuj pavouka přes formát `Bib`.

Skript `cross.py` proto pro F sheet čte JEN XER. XT používá jen pro
Q sheet (kvalifikační listina).

## Časté úkoly

### Přidání nové třídy (např. `MIX`, `XCM`, …)

1. Přidej do `CLASS_TO_SHEETS` v `canoe2eskymo.py`.
2. Pokud je to debl, přidej do `DOUBLE_CLASSES`.
3. Přidej test fixture, který má příslušné sheety v šabloně.
4. Spusť `python tests/run_tests.py` — všechny stávající fixtures musí
   pořád projít.

### Změna logiky DNS handling

V `_write_run()`. Pozor na invarianty (žádná prázdná buňka pro zařazeného
závodníka). Spusť testy.

### Změna logiky cizinců

V `_resolve_paddler()` + `add_foreigner()` + `write_cizi_entries()`.
Spouštěj test `2025.letni` — má nejvíc cizinců.

## Co NEDĚLAT

- **Neupravovat `manual_reference/*.ods`** — to jsou původní lidské
  výstupy, slouží jako historický důkaz "tak to dělal člověk". Změna
  by maskovala regrese.
- **Nepřepisovat `reg` sheet** — read-only zdroj pravdy.
- **Nemazat `experiments/`** — uživatelská data, future scope.
- **Necommitovat generované `*_z_xml.ods`** — gitignored, regenerují
  se z testů.

## Konvence

- Komentáře a uživatelské zprávy v **češtině** (cílová skupina jsou
  čeští organizátoři závodů).
- Identifikátory a docstring summary v Pythonu jsou taky česky kde to
  dává smysl. Standardní Python konstrukty zůstávají anglicky.
- Datum/čas v Eskymu se zapisuje českým formátem `DD.MM.YY`.
