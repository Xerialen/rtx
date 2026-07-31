# rtx live integration tests

This subtree is the portable test runner for rtx. T1 is Nano's in-repository
integration test: declarative movement scenarios are executed against a live
rtx server in the same way unit tests exercise offline code. T0 imports the
upstream Rust test result, T2 measures pacifist free play, and T3 plays one
branch-versus-reference 4on4 match on a prepared KTX server. T4 is reserved
for the frogbot ladder.

T1 and T2 both hold a control connection, and both poll it for a ready
navmesh (`nav_preflight`) before measuring anything: a bot's planner does
nothing at all until its graph is built, so a run started right after a
spawn or a map change would otherwise score a bot that cannot walk yet as a
bot that cannot walk. The envelope's `nav` block records what the poll found —
see [`schema/SCHEMA.md`](schema/SCHEMA.md) for the field-by-field reasoning.

Every invocation writes one atomic JSON evidence file. The common envelope and
tier payloads are defined in [`schema/SCHEMA.md`](schema/SCHEMA.md). Dashboard
and analysis tools consume that contract rather than runner internals.

For an acceptance or published run, follow the phase-by-phase
[`docs/RUNBOOK.md`](docs/RUNBOOK.md). It adds the operational and semantic gates
that schema validation cannot prove: immutable SHA pins, explicit `rtx_mcp`
coverage, bot-free T2 powerup timing, full-match T3 process survival, retraction,
remote readback, demo checks, and exact rig restoration. A valid envelope is not
by itself proof that the intended measurement happened.

## Requirements

- Python 3.11 or newer.
- An rtx server with its TCP control channel enabled for T1 and T2.
- A server build supporting either length-framed msgpack or legacy
  newline-text control messages.
- The configured rtx checkout must be a Git worktree so the runner can record
  the full commit, branch, and dirty state.
- Server `status` must expose the cvars changed by a run under its `cvars`
  object. T2 additionally requires item availability under `items`. The runner
  refuses to mutate a rig without a cvar snapshot. The acceptance runbook adds
  the stricter powerup gate: a zero-take/null-average interval is right-censored,
  not a completed lay-time measurement, and analyzer values must agree with the
  independent live item observer before publication.

The runner uses only the Python standard library. Its msgpack subset is
vendored in `runner/mpwire.py`.

## Quick start

Copy the example configuration and edit it for the local rig:

```sh
cp config.example.toml config.toml
python3 testflow.py selftest
python3 testflow.py t1 --quick
python3 testflow.py t1
python3 testflow.py t2
```

A smoke T2 run can use a shorter duration. Any duration other than 600 seconds
is marked `smoke` and is not comparable with acceptance runs:

```sh
python3 testflow.py t2 --secs 60
```

Produce the summary from a cargo run, then import it; the adapter itself
does not run Cargo:

```
cargo test --workspace --release 2>&1 | python3 tools/cargo_summary.py cargo-summary.json
```

Import an already-produced cargo summary:

```sh
python3 testflow.py t0-import cargo-summary.json
```

The summary is the T0 payload shape from the contract: `modules` and
`quality_floors` are required; totals and the PASS/FAIL verdict are recomputed.
The command exits non-zero when the imported T0 verdict is FAIL.

Use `--config` before the subcommand to select another configuration:

```sh
python3 testflow.py --config rig.toml t1 --quick
```

## Configuration reference

All machine-specific values belong in the configuration file.

- `schema`: must be `rtx-testflow-config/1`.
- `server.host`: control-channel host name or address.
- `server.control_port`: control-channel TCP port. A local lock keyed by this
  port prevents concurrent runners from changing the same rig.
- `server.protocol`: `auto`, `msgpack`, or `text`. Auto probes msgpack first
  and reconnects cleanly before trying text.
- `server.demo_dir`: the server's own demo directory, readable by the runner.
  Setting it turns on MVD evidence: T1 and T2 record their runs, collect the
  demo into `paths.demos_dir`, and link every drill and metric to the moment it
  is about. Leave it empty and everything still measures, just without links.
- `paths.evidence_dir`: destination for one JSON file per run.
- `paths.demos_dir`: where recorded demos are collected, next to the evidence
  that links them.
- `build.repo_dir`: Git checkout for branch, full commit, and dirty identity.
- `t2.duration_s`: default T2 duration; 600 is the acceptance regime.
- `t3.duration_s`: match length in seconds; must equal the match server's
  timelimit times sixty — the preflight verifies this against serverinfo.
- `t3.branch_client` / `t3.reference_client`: the two `rtx-client` binaries.
  Their md5 digests bind each side's evidence to the binary that played.
- `t3.match_server`: the dedicated mvdsv+KTX instance, `host:port`.
- `t3.basedir`: Quake directory holding `qw/` and `id1/`, passed to both
  client processes.
- `t3.control_port_base`: branch client control port; reference uses base+1.
- `t3.reference_branch` / `t3.reference_commit`: what the reference client was
  built from. The commit is the operator's declaration; the digest is measured.
- `t3.demoinfo_dir`: the server's demo directory. When set, the KTX demoinfo
  JSON is the score oracle and the MVD path is recorded; when empty, the
  runner falls back to the clients' own status frags.
- `t4.*`: frogbot endpoint, duration, and fixed skill ladder.
- `t3.rig_up_cmd` / `t3.rig_down_cmd` / `t3.rig_boot_wait_s` (and the same
  keys under `[t4]`): optional on-demand rig lifecycle. The up command runs
  (shell, must succeed) before the preflight; the down command runs
  best-effort after the run, including on failure and abort — so a dedicated
  match server only exists while a run needs it. Absent keys mean the
  operator manages the rig.
- `tools.qw_analyze`: combat-lock analyzer path (T3 enrichment).
- `tools.mvd_api`: base URL of a local qw-analyze REST instance. When set, every
  metric the analyzer can read off a recorded demo comes from there instead of
  from a counter of our own, and the payload's `sources` names the origin per
  metric. `tools.mvd_cache_dir` overrides where demos are planted for it
  (default `~/.cache/qw-mvd`).

Relative filesystem values are resolved from the configuration file's
directory. The SHA-256 digest is calculated from the exact configuration
bytes. The server-reported engine digest is recorded when available.

## Scenario format

Scenario files use TOML schema `rtx-scenario/1`. Unknown schemas, tables, or
fields are errors. A file has common `name`, `map`, `kind`, and `description`
fields plus kind-specific tables.

A `goto` scenario declares:

- `run.start` and `run.target` three-dimensional coordinates.
- `attempts`, `timeout_s`, `pause_s`, `arrive_box`, and `regoto_max`.
- Optional `run.speed_ceiling` (default 850 u/s), `run.give_up_grace_s`
  (default 5 s) and `run.no_progress_s` (default 4 s; zeroes disable either
  test). An attempt ends the moment it can no longer succeed rather than when
  its clock runs out, which is what the drills spend most of their wall time on.

  Two independent ways of knowing that. **Impossible**: elapsed time plus the
  straight-line distance left over the speed ceiling exceeds the time limit plus
  the grace. The grace is flat seconds rather than a share of the limit, because
  the cost being bounded is the waiting and waiting is measured in seconds — a
  multiplier spends the most extra time on the routes that are already slowest,
  which is where it is worth least. Straight-line understates the real path and the ceiling is above
  anything the map has produced, so it only ever fires when arriving in time is
  genuinely out of reach; the attempt records `min_possible_s`, the bound it
  could not have beaten, and ends as `abandoned` rather than `timeout` — it was
  still travelling when this cut it off, which is a weaker claim than a bot
  that stopped moving or ran the clock out. **Wedged**: the bound above goes
  quiet when a bot is stuck a few units from the target, so past the time
  limit an attempt that covers less than 64 units in the window ends too, as
  `timeout`.

  Measure ground *covered*, never ground *gained*. An earlier version measured
  distance to the target and punished routes that swing wide before they close:
  `hex_quad_to_sng` went from `slow 15 s`, the number that shows how far the bot
  is from a human, to a bare `timeout`. Both tests also sit after the outcome
  classifications, so falling, dying and detouring keep their own names.

  The give-up decision itself does not change: an attempt is still cut the
  moment it can no longer succeed, not when its clock runs out, and that
  attempt might well have got there a second or two late. What changed is
  what the cut is called. A wedged attempt is still recorded as a plain
  timeout, indistinguishable in the report from one that genuinely never
  would have arrived — widen `give_up_grace_s` to buy that back, and on DM3
  the measured arrivals sit between 0.8 and 16 seconds past their limit, so
  five seconds keeps the near misses and drops the hopeless ones. An
  impossible-bound attempt no longer shares that fate: it is `abandoned`, and
  the bound it carries is the fact that would otherwise have been lost.
- Optional `run.arrive_z` (default 48). `arrive_box` bounds the square in X and
  Y; this bounds the height inside it. Without it the box is a shaft, and half
  the drills on dm3 have walkable ground on another floor inside their own
  square — the RA targets have floor 344 units below them. Zero restores the
  height-blind behaviour on purpose, for a drill asking about a place rather
  than a floor.
- Optional `run.prep_health` (default 100) and `run.prep_rockets` (default 0):
  the loadout each attempt starts from, stated rather than inherited. What the
  bot carries decides which routes the planner will even consider — a rocket
  jump is priced away for a bot that cannot fly one — so leaving it to whatever
  the bot picked up earlier in the run moves the answer a long way: the same
  drill runs 8.7 s carrying rockets and 14–25 s without.

  `prep_rockets` doubles as the permission. A drill handed none is a drill where
  the rocket jump is not sanctioned, which is every route on dm3 except the pent
  jump. Starting empty is not staying empty — the map hands out rocket boxes —
  so an attempt whose `rj_phase` leaves `Idle` on such a drill ends as
  `rocketjump`: not an arrival and not a failure to arrive, but void, because it
  answered a different question.
- `threshold.required`.
- Optional `setup.plant_links`.
- Optional `fail.fall_gate` and/or `fail.crossing`.
- Optional `requires`, see below.
- Optional `route`, see below.

The generic engine performs stop, hold, teleport, prep, goto, polling, re-goto,
and outcome classification. Outcomes are `passed`, `slow`, `fell`, `timeout`,
`abandoned`, `stall`, `loop`, `detoured`, `rocketjump`, `offroute`, or `died`.
`passed` and `slow` are the two ways of arriving and both carry a time; the
rest never arrived and carry none. `abandoned` carries `min_possible_s`
instead, the bound it could not have beaten — it is a failure to arrive, the
same as `timeout`, not a void outcome like `rocketjump` and `offroute` below.

### Route gates

An arrival records where the bot ended up and nothing about how it got there.
`spawn_lift_to_pent_to_pentmega` passed 5/5 at 0.55× the owner's own time by
stepping off the pent ledge — 270 of 582 units of descent in freefall, 5 hp of
fall damage — instead of taking the route. The endpoint was right and the run
was worthless, and every goto drill has the same hole, not a chosen few.

```toml
[route]
via = [
  { at = [1170.1, 623.9, 80.0], box = 112, name = "window" },
  { at = [1096.9, 569.1, 56.0], box = 112, name = "window ut" },
]
```

`via` is a non-empty, ordered array of waypoints: `at` (three coordinates),
`box` (the half-width of a cube centred on `at`), and `name`, all required.
It is valid only on `goto`. The runner keeps an index into `via`, starting at
0, and on every poll advances it when the bot's position is inside the box of
`via[index]` — per-axis against `box`, the same way arrival itself is tested,
never Euclidean distance. Waypoints are matched strictly in order: standing
inside `via[3]` while the index is still at 1 counts for nothing. The index is
reset per attempt, not per drill.

`via` does not replace `fail.fall_gate` or `fail.crossing` — those end an
attempt early and give it an honest name of its own, and both may still be
present alongside a route. `via` only judges an attempt that survives to the
existing `arrived` path: if the index has not reached the end of `via` by
then, the bot reached the target without taking the route, and the attempt is
`offroute` — not an arrival and not a failure to arrive, because it answered a
different question. A drill with no `[route]` table behaves exactly as before.

The waypoints themselves are anchored on points the owner actually occupied on
his own run of the route, read out of his demos — never derived from the
navmesh or from geometry, because a box built from the route it is meant to
gate would gate nothing. `scenarios/dm3/generate_from_routes.py` owns them, in
a `ROUTE_VIA` table beside `ROUTE_RUN` and `ROUTE_REQUIRES`; hand-editing a
generated drill file is undone the next time it regenerates.

### Drills the build cannot be asked

Some routes only exist in a navmesh the build has to have been given. A drill
anchored on one of those is not measuring the bot when the build lacks it — it
is measuring the absence, and a FAIL would say the bot could not walk a route
that was never in its map. So the drill names what it needs:

```toml
[requires]
capability = "navpatch:dm3-pentlift-rj"
engine_cvar = "rtx_rj_cost_scale"
note = "rutten går genom en raketskuttlänk som navpatchen planterar i pent-hissen"
```

`engine_cvar` is the witness: a cvar that ships with the capability, read off
the engine binary by `runlib.engine_declares` for the same reason the telemetry
probe is — the server's cvar table answers for names no build ever registered.
Absence is the direction that probe establishes reliably, and it is the only
direction that changes anything: `present` and `unknown` both run the drill, and
which of the two it was is recorded rather than acted on. Withholding a drill
because the binary could not be read would turn a rig problem into a silence
about the bot.

A withheld drill carries `verdict: null`, no attempts, and no times, and its
name goes into the envelope's `capabilities.unavailable` as `t1:<name>`. The two
have to agree: a drill withheld in silence would leave the column reading
`5/8 drillar` with nothing to say the eighth was never asked, and a declaration
naming a drill that ran would explain away a number the run produced. It counts
toward neither the level's verdict nor its denominator; the dashboard shows it
as `AVSTÅDD` beside `n/m drillar · 1 avstådd`.

The capability is named explicitly rather than derived from a route that turns
out to have no links, because a missing capability and a bot that cannot use one
it has are different findings and only the first is the harness's fault.

`rj_pent_to_lifts_to_window_to_quad` is the drill this exists for: it times out
5/5 on a build without the patch, unchanged when the launcher is handed 100
rockets, because there is no such route to plan.

### Timed drills

A drill may also declare `threshold.reference_time_s` and
`threshold.max_time_s`. They come as a pair — a limit without the reference it
was measured against is meaningless, and a limit faster than that reference is
rejected. The reference is the time the owner himself ran the route in; the
limit is the slowest arrival still counted as a pass.

With a limit set, arriving is no longer enough: an arrival within
`max_time_s` is `passed`, an arrival past it is `slow`. That distinction is the
whole point — a bot that crawls a route in sixteen seconds where a human takes
six has not learned the route, it has merely survived it, and without a clock
both read identically. The payload adds `arrived` (attempts that reached the
target at all) and `best_time_s` (the fastest of them) so a run can be read
without counting cells.

`scenarios/dm3/routes-v1.json` is the owner's route manifest — start and target
coordinates plus his own time, taken from his demos — and
`scenarios/dm3/generate_from_routes.py` turns it into scenario files. Regenerate
rather than hand-editing the generated drills: regeneration rewrites them whole,
and it had already wiped a hand-added loadout once. Anything the manifest does
not describe — a drill's loadout, a capability it requires — belongs in the
generator's `ROUTE_RUN` and `ROUTE_REQUIRES` tables, where it survives.

A `dash` scenario declares start/target coordinates, dash count, timeout, and
a speed floor. Its optional `workaround.cycle_bot_count` handles the known
post-map-change freeze. `threshold.informative = false` grades the dash: the
peak must reach the floor or the whole T1 run fails. An informative dash is
shown but never flips the verdict.

Drills are declared in two categories: `grunddrill` for the map's fixed
challenges (the routes and jumps a bot must own to play dm3 at all) and
`cellprov` for single-cell probes. Every scenario also names its `place` in
plain words, because a cell id tells a reader nothing. The dash is graded
against its floor unless it declares `informative = true`.

`--quick` runs three attempts per goto scenario and scales each required count
with the same ratio as a full run. Quick evidence is marked `regime_note:
"quick"` and must not be compared with full runs.

The DM3 scenarios live in [`scenarios/dm3`](scenarios/dm3). The jump drills are
anchored on demos of the owner's own runs of those routes, so the coordinates
are the ones a human actually used rather than derived from the navmesh.

## Sweeping several builds

Comparing branches means running each of them through identical work on the same
rig. `[sweep]` in the configuration declares the builds and
`python3 testflow.py sweep` walks them one at a time: install that build's
library, restart the server, run its tiers, move on. Name a target in
`[sweep].restore` and that build's library goes back on the rig at the end
whatever happened; leave it out and the rig keeps the last target deployed,
which the manifest's `restored` field then reports as null rather than
pretending otherwise.

```toml
[sweep]
deploy_to = "/…/qw-fasttrack/runtime/qw/qwprogs.so"
restart_cmd = "systemctl --user restart fasttrack-server"
boot_wait_s = 20
tiers = ["t1", "t2"]
restore = "branch"

[[sweep.target]]
label = "branch"
config = "config.toml"
library = "target/release/librtx.so"
```

The sweep refuses to start while any target's checkout has uncommitted changes,
because build identity is captured when a run starts: a commit or an edit
landing mid-sweep splits that column across two build ids, and nothing warns
you — the evidence simply arrives in two groups that cannot be compared.
`--allow-dirty` overrides it when the dirtiness is deliberate.

It writes one `sweep-<stamp>.json` manifest recording, per column, the branch,
commit, dirty flag, the md5 of the library that actually played, and every run
id it produced — plus a digest over the scenario files, which is the cheap proof
that all columns answered the same questions. A tier that fails is recorded and
the sweep continues; only an abort stops it.

## Evidence safety and conformance

The runner obtains an exclusive lock before touching a control port, snapshots
all cvars it changes, and restores them in `finally` paths. SIGINT produces an
`aborted` envelope and still runs restoration. Evidence is written to a
same-directory temporary file, flushed, and installed with `os.replace`.

`python3 testflow.py selftest` checks valid and deliberately broken fixtures
offline using the hand-written validators in `runner/checks.py`. It covers all
tiers, scenario schema compatibility, missing fields, unknown major versions,
the T2 stall invariant, the T4 stop-at-first-loss rule, and the evidence
lead-in below.

## Demo evidence

Every number the dashboard states should be checkable, so each one carries a
demo-player link that opens three seconds before the moment it is about, on the
POV of the bot it is about. The lead-in is part of the contract rather than a
detail of the URL builder: `checks.py` recomputes it from `at_s` and the link's
`from` and rejects an envelope whose links do not open when they claim to.

`from` is whole seconds because the player parses it with `parseInt` — a
fractional value truncated silently and the URL then claimed a start the player
never honoured. Truncating downwards keeps the lead at or above three seconds
rather than dropping under it.

T1 links a representative attempt per drill and the dash. T2 links the whole
demo, every stall cell, and every metric: what the analyzer measured is linked
to the event it read (a quad being taken), and what the runner measured itself
is linked to the sample that produced it — the fastest 100 ms sample, the
fastest second, the longest stand-still, and the first firing in the zone that
stalled most. T3 and T4 link each player's POV from the match card.

The userid behind `track` is read out of the MVD itself: `svc_updateuserinfo`
(svc 40) carries `[slot][int32-LE userid][\\name\\...]`, and QuakeWorld's
readable-character table turns the bots' fun-char names back into the names on
the scoreboard.

## T4: the frogbot ladder

`python3 testflow.py t4` climbs skills 10, 12, 14, 16, 18, 20 against
server-side frogbots on the `[t4].frogbot_server` KTX instance: one 4on4
match per rung with the same branch build T3 uses, advancing on a win and
stopping at the first loss or draw. The run is `COMPLETE` whenever the
observed ladder obeys those rules — how far it climbs is a sporting result,
not a pipeline verdict.

Rig preparation is the T3 recipe plus frogbots: `k_fb_enabled 1` and the KTX
`bots/` data directory present in the private gamedir. Frogbots cannot be
seated over rcon (`botcmd` is a client console command), so the runner keeps a
spectator client connected across the ladder and seats them through its
control channel (`runcmd botcmd addbot <skill> <team>`). KTX echoes each
seated bot's skill to that spectator's console; the runner verifies four
matching echoes per rung and records the method in `skill_verified_by`. The
KTX demoinfo JSON is the only score oracle for T4 — `[t4].demoinfo_dir` is
required.

Ordering is dictated by KTX, not by the runner: a bot added during a
countdown or match is accepted but never enters, and the frogbots auto-ready
the instant they seat — so each rung seats the frogbots first, and the player
squad joins inside the countdown their ready-up starts. Three usermode
settings make that survivable (put them with the other overrides):
`k_membercount 4` (at 3, KTX silently refuses the fourth frogbot),
`k_lockmode 0` (KTX's own mode re-initialisation stamps it back after every
match, and locked mode silently drops the players as they join), and
`k_count 45` — the rtx clients build their navmesh after joining, and with a
stock 20 s countdown the match begins before the mesh exists, leaving the
squad standing at spawn.

## Combat lock (T3 enrichment)

When `[tools].qw_analyze` points at a qw-analyze binary and the T3 match MVD
is readable, the runner analyzes the demo and fills `combat_lock` in the T3
payload: seconds per bot spent under enemy fire, with the attacker in the
bot's field of view, without answering fire. The shot signal is an
ammo-counter decrease (qw-analyze v21 emits no dedicated shot stream);
respawn resets are filtered by their multi-stream signature. Analysis failure
leaves `combat_lock` null — it never fails a measured match.

## T3: branch versus reference

`python3 testflow.py t3` launches two `rtx-client` processes — the branch
build and the reference build, `seats_per_side` bots each on their own team —
against the configured match server, and writes one `PIPELINE-OK` envelope
with per-side movement statistics and the final score. A branch quality
verdict is never taken from a single match; that belongs to a `T3-agg`
aggregate over two or more replicates with side alternation.

The runner does not manage the server. The operator prepares a dedicated
mvdsv+KTX instance — never a shared lab server, since the runner refuses to
start unless the server is idle in Standby with the exact mode and timelimit:

- A private gamedir (copy the `configs/` tree and root `.cfg` files, symlink
  `qwprogs.so`) so nothing shared is edited.
- Default usermode `<n>on<n>` matching `seats_per_side`, match mode
  (`k_matchless 0` — matchless forces `teamplay 0`).
- The timelimit must survive KTX's mode re-initialisation: KTX stamps its own
  mode default *after* the boot cfg runs, so put `timelimit <min>` in the
  private `configs/usermodes/<mode>/default.cfg`, which KTX executes last.
- `k_noframechecks 1` (headless clients trip the illegal-FPS check),
  `k_lockmode 0` (network clients may join), no frogbots, no master servers.
- Audit the whole reset chain, not only the boot cfg: KTX re-execs
  `server.cfg`, `mvdsv.cfg`, `ktx.cfg` and `pwd.cfg` after every match, and a
  stock `ktx.cfg` sets `k_noframechecks 0` — the first match after boot then
  works while every later match anti-cheat-kicks all headless clients ~90 s
  in. `pwd.cfg` likewise resets `rcon_password`, a stock `mvdsv.cfg` turns
  `sv_crypt_rcon` back on and points `sv_demodir` somewhere else — after the
  first reset the score oracle silently reads an empty directory. The private
  `configs/usermodes/<mode>/default.cfg` is the one file KTX executes after
  both the reset chain and its own mode re-initialisation — put every
  rig-critical override there (`timelimit`, `k_lockmode 0`,
  `k_noframechecks 1`, `k_membercount`, `k_count 45`, `k_overtime 0`,
  `k_exttime 0`, `rcon_password`, `sv_crypt_rcon 0`, `sv_timeout 30`,
  `sv_demodir`). The overtime pair is the sneakiest: a stock `ktx.cfg`
  re-stamps `k_overtime 1` / `k_exttime 3`, which stays invisible until the
  first drawn match — then overtime blows straight through the runner's
  match-end window and the run fails as "match did not finish".
- `maxclients 16` and `sv_timeout 30`: torn-down headless clients leave
  ghosts (they have no disconnect path), and with tight slots a ghost pushes
  the next run's fourth bot onto a spectator slot, where it silently never
  plays. Roomy slots plus a fast reap keep reruns clean.
- MVD recording on with `k_demotxt_format json`: the demoinfo `.txt` written
  next to the MVD is the score oracle.

Readiness is gated in two phases: all seats alive before the match may start,
and every seat moving within the first seconds of play — KTX freezes players
during the pre-match countdown, so movement can only be proven after launch.
A failed gate writes a `failed` envelope and no score.

## Static dashboard

After a build, `dashboard/verify_against_evidence.py <index-file>` reads the
RUNS JSON back out of the built page and compares it field by field against
the raw envelopes the index file names (`t0 evidence/t0-....json` per line) —
the same direction of proof as the demo readback: never trust the pipeline,
read the output. The only presentation transforms it accepts are the Swedish
verdict labels and the ladder's padded unplayed rungs; everything else must
match exactly.

`dashboard/build_dashboard.py` builds one self-contained HTML file from
`rtx-testflow/1` evidence. It groups tier attempts by branch and build
commit — tiers legitimately hash different artifacts (engine library for
T1/T2, client binary for T3/T4), so the commit is the only cross-tier build
identity; the per-artifact `digest_md5` values are retained as group detail,
and a dirty-tree run forms its own `<commit>-dirty` group. Each `run_id` is
retained, and build groups are ordered by their latest `started_utc`. Unknown schema majors are skipped with a warning.
Missing tiers and `failed`/`aborted` envelopes are shown as run state; their
partial payloads are never used as data.

Build from a synced evidence directory:

```sh
python3 dashboard/build_dashboard.py \
  --evidence-dir /path/to/evidence \
  --output dashboard.html
```

The result has no network dependencies. Map geometry is loaded at build time
from `dashboard/assets/maps/<map>/graph.json` and `entities.json`. T2/T3
telemetry cells come only from complete evidence payloads. The EX marker is
controlled only by envelope `provenance: synthetic`; derived values retain
their `*_source` reference. Runs marked `quick` or `smoke` are displayed but
are not compared with full-regime runs.

The offline golden set covers a complete synthetic T0–T4 flow, missing and
null fields, an unknown branch, a failed run, T3 aggregate semantics, and an
unknown schema major:

```sh
python3 dashboard/build_dashboard.py --selftest
python3 dashboard/build_dashboard.py \
  --evidence-dir dashboard/fixtures \
  --output /tmp/rtx-dashboard-fixture.html
```
