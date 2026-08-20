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
| `applicera_recept.py` | appliceraren; kan också verifiera ett recept utan rigg |
| `kanon.py` | oberoende räknare för grafidentitet (nivå 1 + nivå 2) |

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
