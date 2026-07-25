#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/test-dm3-mega-fresh-boot.sh [runtime-root]

Build the game module from this checkout and boot brand-new isolated DM3 server
trees. First verify that an alternate nav-build config skips the patch while
keeping its graph usable; then verify the repo-baked patch provenance and run
40 typed-msgpack mega item trials. The acceptance floor is 38/40 (95%).

runtime-root defaults to $RTX_DM3_FRESH_BOOT_ROOT, then ./playground. It must
contain:
  mvdsv
  id1/pak0.pak
  qw/maps/dm3.bsp

The tests create isolated server trees below the system temp directory. Their
paths and the acceptance run's fresh-boot-report.json are printed and retained.

Example:
  ./scripts/test-dm3-mega-fresh-boot.sh /srv/quake/rtx-test-assets
EOF
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
esac

if (( $# > 1 )); then
    usage >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
runtime_root="${1:-${RTX_DM3_FRESH_BOOT_ROOT:-$repo_root/playground}}"
runtime_root="${runtime_root%/}"

mvdsv="$runtime_root/mvdsv"
pak0="$runtime_root/id1/pak0.pak"
bsp="$runtime_root/qw/maps/dm3.bsp"
manifest="$repo_root/crates/rtx-nav/data/navpatches/dm3-mega-v1.json"

for asset in "$mvdsv" "$pak0" "$bsp" "$manifest"; do
    if [[ ! -f "$asset" ]]; then
        echo "error: required file is missing: $asset" >&2
        echo "run with --help for the expected runtime layout" >&2
        exit 2
    fi
done
if [[ ! -x "$mvdsv" ]]; then
    echo "error: mvdsv is not executable: $mvdsv" >&2
    exit 2
fi
if ! command -v cargo >/dev/null 2>&1; then
    echo "error: cargo is required" >&2
    exit 2
fi
if ! command -v sha256sum >/dev/null 2>&1; then
    echo "error: sha256sum is required" >&2
    exit 2
fi

expected_bsp_sha256="$(
    sed -n 's/^[[:space:]]*"bsp_sha256":[[:space:]]*"\([0-9a-f]\{64\}\)",*[[:space:]]*$/\1/p' "$manifest"
)"
if [[ ${#expected_bsp_sha256} -ne 64 ]]; then
    echo "error: could not read bsp_sha256 from $manifest" >&2
    exit 2
fi
actual_bsp_sha256="$(sha256sum "$bsp" | awk '{print $1}')"
if [[ "$actual_bsp_sha256" != "$expected_bsp_sha256" ]]; then
    echo "error: DM3 BSP SHA-256 mismatch" >&2
    echo "  expected: $expected_bsp_sha256" >&2
    echo "  actual:   $actual_bsp_sha256" >&2
    exit 2
fi

cd "$repo_root"
checkout="unknown"
if command -v git.exe >/dev/null 2>&1 && candidate="$(git.exe rev-parse HEAD 2>/dev/null)"; then
    checkout="$candidate"
elif command -v git >/dev/null 2>&1 && candidate="$(git rev-parse HEAD 2>/dev/null)"; then
    checkout="$candidate"
fi
manifest_sha256="$(sha256sum "$manifest" | awk '{print $1}')"
echo "checkout:        $checkout"
echo "manifest SHA-256: $manifest_sha256"
echo "DM3 BSP SHA-256:  $actual_bsp_sha256"
echo "building release game module..."
cargo build --locked --release -p rtx-game

qwprogs="$repo_root/target/release/librtx.so"
if [[ ! -f "$qwprogs" ]]; then
    echo "error: release build did not produce $qwprogs" >&2
    echo "this runner currently requires a Linux/WSL mvdsv and librtx.so" >&2
    exit 2
fi
echo "qwprogs SHA-256:  $(sha256sum "$qwprogs" | awk '{print $1}')"
echo "starting isolated fresh-boot acceptance..."

RTX_DM3_FRESH_BOOT_MVDSV="$mvdsv" \
RTX_DM3_FRESH_BOOT_PAK0="$pak0" \
RTX_DM3_FRESH_BOOT_BSP="$bsp" \
RTX_DM3_FRESH_BOOT_QWPROGS="$qwprogs" \
    cargo test --locked -p rtx-game --test dm3_mega_fresh_boot -- --ignored --nocapture --test-threads=1
