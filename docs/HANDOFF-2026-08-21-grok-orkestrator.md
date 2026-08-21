# HANDOFF 23 — ÖVERLÄMNING TILL GROK SOM ORKESTRATOR
**Skriven av Fable (Claude) 2026-08-21 kväll, sista handling före tokenslut.**
Ersätter handoff22 som kanonisk ingång. Läs denna FÖRST, hela, före första åtgärd.

Allt nedan är verifierat av mig i denna session eller citerat med filväg. Där jag
är osäker står det utskrivet. Inget är påhittat; kompletteringar är märkta
"KOMPLETTERING".

---

## 0. UPPDRAGET DU TAR ÖVER

**Närtidsmålet, ägarens ord:** få in de framsteg vi gjort i **main** och ha dem
**säkrade** — både RA-rummet och ring2quad.

**Ägarens acceptanskriterium (2026-08-21 kväll, ordagrant):**
> "Om vi är 95% säkra på att vi har identifierat alla variabler och
> konfigurationer och element etc som gav oss 99% resultat är vi nöjda med det"

Det styr allt: frågan är inte att jaga fler decimaler, utan att kunna **räkna upp
ingredienserna** som ger resultatet och visa att de är just de som mätts.
Min bedömning vid överlämning: vi ligger på **85–90 %**, och gapet stängs av
S5-mätningen (se §6).

**Två mål, ur `docs/GOALS.md` i repot (utkast, ej ägarattesterat):**
- **Mål A:** RA-rummet ≥99 % lyckade försök per ben på fork main, mätt mot
  kanonen `reference/ra-room/README.md`. Kriteriet är **inte falla, inte
  fastna** — tider sekundära.
- **Mål B:** ring2quad-kedjan **12/12 hela kedjor** på fork main (sluttest #41).

---

## 1. ROLLKONFLIKT DU MÅSTE LÖSA FÖRST — läs innan något annat

Du (grok, herdr-säte `wN:p9`) är i dag **granskarsätet**: oberoende omräkning av
Fables buntar innan de når ägaren. Din egen rollfil
`AGENTS.grok-validator.md` (inkluderad via `CLAUDE.md`) förbjuder dig att läsa
`WORK_LOGS/` och att koordinera med andra modeller.

**Som orkestrator måste du göra precis tvärtom.** Det går inte att vara båda i
samma ärende utan att den oberoende omräkningen dör — och den oberoende
omräkningen är den enda kontroll som fångat felaktiga tal i det här projektet
(den fällde dk1-bunten utgåva 1, och den korrigerade planen).

**Rekommendation (mitt förslag, ägarens beslut):**
- Du tar orkestratorrollen enligt `.claude/agents/fable-orkestratorn.md` —
  läs den, den är skriven för att kunna bäras av vem som helst.
- **Valideringssätet måste bemannas av någon annan instans** — en andra
  grok-terminal utan orkestratorkontext, eller Sol (p4), eller en Claude-subagent
  om krediter finns. Utan det: skriv ut i varje ägarrapport att talen är
  **oberoende ovaliderade**, och lova aldrig annat.
- Blandar du rollerna ändå: säg det öppet i rapporten. Solodomar är svagare och
  ska märkas så (samma regel som navmeshdoktorns regel 7).

---

## 2. ORGANISATIONEN — vem gör vad, och var rollerna bor

Sju rollfiler ligger **kanoniskt i fork-repot** under `.claude/agents/`
(committade 2026-08-21, commit `9e6f342` + `bd838cc`) och i arbetskatalogen
`/home/xerial/dev/buzz-4on4/.claude/agents/`:

| Roll | Fil | Modell hittills | Gör | Gör ALDRIG |
|---|---|---|---|---|
| **Fable-orkestratorn** | `fable-orkestratorn.md` | Fable 5 → **du** | Ger order, tar rapporter, bereder ägarbeslut | Dömer i sak, mäter, skriver produktionskod |
| **Kodaren** | `kodaren.md` | Opus 5 | Kod, facit, förseglade addenda, mutationsprov | Dömer eget arbete, rör riggens systemd |
| **QA-domaren** | `qa-domaren.md` | Opus 5 | Dömer kod/facit/addenda/körningar, räknar om allt själv | Skriver produktionskod, rör rigg |
| **Hopparen** | `hopparen.md` | Fable 5 | Bygger armbinärer, reser riggar, kör drillar, skriver körkvitton | Dömer eget arbete, orkestrerar andra |
| **Navmeshdoktorn** | `navmeshdoktor.md` | Fable 5 | Navmeshdiagnos enligt `navmesh-doctor/`-paketet | Levererar till produktion utan ägarens GO |
| **Demobyggaren** | `demobyggaren.md` | Fable 5 | Demos, 3D-artefakter, dashboardinslag | Rör rigg, facit, domfiler |
| **Qwen-forensikern** | `qwen-forensikern.md` | Qwen3.8-27B lokalt (`wN:pB`) | Kör namngivna mätskript, loggforensik, tabeller med kopierade tal | Predikat, UI, facit, pins, merge |

**Hur rollerna startades av mig:** Claude Code-subagenter via Agent-verktyget,
med prompten inledd av *"Läs FÖRST `.claude/agents/<roll>.md` och arbeta under
den rollen"*. **Du har inte det verktyget.** Använd grok-CLI:ns egen
subagent-/task-mekanism på samma sätt: **första raden i varje order ska vara att
läsa rollfilen**. Rollfilen bär reglerna; ordern bär uppgiften. Det fungerade —
rollerna stoppade mig korrekt två gånger (se §7).

**Levande herdr-säten** (`herdr agent prompt wN:<pane> "<text>"`, socket
`~/.config/herdr/herdr.sock`):
- `wN:p1` — Fable (min panel; tom när jag är slut)
- `wN:p4` — **Sol** (GPT-5.6): kontrasignatär, variant B. Bokför själv i liggaren.
- `wN:p9` — **grok** (du)
- `wN:pB` — **qwen**, parkerad, ny roll enligt §2-tabellen
- Stängda 2026-08-21: `pA`, `pC`, `pF`, `pE` (blev subagenter), `p6` (grok1,
  konsoliderad — slut-handoff i `WORK_LOGS/2026-08-21-grok1-slut-handoff.md`),
  `p3` (deepseek, ute ur uppställningen på ägarbesked).

**Efter varje prompt till ett herdr-säte: verifiera inom ~10 s att status gick
till `working`** (`herdr agent get wN:pX`) — prompter kan fastna opostade.

---

## 3. LÄGET I SAK — vad som är bevisat, med evidens

### 3.1 Mållinjefixen (RA-rummets största enskilda gap) — DÖMD OCH KLAR
- **Fynd:** styrkanalens mållinjeprov (`control.rs`, fast korridor 96 u) utropade
  falsk ankomst 87–101 u från målet → bot stod still → 0/20 på kanonens 70 u-disk.
  Felet låg i **koden, inte i meshen** (K2 planterade fem korrekta länkar och gav
  ändå 0/20).
- **Fixen:** en beteenderad — krympande korridor — gren `mallinjefix` @
  **`1e37b4e`** (på fork).
- **Uppmätt (dömd körning 1, interfolierat, samma maskin, noll ersatta försök):**
  **referensarm 0/20 · fixarm 20/20** på kanondisken; topp-vid 20/20 båda.
  Kontroller nk1–nk6 PASS. Kvitto:
  `WORK_LOGS/2026-08-21-domd-korning-1-korkvitto.md`.
- **Oberoende validerat:** Grok räknade om två gånger (sha 26/26 resp. 71/71,
  omobduktion ur tick-band) — `WORK_LOGS/2026-08-21-grok-dom-dk1.md`.
- **Dom:** `WORK_LOGS/qa-dom-mallinjefix-design.md` (SLUTDOM fr.o.m. rad 989):
  **§9 PASS · nk7 OKLAR · §12 OFÖRÄNDRAT/OSÄKERT.**
  Etiketten är ett **regeltak**, inte ett mätresultat — 0/20→20/20 står.
- **Pappersked komplett:** facit `a8ba66a1…` + addendum 1 `3562b7fa…` (Sol rad
  151) + addendum 2 `b652d8ca…` (Sol rad 153) + **addendum 3 v2 `ab0cf7c7…`**
  (QA PASS, Sol rad 158). Addendum 3 **v1** (`3cca2e02…`) är ersatt men orörd.
- **nk7 (den enda oklara korridoren):** ägaren valde bort extramätningen (N1)
  efter att QA rättat riskinramningen — se §7.4. Etiketten förblir alltså
  OFÖRÄNDRAT/OSÄKERT. **Det blockerar ingenting.**

### 3.2 Receptautostarten (automatisk receptapplicering) — KOD KLAR, KÖRNING AVBRUTEN
- Gren `receptautostart` @ **`a86586ed`** (på fork). Kod-PASS, 15/15 mutationer,
  applicerarens `--verifiera-offline` lagad (kraschade förut med `KeyError`),
  sha-grindar stramade till full längd.
- **Pappersked komplett och kontrasignerad:** facit `964e80e7…` + addendum 1
  `5b55c045…` + **addendum 2 `003bebdb…`** (basnivå-2, Sol rad 157) + **addendum
  3 `497fdb5c…`** (additivregel, efter-hashar, F1/F2, Sol rad 160). QA: PASS på
  båda — `WORK_LOGS/qa-dom-receptautostart-design.md` (sista domen).
- **Basgrafen är trippelbekräftad:** riggmätning (Hopparen), offlineräkning
  (S3-spåret) och QA:s egen implementation ger alla
  **`58787ce0…`** för dm3 **5977/48207**. Efter-hasharna: `180315a3…` (48211),
  **`feeea6b4…`** (48212, kedjans slutläge).
- **DÖMD KÖRNING 2 ÄR AVBRUTEN MITT I** — se §4. **Inget körkvitto finns.**

### 3.3 Ring2quad — 12/12 bevisat, men inte på fork main
- 12/12 hela kedjor (delmoment 12/12 vardera) mättes på **lokala mains graf**
  (5981/48217). Logg: `WORK_LOGS/2026-08-19-hopptraning-ring2quad.md` (§24–§37,
  12/12-tabellen kring rad 1660–1690). Gren `ring2quad` på fork = **`04666b72`**
  (handoff22 angav `c588d3b`; det verifierade fork-värdet i dag är `04666b72` —
  **kontrollera vilken som är rätt innan du bygger**).
- **Receptet är omhärlett mot fork mains graf** (S3, klart i dag): gren
  **`vf5-fork-omharledning` @ `d886623`**, pushad till fork av mig i kväll så den
  inte går förlorad ur `/tmp`.
  - Rätt generation bevisad via **avfartskoordinaten `[454,7 · 153,3]`** —
    **nivå-2-hashen kan INTE skilja vF3/vF4/vF5 åt** (avfarten bor i
    sidotabellen). Använd aldrig hashen som generationsbevis.
  - Portningskvitto `fork-basbindningen`: rätt recept mot rätt graf matchar; sex
    negativkontroller vägrar korrekt (korsprov åt båda håll).
- **Utfallssiffrorna är INTE omhärledda** — 12/12 gäller vF5-basen med lokal
  mains binär. De får **inte** citeras som fork mains utfall. Det är #41:s jobb.

### 3.4 RA-rummet som helhet — vad "99 %" faktiskt vilar på
- Bästa per-ben-mätningen: **M1, 508/510 = 99,6 %**, 0 fall, 2 fastnad
  (`WORK_LOGS/m1-runda1-kvitto.md`, grok-omräknad). **MEN:** mätt på **lokal
  main** med **F1-flaggorna** (`rtx_bot_edge_narrow=1`, `rtx_bot_walkdiag=1`,
  `rtx_bot_walkplan=1`), **inte** på fork main och **utan** mållinjefixen.
- Senaste helrumsmätning i kedjad drift: T1h 18/8 — fork 399/450 (88,7 %),
  per rutt i `WORK_LOGS/2026-08-15-t1h-timtest.md` (IN tunnel 82,7 %, IN väst
  89,3 %, UT väst 80,0 %). **Den är från före mållinjefixen.**
- **Det finns alltså inget helrumsbevis per ben på fork main efter fixen.**
  Det är precis vad S5 ska producera, och det är gapet mot ägarens 95 %-krav.

---

## 4. DÖMD KÖRNING 2 — EXAKT LÄGE (viktigast just nu)

Hopparen-subagenten dog mitt i körningen när Claude-krediterna tog slut
(2026-08-21 ~19:05Z). **Jag har städat efter den** — verifierat av mig i kväll:

- Unit `ra-drill-dk2-receptautostart.service` var **kvar igång**; jag stoppade
  den. Portar 27580/27960/29580 **tysta**. KTX-paret 28502/28503 orört.
- Rigglåset var kvar taget; **jag släppte det** (tömde filen — se pitfall §7.6).
  Låsets innehåll arkiverat: `lanister:~/hopptraning/dk2/rig-lock-avbruten-20260821.txt`.
- **Ingen mätprocess körde** när jag städade (`pgrep` mot python-drivrutiner: inga).

**Vad som hann bli gjort (rådata finns kvar i `lanister:~/hopptraning/dk2/`):**
- `nkprov-resultat.json` — nk-mutationsbatteriet på unit-nivå, körningens
  oavkortade stdout, varje mutation med fällda tester + `aterstalld_byteidentisk`.
- `las10-av.jsonl` / `las10-pa.jsonl` — tio läsningar med recept AV resp. PÅ.
- `cellpar.json`, `cellpar-apparatfel1.json` (0444 — den senare namnger ett
  apparatfel, läs den).
- `utfall/in_ring/attempt_01..20.jsonl` + `utfall-kor.log` — 20 försök.
- `obd-dk2.json`, `obd-referensarm.json`, `obd-dk1arm3.json`,
  `obd-k2manuell.json` — fyra obduktioner.

**ODÖMT FYND SOM DU MÅSTE HANTERA RÄTT:** utfallsloggen slutar med
`in_ring: 0/20 ok, median None, fall 2`. **Det är förväntat och INTE ett
underkännande av receptautostarten** — grenen `receptautostart` är byggd på
`4db5b19` (fork main + recept), alltså **utan mållinjefixen**, och utan den är
0/20 på kanondisken precis referensläget från dk1. Receptautostartens facit
mäter **att receptet appliceras automatiskt och rullas tillbaka rätt**, inte
kanonutfallet. Låt QA-domaren avgöra; citera aldrig 0/20 som automatikens betyg.

**Vad som saknas:** körkvitto (`WORK_LOGS/2026-08-21-domd-korning-2-korkvitto.md`
finns **inte**), grokbunt (`~/hopptraning/dk2-grokbunt/` finns **inte**),
§2.5:s bekräftelseled (riggen ska själv ha läst **5977/48212** och **`feeea6b4…`**
— jag kan inte se i materialet att det bekräftats), L10/L11-kvittona.

**Beslut du ska fatta tidigt:** går materialet att kvittera i efterhand av en ny
Hopparen-instans (den läser rådatan och skriver kvittot med tydlig märkning att
körningen avbröts), eller ska körningen **göras om ren**? Min rekommendation:
**gör om den ren.** Den tar ~30–40 min riggtid, materialet är oklart avgränsat,
och ett kvitto skrivet av någon som inte var där är exakt den sortens svaga
handling projektet fällt förut. Rådatan sparas som referens, inte som bevis.

---

## 5. VAD SOM LIGGER I FORK-REPOT (github.com/Xerialen/rtx)

**Fork main HEAD vid överlämning: `58096bf`.** Allt nedan pushat i dag.

- `.claude/agents/*.md` — de sju rollerna (`9e6f342`, `bd838cc`)
- `reference/ra-room/` — **kanonen**, räddad in i repot (`a8e4f8d`). Den fanns
  tidigare i **ingen** commit i hela historiken.
- `docs/GOALS.md` (`e924d82`) — målen, märkt utkast
- `docs/BRANCHES.md` (`d93c8e7`) — grenregister
- `docs/AGENT-PREREQS.md` (`2cea442`) — de externa dokument rollfilerna kräver
- `docs/coldstart-review-2026-08-21.md` (`58096bf`) — kallstartsgranskningen
- Taggar `arkiv/91a6e34`, `arkiv/86f7f11` — två pinnade mätcommits räddade ur ett
  delat objektarkiv. **`c8a20fb` är permanent förlorad** (`git cat-file` →
  "Not a valid object name" i ~85 genomsökta repon).
- **45 GitHub-issues** (#1–#45): #1–#26 doktorsdokument-/mesh-/verktygsfynd,
  #27–#45 kallstartsfynden P1–P4 + #34 P0-status. Index:
  `WORK_LOGS/2026-08-21-gh-tickets-index.md`. (Issues var avstängda på forken;
  de slogs på i dag.)

**Levande grenar på fork:** `main 58096bf` · `mallinjefix 1e37b4e` ·
`receptautostart a86586ed` · `ring2quad 04666b72` · `vf5-fork-omharledning
d886623` · `recept-i-tradet 4db5b19` + 17 äldre (klassning saknas — se
`docs/BRANCHES.md`).

**KRITISKT ATT VETA:** `WORK_LOGS/`, `PLANS/`, `GOTCHAS.md`,
`GUIDES/VERKTYGSGRINDAR.md` och `navmesh-doctor/` ligger **INTE i repot** — bara
i `/home/xerial/dev/buzz-4on4/` på pinnacle. Hela domkedjan, alla facit och alla
kvitton bor där. Går den maskinen förlorad är bevisningen borta.
`docs/AGENT-PREREQS.md` listar dem. **KOMPLETTERING/mitt råd:** överväg tidigt
att spegla `WORK_LOGS/` till repot eller ett arkiv — kallstartsgranskningen
underkände repot delvis just på detta.

---

## 6. PLANEN FRAMÅT — där jag slutade

Planen ligger i `PLANS/2026-08-21-plan-ra99-ring2quad.md` (utkast 3, granskad av
en oberoende subagent **och** av grok — båda granskningarna inarbetade;
grok-granskningen: `/tmp/grok-plangranskning.md`, **kopiera in den i WORK_LOGS,
`/tmp` städas**).

Stegen, i ordning, med status vid överlämning:

| Steg | Vad | Status |
|---|---|---|
| S1-papper | R6-förhandsregistrering (nk7) | **KLAR** — addendum 3 v2, QA PASS, Sol rad 158 |
| S1-rigg | R6-mätningen n=60/arm | **UTGÅR** — ägaren valde N1-b (se §7.4) |
| S2-papper | Basgrafaddendum + p3/p4 + restlistan | **KLAR** — addendum 2+3, QA PASS, Sol rad 157+160 |
| S2-rigg | **Dömd körning 2** | **AVBRUTEN** — se §4. Gör om ren. |
| S3 | Omhärled vF5 mot fork mains graf | **KLAR** — gren `vf5-fork-omharledning` |
| S3.5 | **ÄGARBESLUT: mergekandidatens konfiguration** | **VÄNTAR PÅ ÄGAREN** |
| S4 | **#41** — ring2quad 12/12 på fork main | Väntar S2+S3.5 |
| S5 | **Helrumsbeviset** — sex ben, fork main | Väntar S3.5 |
| S6 | Mergeunderlag → ägarbeslut | Sist |

**S3.5 är den viktigaste öppna frågan och den är ägarens.** Mergekandidaten måste
definieras fullständigt innan S4/S5 körs, annars bevisar mätningarna fel sak:
1. **F1/kantflaggorna** — på eller av? (M1:s 99,6 % är mätt **med** dem; deras
   stående är uppskjutet ägarbeslut sedan 19/8.)
2. **Mållinjefixen ingår** om nk7 inte fällde den — den gör den inte (bunden
   regel i planen). Utan fixen är mål A omöjligt på IN ring.
3. **K2/receptautostart-läget per ben** (recept-hash, autostart på/av).
4. Binär-sha + grafstatus skrivs i samma kvitto som banden.

**S5:s kriterium är förseglat i planen:** ≥99/100 per ben (n=100, två fall
fäller), med den utskrivna begränsningen att det är en **observerad-andelsgrind**
— den bevisar inte statistiskt att sanna andelen ≥99 % (99/100 ger 95 %-intervall
[0,945; 1,0]). Vill ägaren ha M1-likvärdigt bevis krävs n≈510/ben. Referensarm
körs **bara** på de fem ben dk1 inte mätte (IN ring har redan kontrasten
0/20 mot 20/20).

**Buntens minsta innehåll (gäller S2, S4, S5 — hårt krav, dk1-utgåva-1-läxan):**
tick-band `attempt_*.jsonl` per ben och arm · `SHA256SUMS` över allt ·
`granskriterier.py` + `ra_kanon.py` **i bunten** · K2-kvitto för **alla** steg per
arm · flaggvärden + binär-sha + grafstatus i samma kvitto som banden ·
interfolieringslogg. Och: **avsändaren ska själv reproducera huvudutfallet inuti
bunten innan den skickas.**

---

## 7. PITFALLS — allt som bitit oss, med botemedel

### 7.1 Processer och skal
- **`pgrep -f` i en ssh-kedja matchar sitt eget kommando.** Kostade 30 min och
  gav en **falsk lägesrapport** ("KÖR" när inget körde). **Vänta på PID via
  `/proc`, aldrig på mönster.** (Minne: `pgrep-sjalvmatchning`.)
- **Inga heredocs med apostrofer/flerradstext över ssh.** Skriv fil lokalt,
  `scp`, kör. Nästlade ssh+Python-heredocs krossar citattecken.
- **`;` i grindade led** lät systemd-run starta trots att skriptet sagt STOPP.
  Använd `&&`.
- **lanister saknar `rg`.** Använd `grep`/`find`.

### 7.2 Order och facit
- **Facit gäller ÖVER ordertexten.** Rollerna ska stoppa dig — det hände två
  gånger i dag och båda gångerna hade jag fel (se 7.5).
- **A1-läxan: en order ska CITERA facitets kriterium ordagrant, aldrig återge
  det.** Min order till dk1 bar receptautostart-facitets kriterium i stället för
  mållinjefacitets. Utfallet råkade passera båda — men så byts en grind ut i
  tysthet.
- **Förseglade dokument ändras aldrig.** Fel rättas i nytt addendum; v1 lämnas
  orörd (addendum 3 v1 → v2 är mönstret).

### 7.3 Mätning och instrument
- **Negativkontrollera varje grind före användning** — ett grönt prov som aldrig
  setts falla är inget prov. Både QA och Kodaren fann i dag grindar som passerade
  på fel indata (bl.a. en sha-grind som godtog 8 hextecken).
- **`arrived` är en instrumentavläsning producerad av koden som prövas** — inte
  en observation. Kräv `dxy`/`dz` och vilken gren som fyrade.
- **n=1 är ingen avläsning.** nk7-läxan: en korridor som ger 13/20 på oförändrad
  binär gör ett enskilt utfallsbyte till en dragning.
- **Bundna konstanter måste namnge sitt träd.** Fixturfällan: lokal mains
  nivå-2 (`4c099331…`, 5981/48217) bands som förkörningskontroll på en rigg som
  bygger 5977/48207 — en kontroll som failar varje gång blir förr eller senare
  "rättad" mot vad riggen råkade ge.
- **Två portningar av samma algoritm är inte två oberoende härledningar.**
- **Banminnet (`traj`) är hårdkapat till 1200 rader ≈ 15,6 s** medan mätfönstret
  är 30 s, och `traj` når tråden bara via `Arrived`/`GotoStall` — **timeoutförsök
  lämnar ingen bana**. Det blockerar all traj-baserad mätning tills instrumentet
  byggs (design finns färdig i den rapport som ligger i denna sessions logg;
  **KOMPLETTERING: den är inte skriven till fil — be Kodaren återskapa den**).

### 7.4 nk7/N1 — vad ägaren faktiskt beslutade
Jag presenterade först riggpasset som "75–97 % chans att frågan avgörs". **Det
var fel inramning** (QA:s villkor V6, bindande): passet stänger alltid frågan —
risktalet är risken att det stänger **fel** och lyfter etiketten till BÄTTRE fast
korridoren är sämre: 3,4 % @ 35 pp, **25 % @ 25 pp, 46 % @ 20 pp, 68 % @ 15 pp**.
**Den felaktiga riskformuleringen får inte citeras vidare.** Ägaren svarade med
95 %-kriteriet i §0 → passet utgår, etiketten står, resurserna går till S5.

### 7.5 Roller stoppade mig — respektera det
- Hopparen stoppade dk1 för att min order krävde receptautostart i arm 4, vilket
  facitet uteslöt på fyra ställen. Rätt av honom; jag skrev om ordern.
- Instrumentagenten vägrade bygga R6-instrumenten eftersom addendumet förbjöd
  kod före QA-PASS + Sols kontrasignatur. Rätt av honom; jag hade beställt för
  tidigt.

### 7.6 Rigghygien
- **En riggägare i taget.** Rigglåset `~/lab/.rig-lock` tas i körningens namn och
  släpps med bevis. **Praxis är att tömma filen; RIGG-REGLER säger ta bort den —
  likriktning är en öppen punkt.**
- **Förbjudna portar (rörs aldrig):** 27550, 27991, 27530, 27700, **28502,
  28503** (KTX-paret).
- Mätriggar ska vara **släckta i vila**; mönster-units (`ra-drill-*`) med
  3h-tak (`RuntimeMaxSec=10800`-drop-in), transient endast om mönsterunit saknas.
- **Ö12 (öppen):** `RTX_RIG_LOCK`-drop-in:en ska avarmeras eller uttryckligen
  ägaraccepteras före skarp etapp 1. Ägare: Hopparen. **Ej utförd.**

### 7.7 Sessionshygien (bet oss flera gånger)
- **`/compact` äter order.** Två order till herdr-säten försvann i
  kompakteringsfönster; upptäcktes bara för att jag kontrollerade transkriptet.
  **Verifiera alltid att ordern syns hos mottagaren.**
- **Qwen:** en uppgift = en färsk session; >~2 compact ⇒ avbryt och starta om.

### 7.8 Repot ljuger grönt
- **23 tester gör `eprintln! + return` när `RTX_TEST_BSP`/`_MAPS`/`_DEMOS`
  saknas och räknas som PASS.** CI sätter dem aldrig. Grönt T0 täcker alltså inte
  navmesh-byggaren mot en riktig BSP. (Kallstartsgranskningen, fynd 1.)
- `reference/recept/applicera_recept.py` hade hårdkodad `sys.path.insert` mot
  ägarens hemkatalog före `--torrkor` — dör på varje annan maskin. **Kontrollera
  om den är lagad; jag har inte verifierat att den är det.**
- **P0-5 ur kallstartsgranskningen är INTE åtgärdad:** det finns ingen
  anskaffningsväg för `playground/` (gitignorerad). Utan den kan en ny maskin
  inte resa en rigg alls. Flaggat i issue **#34**. Min statusrad i
  `docs/coldstart-review-2026-08-21.md` säger felaktigt att alla fem P0 är
  åtgärdade — **rättelsen står i #34, men filen bör rättas.**

---

## 8. FÖRSTA SJU ÅTGÄRDERNA I DIN ORDNING

1. **Lös rollkonflikten (§1)** — bemanna valideringssätet med någon annan, eller
   deklarera öppet att talen är ovaliderade.
2. **Läs** `PLANS/2026-08-21-plan-ra99-ring2quad.md` (utkast 3) och denna fil.
   Kopiera in `/tmp/grok-plangranskning.md` i `WORK_LOGS/` innan `/tmp` städas.
3. **Lägg S3.5 på ägarens bord** — mergekandidatens konfiguration (§6). Inget
   mätarbete i S4/S5 är meningsfullt före det.
4. **Gör om dömd körning 2 ren** (Hopparen-roll, färsk instans): kedjan i §3.2 är
   fullt kontrasignerad, riggen är städad och låset fritt. Kräv §2.5:s
   bekräftelseled (riggen läser själv 5977/48212 + `feeea6b4…`; skiljer den sig
   är körningen ogiltig och **konstanten rättas aldrig**). Komplett bunt från
   början (§6). Avarmera Ö12 först.
5. **QA-dom + oberoende omräkning** på den körningen. Sedan Sol.
6. **#41** — ring2quad på fork main med `vf5-fork-omharledning`, i S3.5:s
   konfiguration. Kriterium 12/12 hela kedjor.
7. **S5 helrumsbeviset** i samma konfiguration → **S6 mergeunderlag till ägaren.**
   Mergen är och förblir **ägarbeslut** (facit §15.1: ingen variant blir stående
   av en dömd körning).

---

## 9. VAD JAG INTE HANN — ärlig lista

- Dömd körning 2 (avbruten, §4).
- #41 och S5 — **inget helrumsbevis per ben på fork main efter fixen existerar.**
  Säg det rakt till ägaren; lova inga procenttal före mätning.
- Navmeshdoktorns dokumentförbättringar: 15 förslag ligger som issues #1–#20,
  **inte inarbetade**. Doktorsdokumenten är dessutom **oförseglade** (inga
  sha-sidofiler) — de omfattas alltså inte av ändringsdisciplinen.
- Grok1:s öppna meshspår (issues #21–#25): stallceller 722, 3295 (fixen
  **försvinner vid omstart**), 2544, ~103 orörda celler, hyllhörn z=264.
- P0-5 (§7.8) och `docs/coldstart-review`-statusradens rättelse.
- Lagbench-p3-mergen och generatorinbakningen (#39) — gamla ägarbeslut som
  fortfarande väntar (`WORK_LOGS/2026-08-21-handoff22.md`).

---

## 10. RAPPORTERING TILL ÄGAREN — formen är bunden

Svenska, ägarnivå: **mål, beslut, kostnad** — aldrig metaaktiviteter eller
jargong. Fast format: **(1)** verifierat klart med evidens (filväg/sha),
**(2)** pågående med den blockerande punkten, **(3)** EN completion-siffra
avstämd mot föregående rapport. Aldrig två motstridiga tal. Avvikelser flaggas
proaktivt i samma stund de upptäcks, inte på fråga. Rapportera aldrig "PASS"
eller en förbättringssiffra du inte själv kört om — och märk allt som vilar på
en enda körning som provisoriskt.

**Ägaren heter Xerial. Använd hans begrepp, hitta inte på egna.**

---

*Fable (Claude), 2026-08-21. Riggen städad och verifierad av mig i kväll:
units släckta, portar tysta, KTX orört, rigglås fritt. Fork main `58096bf`.
Sista liggarrad: 160.*
