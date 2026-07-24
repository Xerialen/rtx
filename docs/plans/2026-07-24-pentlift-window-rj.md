# Pent → lifts → window rocket-jump lab

## Scope

- Branch: `codex/pentlift-window-rj`
- Upstream baseline: `qw-ctf/rtx` `main` at
  `a293067ea89a718bab37d238bde4972ed6c782b0`
- Human reference: `/mnt/c/nQuake/qw/matchinfo/demos/pent-rj.qwd`
- Route: DM3 pent floor → pent lift → window
- Human reference time: approximately `2.715 s`
- Acceptance ceiling: `4.715 s` (reference + `2.000 s`)
- Required result: `5/5`

## Isolated services

- Game: `192.168.86.20:27510`
- Control: `127.0.0.1:27960`
- QTV: `127.0.0.1:29510`
- Home-hub registration: `pentlift-window-rj-codex`
- Bot label: `pentlift-window-rj`

Route-specific movement enables `rtx_bot_rocketjump 1`. Non-route movement
extensions remain disabled: `rtx_doublejump 0`, `rtx_walljump 0`,
`rtx_bot_ledgecap 0`, and `rtx_grapple 0`.

## Final result

The ordinary `goto` planner now selects the certified two-link chain from the
demo start: static-floor RJ `36042` lands directly on the lower lift board, and
compound mover RJ `36390` releases during the lift rise and lands in the window.
The ids are evidence for this exact deterministic DM3 build, not product
configuration.

Reference timing is `2.714660 s`; acceptance is therefore `≤ 4.714660 s`.
Each trial began at `(956.5, 788.5, -296)`, used the normal planner/driver, and
allowed the lift to complete its ordinary down cycle before the next attempt.

| Trial | Window time | Difference | Rockets | Result |
|---:|---:|---:|---:|:---:|
| 1 | 3.799404 s | +1.084744 s | 2 | pass |
| 2 | 3.798264 s | +1.083604 s | 2 | pass |
| 3 | 3.960701 s | +1.246041 s | 2 | pass |
| 4 | 3.797119 s | +1.082459 s | 2 | pass |
| 5 | 3.798042 s | +1.083382 s | 2 | pass |

Result: **5/5**, mean `3.830706 s`, worst `3.960701 s` / `+1.246041 s`.

The gap closed in generic layers rather than with demo coordinates:

- running ground velocity is carried through RJ solve, link telemetry, and
  runtime execution;
- inline `func_plat` speed/footprint/phase is included in a timed moving-surface
  solve, including projectile-frame latency and perturbation certification;
- static RJs whose certified touchdown is physically on a resting mover are
  wired directly to its board cell and retain their solved boarding cost;
- steep-look world-space movement is projected through the engine's flattened
  yaw basis;
- stationary RJ staging settles on the target side inside the existing ±16u
  launch certificate, preventing a reverse-velocity release while aim settles;
- `rtx_rj_cost_scale` is opt-in (`1.0` default, `0.35` in this isolated lab) so
  the test server can prefer certified RJ routes without weakening geometry or
  perturbation gates.

Final graph: `4631` cells, `36395` links, `732` rocket-jump links.

Final release verification:

- default release suite: `269` game + `111` nav + `119` protocol tests, all
  supporting crate/integration/doc tests passed;
- `rtx-mcp`: `1/1`;
- `rtx-nav-view`: `2/2`;
- targeted board-splice and stationary-stage regressions passed.

## Activity log

- [2026-07-24 09:24] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --release --locked -- --test-threads=8
- [2026-07-24 09:26] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --release --locked -- --test-threads=8 — exit 0, wall 88s
- [2026-07-24 09:26] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release --locked -p rtx-mcp
- [2026-07-24 09:27] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release --locked -p rtx-mcp — exit 0, wall 64s
- [2026-07-24 09:27] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release --locked -p rtx-game
- [2026-07-24 09:28] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release --locked -p rtx-game — exit 0, wall 29s
- [2026-07-24 09:29] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc systemctl --user restart route-lab-pentlift-window-rj-server.service; for i in $(seq 1 60); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager -o cat | rg -q "rtx: navmesh: .*rjump"; then systemctl --user status route-lab-pentlift-window-rj-server.service --no-pager -n 8; exit 0; fi; sleep 1; done; journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager -n 80; exit 1
- [2026-07-24 09:30] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc systemctl --user restart route-lab-pentlift-window-rj-server.service; for i in $(seq 1 60); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager -o cat | rg -q "rtx: navmesh: .*rjump"; then systemctl --user status route-lab-pentlift-window-rj-server.service --no-pager -n 8; exit 0; fi; sleep 1; done; journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager -n 80; exit 1 — exit 1, wall 61s
- [2026-07-24 09:31] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for i in $(seq 1 60); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "1 minute ago" --no-pager -o cat | rg -q "rtx: navmesh: .*rjump"; then journalctl --user -u route-lab-pentlift-window-rj-server.service --since "1 minute ago" --no-pager -o cat | rg "rtx: (control|navmesh)|bot" | tail -20; exit 0; fi; sleep 1; done; journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager -n 100; exit 1
- [2026-07-24 09:31] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for i in $(seq 1 60); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "1 minute ago" --no-pager -o cat | rg -q "rtx: navmesh: .*rjump"; then journalctl --user -u route-lab-pentlift-window-rj-server.service --since "1 minute ago" --no-pager -o cat | rg "rtx: (control|navmesh)|bot" | tail -20; exit 0; fi; sleep 1; done; journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager -n 100; exit 1 — exit 0, wall 26s
- [2026-07-24 09:36] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release --locked -p rtx-demo-tool --bin qwd
- [2026-07-24 09:36] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release --locked -p rtx-demo-tool --bin qwd — exit 0, wall 9s
- [2026-07-24 09:39] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --release --locked -p rtx-nav running_rocket_jump_reaches_past_the_stationary_envelope -- --exact --nocapture
- [2026-07-24 09:39] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --release --locked -p rtx-nav running_rocket_jump_reaches_past_the_stationary_envelope -- --exact --nocapture — exit 0, wall 14s
- [2026-07-24 09:39] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --release --locked -p rtx-nav running_rocket_jump_reaches_past_the_stationary_envelope -- --nocapture
- [2026-07-24 09:39] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --release --locked -p rtx-nav running_rocket_jump_reaches_past_the_stationary_envelope -- --nocapture — exit 101, wall 1s
- [2026-07-24 09:45] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --release --locked -p rtx-nav running_rocket_jump_reaches_past_the_stationary_envelope -- --nocapture
- [2026-07-24 09:45] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --release --locked -p rtx-nav running_rocket_jump_reaches_past_the_stationary_envelope -- --nocapture — exit 0, wall 10s
- [2026-07-24 09:45] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --release --locked -- --test-threads=8
- [2026-07-24 09:46] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --release --locked -- --test-threads=8 — exit 101, wall 30s
- [2026-07-24 09:47] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --workspace --release
- [2026-07-24 09:50] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --workspace --release — exit 0, wall 146s
- [2026-07-24 09:50] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game -p rtx-mcp
- [2026-07-24 09:51] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game -p rtx-mcp — exit 0, wall 58s
- [2026-07-24 09:52] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 09:52] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 18s
- [2026-07-24 09:58] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test -p rtx-nav --release platform_runway_reaches_each_rectangular_edge
- [2026-07-24 09:58] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test -p rtx-nav --release platform_runway_reaches_each_rectangular_edge — exit 0, wall 11s
- [2026-07-24 09:58] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game -p rtx-mcp
- [2026-07-24 09:58] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game -p rtx-mcp — exit 0, wall 24s
- [2026-07-24 09:59] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 09:59] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 19s
- [2026-07-24 10:01] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game -p rtx-mcp
- [2026-07-24 10:02] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game -p rtx-mcp — exit 0, wall 29s
- [2026-07-24 10:02] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:02] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 23s
- [2026-07-24 10:03] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:04] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 27s
- [2026-07-24 10:04] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:04] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2 minutes ago" --no-pager | grep -q "navmesh: [0-9].*rjump"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 0s
- [2026-07-24 10:04] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2026-07-24 10:04:29" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:04] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "2026-07-24 10:04:29" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 11s
- [2026-07-24 10:05] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:05] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 27s
- [2026-07-24 10:06] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:06] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 23s
- [2026-07-24 10:09] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game -p rtx-mcp
- [2026-07-24 10:09] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game -p rtx-mcp — exit 101, wall 3s
- [2026-07-24 10:10] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game -p rtx-mcp
- [2026-07-24 10:10] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game -p rtx-mcp — exit 0, wall 28s
- [2026-07-24 10:10] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:11] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 23s
- [2026-07-24 10:11] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:12] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 27s
- [2026-07-24 10:12] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:12] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 90); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 23s
- [2026-07-24 10:13] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:13] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 27s
- [2026-07-24 10:14] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 120); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:14] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 120); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 20s
- [2026-07-24 10:16] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:16] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 24s
- [2026-07-24 10:16] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:16] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 18s
- [2026-07-24 10:17] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:18] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 23s
- [2026-07-24 10:18] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:18] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 19s
- [2026-07-24 10:21] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:21] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 24s
- [2026-07-24 10:21] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:22] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 19s
- [2026-07-24 10:22] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:23] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 23s
- [2026-07-24 10:23] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:23] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 20s
- [2026-07-24 10:26] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:26] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 23s
- [2026-07-24 10:26] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:27] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "plat-rj"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 20s
- [2026-07-24 10:27] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc for trial in 1 2 3 4 5; do marker=$(date "+%F %T"); systemctl --user restart route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "rjump 731"; then break; fi; sleep 1; done; python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool test_link --args "{\"link\":36390,\"via\":\"goto\"}" --unwrap > "/tmp/pentlift-window-fresh-${trial}.json"; jq -c --argjson trial "$trial" "{trial:\$trial,goto:(.goto|{ev,t,origin,dist}),rj:(.rj|del(.traj))}" "/tmp/pentlift-window-fresh-${trial}.json"; done
- [2026-07-24 10:30] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc for trial in 1 2 3 4 5; do marker=$(date "+%F %T"); systemctl --user restart route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "rjump 731"; then break; fi; sleep 1; done; python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool test_link --args "{\"link\":36390,\"via\":\"goto\"}" --unwrap > "/tmp/pentlift-window-fresh-${trial}.json"; jq -c --argjson trial "$trial" "{trial:\$trial,goto:(.goto|{ev,t,origin,dist}),rj:(.rj|del(.traj))}" "/tmp/pentlift-window-fresh-${trial}.json"; done — exit 0, wall 144s
- [2026-07-24 10:30] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:31] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 23s
- [2026-07-24 10:31] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:31] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 20s
- [2026-07-24 10:32] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --release -p rtx-game
- [2026-07-24 10:32] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --release -p rtx-game — exit 0, wall 23s
- [2026-07-24 10:32] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 1; done; exit 1
- [2026-07-24 10:33] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc marker=$(date "+%F %T"); systemctl --user start route-lab-pentlift-window-rj-server.service; for attempt in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "$marker" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 1; done; exit 1 — exit 0, wall 20s
- [2026-07-24 10:38] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:38] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 18s
- [2026-07-24 10:38] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:38] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 18s
- [2026-07-24 10:39] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:40] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 18s
- [2026-07-24 10:40] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:40] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 10:43] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:43] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 23s
- [2026-07-24 10:43] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:43] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 21s
- [2026-07-24 10:45] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:46] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 18s
- [2026-07-24 10:46] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:46] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 10:48] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:48] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 20s
- [2026-07-24 10:48] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:49] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 19s
- [2026-07-24 10:49] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:49] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 180); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -q "rjump 731"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 18s
- [2026-07-24 10:51] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-nav -p rtx-game --release -- --test-threads=8
- [2026-07-24 10:51] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-nav -p rtx-game --release -- --test-threads=8 — exit 101, wall 6s
- [2026-07-24 10:52] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-nav -p rtx-game --release -- --test-threads=8
- [2026-07-24 10:53] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-nav -p rtx-game --release -- --test-threads=8 — exit 0, wall 69s
- [2026-07-24 10:53] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:53] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 1s
- [2026-07-24 10:53] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:54] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 10:55] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:56] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 24s
- [2026-07-24 10:56] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:56] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 20s
- [2026-07-24 10:58] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 10:58] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 23s
- [2026-07-24 10:58] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 10:59] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 11:01] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:02] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 23s
- [2026-07-24 11:02] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 11:02] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 11:05] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:05] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 23s
- [2026-07-24 11:05] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 11:05] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 220); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "30 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 11:11] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:11] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 23s
- [2026-07-24 11:11] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 260); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "40 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 11:12] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 260); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "40 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 19s
- [2026-07-24 11:13] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:13] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 22s
- [2026-07-24 11:13] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 260); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "40 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1
- [2026-07-24 11:14] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc install -m 755 target/release/librtx.so /home/xerial/.local/share/route-lab/pentlift-window-rj/qw/qwprogs.so && systemctl --user restart route-lab-pentlift-window-rj-server.service && for i in $(seq 1 260); do if journalctl --user -u route-lab-pentlift-window-rj-server.service --since "40 seconds ago" --no-pager | grep -Eq "navmesh: [0-9]+ planes.*rjump [0-9]+"; then exit 0; fi; sleep 0.25; done; exit 1 — exit 0, wall 23s
- [2026-07-24 11:18] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:18] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 27s
- [2026-07-24 11:19] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): env ROUTE_LAB_DM3_RA_RUNTIME=/home/xerial/.local/share/route-lab/pentlift-window-rj ROUTE_LAB_DM3_RA_SERVICE=route-lab-pentlift-window-rj-server.service ROUTE_LAB_DM3_RA_PORT=27510 bash ops/deploy_dm3_ra.sh /mnt/c/Users/benya/projects/quakeworld/rtx-pentlift-window-rj/target/release/librtx.so rj-cost-scale
- [2026-07-24 11:25] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:26] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 21s
- [2026-07-24 11:26] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-stationary-stage.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        printf '%s\n' "$lab_status"
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:26] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-stationary-stage.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        printf '%s\n' "$lab_status"
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 23s
- [2026-07-24 11:30] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-nav certified_rocket_landing_on_resting_lift_targets_its_board_cell --release -- --exact
- [2026-07-24 11:31] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-nav certified_rocket_landing_on_resting_lift_targets_its_board_cell --release -- --exact — exit 0, wall 12s
- [2026-07-24 11:31] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-nav certified_rocket_landing_on_resting_lift_targets_its_board_cell --release
- [2026-07-24 11:31] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-nav certified_rocket_landing_on_resting_lift_targets_its_board_cell --release — exit 0, wall 1s
- [2026-07-24 11:31] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:31] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 27s
- [2026-07-24 11:32] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-board-splice.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        printf '%s\n' "$lab_status"
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:32] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-board-splice.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        printf '%s\n' "$lab_status"
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 24s
- [2026-07-24 11:33] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-nav certified_rocket_landing_on_resting_lift_targets_its_board_cell --release
- [2026-07-24 11:33] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-nav certified_rocket_landing_on_resting_lift_targets_its_board_cell --release — exit 0, wall 13s
- [2026-07-24 11:34] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:34] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 27s
- [2026-07-24 11:34] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-board-cost.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        printf '%s\n' "$lab_status"
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:35] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-board-cost.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        printf '%s\n' "$lab_status"
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 22s
- [2026-07-24 11:38] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:38] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 22s
- [2026-07-24 11:41] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:41] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 19s
- [2026-07-24 11:41] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-final-route.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:41] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-final-route.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then
        exit 0
      fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 19s
- [2026-07-24 11:42] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then exit 0; fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:42] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then exit 0; fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 21s
- [2026-07-24 11:47] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-game stationary_stage_leans_targetward_inside_the_certified_window --release
- [2026-07-24 11:47] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-game stationary_stage_leans_targetward_inside_the_certified_window --release — exit 0, wall 27s
- [2026-07-24 11:47] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo build --locked -p rtx-game --release
- [2026-07-24 11:47] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo build --locked -p rtx-game --release — exit 0, wall 26s
- [2026-07-24 11:48] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-forward-stage.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then exit 0; fi
    fi
    sleep 1
  done
  exit 1
- [2026-07-24 11:48] [pentlift-window-rj] HEAVY DONE (heavy.sh): bash -lc
  set -euo pipefail
  lab_runtime=/home/xerial/.local/share/route-lab/pentlift-window-rj
  lab_incoming=$(mktemp "$lab_runtime/qw/.qwprogs-rj-forward-stage.XXXXXX")
  trap 'rm -f "$lab_incoming"' EXIT
  cp target/release/librtx.so "$lab_incoming"
  chmod 755 "$lab_incoming"
  mv -f "$lab_incoming" "$lab_runtime/qw/qwprogs.so"
  trap - EXIT
  systemctl --user restart route-lab-pentlift-window-rj-server.service
  for lab_attempt in $(seq 1 180); do
    if lab_status=$(python3 playground/mcp_call.py --binary target/release/rtx-mcp --control-port 27960 --tool status --args "{}" --unwrap 2>/dev/null); then
      if jq -e '.navmesh == "ready" and .links == 36395 and .rj_links == 732' >/dev/null <<<"$lab_status"; then exit 0; fi
    fi
    sleep 1
  done
  exit 1
   — exit 0, wall 27s
- [2026-07-24 11:50] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked --release -- --test-threads=8
- [2026-07-24 11:51] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked --release -- --test-threads=8 — exit 0, wall 70s
- [2026-07-24 11:52] [pentlift-window-rj] HEAVY START (heavy.sh, lock taken): cargo test --locked -p rtx-mcp -p rtx-nav-view --release -- --test-threads=8
- [2026-07-24 11:53] [pentlift-window-rj] HEAVY DONE (heavy.sh): cargo test --locked -p rtx-mcp -p rtx-nav-view --release -- --test-threads=8 — exit 0, wall 58s
