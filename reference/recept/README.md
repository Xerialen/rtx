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
| `applicera_recept.py` | appliceraren; kan också verifiera ett recept utan rigg |
| `kanon.py` | oberoende räknare för grafidentitet (nivå 1 + nivå 2) |
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

## Så appliceras de

```sh
# 1. Verifiera receptet utan att röra någon rigg (gör alltid detta först)
python3 applicera_recept.py vf5_ring2quad.json \
        --verifiera-offline dm3-full-graph.json

# 2. Se vad som skulle skickas
python3 applicera_recept.py ra_climb_planted.json --torrkor

# 3. Applicera på en levande rigg
RTX_PORT=27970 python3 applicera_recept.py \
        ra_climb_planted.json vast_296_planted.json --applicera --port 27970
```

Appliceraren räknar själv efter: länktalet **måste** ändras med exakt det antal
stegen förutsäger, annars avbryter den med `STOPP`. Ett kvitto skrivs till
`~/recept-kvitto.json` med länk-ID och celler per steg.

`--verifiera-offline` spelar upp receptet mot en grafdump och räknar fram
resulterande nivå-2 med `kanon.py`. Stämmer den med `efter.niva2_sha256` är
filen intakt och beskriver den graf den påstår sig beskriva.

### Att grinden faktiskt kan fälla

Verifieringen är negativkontrollerad 2026-08-20, inte bara sedd grön:

| prov | utfall |
|---|---|
| oförändrat vF5-recept mot riggens basdump | `MATCHAR` (`d155c22e…`), exit 0 |
| ett länk-ID ändrat 35592 → 35593 | `MATCHAR INTE`, exit 3 |
| avfartens målcell ändrad 2083 → 2072 | `MATCHAR INTE`, exit 3 |
| receptets `bas.niva2_sha256` förvanskad | `STOPP: dumpens bas matchar inte`, exit 2 |

Appliceringsvägen är körd mot referensservern samma dag och gav samma fem
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
