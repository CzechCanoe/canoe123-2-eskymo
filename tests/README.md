# Regresní testy

Spuštění:

```bash
python tests/run_tests.py              # všechny fixtures
python tests/run_tests.py 2026.cb      # jen jeden
python tests/run_tests.py -v           # ukáže i očekávané rozdíly
```

Návratový kód: `0` = vše v pořádku, `1` = regrese, `2` = chyba runneru.
CI ([.github/workflows/test.yml](../.github/workflows/test.yml)) volá
runner při každém push i pull request.

## Co testujeme

Pro každý fixture (`tests/<rok.místo>/`):

1. **Spustíme skript** s argumenty z `config.json` (xml + empty.ods → tmp output).
2. **Porovnáme výstup** s ručně vyplněným `manual_reference/*.ods` (semanticky,
   bib po bibu).
3. **Vyfiltrujeme známé rozdíly** uvedené v `config.json` →
   `known_differences` (typicky chyby v ručních souborech, pořadí cizinců
   apod.).
4. **Selžeme**, pokud zbude libovolný nečekaný rozdíl, nebo skript spadne.

## Struktura fixture

```
tests/2025.letni/
├── canoe123.xml             # XML export z Canoe123
├── empty.ods                # prázdná Eskymo šablona (s nakopírovaným reg)
├── config.json              # běhy + očekávané rozdíly
└── manual_reference/        # ručně vyplněné soubory pro srovnání
    ├── 101.ods
    └── 102.ods
```

### config.json

```jsonc
{
  "name": "Letní slalomy 2025",
  "description": "popis pro lidi",
  "runs": [
    {
      "id": "101",                              // identifikátor běhu, použit v tmp output
      "args": {
        "day": "26",                            // --day
        "race": 101,                            // --race  (volitelné)
        "date": "26.07.25",                     // --date  (volitelné)
        "name": "Letní"                         // --name  (volitelné)
      },
      "manual_reference": "manual_reference/101.ods",
      "known_differences": [
        {
          "sheet": "c1m",                       // c1m, k1z, c2x, ...
          "type": "value_diff",                 // nebo only_in_script / only_in_manual
          "key": 51,                            // bib (číslo) — startovní číslo
          "field": "time1",                     // jen pro value_diff: rgc / time1 / pen1 / time2 / pen2
          "note": "..."                         // proč to tolerujeme
        }
      ]
    }
  ]
}
```

## Co dělat při změně skriptu

1. Spusť `python tests/run_tests.py`.
2. Pokud projde — máš jistotu, že chování zůstalo stejné jako dřív.
3. Pokud najde regresi:
   - **Nečekaná regrese** → oprav skript.
   - **Zamýšlená změna chování** → upravíš `known_differences` v daném
     `config.json` (přidat / odebrat / popsat).
4. Commit.

## Co dělat při přidání nové akce do testů

1. Vytvoř `tests/<rok.místo>/` se třemi soubory: `canoe123.xml`,
   `empty.ods`, `manual_reference/<jméno>.ods`, plus `config.json`.
2. Spusť `python tests/run_tests.py <rok.místo>`.
3. Projdi všechny diffy v `known_differences` a popiš, proč je tolerujeme
   (typicky lidská chyba v `manual_reference`, kosmetika nebo nepodporovaný
   formát XML).
4. Commit.

## Známé limitace porovnání

- Porovnává **jen sémantiku** (bib → rgc, time, pen). Nepřihlíží k
  pořadí řádků, formátování, stylům.
- Pro startovku porovnává `rgc`. Pro výsledky `time1/pen1/time2/pen2`.
- Nepřihlíží k tomu, co je v `cizi` sheetu, k `param`, ani k jiným
  sloupcům (`oddil`, `vk`, …) — ty se v Eskymu počítají vzorci z `reg`.
