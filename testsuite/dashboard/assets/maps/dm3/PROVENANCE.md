# dm3 map assets — provenance

| file | source | generated |
|---|---|---|
| graph.json | rtx kontrollkanal :27980 via `mkgraph.py`, testflödets fasttrack-rigg på nattens binär `c7fd8098` (west-shelf-grafen) | 2026-08-19 |
| entities.json | dm3.bsp entity lump extraction (items/spawns/teleports) | 2026-07-26 |
| linkgeo-westshelf.json | länkgeometri ur samma dump, för de 68 länk-id nattens kuvert refererar | 2026-08-19 |

## Vilken graf det här är

Nattens T0–T4 (kuvert `t2-20260818T225548Z-b89bbd46`) kördes på **5981 celler /
48 217 länkar**. Kartan är dumpad från exakt den grafen: riggen startades på nattens
artefakt ur det sha-förseglade arkivet, och dumpen togs när kontrollkanalen
rapporterade `cells=5981, links=48217`.

Den återställda riggbinären (`27b493b6`) ger en **annan** graf — 5978/48208, utan
engine-sidans `west-shelf`-navpatch. Kartan går alltså inte att reproducera utan
nattens binär, och det är avsiktligt bokfört här.

## Accepterade tal, och varför de inte är identiska

| | |
|---|---|
| celler | **5981** — exakt lika kuvertets nav-block |
| länkar i dumpen | **48 202** — traverserbara out-länkar |
| länkar enligt motorn | 48 217 |
| ritade i `graph.json` | **6 354** — de sex arter kartan ritar |

Tre tal, tre olika saker, och de ska inte förväxlas:

**48 217 mot 48 202 — skillnaden är 15.** `mkgraph.py` bygger länklistan ur varje
cells `out`. Motorns `links`-total räknar hela länkarrayen.

* **Mätt:** motorns total minus summan av alla `out` är exakt **15**, och `CellById`
  svarar med fälten `cell, hazard, incoming, ledge, origin, out` — det finns **inget
  `out_pruned`** att läsa på kanalen. 48 217 går därför inte att nå via den här
  dumpvägen, oavsett hur många gånger den körs.
* **Slutsats, inte mätning:** att just de 15 är den beskurna T=0-familjen. Det är
  konsistent med motorns eget `out_pruned`-begrepp och med att 15 är antalet i
  T-återuppståndelsehistorien — men det mättes inte här, och ska inte läsas som om
  det gjorde det.

Acceptansen justerades därför av Fable till *celler exakt, länkar = traverserbara
out-länkar*.

**48 202 mot 6 354** är inte en förlust. `graph.json` har alltid burit bara de arter
kartan ritar (`linkKinds`: jump, speedjump, rocketjump, drop, plat, teleport) —
julidumpen bar 7 478 av sin grafs länkar på samma sätt. Gång, steg och simning ritas
inte. Fördelningen i dumpen: walk 28 152, swim 11 892, speedjump 2 679, jump 2 126,
step 1 804, drop 1 546, plat 3, rocketjump 0, teleport 0.

## Format

`cells` är en **platt** lista med tre tal per cell (5981 celler → 17 943 tal), inte
nästlade triplar. `links` är platta triplar `[from_index, to_index, kind_index]` där
index pekar in i `cells`, inte serverns id. `cell_ids[i]` bär serverns id för index
`i` — det är den tabellen sidan bygger sin id→position-karta ur.

## Cell-id är grafversionsspecifika

Id:n går inte att översätta mellan grafer: meganav (4635), pre-meganav (4631),
julidumpen (4602) och den här (5981) delar inga id. Snapshotskrivare måste bokföra
vilken graf de kördes mot; sidan löser positioner ur snapshottens egna `pos` när de
finns och faller tillbaka på de här filerna annars.

`linkgeo-meganav.json` är **borttagen**. Dess nycklar var `m`-prefixade
(`m10085`), medan overlayn slår upp på rått länk-id (`LINKGEO[id]`) — den löste
alltså aldrig något ens för sin egen graf. Ersättaren är nycklad på rått id och löser
**68 av 68** länk-id som nattens kuvert refererar.
