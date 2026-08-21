# HANDOFF 23 — ÖVERLÄMNING TILL GROK SOM ORKESTRATOR

**RÄTTAD UTGÅVA 2 efter oberoende adjudicering (se
`WORK_LOGS/2026-08-21-handoff23-adjudicering.md`) — 17 sakfel rättade.**

**Skriven av Fable (Claude) 2026-08-21 kväll, sista handling före tokenslut.**
Ersätter handoff22 som kanonisk ingång. Läs denna FÖRST, hela, före första åtgärd.

Allt nedan är verifierat av mig i denna session eller citerat med filväg. Där jag
är osäker står det utskrivet. Inget är påhittat; kompletteringar är märkta
"KOMPLETTERING". Utgåva 2 bär dessutom rättelserna R1–R21 ur adjudiceringen samt
tre fynd som gjordes efter att adjudiceringen skrevs — de är märkta
**"NYTT I UTGÅVA 2"**. Ändringsloggen ligger sist.

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

**Läs detta först i §0:** de tal som bär ordet "99 %" i den här överlämningen är
inte fria att citera. Sol rad 157 och rad 160 spärrar uttryckligen basgrafhashen
`58787ce0…` och efter-hasharna `180315a3…`/`feeea6b4…` från att användas som
kanoncykel- eller 99 %-belägg — de är förkörningskontroller. Och Sol rad 153
spärrar språket kring mållinjefixen: OKLAR *"stänger BÄTTRE"*, och 0/20→20/20
*"får redovisas endast med etiketten"*. Se §3.1 och §3.2.

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
- Du tar orkestratorrollen enligt `.claude/agents/fable-orkestratorn.md` — men
  **inte oförändrad**, se nedan.
- **Valideringssätet måste bemannas av någon annan instans.** Utan det: skriv ut
  i varje ägarrapport att talen är **oberoende ovaliderade**, och lova aldrig
  annat.
- Blandar du rollerna ändå: säg det öppet i rapporten. Solodomar är svagare och
  ska märkas så (samma regel som navmeshdoktorns regel 7).

**Konkret, verifierat 2026-08-21 ~22:0x med `herdr agent list` (R13):** det står
redan lediga säten i arbetskatalogen `/home/xerial/dev/buzz-4on4` —

| Säte | Namn | Agentsort | Status |
|---|---|---|---|
| **`wN:pG`** | `grok-4on4-2` | **grok** | **idle** |
| `wS:p1` | `grok-qwen-review` | grok | idle |
| `wN:pH` | `deepseek-v4-pro-4on4` | `pi` | idle |
| `wN:pB` | `qwen-4on4` | `pi` | idle |
| `wN:p4` | `terra-4on4` | `pi` | idle |

**`wN:pG` är det självklara valideringssätet** — grok-CLI, ledigt, rätt
arbetskatalog, ingen orkestratorkontext. Du behöver inte starta något nytt för
att lösa §1. (Rättelse mot adjudiceringen: `wN:pH` är en `pi`-session med namnet
`deepseek-v4-pro-4on4`, inte en deepseek-CLI. Det spelar roll om du beordrar dit
något som förutsätter en viss CLI.)

**Obs om `wN:p4`:** i `herdr` heter det `terra-4on4` och körs som `pi` — bekräfta
att det är Sol innan du beordrar dit något.

**Två saker som inte är mitt förslag utan hinder — de ska till ägaren, inte
lösas internt:**

1. **`fable-orkestratorn.md` kan inte bäras av dig oförändrad.** Verifierat i
   filen: frontmatter pinnar **`model: fable`**, brödtexten öppnar **"Du är
   Fable, teamets release manager och orkestrator"**, och rad 20–21 innehåller
   regeln *"I RA-99-spåret grok-valideras ALLT före ägarrapport, via neutral väg
   — aldrig WORK_LOGS-material till grok."* Den regeln beordrar §8.2 dig att
   bryta i din andra åtgärd. **Rollfilen måste anpassas** — ny variant utan
   modellpinne, utan "Du är Fable", och med den regeln uttryckligen ersatt.
   Skriv om den, bär den inte som den står.
2. **Upphävandet av ägarregeln "grok läser aldrig WORK_LOGS" är ett
   ÄGARBESLUT.** Ägarregeln av 2026-08-20 lyder: *allt i RA-99-spåret
   grok-valideras före ägarrapport; material via neutral väg, aldrig WORK_LOGS
   till grok.* Blir du orkestrator upphör den regeln att kunna uppfyllas. Det är
   en **ägarregel som sätts ur spel**, inte en rollpreferens och inte ett val du
   får göra åt ägaren. **Lägg den på Xerials bord i samma ärende som S3.5.**

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

### 2.1 Hur roller startas — den VERIFIERADE vägen (R14)

**Hur jag startade dem:** Claude Code-subagenter via Agent-verktyget, med
prompten inledd av *"Läs FÖRST `.claude/agents/<roll>.md` och arbeta under den
rollen"*.

**Du har inte det verktyget, och jag har inte verifierat att grok-CLI:n har en
motsvarighet — anta det inte.** Utgåva 1 påstod att du skulle "använda grok-CLI:ns
egen subagent-/task-mekanism". Det var ett antagande utan belägg, i ett dokument
som i övrigt anger filväg och sha för allt. Ta det inte som en instruktion.

**Den verifierade vägen är `herdr`** — samma väg som `pA`/`pC`/`pF`/`pE` en gång
skapades. Kommandoytan är kontrollerad mot `herdr --help` 2026-08-21:

```
herdr pane split <pane> --direction down
herdr agent start <namn> --kind <kind> --pane <id>
herdr agent prompt wN:<pane> "<text>"      # första raden: läs rollfilen
herdr agent get wN:<pane>                  # verifiera 'working' inom ~10 s
herdr agent wait wN:<pane> --until idle
```

- `herdr agent start` = *"Start a supported interactive agent in an existing
  pane"*. Giltiga `--kind`-värden inkluderar **`claude`, `grok`, `pi`, `codex`,
  `gemini`, `kimi`, `opencode`, `copilot`, `droid`, `amp`** m.fl.
- `herdr agent wait --until` tar `idle`, `working`, `blocked`, `done`, `unknown`.
- **Slash-kommandon** (t.ex. `/compact` till en Claude-CLI) går **inte** genom
  `herdr agent prompt` — de kräver `herdr pane send-text` följt av
  `herdr pane send-keys Enter`.
- Socket: `~/.config/herdr/herdr.sock`.

**Första raden i varje order ska vara att läsa rollfilen.** Rollfilen bär
reglerna; ordern bär uppgiften. Det fungerade — rollerna stoppade mig korrekt två
gånger (se §7.5).

### 2.2 Levande herdr-säten

- `wN:p1` — Fable (min panel; tom när jag är slut)
- `wN:p4` — **Sol** (GPT-5.6): kontrasignatär, variant B. Bokför själv i liggaren.
  Heter `terra-4on4` i `herdr` och körs som `pi` — bekräfta identiteten först.
- `wN:p9` — **grok** (du), `grok2-4on4`
- **`wN:pG`** — **grok, `grok-4on4-2`, LEDIG** — valideringssätet, se §1
- `wS:p1` — grok, `grok-qwen-review`, ledig
- `wN:pH` — `pi`, `deepseek-v4-pro-4on4`, ledig
- `wN:pB` — `qwen-4on4` (`pi`), parkerad, ny roll enligt §2-tabellen
- Stängda 2026-08-21: `pA`, `pC`, `pF`, `pE` (blev subagenter), `p6` (grok1,
  konsoliderad — slut-handoff i `WORK_LOGS/2026-08-21-grok1-slut-handoff.md`),
  `p3` (deepseek, ute ur uppställningen på ägarbesked).

**Efter varje prompt till ett herdr-säte: verifiera inom ~10 s att status gick
till `working`** (`herdr agent get wN:pX`) — prompter kan fastna opostade.

### 2.3 Vägar och portar du behöver och som inte stod i utgåva 1

- Forkträdet på riggen: **`lanister:~/rtx-recept`** (fjärr `fork`). Det är där du
  kör `git fetch fork`, `git ls-remote`, `git worktree add`.
- Buntar bor i **`lanister:~/hopptraning/<id>-grokbunt/`**.
- Rådata från körningar: **`lanister:~/hopptraning/<id>/`**.
- dk1:s spegelportar var **`:27970`** och **`:27960`**, RA-kontrollen **`:27990`**.
- Det operativa i `AGENTS.grok-validator.md` (den fil §1 ber dig lämna) är därmed
  lyft hit — du behöver inte gå tillbaka till den för vägarna.

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
  Kvitto: `WORK_LOGS/2026-08-21-domd-korning-1-korkvitto.md`.
- **Kontroller nk1–nk3 och nk5–nk6 PASS (R2).** **nk4 är INTE PASS** utan
  *"0 FAIL, 2 UA2"* (körkvittot §6, bekräftat av Grok) — två utfall omklassade,
  huvudutfallet opåverkat. Skriv aldrig "nk1–nk6 PASS".
- **Oberoende validerat, med utskrivna begränsningar (R1):** Grok räknade om
  bunten och bekräftade huvudutfallet 0/20 mot 20/20 på 70 u-disken
  (`sha256sum -c SHA256SUMS` → **26/26 OK**). **Originalbanden `attempt_*.jsonl`
  saknades i bunten** — omräkningen gjordes ur
  `data/band-domda-mot-bada-kriterierna.json` och korloggen, **inte ur tickarna**.
  70 u och topp-vid är alltså **inte** omobducerade från tick. Groks egna
  räknetal är korlogg 40 rader, band 20+20, T1 21+21, nk6 5+5, nk7 21+21, n20
  20+20 — **talet "71/71" som stod i utgåva 1 finns inte i något dokument och får
  inte citeras.** Två invändningar står öppna: (a) stoppositionerna i RAPPORT §5
  är trängre än banden (arm 3 är 88–101 u, inte 88–100; arm 4 är 59–69 u, inte
  61–65, och `n_uppe` är 44–54, inte "~44 ticks"), (b) nk2:s frysta data ligger
  inte i bunten. Groks slutsats om nk7: **mer mätning krävs**. Källa:
  `WORK_LOGS/2026-08-21-grok-dom-dk1.md`.
- **Dom (R4):** `WORK_LOGS/qa-dom-mallinjefix-design.md`. **Läs hela filen (2015
  rader) — "SLUTDOM" på rad 989 är INTE den sista domen.** Efter den följer
  `VERDICT: FAIL` på rad 1169 (addendum 3 v1) och `VERDICT: PASS` på rad 1493
  (addendum 3 v2) med **bindande V6**, som säger att riskprosan inte får gå till
  ägaren som den står. Ordet "SLUTDOM" får dig annars att stanna 1000 rader för
  tidigt. SLUTDOM 989 lyder: **§9 PASS · nk7 OKLAR · §12 OFÖRÄNDRAT/OSÄKERT.**
  Etiketten är ett **regeltak**, inte ett mätresultat — 0/20→20/20 står.
- **Pappersked komplett:** facit `a8ba66a1…` + addendum 1 `3562b7fa…` (Sol rad
  151) + addendum 2 `b652d8ca…` (Sol rad 153) + **addendum 3 v2 `ab0cf7c7…`**
  (QA PASS, Sol rad 158). Addendum 3 **v1** (`3cca2e02…`) är ersatt men orörd.
- **nk7 (den enda oklara korridoren) (R3):** ägaren valde bort extramätningen
  (N1-b) efter att QA rättat riskinramningen — se §7.4. Etiketten förblir
  OFÖRÄNDRAT/OSÄKERT. **Det blockerar inte mätarbetet i S4/S5 och inte mergen,
  men det blockerar språket:** Sol rad 153 är bindande — OKLAR *"utlöser inte
  SÄMRE men stänger BÄTTRE"*, och uppmätta 0/20→20/20 och 20/20 topp-vid *"får
  redovisas endast med etiketten"*. **Leveransen får alltså inte beskrivas som en
  förbättring i ägarrapporten.**
- **Följdkrav (Sol rad 158, V9), utlöst av att N1-b valdes — detta bär du nu:**
  *"väljs N1-b körs ingen parad offlineutvärdering; F är då oprövad i praktiken
  och icke-FAIL vilar på källkodsargumentet, vilket ska stå i ägarrapporten."*
  Ägaren **valde** N1-b (§7.4). Meningen ska alltså in i din ägarrapport.
- **Övriga bindande villkor ur Sol rad 158 som följer med:** **V8** —
  `verify_a3v2.py` saknas och ska bevaras/biläggas med negativkontrollutfall;
  **V10** — egenskapssvepets frö och punktantal ska bindas i kvittot, alternativt
  deterministiskt rutnät; **V11** — `N1` återanvänds inte generiskt, framtida
  korridorposter får egna id:n.

### 3.2 Receptautostarten (automatisk receptapplicering) — KOD KLAR, KÖRNING AVBRUTEN
- Gren `receptautostart` @ **`a86586ed`** (på fork). Kod-PASS, applicerarens
  `--verifiera-offline` lagad (kraschade förut med `KeyError`), sha-grindar
  stramade till full längd.
- **Mutationsproven är 5/5 i `recept.rs` och 10/10 på offlinevägen**
  (liggaren rad 159). **Talet "15/15" i utgåva 1 ska inte citeras** — strängen
  finns inte i receptautostart-domen (R5/faktafel 9).
- **Pappersleden är förseglade och kontrasignerade** (facit `964e80e7…` +
  addendum 1 `5b55c045…` + addendum 2 `003bebdb…`, Sol rad 157 + addendum 3
  `497fdb5c…`, Sol rad 160; QA PASS på båda —
  `WORK_LOGS/qa-dom-receptautostart-design.md`). **Men QA:s restlista är inte
  stängd (R5):** Sol rad 157 — *"L2–L11 förblir öppna"*; Sol rad 160 — *"L10/N1–N2
  och L11:s nio §16-kvitton blockerar dom."* **Kedjan bär alltså papperet, inte
  domen.**
- **Basgrafen `58787ce0…` (dm3 5977/48207)** är korsbekräftad av riggmätning
  (Hopparen), offlineräkning (S3-spåret) och QA:s egen implementation.
  Efter-hasharna: `180315a3…` (5977/48211) och **`feeea6b4…`** (5977/48212,
  kedjans slutläge).
  **BINDANDE CITATFÖRBUD (Sol rad 157 och 160):** dessa värden *"får inte
  användas som kanoncykel- eller 99 %-belägg"* — de är
  förkörnings-/förutsägelsekontroller. Hela §0 ramar in uppdraget som "99 %";
  detta är talen som är utestängda ur just den meningen.
- **F3–F5 bokförda i Sol rad 160** och följer med: §2.3:s block är ett förkortat
  utdrag och får inte kallas "ordagrant" (F3); vF5:s `STOPP: ogiltig
  efter-konstant` är mekaniskt belagt men inte reproducerat mot någon vF5-basdump
  på riggen (F4); moderfacitets ord "mäts" ersätts för `efter` av den
  QA-godkända offlinehärledningen, och **lagligheten vilar bindande på
  körstartens bekräftelseled** (F5).
- **DÖMD KÖRNING 2 ÄR AVBRUTEN MITT I** — se §4. **Inget körkvitto finns.**

### 3.3 Ring2quad — 12/12 bevisat, men inte på fork main
- 12/12 hela kedjor (delmoment 12/12 vardera) mättes på **vF5-grafen
  `5981/48211`, nivå-2 `d155c22e…`** — alltså **med vF5-receptet applicerat**,
  med **lokala mains binär**. **`5981/48217` (nivå-2 `4c099331…`) är baslinjen
  FÖRE receptet och är inte mätgrafen** (R6). Utgåva 1 band 12/12 till 48217; det
  var projektets egen fixturfälla (§7.3) begången i själva överlämningen.
  Logg: `WORK_LOGS/2026-08-19-hopptraning-ring2quad.md` §29.3, tabellen vid rad
  1683; grafraden står ordagrant där: *"Allt nedan är mätt på vF5-grafen,
  5981/48211, nivå-2 `d155c22e…`, en bot"*.
- **Kedjedefinitionen (loggen rad 1660), som måste följa med talet:** ett
  kedjeförsök = de tre benen i följd, **vart och ett teleporterat till sin egen
  startpunkt** precis som när hoppen mättes var för sig. Kedjan räknas som hel
  endast om alla tre benen lyckas. **Det är alltså inte en sammanhängande
  färdväg.** Baselinevarvet kördes dessutom **utan vakter** (RA-vakten och
  SNG-vakten kunde inte köras, ägarens order tog bort deras bottar).
- **Grenspetsen är avgjord, ingen kontroll behövs (R6).** Gren `ring2quad` på
  fork = **`04666b72`**. Handoff22:s `c588d3b` är **ancestor till** `04666b72` på
  samma gren, 25 minuter äldre — alltså **superseded, inte konkurrerande**.
  Verifierat: `git merge-base --is-ancestor c588d3b 04666b72` → sant; `04666b72`
  är *"70u: rattelse efter groks dom + fonstermatningen"*. Utgåva 1 lämnade detta
  som en öppen fråga till dig; det var ett arbetsmoment som borde varit utfört.
- **Receptet är omhärlett mot fork mains graf** (S3, klart 21/8): gren
  **`vf5-fork-omharledning` @ `d886623`**, pushad till fork av mig i kväll så den
  inte går förlorad ur `/tmp`.
  - Rätt generation bevisad via **avfartskoordinaten `[454,7 · 153,3]`** —
    **nivå-2-hashen kan INTE skilja vF3/vF4/vF5 åt** (avfarten bor i
    sidotabellen). Använd aldrig hashen som generationsbevis.
  - Portningskvitto `fork-basbindningen`: rätt recept mot rätt graf matchar; sex
    negativkontroller vägrar korrekt (korsprov åt båda håll).
- **Utfallssiffrorna är INTE omhärledda** — 12/12 gäller vF5-grafen `5981/48211`
  med lokal mains binär. De får **inte** citeras som fork mains utfall. Det är
  sluttestets jobb (se namnkollisionen i §3.5).

### 3.4 RA-rummet som helhet — vad "99 %" faktiskt vilar på
- Bästa per-ben-mätningen: **M1, 508/510 = 99,6 %**, 0 fall, 2 fastnad
  (`WORK_LOGS/m1-runda1-kvitto.md`, grok-omräknad). **MEN:** mätt på **lokal
  main** med **F1-flaggorna** (`rtx_bot_edge_narrow=1`, `rtx_bot_walkdiag=1`,
  `rtx_bot_walkplan=1`), **inte** på fork main och **utan** mållinjefixen.
- Senaste helrumsmätning i kedjad drift: **T1h, körd 2026-08-15** — fork
  399/450 (88,7 %), main 415/450 (92,2 %).
  `WORK_LOGS/2026-08-15-t1h-timtest.md`.
  **Per ben, fork (R7 — utgåva 1 utelämnade det svagaste benet):**

  | Rutt | fork | main |
  |---|---|---|
  | UT ring | 100 % | 100 % |
  | **IN ring** | **80,0 %** | 82,7 % |
  | UT tunnel | 100 % | 100 % |
  | IN tunnel | 82,7 % | 81,3 % |
  | UT väst | 80,0 % | 93,3 % |
  | IN väst | 89,3 % | 96,0 % |

  **IN ring 80,0 % är det ben hela mållinjefixen handlar om** — det svagaste och
  det mest relevanta. Det ska stå med.
- **BINDANDE FÖRBEHÅLL PÅ T1h (R7 + utelämnande 21):** serien kördes **15/8**,
  klockan **18:25–19:26 CEST** — utgåva 1 skrev "18/8" och förväxlade klockslag
  med datum. Och den mätte **ett annat mål** ("topp-vid": z≥320,
  dxy([250,−703])≤130, ≥15 konsekutiva ticks, på ägarens order). Filens eget
  villkor, rad 5–7: *"serien T1h **JÄMFÖRS ALDRIG** med K-serier/kanonfacit."*
  **88,7 % får därför inte ställas mot Mål A:s kanonkriterium.** Utgåva 1
  använde talet som helrumsbaslinje för Mål A. Gör inte om det.
- **Den är dessutom från före mållinjefixen.**
- **Det finns alltså inget helrumsbevis per ben på fork main efter fixen.**
  Det är precis vad S5 ska producera, och det är gapet mot ägarens 95 %-krav.

### 3.5 TVÅ NUMMERSERIER SOM KOLLIDERAR — läs innan du citerar ett "#" (R19)

Sluttestets **"#41"** och generatorinbakningens **"#39"** i planen är **planens
interna numrering**, inte GitHub-issues. På GitHub (`Xerialen/rtx`) betyder samma
nummer något annat:

- **GitHub #41** = `[kallstart][P1] docs/baseline/DEMOS.md + hämtväg för
  demokorpusen`
- **GitHub #39** = `[kallstart][P2] rust-toolchain.toml — pinna verktygskedjan`

Utgåva 1 använde båda serierna omärkt i samma dokument. **Blanda inte serierna,
och skriv alltid ut vilken du menar.** Nedan skriver jag *"sluttestet (planens
#41)"* när planens serie avses.

---

## 4. DÖMD KÖRNING 2 — EXAKT LÄGE (viktigast just nu)

Hopparen-subagenten dog mitt i körningen när Claude-krediterna tog slut
(2026-08-21 ~19:05Z). **Jag har städat efter den** — verifierat av mig i kväll:

- Unit `ra-drill-dk2-receptautostart.service` var **kvar igång**; jag stoppade
  den. Portar 27580/27960/29580 **tysta**. KTX-paret 28502/28503 orört.
- Rigglåset var kvar taget; **jag släppte det** (tömde filen — se pitfall §7.6).
  Låsets innehåll arkiverat: `lanister:~/hopptraning/dk2/rig-lock-avbruten-20260821.txt`.
- **Ingen mätprocess körde** när jag städade (`pgrep` mot python-drivrutiner: inga).

**Fullständig filinventering (`lanister:~/hopptraning/dk2/`), R9 — utgåva 1
utelämnade fyra filer, och tre av dem visade sig avgöra saken:**

- `nkprov-resultat.json` — nk-mutationsbatteriet på unit-nivå, körningens
  oavkortade stdout, varje mutation med fällda tester + `aterstalld_byteidentisk`.
- `las10-av.jsonl` / `las10-pa.jsonl` — tio läsningar med recept AV resp. PÅ.
- `cellpar.json`, `cellpar-apparatfel1.json` (0444 — den senare namnger ett
  apparatfel, läs den).
- `utfall/in_ring/attempt_01..20.jsonl` + `utfall/in_ring/summary.json` +
  `utfall-kor.log` — 20 försök.
- `obd-dk2.json`, `obd-referensarm.json`, `obd-dk1arm3.json`,
  `obd-k2manuell.json` — fyra obduktioner.
- **Tidigare inte uppräknade:** `kontroll82-pass.log`, `kontroll82-nk5.log`,
  `riggtest10.log`.

### 4.1 NYTT I UTGÅVA 2 — §2.5:s bekräftelseled ÄR utfört och ligger i materialet

**Detta är den enskilt viktigaste rättelsen i utgåva 2, och den går emot både
utgåva 1 och adjudiceringen.** Utgåva 1 skrev att bekräftelseledet saknades ("jag
kan inte se i materialet att det bekräftats"). Adjudiceringen byggde vidare på det
och gjorde *"§2.5:s bekräftelseled kan inte rekonstrueras i efterhand"* till skäl
2 av 3 för omkörning. **Båda har fel.** Verifierat av mig 2026-08-21 ~22:0x:

- **`las10-pa.jsonl`** — tio riggläsningar 2026-08-21T18:53Z, var och en:
  `"cells": 5977, "links": 48212, "graph_content_hash":
  "feeea6b41284a1cddf3907f2d9e1ff668b48da524b865530df81925b997dbaa9"`, med
  `recept.utfall: "applicerat"`, `bas_hash: 58787ce0…`, `slut_hash: feeea6b4…`,
  `lankar: 5`. **Det är exakt den läsning Sol rad 160 kräver: 5977/48212 och
  `feeea6b4…`, läst av riggen själv.**
- **`riggtest10.log`** — tre rader `PAR-8.2-KONTROLL: PASS (5977/48212, niva2
  lika)`.
- **`kontroll82-pass.log`** — basläsningen med receptvägen AV: `rigg:27960`,
  5977/48207, nivå-2 `58787ce0…` → PASS.
- **`kontroll82-nk5.log` är en NEGATIVKONTROLL AV SJÄLVA GRINDEN, och den föll
  korrekt:** mot dumpen `dm3-rigg-full-graph.json` (5981/48217, `4c099331…`) gav
  PAR-8.2-kontrollen `FAIL - matt 5981/48217 … vantat 5977/48207 …`. Grinden kan
  alltså bevisligen falla. Det är precis det negativa provet husets regelverk
  kräver innan en grön grind får tros.
- Tidsordning: läsningarna 18:53Z, riggtest10 18:55, första försöket 18:56.
  Bekräftelseledet ligger alltså **före** körningen.

**Följd för din bedömning:** rådatan från dk2 är **starkare än båda dokumenten
påstår**. Skäl 2 för omkörning faller. Skäl 1 och 3 står kvar (se §4.3).
**Adjudiceringens gissning att `riggtest10.log` "sannolikt är det L10-material
§4 säger saknas" är också fel** — det är §2.5:s bekräftelseled, inte L10. L10
saknas fortfarande.

### 4.2 ODÖMT FYND SOM DU MÅSTE HANTERA RÄTT

Utfallsloggen slutar med `in_ring: 0/20 ok, median None, fall 2`
(`summary.json`: `n_ok: 0, n_timeout: 20, falls_tot: 2`). **Det är förväntat och
INTE ett underkännande av receptautostarten** — grenen `receptautostart` är byggd
på `4db5b19` (fork main + recept), alltså **utan mållinjefixen**, och utan den är
0/20 på kanondisken precis referensläget från dk1. Premissen är verifierad:
`git merge-base --is-ancestor 4db5b19 a86586ed` → sant, och `1e37b4e`
(mållinjefixen) är **inte** ancestor till `a86586ed`. 20 av 20 timeout utan
ankomst är exakt referensarmens signatur från dk1. Receptautostartens facit mäter
**att receptet appliceras automatiskt och rullas tillbaka rätt**, inte
kanonutfallet. Låt QA-domaren avgöra; **citera aldrig 0/20 som automatikens
betyg.**

**Rådatan bär instrumentmetadata (R9):** `summary.json` har per försök
`tic_drift_pct` (0,0–0,01 %), `poll_hz` (51,8–52,0), `max_steg_u` och `n_rader`
— det en obduktion kräver. **Materialet duger därför som negativkontroll för
omkörningen** (samma 20 timeouts ska återkomma) och ska **namnges i det nya
kvittot**, inte bara "sparas som referens".

**Vad som faktiskt saknas:** körkvitto
(`WORK_LOGS/2026-08-21-domd-korning-2-korkvitto.md` finns **inte**), grokbunt
(`~/hopptraning/dk2-grokbunt/` finns **inte** — verifierat), L10- och
L11-kvittona.

### 4.3 Rekommendation: gör om ren — på två hårda skäl (R10, korrigerat)

**Min rekommendation: gör om den ren.** Utgåva 1 motiverade det med omdöme
("materialet är oklart avgränsat", "ett kvitto skrivet av någon som inte var
där"). Det är svaga skäl. De hårda skälen är:

1. **Materialet uppfyller inte §6:s "buntens minsta innehåll".** Där saknas
   `SHA256SUMS` över allt, `granskriterier.py` + `ra_kanon.py` **i bunten**,
   K2-kvitto per steg per arm, flaggvärden + binär-sha + grafstatus i samma
   kvitto som banden, och interfolieringslogg. **Ett efterhandskvitto kan inte
   skapa dem.**
2. **Sol rad 160 spärrar domen ändå:** *"Före dömd körning 2 kvarstår risk 2b …
   L10/N1–N2 och L11:s nio §16-kvitton blockerar dom."* Även ett perfekt
   efterhandskvitto skulle mötas av en spärr.

**Skälet som INTE gäller:** §2.5:s bekräftelseled — det är utfört, se §4.1.
Adjudiceringen räknade det som skäl; det är det inte.

**Skäl som talar MOT omkörning och som du ska väga in:** rådatan är bättre än
beskrivningen antydde, bekräftelseledet finns, och grindens negativkontroll finns
dokumenterad. **Spara materialet, namnge det i det nya kvittot, och använd det
som negativkontroll** — samma 20 timeouts ska återkomma.

Körningen tar ~30–40 min riggtid.

---

## 5. VAD SOM LIGGER I FORK-REPOT (github.com/Xerialen/rtx)

**Fork main HEAD vid överlämning: `249e7af`** — verifierat av mig 2026-08-21
~22:0x med `ssh lanister 'cd ~/rtx-recept && git fetch fork -q && git rev-parse
fork/main'` → `249e7af9b4be8a78153b8a9478203badaf859b55`. Det är denna handoffs
egen commit (*"docs: handoff 23 - overlamning till Grok som orkestrator"*, 21/8
19:39 UTC). **Utgåva 1 angav `58096bf`; det talet var fel i samma stund det
skrevs** — `58096bf` är commiten närmast före (R8).

- `.claude/agents/*.md` — de sju rollerna (`9e6f342`, `bd838cc`)
- `reference/ra-room/` — **kanonen**, räddad in i repot (`a8e4f8d`). Den fanns
  tidigare i **ingen** commit i hela historiken.
- `docs/GOALS.md` (`e924d82`) — målen, märkt utkast
- `docs/BRANCHES.md` (`d93c8e7`) — grenregister
- `docs/AGENT-PREREQS.md` (`2cea442`) — de externa dokument rollfilerna kräver
- `docs/coldstart-review-2026-08-21.md` (`58096bf`) — kallstartsgranskningen
- `docs/HANDOFF-2026-08-21-grok-orkestrator.md` (`249e7af`) — denna fil
- Taggar `arkiv/91a6e34`, `arkiv/86f7f11` — två pinnade mätcommits räddade ur ett
  delat objektarkiv. **`c8a20fb` är permanent förlorad** (`git cat-file` →
  "Not a valid object name" i ~85 genomsökta repon).
- **45 GitHub-issues** (#1–#45): #1–#26 doktorsdokument-/mesh-/verktygsfynd,
  #27–#45 kallstartsfynden P1–P4 + #34 P0-status. Index:
  `WORK_LOGS/2026-08-21-gh-tickets-index.md`. (Issues var avstängda på forken;
  de slogs på i dag.)

**Levande grenar på fork — forken har 24 grenar (R8/faktafel 17):** de sex
namngivna nedan **+ 18 övriga** (utgåva 1 skrev "17 äldre").
`main 249e7af` · `mallinjefix 1e37b4e` · `receptautostart a86586ed` ·
`ring2quad 04666b72` · `vf5-fork-omharledning d886623` ·
`recept-i-tradet 4db5b19`. Klassning av de 18 saknas — se `docs/BRANCHES.md`.

**FÖRSEGLINGSVERKTYGEN FINNS INTE PÅ MERGEMÅLET (R21).** Verifierat med
`git ls-tree -r fork/<gren> -- tools/gates/`:

| Gren | filer i `tools/gates/` |
|---|---|
| `ring2quad` | **2** (`facit_lint.py`, `forsegla_facit.sh`) |
| `main` | **0** |
| `mallinjefix` | 0 |
| `receptautostart` | 0 |
| `vf5-fork-omharledning` | 0 |
| `recept-i-tradet` | 0 |

`facit_lint.py` / `forsegla_facit.sh` går alltså **inte att köra på
mergemålet**. §7.2 gör förseglingsdisciplinen bärande i hela kedjan. **De måste
portas till `main` före S6**, annars kan mergeunderlaget inte förseglas med samma
verktyg som allt annat.

**KRITISKT ATT VETA:** `WORK_LOGS/`, `PLANS/`, `GOTCHAS.md`,
`GUIDES/VERKTYGSGRINDAR.md` och `navmesh-doctor/` ligger **INTE i repot** — bara
i `/home/xerial/dev/buzz-4on4/` på pinnacle. **Katalogen är inte ens ett
git-repo** — det finns ingen lokal historik att falla tillbaka på. Hela
domkedjan, alla facit och alla kvitton bor där. Går den maskinen förlorad är
bevisningen borta. `docs/AGENT-PREREQS.md` listar dem.
**Detta hör hemma bland dina första åtgärder, inte i en parentes** — se §8
punkt 8. Kallstartsgranskningen underkände repot delvis just på detta.

**Öppen post som ingen handoff nämnt:** issue **#35** listar `1cc87180615f` som
fortfarande oompinnad. Bilden "två räddade, en förlorad" ovan är därmed
ofullständig. Commiten finns i det lokala repot, men det är **inte** verifierat
att den är nåbar från någon fjärr-ref på forken. Verifiera det innan bilden
upprepas.

---

## 6. PLANEN FRAMÅT — där jag slutade

Planen ligger i `PLANS/2026-08-21-plan-ra99-ring2quad.md` (utkast 3, granskad av
en oberoende subagent **och** av grok — båda granskningarna inarbetade).
Grok-granskningen ligger redan i
`WORK_LOGS/2026-08-21-grok-plangranskning.md` — **den är byteidentisk med
`/tmp/grok-plangranskning.md` (verifierat med `diff`, tom utdata). Utgåva 1
beordrade dig att kopiera in den; den ordern är redan utförd (R15).**

Stegen, i ordning, med status vid överlämning:

| Steg | Vad | Status |
|---|---|---|
| S1-papper | R6-förhandsregistrering (nk7) | **KLAR** — addendum 3 v2, QA PASS, Sol rad 158 |
| S1-rigg | R6-mätningen n=60/arm | **UTGÅR** — ägaren valde N1-b (se §7.4) |
| S2-papper | Basgrafaddendum + p3/p4 + restlistan | **KLAR** — addendum 2+3, QA PASS, Sol rad 157+160 |
| S2-rigg | **Dömd körning 2** | **AVBRUTEN** — se §4. Gör om ren. |
| S3 | Omhärled vF5 mot fork mains graf | **KLAR** — gren `vf5-fork-omharledning` |
| S3.5 | **ÄGARBESLUT: mergekandidatens konfiguration** | **VÄNTAR PÅ ÄGAREN** |
| S4 | **Sluttestet (planens #41)** — ring2quad 12/12 på fork main | Väntar S2+S3.5 |
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

**S5:s kriterium ska förseglas FÖRE körning (R16).** Planens ord, rad 75:
*"förseglat före körning: ≥99/100 per ben (n=100; två fall fäller)."* **Det är
ännu inte förseglat** — planfilen är `0664`, saknar `.sha256`-sidofil, och något
S5-facit finns inte. Per husets egen definition är ingenting förseglat.
**Förseglingen är ett led som måste utföras, inte ett läge som är uppnått.**
Utgåva 1 skrev "är förseglat i planen" och gjorde ett framtida krav till ett
uppnått tillstånd.

Kriteriet har den utskrivna begränsningen att det är en
**observerad-andelsgrind** — det bevisar inte statistiskt att sanna andelen
≥99 % (99/100 ger 95 %-intervall [0,945; 1,0]). Vill ägaren ha M1-likvärdigt
bevis krävs n≈510/ben. Referensarm körs **bara** på de fem ben dk1 inte mätte
(IN ring har redan kontrasten 0/20 mot 20/20).

**Buntens minsta innehåll (gäller S2, S4, S5 — hårt krav, dk1-utgåva-1-läxan),
komplett ur planen (R17):**
- tick-band `attempt_*.jsonl` per ben och arm
- `SHA256SUMS` över allt
- `granskriterier.py` + `ra_kanon.py` **i bunten** — och **EN förseglad olikhet:
  `at_topp` är `dxy < 70`**
- K2-kvitto för **alla** steg per arm (inte bara sista)
- flaggvärden + binär-sha + grafstatus i samma kvitto som banden
- interfolieringslogg
- **för R6: inspelad `traj`, inte bara `arrived`-biten**
- **avsändaren ska själv reproducera huvudutfallet inuti bunten innan den
  skickas.**

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
- **NYTT I UTGÅVA 2 — en subagents utdatafil-storlek är INGET mått på om den
  lever.** Jag stoppade adjudiceraren i kväll i tron att den hängt sig, eftersom
  utdatafilen inte växte. Den var i själva verket precis klar. **Rätt signal är
  agentens egen slutnotifiering** (eller `herdr agent get`/`wait --until idle`
  för herdr-säten) — aldrig filstorlek, aldrig CPU-tid, aldrig "det känns
  tyst". Kostnaden av att vänta är noll; kostnaden av att döda en färdig
  granskare är hela granskningen.
- **NYTT I UTGÅVA 2 — en hängd engångsprocess kan äta maskinen i en vecka utan
  att någon märker det.** Se §7.9.

### 7.2 Order och facit
- **Facit gäller ÖVER ordertexten.** Rollerna ska stoppa dig — det hände två
  gånger i dag och båda gångerna hade jag fel (se 7.5).
- **A1-läxan: en order ska CITERA facitets kriterium ordagrant, aldrig återge
  det.** Min order till dk1 bar receptautostart-facitets kriterium i stället för
  mållinjefacitets. Utfallet råkade passera båda — men så byts en grind ut i
  tysthet.
- **Förseglade dokument ändras aldrig.** Fel rättas i nytt addendum; v1 lämnas
  orörd (addendum 3 v1 → v2 är mönstret). *Denna handoff är inte förseglad — den
  är ett arbetsdokument, och utgåva 2:s rättelser är därför införda i texten med
  ändringslogg sist. Hade den varit förseglad hade de gått i addendum.*

### 7.3 Mätning och instrument
- **Negativkontrollera varje grind före användning** — ett grönt prov som aldrig
  setts falla är inget prov. Både QA och Kodaren fann i dag grindar som passerade
  på fel indata (bl.a. en sha-grind som godtog 8 hextecken). *Ett gott exempel
  ligger i dk2: `kontroll82-nk5.log` visar PAR-8.2-grinden falla korrekt mot fel
  graf — se §4.1.*
- **`arrived` är en instrumentavläsning producerad av koden som prövas** — inte
  en observation. Kräv `dxy`/`dz` och vilken gren som fyrade.
- **n=1 är ingen avläsning.** nk7-läxan: en korridor som ger 13/20 på oförändrad
  binär gör ett enskilt utfallsbyte till en dragning.
- **Bundna konstanter måste namnge sitt träd.** Fixturfällan: lokal mains
  nivå-2 (`4c099331…`, 5981/48217) bands som förkörningskontroll på en rigg som
  bygger 5977/48207 — en kontroll som failar varje gång blir förr eller senare
  "rättad" mot vad riggen råkade ge. **Utgåva 1 av denna handoff gick i exakt den
  fällan i §3.3** och band 12/12 till 48217 i stället för mätgrafen 48211. Läs
  §3.3 och lär av det.
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
95 %-kriteriet i §0 → passet utgår (N1-b), etiketten står, resurserna går till
S5. **Valet av N1-b utlöste V9 — se §3.1, den meningen ska in i ägarrapporten.**

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
- **Ö12 (öppen, och INTE lokaliserad) (R11):** `RTX_RIG_LOCK`-drop-in:en ska
  avarmeras eller uttryckligen ägaraccepteras före skarp etapp 1 (Sol rad 159 och
  160). Ägare: **Hopparen**. **Men sökning 2026-08-21 i `~/.config/systemd`,
  `/etc/systemd`, `~/lab`, `~/bin` och `~/.local` gav ingen `.conf`/`.service`-träff
  på `RTX_RIG_LOCK`** — träffarna är binärer och `facit-receptautostart-v2.md`.
  Antingen är drop-in:en redan borta eller heter den något annat.
  **Ordern "avarmera Ö12 först" är därför inte utförbar som den är skriven.**
  Första åtgärd är att **lokalisera filen eller kvittera att den inte finns** —
  inte att avarmera. Liggarens Ö12-post (rad 159) beskriver mekanismen. Detta är
  den enda punkt i hela överlämningen där sakläget är oavgjort.

### 7.7 Sessionshygien (bet oss flera gånger)
- **`/compact` äter order.** Två order till herdr-säten försvann i
  kompakteringsfönster; upptäcktes bara för att jag kontrollerade transkriptet.
  **Verifiera alltid att ordern syns hos mottagaren.**
- **Qwen:** en uppgift = en färsk session; >~2 compact ⇒ avbryt och starta om.

### 7.8 Repot ljuger grönt
- **23 tester gör `eprintln! + return` när `RTX_TEST_BSP`/`_MAPS`/`_DEMOS`
  saknas och räknas som PASS.** CI sätter dem aldrig. Grönt T0 täcker alltså inte
  navmesh-byggaren mot en riktig BSP. Bokfört som **GitHub-issue #36, märkt P2**
  — alltså inte som ett P0/P1-fynd.
- **`reference/recept/applicera_recept.py` är INTE lagad — verifierat av mig
  2026-08-21 (R18), så du slipper.** `sys.path.insert(0,
  "/home/xerial/rtx-tools")` står kvar på rad 127 i `mot_rigg()`, **före**
  `--torrkor`-grenen (rad 131) — och den ligger kvar likadant på **både**
  `fork/main` och `fork/receptautostart` (där den är rad 355 resp. 359). Skriptet
  dör alltså på varje annan maskin än ägarens, även i torrkörning. Bokfört som
  **GitHub-issue #42, P1**.
- **P0-4 OCH P0-5 är båda oåtgärdade (R18).** Statusraden i
  `docs/coldstart-review-2026-08-21.md` säger *"P0-1–P0-5 åtgärdade"* och namnger
  **fyra** commits (`a8e4f8d`/`e924d82`/`d93c8e7`/`2cea442`) för **fem** punkter.
  - **P0-5:** ingen anskaffningsväg för `playground/` (gitignorerad). Utan den
    kan en ny maskin inte resa en rigg alls. Flaggat i issue **#34**.
  - **P0-4** (granskningens rad 381, *"Laga eller ta bort `.claude/agents/`"*)
    kan inte vara stängd så länge `navmesh-doctor/` inte ligger i repot — vilket
    §5 själv konstaterar. Utgåva 1 antydde att P0-5 var den enda öppna.
  - **Statusraden i filen är alltså felaktig och bör rättas.**

### 7.9 NYTT I UTGÅVA 2 — maskinen var inte ren under mätningarna

**Fynd 2026-08-21 ~22:0x:** `python3 /tmp/kantreg3.py` (PID 2579913) hade kört på
**99,9 % CPU i 7 dygn och 5 timmar, sedan 2026-08-14 14:40**. Det var en
engångsanalys som fastnat i en oändlig loop — inga öppna filer, inga sockets,
PPID 1, ingen som ägde den. Den **konkurrerade om CPU med M1, T1h, dömd körning 1
och dömd körning 2.**

- **Åtgärdat:** jag stoppade processen 2026-08-21 ~22:0x och arkiverade skriptet
  som `lanister:~/hopptraning/arkiv-kantreg3-hangd-sedan-20260814.py` (4 827
  byte). Det är alltså **inte** en kvarstående uppgift för dig — utgåva 1 kände
  inte till processen, adjudiceringen fann den men lämnade den medvetet orörd.
- **CPU-toppen efter stoppet:** KTX-paret (`mvdsv` 28502/28503) på 1,1 % vardera.
  Maskinen är ren.

**KONSEKVENS SOM MÅSTE SKRIVAS UT, och som du bär vidare:**
**alla tidsbaserade mätvärden från 2026-08-14 och framåt är tagna under en
konstant CPU-belastning som nu är borta.** Det gäller M1, T1h, dk1 och dk2.
**Jämförelser mellan gamla och nya tider måste beakta det** — en ny mätning som
ser snabbare ut kan vara snabbare av det skälet allena, inte av kodens förtjänst.

**Falla/fastna-kriteriet påverkas rimligen inte** — det är ett utfallskriterium,
inte ett tidskriterium, och tidsdatan i dk2 ser frisk ut (`poll_hz` 51,8–52,0,
`tic_drift_pct` ≤0,01 %). **Men det är en bedömning, inte en mätning.** Skriv det
så till ägaren. Vill någon hävda saken hårdare krävs en omkörning av en
tidskänslig serie på ren maskin.

### 7.10 NYTT I UTGÅVA 2 — riggens verifierade läge vid överlämning

Verifierat av mig 2026-08-21 ~22:0x, kommandon utskrivna:

| Sak | Läge | Hur |
|---|---|---|
| `ra-drill-*`-units | **inga** (varken system eller user) | `systemctl list-units --all \| grep ra-drill` |
| Portar 27570/27970/29570/27580/27960/29580 | **tysta** | `ss -tulnp` |
| KTX 28502/28503 | **orört och lyssnande** (`mvdsv`, uppe ~10 dygn) | `ss -tulnp` |
| `~/lab/.rig-lock` | **tömd (0 byte) = fri** | `ls -la`, `cat` |
| `/tmp/kantreg3.py` | **stoppad och arkiverad** | `ps -eo pid,etimes,pcpu,args` |
| Högsta CPU-post | KTX-paret, **1,1 %** | `ps --sort=-pcpu` |

**Rättelse mot adjudiceringen — förbjuden port 27991:** adjudiceringen påstod att
`hub_publish.py` *"håller den förbjudna porten 27991"* och gjorde det till en
åtgärdspunkt. **Det stämmer inte.** Verifierat:
- `ss -tulnp | grep 27991` → **tomt. Porten är tyst; ingenting lyssnar på den.**
- Processen finns (PID 1475, uppe sedan 11/8 20:48, ~10 dygn) och bär flaggan
  `--control-port 27991` i sin kommandorad, men `/proc/1475/fd` innehåller
  **bara** fd 1 och 2 (stdout/stderr) — **den binder ingen socket alls.**
- Det är alltså en **kvarglömd process som refererar till porten**, inte en
  ockupation av den. Den blockerar ingenting och behöver inte hanteras före
  mätning. Städa den gärna, men det är inte en grind.
- De hub_publish-processer som faktiskt kör bär portarna 27996/27600/29600 (route
  drill), 27994/27640/29640, 27992/27660/29660 och 27995/27560/29560 — inga av
  dem på förbjuden port.

---

## 8. FÖRSTA ÅTGÄRDERNA I DIN ORDNING

1. **Lös rollkonflikten (§1).** Sätet står ledigt: **`wN:pG`** (`grok-4on4-2`,
   grok, idle, rätt cwd). Bemanna det som valideringssäte — eller deklarera öppet
   i varje rapport att talen är ovaliderade. Starta det med `herdr agent prompt
   wN:pG "<order>"`, första raden = läs rollfilen.
2. **Anpassa `fable-orkestratorn.md` innan du bär den (§1).** Ta bort
   `model: fable`, skriv om "Du är Fable", och hantera WORK_LOGS-regeln
   uttryckligen. **Bär den inte oförändrad.**
3. **Lägg TVÅ frågor på ägarens bord i samma ärende:**
   **(a) S3.5** — mergekandidatens konfiguration (§6). Inget mätarbete i S4/S5 är
   meningsfullt före det.
   **(b) Upphävandet av ägarregeln "grok läser aldrig WORK_LOGS"** (§1) — det är
   ägarens beslut, inte ditt.
4. **Läs** `PLANS/2026-08-21-plan-ra99-ring2quad.md` (utkast 3) och denna fil.
   *(Kopieringen av grok-plangranskningen är redan gjord — se §6.)*
5. **Utred Ö12 (§7.6) — lokalisera `RTX_RIG_LOCK`-drop-in:en eller kvittera att
   den inte finns.** Ägare: Hopparen. Detta ersätter utgåva 1:s order "avarmera
   Ö12 först", som inte är utförbar som skriven. **Riggen i övrigt är städad och
   verifierad — se §7.10; du behöver inte städa den igen.**
6. **Gör om dömd körning 2 ren** (Hopparen-roll, färsk instans): kedjan i §3.2 är
   fullt kontrasignerad, riggen är städad, låset fritt och maskinen numera utan
   den hängda CPU-processen. Kräv §2.5:s bekräftelseled (riggen läser själv
   5977/48212 + `feeea6b4…`; skiljer den sig är körningen ogiltig och
   **konstanten rättas aldrig** — Sol rad 160). **Namnge dk2-rådatan som
   negativkontroll i det nya kvittot** (§4.2). Komplett bunt från början (§6).
7. **QA-dom + oberoende omräkning** på den körningen (valideringssätet från
   punkt 1). Sedan Sol.
8. **Säkra bevisningen ur pinnacle (§5).** `WORK_LOGS/`, `PLANS/`, `GOTCHAS.md`,
   `GUIDES/` och `navmesh-doctor/` finns bara på en maskin, i en katalog som inte
   ens är ett git-repo. **Det är projektets enskilt största bevarandefråga.**
   Utgåva 1 lade den som "KOMPLETTERING/mitt råd" i en parentes; den hör hemma
   här. Spegla till repot eller ett arkiv.
9. **Porta `tools/gates/` till `main` (§5, R21)** — annars kan S6:s mergeunderlag
   inte förseglas med samma verktyg som resten av kedjan.
10. **Sluttestet (planens #41)** — ring2quad på fork main med
    `vf5-fork-omharledning`, i S3.5:s konfiguration. Kriterium 12/12 hela kedjor,
    med kedjedefinitionen ur §3.3 utskriven.
11. **S5 helrumsbeviset** i samma konfiguration → **S6 mergeunderlag till
    ägaren.** Mergen är och förblir **ägarbeslut** (facit §15.1: ingen variant
    blir stående av en dömd körning).

---

## 9. VAD JAG INTE HANN — ärlig lista

- Dömd körning 2 (avbruten, §4).
- Sluttestet (planens #41) och S5 — **inget helrumsbevis per ben på fork main
  efter fixen existerar.** Säg det rakt till ägaren; lova inga procenttal före
  mätning.
- Navmeshdoktorns dokumentförbättringar: 15 förslag ligger som issues #1–#20,
  **inte inarbetade**. Doktorsdokumenten är dessutom **oförseglade** (inga
  sha-sidofiler) — de omfattas alltså inte av ändringsdisciplinen.
- Grok1:s öppna meshspår (issues #21–#25): stallceller 722, 3295 (fixen
  **försvinner vid omstart**), 2544, ~103 orörda celler, hyllhörn z=264.
- P0-4 och P0-5 (§7.8) och `docs/coldstart-review`-statusradens rättelse.
- Lagbench-p3-mergen och generatorinbakningen (**planens #39**, inte GitHub #39 —
  se §3.5) — gamla ägarbeslut som fortfarande väntar
  (`WORK_LOGS/2026-08-21-handoff22.md`).
- Verifiering att `1cc87180615f` (issue #35) är nåbar från någon fjärr-ref på
  forken (§5).
- Portning av `tools/gates/` till `main` (§5).

---

## 10. RAPPORTERING TILL ÄGAREN — formen är bunden

Svenska, ägarnivå: **mål, beslut, kostnad** — aldrig metaaktiviteter eller
jargong. Fast format: **(1)** verifierat klart med evidens (filväg/sha),
**(2)** pågående med den blockerande punkten, **(3)** EN completion-siffra
avstämd mot föregående rapport. Aldrig två motstridiga tal. Avvikelser flaggas
proaktivt i samma stund de upptäcks, inte på fråga. Rapportera aldrig "PASS"
eller en förbättringssiffra du inte själv kört om — och märk allt som vilar på
en enda körning som provisoriskt.

**Fyra saker som MÅSTE stå i din första ägarrapport, och som är bindande, inte
mina önskemål:**
1. **V9 (Sol rad 158):** N1-b valdes ⇒ *"F är oprövad i praktiken och icke-FAIL
   vilar på källkodsargumentet"*.
2. **Citatförbudet (Sol rad 157 och 160):** `58787ce0…`, `180315a3…`,
   `feeea6b4…` får inte användas som kanoncykel- eller 99 %-belägg.
3. **Etikettkravet (Sol rad 153):** 0/20→20/20 och 20/20 topp-vid får redovisas
   **endast med etiketten** OFÖRÄNDRAT/OSÄKERT — leveransen får inte kallas en
   förbättring.
4. **CPU-förbehållet (§7.9):** tidsvärden från 14/8 och framåt är mätta under en
   konstant belastning som nu är borta.

**Ägaren heter Xerial. Använd hans begrepp, hitta inte på egna.**

---

## ÄNDRINGSLOGG — UTGÅVA 1 → UTGÅVA 2

**Grund:** oberoende adjudicering, `WORK_LOGS/2026-08-21-handoff23-adjudicering.md`
(dom: DUGLIG MED RÄTTELSER, 17 sakfel + 9 väsentliga utelämnanden).
**Genomfört 2026-08-21 kväll.** Varje rättelse är verifierad mot sin källa av mig
innan den skrevs in — adjudiceringen togs inte på tro.

### Rättelser R1–R21 införda (21 av 21)

| R | Vad som rättades | Var |
|---|---|---|
| R1 | "omobduktion ur tick-band" → banden saknades i bunten; omräkning ur härledd json + korlogg. Groks två invändningar och "nk7: mer mätning krävs" införda | §3.1 |
| R2 | "nk1–nk6 PASS" → nk4 är "0 FAIL, 2 UA2". Talet "71/71" struket som obelagt | §3.1 |
| R3 | nk7 "blockerar ingenting" → blockerar språket (Sol rad 153); V9-följdkravet infört | §3.1 |
| R4 | "SLUTDOM fr.o.m. rad 989" → rad 989 är inte sista domen; FAIL 1169 och PASS 1493 med bindande V6 | §3.1 |
| R5 | "Pappersked komplett" → restlistan L2–L11 är öppen; citatförbudet infört; "15/15" → 5/5 + 10/10 | §3.2 |
| R6 | 12/12 bands till `5981/48217` → rätt graf är **`5981/48211`** (med receptet). Kedjedefinitionen införd. `c588d3b`-frågan avgjord (ancestor, superseded) | §3.3 |
| R7 | T1h "18/8" → **15/8**, 18:25–19:26 CEST. "JÄMFÖRS ALDRIG"-förbehållet och IN ring 80,0 % införda | §3.4 |
| R8 | Fork main `58096bf` → **`249e7af`**. "+17 äldre" → 24 grenar, 18 övriga | §5 |
| R9 | dk2-inventeringen kompletterad med fyra filer; instrumentmetadatan och negativkontrollrollen införda | §4 |
| R10 | Omdömesargumenten ersatta med hårda skäl — **men bara två av tre, se avvikelse 3** | §4.3 |
| R11 | Ö12 "avarmera först" → drop-in:en är inte lokaliserad; utred först | §7.6, §8 |
| R12 | Avslutningsraden "riggen städad och verifierad" — ersatt av verifierad tabell i §7.10, **med avvikelse 1 och 2** | §7.10 |
| R13 | Lediga säten namngivna: `wN:pG`, `wS:p1`, `wN:pH` — **med avvikelse 4** | §1, §2.2 |
| R14 | Antagandet om grok-CLI:ns subagentmekanism ersatt av den verifierade herdr-vägen med kommandon | §2.1 |
| R15 | Ordern "kopiera in grok-plangranskningen" struken — redan utförd | §6, §8 |
| R16 | "S5:s kriterium är förseglat" → **ska förseglas före körning**; är det inte | §6 |
| R17 | Buntlistan kompletterad: förseglad olikhet `at_topp = dxy < 70`, inspelad `traj` för R6 | §6 |
| R18 | P0-punkten: **P0-4 och P0-5 båda öppna**; `applicera_recept.py` verifierat olagad på båda grenarna, issue #42 | §7.8 |
| R19 | De två nummerserierna märkta och separerade (planens #41/#39 vs GitHub #41/#39) | §3.5, genomgående |
| R20 | Riggstädningen — **utförd i stället för beordrad, se avvikelse 1** | §7.9, §7.10 |
| R21 | Förseglingsverktygen saknas på mergemålet; `tools/gates/` bara på `ring2quad` | §5, §8 |

### Nio väsentliga utelämnanden införda

V9-kravet (§3.1) · citatförbudet rad 157/160 (§3.2, §10) · Groks två invändningar
+ "mer mätning krävs" (§3.1) · T1h:s "JÄMFÖRS ALDRIG"-förbehåll (§3.4) · IN ring
80,0 % (§3.4) · förseglingsverktygen saknas på mergemålet (§5) · de lediga
herdr-sätena (§1, §2.2) · dk2-inventeringens fyra filer (§4) · portfrågan
(§7.10, korrigerad).

### Avvikelser — där jag INTE följde adjudiceringen, med skäl

1. **R12/R20/utelämnande 26 — "hub_publish.py håller förbjuden port 27991":
   UNDERKÄND.** `ss -tulnp | grep 27991` → tomt, och `/proc/1475/fd` innehåller
   bara stdout/stderr — processen binder ingen socket. Porten är tyst.
   Adjudiceringen slöt sig till portinnehav ur en kommandorad. Skrivet korrekt i
   §7.10 som "kvarglömd process som refererar till porten", inte som en
   åtgärdsgrind.
2. **R12/R20 — kantreg3: rättelsen var sann men är nu överspelad.** Processen är
   stoppad och arkiverad (§7.9). Adjudiceringens order "döda PID 2579913" är
   därför ersatt av det som faktiskt betyder något för dig: **konsekvensen för
   alla tidsvärden från 14/8 och framåt.**
3. **R10 skäl (2) — "§2.5:s bekräftelseled kan inte rekonstrueras": UNDERKÄND.**
   Bekräftelseledet **är utfört och ligger i materialet**: `las10-pa.jsonl` bär
   tio riggläsningar 18:53Z med 5977/48212 och `feeea6b4…`, och `riggtest10.log`
   tre `PAR-8.2-KONTROLL: PASS`. Både utgåva 1 och adjudiceringen hade fel här.
   Rekommendationen "gör om ren" står kvar men vilar nu på **två** skäl, inte
   tre. Se §4.1 — detta är utgåva 2:s viktigaste fynd.
4. **Utelämnande 24 — `wN:pH` är inte "deepseek".** `herdr agent list` ger
   agentsort **`pi`** med namnet `deepseek-v4-pro-4on4`. Rättat i §1 och §2.2;
   det spelar roll om ett kommando förutsätter en viss CLI.
5. **Utelämnande 25 — "`riggtest10.log` är sannolikt det L10-material som
   saknas": UNDERKÄND.** Filen är §2.5:s bekräftelseled. **L10 saknas
   fortfarande.**

### Nytt i utgåva 2 som inte stod i något dokument

- **§7.9** — den hängda `kantreg3`-processen, 7 dygn 5 tim på 99,9 % CPU sedan
  14/8, dess konkurrens med M1/T1h/dk1/dk2, dess stopp och arkivering, och
  konsekvensen för alla tidsjämförelser.
- **§7.10** — riggens verifierade läge vid överlämning, i tabell med kommandon,
  inklusive portkorrigeringen.
- **§4.1** — att §2.5:s bekräftelseled är utfört, med filbevis; och att
  `kontroll82-nk5.log` är en dokumenterad negativkontroll av PAR-8.2-grinden.
- **§7.1** — pitfallen om subagenters livstecken: **utdatafilens storlek är inget
  mått; slutnotifieringen är signalen.** Jag stoppade adjudiceraren av misstag i
  tron att den hängt, när den just var klar.
- **§2.3** — vägar och portar som utgåva 1 lämnade i en rollfil du ombeds sluta
  läsa.
- **§8** — bevarandefrågan och gates-portningen lyfta till åtgärdslistan.

*Utgåva 2 skriven av Fable (Claude), 2026-08-21 kväll. Samtliga rättelser
verifierade mot källa före införande; fem avvikelser mot adjudiceringen
utskrivna ovan. Fork main `249e7af`. Sista liggarrad: 160.*
