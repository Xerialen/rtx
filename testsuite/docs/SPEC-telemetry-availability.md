# Spec: a build that cannot measure must not report zero

Status: agreed 2026-07-28, not yet implemented. Owner asked for this to be
written down before the session compacts.

## The defect

`meganav-stock` (0b0a4d1) produced a T2 column reading `stall_firings: 0` with
zero stall cells, against 344 for `testsuite` and 435 for `main`. The zero is
not a measurement. The branch has no stall instrumentation at all:

- `rtx_telemetry` does not exist as a cvar (`grep` over `crates/` finds nothing).
- `goto_stall` appears only in comments and a README, never as an emitted event.
- `main` has the emitting code in three files (`bot/state.rs`, `control.rs`,
  `rtx-ctlproto/src/lib.rs`).

So the column reads best-in-class precisely because it cannot measure. That is
the worst kind of wrong number: silent, plausible and flattering. The same
applies to T1 — the `stall` outcome can never fire on that build, so a stalled
attempt is recorded as `timeout` instead.

The run was allowed through because `CvarRestore` (runner/runlib.py:355) treats
the config's `[restore]` table as an acceptable source for a cvar the server
cannot answer for. That fallback is correct for *restoring* a value; it is wrong
as evidence that the build *has* the cvar. Our own configuration vouched for a
capability the build does not have.

## The rule

A tier may only report a number it was able to measure. Where the build under
test cannot produce the underlying signal, the value is `null` and the envelope
says why. Absence of a capability is never zero, and never silently equal to a
build that measured zero.

## Contract change

Envelope gains an optional top-level block, because a capability is a property
of the build and the rig rather than of one tier's payload:

```json
"capabilities": {
  "telemetry": false,
  "unavailable": ["stall_firings", "cells", "t1:stall"],
  "note": "build exposes no rtx_telemetry cvar; stall events are not emitted"
}
```

- Absent block means everything the tier needs was available (the common case).
- `telemetry: false` makes the T2 payload's `stall_firings` and `cells`
  `null`/`[]` rather than `0`/`[]`-meaning-none.
- The T2 invariant (`stall_firings == sum(cells[].n)`) is skipped when
  `stall_firings` is null, and `checks.py` must reject a null `stall_firings`
  that is *not* accompanied by `capabilities.telemetry: false` — the excuse has
  to be declared, not inferred.

## Detection

One probe, once, during the T1/T2 preflight, after the cvar set:

1. `set rtx_telemetry 1`
2. `get rtx_telemetry` — success means the build has it; a control error or a
   missing value means it does not.

This is the probe `CvarRestore` already performs; the change is to record the
outcome instead of papering over it with the `[restore]` baseline. Split the two
concerns explicitly in `runlib`:

- `restore_source(name) -> "server" | "baseline"` — what will be restored, as now.
- `server_has(name) -> bool` — did the server itself answer for this cvar.

`CvarRestore` keeps its current forgiving behaviour for restoration. The new
`server_has` is what the tiers consult before claiming a measurement.

## Behaviour, tier by tier

**T2.** With telemetry unavailable: still run, still measure movement, powerups,
speeds and stand-still — those come from `status` polling and the analyzer and
are unaffected. Set `stall_firings: null`, `cells: []`, emit the capabilities
block, and skip the stall moment. Do not fail the run: a build without telemetry
is a legitimate thing to measure, it simply cannot be asked about stalls.

**T1.** Drills still run and are still graded — arrival is observed from
`status`, not from telemetry. Record `t1:stall` in `unavailable` so a reader
knows a failure that would have been `stall` was recorded as `timeout` on this
build. Thresholds and verdicts are unchanged.

**Sweep.** The manifest already records per-column build identity; add the
capabilities block per column so a mixed sweep is readable at a glance.

## Dashboard

- A null metric renders as `ej mätbar` with the reason in its title, never as a
  dash that could be mistaken for zero and never as `0`.
- A column whose capabilities differ from the focus column must not be compared
  on the affected metrics: `canCompare` stays as it is for the tier as a whole,
  but the affected cells show `ej jämförbar` instead of a reference number.
- The T2 map view draws no cells for such a column and says why rather than
  showing an empty map that reads as "no problem zones".

## Tests

Fixtures, all offline:

- valid: a T2 envelope with `capabilities.telemetry: false`, `stall_firings:
  null`, `cells: []` — must be accepted.
- broken: `stall_firings: null` with no capabilities block — must be rejected
  ("a missing measurement has to declare why").
- broken: `capabilities.telemetry: false` together with `stall_firings: 344` —
  must be rejected (claiming a measurement the build could not make).
- broken: `stall_firings: null` with a non-empty `cells` list.

Dashboard selftest: assert the null renders as `ej mätbar` and that a
capability-mismatched reference shows `ej jämförbar` rather than a number.

## Acceptance

1. Re-run T2 against `meganav-stock` and confirm the envelope carries
   `capabilities.telemetry: false` and a null `stall_firings`.
2. Re-run T2 against `testsuite` and confirm no capabilities block appears and
   the numbers are unchanged from today's run.
3. The dashboard shows the stock column as `ej mätbar` on stall firings, and
   comparing it against main shows `ej jämförbar` on that row rather than
   `435`.
4. `python3 testflow.py selftest` covers the four fixtures above.

## Out of scope

Making `meganav-stock` measurable. It is a reference build for what the engine
did before the meganav work; instrumenting it would change what it is.
