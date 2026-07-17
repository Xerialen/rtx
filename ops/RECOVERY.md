# MLX Phase 1 recovery

The Phase 1 match jobs live under `~/mlx/jobs/` on vmonster. Every long run is
owned by a tmux session named `mlx-<job-id>` and can survive loss of the
pinnacle, VPN, Mac, or SSH connection.

## Reconnect and inspect before acting

Connect through the configured ProxyJump and inspect all jobs before starting
or resuming anything:

```bash
ssh vmonster
~/mlx/bin/mlx-status
tmux list-sessions
```

Confirm the intended job ID, heartbeat age, completed/running/failed counts,
leases, and tmux state. Also confirm at least 100 GiB disk remains. Never start
a second runner while its `mlx-<job-id>` session is present.

## Attach and detach

```bash
~/mlx/bin/mlx-attach <job-id>
```

Detach without stopping the job with `Ctrl-b d`. A disconnected SSH client or
detached tmux client does not stop the runner.

## Resume an interrupted job

Run status first, then:

```bash
~/mlx/bin/mlx-resume <job-id>
```

Every resume creates a new `runner_run_id` and performs the mandatory
orphan-sweep before selecting work. Leases from an older runner generation are
validated against PID, process-group ID, and `/proc/<pid>/stat` start-time
nonce; owned old process groups are terminated and `running` matches are
replanned. Completed matches are not rerun.

Do not delete lease files or kill individual mvdsv/rtx-client processes by
hand. If the sweep refuses a recycled PID/nonce mismatch, preserve the job
directory and logs and investigate before taking further action.

## Retry only failed matches

```bash
~/mlx/bin/mlx-retry-failed <job-id>
```

This also starts a new runner generation and runs only matches whose terminal
state is `failed`. Check `mlx-status` again after it finishes.

## Stop safely

```bash
~/mlx/bin/mlx-stop <job-id>
```

The command removes the tmux session if present, performs an owned
process-group sweep, replans any `running` matches, and writes state `stopped`.
It does not delete completed matches, demos, or unsynced publications.

## Export job metadata

```bash
~/mlx/bin/mlx-export <job-id>
```

The metadata-only archive is written under `~/mlx/export/`. Quake assets and
demos are deliberately excluded.

## Sync unsynced demos

Demo relay commands run from pinnacle's Ubuntu-24.04 WSL checkout, not from
vmonster:

```powershell
wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/benya/projects/quakeworld/mlx/ops/list-unsynced-demos.sh
wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/benya/projects/quakeworld/mlx/ops/sync-demos.sh
wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/benya/projects/quakeworld/mlx/ops/verify-demo-sync.sh
wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/benya/projects/quakeworld/mlx/ops/list-corrupt-demos.sh
```

The relay consumes only `.ready` publications, resumes partial rsync data,
verifies the hub SHA-256 and HTTP response, writes an idempotent transfer event,
and only then moves vmonster `outbox` to `synced`. Never delete an unsynced demo.

If WSL is wedged, leave publications in `~/mlx/demos/outbox/` and retry later.
Do not run `wsl --terminate` or `wsl --shutdown` from MLX recovery because other
work may own the distribution.

## Stale-state rule

A job is operationally stale when its heartbeat is older than five minutes or
leases exist without its tmux session. Treat that as a reason to inspect and
run the supported resume/stop command, not as permission to remove locks.
Breaking a stale state is safe only after the command's new-generation
orphan-sweep has reconciled PID, PGID, nonce, and `runner_run_id` ownership.
