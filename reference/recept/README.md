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
| `manifest.json` | vilka recept motorn kör per karta, **och i vilken ordning** |
| `applicera_recept.py` | appliceraren; kan också verifiera ett recept utan rigg |
| `kanon.py` | oberoende räknare för grafidentitet (nivå 1 + nivå 2) |
| `negprov_offline.py` | mutationsbatteri för offlineverifieringens grindar |
| `trunkeringsprov.py` | känslighetsprov för cellresolveringen mot dumpens heltal |
| `additivprov.py` | additivregeln: ny fil minus de tillagda fälten = den certade filen |

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
