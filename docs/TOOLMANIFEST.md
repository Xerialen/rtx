# docs/TOOLMANIFEST.md — verktygsmanifest (repo-sidan)

Ägarorder 2026-08-25: skills registreras i verktygsmanifestet och
versioneras i det här repot. Det fullständiga prosa-manifestet för
navmesh-verktygslådan lever på pinnacle
(`/home/xerial/dev/buzz-4on4/navmesh-doctor/TOOLMANIFEST.md`, se
`docs/AGENT-PREREQS.md` punkt 1) — det dupliceras INTE här. Den här
filen förtecknar de verktyg/skills som ägaren beslutat ska finnas i
repot. Inga poster läggs till eller tas bort utan ägarens godkännande.

| Verktyg/skill | Fil i repot | Ägarbeslut | Vad |
|---|---|---|---|
| `hubb-klipplank` | `docs/skills/hubb-klipplank.md` | 2026-08-25 | Spelbar hemmahubb-länk per beslutscase: band→MVD-mappning, sha-verifierad kopia, demo-player-URL, tidskalibrering mot demoklockan |
| `skarmdumpsvalidering` | `docs/skills/skarmdumpsvalidering.md` | 2026-08-25 | Obligatorisk valideringsgrind före ägarvänd leverans: headless-skärmdumpar (telefon först), testklick, pixeldiff, 1 bild/s-händelsefönster, ögonkontroll, negativkontroll |

Aktiva exemplar (de som laddas av agenterna) ligger i
`.claude/skills/<namn>/SKILL.md` på pinnacle; repo-kopiorna är den
versionerade sanningen vid drift-kontroll. Vid ändring: uppdatera
BÅDA och notera datum här.
