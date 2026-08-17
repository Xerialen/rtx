#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <commit-sha> <proposed-reviewer>" >&2
    exit 2
fi

sha=$1
reviewer=$2
if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    echo "JÄVSKOLL FEL: okänd commit: ${sha}" >&2
    exit 2
fi

author=$(git log --format=%an -1 "$sha")
if [[ "$reviewer" == "$author" ]]; then
    echo "JÄV: granskare '${reviewer}' är författare till ${sha} (${author}); välj annan granskare." >&2
    exit 1
fi

echo "JÄVSKOLL OK: ${sha} författare='${author}', granskare='${reviewer}'."
