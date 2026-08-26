# HANDOFF — Fable (`wN:p1`) → Grok (`wN:pG`), 2026-08-26

Du tar över som **orkestrator och ordergivare** för buzz-4on4.
Den här filen förutsätter att du aldrig arbetat här. Läs den HELA
före första order. Din rollfil är `.claude/agents/grok-orkestratorn.md`
(finns även i rtx-repot). Chatt med ägaren överstyr allt nedan.

---

## 0. Projektet på tio rader

Ägaren (Xerial) driver en fork av QuakeWorld-motorn RTX med
navmesh-styrda bottar som ska spela 4on4 på kartan dm3. Arbetet består
av: (a) få bottarna att röra sig rätt (navmesh/motorstyrning),
(b) mäta det bevisbart, (c) skydda uppnådda resultat mot regression.
Två resultat är i hamn och får aldrig tappas: **RA-rummet** (bottarna
tar sig upp i Red Armor-rummet, ≥99 % per ben) och **ring2quad**
(12/12 hela kedjor). Båda är låsta av CI-grindar i GitHub-repot.
Det aktiva arbetet handlar om **SNG-megan**: bottarna ska ta sig till
megahealthen vid SNG från tre riktningar, ≥95 % per riktning.
Ägaren rapporteras på **svenska, ägarnivå** (mål/beslut/kostnad) —
aldrig metaaktiviteter, aldrig jargong, aldrig cellnummer i löptext.

---

## 1. Din roll och dina gränser

Du **ger order och tar rapporter**. Du dömer aldrig i sak (det gör
QA-domaren), mäter aldrig primärt (Hopparen/Navmeshdoktorn), skriver
aldrig produktionskod (Kodaren). Max ~25 % av teamtiden får vara ditt
eget handarbete.

**Du verifierar själv innan du rapporterar vidare.** Ett säte som
säger "klart" är inte klart förrän du sett kommandot och den råa
utdatan, eller kört om kontrollen själv. Det här är projektets
dyrast köpta regel — se §7.

**Ägarens beslut är ägarens:** merge, kostnad (utökade mätserier),
antal riggar, konfigurationsval, verktygstillägg. Du bereder underlag
och verkställer. Du parkerar aldrig nästa arbetsblock i väntan på
ägaren om något annat kan göras under tiden.

**Domkedjan är helig:** förseglat facit → mätning → QA-dom →
(Sol-kontrasignatur vid merge) → oberoende omräkning → ägarrapport.
Producent ≠ omräknare ≠ leverantör (kallas R8).

---

## 2. Maskiner, säten och kvoter (kontrollera FÖRE order)

| Vad | Var |
|---|---|
| Din arbetskatalog | `pinnacle:/home/xerial/dev/buzz-4on4` (**inte** ett git-repo) |
| GitHub-repo | `Xerialen/rtx`, main = **`fb5933e0`** (2026-08-26) |
| Riggen | `ssh lanister` (100.64.0.2). Allt som mäter körs där. |
| Ägarens maskin | minimain (du når den inte) |
| Hemmahubben | `http://192.168.86.34:8095/` — ägaren tittar på matcher/klipp här |

**Säten i herdr-workspacen `wN` (bara den):**

| Panel | Roll | Läge 2026-08-26 |
|---|---|---|
| `wN:pG` | **du** (orkestrator) | — |
| `wN:p1` | Fable (föregående orkestrator) | lämnar över |
| `wN:p4` | Sol (`terra-4on4`) — kontrasignatur | fungerar |
| `wN:p9` | grok-validatorsätet (oberoende omräkning) | **0 % veckokvot** |
| `wN:pB` | Qwen-forensikern | fryst tills ägar-OK |
| `wN:pH` | DeepSeek | bara vid återvändsgränd |

**Harness-subagenter** (du spawnar dem, de är inte paneler):
`kodaren`, `qa-domaren`, `hopparen`, `navmeshdoktor`, `demobyggaren`,
`general-purpose`. Rollfiler i `.claude/agents/`.

### KVOTREGEL (ny, dyrköpt 2026-08-26)
Kontrollera kvot **innan** du lägger en order på ett panelsäte:
`herdr agent read <pane>` visar raden "Weekly limit left". Grok-sätet
`wN:p9` hade 0 % och ordern gick förlorad tyst.
**Modellval för subagenter:** långa mätpass och stora granskningar ska
köras med `model: opus` — standardmodellen (Fable 5) slog i
kreditgränsen mitt i en mätning och sätet dog. Opus fungerar.
Billig grovräkning kan gå på `sonnet`.

---

## 3. Var sanningen finns (läs i denna ordning)

1. **`WORK_LOGS/ORK-INGANG.md`** — kanonisk återingång, alltid först.
   Den bär gällande läge; daterade handoffs länkas därifrån.
2. **Den här filen.**
3. `WORK_LOGS/2026-08-25-beslutsblad-164kanten.md` — det aktiva
   beslutsärendet, med alla rättelser inlagda kronologiskt.
4. `CLAUDE.md` (projektregler) + `AGENTS.grok-validator.md`
   (validatorsätets regler — inte dina, men du beställer av det).
5. `GOTCHAS.md` — enradiga fällor, **append aldrig skriv om**.
6. `GUIDES/`: `VERKTYGSGRINDAR.md` (publiceringsgrindar),
   `HANDOVER.md` (tjänster/portar på lanister), `RIG_DRIFT.md`
   (låskonvention), `ETIKETTREGLER.md` (**bindande** märkning av tal).
7. `PLANS/` — aktiva planer; `WORK_LOGS/stridsfix-liggare.md` —
   append-only bokföring, varje leverans får en rad.
8. Minnesfilerna (laddas automatiskt i sessionen): ägarregler och
   dagslägen.

---

## 4. Var vi står NU — SNG-spåret (det enda aktiva)

### 4.1 Målet och mätningen
Ägaren kräver **≥95 % rena band på alla tre riktningar** till
SNG-megan. Rocketjumps räknas inte som godkänd väg. En **bindande
designnot**: bottarna ska kunna ta 10-roxen bredvid SNG, upp för
trappan till maxhöjdplattån och över till megakrypinnet — vägen får
aldrig byggas bort, även om den inte finns i referensdemona.

### 4.2 Falldefinitionen — BESLUTAD av ägaren 2026-08-25
> **Fall = ofrivillig nedstigning på rutten** (nedstigningen finns
> inte som planerad drop-länk i försökets `rutt0`).
> **Självvald dropp** (t.ex. spawn i lifts) = **aldrig fall**.
> **Ruttavbrott utan störtning** = **egen felklass**, inte fall.

Ägaren dömde den via hubbklipp (se skillen `hubb-klipplank`).

### 4.3 Läget efter omklassning (fast, tre oberoende räkningar)
| Riktning | Rena band | Mot 95 % |
|---|---|---|
| A (spawns→mega) | 19/60 = **31,7 %** | nej |
| B (lifts/ring→mega) | 14/60 = **23,3 %** | nej |
| B2 (underlifts) | 57/57 = **100 %** | ja |

Bottarna kommer **alltid fram** (177/177). Problemet är fall på vägen.

### 4.4 Felen är tre klasser, alla vid samma kant
F1 krönhopp **59** · F2 väggträff **67** · F3 underfartshopp **29**
(= 155 äkta fall; 60 spawn-droppar friade av ägarens definition).

### 4.5 Vad som prövats och vad det gav
**S1 — zonat länkborttag (`Cmd::RemoveLinks`) mot F1.** Facit
förseglat (`ef48148d…`), drill 20 block × (3 OFF + 3 ON), n=60/arm.
Resultat, bekräftat av producent + QA + fristående omräkning:
rena band **30,0 % → 45,0 %**, F1-zonfall **77 → 36**, ankomst
59/60 i båda armar, Fisher **p = 0,1310**, KI **+15,0 pp
[−2,3; +31,1]**, styrka ≈ 32 % (~170/arm krävs för 80 %).
**QA-dom: FAIL** — facitet krävde 0 fall i den kriteriebärande
delmängden, uppmätt 23. Alla grindar höll; genomförandet är utan
anmärkning.

**Det avgörande fyndet:** länkborttag ändrar **routing**, inte
**lokomotion**. Tre motexempel ur samma mätning: cell 54 — länken dit
borttagen, 87 inträden ändå; cell 91 — de sex hopp-in-länkar facit
namngav bar **noll trafik**, och fallen bars av lämnade zoninterna
gångvägar; cell 92→91 — länk borttagen, fallen 1 → 5.
⇒ **RemoveLinks är fel verktyg mot F1.** Halveringen kan vara
omfördelning, inte bot.

**S2 — certprov för en ny hopplänk (F2-b).** **0 av 13** lyckade;
motorns hopptak ligger på +44 där +64 krävs. Alternativet är dött.

**S3 — PlanTick-serie för F3.** Kördes, men mätte **fel population**
(målcellen hade noll telemetrirader). F3-orsaken är **UNKNOWN**.

**Kantvaktsspåret (`lip` / `ledge_ahead`) — STÄNGT.** `lip` är ingen
kantvakt (sitter i en OR, kan bara öppna hoppgrinden). `ledge_ahead`
är strukturellt död i zonen (kräver hoppfas Off — sant i 0 av 2367
rader). Hypotesen att hoppdämpning skulle väcka den **underkändes**
av QA: samma handling armerar en gångplanerare som står ned båda
bromsarna (mutationsprov: 0,21 % som övre gräns).

### 4.6 Ägarens öppna vägval (ligger hos honom NU)
| # | Väg | Kostnad | Risk | Evidens |
|---|---|---|---|---|
| **B** | F2 `styre_sjband` (gropkanten, 67 fall) | medel | zonbar | **enda licensierade åtgärdsklassen** |
| **A′** | Utred gångplaneraren i krönzonen (~89 band) | hög riggtid | ingen (mätning) | enda vägen till F1:s orsak |
| **C** | Nollalternativ | ingen | ingen | — |
| **D** | Bevisdrill 170/arm på S1-vinsten | hög | ingen | avråds |

Fables rekommendation var **B nu**; F1 står mellan A′ och C.
**Bygg ingenting förrän ägaren valt.**

### 4.7 Övriga öppna ägarbeslut (äldre, obesvarade)
- **F2-a** (zonerat ansatskrav) ja/nej — enda kvarvarande aktiva
  F2-vägen efter certfallet.
- **Ratificering av `testsuite/tools/mutationsprov_dom.py`** —
  Kodaren la till verktyget utan godkännande; QA fällde det på
  verktygsregeln men konstaterade att det är inert. Behåll/ta bort.
- **Fem oredovisade portar** (27590/27595/27981/28501/28505) —
  klassbeslut till portvalvet.
- **Installation av parallellrigg-skillen** i `.claude/skills/`.
- **2000-raderstaket i `predikat_v2_hoppa.py`** — att höja det ändrar
  ett main-pinnat mätpredikat och kräver etikettbeslut.
- **Mätinstrumenten i lådorna på lanister är inte versionshanterade** —
  samma risk som `docs/AGENT-PREREQS.md` varnar för.

---

## 5. Praktik du måste kunna (annars går det fel)

**Försegla facit:** endast via D-lådan på riggen —
`ssh lanister 'cd ~/rtx-toolbox-d && bash tools/gates/forsegla_facit.sh <fil>'`.
Repots kopia av linten saknar beroenden (`d_failclosed`, `d_recipe`)
och kan inte köras på pinnacle. Hämta hem den förseglade filen +
`.sha256`, verifiera samma sha på båda sidor, lägg körexemplar på
riggen. **Ingen mätning startar mot oförseglat facit.**

**Portar:** `docs/PORTAR.md` i repot är enda sanningen. Läs den med
`testsuite/rig/portar.py` — hårdkoda aldrig portnummer.
Självallokering är förbjuden.

**Riggslås:** `~/lab/.rig-lock` — **fri betyder att filen inte finns**
(0-byte-konventionen är avskaffad). Triolås per lab-trio tas atomiskt
(O_EXCL). **Låshållaren måste vara en systemd `--user`-unit** — en
`setsid`-process ur ssh dör med sessionen och lämnar ett övergivet
lås (hände 2026-08-26). Dött ägar-PID = övergivet lås, får tas om
med bokföring i `~/lab/riggar/REGISTER.md`.

**Buntar (bevispaket):** `lanister:~/hopptraning/<id>-grokbunt/`.
`SHA256SUMS` sist, WORK_LOGS aldrig inuti, sätt **0444 direkt vid
bygge**. **`__pycache__` får aldrig ingå i SUMS** — interpretern
skriver om .pyc och skapar falska integritetsavvikelser som en riktig
manipulation kan gömma sig i.

**Skalregel (bruten fyra gånger, kostar tid varje gång):** aldrig
ssh-heredocs med citattecken. Skriv skriptet lokalt, `scp`, kör.
Skärp det i varje order.

**Publicering till ägaren:** ladda skillen `skarmdumpsvalidering`
FÖRE leverans. Ägaren läser på telefon; validera i mobilformat,
testklicka varje utlovad väg, ögonkontrollera varje bild.
**claude.ai-artefakter tar aldrig emot URL-parametrar** — bygg
startpaneler i sidan i stället. Vill ägaren se ett specifikt
ögonblick: använd skillen `hubb-klipplank` (en hubblänk per case).

---

## 6. Ägarens stående regler (bindande)

1. **Smala åtgärder.** Undvik ändringar med hög risk att påverka
   bottens förmåga i stort. Zonat framför global policy, flagga
   default av, bred regression (RA + ring2quad + strid + T3) som
   beviskrav, global risknivå redovisad per alternativ, alltid ett
   nollalternativ.
2. **Verifiera före påstående.** Aldrig "klart"/"PASS"/en
   förbättringssiffra utan att du själv kört om kontrollen och kan
   visa kommandot och rå utdata. n=1 märks provisoriskt.
3. **Grindar är misstänkta tills de fällt något.** Negativkontrollera
   varje instrument mot känd-dålig input innan du litar på grönt.
4. **Avvikelser rapporteras i samma stund de upptäcks** — även dina
   egna fel, särskilt om de redan gått till ägaren.
5. **Verktyg:** inga läggs till eller tas bort ur repot utan ägarens
   godkännande.
6. **Rapportformat:** (1) verifierat klart med evidens, (2) pågående
   med blockerare, (3) **en** completion-siffra avstämd mot förra
   rapporten, (4) det du själv bedömer som undermåligt.
7. **Ägarens begrepp, inte egna.** Använd hans ord (hemmahubben,
   megakrypinnet, rox-vägen). Hitta aldrig på egna namn.
8. **Inga ceremonier.** En dom per sak, led i följd i en order,
   motpröva tal och kod — inte processen för dess egen skull.

---

## 7. Läxor från dagens arbete (de dyra)

- **Fable rapporterade tre gånger fel till ägaren** och fick rätta:
  ett antal som var bandnummer, en andel som var felräknad, och ett
  samband som fördes vidare innan QA hunnit döma. Vänta in domen
  eller märk tydligt att den saknas.
- **Ett säte dog på kreditbrist mitt i en mätning.** Riggen klarade
  sig eftersom drivern var fail-closed. Kör tunga pass på opus.
- **Motorn har egna säkerhetsvakter.** Den vägrade ta bort länkar som
  skulle lämna en väg utan återväg (`control.rs::envag_nyfalla`).
  Läs vaktregeln ur källan och spegla den offline **innan** en lista
  skickas till riggen.
- **Mät rätt population.** S3-serien uppfyllde sitt eget kriterium
  (41 passager) men mätte fel felklass. Förregistrera vad som räknas.
- **Ett instrument som kan gissa kommer att gissa.** Fable beslutade:
  kriterieinstrument kör fail-closed; bevisat återvinningsbar data
  redovisas som separat märkt sekundärvy, aldrig i rubriken.

---

## 8. Hårda nej

- Ingen live-koppling till RA-kontrollporten `:27990` eller main-test.
- `rtx_bot_count 0` i cfg: aldrig.
- Rör aldrig frysgrenen `grokork` @ `9d015db` som arbetsgren.
- Rör aldrig originalen i en bunt; kopiera till egen katalog.
- Slå aldrig på plan-telemetri mot en konsument som inte känner
  `PlanTick` (den deployade navviewern dör på okänd eventvariant).
- Publicera aldrig tal från olika grafer i samma tabellcell —
  armarna i en on/off-drill har olika graf och kräver etikett.
- Bygg ingen ny visning; öppna de befintliga (`navmesh-sight`).

---

## 9. Första fem stegen när du tar över

1. Läs `WORK_LOGS/ORK-INGANG.md` och den här filen.
2. Verifiera läget själv: `gh api repos/Xerialen/rtx/commits/main --jq .sha`
   (ska vara `fb5933e0…` eller senare), riggen fri
   (`ssh lanister 'ls ~/lab/.rig-lock'` ska ge "no such file"),
   inga aktiva units.
3. Kontrollera kvoter på de säten du tänker använda (§2).
4. Kvittera till ägaren att du tagit över, på svenska, ägarnivå —
   och lägg fram hans öppna vägval (§4.6) utan att välja åt honom.
5. Starta ingenting nytt i SNG-spåret förrän han valt. Backloggen i
   §4.7 kan beredas under tiden.

---

*Skriven av Fable (`wN:p1`) 2026-08-26. Granskad av oberoende
subagent enligt ägarorder — se `2026-08-26-granskning-handoff-grok.md`.*
