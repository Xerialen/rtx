# FACIT — hopptraning ring2quad (agarens tre hopp)

Skrivet och bokfort FORE forsta domda varvet. Ingen observation far bli sin
egen forvantan.

## 1. Grund (identitet fore matning)

| vad | varde |
|---|---|
| karta | dm3 |
| graf, nivå-1 (graph_stamp) | `17645347086516095554` (FNV-1a-64 av `dm3` ++ LE32(5981) ++ LE32(48217) ++ LE32(0)) |
| graf, STATUS-antal | 5981 celler · 48217 lankar · 0 rj-lankar |
| grafdump (T=1-inventarium) | `~/hopptraning/graf/dm3-nymain-live-graph.json`, sha256 `7b6cfea25669e0b555879c8ffa8dc260b7253e02c6d1dd14eeb26305164f6f5c` (48202 T=1-lankar; 15 prunade) |
| binar (botens klient) | `qwprogs.so` sha256 `6b64efc7f5f127a94fb903e889aee2c1eeb72496e06f464e5b51c04343b2600c` |
| binarens ursprung | rent extrakt av **nya main** `4f0b910613c27480ea36c7755c011ab9feba52c0`, byggt i worktree `~/rtx-ring2quad` pa branch `ring2quad` (arbetsträdet rent vid bygget) |
| serverbinar | `mvdsv` sha256 `858465007c7bea52c5c790cdfdd07c0d65cce17b48110b327595bb8c2e051f15` |
| rigg / unit | `fasttrack-server` (ztricks-riggen): spel **27530**, kontroll **27980** |
| rort ej | `fasttrack-ra` (27540/27990), `rtx-test-match`, main 27550/27991, `~/projects/quakeworld/rtx` (endast last) |
| cvars | `rtx_telemetry 1` · `rtx_bot_debug 1` · `rtx_nav_patch 1` · `rtx_bot_count 4` · `rtx_bot_skill 7` |

Grafen raknas om och jamfors mot dessa varden vid varje varv. Avvikelse =
STOPP, aldrig "ungefar".

## 2. Hoppen (agarens ord och agarens exakta punkter)

| hopp | fran | till | agarens referensdemo |
|---|---|---|---|
| 1 | ringkanten dar RA-rummet slutar: **syd [478,-515,56]**, **nord [193,-45,56]** | quad **[946,334,56]** | ring2quad2 (6,34 s) / ring2quad3 (5,69 s) |
| 2 | RA-spawnen **rarox [-632,-680,-16]** | teleporten ut **[224,-320,75]** | ring2quad1, t = 0–5,55 s |
| 3 | ringspawnen **[224,-320,75]** | quad **[946,334,56]** | ring2quad1:s senare del |

## 3. Utfallsklasser (slutna, en klass per forsok)

* **lyckad** — boten star pa marken inom 56 u (hopp 2: 64 u) i xy fran malet
  och inom 12 u (hopp 2: 40 u) i z, utan att ha fallit och utan stall.
* **fall** — banan gar under `fall_z` (hopp 1 och 3: **z < 48**; hopp 2:
  **z < -260**), eller boten dor under forsoket. **Ingen avsedd drop-geometri
  ar forregistrerad i nagot av de tre hoppen** — agarens egna demon ligger pa
  min_z 56,0 rakt igenom i ring2quad2/3. Varje dyk under gransen ar darfor ett
  fall, aldrig ett avsett drop.
* **stall** — motorns egen `GotoStall` for boten under forsoket.
* **timeout** — ingen ankomst inom budgeten (hopp 1: 12 s, hopp 2: 15 s,
  hopp 3: 14 s). Budgeten ar ~2x agarens tid och ar bara en avbrytsgrans.
* **fel_mal** — boten stannar utan stall men inte vid malet.
* **start_blockerad** — en annan bot stod pa startpunkten; raknas inte som
  forsok, redovisas separat.

## 4. Varv och godkannande

* Ett **varv** = **10 forsok** pa ETT hopp. Hopp 1 alternerar syd/nord
  (5 + 5) sa bada agarens ansatser provas i samma varv.
* **Godkant = 10 av 10 lyckade.** Tider ar sekundara och redovisas som
  matvarde, aldrig som krav (agarens falla/fastna-kriterium).
* Delvis kredit finns inte. 9/10 ar inte godkant.
* Varje forsok spelas in med egen 20 Hz-tape; misslyckanden obduceras i
  navmesh-obduktionspipelinen fore nasta varv.

## 5. Andringar mellan varv

* En andring at gangen, minsta verksamma forst (ruttval fore lankkostnad,
  lankkostnad fore lankoperation).
* Varje andring bokfors i rapporten med **vad, varfor, och vilket
  obduktionsfynd den svarar mot**, och committas pa branch `ring2quad`.
* Lankoperationer ar **kombinationer**: in- och utlankar for varje rord cell
  granskas ihop (`recept_lint`), aldrig en ensam remove.
* Ingen andring gar till nagon produktionsrigg. Riggen lamnas bokford.

## 6. Stoppregel

Tre varv i rad utan forbattring pa samma hopp = **BLOCKED** till Fable med
obduktionsfynden. Ingen fortsatt gissning.

## 7. Avvikelse mot FACIT-MALL.md (deklarerad)

`PLANS/FACIT-MALL.md` och `tools/gates/facit_lint.py` ar skrivna for
A/B-armar i K-serien: de kraver cykeldefinition (sex terminala utfall),
trunkeringsparitet, referensarm och main-jamforelse. **Detta uppdrag har
ingen A/B-arm** — det ar en traningsloop mot ett fast facit pa en dedikerad
rigg. Klausulerna 2–5 ar darfor inte tillampliga och detta facit gar inte
igenom `facit_lint`. Jag har inte rort granden. Facitet ar i stallet
bokfort fore forsta domda varvet genom commit pa branch `ring2quad`
(commit-sha = tidsstampeln). **Eskalerat till Fable** (facit-granden ags av
Fable) for besked om annan onskad forseglingsvag.
