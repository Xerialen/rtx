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
      "category": "grunddrill",
      "place": "RA-ingången → RA-plattan (uppför trapporna)",
      "attempts": [
        {"status": "passed", "time_s": 5.37, "demo_t_s": 12.5},
        {"status": "slow", "time_s": 9.02, "demo_t_s": 27.4},
        {"status": "fell", "time_s": null, "demo_t_s": 41.0}
      ],
      "threshold": {"required": 10, "of": 10,
                    "reference_time_s": 5.95, "max_time_s": 6.66},
      "passed": 9,
      "arrived": 10,
      "best_time_s": 5.37,
      "verdict": "FAIL",
      "evidence": {
        "demo": "t1-....mvd", "attempt": 2, "status": "fell", "at_s": 41.0,
        "link": "/demo-player/?demoUrl=%2Fdemos%2Ft1-....mvd&map=dm3&duration=500&from=38.0&track=1"
      }
    }
  ],
  "dash": {
    "peaks": [527, 609], "peak": 609, "floor": 790, "informative": false,
    "verdict": "FAIL", "place": "100m, rak bana", "evidence": { }
  },
  "demo": "t1-....mvd",
  "verdict": "FAIL"
}
```
- `attempts[].status`: `passed|slow|fell|timeout|stall|loop|detoured|died`. `time_s`
  exactly for the two arriving statuses (`passed`, `slow`), else null. Attempt count
  and order are preserved. `demo_t_s` is the moment the attempt began on the run's
  own demo clock. `timeout` covers both running the clock out and being cut short
  once past the time limit with the bot no longer moving (`run.no_progress_s`);
  either way it did not arrive.
- `threshold.reference_time_s` and `threshold.max_time_s` are optional and come as a
  pair: the time the owner ran the route in, and the slowest arrival still counted as
  a pass (a limit faster than its own reference is rejected). With them set, an
  arrival within `max_time_s` is `passed` and an arrival past it is `slow` — the
  difference between owning a route and merely surviving it. `arrived` counts every
  attempt that reached the target and `best_time_s` is the fastest of them; both are
  null-safe on drills with no timing.
- `category` splits the map's fixed challenges (`grunddrill`) from single-cell
  probes (`cellprov`); `place` says in plain words where on the map it happens,
  because a cell id tells a reader nothing.
- `evidence` links the attempt worth watching — the first failure, else the first
  pass — opening the demo player three seconds before it, POV on the drilling bot.
  Null when the rig has no readable demo directory.
- Run `verdict` = PASS iff ALL scenario thresholds hold AND the dash clears its
  floor when it is graded (`informative: false`). An informative dash carries
  `verdict: null` and never flips the run.
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
    "polls": 5996, "bots": 4
  },
  "sources": {"quad_takes": "qw-analyze/items", "quad_lay_avg": "qw-analyze/items"},
  "cells": [ {"id": "m4569", "pos": [1984,-288,-72], "n": 350, "reasons": {"displacement": 350},
              "links": {}, "evidence": { }} ],
  "demo": "t2-....mvd",
  "evidence": {"demo": "t2-....mvd", "at_s": 0.0, "link": "/demo-player/?..."},
  "moments": [ {"demo": "t2-....mvd", "metric": "quad_takes", "at_s": 33.2,
                "who": "bot.ref1", "link": "/demo-player/?..."},
               {"demo": "t2-....mvd", "metric": "speed_100ms", "at_s": 271.4,
                "detail": "snabbaste 100 ms-provet", "link": "/demo-player/?..."} ],
  "verdict": "MEASURED"
}
```
- 600 s is the acceptance regime; 300 s runs set `"regime_note": "smoke"` and are
  never compared against 600 s runs.
- Anything the analyzer can read off the recorded demo comes from the analyzer,
  not from a counter of our own, and `sources` names the origin per metric.
  Availability is a property of the demo: a non-KTX rig has no demoinfo block, so
  those metrics keep our own measurement and stay out of `sources`.
- `evidence` opens the whole demo and every metric in `stats` that can be tied to a
  moment gets one in `moments`, keyed by metric name: what the analyzer read comes
  from the event it read (a powerup taken), what the runner measured itself comes
  from the sample that produced it — the fastest 100 ms sample, the fastest second,
  the longest motionless stretch, and the first firing in the busiest stall zone.
  `detail` says in plain words which sample it is. `cells[].evidence` opens the first
  firing of each stall zone. Speed over the 100 m profile belongs to T1's dash,
  not here.
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
  "scoreboard": {
    "teams": [{"name": "brch", "frags": 87}, {"name": "ref", "frags": 74}],
    "players": [ {"name": "bot.brch1", "team": "brch", "frags": 12, "deaths": 9,
                  "dmg_given": 3120, "dmg_taken": 2890, "speed_max": 512.4,
                  "speed_avg": 246.1, "spree_max": 3, "link": "/demo-player/?..."} ],
    "map": "The Abandoned Base", "duration_s": 300, "demo": "....mvd",
    "source": "qw-analyze/demoinfo", "link": "/demo-player/?..."
  },
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
    {"skill": 10, "frags_for": 61, "frags_against": 45, "win": true, "mvd": "...",
     "scoreboard": { }},
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
- Every played rung carries the same `scoreboard` card as T3: the match as the
  KTX scoreboard saw it, with a POV link per player. Null when no analyzer or no
  demoinfo block was available for that match.

## Demo evidence

Numbers nobody can look at are hard to trust, so a tier that can record its own
match does: the runner asks the server for an MVD, keeps the demo clock, and
emits links that open the hub demo player three seconds before the moment in
question with the POV locked to the bot the number is about.

- Links are host-relative (`/demo-player/?demoUrl=%2Fdemos%2F<file>&map=<map>&duration=<s>&from=<s>&track=<userid>`),
  so the same evidence file works on the LAN hub and behind the gated dashboard.
- `from` is whole seconds — the player parses it with `parseInt`, so a fractional
  value would truncate silently and the URL would claim a start it never honours.
  It is truncated downwards, which keeps the lead at or above three seconds. The
  conformance checks recompute the lead from `at_s` and reject any link that does
  not open when it claims to.
- `track` is an FTE userid, read out of the demo's own `svc_updateuserinfo`
  records — no analyzer endpoint exposes it.
- Demo file names in links follow the publisher's URL-safe rewrite, so a KTX
  demo called `4on4_a_vs_b[dm3]….mvd` is linked as `4on4_a_vs_b_dm3_….mvd`.
- All of it is best effort: a rig without a readable demo directory still
  measures everything, it just cannot show its work, and every evidence field
  is then null.

## Fixtures

`schema/fixtures/` holds one valid example per tier + deliberately broken ones
(bad major version, missing field, violated T2 invariant, T4 ladder that continues
after a loss). `runner/selftest.py` validates all fixtures with the hand-rolled
checkers — this is the schema conformance suite and runs offline in CI.
