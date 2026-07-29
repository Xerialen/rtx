# Movement baseline — dm3, before the lane work

Taken on `c8a20fb`, dm3, one bot, against the human coverage suite from
`demos/20260507-2107_4on4_]sr[_vs_book[dm3].mvd` (4330 segments → 544 distinct).

```
cargo run --release -p rtx-ctlproto --example flyprobe -- line \
    "demos/20260507-2107_4on4_]sr[_vs_book[dm3].mvd" \
    --first 0 --count 60 --stride 9 --trials 3 --csv docs/baseline/dm3-4on4-suite.csv
```

60 movements strided across the whole suite (which is ordered fastest-first, so a stride is
necessary — the head of it is all 630-700 ups running), 3 trials each. Raw per-run rows are in
`dm3-4on4-suite.csv`, the full transcript in `dm3-4on4-suite.log`.

## The numbers

| | p50 | p90 |
|---|---|---|
| time vs human | **1.25x** | 4.71x (max 7.84x) |
| speed vs human | **0.87x** | 0.70x (p10) |
| late-line speed | 0.87x | |
| cross-track p95 | 71u | 274u |
| yaw jitter p95 | 500 deg/s | 1038 deg/s |
| reverse frames | 0 | 103 |
| wall events | 35 | 94 |

135 timed runs over 45 movements; **120/135 arrived (89%)**.

A further 33 runs are excluded from the timings because the bot covered only 38% of the human's
ground at p50 — it found a shorter journey between the same two points, so the two clocks measure
different things (see `LineScore::comparable`). All 33 arrived, at 14 wall events p50.

## What it says

**Arrival is bimodal, not flaky.** Of 56 movements that produced runs: **51 arrive on every trial,
5 arrive on none, 0 are intermittent.** The failures are deterministic — specific geometry the bot
cannot get through — not variance. A sixth group of 4 sampled movements produced no event at all
within 30s: the goto neither arrived nor stalled, which is its own bug.

**The median movement is close to human, the tail is not.** 1.25x time at 0.87x speed while staying
71u off the human's line is a bot that basically works. But p90 is 4.71x and cross-track p90 is
274u — a tenth of the suite is a different order of failure.

**None of that speed shortfall is a speed *ability* shortfall.** The same build, same session, down
the 100m runway (`flyprobe goto 224 -1408 32 224 2900 32 3`):

```
trial 0: arrived  7.5s  speed peak 803  p90 774  p50 650   (10 hops, 94% of frames airborne)
trial 1: arrived  7.5s  speed peak 800  p90 771  p50 649   (10 hops, 94% of frames airborne)
trial 2: arrived  7.5s  speed peak 801  p90 773  p50 649   (10 hops, 94% of frames airborne)
```

It **sustains** 775 ups — faster than the 630-700 ups at the head of the human suite — by bhopping
the whole runway, airborne 94% of the time. (Which is the only way to get there: ground friction
caps a QW run near 320 ups, so anything above that is necessarily airborne.)

So wherever it arrives late on dm3 it is not because it cannot carry speed; navigation is costing it
speed it demonstrably has. Read every ratio in the table above as a navigation number, not a
movement-physics one. No tuning of the gait closes it.

**~~It fights its own steering everywhere.~~** — *superseded; see below.* This section originally
read 500 deg/s of yaw jitter at p50 as the bot sawing at its heading. That was wrong, and the way it
was wrong is worth keeping: `yaw_jitter_p95` is a peak *turn rate*, and it cannot tell a wide smooth
arc from an oscillation. A strafe jump is a hard continuous turn, so excellent movement scores high
on it — humans on this suite reach 754 deg/s. What separates the two is how often the turn *reverses
direction*, which is why `LineScore` now carries `yaw_reversals` and why the human numbers below are
split by pace.

## What the human suite actually reads (the targets)

Each human run scored against its own line, split by pace — the two halves are different activities
and averaging them hides a factor of five:

| | segments | turn rate p50 | **turn reversals p50** | wall events p50 |
|---|---|---|---|---|
| travelling (≥500 ups) | 122 | 339 deg/s | **1.9/s** | 2 |
| manoeuvring (<300 ups) | 149 | 611 deg/s | 15.0/s | 2 |

Against that, the bot's reversal rate at the median is **2.1/s** — already human. Steering
smoothness is not the deficit.

**Where the deficit actually is.** The median run takes the human's path (path ratio p50 **1.04**)
at **0.84x** their speed, and the speed profile binned along the line is *flat* —
`9 9 8 9 9 8 10 8 8 10`. Nothing is lost at corners or at handoffs between movement owners, so the
guard pile is not what costs the median run. `1.04 / 0.84` accounts for its time ratio exactly.

The damage is in the tail: **22% of runs travel more than 1.5x the human's distance** (p90 2.24x),
and 30% of runs are already near-perfect (path <1.2x, time <1.3x).

**The open lead.** Handed the human's entry speed, the bot stays 80-84% airborne on the correct path
and *bleeds* the speed rather than holding it — one segment enters at 604 ups and averages 0.54x of
the human, with the per-twentieth speed strip starting at 8-9 and sagging to 2-4 mid-movement. That
is the question the gait-phase column on `TrajRow` exists to answer.

## Run the sweep on an idle machine — this is not optional

Compiling while a sweep runs invalidates it. Measured, same commit, lane off in both:

| | quiet machine | `cargo build`/`test` running |
|---|---|---|
| speed vs human | 0.91x | **0.73x** |
| wall events (mean) | 43 | **5** |
| yaw p95 (p50) | 476 deg/s | 364 deg/s |
| arrived | 89% | 89% |

Arrival, cross-track and the p90 time ratio barely move; everything derived from frame-to-frame
differencing collapses. The server drops frames under CPU starvation, so the bot's frametime grows
and it genuinely moves differently — this is not a sampling artefact that better statistics would
fix. A/B arms must run back to back with nothing else on the box, and a sweep that straddles a build
is worthless. It is easy to fool yourself here: the degraded numbers look like an *improvement* in
wall events and yaw jitter, because a bot that is moving slower hits fewer things.

## Caveats worth carrying

- **The bot is told only "reach the end"** — it picks its own path. High cross-track means it chose
  a different route, which is a separate finding from executing the same route badly.
- **The reference entry velocity is injected** (`Teleport` carries `vel`), measured over the first
  0.1s of the human's segment. Without it the run measures a standing start; that alone was worth
  0.48x → 0.73x of human speed on the suite's fastest movement.
- **Human references are the fastest run over given ground**, so slower segments in the suite are
  slow because that ground was only ever crossed slowly — not because the human was strolling.
- **`flyprobe goto` teleports with zero velocity; `flyprobe line` does not.** Anything measured
  through `goto` on a ~640u segment is a standing start, and a standing start has its own ceiling:
  peak speed reads ~496 on dm3 in *every* configuration, which reads like a gate pinning the bot
  when it is just the accelerate-from-rest limit. Use `line` for anything about sustained movement.

## Approaches measured and rejected

Recorded so they are not re-attempted. Each was A/B'd with paired arms on one idle server.

- **Lateral lane shaping** (elastic-band path deformation). Refuted on this geometry: 30-39% of
  route points sit in corridors too narrow for a 32-unit hull to move sideways at all, 14-23% have
  no standable floor beside them, and the relaxation displaces the line **1.2u** against 32u cells.
  Live A/B flat on every metric. Quake corridors are one to two hull widths wide — the cell-centre
  line is already near the middle.
- **Proportional air-turn** (`rtx_bot_sweep`). Fixed a genuine defect — the gait requests 720 deg/s,
  the physical cap, on every frame regardless of error — but reversals were already at human level,
  so it moved nothing.
- **Disabling both reactive brakes.** Reverse frames 20 → 18. They were never the source;
  `reverse_frames` counts frames behind the furthest point reached, not braking.
- **Verifying every hop's landing and vetoing bad ones.** Made it worse (speed 0.84 → 0.73):
  `plan_hop`'s tolerances refuse too often and the bot walks where it should hop.
- **Keeping the hop chain alive through bends.** No change; a direct probe showed identical airborne
  fraction and hop count either way.
