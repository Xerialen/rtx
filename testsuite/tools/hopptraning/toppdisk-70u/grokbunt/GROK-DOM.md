# GROK-DOM — 70u-toppdiskmätningen

Oberoende omräkning 2026-08-20 ur `~/hopptraning/70u-grokbunt/` (kopierad, hashar mot `PROVENIENS.txt` 13/13 träff). `kanten.json` inte använd som underlag. Live-skript (`fran1216.py`, `rutt1216.py`) inte körda: servern släcktes 20:35:26 enligt bunten. `disk70.py` kördes mot uppackad `data/mainref-live-graph.json.gz`. Siffror nedan är omräknade ur `data/`, inte kopierade ur README.

## HELHET: FALL

(1) och (2) håller. (3):s 8/8 + tom plan vid läppen håller. Höjdtabellen 22–43 u och slutsatsen «klättringens sista landning» bärs **inte** av tillåten rundata — och motbevisas av (2). Leveransen får inte nå ägaren med den kausala slutsatsen.

---

## (1) Länk 8992 i levande cc5fa8e-mesh — 19 inkommande walk, noll luftburna

**DOM: GODKANN**

**Omräkning** (`disk70.py` + egen räkning, disken `z ≥ 320 ∧ dxy([250,−703]) < 70`):

| | |
|---|---|
| dump | 5977 celler, 48192 länkar i adjacens |
| provenance i dumpen | live `:27970`, status 5977/48207 — delta **15** (prunade, utanför adjacens) |
| celler i disken | **21** |
| närmast centrum | cell **1372** [256, −704, 328], **6,1 u** |
| cell 1216 | [160, −704, 328], **90,0 u** — utanför disken |
| inkommande utifrån | **19**, kinds `{'walk': 19}`, luftburna **0** |
| länk **8992** | **FINNS** `1216 → 1265` `walk` [160,−704,328] → [194,−733,328]; mål **63,5 u** från centrum, i disken |

Från 1216 in i disken finns dessutom 8993 (→1267) och 8994 (→1269), båda walk.

**Negativkontroll.** Länk-id 999999 saknas. `R=0` ger 0 diskceller. Utgående *från* disken har jump 42 / drop 29 / speedjump 17 / walk 19 — dumpen *kan* bära luftburna sorter; noll inkommande är alltså inte ett blint filter.

**Villkor.** Binärhash `27b493b6…` / «kodidentisk cc5fa8e↔4db5b19» är provenienspåstående; qwprogs.so har inte hashatts om här (server släckt). Dumpen själv säger live `:27970`. K2-kvittot visar bara +1 länk (48211→48212); README:s «+5» är inte rekonstruerad ur kvittot. Disklänkarna 8992/8993 sitter i default-dumpen (48207) och beror inte av K2.

---

## (2) Från cell 1216: planerar och går in i disken 0,61 s, 3/3 (`fran1216.json`)

**DOM: GODKANN** (Goto-armen; det är den påstådda mätningen)

**Omräkning.** 6 rader: 3 `goto` + 3 `fri`.

| läge | inne | t | första plan |
|---|---|---|---|
| Goto → [256,−704,328] | **3/3** | **0,61 / 0,61 / 0,61 s** | identisk: `8993 walk 1216→1267 · 9325 walk 1267→1319 · 9732 walk 1319→1372` |
| fri (eget mål) | **0/3** | — | `8988 walk 1216→1191` … bort från disken |

Slutcell Goto = 1267, dxy 58,0 u, i disken. Första telemetri *redan* z=352/354/356 (dz 24–28 över cell 1216:s golv z=328) — och de går in ändå.

**Negativkontroll i samma fil.** `fri` 0/3: lämnar 1216 åt andra hållet. Routern *kan* planera ut; Goto är det som tar in. Påståendet «3/3 på 0,61 s» avser Goto, inte fri — README säger det, och datan stämmer.

**Villkor.** n=3, men noll spridning. `cell_vid` är naiv närmaste-origin, inte motorns `nearest()`; 1267:s origin ligger i disken så klassningen «inne» är tautologisk mot samma diskdefinition som (1). Live inte omkörd.

---

## (3) IN-ring: disklänk i plan 8/8, boten stannar med tom plan, 22–43 u över golv — slutsats «inte mesh, inte router, utan klättringens sista landning»

**DOM: FALL**

Delutfall:

| delpåstående | dom | bäring |
|---|---|---|
| disklänk i planen 8/8 | **GODKANN** | 8/8 har 8992 vid t≈3,44–3,46 s och 8993 vid t≈4,60–5,17 s |
| 0/8 i disken | **GODKANN** | `i_disken=false` alla 8; slutcell 1216 (7 st) eller 1218 (1 st) |
| tom plan *medan boten står på västkanten* | **GODKANN** | `vastkantsprov` 8/8, 4 avläsningar vardera, `legs=[]` |
| 22–43 u över cellgolvet som stopptillstånd | **FALL** | se nedan |
| «inte mesh, inte router» | följer av (1)+(2) | inte av (3) ensamt |
| «utan klättringens sista landning» | **FALL** | kausal överräckning |

**Omräkning `rutt1216.json` (n=8), slut_pos mot cellorigin:**

| i | slut_pos | cell | dz |
|---|---|---|---|
| 1 | [149, −700, **328**] | 1216 | **0** |
| 2 | [153, −701, **328**] | 1216 | **0** |
| 3 | [157, −691, **328**] | 1218 | **0** |
| 4 | [153, −700, **328**] | 1216 | **0** |
| 5 | [153, −700, **328**] | 1216 | **0** |
| 6 | [153, −700, **328**] | 1216 | **0** |
| 7 | [152, −701, **328**] | 1216 | **0** |
| 8 | [152, −699, **356**] | 1216 | **28** |

**7/8 stannar på golvet (dz=0).** En enda rad har dz=28. README:s tabellrader [153,−700,350] dz 22, [155,−703,357] dz 29, [157,−691,371] dz 43 finns **inte** i `rutt1216.json`. Isoleringens start [160,−704,330] dz 2 finns inte heller som telemetri: `fran1216` första stick är z=352–356.

`sista_plan` är sista *icke-tomma* planen (skriptet skriver över bara när `legs` är sann). Den innehåller 8993 i 8/8 — det är klättringsplanen *före* läppen, inte tillståndet på läppen. På läppen är planen tom. Det stämmer. Det visar «planeras under vägen, är borta när boten står där», inte *varför*.

**Varför slutsatsen faller.** (2) visar att samma cell 1216, med plan, tar in boten på 0,61 s *från z=352–356* (dz 24–28). Höjd över golvet räcker alltså inte som spärr. IN-ring-felet som datan bär är **tom plan vid läppen**, inte landningshöjd. README §6 medger att varför planen töms är oprövat; §5:s «sista landning» är den gissning §6 just avstår från.

**Villkor / öppet.** `cell_vid` ≠ motorns `Cell`. n=8, 8/8 utan spridning på plan/tom/ute. Live inte omkörd. `kanten.json` orörd som underlag (README: kasserat, felaktig domrad).

---

## EVIDENS (kommandon denna session)

- `sha256sum` av hela bunten mot `PROVENIENS.txt`: 13/13.
- `gzip -dc data/mainref-live-graph.json.gz` → 5977 / 48192; provenance `:27970` 48207.
- `python3 skript/disk70.py <dump>`: 21 celler, 19 walk in, 8992 finns, mål 63,5 u inne.
- Egen räkning samma dump: identiska 19/0; utgående luftburna finns; 8992/8993/8994 från 1216.
- Egen räkning `fran1216.json`: Goto 3/3 t=0,61; fri 0/3.
- Egen räkning `rutt1216.json`: 8/8 disklänk, 0/8 inne, 8/8 tom `vastkantsprov`, dz={0×7, 28×1}.

## OPEN QUESTIONS (inte gissade)

1. Varför töms den levande planen mellan t≈5 s (8993 synlig) och första västkantsstick t≈6,6–6,9 s?
2. Motorns `Cell` / `nearest()` på stoppunkterna — inte mätt i tillåten fil.
3. Binärens sha omkörd mot `qwprogs.so` på den levande hosten.

Ägar-säker extrakt ur denna bunt: (1) GODKANN, (2) GODKANN, IN-ring 8/8 plan + tom läpp + 0/8 inne GODKANN. Inte «sista landningen».
