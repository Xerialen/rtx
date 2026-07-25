# DM3 mega navpatch provenance

The built-in `dm3-mega-m2-v1` patch makes the SNG→mega chain part of a normal DM3 nav build.
There is no editor export or post-boot curation step. The game embeds
[`dm3-mega-v1.json`](../crates/rtx-nav/data/navpatches/dm3-mega-v1.json), applies it immediately
after the ordinary graph build, rebuilds derived topology, and installs the graph only if every
identity and count below matches.

## Pinned chain

| identity | SHA-256 or count |
|----------|------------------|
| Patch schema | `rtx-nav-postbuild-patch/2` |
| Patch id | `dm3-mega-m2-v1` |
| Patch-manifest SHA-256 | `771d69033620be6eeaeabc2dc703ad0c884a89045035544cec96650a4a544aeb` |
| Stock DM3 BSP SHA-256 | `aec9edbb727c0a206edc2c0688775ce8242c0d51e1ee7583c7126c76f7c3b2f1` |
| Source graph SHA-256 | `5c15ad55f1af87914ca70ccb125d9170deb6079cc0c7b86b648b0a30aebc3836` |
| Source graph | 4,631 cells; 50,993 links |
| Geometric removal selectors | 174 selectors; exactly 199 matched links |
| Added traversal profiles | 12 speed-jump profiles |
| Patched graph SHA-256 | `af319dbea3bc7a4eb448475b3ab770885004393fa5f01dc3288dde3d6ca7935c` |
| Patched graph | 51,005 total links; 50,806 active links |

The source SHA is scoped to this exact nav-generator configuration, stored as
`source_build` in the manifest:

```text
stock_movement=false
hooks=None
double_jump=false
speed_jump={gravity=800, accel=10, maxspeed=320, friction=4, stopspeed=100, curl=true}
rocket_jump={gravity=800, rj_extra=0, accel=10, maxspeed=320, friction=4,
             stopspeed=100, cost_scale=0.35}
```

This is a snapshot of the effective build inputs, not a later reread of cvars. A long background
build therefore remains bound to the configuration that actually produced its graph even if an
operator changes a cvar while it runs.

The graph digest uses the `rtx-nav-graph-sha256/1` canonical encoding implemented in
[`patch.rs`](../crates/rtx-nav/src/navmesh/patch.rs). It covers ordered cell geometry, ordered
link topology and link kinds, removal state, link cost, and the complete speed-jump execution
contract. Link order is intentionally significant because routes and side tables use link indices
as identities.

Each removal selector is a link kind plus exact source and target cell origins and an expected
multiplicity. Each addition is a complete speed-jump profile. The manifest is the only coordinate
source for both patching and acceptance scenarios.

Application first checks its domain, then remains fail-closed inside that domain:

1. If the effective source-build configuration differs, the patch is explicitly skipped and the
   legitimate unpatched graph is installed. This is not `nav_patch_error`.
2. If the configuration matches, the runtime BSP digest must match the pinned BSP.
3. The freshly generated source graph must match both source counts and source digest.
4. Every geometric selector must match exactly its declared multiplicity, without overlap.
5. The removal and addition totals must match.
6. After rebuilding derived topology, total/active counts and the patched digest must match.
7. Only then is the patched graph installed and its provenance exposed through control-channel
   status.

A matching-config mismatch in steps 2–6 keeps the graph uninstalled and reports
`nav_patch_error`; it never falls back to an unverified or partly patched graph. A config skip is
logged with the patch id, manifest SHA and first differing build input. For example,
`rtx_bot_rocketjump 0` produces no RJ links and now installs that unpatched graph instead of
comparing its link count with the RJ-enabled source contract.

## One-command fresh-boot gate

From any fresh Linux/WSL clone, supply a runtime-asset directory and run:

```sh
./scripts/test-dm3-mega-fresh-boot.sh /path/to/runtime-assets
```

The asset directory must contain:

```text
mvdsv
id1/pak0.pak
qw/maps/dm3.bsp
```

The wrapper checks the BSP digest before building, builds `librtx.so` from the current checkout,
and invokes two serial ignored real-server integration tests. The first boots with
`rtx_bot_rocketjump 0` and proves that the config mismatch is logged, no patch error is exposed,
the graph reaches `ready`, and it contains no RJ links. The second boots with the pinned config,
forces `rtx_walljump 0` and `rtx_doublejump 0`, waits for a fresh DM3 nav build, and compares the
server-reported manifest/source/patched digests and every link count with the checkout's manifest.
It then runs 40 alternating `sng_mega_w`/`sng_mega_s` item trials over the typed msgpack control
channel. Passing requires at least 38/40.

The retained `fresh-boot-report.json` records BSP, manifest, built-module, source-graph and
patched-graph digests; source/removal/addition/final counts; per-scenario results; failure reasons;
elapsed times; and the disabled movement cvars. The wrapper prints both the isolated runtime path
and the report path.
