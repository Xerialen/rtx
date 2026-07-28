# The side-jump harness — measuring short-ramp strafe jumps

A **side jump** is the commonest human shortcut and the one our generator was longest blind to: run
*along* a ledge, then leap off its *flank*. `demos/dm3_rlstrafejump.qwd` is the reference — 144u of
run-up on a balcony 448u wide and two grid rows (64u) deep, takeoff at 399 ups on a heading 63° off
the chord, 256u across dm3's z=152 pit in 0.66 s.

`solve_side_jumps_from` generates these (see the module note at the top of the side-jump constants in
`crates/rtx-nav/src/navmesh/physics.rs`), and its envelope constants are **empirical**. This harness
is how they get measured, on the flat `100m` runway where geometry contributes nothing.

## The tools

| tool | what it does |
| --- | --- |
| `side_jump_test` | one parameter set: plant a link, fly it N times, reset between trials, report per-trial landings |
| `fly_link` | fly one existing link by id and report the landing (the speed-jump counterpart of `test_link`, which is rocket-jump only) |
| `list_curl_links` | every speed-jump link — curls *and* straight/chained — including ones planted this session |

`side_jump_test` places the bot **side-on to the goal**: it runs along `goal_yaw ∓ 90°` so the target
sits off its flank, exactly the demo's posture. Per parameter set it prepares the bot, optionally
probes the build-time certifier over the same geometry, plants the link **once**, then loops: teleport
to the start → order the jump → record the landing → teleport back. Every trial therefore gets an
identical, full-length runway.

```
side_jump_test(side="left", runup=128, off_angle=60, target_dist=256, gain=12, trials=5)
```

Defaults suit `100m` (start `(224,-1408,32)`, `goal_yaw` 90 = +Y). Key reply fields:

- `probe` — what `certify_curl` says about the same takeoff/target/heading/runway. Divergence between
  this and the trials is the interesting signal: it means the offline certifier and the live
  controller disagree, and the certifier is what gates generation.
- `trials[].takeoff_speed` — what the ground prestrafe actually delivered. Compare against
  `prestrafe_delivered(runup)`; the model is known to be optimistic (the demo reads ~0.84 of it).
- `trials[].runup_cross_track` — how far the speed-building weave swung off the run-up line, measured
  only over grounded samples. This is what `SIDE_WEAVE_CLEARANCE` / `SIDE_WEAVE_NARROW_DEG` encode.
- `summary.hit_rate`, `mean_miss_xy`, `takeoff_speed_over_v_req`.

## Three things the first live flights caught

Worth knowing before trusting a rollout, because all three were **policy mismatches between the
certifier and the controller** — the build proved one flight and the bot flew another. None was
visible offline; each showed up as a link that landed on some attempts and not others.

- **The air-curl must latch its strafe side.** `air_correct` re-derived it from `err.signum()` every
  tick, which is fine for a fixed bearing but not for a curl pursuing a bearing that swings as the bot
  closes: an overshoot reverses the strafe and the arc chatters. The demo is unambiguous — the human
  holds one side key for the whole flight, yaw climbing monotonically. `air_correct_held` does that,
  and the rollouts fly it too.
- **The certifier must prove the heading spread the runtime actually delivers.** The leap fires on
  crossing the takeoff line, at whatever phase of the weave the bot happens to be in, and above
  `sv_maxspeed` no amount of coasting pulls the heading back. Measured: ±12°, not the 6° the certifier
  assumed.
- **A committed curl must not hop off its own landing.** It is one arc onto a fixed target; chaining
  another hop throws the bot straight back off the platform it just reached.

If a swept parameter set lands inconsistently, suspect this class of thing before the envelope
constants: check that what `probe` rolls is what the trajectory shows.

## Sweeps

Each sweep calibrates a specific constant. Run them in order — the later ones assume the earlier
constants are settled.

**S1 — is `prestrafe_delivered` honest on short runways?** `off_angle` 0–10, `runup ∈ {32, 48, 64,
96, 128, 160, 192, 256}`, `target_dist ∈ {220, 256, 300}`. Plot `takeoff_speed` against
`prestrafe_delivered(runup)`. Sets **`SIDE_MIN_RUNWAY`** (the length below which nothing lands) and
**`SIDE_V_FLOOR_FRAC`**, and validates the runtime's `prestrafe_deficit` abort threshold (0.85,
hardcoded in `steer.rs`).

**S2 — the off-angle envelope.** `runup` 128, `target_dist` 256, `off_angle ∈ {30, 45, 60, 75, 90,
105, 110}` × `gain ∈ {8, 12, 16, 20}`. The hit-rate map sets **`SIDE_PSI_MAX`** and says whether
`CURL_GAINS` needs a rung above 20. Watch for `probe` refusing what the trials land — `curl_lands`
has a mid-flight divergence veto that may be too strict at wide angles.

**S3 — what controls distance.** `off_angle` 60, `target_dist` 220–340 step 20, sweeping `gain` and
`v_req`. Produces the relationship the generator's speed ladder is searching over.

**S4 — weave width.** Read `runup_cross_track` across S1's and S3's runs. Sets
**`SIDE_WEAVE_CLEARANCE`** (how much lateral floor the uncapped weave needs) and
**`SIDE_WEAVE_NARROW_DEG`** (what to clamp it to when the floor is thinner).

**Acceptance anchor.** The dm3 cell: `runup ≈ 128`, `off_angle ≈ 55–65`, `target_dist ≈ 260–280`,
`v_req ≈ 390–400` should hit at least 4/5. That is the demo, and it is the case the whole pass exists
for.

## Practical notes

- Planted links accumulate — there is no unplant. `server_restart` every ~50 parameter sets, and
  re-list afterwards: **link ids are not stable across a restart or a map change**.
- Out-of-bounds geometry fails cleanly at the plant (`no cell near from/tgt`), so the usable extent of
  the runway can be discovered by sweeping until it errors.
- A `chained` link needs a prior jump to carry `v_req` in; flying one from a standing start fails by
  design, and `fly_link` says so rather than reporting a mystery miss.
- The server gives up on a flight after 8 s (`FLY_TIMEOUT`) and reports `timeout: true`, so a sweep
  always advances.
