# 70u — mätbunt 2: varför töms planen?

Uppföljning på grokks dom över bunt 1. **Rättelserna är gjorda först**, mätningen
sedan, i den ordning domen krävde.

---

## 0. Vad som rättades ur bunt 1

Grokks omräkning var riktig på båda punkterna. Bunt 1:s README är rättad i
sitt §5.1 och `WORK_LOGS/2026-08-20-70u-toppdisk-fynd.md` har fått ett §8 som
drar tillbaka slutsatsen. Sammanfattat:

- **Höjdtabellen 22–43 u är indragen.** Faktisk fördelning i `rutt1216.json`:
  **7 av 8 stopp på golvet (dz = 0)**, ett på dz = 28. Fyra av den gamla
  tabellens sex rader fanns inte i filen — de kom från n20-armarnas
  stoppositioner och redovisades under fel rubrik.
- **Slutsatsen "klättringens sista landning" är indragen**, dessutom motbevisad
  av bunt 1:s egen §4: isoleringen startade på **z = 352 / 354 / 356**, mitt i
  det utpekade höjdbandet, och gick in ändå på 0,61 s.
- **K2-kvittot** i bunt 1 bar bara sista steget (+1). Femstegskvittot ligger med
  här som `data/k2-fem-steg-kvitto.json`.

Det som stod kvar och som denna bunt bygger på: **planen är tom när boten står
på västkanten.**

---

## 1. Proveniens

| | |
|---|---|
| rigg | `RA-DRILL-MAIN-REF`, styrkanal 27970 — uppe kort, **ingen hubbpublicering** |
| binär | `qwprogs.so` `27b493b6f5e1d148…` ur fork `cc5fa8e` |
| graf | default 5977 / 48207 → **med K2 5977 / 48212** (kvitto i data) |
| släcktes | direkt efter mätningen, återställd till default först (verifierat) |

---

## 2. Design (grokks)

IN-ring från kanonens startpunkt [449 · −338 · 56] mot toppen
[256 · −704 · 328]. **Tät plantelemetri i fönstret t = 3,5–9 s**, åtta försök.
Varje avläsning tar tre saker:

- `Route` → `route_pos` + alla ben (länk, kind, src_cell, tgt_cell)
- `Status` → origin, order, posture, on_ground
- **motorns eget `Cell`** för botens punkt — inte min naiva närmaste-avstånd
  (den domen fälldes i bunt 1 och används inte här)

Utöver pollningen dras botens **flygskrivare** (`Audit`, 400 bilder ≈ 5 s) efter
varje försök. Den går i motorns takt och bär `route_len`, `route_pos`, `cell`,
`target`, `route_goal`, `leg`, `goal_cell`, `off_reason` per bild.

**Kör om:** `python3 skript/fonstret.py 8` → `data/fonstret.json`
**Läs övergången:** `python3 skript/audit_fonster.py`

---

## 3. Pollningen: planen dör vid t ≈ 6,63 s, i 7 av 8

| försök | plan tom från | sista planen | första benet då | slut |
|---|---|---|---|---|
| 1 | 6,635 | 6,574 (8 ben) | 9385 walk 1274→1222 | [155 · −700 · 371] cell 1216 |
| 2 | 6,630 | 6,569 (8 ben) | 9385 walk 1274→1222 | [151 · −700 · 372] cell 1216 |
| 3 | 6,638 | 6,576 (4 ben) | 8796 walk 1191→1216 | [155 · −700 · 372] cell 1216 |
| 4 | 6,635 | 6,573 (3 ben) | **8993 walk 1216→1267** | [155 · −700 · 372] cell 1216 |
| 5 | 6,636 | 6,575 (4 ben) | 8796 walk 1191→1216 | [151 · −700 · 372] cell 1216 |
| 6 | — | 9,055 (27 ben) | 8030 step 1090→1123 | [91 · −879 · 184] cell 1152 |
| 7 | 6,624 | 6,562 (3 ben) | 9008 walk 1218→1267 | [162 · −684 · 371] cell 1218 |
| 8 | 6,642 | 6,581 (8 ben) | 9385 walk 1274→1222 | [151 · −700 · 372] cell 1216 |

Sju av åtta inom **20 ms**. Försök 6 tog en annan väg och hade 27 ben kvar när
fönstret stängde — det är inget undantag att bortförklara, det är en
kontrollobservation: när vägen blir en annan inträffar inte fallet.

---

## 4. Flygskrivaren: det sker på **en enda bild**

Försök 4, motorns egen logg (mönstret är identiskt i alla sju):

```
t=69,626  pos=[151 · −701 · 328]  cell=1216  len=3  pos_i=0  leg=Walk  target=1372  goal_cell=1372
t=69,646  pos=[155 · −700 · 328]  cell=1216  len=0  pos_i=0  leg=None  target=1216  goal_cell=1372
```

Fyra saker byter i samma bild, ~20 ms:

1. `route_len` **3–4 → 0**
2. `leg` **Walk → None**
3. `target` **1372 → 1216** — från målet inne i disken till **botens egen cell**
4. `off_reason` `"zigzag"` → tomt

**`goal_cell` står kvar på 1372.** Ordern lever. Det är **rutten** som dör.

Boten står på **z = 328 — cellens golv** — när det sker. (Slutpositionerna i
tabellen ovan visar z ≈ 371 därför att fönstret stänger innan boten fallit
tillbaka; flygskrivaren fångar själva ögonblicket, och där är z = 328.)

---

## 5. Det är inte positionsbundet — 24 av 24

Dödpunkterna ligger på x ≈ 151–155, ~5–9 u väster om cell 1216:s origin
[160 · −704 · 328]. Om positionen vore orsaken skulle en **ny** order från samma
punkt också misslyckas.

**Kör om:** `python3 skript/punktprov.py 3` → `data/punktprov.json`

| startpunkt | motorns cell | fick rutt | **in i disken** |
|---|---|---|---|
| 1191 origin [141 · −701] | 1191 | 3/3 | **3/3** |
| x=144 | 1191 | 3/3 | **3/3** |
| x=148 | 1216 | 3/3 | **3/3** |
| **x=151 (dödpunkt)** | 1216 | 3/3 | **3/3** |
| **x=155 (dödpunkt)** | 1216 | 3/3 | **3/3** |
| x=158 | 1216 | 3/3 | **3/3** |
| 1216 origin | 1216 | 3/3 | **3/3** |
| 1218 origin | 1218 | 3/3 | **3/3** |

**24 av 24.** Från exakt de punkter där rutten dör under gången fungerar allt när
ordern är ny.

---

## 6. Vad bunten fastställer

**Rutten dör av något i den pågående orderns tillstånd — inte av var boten står,
inte av grafen, inte av höjden.**

| belägg | var |
|---|---|
| samma punkt, **ny** order → rutt och genomförande, 24/24 | §5 |
| samma punkt, **gammal** order → tom rutt, 7/8 | §3–4 |
| `goal_cell` överlever medan `route_len` nollas | §4 |
| annan väg (försök 6) → inget fall | §3 |

Det avför också de förklaringar som stod kvar efter bunt 1: **inte** att målet
räknas som nått i ordermening (`goal_cell` står kvar), och **inte** att boten
står fel (samma punkt fungerar med ny order).

---

## 7. Vad som INTE är fastställt

**Vad i orderns tillstånd som nollställer rutten.** Förklaringar som *skulle*
passa mönstret — en ruttlivstid, en omplaneringsbudget, ett ankomsttest som fyrar
en gång — är **alla oprövade**. Jag har inte mätt någon av dem och sätter ingen
hypotes utan mätning. Nästa mätning ska riktas mot orderns egen livscykel, inte
mot banan.

**Öppen anomali:** i försök 1 stod `goal_cell` på **2544**, inte 1372 som i
övriga sju. Noteras, inte tolkas.

**Förbehåll på siffrorna:**

- Pollningens `t` är väggklocka i mitt skript; flygskrivarens `t` är motorns
  speltid. Tiderna i §3 och §4 är alltså **inte** samma tidsaxel och ska inte
  jämföras rakt av. Det som jämförs är ordningen och intervallens vidd.
- `t ≈ 6,63 s` är avläst med ~60 ms pollperiod; den verkliga händelsen ligger i
  det intervallet. Flygskrivaren ger ögonblicket exakt (~20 ms upplösning).
- n = 8 respektive 3 per punkt. Utfallen är 7/8 och 24/24 utan spridning.
- Cellidentiteter kommer ur motorns `Cell`/flygskrivarens `cell`. Skriptens
  `cell_vid()` används bara för att välja **när** ett prov ska tas, aldrig för
  att döma.

---

## 8. Filer

```
data/fonstret.json              8 IN-ring-försök: tät pollning + 400 auditbilder vardera
data/punktprov.json             8 startpunkter x 3, ny order
data/k2-fem-steg-kvitto.json    hela K2-planteringen med celler (+5)
data/mainref-live-graph.json.gz dumpen ur levande servern (samma som bunt 1)
skript/fonstret.py              mätningen
skript/punktprov.py             positionsprovet
skript/audit_fonster.py         läser övergången ur flygskrivaren
```
