# rtx live integration tests

This subtree is the portable test runner for rtx. T1 is Nano's in-repository
integration test: declarative movement scenarios are executed against a live
rtx server in the same way unit tests exercise offline code. T0 imports the
upstream Rust test result, while T2 measures pacifist free play. T3 and T4 are
reserved for later match orchestration stages.

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
- `t3.*`: future branch/reference clients, duration, and seat count.
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

T3 and T4 currently validate configuration and exit with explicit E3/E4
not-implemented messages.
