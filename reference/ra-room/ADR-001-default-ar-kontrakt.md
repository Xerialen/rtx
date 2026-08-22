# ADR-001 — RA-room default är ett kontrakt

## Context

S5 n=100 giltiga/ben gav 592/600 (98,7 %) med F1 på. Ägare 22/8: det är
SUCCESS; 99/ben är stängt. PR #46 la fröna och `recept::tests::ra_room_kontrakt`
på `main` (`205fc8af`). RING2QUAD kommer efter och får inte vända RA-rummets
lastbärare tyst. Unit-testet *kan* falla (`Bool(true)`→`false` gav
`RA_ROOM_LOCK: edge_narrow≠true`) men blockerar inte merge om checken inte
är required, om testet raderas i samma PR, eller om jobbet skippas.

## Decision

`rtx_bot_edge_narrow` default **true** är ett kontrakt, inte en UI-knapp.
Ändring av lås 1–8 i `DEFAULT-KONTRAKT.md` kräver ny RA-omkörning, uppdaterad
kontraktfil, och grön check `ra-room-lock` som *avsiktligt* skrivits om — inte
en grön svit efter tyst revert.

Kedja krav → test → check-namn:

| Lås | Krav | Test | Check |
|---|---|---|---|
| 1 | tom dir+dm3 = inbäddad bake | `recept::tests::ra_room_kontrakt::tom_dir_dm3_inbaddad` | `ra-room-lock` |
| 2 | mållinje c1–c5 | `control::tests::c1_…`–`c5_…` (`--exact`, fail-om-tomt) | `ra-room-lock` |
| 3 | `rtx_bot_edge_narrow` default true | `ra_room_kontrakt::edge_narrow_true` + token i `cvars.rs` + `tests/ra_room_lock.rs` | `ra-room-lock` |
| 4 | `rtx_bot_walkplan` default true | `ra_room_kontrakt::walkplan_true` + token | `ra-room-lock` |
| 5 | K2-bake hash `feeea6b4…` | `ra_room_kontrakt::k2_bake_hash_feeea6b4` | `ra-room-lock` |
| 6 | vf5 inte inbäddad | `ra_room_kontrakt::vf5_inte_inbaddad` | `ra-room-lock` |
| 7 | `walkdiag` default false | `ra_room_kontrakt::walkdiag_false` + token | `ra-room-lock` |
| 8 | `rtx_bot_count` default 0 | `ra_room_kontrakt::bot_count_noll` + token | `ra-room-lock` |

Jobbet `ra-room-lock` har inget path-filter, inget skip-`if:`, inget
`continue-on-error`. Settings (required check + no bypass) är ägarled.

## Consequences

RING2QUAD får sätta cvarer i *sin* cfg/rigg, inte i `RTX_CVAR_DEFAULTS`.
vf5 stannar utanför `las_inbaddad`. Bake för tom dir på dm3 stannar.
`walkdiag` stannar false. `rtx_bot_count` stannar 0. Markdown, ADR och
PR-checkbox fäller inte binären; named check utan bypass gör det.
CODEOWNERS flaggar hunken på en 1-persons-fork, den är inte mergegrinden.
Dummy-PR som vänder fröet till `Bool(false)` är negativkontrollen efter
Settings — inte del av denna PR.
