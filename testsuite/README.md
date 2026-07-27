# rtx live integration tests

This subtree is the portable test runner for rtx. T1 is Nano's in-repository
integration test: declarative movement scenarios are executed against a live
rtx server in the same way unit tests exercise offline code. T0 imports the
upstream Rust test result, T2 measures pacifist free play, and T3 plays one
branch-versus-reference 4on4 match on a prepared KTX server. T4 is reserved
for the frogbot ladder.

Every invocation writes one atomic JSON evidence file. The common envelope and
tier payloads are defined in [`schema/SCHEMA.md`](schema/SCHEMA.md). Dashboard
and analysis tools consume that contract rather than runner internals.

## Requirements

- Python 3.11 or newer.
- An rtx server with its TCP control channel enabled for T1 and T2.
- A server build supporting either length-framed msgpack or legacy
  newline-text control messages.
- The configured rtx checkout must be a Git worktree so the runner can record
  the full commit, branch, and dirty state.
- Server `status` must expose the cvars changed by a run under its `cvars`
  object. T2 additionally requires item availability under `items`. The runner
  refuses to mutate a rig without a cvar snapshot or to publish incomplete
  powerup statistics.

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

Import an already-produced cargo summary; the adapter does not run Cargo:

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
- `paths.evidence_dir`: destination for one JSON file per run.
- `paths.demos_dir`: reserved demo-artifact directory.
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
- `t4.*`: future frogbot endpoint, duration, and fixed skill ladder.
- `tools.qw_analyze`: future combat-lock analyzer path.

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
- `threshold.required`.
- Optional `setup.plant_links`.
- Optional `fail.fall_gate` and/or `fail.crossing`.

The generic engine performs stop, hold, teleport, goto, polling, re-goto, and
outcome classification. Outcomes are `passed`, `fell`, `timeout`, `stall`,
`loop`, `detoured`, or `died`; only a passed attempt has a time.

A `dash` scenario declares start/target coordinates, dash count, timeout, and
an informative floor. Its optional `workaround.cycle_bot_count` handles the
known post-map-change freeze. Dash color can be red in consumers, but it never
changes the T1 verdict.

`--quick` runs three attempts per goto scenario and scales each required count
with the same ratio as a full run. Quick evidence is marked `regime_note:
"quick"` and must not be compared with full runs.

The six initial DM3 scenarios live in [`scenarios/dm3`](scenarios/dm3).

## Evidence safety and conformance

The runner obtains an exclusive lock before touching a control port, snapshots
all cvars it changes, and restores them in `finally` paths. SIGINT produces an
`aborted` envelope and still runs restoration. Evidence is written to a
same-directory temporary file, flushed, and installed with `os.replace`.

`python3 testflow.py selftest` checks valid and deliberately broken fixtures
offline using the hand-written validators in `runner/checks.py`. It covers all
tiers, scenario schema compatibility, missing fields, unknown major versions,
the T2 stall invariant, and the T4 stop-at-first-loss rule.

T4 currently validates configuration and exits with an explicit E4
not-implemented message.

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
- MVD recording on with `k_demotxt_format json`: the demoinfo `.txt` written
  next to the MVD is the score oracle.

Readiness is gated in two phases: all seats alive before the match may start,
and every seat moving within the first seconds of play — KTX freezes players
during the pre-match countdown, so movement can only be proven after launch.
A failed gate writes a `failed` envelope and no score.

## Static dashboard

`dashboard/build_dashboard.py` builds one self-contained HTML file from
`rtx-testflow/1` evidence. It groups tier attempts by branch and engine
`build.digest_md5`, retains each `run_id`, and orders build groups by their
latest `started_utc`. Unknown schema majors are skipped with a warning.
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
