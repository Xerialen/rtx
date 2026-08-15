# B-körgrinden — körordning utan motorkunskap

Tre skript kör hela den riggbundna delen av spår B. Ingen av dem kräver att du
kan motorn; alla tre vägrar hellre än gissar, och alla tre har `--help`.

**Instansen de förutsätter:** `toolbox-b-test`, spel 27590, kontroll **27995**,
runtime `~/.local/share/qw-fasttrack/runtime-tbx`, byggt från
`toolbox/b-planner-telemetry`. Riglocket tas av Fable.

Kör i den här ordningen. Steg 1 är det som avgör om resten är meningsfullt.

---

## 1. V296-återspelningen (viktigast)

```
python3 b_v296_replay.py --port 27995 --n 10 --out ~/lab/v296-replay.jsonl
```

Planterar V296-länken i två varianter, flyger boten över dem tio gånger var, och
skriver ut de fält terras förseglade facit pekar ut — en rad per tick i
takeoff-fönstret, plus hela PlanTick-strömmen som JSONL.

**Skriptet dömer inte.** Det skriver aldrig "godkänt" eller någon orsaksetikett.
Domen mot facit är kimis. Det du får är underlaget, råt:

| | facit väntar |
|---|---|
| C1 | källcell **1139**, `phase_prev=Prestrafe` → `phase=Hop`, `on_ground=false`, `jump_cmd=false`, `first_air_vz ≤ 0` (facit såg −9,6) |
| C2 | källcell **1167**, jump-cmd på övergångsframen, `first_air_vz > 0` (facit såg +260,4) |

Båda kör `v_req=320`. Det är poängen: farten kan inte skilja dem åt. Skiljelinjen
ligger i controller-tillståndet.

Avbryter (kod 2) om servern inte är redo, om planteringen nekas, om cvarerna inte
läser tillbaka som satta, eller om **noll PlanTick** kommer in — det sista betyder
fel bygge eller avstängd telemetri, och tomma rader hade sett ut som ett resultat.

## 2. Regressionsskyddet

```
./b_regressionsdiff.sh --port 27995 --out ~/lab/b-regress/tbx
# samma sak mot ett main-bygge på egen port:
./b_regressionsdiff.sh --port <mainport> --out ~/lab/b-regress/main
diff ~/lab/b-regress/tbx.signatur ~/lab/b-regress/main.signatur && echo LIKA
```

Två grindar, båda byte-jämförbara:

1. **Hård grind:** noll `PlanTick` och noll `PlanContract` med cvarerna av.
   Fallerar den returnerar skriptet 1 — regressionen är ett faktum.
2. **Signatur:** eventarterna och deras sorterade fältnamn, tidsoberoende.
   Den ska vara byte-identisk mot mains.

*Varför inte en rå byte-diff av strömmen:* strömmen bär servertid, och två
körningar mot en levande server startar aldrig på samma tick. Ett test som per
konstruktion alltid är rött bevisar ingenting. Det som faktiskt står på spel är
att inget nytt hamnar på tråden — och det mäter grind 1 exakt.

## 3. Overheaden

```
./b_overhead.sh --port 27995 --secs 60
```

Samma fönster med cvarerna av och på, och kvoten serverklocka/väggtid i båda
lägena. Skillnaden är overheaden. Returnerar 1 om den överstiger 1 %.

Serverns interna tic-räknare går inte att läsa utifrån kontrollkanalen, men det
tic-vakten skyddar går att mäta direkt: håller servern sin egen klocka? En server
som halkar efter tappar speltid mot väggtid, vilket är vad en överbelastad tic ser
ut som utifrån.

Skriptet lämnar cvarerna **avslagna** efteråt.

---

## Om något går fel

Alla tre skriver `AVBRYTER: <skäl>` och returnerar 2 när förutsättningarna
brister. Skälet är avsett att räcka: det säger vad som saknades, inte att något
gick fel. Vanligaste orsakerna, i ordning:

1. **Fel port** — B-instansen är 27995, inte 27990 (dm3-labbet) eller 27980.
2. **Navmesh inte redo** — vänta ut bygget, det tar en stund efter map-start.
3. **Cvar läser inte tillbaka** — nästan alltid ett bygge utan planerartelemetri.
   `rtx_plan_telemetry` finns bara på `toolbox/b-planner-telemetry`.
4. **Noll PlanTick** — samma sak som 3, eller `rtx_telemetry` avslagen. Båda
   krävs; fin-cvaren ensam ger ingenting på tråden, med flit.

## Bonus: den kompletta grafdumpen

```
python3 mkgraph_full.py ~/lab/dm3-graph-full.json --port 27995
```

Skriver alla 48208 länkar med traverserbarhetsflagga och räknar fram nivå
2-hashen (`graph_content_hash`) i stderr — det värdet uppdaterar deepseeks §8.4.
Vägrar köra mot ett bygge utan `out_pruned`, i stället för att tyst skriva en
ofullständig dump. Det var precis så 48193-dumpen uppstod.
