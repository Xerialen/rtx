# RA-rummet — defaultkontrakt (strömbrytare)

Ring2quad får inte ändra låsen nedan utan ny RA-omkörning. En PR som
släcker F1-default, slår av bake för tom dir på dm3, eller släpper in
vf5 i inbäddad las ska falla i `cargo test -p rtx-game --lib` med
prefix `RA_ROOM_LOCK`.

Källa: `WORK_LOGS/2026-08-22-ra-room-las.md` (buzz-4on4). Inte 99 %.
Inte merge till main. `walkdiag` stannar false. `rtx_bot_count` stannar 0.

## Lås

| # | Lås | Värde | Test |
|---|---|---|---|
| 1 | Karta default-recept | bara dm3 | `defaultgraf_tom_dir_dm3_ar_inbaddad` / `ra_room_kontrakt::tom_dir_dm3_inbaddad` |
| 2 | Mållinjefix | `goto_crossed_finish` c1–c5 | `control.rs` §8-tester |
| 3 | F1 `edge_narrow` | default **true** | `edge_narrow_osatt_fro_laser_true` / `ra_room_kontrakt::edge_narrow_true` |
| 4 | `walkplan` | default **true** | `walkplan_osatt_fro_laser_true` / `ra_room_kontrakt::walkplan_true` |
| 5 | K2 bake | tom dir + dm3 ⇒ climb+väst, 5977/48212, hash `feeea6b4…` identitet | `k2_bake_identitet_feeea6b4_och_mutation_andrar_hash` / `ra_room_kontrakt::k2_bake_hash_feeea6b4` |
| 6 | Inte vf5 | `las_inbaddad("vf5_*")` = None, ingen katalogscan | `inbaddad_las_aldrig_vf5_eller_katalogscan` / `ra_room_kontrakt::vf5_inte_inbaddad` |
| 7 | `walkdiag` | default **false** | `walkdiag_stannar_false_som_default` / `ra_room_kontrakt::walkdiag_false` |
| 8 | `rtx_bot_count` | default **0** (ent via cfg) | `bot_count_orord_noll` / `ra_room_kontrakt::bot_count_noll` |

Ring2quad-PR: får inte ändra rad 2–7 utan ny RA-omkörning. Får inte lägga
vf5 i inbäddad las. Får inte slå av bake för tom dir på dm3.

Startkontrakt (dokumenteras här, inte cvar-frö): dm3, ingen cfg som sätter
`edge_narrow 0`, `rtx_recept_dir` tom eller bake, vänta navmesh, minst en
bot via cfg.
