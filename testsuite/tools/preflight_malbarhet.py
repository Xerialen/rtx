#!/usr/bin/env python3
"""Static targetability preflight for measurement tools.

Reports hard-coded hosts, ports, and systemd units with file/line locations.
Only the dedicated tbx endpoint ports and tbx-d1..d4 units are accepted.
No network or systemd operation is performed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ALLOWED_PORTS = frozenset(range(27592, 27596)) | frozenset(range(27996, 28000))
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "tbx-d1", "tbx-d2", "tbx-d3", "tbx-d4"})
ALLOWED_UNITS = frozenset({"tbx-d1", "tbx-d2", "tbx-d3", "tbx-d4"})

PORT_PATTERNS = (
    re.compile(r"(?<![\w.]):(?P<value>\d{4,5})\b"),
    re.compile(r"(?:--(?:game-)?port\s+|[\"']?\b(?:[A-Za-z_]*port)\b[\"']?\s*[:=]\s*)[\"']?(?P<value>\d{4,5})\b", re.IGNORECASE),
)
HOST_PATTERNS = (
    re.compile(r"\b(?:host|hostname)\b\s*[:=]\s*[\"'](?P<value>[^\"']+)[\"']", re.IGNORECASE),
    re.compile(r"\b(?P<value>fasttrack-(?:ra|main-test)|tbx-d[1-4]|localhost|127\.0\.0\.1)\b"),
)
UNIT_PATTERNS = (
    re.compile(r"\bsystemctl\b[^\n#]*(?:start|restart|stop)\s+(?P<value>[A-Za-z0-9_.@-]+)"),
    re.compile(r"[\"'](?P<value>(?:fasttrack-(?:ra|main-test)|tbx-d[1-4])(?:\.service)?)[\"']"),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str
    value: str
    allowed: bool


def source_files(target: Path) -> Iterable[Path]:
    if target.is_file():
        yield target
        return
    if not target.is_dir():
        raise FileNotFoundError(target)
    for path in sorted(target.rglob("*")):
        if path.is_file() and not any(part in {".git", "__pycache__"} for part in path.parts):
            yield path


def scan(target: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in source_files(target):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            seen: set[tuple[str, str]] = set()
            for pattern in PORT_PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group("value")
                    key = ("port", value)
                    if key not in seen:
                        findings.append(Finding(path, number, "port", value, int(value) in ALLOWED_PORTS))
                        seen.add(key)
            for pattern in HOST_PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group("value")
                    key = ("host", value)
                    if key not in seen:
                        findings.append(Finding(path, number, "host", value, value.lower() in ALLOWED_HOSTS))
                        seen.add(key)
            for pattern in UNIT_PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group("value")
                    base = value.removesuffix(".service")
                    key = ("systemd-unit", value)
                    if key not in seen:
                        findings.append(Finding(path, number, "systemd-unit", value, base in ALLOWED_UNITS))
                        seen.add(key)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", type=Path, help="measurement tool file or directory")
    args = parser.parse_args(argv)
    try:
        findings = scan(args.tool)
    except FileNotFoundError:
        parser.error(f"path does not exist: {args.tool}")

    for finding in findings:
        status = "ALLOWED" if finding.allowed else "OUTSIDE-TBX"
        print(f"{finding.path}:{finding.line}: {finding.kind}={finding.value} [{status}]")
    outside = sum(not finding.allowed for finding in findings)
    print(f"preflight: findings={len(findings)} outside_tbx={outside}")
    return 1 if outside else 0


if __name__ == "__main__":
    raise SystemExit(main())
