# docs/BRANCHES.md

Grenregister genererat ur `git branch -r` mot `fork` (`git@github.com:Xerialen/rtx.git`)
och `git log -1` per gren, 2026-08-21. Klassning är begränsad till vad
som kunde beläggas i denna genomgång (P0-2,
`WORK_LOGS/2026-08-21-kallstartsgranskning.md`). Grenar utan känd
ägare/plan är märkta **behöver klassning** — gissa inte status åt dem.

## Mätverktygslådan finns bara på grenar

`main` har **2** filer under `testsuite/tools/`. `fork/ring2quad` har
**522**. `tools/gates/` (försegling: `facit_lint.py`,
`forsegla_facit.sh`) och `crates/ben3d/` (6 filer) finns **inte alls**
på main, bara på `fork/ring2quad` (2 respektive 6 filer där). En
agent som börjar på main saknar alltså både mätverktygslådan och
förseglingskedjan. Ingen sammanslagningsplan för detta dokumenteras
här — det kräver ägarbeslut.

## Register

| gren | senaste commit | datum | unika i main | unika i grenen | klass |
|---|---|---|---|---:|---:|
| `main` | `bd838cc` docs(agents): add qwen-forensikern role | 2026-08-21 | 0 | 0 | **leveransgren** |
| `mallinjefix` | `1e37b4e` fix(control): shrink the goto finish corridor | 2026-08-21 | 3 | 1 | **aktiv mätgren** |
| `receptautostart` | `e6b25b7` receptautostart: etapp 1 komplett enligt facit v2 | 2026-08-21 | 2 | 3 | **aktiv mätgren** |
| `ring2quad` | `04666b7` 70u: rättelse efter groks dom + fönstermätningen | 2026-08-20 | 3 | 206 | **aktiv mätgren** |
| `lagbench-p3` | `9677fc0` bot: never recreate a rostered name ... | 2026-08-20 | 3 | 181 | behöver klassning |
| `lagbench-p3-bench` | `1a7d559` bot: never recreate a rostered name ... | 2026-08-20 | 3 | 1 | behöver klassning |
| `toolbox/d-drift` | `7b3e0a3` feat(d): U5b — seal producer in CI preflight | 2026-08-17 | 3 | 109 | behöver klassning |
| `toolbox/b-planner-telemetry` | `26b4354` docs(B): C2-replay är ~3/10 kanoniska | 2026-08-16 | 3 | 16 | behöver klassning |
| `toolbox/dashboard-i-classes` | `356c110` feat(dashboard): additiva I-klassnycklar | 2026-08-15 | 3 | 1 | behöver klassning |
| `dm3-westshelf-navpatch` | `7c124cb` docs(trap_repro): carry the acceptance run's numbers | 2026-08-03 | 7 | 3 | behöver klassning |
| `toolbox/d-navpatch-rebase` | `7c124cb` docs(trap_repro): carry the acceptance run's numbers | 2026-08-03 | 7 | 3 | behöver klassning (samma tipp som `dm3-westshelf-navpatch`) |
| `jumps-on-main-pr` | `8d76166` chore: scope this PR to the DM3 jump work | 2026-07-25 | 141 | 108 | behöver klassning — hopplöst efter main; bär `rtx_rj_cost_scale` (se P1-3 i granskningen) |
| `pr6-all-jumps` | `d1d5fd3` fix(control): arm fresh DM3 item trials | 2026-07-25 | 143 | 106 | behöver klassning — hopplöst efter main |
| `merge-trial` | `ad26ccc` Trial harness: mandatory waypoint pickup | 2026-07-23 | 183 | 77 | behöver klassning — hopplöst efter main |
| `ra-tunnel-on-main` | `3222689` chore: PR hygiene | 2026-07-21 | 183 | 41 | behöver klassning — hopplöst efter main |
| `focus-controller` | `04437e7` Add runtime nav cell planting | 2026-07-22 | 259 | 32 | behöver klassning — hopplöst efter main |
| `bsp-probe` | `0e94183` fix: honor scalar yaw on secret doors | 2026-07-22 | 259 | 12 | behöver klassning — hopplöst efter main |
| `recept-i-tradet` | `4db5b19` reference/recept: versionera K2- och vF5-recepten | 2026-08-20 | 0 | 2 | behöver klassning — **helt sammanslagen i main** (0 unika i main innebär allt är ancestor av main? se not) |
| `chain-entry-gate` | `a84836b` fix(nav): add every-tick leg-hold check | 2026-07-31 | 0 | 7 | behöver klassning — kandidat för städning, se not |
| `sj-abort-grounded` | `e2d6c1c` fix(nav): gate curl too-slow aborts on ground | 2026-08-01 | 0 | 4 | behöver klassning — kandidat för städning, se not |
| `plan-cell` | `9985808` fix(nav): validate what gets planted | 2026-07-25 | 0 | 134 | behöver klassning |
| `meganav-plus-telemetry` | `a2e7059` fix(bot): speed-scale the winding horizon | 2026-07-26 | 0 | 102 | behöver klassning |
| `testsuite` | `11b8d67` fix(testsuite): stop tracking the built dashboard | 2026-07-28 | 0 | 76 | behöver klassning |

Not om "unika i main" = 0: enligt `git rev-list --left-right --count
fork/main...fork/<gren>` har dessa grenar inga commits som saknas på
`fork/<gren>` sett från main-sidan av den symmetriska skillnaden —
det betyder INTE nödvändigtvis att grenen är en ren förfader till
main (main kan ha tagit samma ändringar via en annan commit-kedja,
t.ex. en squash-merge). Kontrollera med `git merge-base
--is-ancestor fork/<gren> fork/main` innan någon gren tas bort.
Granskningen 2026-08-21 flaggade sex grenar som "0 commits före
main" och rekommenderade städning av dem — den här tabellen bekräftar
siffrorna men utför ingen radering.

## Vad som INTE gjordes här

Ingen gren togs bort. Ingen gren mättes om. Klassningen "aktiv
mätgren" för `mallinjefix`, `receptautostart` och `ring2quad` kommer
direkt från uppdragsgivarens instruktion, inte från nytt beläggande
arbete i den här genomgången — verifiera själva innehållet innan
någon åtgärd tas på dem.

---
Källa: `git branch -r` + `git log -1` mot `fork` 2026-08-21, kört i
`/tmp/rtx-p0` (worktree av `/home/xerial/projects/quakeworld/rtx` på
lanister). Se även `WORK_LOGS/2026-08-21-kallstartsgranskning.md` §5.
