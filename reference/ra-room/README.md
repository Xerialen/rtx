# RA-rummet — KANONISKT FACIT (Xerial-godkänt 2026-08-14 ~20:15 CEST)

Ägarens definition, visad live på riggen 2026-08-14 (telemetri:
`xerial-granser-20260814.jsonl.gz`, Pmove ent 2, stillastående per punkt)
och GODKÄND av Xerial efter fyrfaldig agentgranskning
(WORK_LOGS/gransfacit-review-{terra,grok,kimi,deepseek}.md).

## Rummets gränser (ägarvisade punkter)

| Punkt | Position | Betydelse |
|---|---|---|
| ra-topp / ra-spawn | [250, −703, 328] | målet uppe (≈ RA-itemet) |
| ring-gräns | [479, −421, 56] | rummet slutar, området "ring" börjar |
| tunnel-gräns | [30, −479, −16] | rummet slutar, ra-tunneln börjar |
| väst/sng-gräns | [−373, −709, −16] | rummet slutar, "sng spawns" börjar |

**Målet:** boten ska navigera RA-rummet optimalt — **in, upp till ra-topp,
och ut**. Inga mätmål utanför gränserna.

## Klippkriterier (godkända arbetsdefinitioner)

- topp: `z ≥ 320 && dist_xy([250,−703]) < 70` (skiva kring itemet)
- ring: `40 ≤ z ≤ 90 && x > 450 && y ≥ −421` (dörrplan)
- tunnel: `z < 20 && dist_xy([30,−479]) < 48` (öppningsradie)
- väst: `z < 20 && x ≤ −373 && |y+709| < 80` (dörrplan i korridoren)
- UT = sista topp-tick → första gränspassage; IN = sista gränspassage →
  första topp-tick; 25 s-tak; en emission per toppbesök.
- Verktyg: `facit_reclip.py` (avdubbletterade källor); klipp:
  `facit_reclip.json`.

## FACIT — Xerials egna tider (unika körningar, granskade)

| Riktning | Bästa | Fördelning | n |
|---|---|---|---|
| UT → ring | **1,48** | 1,5 ×3 · 1,6 · 1,9 · 2,0 · 2,5 | 8 |
| UT → tunnel | **1,87** | 1,9 · 2,1 · 2,7 (+outlier) | 4 |
| UT → väst/sng | **2,21** | 2,2 · 2,9 (+outlier) | 3 |
| IN ring → topp | **4,94** | 4,9–5,6 ×6 | 9 |
| IN tunnel → topp | **6,87** | 6,9 · 7,6 · 11,0 | 3 |
| IN väst → topp | **8,18** | 8,2 · 8,5 (teleportstarter exkl.) | 2 |

## ERSÄTTER (historik, mät ALDRIG mot dessa igen)

Gamla facit 3,5/2,4/2,2/3,4 mätte till punkter INNE i grannområdena
([288,−160] i ring; [−616,−251] i sng) och "Klätterkedjan ~4s" var ett
påhittat begrepp (Fabians namn — utgår). Historiska mätserier
(dashboard t.o.m. session 18) är mot de gamla målen och jämförs inte
med nya facit.

## Källinspelningar

- `xerial-ra-20260810.jsonl` (endast i ~/lab, 21 MB — stora sessionen)
- `xerial-ra2-20260811.jsonl.gz` — klättringen (IN ring-underlag)
- `xerial-ra-down-20260811.jsonl.gz` — nedhoppen
- `xerial-ra-ring-20260811.jsonl.gz` — ringsidan
- `xerial-granser-20260814.jsonl.gz` — gränsvisningen
- OBS: lab-kopior av 11/8-filerna är byteidentiska dubbletter — läs EN
  källa (terra-granskningens läxa).

## Nästa steg (låst ordning)

1. Harnessets mål/starter (`ra_down_all.py`, `ra_up_all.py`,
   `ra_edge_stress.py`-mål vid behov) klipps om till gränskriterierna.
2. Boten mäts mot in/ut/upp-definitionen under låst manifest
   (auditsyntesens åtgärd 1–3).
3. Main ommäts under samma protokoll (kräver Xerials ok).

Botbaslinje mot GAMLA målen (för kontinuitet): se dashboarden
t.o.m. session 18 samt WORK_LOGS/2026-08-14-receptbeslut.md.
