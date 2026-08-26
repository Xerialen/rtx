# docs/TOOLMANIFEST.md — verktygsmanifest (repo-sidan)

Ägarorder 2026-08-25: skills registreras i verktygsmanifestet och
versioneras i det här repot. Ägarorder 2026-08-26: manifestet och
subagentbeskrivningarna ska vara uppdaterade i repot inför
orkestratorbytet. Det fullständiga prosa-manifestet för
navmesh-verktygslådan lever på pinnacle
(`/home/xerial/dev/buzz-4on4/navmesh-doctor/TOOLMANIFEST.md`, se
`docs/AGENT-PREREQS.md` punkt 1) — det dupliceras INTE här.
**Inga poster läggs till eller tas bort utan ägarens godkännande.**

## 1. Skills (aktiva exemplar i `.claude/skills/<namn>/SKILL.md` på pinnacle)

| Skill | Fil i repot | Ägarbeslut | Vad |
|---|---|---|---|
| `hubb-klipplank` | `docs/skills/hubb-klipplank.md` | 2026-08-25 | Spelbar hemmahubb-länk per beslutscase: band→MVD-mappning, sha-verifierad kopia, demo-player-URL, tidskalibrering mot demoklockan. Ägaren dömde falldefinitionen via den här kedjan. |
| `skarmdumpsvalidering` | `docs/skills/skarmdumpsvalidering.md` | 2026-08-25 | Obligatorisk grind före varje ägarvänd leverans: headless-skärmdumpar (telefonformat först), testklick på varje utlovad väg, pixeldiff för uppspelning, 1 bild/s-fönster runt händelser, ögonkontroll av varje bild, negativkontroll. |
| `navmesh-sight` | *(endast pinnacle)* | äldre | Öppnar befintliga navmeshvisningar; bygger aldrig nya. Första raden i varje diagnosorder ska peka på den. |
| `qa-verdict`, `report`, `impeccable` | *(endast pinnacle)* | äldre | Domsmall, rapportformat, gränssnittsarbete. |

## 2. Roller / subagenter (`.claude/agents/`)

Rollfilerna är kanon för vad varje säte får och inte får göra.
Synkade från pinnacle 2026-08-26.

| Roll | Fil | Ger | Får inte |
|---|---|---|---|
| Orkestrator (Grok) | `grok-orkestratorn.md` | order, ägarrapport, sekvensering | döma i sak, mäta, skriva produktionskod |
| Orkestrator (Fable) | `fable-orkestratorn.md` | samma roll, föregående innehavare | samma gränser |
| Kodaren | `kodaren.md` | kod, facitutkast, addenda på order | döma sitt eget arbete |
| QA-domaren | `qa-domaren.md` | dom PASS/FAIL/OKLAR ur rådata | producera det den dömer |
| Hopparen | `hopparen.md` | armbyggen, riggar, drillar, körkvitton | döma utfallet |
| Navmeshdoktorn | `navmeshdoktor.md` | diagnos bevisförst, åtgärdsKLASS | utföra åtgärder |
| Demobyggaren | `demobyggaren.md` | demos, artefakter, visningar | röra mätriggar eller facit |
| Qwen-forensikern | `qwen-forensikern.md` | namngivna mätskript, tabeller | predikat, facit, pins, merge |

**Modellval (dyrköpt 2026-08-26):** långa mätpass och stora
granskningar körs med `model: opus` — standardmodellen slog i
kreditgränsen mitt i en mätning och sätet dog. Billig grovräkning kan
gå på `sonnet`. Kontrollera panelsätens veckokvot med
`herdr agent read <pane>` **innan** order läggs; en order till ett
kvotlöst säte försvinner tyst.

## 3. Mätinstrument som INTE ligger i repot (öppen risk)

Följande används i dömda mätningar men lever bara på riggen och är
alltså inte versionshanterade — samma risk som `docs/AGENT-PREREQS.md`
beskriver för rollfilernas kunskapsbas. **Öppet ägarbeslut** om de ska
in i repot:

| Instrument | Var | Roll |
|---|---|---|
| `falldiag.py` | `lanister:~/lab/falldef-diagnos/` | fall-/zonklassning i diagnosläge |
| `kor_block_s1.py` | `lanister:~/lab/s164-omgang/s1-drill-v2/skript/` | drivern för on/off-drillen (bär fail-closed audit-märkning) |
| `obducera` | `lanister:~/ft-toolbox-i` | kanonisk obduktion + remedie-taxonomi |
| heatmap-CLI | `lanister:~/ft-toolbox-h` | evidensbilder |
| `forsegla_facit.sh` + `facit_lint.py` | `lanister:~/rtx-toolbox-d` | **enda** förseglingsvägen; repots kopia saknar beroenden och kan inte köras |

## 4. Överlämning

`docs/HANDOFF-2026-08-26-grok-orkestrator.md` — fullständig
överlämning till ny orkestrator (roll, maskiner, säten, kvoter,
aktivt spår, öppna ägarbeslut, praktik, hårda nej).

Aktiva exemplar av skills ligger i `.claude/skills/<namn>/SKILL.md`
på pinnacle; repo-kopiorna är den versionerade sanningen vid
driftkontroll. Vid ändring: uppdatera BÅDA och notera datum här.
