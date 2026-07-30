"""Turn `cargo test` output into the summary JSON t0-import expects.

The adapter deliberately does not run Cargo (README says so); this is the
producer that feeds it. Names follow the existing envelope's convention:
one entry per test binary, "<crate> (doc)" for doc-tests.
"""
import json
import re
import sys

RUNNING = re.compile(r"^\s+Running (?:unittests |tests )?\S+ \(.*/deps/([A-Za-z0-9_]+)-[0-9a-f]{16}\)")
DOCTEST = re.compile(r"^\s+Doc-tests (\S+)")
RESULT = re.compile(
    r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed;"
)

modules = []
current = None
for line in sys.stdin:
    match = RUNNING.match(line)
    if match:
        current = match.group(1)
        continue
    match = DOCTEST.match(line)
    if match:
        current = f"{match.group(1)} (doc)"
        continue
    match = RESULT.match(line)
    if match and current is not None:
        passed = int(match.group(2))
        failed = int(match.group(3))
        modules.append(
            {"name": current, "tests": passed + failed, "passed": passed}
        )
        current = None

if not modules:
    raise SystemExit("no test results parsed — refusing to write an empty summary")

# Same binary can appear once per target; merge by name so the module list is
# stable regardless of cargo's ordering.
merged: dict[str, dict] = {}
for module in modules:
    slot = merged.setdefault(
        module["name"], {"name": module["name"], "tests": 0, "passed": 0}
    )
    slot["tests"] += module["tests"]
    slot["passed"] += module["passed"]

document = {
    "modules": sorted(merged.values(), key=lambda m: m["name"]),
    "quality_floors": [],
}
json.dump(document, open(sys.argv[1], "w"), indent=1)
total = sum(m["tests"] for m in merged.values())
passed = sum(m["passed"] for m in merged.values())
print(f"wrote {sys.argv[1]}: {len(merged)} modules, {passed}/{total} passed")
