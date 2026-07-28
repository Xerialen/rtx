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
trial 0: arrived  7.5s  speed peak 805  p90 777  p50 651
trial 1: arrived  7.5s  speed peak 803  p90 775  p50 651
trial 2: arrived  7.5s  speed peak 802  p90 772  p50 648
```

It **sustains** 775 ups — faster than the 630-700 ups at the head of the human suite, and it does it
with zero jumps, on plain ground. So wherever it arrives late on dm3 it is not because it cannot
carry speed; navigation is costing it speed it demonstrably has. Read every ratio in the table above
as a navigation number, not a movement-physics one. No tuning of the gait closes it.

**It fights its own steering everywhere.** 500 deg/s of yaw jitter at p50 is not a tail effect —
that is the *median* movement sawing at its heading, and it is measured only over frames above
100 ups, so it is not crawl noise. Likewise wall events: the median run loses >60 ups to geometry
**35 times**.

These two are the numbers the lane work has to move. Time and arrival can stay flat and it would
still be a win if yaw jitter and wall events came down, because those are what "bumping into
geometry" and "hack on top of hack" actually look like in data.

## Caveats worth carrying

- **The bot is told only "reach the end"** — it picks its own path. High cross-track means it chose
  a different route, which is a separate finding from executing the same route badly.
- **The reference entry velocity is injected** (`Teleport` carries `vel`), measured over the first
  0.1s of the human's segment. Without it the run measures a standing start; that alone was worth
  0.48x → 0.73x of human speed on the suite's fastest movement.
- **Human references are the fastest run over given ground**, so slower segments in the suite are
  slow because that ground was only ever crossed slowly — not because the human was strolling.
