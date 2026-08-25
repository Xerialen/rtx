# K2 — baslinje mot KANONEN (genererad 2026-08-15 00:41:03 CEST)

Kanon: reference/ra-room/README.md @ 91a6e34. K1 täcker ENBART
kanonens in/ut-rutter — inte gamla-måls-ned/kant-sviterna (grok krav 8).
Arm A = kod **+ plantering** (inte kod-vs-kod); main-IN väntas via
västspiralen; teleportstart ger lägre gränsfart än Xerials löppassager
— bot-mot-facit är konservativt (plan v2 §9.8).

| Rutt | Arm A (senaste + plant) | Arm B (main) | Xerial-facit |
|---|---|---|---|
| UT → ring | **2.05** (12/12) bästa 1.95 IQR 2.01–2.07 · 24 fall | **5.55** (12/12) bästa 4.33 IQR 5.09–8.18 · 24 fall | 1.48 |
| UT → tunnel | **2.36** (12/12) bästa 1.51 IQR 1.89–2.73 · 24 fall | **2.18** (12/12) bästa 1.43 IQR 1.99–2.25 · 24 fall | 1.87 |
| UT → väst/sng | **2.86** (12/12) bästa 2.84 IQR 2.86–2.87 · 24 fall | **2.83** (12/12) bästa 2.09 IQR 2.10–2.85 · 24 fall | 2.21 |
| IN ring → topp | **6.38** (10/10) bästa 5.34 IQR 6.38–6.40 | 0/10 ok · 10 timeout, 2 fall | 4.94 |
| IN tunnel → topp | **7.68** (10/10) bästa 7.35 IQR 7.47–7.79 | **9.03** (10/10) bästa 8.74 IQR 8.91–9.67 | 6.87 |
| IN väst → topp | **8.74** (10/10) bästa 8.08 IQR 8.61–9.01 | **9.50** (10/10) bästa 9.07 IQR 9.14–9.84 · 1 fall | 8.18 |

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
- arm A: 1cc87180615f (ren) · so sha256 9eabf0020440… · start 2026-08-15 00:25:05 CEST · taskset srv/harn 2/3
  manifest sha256: f82c83fde75921a6911ee4a619cde7b50b1db58045caca9ce87c8c7ed1898bc9
- arm B: byteidentisk kopia av 27550:s (main) qwprogs.so; git-hash okänd för deployen, identitet = sha256 · so sha256 27b493b6f5e1… · start 2026-08-15 00:25:05 CEST · taskset srv/harn 4/5
  manifest sha256: 82746a86167dcf889f620651f612a6d11ec4f15637acbacaa1d78014f66dfb50
- klippmodul sha256 f19ffd18f75a… · harness f3b4e891b0c2… · N {"ut_ring": 12, "ut_tunnel": 12, "ut_vast": 12, "in_ring": 10, "in_tunnel": 10, "in_vast": 10}
- tic-vakt: arm A: maxdrift 0.05% · poll 48–49 Hz · arm B: maxdrift 0.03% · poll 48–49 Hz

## Brusbudget (qa-revision-2 punkt 3 — dom före utredning)

Per rutt: median-delta mot K2 INOM budgeten = brus (kräv upprepad körning
före regressions-/förbättringsdom); utanför = utred.
- UT ring: ±0,3 s · UT tunnel: ±0,5 s (bimodal droppunkt — redovisa
  klusterandelar tidig/sen dropp, se deepseek-uttunnel-ruttval) ·
  UT väst: ±0,15 s (extremt tajt fördelning)
- IN ring: ±0,3 s · IN tunnel: ±0,4 s · IN väst: ±0,5 s
- Fall/stall har INGEN brusbudget: varje fall på IN-sidan mot K2:s 0
  utreds (falla/fastna-kriteriet).
