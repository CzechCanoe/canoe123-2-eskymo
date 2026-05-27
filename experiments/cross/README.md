# Cross sprint ČB 2026 — uživatelské pole

Tato složka obsahuje XML export z Canoe123 pro **kajak-kros / cross
sprint** v ČB a místo pro experimentování s výstupem.

## Co tu je

- `2026_cpz_cb_XC.xml` — Canoe123 export z X-sprintu v ČB
- `49_empty.ods` — **pozor: to je slalomová šablona, ne cross!**
  Pro cross potřebuješ jinou.

## Třídy v XML

- X1M-ZS, X1M-ZM (žáci starší / mladší)
- X1Z-ZS, X1Z-ZM (žákyně starší / mladší)

RaceId formát: `X1M-ZM_XT_23` (kvalifikace den 23), `X1M-ZM_XS_24`
(semifinále), `X1M-ZM_XF_24` (finále), `X1M-ZM_XER_24` (výsledky).

## Jak vyrobit výstup

1. **Připrav cross Eskymo šablonu** s vhodnými sheety. Vezmi
   `tests/2026.troja.cross/empty.ods` (kajak-kros Troja šablona)
   a přejmenuj sheety:
   - `MX1-Q` → `X1M-ZS-Q` (nebo `X1M-ZM-Q` podle kategorie)
   - `MX1-F` → `X1M-ZS-F`
   - … pro všechny 4 žákovské kategorie

   Nebo zachovej názvy `MX1-Q`/`WX1-Q`/`MX1-F`/`WX1-F` a v XML předem
   přejmenuj ClassId. (Trojská šablona má jen MX1/WX1/MX1J/WX1J sheety,
   pro 4 žákovské kategorie potřebuješ vytvořit duplikáty.)

2. **Spusť cross skript**:
   ```bash
   python ../../cross.py 2026_cpz_cb_XC.xml <tvoje_sablona.ods> output.ods \
       --day 23 --day-final 24 --date 23.05.26
   ```

   `--day` = den XT (kvalifikace, sobota).
   `--day-final` = den XS/XF/XER (pavouk, neděle), pokud jiný.

3. Otevři výstup v LibreOffice → Ctrl+Shift+F9 → zkontroluj.

## Pozn.

Pokud šablona pro některou kategorii sheety nemá, skript ji
přeskočí a zaloguje to. Nevadí — později můžeš sheety doplnit a spustit
skript znovu.
