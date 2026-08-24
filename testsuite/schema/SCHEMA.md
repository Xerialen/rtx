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
- `capabilities` is optional and present only when the build under test could not be
  asked about something the tier normally reports:

  ```json
  "capabilities": {
    "telemetry": false,
    "unavailable": ["stall_firings", "cells"],
    "note": "build exposes no rtx_telemetry cvar; stall events are not emitted"
  }
  ```

  Absence of the block means everything the tier needed was available, which is the
  common case. A number a run could not measure is `null`, never `0` — a build with
  no stall instrumentation would otherwise sit at the top of the column precisely
  because it cannot see. Detection reads the **engine binary** for the cvar name
  (`runlib.engine_declares`), not the server's cvar table: the control layer's Get
  answers for any name a build never registered, and mvdsv's console `set` creates
  an unknown cvar, so a rig config that does `set rtx_telemetry 1` at boot
  manufactures the cvar on a build that has no such thing. The binary is checked
  only when its md5 matches what the server reports it is running; otherwise the
  answer is *unknown* and nothing is declared, because unknown and absent are
  different answers and only one of them is a finding. An empty `unavailable` or a
  blank `note` is rejected: the block exists to explain an absence, so an
  unexplained one is worse than none.
- `nav` is required on every complete T1 or T2 envelope and rejected on every other
  tier. Those two are the tiers that measure a bot against exactly one graph. T0 never
  connects at all; T3 and T4 do connect, but to two client builds at once, and each
  side builds its own navmesh after joining — a single block beside a single `build`
  could not say which side's graph it described, so rather than stamp an ambiguous
  one they stamp none.

  ```json
  "nav": {
    "map": "dm3",
    "state": "ready",
    "cells": 4634,
    "links": 36956,
    "rj_links": 2021,
    "waited_s": 0.0
  }
  ```

  `map` must equal the envelope's own `map`: a ready graph for another map is the
  wrong graph, and a drill measured against it would fail for a reason that has
  nothing to do with the bot. `state` is always `"ready"` in a written envelope — the
  runner refuses to measure otherwise, so any other value here is a stamp nothing
  produced. A non-complete envelope (`failed`/`aborted`) may still carry one if the
  run died after the preflight passed, or carry none if it died during the preflight,
  before there was anything to stamp. `cells` and `links` are positive integers: a
  ready graph reporting zero cells is not a graph, which is the concrete case the "a
  value that could not be measured is null, never 0" rule exists for here — a graph
  that never finished building must not be able to sit at zero and read as measured.
  `rj_links` is `>= 0`; a build with no rocket-jump links is a legitimate build and
  must not be rejected for having none. `waited_s` is `>= 0`, how long the preflight
  polled before the graph was ready — provenance for the stamp itself, not a
  measurement of anything the bot did, and it tells a reader whether the rig was hot
  or cold when the numbers were taken.

  The block exists because the control layer's `navmesh` status is transient on both
  ends of "not ready": immediately after a spawn or a map change it reads `"building"`
  with `cells`/`links`/`rj_links` all at zero, and `"none"` means no build is in
  flight *yet* — both look exactly like a broken build if read once. Reading `status`
  a single time and judging the answer would treat "not yet" the same as "never
  will", so `nav_preflight` polls about once a second until `navmesh == "ready"` and
  the map matches, and only the deadline — 120 s, twice the engine's own MCP helper's
  wait for the same condition, because a loaded lab rig is slower than a developer's
  laptop — is a verdict. `nav` is that verdict, stamped once the poll succeeds; it is
  what lets two runs with different graphs be told apart after the fact instead of
  being silently compared as if they had measured the same map knowledge.

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
      "name": "ralow_to_ratop",
      "category": "grunddrill",
      "place": "RA låg → RA",
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
- `attempts[].status`: `passed|slow|fell|timeout|abandoned|stall|loop|detoured|rocketjump|offroute|died`.
  `time_s` exactly for the two arriving statuses (`passed`, `slow`), else null. Attempt count
  and order are preserved. `demo_t_s` is the moment the attempt began on the run's
  own demo clock. `timeout` now covers two ways of not arriving: running the
  clock out, and being cut short past the time limit with the bot no longer
  moving. A cut attempt is indistinguishable from one that never would have
  arrived, which is the price of not waiting — `run.give_up_grace_s` sets how
  many seconds past the limit the bot is still allowed to aim for.
  `abandoned` is the third way an attempt can end early, and it is not folded
  into `timeout`: it is cut the moment reaching the target in time becomes
  impossible, while the bot is still travelling. That is a weaker claim than a
  timeout — the bot might well have arrived a second or two late — so it reads
  as "we stopped this" rather than "it failed", and it carries `min_possible_s`,
  the bound it could not have beaten, so the fact that was cut short is visible
  instead of collapsing into an ordinary non-arrival. `min_possible_s` is
  present exactly for `abandoned` attempts. `rocketjump` is neither an arrival
  nor a failure to arrive, and neither is `abandoned` a member of that group:
  the drill handed the bot no rockets, so the jump was not sanctioned on that
  route, and the bot picked some up and took it anyway. The attempt answered a
  different question and counts as void. `offroute` is the same shape, for a
  scenario carrying a `[route]` table: the bot reached the target without
  passing all of its waypoints, in order, on the way — it answered where,
  never how, and the attempt is void rather than a pass or a failure to
  arrive.
- A scenario may carry `requires` instead of a verdict:

  ```json
  "verdict": null,
  "attempts": [],
  "threshold": {"required": 4, "of": 0},
  "passed": 0, "arrived": 0, "best_time_s": null, "evidence": null,
  "requires": {
    "capability": "navpatch:dm3-pentlift-rj",
    "engine_cvar": "rtx_rj_cost_scale",
    "state": "absent",
    "note": "the route runs through a rocket-jump link the navpatch plants"
  }
  ```

  The route only exists in a navmesh this build was not given, so the drill was
  never run. `state` is `present|absent|unknown`, read off the engine binary the
  same way `capabilities` is; only `absent` withholds a drill, and `unknown`
  runs it, because a binary we could not read is a rig problem and must not
  become a silence about the bot. A withheld drill is graded by nobody: it
  counts toward neither `verdict` nor the dashboard's denominator, and a FAIL
  would have said the bot could not walk a route that was never in its map.
  A drill that ran keeps its `requires` block too, so a reader knows the graded
  columns were graded against the same map.

  A withheld drill MUST be named in `capabilities.unavailable` as `t1:<name>`
  and a graded one MUST NOT be — the drill and the envelope have to tell the
  same story, or the column reads `5/8 drillar` with nothing to say the eighth
  was never asked.
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
- Run `verdict` = PASS iff ALL graded scenario thresholds hold AND the dash
  clears its floor when it is graded (`informative: false`). An informative dash
  carries `verdict: null` and never flips the run, and neither does a withheld
  drill — the level's verdict is about the bot, and a missing navmesh capability
  is a question about the build.
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
              "kinds": {"offroute": 350}, "links": {}, "evidence": { }} ],
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
  cells of sum(reasons.values()) == sum over cells of sum(kinds.values())`.
  Violation ⇒ `status: "failed"`.
- `cells[].kinds` is the LinkKind of the route leg in force per firing
  (`JumpGap`, `Walk`, `SpeedJump`, `Step`, …), with `offroute` for a bot that
  held no leg. `reasons` says which watchdog fired; `kinds` says what the bot
  was traversing. A pricing change on one link kind is invisible without it —
  the margin-tax measurement had to be a hand-written probe for exactly this
  reason.
- `stall_firings` is `null` with `cells: []` when the envelope declares
  `capabilities.telemetry: false`, and the invariant is then read the other way
  round: nothing was counted, so nothing may appear on the map. A null without that
  declaration is rejected, and so is a count alongside it — the first hides a
  dropped measurement, the second claims one the build could not make. Everything
  else T2 reports comes from `status` polling and the analyzer and is unaffected.
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
  "t4_schema": 2,
  "duration_s_per_match": 300,
  "ladder": [
    {"skill": 10, "frags_for": 61, "frags_against": 45, "win": true, "mvd": "...",
     "scoreboard": { },
     "measured": {"shots_fired": 402, "teamkills": 8, "kills": 45,
                  "still_s_per_bot": 18.4, "still_gap_max_s": 1.8,
                  "item_takes": 27, "items_poll_gap_max_s": 1.2,
                  "items_tracked": 42}},
    {"skill": 12, "frags_for": 54, "frags_against": 49, "win": true, "mvd": "...",
     "measured": { }},
    {"skill": 14, "frags_for": 42, "frags_against": 50, "win": false, "mvd": "...",
     "measured": { }}
  ],
  "reached": 12,
  "skill_verified_by": "client console skill echo (KTX addbot)",
  "verdict": "OK",
  "measurements": {"shots_fired": 1206, "teamkills": 24, "kills_total": 135,
                   "still_s_per_bot_max": 18.4, "item_pickups": 81},
  "sampling": {"still_interval_s": 1.0, "still_gap_max_s": 1.8,
               "items_poll_s": 1.0, "items_poll_gap_max_s": 1.2},
  "thresholds": {"teamkill_share_max": 0.2, "still_s_per_bot_max": 75.0,
                 "item_pickups_min": 1, "still_sample_interval_s": 1.0,
                 "still_sample_gap_max_s": 3.0, "items_poll_s": 1.0,
                 "items_poll_gap_max_s": 3.0},
  "dom": {"failed_gates": [], "missing": [], "labels": ["item-pickups-proxy"],
          "reason": "spelad stege; alla fyra fält mätta och gröna"}
}
```
- Ladder stops at the first loss or draw; `reached` = **highest won** skill, 0 when
  nothing was won. A draw after a won rung N reaches N. Rungs are 10,12,14,16,18,20.
  The validator recomputes `reached` from the rungs and fells a mismatch.
- `verdict` is one of five: `VINST` (won level 20), `OK` (played and lost, all four
  fields measured and green), `FAIL` (a **measured** gate fell — beats every other
  value, draw included), `OMÄTT` (nothing fell but a field is unavailable; never
  green, never OK), `OAVGJORD` (draw with all four measured and green; carries
  `"draw_semantik": "ägarbeslut saknas"`). A `FAIL` carries `cross_alarm`: the
  nearest preceding T1/T3 `run_id` of the same commit, or the literal
  `"no matching T1/T3 run found"` — a documented heuristic, never a proven link.
- The four gates, judged only on measured fields: (a) `shots_fired == 0`,
  (b) `teamkills / max(1, kills_total) > 0.20`, (c) `still_s_per_bot_max > 75.0`,
  (d) `item_pickups == 0` over the whole ladder. The thresholds are calibrated
  against the existing corpus, copied into the envelope, and pinned: a run that
  restates its own gate is refused.
- **`t4_schema` is the measurement contract, and the bump is the compatibility
  mechanism.** `2` is the five-value verdict with optional `sources`; `3` makes
  `sources` mandatory and binds every KTX-sourced number to its card. Envelopes
  written under 2 keep being judged by 2's rules — nothing is kept lenient for
  new runs in order to spare old ones.
- **From contract 3, a KTX-sourced number carries its card.** The rung's `card`
  block is `{"path": "demos/<file>", "sha256": "<64 hex>"}`, the path is
  relative to the envelope's own directory and may not leave it (no absolute
  path, no `..`, no backslash). The validator resolves it, hashes the bytes
  against the pin, and **recounts `shots_fired`/`teamkills`/`kills` out of those
  bytes**; a card that cannot be found, does not hash, does not parse, or does
  not produce the reported number fells the envelope. A rung that carries a card
  no measurement sourced is refused too. The runner archives the card into
  `evidence/demos/` itself, and drops any KTX reading it could not archive
  rather than reporting a number with no provenance.
- **Two sources, in order (addendum to v6 §3, 2026-08-24).** `shots_fired` and
  `teamkills` come first from the MVD (the ammo signal and the qw-analyze card)
  and, when the MVD is missing or empty, from **KTX's own demoinfo card** — the
  same file the frag oracle already reads. Each rung names what it used in
  `sources` (`mvd/ammo`, `qw-analyze/card`, `ktx/demoinfo`); a measured field
  without a source, a source without a measurement, or a source outside that
  vocabulary is refused. A rung carrying a qw-analyze card that derives the
  pair must use it, so the validator can always recount that path.
  On the KTX card the counters are read outright: `weapons.<w>.acc.attacks`
  for shots and `stats.tk` for teamkills — **not** `kills - frags - suicides`.
  The reason is narrow: the identity `frags = kills - tk - suicides` holds on
  15 of the 16 player rows across the evening's two cards, and the one row
  that breaks it (`bot.brch3`, `-8` against a derived `-7`) is the whole
  difference between a derived 11 and a counted 10 at team level. When a row
  breaks the identity the derivation cannot carry the number and the direct
  counter can — and here the derivation's 11 teamkills on 1 kill is refused by
  the guard above, leaving no number at all. `tk > kills` is an ordinary
  reading on that source, not a malformed one: `kills` counts enemy kills and
  `tk` counts team kills. Both readings fell gate (b) far over threshold, so
  the choice does not change a verdict. A zero shot count is believed
  only after the card has been shown to carry accuracy at all: KTX omits `acc`
  for a weapon never fired, so a card with no accuracy anywhere is unavailable.
- `teamkills` from the qw-analyze card is `kills - frags - suicides` for team
  `brch`, and is **unavailable** — never a number — when any component is
  missing, non-numeric or negative, or when the derived count exceeds the
  team's own kills. Five real cards in the corpus derive more teamkills than
  kills (`frags` goes negative), which would put gate (b) above 1.0 on an
  ordinary match. When a rung carries its card, the validator recounts the pair
  off it and refuses a rung that reports something else.
- Every unmeasured field is named in `dom.missing` **and** in
  `capabilities.unavailable` (`t4:shots_fired`, `t4:teamkills`, `t4:still_s`,
  `t4:item_chase`). It is never a numeric zero. `item_pickups` is a proxy — the
  world item channel says an item was taken, not by whom — so a judged (d) outcome
  always carries the `item-pickups-proxy` label.
- `measured.items_tracked` is how many distinct items the world channel could
  identify at all. No gate reads it: it is the receipt that says whether gate
  (d) is measuring the wide channel it was calibrated on or has quietly
  narrowed to the two powerups (46 of 51 ten-minute T2 runs saw zero quad+pent
  takes, so a narrow channel would make `item_pickups == 0` the normal reading).
- `measured.demo_flush_s` is how long the tier waited for the server to write
  this match's demo, or null when it never appeared. `sv_demoUseCache 1` keeps
  the recording in memory until KTX stops recording, and a fixed sleep before
  teardown left 14 of 17 T4 demos at 0 bytes — with every demo-derived field
  silently unavailable behind it. The tier now waits for the file, bounded,
  and says so.
- `measured` per rung is the ladder's audit trail: when every rung carries it, the
  validator refolds it and refuses `measurements`/`sampling` that disagree.
- Draw rule: a draw does not advance the ladder and stops it (recorded as
  `"win": false, "draw": true`). What a draw *should* mean for the ladder is an
  open owner question, flagged as `OAVGJORD` until it is answered.
- **Legacy:** envelopes written before the five-value verdict carry no
  `t4_schema` and `"verdict": "COMPLETE"`. They are accepted only if their
  filename and sha256 are in `schema/legacy-t4-inventering.json` (27 envelopes,
  fail-closed, the inventory itself sha-pinned in `checks.py`). Any other
  `COMPLETE` is refused whatever its date, and the dashboard marks the
  grandfathered ones `legacy` — none of them was judged on the four gates.
- Every played rung carries the same `scoreboard` card as T3: the match as the
  KTX scoreboard saw it, with a POV link per player. Null when no analyzer or no
  demoinfo block was available for that match.

## The match card

`scoreboard` is the public hub's game page, column for column, taken from its own
source (`vikpe/servers.qwlan.pl`, `DemoStats.tsx`) so a lab match reads like a
real one instead of like an invention of ours. Team rows carry the same line as
player rows because the hub renders both through the same row:

```
frags · efficiency · kills · deaths · suicides ("Bores") · tk ("TKs")
dmg_given · dmg_taken · dmg_enemy_weapons ("EWEP") · taken_to_die ("To Die")
ga · ya · ra · mh · sg_acc · lg_acc · rl_direct ("RL#", direct hits)
quad · pent · ring · lg/rl taken-kills-dropped
```

The hub's table changes with the mode: teamplay adds Team and the powerup
columns and drops S.Frags, team deathmatch adds TKs and EWEP. Ours is always
team deathmatch, so that is the template it follows. Players also carry `ping`
and `top_color`/`bottom_color`, which is what the frag box is painted with.

Three of these are computed rather than read, the way KTX computes them:
efficiency is `kills / (kills + deaths)`, the two accuracies are `hits /
attacks` and stay null when nothing was fired, and `taken_to_die` is
`taken // deaths` — an average, so it is recomputed on a team row instead of
summed. ktxstats serialises with serde defaults, so a counter the player never
touched has no key at all: absence means none, not unknown, and it reads as
zero.

## Demo evidence

Numbers nobody can look at are hard to trust, so a tier that can record its own
match does: the runner asks the server for an MVD, keeps the demo clock, and
emits links that open the hub demo player three seconds before the moment in
question with the POV locked to the bot the number is about.

- Links are host-relative (`/demo-player/?demoUrl=%2Fdemos%2F<file>&map=<map>&duration=<s>&from=<s>&track=<userid>`),
  so the same evidence file works on the LAN hub and behind the gated dashboard.
  Host-relative is not enough on its own — both surfaces have to actually hold
  the demo at that path. The publisher mirrors every published demo into the
  hub's static `demos/` directory for exactly that reason; without it a link
  pasted on the hub opens the player and then finds nothing.
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
after a loss, an unmeasured stall count with no declared reason, a declared absence
that nonetheless carries a count, one that carries map zones, a `nav` block stuck at
`"building"`, a `"ready"` one with zero cells, one whose `nav.map` names a different
map than the envelope, one on a two-sided tier, where a single stamp could not say which side it described, and a
complete T1 with no `nav` at all). `runner/selftest.py` validates all fixtures with the hand-rolled
checkers — this is the schema conformance suite and runs offline in CI.
