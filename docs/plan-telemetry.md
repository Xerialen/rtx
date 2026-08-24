# Planner telemetry — `PlanTick`, and how to read it

Per-tick record of what the planner decided for one bot: **which link A\* chose**, what that link
cost term by term, and the controller state it was steering in. Off by default. This file is the
field reference a consumer parses against; the engine-side contract lives in the doc comments on
`rtx_ctlproto::PlanTick`, and this is written to agree with them.

Schema string on every row: `qw-nav-graph/1` (`PLAN_SCHEMA`).

## The gate — both cvars, or nothing

| cvar | default | what it does |
|---|---|---|
| `rtx_telemetry` | `0` | Master switch for the whole telemetry surface |
| `rtx_plan_telemetry` | `0` | Fine switch for `PlanTick` / `PlanContract` |
| `rtx_plan_telemetry_div` | `1` | Emit one row per this many frames per bot |

```rust
plan_gate(telemetry, plan_cvar, div_cvar).on == telemetry && plan_cvar > 0.0
```

`rtx_plan_telemetry` alone is **never** enough. The fine switch is subordinate to the master one on
purpose: turning it on puts new `Event` variants on the wire, and a typed consumer built before
them cannot decode a variant it has never heard of — the deployed nav-viewer dies on it rather than
skipping it. So an operator who sets only the fine switch gets nothing, which is the safe way for
that mistake to fail.

**Both off ⇒ a byte-identical event stream to an unbuilt-with-telemetry engine.** That is the
contract the off-mode regression checks, and it is what makes it safe to ship this on main: a
server nobody is measuring pays nothing and emits nothing new.

`rtx_plan_telemetry_div` thins deliberately. Events are droppable under backlog by design (replies
are not), so a long capture is better decimated on purpose than thinned by silent drops. The
divisor is clamped to `>= 1`, so a nonsense value thins nothing rather than dividing by zero.

### A row goes out when

```rust
plan_row_due(gate, fresh, seq) == gate.on && fresh && seq % gate.div == 0
```

`fresh` is **frame identity**, set by steering and cleared after the check. It is not a time
comparison: think runs at ~77 Hz and `frame_end` at ~50 Hz, so their two `game.time()` readings are
never bit-identical live. The times travel on the row as payload; they are not the emit key. A bot
that did not steer this frame — dead, not in play — is not fresh, and emitting it would report a
decision it never made.

## Absence: sentinels, never null

**No field is ever absent or null.** A consumer never has to tell "key not present" from "value not
known", and an old dataset run through an adapter stays byte-comparable with a fresh one.

| sentinel | value | meaning |
|---|---|---|
| `PLAN_NONE` | `u32::MAX` (`4294967295`) | no cell / no link |
| `PLAN_NO_BAND` | `u8::MAX` (`255`) | no planned speed band |
| `PLAN_UNSET` | `-1.0` | no float reading |

`link == PLAN_NONE` says the bot was demonstrably off-route. It does **not** mean "the row did not
say" — that case is a missing row, not a sentinel. Keep them apart when classifying.

Some absences ride on a **flag** rather than a sentinel, because zero is a real reading for them:
`runway`/`runway_measured`, `sj_progress`/`sj_progress_measured`,
`first_air_vz`/`first_air_vz_measured`. A bot that left the ground with no upward impulse measures
exactly `0.0`, and there is no out-of-domain number to spare.

## Fields

### Identity and joining

| field | type | notes |
|---|---|---|
| `schema` | string | `qw-nav-graph/1` |
| `graph_stamp` | u64 | **Level 1**, the row's only graph pin. Rows with different stamps must never be compared. Level 2 (inventory hash) rides on `PlanContract`, never on a row |
| `bot` | u32 | engine client number, `1..=maxclients` |
| `t` | f32 | server time; join key against the harness row alongside `bot` |
| `seq` | u32 | per-bot monotone counter |

`seq` is how a consumer tells **"the planner did nothing"** from **"the row never made it"**. Events
are droppable; a gap in `seq` is a dropped row. A run with gaps must be reported as such, never read
as an absence of events.

### The decision — this is what the event exists to record

| field | type | notes |
|---|---|---|
| `link` | u32 | **The active leg** — the link index the bot is steering along. `PLAN_NONE` off-route or arrived |
| `kind` | string | that leg's `LinkKind` as its `Debug` name; empty off-route |
| `link_from`, `link_to` | u32 | the leg's source and target cells, so an attribution pass never re-opens the graph |
| `cell`, `goal_cell` | u32 | where the bot resolved itself, and what it routes toward |
| `route_len`, `route_pos` | u32 | committed route and the bot's leg within it |
| `band` | u8 | planned *entry* speed band for this leg; `PLAN_NO_BAND` if none |
| `replanned` | bool | a repath ran this tick |
| `route_goal`, `route_target` | u32 | the goal the last repath was handed, and the cell A\* actually searched to after reachability redirection and LOD truncation |
| `plan_cost` | f32 | banded total for the committed route, in seconds; `PLAN_UNSET` if unbanded or none |
| `remaining_cost` | f32 | what finishing the plan costs from where the bot actually is |

`link`, `link_from`, `link_to` and `kind` are all read from the same steering stamp, so they agree
by construction. Do not re-derive a chosen link from positions — **guessing the chosen link is the
failure mode this event was built to end.**

### Why a plan failed

| field | type | notes |
|---|---|---|
| `plan_fail` | string | empty on success, else `no_path`, `priced_out`, `unreachable` |
| `goal_reachable` | bool | goal reachable as **pure topology** in this graph |
| `goal_redirected` | bool | the search target was consequently redirected away from it |

`plan_fail` separates a *structurally missing link* from an *execution failure*. A bot failing
repeatedly with `plan_fail` empty had a route and could not fly it; one with `no_path` was never
offered a way at all, and no amount of steering work would have helped.

`plan_fail` **alone cannot decide** `structural_missing_link`: `unreachable` may be an ordinary
reachability redirection, and `no_path` can also come from a priced-out window. `goal_reachable` /
`goal_redirected` are the topological half, pinned to `graph_stamp`. The other half — whether the
trace stopped outside the goal predicate — is the harness's to supply, because the predicate
belongs to the scenario, not to the graph. Until both halves are present, classify `unknown`.

### What the leg cost, term by term

`p_base`, `p_gate`, `p_penalty`, `p_jitter`, `p_rj`, `p_water`, `p_hazard`, `p_chained`, `p_total`
— all f32 seconds.

A sum cannot be acted on. `100000.4s` is a shut gate plus jitter, or an unfit rocket jump plus a
failed-link strike, and those want opposite fixes. Split, the reason is readable directly.

`p_penalty` is the per-link surcharge path — this is where a failed-link strike, a teleport-reuse
charge and an operator `Cmd::Recost` price all land, **summed**, because the nav query keeps one
extra per link and `merge_link_penalty` adds into it.

`p_jitter` scales against the link's own base cost, never against a surcharge, so pricing a link
does not make it divert differently for different bots.

**New `p_*` terms may be added.** A consumer meeting one it does not know must ignore it —
instrumenting a fork's extra pricing is an extension, not a break.

### Movement and controller state

`v_req`, `origin[3]`, `vel[3]`, `speed`, `chained`, `curl_gain`, `weave_cap`, `on_ground`, `phase`,
`phase_prev`, `runway`, `sj_progress`, `runway_measured`, `sj_progress_measured`, `takeoff_cell`,
`takeoff_xyz[3]`, `runup`, `wp`, `lip`, `takeoff_ok`, `sj_held`, `hold_jump`, `jump_cmd`,
`first_air_vz`, `first_air_vz_measured`, `hops`, `off_reason`.

Two that bite if misread:

- `jump_cmd` is whether `+jump` is set in the usercmd **actually sent** this tick, read after the
  whole button chain including the late clears. Recording the intention instead of the command
  would hide the jump-cmd-on-ground race this telemetry exists to expose.
- `takeoff_cell` is measured from the jump leg's source cell and is meaningless off a speed jump —
  it is `PLAN_NONE` unless `v_req > 0.0`.

`v_req_deficit` is **not representable on the wire**. Do not synthesise it.

## `PlanContract`

Emitted alongside the stream: which graph the run was measured against, carrying the level-2
inventory hash that `graph_stamp` abbreviates. A capture stands on its own only with it —
`graph_stamp` is a bare integer, and the contract is what it means.

## Absence of the stream

If a series carries no `PlanTick` at all, classify it `missing_plan_fields` and re-run
instrumented. Do **not** infer the chosen link from positions, and do not treat a missing stream as
a finding about the bot: it is a finding about the instrument.

A parser must therefore distinguish three states, and never collapse them:

| state | what it is |
|---|---|
| no rows at all | `missing_plan_fields` — the gate was off, or the build predates it |
| rows with `seq` gaps | dropped events; report the gaps |
| rows with sentinels | real readings of "none" |

A consumer that crashes on an absent stream is wrong twice: the absence is the single most
expected condition, since both cvars default off.

## Cross-references

- Field-level contract: doc comments on `rtx_ctlproto::PlanTick` / `PlanContract`
- Gate: `rtx_game::control::plan_gate` / `plan_row_due`
- Price split: `rtx_nav::navmesh::NavGraph::link_extra_breakdown` → `LinkExtra`
- Operator surcharges that show up in `p_penalty`: `rtx_ctlproto::Cmd::Recost`
