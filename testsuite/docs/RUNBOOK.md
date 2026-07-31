# T0–T4 acceptance and publication runbook

This is the operator checklist for a **deterministic, publishable T0–T4 run**.
It is deliberately stricter than the JSON schema. The schema proves that an
envelope is well formed; this runbook proves that the intended experiment was
actually run, that every stated number was observed, and that the remote
Dashboard shows those exact observations.

Do not replace this checklist with “the command exited zero”. In particular:

- `status: failed` proves an attempt was recorded, not that a tier was measured.
- `quad_takes: 0, quad_lay_avg: null` does not say how long an untouched quad
  lay; it is a right-censored observation and is not an accepted powerup
  measurement.
- A T3 in which either client exits before match end has no valid score, even if
  KTX later prints a scoreboard after timing the missing players out.
- Schema validation and Dashboard field verification do not make a semantically
  incomplete run complete.

## 1. Words used in reports

Use these terms consistently:

| Term | Required meaning |
|---|---|
| **attempted** | An envelope exists, including `failed` or `aborted`. |
| **measured tier** | The envelope is `complete` and passes the tier-specific semantic gate below. |
| **complete chain** | T0, T1, T2, T3, and T4 are all measured tiers for one pinned branch commit. Quality verdicts may still be FAIL. |
| **green** | A complete chain whose required quality gates pass. Do not infer this merely from “complete”. |
| **published** | The exact indexed envelopes were built, field-verified locally, uploaded, fetched back from remote storage, and field-verified again. |
| **retracted** | Preserved for audit but removed from the Dashboard discovery directory with a written reason. Never delete or silently edit the original. |

A failed T3 may be published as a failed attempt. It does **not** make the chain
complete, and no score may be reported for it.

## 2. Run directory and immutable pins

Create one run directory per branch-under-test commit. Never reuse one after a
branch advances.

```sh
set -euo pipefail

export SUITE=/path/to/rtx/testsuite
export BRANCH_REPO=/path/to/branch/rtx
export REFERENCE_REPO=/path/to/reference/rtx
export CONFIG="$SUITE/config-<branch8>-vs-<reference8>.toml"
export RUN="$HOME/rtx-full-suite-$(date -u +%Y%m%dT%H%M%SZ)-<branch8>"
mkdir -p "$RUN"/{artifacts,logs,state,evidence,demos}
```

### Pin checklist

- [ ] Fetch both remotes without merging.
- [ ] Record the full 40-character branch and reference SHAs.
- [ ] Check out or create clean worktrees at those exact SHAs.
- [ ] `git status --porcelain` is empty in both worktrees.
- [ ] Record remote URL, branch name, SHA, fetch time, and config SHA-256 in
      `$RUN/state/pins.txt`.
- [ ] Verify every later tier reports the same branch-under-test SHA.
- [ ] Build the reference client from the recorded reference SHA, not from an
      old `target/` file.

If upstream advances while the suite runs, finish the pinned chain without
mixing tiers. State clearly that it tested the start-of-run SHA. A newer SHA is
a new run directory and a new T0–T4 chain.

## 3. Machine and rig preflight

Before stopping or changing anything:

```sh
python3 "$SUITE/testflow.py" selftest
python3 "$SUITE/dashboard/build_dashboard.py" --selftest
python3 "$SUITE/tools/powerup_watch.py" --selftest
systemctl --user list-units --state=running --type=service --no-legend \
  > "$RUN/state/active-services.before.txt"
cp /path/to/live/qw/qwprogs.so "$RUN/state/qwprogs.before.so"
sha256sum "$RUN/state/qwprogs.before.so" \
  > "$RUN/state/qwprogs.before.sha256"
```

### Preflight checklist

- [ ] Runner selftest accepts every valid fixture and rejects every broken fixture.
- [ ] Dashboard and powerup-watcher selftests pass.
- [ ] Required maps, `pak0.pak`, mvdsv, KTX, Frogbot data, analyzer, and
      demo directories exist and are readable.
- [ ] T1/T2, T3, and T4 ports are free or owned by the expected units only.
- [ ] No Cargo build, competing test runner, or stale `rtx-client` is running.
- [ ] Acquire the machine-wide build lock before Cargo work.
- [ ] Save active service names and hashes of every module that will be replaced.
- [ ] Stop unrelated RTX experiment services during measurement.
- [ ] Record enough state to restore services and module bytes exactly.
- [ ] Confirm the config points to the pinned worktrees and artifacts.

Never rely on a service name alone. After deployment, hash the live file and
verify that the server process maps that path.

## 4. T0 — Rust tests and artifact build

Use `--locked`, release mode, all workspace targets, and `--no-fail-fast` so one
failure does not hide later test binaries. Preserve Cargo's real exit status;
`tee` otherwise turns a failure into success.

```sh
set +e
(
  cd "$BRANCH_REPO"
  cargo test --workspace --all-targets --release --locked \
    --no-fail-fast 2>&1 | tee "$RUN/logs/t0-workspace.log"
  exit "${PIPESTATUS[0]}"
)
workspace_rc=$?
set -e
```

Run any package that is intentionally outside the workspace/default target
explicitly. In particular, do not infer that `rtx-mcp` ran because Cargo built
it:

```sh
set +e
(
  cd "$BRANCH_REPO"
  cargo test --release --locked -p rtx-mcp --all-targets \
    --no-fail-fast 2>&1 | tee "$RUN/logs/t0-rtx-mcp.log"
  exit "${PIPESTATUS[0]}"
)
mcp_rc=$?
set -e
```

Feed the adapter through stdin; it does not accept log paths as input options:

```sh
cat "$RUN/logs/t0-workspace.log" \
  | python3 "$SUITE/tools/cargo_summary.py" \
      "$RUN/artifacts/cargo-summary.json"
python3 "$SUITE/testflow.py" --config "$CONFIG" \
  t0-import "$RUN/artifacts/cargo-summary.json"
```

### T0 acceptance checklist

- [ ] Every Cargo command's real exit code is recorded.
- [ ] The complete log contains one `test result:` block per expected test binary.
- [ ] The summary total is nonzero and matches the sum of module totals.
- [ ] Every expected package is present; no package silently disappeared.
- [ ] `rtx_mcp.tests > 0`, and its pass count equals its test count.
- [ ] Any failure is named by package and test, not summarized as “Cargo failed”.
- [ ] T0 envelope totals and verdict recompute from module/floor data.
- [ ] A T0 quality FAIL is reported honestly. Continuing for diagnostics does
      not turn it into PASS.

Build release artifacts from the same pinned worktree, then copy them into the
run directory and hash them. Never let later tiers read mutable `target/` paths.

```sh
(
  cd "$BRANCH_REPO"
  cargo build --release --locked -p rtx-game -p rtx-client
)
install -m 0644 "$BRANCH_REPO/target/release/librtx.so" \
  "$RUN/artifacts/branch-librtx.so"
install -m 0755 "$BRANCH_REPO/target/release/rtx-client" \
  "$RUN/artifacts/branch-rtx-client"
sha256sum "$RUN/artifacts/"* > "$RUN/state/artifact-sha256.txt"
```

Repeat the client build and copy for the pinned reference SHA.

## 5. Deploy and prove what is live

- [ ] Install the hashed branch module into the isolated T1/T2 runtime.
- [ ] Restart the server; do not merely copy over a module already mapped.
- [ ] Hash source artifact and deployed file; require equality.
- [ ] Find `qwprogs.so` in `/proc/<MainPID>/maps`.
- [ ] Connect to the control channel and require the expected map.
- [ ] Wait for `navmesh == "ready"`; record cells, links, and RJ links.
- [ ] Record whether the rig applies any in-memory graph mutation. A planted
      cell/link is part of the rig and must not appear or disappear between tiers.
- [ ] Start every tier from a documented fresh state. A T1 map cycle can erase
      an in-memory nav patch; restart/reapply deliberately rather than inheriting
      whichever graph the previous tier left behind.

## 6. T1 — full movement suite

Run quick mode only as a smoke test. Acceptance uses the full scenario set:

```sh
python3 "$SUITE/testflow.py" --config "$CONFIG" t1 \
  2>&1 | tee "$RUN/logs/t1.log"
```

### T1 acceptance checklist

- [ ] The run is not `quick`.
- [ ] Scenario count equals the checked-in scenario inventory.
- [ ] Every graded scenario has the configured number of attempts.
- [ ] Every withheld scenario names its absent capability and appears in
      `capabilities.unavailable`; it is not counted as a failure or a pass.
- [ ] The envelope nav stamp matches the graph observed immediately before T1.
- [ ] Dash has two observations, a peak, a floor, and the correct verdict.
- [ ] T1 demo exists and is nonempty when demo evidence is configured.
- [ ] Representative drill and dash links resolve to the published demo name.
- [ ] Count PASS, FAIL, and withheld drills explicitly in the report.

A complete T1 with a FAIL verdict is still a measured tier. Report the verdict
and failed drills; do not call the chain green.

## 7. T2 — pacifist free play and powerup lay time

T2 acceptance is 600 seconds. The powerup timer starts when the item is first
observed available, **before measured bots can take it**.

### Clean T2 start

The T2 rig must not boot with a roaming bot. A server config containing
`rtx_bot_count 1` can consume quad/pent while navmesh preflight runs and makes
lay time unknowable.

- [ ] Start with `rtx_bot_count 0` and an empty bot roster.
- [ ] If nav construction is lazy, use one disposable pacifist bot to trigger
      the build, wait for `ready`, remove it immediately, and wait for an empty
      roster.
- [ ] Before the measured squad starts, query `items` and require both
      `item_artifact_super_damage` and `item_artifact_invulnerability` with
      `available: true`.
- [ ] Start an independent item-availability observer (10 Hz minimum; 20 Hz is
      preferred) before launching T2.
- [ ] The observer records every available/unavailable transition, each
      completed lay interval, and any open interval at run end.
- [ ] Then run T2 as the observer's child; do not start the observer after
      `nav_preflight` or `_wait_for_bots`.

Run the checked-in watcher at 20 Hz. It refuses a non-ready graph, a nonempty
bot roster, or an initially unavailable quad/pent, writes its sidecar even when
the child fails, and `--require-take` fails acceptance when either powerup has
no completed interval. After a successful child run it finds the one new T2
envelope and compares all four take/lay fields with the independent watch; an
analyzer overwrite or late-start discrepancy exits 3 instead of reaching
publication:

```sh
cd "$SUITE"
python3 tools/powerup_watch.py \
  --config "$CONFIG" \
  --output "$RUN/evidence/t2-powerups.json" \
  --interval 0.05 --require-take -- \
  python3 testflow.py --config "$CONFIG" t2 \
  2>&1 | tee "$RUN/logs/t2.log"
```

### T2 semantic acceptance gate

- [ ] `duration_s == 600` and `regime_note == null`.
- [ ] Four bots were observed; poll count is plausible for the configured rate.
- [ ] `stall_firings == sum(cells[].n)` and nav provenance is present.
- [ ] Quad and pent each have at least one completed available→taken interval.
- [ ] `powerup_watch.py` exits zero and its `envelope_comparison.mismatches` is empty.
- [ ] `quad_takes`, `quad_lay_avg`, `pent_takes`, and `pent_lay_avg` equal the
      independent observer (averages rounded only for envelope display).
- [ ] The metric `sources` say which observer/analyzer supplied each value.
- [ ] Analyzer output is compared with live control observations before it is
      allowed to replace them.
- [ ] If the analyzer says `0/null` while live control saw transitions, reject
      the envelope. Preserve it as retracted evidence; do not publish it as T2.
- [ ] Every powerup take used in an average has a transition timestamp and,
      when possible, a demo moment/link.

If a powerup has no take, `lay_avg: null` is valid schema but **not accepted by
this runbook**. Record the unfinished available interval as “at least N
seconds/right-censored”, mark T2 incomplete for acceptance, and do not call it
measured until the contract/dashboard can represent that interval or a rerun
produces a completed observation.

## 8. T3 — branch versus pinned reference

T3 needs a fresh dedicated KTX server. One client process controls all four
seats on each side, so one process death removes a whole team.

```sh
python3 "$SUITE/testflow.py" --config "$CONFIG" t3 \
  2>&1 | tee "$RUN/logs/t3.log"
```

### T3 preflight

- [ ] Branch and reference client files match the artifact manifest.
- [ ] Reference branch/commit declaration matches the worktree used to build it.
- [ ] Match server is idle in Standby, exact `4on4`, exact timelimit, no ghosts,
      no Frogbots, and enough client slots.
- [ ] KTX reset-chain overrides survive a second match, not only first boot.
- [ ] Demo directory is empty of ambiguous stale scorecards or is indexed by
      a before/after snapshot.
- [ ] All eight seats pass heartbeat and post-start movement gates.

### T3 acceptance checklist

- [ ] Envelope `status == "complete"` and payload verdict is `PIPELINE-OK`.
- [ ] Both client processes remain alive until authoritative match end.
- [ ] Scan both client logs for `panicked`, `fatal`, disconnect, timeout, and
      early process exit; require none.
- [ ] KTX demoinfo names both teams and all eight players for the full duration.
- [ ] MVD is nonempty, readable, and matches the demoinfo/demo name.
- [ ] Scoreboard team totals equal side frags, winner, and absolute diff.
- [ ] Branch/reference artifact digests and full commits are present in payload.
- [ ] Preserve branch log, reference log, server journal, MVD, demoinfo, and
      evidence envelope.

If either client exits early, the run is a failed T3 attempt. Do not quote the
partial score and do not use a later KTX scoreboard after timeout as a full
match result. Publishing the failed envelope is allowed; saying “T3 measured”
or “complete chain” is not.

A single complete match proves only the pipeline. A branch quality verdict
requires at least two replicates with side/spawn alternation and a valid
`T3-agg` envelope.

## 9. T4 — Frogbot ladder

```sh
python3 "$SUITE/testflow.py" --config "$CONFIG" t4 \
  2>&1 | tee "$RUN/logs/t4.log"
```

### T4 acceptance checklist

- [ ] Exact pinned branch client artifact is used.
- [ ] Four Frogbot skill echoes match the requested rung.
- [ ] Each played rung is a full-duration match with authoritative demoinfo.
- [ ] Ladder stops on first loss/draw and never skips a configured skill.
- [ ] `reached` recomputes from rung outcomes.
- [ ] MVD/scoreboard availability is stated; a zero-byte MVD is not presented
      as watchable evidence.
- [ ] Sporting loss is not mislabeled as a pipeline failure.

## 10. Evidence-chain gate

Create an index containing the **exact selected attempt for every tier**, even
when one failed:

```text
t0 evidence/t0-....json
t1 evidence/t1-....json
t2 evidence/t2-....json
t3 evidence/t3-....json
t4 evidence/t4-....json
```

### Chain checklist

- [ ] Every envelope passes `runner.checks.validate_result`.
- [ ] Every run id and full build commit is recorded in the run report.
- [ ] T0–T4 branch-under-test commits are identical.
- [ ] Config digest is expected; any intentional per-tier difference is explained.
- [ ] Binary digest matches the correct artifact type per tier.
- [ ] Tier semantic gates above pass. Schema validity alone is insufficient.
- [ ] Evidence and demos are copied into the immutable run directory.
- [ ] Generate `SHA256SUMS` over artifacts, evidence, demos, logs, config, and report.
- [ ] `sha256sum -c SHA256SUMS` passes.

A later corrected/derived envelope must name its source computation and preserve
all measured inputs. Never edit an existing envelope in place.

## 11. Retraction procedure

Use this when a syntactically valid envelope is later found semantically wrong.

- [ ] Copy the original into the immutable run archive first.
- [ ] Move it out of the Dashboard's top-level `evidence/*.json` discovery path
      into `evidence/retracted/` (or use an equivalent curated publish directory).
- [ ] Add a text/JSON retraction record naming run id, UTC time, reason, operator,
      and replacement run id if one exists.
- [ ] Do not delete or rewrite the original.
- [ ] Rebuild and verify that the affected Dashboard tier is either missing,
      failed, or replaced by the explicitly selected corrected envelope.
- [ ] Mention the retraction in the final report.

## 12. Dashboard build and local verification

```sh
python3 "$SUITE/dashboard/build_dashboard.py" --selftest
python3 "$SUITE/dashboard/build_dashboard.py" \
  --evidence-dir "$SUITE/evidence" \
  --output "$SUITE/dashboard/dashboard.html"
python3 "$SUITE/dashboard/verify_against_evidence.py" "$RUN/state/index.txt"
```

The verifier checks failed/aborted tiers by status and error and checks complete
tiers field by field. A verifier PASS means the page accurately represents the
selected envelopes; it does not upgrade a failed tier to measured.

### Local Dashboard checklist

- [ ] Builder emits zero unexpected warnings.
- [ ] The newest displayed group is the intended pinned commit.
- [ ] Displayed run ids equal the index, not merely “latest files”.
- [ ] T2 powerup takes, averages, and sources equal accepted evidence.
- [ ] Failed T3 reads FAILED with the exact error and no score.
- [ ] Missing/retracted tiers read `EJ KÖRD`, not zero or best-in-class.
- [ ] No local home paths, tokens, passwords, or private server addresses are embedded.
- [ ] Every linked demo exists under its URL-safe published name and is nonempty.

## 13. Publish and prove the remote artifact

Use the existing gated publisher. Capture its complete output, Wrangler version,
worker version id, uploaded demo list, and dashboard byte count.

```sh
SUITE_DIR="$SUITE" /path/to/dashboard-gate/publish.sh \
  2>&1 | tee "$RUN/logs/dashboard-publish.log"
```

Then fetch the actual remote KV/object back, not the local pre-upload file, and
run `verify_against_evidence.py` against that fetched HTML. If the verifier has
a fixed input path, temporarily substitute the fetched file and restore the
local file in a shell `trap`.

### Remote checklist

- [ ] Remote storage fetch succeeds after publication.
- [ ] Remote HTML passes the same exact index verification.
- [ ] Remote run ids and T2/T3 states match the intended chain.
- [ ] Unauthenticated HTTP receives the expected OAuth denial/challenge.
- [ ] An authenticated browser loads the Dashboard.
- [ ] At least one demo link per measured demo-bearing tier is opened through
      the deployed origin and seeks to the expected moment/POV.
- [ ] Downloaded demo bytes/hash equal the published source.
- [ ] Record worker version id and remote HTML SHA-256.

A 401 without OAuth proves only that the gate is closed. It does not prove that
an authenticated user can see the new Dashboard or that demo assets work.

## 14. Restore and audit cleanup

Restoration is part of the run, not an optional courtesy.

- [ ] Stop on-demand T3/T4 rigs and every test client.
- [ ] Restore the exact pre-run module bytes and verify SHA-256 equality.
- [ ] Restart exactly the services listed in the pre-run snapshot.
- [ ] Require every previously active unit to be active.
- [ ] Check no test-only unit, client, mvdsv, or control port remains.
- [ ] Remove temporary config/drop-in files.
- [ ] Confirm both source worktrees are clean and still identify the pinned builds
      recorded in evidence (or document a deliberate post-run fast-forward).
- [ ] Write `restore_verdict=PASS` plus module hash and unit count into run state.
- [ ] Re-run the archive checksum verification after the final report is written.

If service startup needs a temporary timeout override, remove the override after
startup and record that it was used. Do not leave the machine in a subtly new
operational state.

## 15. Final report template

The final operator report must contain:

1. Full branch and reference SHAs and artifact hashes.
2. T0 total plus every failing package/test; state `rtx_mcp` count explicitly.
3. T1 PASS/FAIL/withheld counts and dash peak/floor.
4. T2 duration, bots, polls, movement/stall values, every quad/pent lay interval,
   take counts, averages, unfinished interval if any, and metric sources.
5. T3 pipeline status. Give score only for a complete full-duration match; name
   client/process failure otherwise. Distinguish one pipeline match from an
   aggregate quality verdict.
6. T4 rungs, scores, and reached skill.
7. Retractions/corrections and why they occurred.
8. Local and remote field-verification counts and deviations.
9. Dashboard URL, worker version, and remote HTML hash.
10. Run archive path, `SHA256SUMS` hash, and restoration verdict.
11. Whether upstream advanced during the run.

Before sending, answer these five questions literally:

- [ ] Did every claimed tier actually finish?
- [ ] Can every displayed number be traced to an observation, not merely a null,
      default zero, parser fallback, or stale file?
- [ ] Did both T3 sides remain alive for the whole match?
- [ ] Was the exact remote artifact, not just the local build, verified?
- [ ] Am I saying **attempted**, **measured**, **complete**, **green**, and
      **published** according to the definitions at the top of this runbook?
