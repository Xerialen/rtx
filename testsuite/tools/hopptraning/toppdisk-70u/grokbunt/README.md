# 70u-toppdisken — mätbunt för oberoende validering

Buntens fråga, ordagrant ur `WORK_LOGS/2026-08-20-70u-toppdisk-fynd.md` §6:

> 1. Dumpa grafen ur ett `4db5b19`-bygge och kontrollera om **länk 8992** (eller
>    någon `walk` in i 70u-disken) finns. Finns den inte är det ett
>    **meshbygge**-fynd; finns den är det ett **styrnings/kostnads**-fynd.
> 2. Kör kanonens IN ring på `4db5b19` + K2-receptet och läs botens rutt vid
>    cell 1216: **planeras** gånglänken in i disken och avbryts, eller planeras
>    den aldrig? Det skiljer router från styrning.

**Mitt svar är att det är varken–eller.** Underlaget ligger här i sin helhet så
domen kan prövas oberoende.

---

## 1. Proveniens

| | |
|---|---|
| rigg | `RA-DRILL-MAIN-REF`, styrkanal 27970, spel 27570 |
| binär | `qwprogs.so` sha256 **`27b493b6f5e1d1486cb24308ab089d396b54e59d6edc2d0b8db3ad0291186ce0`** |
| kod | fork `cc5fa8e` — **kodidentisk med `4db5b19`** (`4db5b19` lägger bara till `reference/recept/`, ingen motorkod) |
| graf, default | 5977 celler / 48207 länkar |
| graf, med K2 | 5977 celler / **48212** länkar |
| kartan | dm3 |
| kördes | 2026-08-20 kl. 20:16–20:32 CEST |
| servern släcktes | 20:35:26 CEST — **efter** mätningen |

**Receptet är bevisligen samma som A-armen körde.** `data/ra_climb_planted.json`
har sha256 `42f49e6c798cd3ee…`, vilket är exakt den hash fyndets §1 anger för
A-armens egen planteringsfil. Ingen omtolkning, ingen ny parameter.

Alla filhashar i `PROVENIENS.txt`.

---

## 2. Steg 1 — finns gånglänken i dagens mesh?

**Metod.** `~/rtx-tools/mkgraph.py --port 27970` läser den **levande** navmeshen
cell för cell över styrkanalen och skriver serverns egna cell- och länk-id.
Dumpen tar cellernas `out`, alltså **adjacensen** — det routern faktiskt kan
använda. 48192 länkar mot statusens 48207: differensen är de 15 avsiktligt
prunade, som per konstruktion ligger utanför adjacensen.

**Kör om:** `python3 skript/disk70.py data/mainref-live-graph.json`
(gzippad — packa upp först).

**Utfall.** Disken definierad som `z ≥ 320 ∧ dxy([250,−703]) < 70`:

| | |
|---|---|
| celler innanför 70 u | **21** |
| närmast centrum | cell **1372** [256 · −704 · 328], **6,1 u** ut |
| västkanten | cell **1216** [160 · −704 · 328] = **90,0 u** · cell **1218** = 92,0 u |
| inkommande länkar utifrån | **19 — samtliga `walk`. Noll luftburna.** |
| **länk 8992** | **FINNS.** `1216 → 1265`, `walk`, [160,−704,328] → [194,−733,328]. Målet ligger **63,5 u** från centrum, alltså innanför disken. |

Identiskt med §3:s siffror på 11 augusti-dumpen. **Inget meshbyggefynd.**

---

## 3. Steg 2 — planeras länken?

**Metod.** K2 applicerat (kvitto i `data/k2-recept-kvitto.json`: +5 länkar,
cellerna 1456→1590, 1275→1090, 1312→1429, 1139→1214, 1167→1191). Åtta
IN-ring-försök från kanonens startpunkt [449 · −338 · 56] mot toppen
[256 · −704 · 328]; botens **levande plan** avläst med `Route` under gången.

**Kör om:** `python3 skript/rutt1216.py 8` → `data/rutt1216.json`.

**Utfall — 8 av 8 hade en disklänk i planen.** Först `8992` vid t ≈ 3,4 s, sedan
`8993` vid t ≈ 4,9 s: planeraren väljer disken och byter till och med
ingångslänk under vägen. Sista fullständiga planen i försök 1:

```
9038 step 1222→1197 · 8844 step 1197→1167 · 8649 jump 1167→1193
8811 walk 1193→1216 · 8993 walk 1216→1267 · 9325 walk 1267→1319
```

**8 av 8 stannade ändå utanför**, på cell 1216 eller 1218 — och varje avläsning
medan boten stod där gav **tom plan**.

Så långt ser det ut som "planeras och avbryts". Det håller inte.

---

## 4. Det avgörande provet — steget isolerat

Föregående mätning blandar två saker: klättringen dit **och** steget in. Här är
de skilda. Boten teleporteras **rakt till cell 1216**, ingen klättring alls.

**Kör om:** `python3 skript/fran1216.py 3` → `data/fran1216.json`.

| läge | utfall |
|---|---|
| `Goto` till toppen | **3 av 3 inne i disken efter 0,61 s** |
| ingen order (botens eget mål) | 0 av 3 — går åt annat håll; dess eget mål ligger inte där |

Planen i `Goto`-läget, identisk alla tre gångerna:

```
8993 walk 1216→1267 · 9325 walk 1267→1319 · 9732 walk 1319→1372
```

— rakt in till cell 1372, 6,1 u från centrum.

**Routern klarar steget. Styrningen utför det. På 0,61 sekunder.**

---

## 5. Domen

- **Inte meshbygge** — länken finns (§2).
- **Inte routern** — från 1216 planeras *och* går boten in, 3/3 (§4).
- **Inte "planeras och avbryts vid läppen"** — steget avbryts inte, det påbörjas
  aldrig, för planen är redan tom när boten står där.

Spärren i fyndets §5 är alltså hävd åt två håll. **Ingen patch går att formulera
på det här underlaget.**

### 5.1 RÄTTELSE — höjdslutsatsen är indragen

Denna buntens första utgåva bar en tabell med "dz = 22–43 u över cellens golv"
och slutsatsen att felet låg i **klättringens sista landning**. Grok räknade om
mot rundata och fällde den. **Omräkningen är riktig och slutsatsen är
indragen.** Vad som faktiskt står i data:

**Stoppositionerna i `rutt1216.json` — samtliga åtta:**

| nr | slut | cell | dz mot cellgolvet (z=328) |
|---|---|---|---|
| 1 | [149 · −700 · 328] | 1216 | **0** |
| 2 | [153 · −701 · 328] | 1216 | **0** |
| 3 | [157 · −691 · 328] | 1218 | **0** |
| 4 | [153 · −700 · 328] | 1216 | **0** |
| 5 | [153 · −700 · 328] | 1216 | **0** |
| 6 | [153 · −700 · 328] | 1216 | **0** |
| 7 | [152 · −701 · 328] | 1216 | **0** |
| 8 | [152 · −699 · 356] | 1216 | 28 |

**Sju av åtta står på golvet.** Inte 22–43 u över det.

**Två av den gamla tabellens sex rader fanns i `rutt1216.json`; fyra gjorde
det inte.** De togs från *andra* körningars stoppositioner (n20-armarna i
hoppträningsloggens §32–§33) och redovisades under fel rubrik — i något fall
dessutom oexakt avskrivna. Det var mitt fel: jag valde en handplockad lista
punkter att fråga `Cell` om, och presenterade svaret som om det beskrev
`rutt1216.json`. Cellidentiteterna i de svaren var riktiga; **kopplingen till
den här mätningen var det inte.**

**Slutsatsen motbevisas dessutom av §4.** Isoleringen startade inte på
`dz ≈ 2` som README påstod. Botens faktiska position vid första avläsningen var
**z = 352, 354 respektive 356** — mitt i det höjdband som slutsatsen utpekade
som problemet — och därifrån gick den in i disken på 0,61 s, alla tre gångerna:

```
t=0,04  [160 · −704 · 352]  cell 1216
t=0,33  [168 · −704 · 371]  cell 1216
t=0,61  [176 · −704 · 335]  cell 1267   <- inne i disken
```

Höjden är alltså inte hindret. Ingenting i bunten pekar längre på
klättringens landning.

### 5.2 Vad som står kvar

Att planen **är tom** när boten står på västkanten står oemotsagt — det är mätt i
alla åtta försöken. Frågan är **varför**, och den är obesvarad.

Tidsfönstret är känt: `8993` syns i planen vid **t ≈ 4,9 s**, och vid
**t ≈ 6,6–6,9 s** står boten stilla på 1216 med tom plan. Vad som händer i de
dryga två sekunderna däremellan är nästa mätning.

---

## 6. Vad jag INTE har fastställt

**Varför den levande planen töms** mellan t ≈ 4,9 s och t ≈ 6,6 s. Öppet.
Kandidaterna jag hade skrivit upp här bygger båda på höjdhypotesen och är
**avförda** med den (§5.1). Jag sätter ingen ny hypotes utan mätning.

**Övriga förbehåll som ska följa med siffrorna:**

- **K2-kvittot i bunten är ofullständigt.** `~/recept-kvitto.json` skrivs om per
  anrop, så `data/k2-recept-kvitto.json` bär bara sista steget (+1, V296,
  48211 → 48212). Hela femstegsplanteringen ligger i
  `data/k2-fem-steg-kvitto.json` (från arm 3, samma celler). Grok har rätt att
  "+5" inte gick att rekonstruera ur den fil som låg med.
- **Binärhashen är ett provenienspåstående**, inte omhashat i bunten — servern
  var släckt när bunten packades. Dumpen bär däremot sin egen `provenance`-rad
  med `live :27970` och status 5977/48207.

- `data/kanten.json` är ett **kasserat** mellanprov. Dess egen domrad säger
  "(b) INGEN RUTT FRÅN 1216" — den domen är **fel** och motsägs av dess egna
  rader (`order=hold` medan boten ändå rörde sig, och den nådde faktiskt cell
  1319 inne i disken). Provet blandade tillstånd. Det ligger med för fullständig
  spårbarhet, inte som underlag. **Domen vilar på `fran1216.json`.**
- `cell_vid()` i skripten är en naiv närmaste-avstånd-över-cellorigin, **inte**
  motorns `nearest()`. Den användes för att välja *när* ett prov skulle tas, inte
  för att döma. Cellidentiteterna i §5 kommer ur motorns eget `Cell`-svar.
- n = 8 respektive 3. Litet, men utfallen är 8/8 och 3/3 utan spridning.

---

## 7. Filer

```
PROVENIENS.txt                  sha256 för allt nedan + binären
data/mainref-live-graph.json.gz dumpen ur den levande servern (steg 1)
data/rutt1216.json              8 IN-ring-försök med planavläsning (steg 2)
data/fran1216.json              isoleringen från cell 1216 (det avgörande)
data/kanten.json                KASSERAT mellanprov, se §6
data/k2-recept-kvitto.json      vilka länkar K2 planterade, med celler
data/ra_climb_planted.json      K2 del 1 — sha matchar A-armens egen fil
data/vast_296_planted.json      K2 del 2
skript/disk70.py                steg 1
skript/rutt1216.py              steg 2
skript/fran1216.py              isoleringen
skript/kanten.py                det kasserade provet
```

Skripten kräver `~/rtx-tools/labctl.py` och en levande server på 27970 (utom
`disk70.py`, som bara läser dumpen).
