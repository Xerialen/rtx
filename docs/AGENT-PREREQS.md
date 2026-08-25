# docs/AGENT-PREREQS.md

**Rollfilerna i `.claude/agents/` ändras INTE av det här dokumentet.**
Det här är en lista över de externa dokument rollfilerna förutsätter
men som repot inte innehåller (P0-4,
`WORK_LOGS/2026-08-21-kallstartsgranskning.md` §4). Se granskningens
tabell för exakta radnummer per rollfil.

Alla sex punkter nedan bekräftades 2026-08-21 att de finns på
`/home/xerial/dev/buzz-4on4/` på **pinnacle** (Xerials
arbetsmaskin) — inte i `rtx`-repot, inte på lanister.

## De sex dokumenten

| # | Vad rollfilen förutsätter | Refererat av | Finns idag |
|---|---|---|---|
| 1 | `navmesh-doctor/NAVMESHDOCTOR.md`, `NAVMESHDIAGNOSTICS.md`, `TOOLMANIFEST.md`, `runbooks/` (17 filer) + `contrib/` (6 filer) | `navmeshdoktor.md:10–13` | `/home/xerial/dev/buzz-4on4/navmesh-doctor/` |
| 2 | `reference/ra-room/README.md` — kanonen | `navmeshdoktor.md:23`, `hopparen.md:15`, `fable-orkestratorn.md:32` | **Åtgärdad av P0-3 i den här omgången** — finns nu i `reference/ra-room/` på fork/main. Denna rad står kvar som bokföring av att den TIDIGARE saknades. |
| 3 | `GOTCHAS.md` | `hopparen.md:13`, `kodaren.md:28`, `fable-orkestratorn.md:32` | `/home/xerial/dev/buzz-4on4/GOTCHAS.md` |
| 4 | `PLANS/RA_STATUS.md` | `kodaren.md:28`, `fable-orkestratorn.md:32` | `/home/xerial/dev/buzz-4on4/PLANS/RA_STATUS.md` |
| 5 | `WORK_LOGS/` (bokföring per session) | `hopparen.md:17`, `kodaren.md:28`, `qa-domaren.md:20` | `/home/xerial/dev/buzz-4on4/WORK_LOGS/` (573 filer vid kontrolltillfället) |
| 6 | `GUIDES/VERKTYGSGRINDAR.md` | `demobyggaren.md:15` | `/home/xerial/dev/buzz-4on4/GUIDES/VERKTYGSGRINDAR.md` |

## Varning — måste följa med vid evakuering

Ingen av punkterna 1, 3, 4, 5, 6 checkas in i `rtx`-repot. De lever
enbart som filer på pinnacle, utanför versionskontroll för det här
repot. Om pinnacle går förlorad, byts ut, eller blir oåtkomlig
**försvinner rollfilernas hela kunskapsbas** — precis den typ av
förlust som redan drabbat kanonen (punkt 2) innan P0-3 åtgärdade den.

Att evakuera/backa upp `rtx`-repots kloner räcker alltså INTE. Var och
en som tar över ansvaret för `.claude/agents/`-rollerna måste
separat säkra `/home/xerial/dev/buzz-4on4/` (navmesh-doctor/,
GOTCHAS.md, PLANS/, GUIDES/, WORK_LOGS/) — antingen genom att checka
in relevanta delar i repot (P1-arbete, inte gjort här) eller genom en
dokumenterad backupväg utanför repot.

## Vad detta INTE gör

Detta dokument flyttar inte filerna in i repot, skriver inte om
rollfilerna, och löser inte P1/P2-punkterna i granskningen
(t.ex. `/home/xerial/rtx-tools`-beroendet). Det är enbart en
kartläggning så att en ny agent vet vad som saknas och var det
faktiskt bor idag.

---
Källa: `WORK_LOGS/2026-08-21-kallstartsgranskning.md` §4 (P0-4),
verifierad `ls`/`find` mot `/home/xerial/dev/buzz-4on4/` på pinnacle
2026-08-21.
