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

message=$(git log --format=%B -1 "$sha")
mapfile -t agent_lines < <(printf '%s\n' "$message" | grep '^Agent:' || true)
if [[ ${#agent_lines[@]} -eq 0 ]]; then
    echo "JÄVSKOLL FEL: okänd författare — kräv Agent:-trailer på ${sha}." >&2
    exit 2
fi
if [[ ${#agent_lines[@]} -ne 1 ]]; then
    echo "JÄVSKOLL FEL: tvetydig författare — exakt en Agent:-trailer krävs på ${sha}." >&2
    exit 2
fi

agent=${agent_lines[0]#Agent:}
agent=${agent#"${agent%%[![:space:]]*}"}
agent=${agent%"${agent##*[![:space:]]}"}
case "$agent" in
    opus5|grok|grok2|deepseek|terra|qwen|fable) ;;
    *)
        echo "JÄVSKOLL FEL: okänd Agent: '${agent}' på ${sha}; tillåtna: opus5/grok/grok2/deepseek/terra/qwen/fable." >&2
        exit 2
        ;;
esac

reviewer_norm=${reviewer,,}
if [[ "$reviewer_norm" == "$agent" ]]; then
    echo "JÄV: granskare '${reviewer}' är commit-agent för ${sha} (Agent: ${agent}); välj annan granskare." >&2
    exit 1
fi

echo "JÄVSKOLL OK: ${sha} Agent='${agent}', granskare='${reviewer}'."
