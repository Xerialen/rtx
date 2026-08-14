# K1B — baslinje mot KANONEN (genererad 2026-08-14 22:41:49 CEST)

Kanon: reference/ra-room/README.md @ 91a6e34. K1 täcker ENBART
kanonens in/ut-rutter — inte gamla-måls-ned/kant-sviterna (grok krav 8).
Arm A = kod **+ plantering** (inte kod-vs-kod); main-IN väntas via
västspiralen; teleportstart ger lägre gränsfart än Xerials löppassager
— bot-mot-facit är konservativt (plan v2 §9.8).

| Rutt | Arm A (senaste + plant) | Arm B (main) | Xerial-facit |
|---|---|---|---|
| UT → ring | **2.00** (12/12) bästa 1.93 IQR 1.95–2.07 · 24 fall | **6.04** (12/12) bästa 4.35 IQR 5.38–8.37 · 24 fall | 1.48 |
| UT → tunnel | **1.98** (12/12) bästa 1.48 IQR 1.52–2.65 · 24 fall | **1.62** (12/12) bästa 1.34 IQR 1.54–2.19 · 24 fall | 1.87 |
| UT → väst/sng | **2.86** (12/12) bästa 2.84 IQR 2.86–2.86 · 24 fall | **2.83** (12/12) bästa 2.11 IQR 2.13–2.84 · 24 fall | 2.21 |
| IN ring → topp | **6.75** (10/10) bästa 6.36 IQR 6.72–6.77 · 2 fall | 0/10 ok · 10 timeout, 2 fall | 4.94 |
| IN tunnel → topp | **8.04** (10/10) bästa 7.70 IQR 7.79–8.23 | **9.07** (10/10) bästa 8.88 IQR 9.00–9.50 | 6.87 |
| IN väst → topp | **9.62** (10/10) bästa 9.02 IQR 9.30–10.29 · 4 fall | **9.09** (10/10) bästa 8.97 IQR 9.08–9.42 | 8.18 |

Median över lyckade klipp (sann median); IQR = linjär interpolation
P25/P75; timeouts i nämnaren (grok krav 9). OBS: IN väst-facit 8,18 är
PRELIMINÄRT (n=2, ej målmedvetna körningar — kimi villkor 2); be Xerial
om riktade väst→topp-inspelningar innan det låses.
fall_def=peak_drop_150 (Δz>150 från löpande peak, inget golv) · tic-gräns 1.0% per försök.
OBS: på UT-rutterna räknar falldetektorn även ruttens AVSEDDA nedhopp
(topp→golv är >150u) — UT-fall är deskriptiva, inte felsignal;
på IN-rutterna är fall en verklig felsignal.

## Obligatoriska läsflaggor (granskarnas villkor, K1-pilotreviewerna)

1. **Jämför median mot facit, aldrig bästa.** Bästa-under-facit på UT
   tunnel är läpp-geometri (klippet startar på 70u-diskens norra kant
   med ansatsfart över skivan) och uppstår i BÅDA armarna — inte bevis
   att någon arm slår Xerial (grok 3a; verifierad i
   2026-08-14-utunnel-149-verifiering.md). Gäller även mains UT väst-
   bästa under facit — UT-jämförelsen är kriteriebunden, inte
   färdighetsbunden (kimi villkor 5).
2. **IN ring för main är strukturell, inte en prestandaförlust:** main
   når RA-plattan men stannar utanför kanonens 70u-toppdisk (hoppar på
   västkanten [152,−704]) — ingen upp-länk till disken (grok 3c,
   deepseek c). 0/N redovisas; jämför aldrig mot facit 4,94.
3. **A:s IN väst citeras alltid med full nämnare + timeout + fall** —
   fallen är riktiga klätterras från västra övre hyllan (~[60–136,
   −660..−690]), inte ruttens nedhopp (fallklassningen i
   verifieringsdokumentet; terra §2, grok 3b).
4. **K-serien täcker ENBART kanonens in/ut** — ned-/kant-sviterna har
   ingen K-baslinje. Ny so eller ny plant-JSON ⇒ ny K-serie (K2),
   aldrig 'K + delta' (grok villkor 6–7).

## Manifest (fulla hashar och statebevis i manifest.json + fas_state_*.json)
- arm A: 3524f6aa9f9d (ren) · so sha256 9eabf0020440… · start 2026-08-14 22:25:03 CEST · taskset srv/harn 2/3
  manifest sha256: 26a247619f41fb88fd532ab67e2bcf207df3f37373f534fd6790967198a51098
- arm B: byteidentisk kopia av 27550:s (main) qwprogs.so; git-hash okänd för deployen, identitet = sha256 · so sha256 27b493b6f5e1… · start 2026-08-14 22:25:03 CEST · taskset srv/harn 4/5
  manifest sha256: b0848ef6a53eb48fa0b9e9ba47399f8dd787d945faaf34626f88d3eab7938405
- klippmodul sha256 f19ffd18f75a… · harness f3b4e891b0c2… · N {"ut_ring": 12, "ut_tunnel": 12, "ut_vast": 12, "in_ring": 10, "in_tunnel": 10, "in_vast": 10}
- tic-vakt: arm A: maxdrift 0.01% · poll 48–49 Hz · arm B: maxdrift 0.05% · poll 48–49 Hz
