# The demo corpus — what the baseline and the engine constants are calibrated against

`/demos` is gitignored (`.gitignore:7` — issue #41 cites `:6`, which is the
`.DS_Store` line), so none of the recordings that fund the
numbers in `docs/baseline/README.md` or the constants in `crates/rtx-nav` travel
with the tree. This file is the manifest: for every recording, its sha256, its
size, the exact command that fetches it, and the line in the tree that leans on
it. Nothing here is a copy of a demo — this is the retrieval and verification
layer only.

Inventory taken 2026-08-23 against `lanister` (`100.64.0.2`), read-only, at
tree state `e133cf7`. Two of the five recordings named in the source tree could
not be found on that machine; they are listed under [SAKNAS](#saknas) with the
search that failed, not with a guessed path.

---

## 1. The 4on4 movement baseline (1 file, 14.4 MB)

`docs/baseline/README.md:4` measures the whole movement baseline against one
human match — 4330 segments reduced to 544 distinct movements — and the
`flyprobe line` invocation at `README.md:7-9` names it as its input.

| | |
|---|---|
| **Name in the tree** | `demos/20260507-2107_4on4_]sr[_vs_book[dm3].mvd` (`docs/baseline/README.md:4`) |
| **Name on disk** | `4on4_book_vs_]sr[[dm3]20260507-2107.mvd` |
| **sha256** | `721db48a21a659328e5030964d21037ad6ee92dded72e157d55972e41be81e6b` |
| **Size** | 14 434 583 B |
| **Calibrates** | The entire movement baseline: the p50/p90 table, the 120/135 arrival figure, the human travelling/manoeuvring targets, and `LineScore::comparable`'s exclusion set |

**The name differs from the tree's and no file carries the tree's name.** The
tree writes the teams `]sr[_vs_book` with the timestamp first; the recording on
disk writes `book_vs_]sr[` with the timestamp last, which is the QTV recorder's
own convention. The match identity is otherwise a single unambiguous hit: same
server (`212.53.165.161:28000`), same date and minute (2026-05-07 21:07), same
map (dm3), same two teams, and it is the **only** dm3 recording of those teams
at that timestamp anywhere on the machine. Four hardlinks to that one inode
exist; none of them is named as `README.md:4` names it. The tree's name is
therefore a rename that was never carried out, or was carried out on a machine
that is not this one. **Treated here as the same recording, flagged rather than
silently equated** — an owner or a re-run of the `flyprobe line` command above
(expect 4330 segments → 544 distinct) settles it.

One of the four hardlinks is content-addressed, and its filename is exactly this
file's sha256 — an independent confirmation of the hash:

```
/mnt/usb-ssd/torrent-set/721db48a21a659328e5030964d21037ad6ee92dded72e157d55972e41be81e6b.mvd
```

### Fetch

```
scp "lanister:/mnt/usb-ssd/4on4-corpus/demos/4on4_book_vs_]sr[[dm3]20260507-2107.mvd" demos/
```

The double quotes are load-bearing: the name contains `[` and `]`, which both
the local and the remote shell would otherwise treat as a glob. The path above
is the corpus copy; the escaping-free alternative is the content-addressed
hardlink, which is byte-identical:

```
scp lanister:/mnt/usb-ssd/torrent-set/721db48a21a659328e5030964d21037ad6ee92dded72e157d55972e41be81e6b.mvd demos/
```

If you fetch via the second path, rename it to the name in the manifest before
running `sha256sum -c`.

---

## 2. The owner's 18 route demos (18 files, 1.8 MB total)

`testsuite/scenarios/dm3/generate_from_routes.py:118` anchors every drill's
ordered waypoint gates on these: *"Anchored on the owner's own 18 route demos
(`lanister:~/dm3-drillar`, read with his qwd_v2 extractor): every point below is
a position he actually occupied, in the order he occupied them."* Without them
the waypoint boxes in that file cannot be re-derived, only trusted.

All 18 are present. Each recording is one named route; the drill it anchors
carries the same name.

| File | sha256 | Size (B) |
|---|---|---|
| `(hex)quad-to-sng.qwd` | `9dbb092bc25864a7ac9e04a6d5776e75c957b7815a02ee4b94ae82dec301f983` | 90 332 |
| `(hex)ratop-to-ssg.qwd` | `8b48522b7749eaaa097b5291c388b7467cda3781637611d89ab537af2aac4bf9` | 104 220 |
| `(hex)sng-to-quad.qwd` | `85fd9f10fb98c61fe2e108069170eefcd328beb0d6bcd2b985021772ba0581b8` | 88 018 |
| `(hex)ssg-to-ratop.qwd` | `99042e349e948001875cce80ff7f910a8aa141f3fa63a6d05579a9f837620312` | 118 406 |
| `(spawn)lift-to-pent-to-pentmega.qwd` | `ed39fc970752182aa4d743412f75fbf2a3908c4cb56c377b7077432f656e7b36` | 112 723 |
| `(spawn)ra-tunnel-to-lg.qwd` | `cca3b4f135aa45d0c0fc20b044a6c8580f3f918f825b858fa4949ae8c7d79c08` | 79 597 |
| `(spawn)rarox-to-quad.qwd` | `3e5008dd743670269eac5d0fe07484507bd37a05e06e874ad8498ab666ba23ea` | 75 716 |
| `(spawn)rl-to-ratop-xer.qwd` | `5930b78c36f1e59c8525e563300fd51e95cfec55282ebbbff0309af08f73ccfc` | 158 204 |
| `(spawn)sngspawn-to-ring-to-ratop.qwd` | `0f284b8837f16c2d75c5fe0288b88a284c705b94dabab975ad10a1aa32bca954` | 111 802 |
| `highbridge-to-rl.qwd` | `4f6e1863a12d1e047f7f70ef52326b0e3085d343ebd5352778b1a7c16453ac02` | 74 260 |
| `lg-to-pent-to-pentmega.qwd` | `38bdfe8e69cf3709a1fcfa9f3ad8d605eb602cf1ef927d4dc75d87b828dc2892` | 116 796 |
| `lifts-or-ring-to-sngmega.qwd` | `3f38570fa07d7a03e1a3fbfa0129e49e11ccc92cdde5cd6890a29eeddfa5464b` | 118 991 |
| `ralow-to-ratop.qwd` | `6325e85420cc209c575540fb59ec65422c1781c682194a601a455876a8676838` | 97 160 |
| `ring-to-ratop.qwd` | `1c77ef5dadad1160e49543b52dc0282546a25c38e07ff67e84754cf2594e595d` | 127 731 |
| `ring-to-rl.qwd` | `6a49f39531c9cc4e3f583563ba88a84cd10cf5a73a606cdc4f8258f5273122b9` | 87 743 |
| `rj-pent-to-lifts-to-window-to-quad.qwd` | `18b3f24f5ae874dffa20b7f646f334466902b01e8b69a0382235b1bfea544e85` | 102 722 |
| `sngspawns-to-sngmega.qwd` | `1867a1a54fc610f112613530b3bfc0c9ddea7fb5e1cfbb2d32be0f7280c907b3` | 103 858 |
| `window-to-rl.qwd` | `ef8d074195690fbd789907debaf8669c6ede02761a7847a8a79cf3e55c703866` | 71 960 |

The parentheses in five of the names are part of the filename, not shell syntax.

### Fetch

```
scp "lanister:~/dm3-drillar/*.qwd" demos/
```

The quotes keep the glob on the remote side; the parentheses in the resulting
names need no further escaping.

### Companions in the same directory (not demos)

Three non-demo files sit beside them. They are the extractor and its output —
carry them if you intend to re-derive the waypoints rather than re-read the
tapes.

| File | sha256 | Size (B) | What it is |
|---|---|---|---|
| `dm3-drillar-routes.json` | `1239efa0d9a6477654927527316130b61a7e01a6fe534ed78b72e4b0ab9f0918` | 42 036 | Routes extracted from the 18 tapes |
| `dm3-items.json` | `24e79060f2588ca9270a44559ba6bf582c7d031819f88dabe9c2cac09fd7f5d5` | 5 897 | dm3 item positions |
| `extract-routes.py` | `79c87bbbb9ab6e90191a9d175be8926dff899566381cb794432e44ce07519131` | 12 923 | The qwd_v2 extractor referenced at `generate_from_routes.py:118` |

---

## 3. SAKNAS — the two engine-constant ground truths {#saknas}

**`demos/dm3_rastairs.qwd` and `demos/dm3_rlstrafejump.qwd` were not found.**

These are not incidental. They are the stated ground truth for movement
constants and for one live test assertion, at these lines on `e133cf7`:

| Line | What leans on it |
|---|---|
| `crates/rtx-nav/src/navmesh/physics.rs:72` | *"Ground truth for both: `demos/dm3_rastairs.qwd`, `demos/dm3_rlstrafejump.qwd`"* — the air-strafe/prestrafe split, and the warning not to unclamp `bhop_k` from `AIR_CAP` |
| `crates/rtx-nav/src/navmesh/physics.rs:91` | *"Calibrated against `demos/dm3_rastairs.qwd`, not intuition"* — `SJ_MARGIN` is 1.05 because the human clears the two gaps carrying **438** and **395** ups where the chords need 397 and 388 |
| `crates/rtx-nav/src/navmesh/physics.rs:193` | The side-jump pass exists because `dm3_rlstrafejump.qwd` crosses the z=152 pit off a balcony 448u wide and two grid rows deep, ~63° between run-up and leap |
| `crates/rtx-nav/src/strafe.rs:159` | `air_correct_held` latches the strafe side because in that demo the side key is held at `-400` for every airborne frame while yaw climbs monotonically 227° → 290° |
| `crates/rtx-nav/src/navmesh/mod.rs:3117` | A `#[test]` asserting the side-jump pass finds the z=152 pit crossing — derived from that demo |
| `crates/rtx-nav/src/navmesh/mod.rs:3168` | That test's failure message: *"the `dm3_rlstrafejump` demo does it from a standing start in 144u of run-up"* |

Issue #41 cites `navmesh/mod.rs:2968`; on `e133cf7` those two citations sit at
**3117** and **3168**. The file grew — the numbers above are the current ones.

### Where the search was run, and what it covered

On `lanister`, every mounted filesystem (`/`, `/boot`, `/boot/efi`,
`/mnt/usb-ssd`, `/dev/shm`, `/run*`):

```
find / -iname "*rastairs*" -o -iname "*rlstrafejump*" -o -iname "*strafejump*" 2>/dev/null
```

No output. Also checked, all empty:

- every `demos/` directory on the machine (51 of them under `~` and `/mnt`, incl.
  `~/servexeri-mirror/4on4-corpus/demos`, `/mnt/usb-ssd/4on4-corpus/demos`,
  `~/.local/share/qw-fasttrack/demos`, the `kbot` server dirs)
- `~/dm3-drillar` (the 18 above are the whole directory)
- the fork's entire history — `git log --all --diff-filter=A -- "*rastairs*"
  "*rlstrafejump*" "demos/*"` returns nothing, so they were never committed on
  any branch before `/demos` was ignored

**Consequence, stated plainly:** the numbers 438/395/397/388 at `physics.rs:91`,
the 448u × 64u balcony at `physics.rs:193`, and the 144u run-up at `mod.rs:3168`
currently cannot be re-derived by anyone. A change to `SJ_MARGIN` or to the
side-jump envelope can be argued but not checked. Recovering these two
recordings, or replacing the citations with a reproducible derivation, is the
only way to close that.

---

## 4. Verifying a fetched corpus

Write the block below to `demos/SHA256SUMS`, then from `demos/`:

```
sha256sum -c SHA256SUMS
```

Every line must read `OK`. A single flipped byte fails the file it belongs to
and returns exit 1 — this was negative-controlled on 2026-08-23 by corrupting
one byte of `window-to-rl.qwd` (`window-to-rl.qwd: FAILED`, exit 1) and
restoring it (19 × `OK`, exit 0). If `sha256sum -c` reports every file `OK` on a
corpus you have not just fetched, check that you are not verifying against a
`SHA256SUMS` generated from those same files.

```
721db48a21a659328e5030964d21037ad6ee92dded72e157d55972e41be81e6b  4on4_book_vs_]sr[[dm3]20260507-2107.mvd
9dbb092bc25864a7ac9e04a6d5776e75c957b7815a02ee4b94ae82dec301f983  (hex)quad-to-sng.qwd
8b48522b7749eaaa097b5291c388b7467cda3781637611d89ab537af2aac4bf9  (hex)ratop-to-ssg.qwd
85fd9f10fb98c61fe2e108069170eefcd328beb0d6bcd2b985021772ba0581b8  (hex)sng-to-quad.qwd
99042e349e948001875cce80ff7f910a8aa141f3fa63a6d05579a9f837620312  (hex)ssg-to-ratop.qwd
ed39fc970752182aa4d743412f75fbf2a3908c4cb56c377b7077432f656e7b36  (spawn)lift-to-pent-to-pentmega.qwd
cca3b4f135aa45d0c0fc20b044a6c8580f3f918f825b858fa4949ae8c7d79c08  (spawn)ra-tunnel-to-lg.qwd
3e5008dd743670269eac5d0fe07484507bd37a05e06e874ad8498ab666ba23ea  (spawn)rarox-to-quad.qwd
5930b78c36f1e59c8525e563300fd51e95cfec55282ebbbff0309af08f73ccfc  (spawn)rl-to-ratop-xer.qwd
0f284b8837f16c2d75c5fe0288b88a284c705b94dabab975ad10a1aa32bca954  (spawn)sngspawn-to-ring-to-ratop.qwd
4f6e1863a12d1e047f7f70ef52326b0e3085d343ebd5352778b1a7c16453ac02  highbridge-to-rl.qwd
38bdfe8e69cf3709a1fcfa9f3ad8d605eb602cf1ef927d4dc75d87b828dc2892  lg-to-pent-to-pentmega.qwd
3f38570fa07d7a03e1a3fbfa0129e49e11ccc92cdde5cd6890a29eeddfa5464b  lifts-or-ring-to-sngmega.qwd
6325e85420cc209c575540fb59ec65422c1781c682194a601a455876a8676838  ralow-to-ratop.qwd
1c77ef5dadad1160e49543b52dc0282546a25c38e07ff67e84754cf2594e595d  ring-to-ratop.qwd
6a49f39531c9cc4e3f583563ba88a84cd10cf5a73a606cdc4f8258f5273122b9  ring-to-rl.qwd
18b3f24f5ae874dffa20b7f646f334466902b01e8b69a0382235b1bfea544e85  rj-pent-to-lifts-to-window-to-quad.qwd
1867a1a54fc610f112613530b3bfc0c9ddea7fb5e1cfbb2d32be0f7280c907b3  sngspawns-to-sngmega.qwd
ef8d074195690fbd789907debaf8669c6ede02761a7847a8a79cf3e55c703866  window-to-rl.qwd
```

The full fetch, from an empty checkout:

```
mkdir -p demos
scp "lanister:~/dm3-drillar/*.qwd" demos/
scp "lanister:/mnt/usb-ssd/4on4-corpus/demos/4on4_book_vs_]sr[[dm3]20260507-2107.mvd" demos/
# write the block above to demos/SHA256SUMS, then:
( cd demos && sha256sum -c SHA256SUMS )
```

Verified end to end on 2026-08-23: 19 files fetched by exactly these two
commands, 19 × `OK` against the hashes computed on `lanister`.

---

## 5. Why the demos are not in this repository

`/demos` is gitignored and stays that way; 16 MB of binary recordings do not
belong in the history. Issue #41 proposes Git LFS or a published URL as the
distribution mechanism. **Neither is set up here, deliberately:** both spend
owner resources — LFS bandwidth and storage quota on the account, or hosting
plus a public-exposure decision about match recordings that are not ours to
republish. That is an owner cost decision, not a documentation one. Until it is
taken, `lanister` is the source of truth and the two `scp` commands above are
the retrieval path. The hashes in this file are what make any future
distribution channel verifiable against the corpus as it stood on 2026-08-23.
