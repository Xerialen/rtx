# rtx-testflow result contract — v1

Every tier run writes exactly ONE JSON file to the evidence directory. The dashboard
is built ONLY from these files plus the versioned map assets checked into
`dashboard/assets/maps/<map>/`. Writers write atomically: temp file in the same
directory, then `os.replace()`. Readers reject any file whose `schema` major version
they do not know.

Minimum Python: 3.11 (tomllib). No third-party dependencies; the msgpack control-wire
codec is vendored in `runner/mpwire.py`.

## Envelope (all tiers)

```json
{
  "schema": "rtx-testflow/1",
  "run_id": "t1-20260727T190000Z-a2e70596",
  "tier": "T1",
  "status": "complete",
  "started_utc": "2026-07-27T19:00:00Z",
  "ended_utc": "2026-07-27T19:08:12Z",
  "map": "dm3",
  "build": {
    "branch": "testsuite",
    "commit": "a2e705968829275671172da8961ee1bbc01871fa",
    "digest_md5": "7fc55d7e",
    "dirty": false
  },
  "config_digest": "sha256:...",
  "runner_version": "0.1.0",
  "provenance": "measured",
  "payload": { }
}
```

- `run_id`: `<tier>-<startstamp>-<commit8>`; unique per attempt, stable for grouping.
- `status`: `complete` | `failed` | `aborted` (SIGINT/timeout). Non-complete files
  keep whatever partial payload exists plus an `error` string; the dashboard shows
  them as failed runs, never as data.
- `provenance`: `measured` (came from a live run), `derived` (computed from measured
  inputs; the computation is named in the field that carries it), `synthetic`
  (example data). The dashboard's EX-marking is driven by THIS FIELD ONLY.
- `build.commit` is the full hash; `digest_md5` is the short display id of the
  engine binary actually running (server-reported), `dirty` from git status at build.
- For T3 the envelope `build` is the branch-under-test; the reference build lives in
  the payload side entry.

## T0 payload (import adapter — cargo runs stay upstream)

```json
{
  "modules": [ {"name": "combat", "tests": 58, "passed": 58} ],
  "total": {"tests": 313, "passed": 313},
  "quality_floors": [ {"name": "bhop corpus 100m", "floor": 795, "unit": "u/s", "passed": true} ],
  "verdict": "PASS"
}
```
`verdict` = PASS iff every module passes and every floor holds. `testflow.py all`
stops before T1 when T0 import is missing or FAIL.

## T1 payload (scenario drills)

```json
{
  "scenarios": [
    {
      "name": "ra_climb",
      "attempts": [ {"status": "passed", "time_s": 5.37}, {"status": "fell", "time_s": null} ],
      "threshold": {"required": 10, "of": 10},
      "passed": 9,
      "verdict": "FAIL"
    }
  ],
  "dash": {"peaks": [527, 609], "peak": 609, "floor": 800, "informative": true},
  "verdict": "FAIL"
}
```
- `attempts[].status`: `passed|fell|timeout|stall|loop|detoured|died`. `time_s` only
  for `passed`, else null. Attempt count and order are preserved.
- Run `verdict` = PASS iff ALL scenario thresholds hold (dash is informative: shown
  red under floor but does not flip the verdict — matches suite.py semantics).
- A quick run (3 attempts) scales thresholds like suite.py and MUST set
  `"regime_note": "quick"`; quick runs are never compared against full runs.

## T2 payload (pacifist free-play)

```json
{
  "duration_s": 600,
  "regime_note": null,
  "stats": {
    "quad_takes": 4, "quad_lay_avg": 8.1,
    "pent_takes": 1, "pent_lay_avg": 2.5,
    "speed_1s": 182.0, "speed_100ms": 182.4,
    "still_s_per_bot": 226.6, "stall_firings": 687,
    "polls": 5996, "bots": 4, "peak_100m": null
  },
  "cells": [ {"id": "m4569", "pos": [1984,-288,-72], "n": 350, "reasons": {"displacement": 350}, "links": {}} ],
  "verdict": "MEASURED"
}
```
- 600 s is the acceptance regime; 300 s runs set `"regime_note": "smoke"` and are
  never compared against 600 s runs.
- `peak_100m` is null unless a dash test ran for this build; when present it is
  `derived` from the T1 dash and says so: `"peak_100m_source": "<t1 run_id>"`.
- `still_s_per_bot` divides by `stats.bots` (recorded, not assumed 4).
- Invariant, checked by the writer: `stall_firings == sum(cells[].n) == sum over
  cells of sum(reasons.values())`. Violation ⇒ `status: "failed"`.
- T2 has no thresholds: `verdict` is always the literal `MEASURED`.

## T3 payload (branch vs reference, 4on4)

```json
{
  "duration_s": 300,
  "sides": [
    {"side": "branch", "build": {"branch": "...", "commit": "...", "digest_md5": "...", "dirty": false},
     "frags": 87, "stats": { }, "cells": [ ]},
    {"side": "reference", "build": { }, "frags": 74, "stats": { }, "cells": [ ]}
  ],
  "result": {"diff": 13, "winner": "branch", "oracle": "ktx-scoreboard", "mvd": "demos/....mvd"},
  "readiness": {"seats_ok": 8, "gate": "heartbeat+movement", "passed": true},
  "combat_lock": null,
  "replicate_of": null,
  "verdict": "PIPELINE-OK"
}
```
- `oracle` names the authoritative score source; the MVD path is kept for audit.
- Readiness gate before start: every seat shows heartbeat + movement, else the run
  fails — no match data is written as `complete` without `readiness.passed`.
- A single 5-minute match proves the pipeline (`verdict: "PIPELINE-OK"`). A branch
  quality verdict (`"PASS"|"FAIL"`) is only allowed on an aggregate file
  (`"tier": "T3-agg"`) over ≥2 replicates with side/spawn alternation.
- `combat_lock`: null until the MVD analysis ran; then
  `{"s_per_bot": {"branch": 51.8, "reference": 58.4}, "source": "qw-analyze", "version": "..."}`.

## T4 payload (frogbot ladder)

```json
{
  "duration_s_per_match": 300,
  "ladder": [
    {"skill": 10, "frags_for": 61, "frags_against": 45, "win": true, "mvd": "..."},
    {"skill": 12, "frags_for": 54, "frags_against": 49, "win": true, "mvd": "..."},
    {"skill": 14, "frags_for": 42, "frags_against": 50, "win": false, "mvd": "..."}
  ],
  "reached": 12,
  "skill_verified_by": "server-log addbot sequence",
  "verdict": "COMPLETE"
}
```
- Ladder stops at the first loss; `reached` = highest beaten skill, 0 on immediate
  loss. Rungs are 10,12,14,16,18,20.
- The ladder LOGIC is proven by fixtures (immediate loss, full climb, abort, draw);
  a live run is `COMPLETE` whenever the observed ladder obeys the rules, regardless
  of sporting outcome.
- Draw rule: a draw does not advance the ladder and stops it (recorded as
  `"win": false, "draw": true`).

## Fixtures

`schema/fixtures/` holds one valid example per tier + deliberately broken ones
(bad major version, missing field, violated T2 invariant, T4 ladder that continues
after a loss). `runner/selftest.py` validates all fixtures with the hand-rolled
checkers — this is the schema conformance suite and runs offline in CI.
