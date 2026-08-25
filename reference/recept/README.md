# reference/recept — versionerade navmesh-recept

Ett **recept** är en namngiven, ordnad följd av ingrepp i en karts navmesh:
planterade länkar och borttagna länkar. Det är inte kod och inte en patch mot
motorn — det är **riggdata**. En rigg som kör ett recept beter sig annorlunda än
en rigg som inte gör det, och den skillnaden är osynlig i binärens sha256.

Därav husregeln längst ned: **recept ska deklareras i evidensbundlar.**

---

## Vad som ligger här

| fil | vad |
|---|---|
| `ra_climb_planted.json` | K2, del 1 — RA-rummets uppväg, fyra plana hopp P1–P4 |
| `vast_296_planted.json` | K2, del 2 — västhyllans V296-länk |
| `vf5_ring2quad.json` | vF5 — ringkanten över gapet till quad (F-serien) |
| `vf5_ring2quad_forkmain.json` | samma vF5-ingrepp, omhärlett mot **fork mains** grafidentitet |
| `manifest.json` | vilka recept motorn kör per karta, **och i vilken ordning** |
| `applicera_recept.py` | appliceraren; kan också verifiera ett recept utan rigg |
| `kanon.py` | oberoende räknare för grafidentitet (nivå 1 + nivå 2) |
| `negprov_offline.py` | mutationsbatteri för offlineverifieringens grindar |
| `trunkeringsprov.py` | känslighetsprov för cellresolveringen mot dumpens heltal |
| `additivprov.py` | additivregeln: ny fil minus de tillagda fälten = den certade filen |
| `omharled_forkmain.py` | flyttar ett stegrecept mellan två grafidentiteter, geometriskt |
| `forkbas_kalla.py` | belägget för vilken graf fork-basdumpen beskriver |
| `kvitto_forkbasbindningen.sh` | portningskvittot + dess negativkontroller |

De två `*_planted.json` är **planteringskörningarnas egna sparade facit** — de
skrevs av `plant_ra_climb.py` respektive västplanteringen när stegen certades.
De är inte återskapade i efterhand.

---

## K2 = `ra_climb_planted.json` + `vast_296_planted.json`

Rent additivt: fem planterade länkar, ingenting tas bort.

### Vilka länkar och celler

| steg | cell → cell | avfart | v_req | gain |
|---|---|---|---|---|
| P1 z=60 | 1456 → 1590 | [488,3 · −777,7 · 56] | 490,0 | 6,0 |
| P2 z=155 | 1275 → 1090 | [62,0 · −562,6 · 152] | 449,3 | 12,0 |
| P3 z=267 | 1312 → 1429 | [352,1 · −809,4 · 264] | 464,0 | 8,0 |
| P4 z=331 | 1139 → 1214 | [8,0 · −585,5 · 328] | 380,0 | 5,5 |
| V296 västhyllan | 1167 → 1191 | [92,0 · −588,0 · 296] | 320,0 | 5,5 |

Cellnumren ovan är uppmätta på main (`cc5fa8e`, dm3, 5977 celler / 48207
länkar). De tre parametrarna per steg är inte gissade: `v_req` ligger vid eller
strax över uppmätt läppfart, `shift` kompenserar att motorn hoppar upp till
`bhop::LIP_REACH = 28 u` **före** den planterade avfartslinjen, och `gain`
sänktes från motorns default 12 som är för hård för de här hoppen. Underlaget är
ägarens egen klättersekvens 2026-08-10.

### Vad som certar det

- **K2-baslinjen 2026-08-15**, godkänd 4/4 (commit `86f7f11`): IN-sidan
  **30/30**, noll fall, noll stall — IN väst 8,74 · IN ring 6,38 · IN tunnel 7,68.
- **Arm 3, 2026-08-20**: 20 IN-ring-försök på referensservern med receptet
  applicerat → **20/20 topp-vid**, **noll** som inte kom upp, median **5,38 s**
  (mot 5,60 för bar main och 5,70 för hoppriggen). Detaljer i
  `WORK_LOGS/2026-08-19-hopptraning-ring2quad.md` §33.

**Avgränsning som måste följa med siffran:** receptet gör klättringen stabil,
men det öppnar **inte** kanonens 70 u-toppdisk. Arm 3 stod 0/20 mot disken, och
alla lyckade klättringar stannar 87–101 u från toppcentrum. K2-baslinjens
"IN ring 10/10 på 6,38" mättes på arm A:s **egen binär** (`1cc87180615f`) —
förmågan att landa innanför 70 u satt i den grenens kod, inte i receptet.

---

## vF5 = `vf5_ring2quad.json`

Ett **stegrecept**: en plantering följd av sju borttagningar, i den ordningen.

### Vad det gör

1. **Planterar en läppinnad sydavfart** — cell 1450 → 2083, avfart
   [454,7 · 153,3 · 56], v_req 419,33, gain 6,0.
   Motorn utlöser språnget när avståndet till avfartslinjen längs ansatsaxeln
   understiger `LIP_REACH = 28 u`. Sydmissarna låg på 27,1 och 27,65 — knappt
   innanför, så de lyfte för tidigt. Linjen är flyttad 12 u framåt längs samma
   axel (−45°); missarnas punkt hamnar då utanför tröskeln medan träffens punkt
   fortfarande ligger innanför. Punkten ligger inne i cell 1714:s fotavtryck.

2. **Tar bort sju länkar**

   | id | cell → cell | varför |
   |---|---|---|
   | 34501 | 1701 → 2072 | oflygbar korsning |
   | 34503 | 1712 → 2083 | oflygbar korsning |
   | 35683 | 1691 → 1925 | gropdykare, landar på z = −194 |
   | 35761 | 1233 → 2177 | gropdykare, landar på z = −200 |
   | 35762 | 1246 → 2177 | gropdykare, landar på z = −200 |
   | 35592 | 1691 → 1617 | drar rutten in i den dåliga korridoren |
   | 35738 | 1450 → 2083 | originalavfarten, ersatt av tvillingen ovan |

### Sidoeffekten som är avsiktlig

Motorns `remove_links_by_id` nollar adjacensen och `push_link`:ar varje behållen
länk. Följden är att **alla prunade länkar återupplivas** — inklusive dm3:s enda
teleportlänk, som `prune_links_through_teleports` annars klipper. Utan den kan
A\* aldrig planera genom en teleportör.

Det är inte en lycklig slump utan pinnad semantik: testet
`control::tests::komponat_remove_links_resurrects_pruned_links_like_the_recipes_do`
låser beteendet. **Riv inte den grinden** — vF5 hänger på den.

### Vad som certar det

Hopp 1 över gapet **35/36** över tre vinklar (syd, nord, ringitemet), varav två
rena 12/12-varv i följd · hopp 2 ut genom teleporten **12/12** · hopp 3
ringitemet → quad **12/12** · tillbakahoppet **12/12** utan ingrepp ·
**helkedjan 12/12** hela kedjor på 7,2–8,9 s. RA-regressionen mot kanonen
(`91a6e34`) i §30–§31 av samma arbetslogg.

### Grafidentitet — läs detta innan du applicerar

vF5 är bundet till **en** grafidentitet:

```
bas   5981 celler / 48217 länkar   nivå-2  4c099331899d7aae…
efter 5981 celler / 48211 länkar   nivå-2  d155c22ebf8a6536…
```

Basen är den graf som **lokal main `4f0b910`** bygger för dm3 — inte den som
`cc5fa8e` bygger (5977 / 48207). Länk-ID kompakteras när länkar tas bort, och
en annan motorversion bygger en annan graf. **Applicera aldrig ett stegrecept på
en rigg vars bas inte matchar `bas.niva2_sha256`.**

---

## vF5 omhärlett till fork main = `vf5_ring2quad_forkmain.json`

`vf5_ring2quad.json` kan inte appliceras på ett rent bygge ur fork main: dess
bas är en annan graf. Den här filen är **samma ingrepp** — samma plantering,
samma sju borttagningar — omräknat mot fork mains grafidentitet, så
ring2quad-kedjan kan sluttestas där.

### Vilken generation som omhärleddes

**vF5 med 12 u läppinning, avfart [454,7 · 153,3]** — den generation som gav två
rena 12/12-varv i följd (vF5b, vF5c) och helkedjan 12/12. Avfartskoordinaten är
identitetsbäraren: nivå-2 kan **inte** skilja vF3, vF4 och vF5 åt (alla tre ger
`d155c22e…`, se arbetsloggens §25.4 — avfartspunkten bor i sidotabellen och
hashas inte). vF4:s läpp låg på [450,4 · 157,6] och vF3 hade ingen planterad
tvilling alls.

### Hur den räknades om

Id:n kopieras aldrig rakt av — ankaret är geometrin (`fran_pos`/`mal_pos` per
borttagning, världskoordinater per plantering). `omharled_forkmain.py` gör två
led: först en positiv kontroll att varje id i källreceptet pekar på exakt den
länk receptet påstår i källgrafen, sedan uppslag på samma
(fran_pos, mal_pos, kind) i målgrafen med krav på **exakt en** träff.

**Mätt utfall:** fork-basens celler 0–5976 och länkar 0–48206 är identiska med
vF5-basens prefix — samma koordinater, samma `from/to/kind/T`, samma `cell_ids`
och `link_ids`. vF5-basen är fork-basen plus fyra celler (5977–5980, kring
[−9xx · −48 · 88]) och tio länkar (id 48207–48216) **påklistrade sist**.
Omhärledningen blir därför identitetsavbildningen på alla sju länk-id och båda
cellnumren. Det som skiljer är grafidentiteten, inte bindningarna — och just
därför måste hasharna bytas: ett recept som deklarerar fel bas blir aldrig
applicerat.

```
bas   5977 celler / 48207 länkar   nivå-2  58787ce0d27ddd49…   PRELIMINÄR
efter 5977 celler / 48201 länkar   nivå-2  dcb487f79abdd415…   PRELIMINÄR
```

### Varför båda värdena är märkta PRELIMINÄRA

De är framräknade **offline** ur en grafdump, inte lästa ur en levande rigg på
fork main. Fork mains kontrollkanal saknar `out_pruned` och kan bara leverera
adjacensen (48192 av 48207 länkar), så nivå-2 går inte att mäta där förrän
`graph_content_hash` är portad. Facit `facit-receptautostart-v2` §8.3 kräver att
basvärdet **mäts på riggen och förseglas som addendum före första dömande
körning** — den här filen får inte användas för att fylla i det värdet i
efterhand. Den är kandidaten som mätningen ska bekräfta eller fälla.

### Källan till fork-basens graf

| | |
|---|---|
| komplett dump | `lanister:~/lab/toolbox/dm3-base-full-graph.json`, sha256 `a04c7ada…ef6a` |
| tagen på | toolbox/b-planner-telemetry, `nav_patch` **av**, ctl :27995 — **inte** fork main |
| motpart | `lanister:~/hopptraning/graf/mainref-live-graph.json`, sha256 `2ae8ccfd…9836`, ctl :27970 på `rtx-mainref` @ `cc5fa8e` |

Att den kompletta dumpen ändå beskriver fork mains dm3-default är **mätt, inte
antaget**: `forkbas_kalla.py` visar att cellerna och `cell_ids` är identiska, och
att dumpens 48192 T=1-länkar är exakt fork mains länkar i samma ordning med
samma `link_ids`. Det fork main inte kan leverera — de 15 prunade länkarna,
inklusive teleportlänken `36314` (4633 → 1330) — är precis det den kompletta
dumpen bidrar med.

### Portningskvittot: **fork-basbindningen**

Kvittot är inte "antal gröna". Det är en namngiven kontroll av *en* sak: att det
omhärledda receptet binder mot fork-basens nivå-2 och mot ingen annan graf.

```sh
bash kvitto_forkbasbindningen.sh          # K1 + N1–N6
```

| prov | utfall |
|---|---|
| **K1** omhärlett recept mot fork-basen | `bas: matchar` → härleder `dcb487f7…`, **MATCHAR**, exit 0 |
| N1 omhärlett recept mot vF5-basen | `STOPP: dumpens bas matchar inte`, exit 2 |
| N2 originalreceptet mot fork-basen | `STOPP: dumpens bas matchar inte`, exit 2 |
| N3 ett länk-id ändrat 35592 → 35593 | `MATCHAR INTE`, exit 3 |
| N4 avfartens målcell ändrad 2083 → 2072 | `MATCHAR INTE`, exit 3 |
| N5 basens nivå-2 förvanskad | `STOPP`, exit 2 |
| N6 efter-hashen satt till lokala mains `d155c22e` | `MATCHAR INTE`, exit 3 |

N1 och N2 är korsprovet som ger kvittot tänder: de två recepten är **inte**
utbytbara, och vart och ett vägras av den andras graf.

### Vad som INTE är omhärlett

Utfallssiffrorna. 35/36, 12/12 och helkedjan är mätta på vF5-basen med lokal
mains binär. De får inte citeras som fork mains utfall — den mätningen återstår.

---

## `manifest.json` — vilka recept som körs, och i vilken ordning

```json
{ "schema": "rtx-recept-manifest/1",
  "kartor": { "dm3": [ {"fil": "ra_climb_planted.json", "ordning": 1},
                       {"fil": "vast_296_planted.json", "ordning": 2} ] } }
```

Motorns receptautostart läser **manifestet**, inte katalogen. Skälet är att
ordningen är betydelsebärande i allmänhet: `PlanLink` resolverar `from`/`tgt`
genom `nearest()`, så ett recept som körs efter ett annat *kan* resolvera
annorlunda. Katalogordning är filsystemets godtycke; manifestet är ett beslut.
En kvarglömd fil i katalogen blir därmed en **no-op**, inte en tyst grafändring.

`vf5_ring2quad.json` står **medvetet utanför** manifestet: det är ett stegrecept
med `RemoveLinks` (etapp 2), motorns `recept.rs` vägrar `op != PlanLink`, och
dess bas är en annan grafidentitet (5981 / 48217).

*(För just K2:s två filer ändrar ordningen ingenting i utfallet — inga celler
tillkommer, så alla fem `nearest()`-svar är desamma oavsett ordning. Ordningen
står ändå som ett beslut, eftersom nästa recept kan vara ett som planterar celler.)*

---

## Så appliceras de

```sh
# 1. Verifiera receptkedjan utan att röra någon rigg (gör alltid detta först).
#    Flera filer = EN kedja, i manifestordning.
python3 applicera_recept.py ra_climb_planted.json vast_296_planted.json \
        --verifiera-offline dm3-base-full-graph.json

# 2. Se vad som skulle skickas
python3 applicera_recept.py ra_climb_planted.json --torrkor

# 3. Applicera på en levande rigg
RTX_PORT=27970 python3 applicera_recept.py \
        ra_climb_planted.json vast_296_planted.json --applicera --port 27970
```

Appliceraren räknar själv efter: länktalet **måste** ändras med exakt det antal
stegen förutsäger, annars avbryter den med `STOPP`. Ett kvitto skrivs till
`~/recept-kvitto.json` med länk-ID och celler per steg.

`--verifiera-offline` spelar upp kedjan mot en grafdump och räknar fram nivå-2
med `kanon.py` efter **varje** fil. Stämmer varje fils `efter.niva2_sha256` är
filerna intakta och beskriver de grafer de påstår sig beskriva.

`vast_296_planted.json`s `efter` är kedjans slutläge (48 212 länkar) och nås
bara i manifestordning — filen ensam mot basdumpen ger 48 208 och `MATCHAR
INTE`, vilket är rätt: den är inte skriven för att köras ensam.

### Fyra grindar, och varför de ser ut som de gör

*(Stramade 2026-08-21 efter QA-domen — `WORK_LOGS/qa-dom-receptautostart-design.md`,
avgjorda i `WORK_LOGS/facit-receptautostart-v2-addendum3.md`.)*

1. **`bas` och `efter` kräver full 64-teckens hex och exakt likhet.** Grinden var
   en prefixgrind, och åtta hextecken räckte för att passera den. En förkortad
   konstant är nu ogiltig indata, inte "nästan rätt".
2. **Cellparen prövas geometriskt.** Bär ett planteringssteg `fran_cell`/`mal_cell`
   resolveras cellerna *ändå* ur dumpen med en port av `NavGraph::nearest`, och
   de två måste stämma. Det gör de fem certade cellparen maskinellt prövbara
   utan rigg.
3. **Rutnätssvaret dubbelkollas mot en rak genomsökning** av alla celler. Skiljer
   de sig är det `STOPP` — dumpens koordinater är heltalstrunkerade, och den
   frågan ska synas, inte gissas. (`trunkeringsprov.py` räknar marginalerna.)
4. **Varje fils `efter` prövas efter just den filens steg.** Motorn läste förut
   bara `recept.last()`, så en kedja där bara den första filen bar `efter`
   jämförde ingenting alls. Sista filens `efter` är därmed hela kedjans
   slutläge — design v2 §4.4 som specialfall — och facit §2 punkt 3, `bas`
   **och** `efter` i **båda** receptfilerna, går att uppfylla.

**Följd att känna till:** `vf5_ring2quad.json` bär ännu en 8-teckens
`efter.niva2_sha256` (`d155c22e`) och avvisas därför nu av grind 1 med
`STOPP: ogiltig efter-konstant`. Det receptet är etapp 2 och byggs inte nu; dess
fulla värde ska härledas ur en vF5-basdump när etappen tas upp.

### Att grinderna faktiskt kan fälla

`negprov_offline.py` kör hela batteriet och skriver ut vad varje mutation gav.
Utfall 2026-08-21, **10 av 10**:

| prov | utfall |
|---|---|
| oförändrad kedja | `MATCHAR`, exit 0 |
| `fran_cell` 1456 → 1457 | `STOPP: … geometrin resolverar 1456`, exit 2 |
| `bas` trunkerad till 8 hextecken | `STOPP: ogiltig bas-konstant`, exit 2 |
| `bas` full längd men fel | `STOPP: dumpens bas matchar inte`, exit 2 |
| `efter` trunkerad till 8 hextecken | `STOPP: ogiltig efter-konstant`, exit 2 |
| `efter` full längd men fel | `MATCHAR INTE`, exit 3 |
| kedjans sluthash lagd i FÖRSTA filen | `MATCHAR INTE`, exit 3 (bryter mellan filerna) |
| `efter` borttaget ur sista filen | `VARNING: … SLUTLÄGE … oprövat`, exit 1 |
| en länk i dumpen ändrad | `STOPP: dumpens bas matchar inte`, exit 2 |
| plantering flyttad till annan målcell | `MATCHAR INTE`, exit 3 |

Det äldre batteriet 2026-08-20 mot vF5-receptet (länk-ID 35592 → 35593 och
målcell 2083 → 2072, båda `MATCHAR INTE`, exit 3) står kvar som historik.

Appliceringsvägen är körd mot referensservern 2026-08-20 och gav samma fem
länkar och samma celler som arm 3:s ursprungliga plantering.

### Återställning

Det finns ingen undo-väg i det här skriptet. Ett recept tas bort genom att
**starta om servern** — navmeshen byggs då från kartan igen. Verifiera alltid
efteråt att cell- och länktalen är tillbaka på basvärdena.

---

## Recept är riggdata — deklarera dem

Två riggar med **identisk** binär-sha256 kan bete sig helt olika om den ena kör
ett recept. Ett mätvärde utan receptdeklaration är därför inte reproducerbart.

Varje evidensbundel ska ange, per rigg:

- **vilket recept** som var applicerat (filnamn + commit-SHA här i trädet), eller
  uttryckligen `inget recept`
- **grafidentiteten vid mättillfället** — celler, länkar och nivå-2-hash, läst ur
  den levande riggen, inte antagen
- **binärens sha256**

En jämförelse mellan två riggar som skiljer sig i *både* recept och binär bär två
skillnader samtidigt och kan inte tillskriva utfallet någondera. Säg det rakt ut
i bundeln när det är fallet.

---

## Vad denna gren INTE är

Grenen versionerar recepten. Den **ändrar ingen motorkod** och innehåller ingen
patch. Sammanslagning är ägarbeslut. Ingen PR går någonsin uppströms.
