# Cross sprint — zatím nepodporováno

Tato složka obsahuje XML export z Canoe123 pro **cross sprint**
disciplínu (`X1M-ZM_XT_23` style RaceId), kterou hlavní skript
`canoe2eskymo.py` zatím **nezpracovává**.

Cross sprint má jiné race ID schéma (kategorie vestavěna v ID:
`X1M-ZM`, `X1W-DS`, …) a pravděpodobně i jiný formát výsledků
(K.O. systém, postupy do dalších kol). Vyžaduje samostatný design.

## Co tu je

- `2026_cpz_cb_XC.xml` — Canoe123 export z X-sprintu CB
- `49_empty.ods` — kopie standardní Eskymo šablony (nemá cross sheety)

## Pokud se sem chceš pustit

1. Začni průzkumem XML — kolik kategorií, jaké RaceId tvary
2. Zjisti, jakou Eskymo šablonu / sheety potřebuje cross sprint
3. Pravděpodobně bude potřeba samostatný entry-point nebo režim ve
   skriptu (`canoe2eskymo.py --discipline xsprint`)
