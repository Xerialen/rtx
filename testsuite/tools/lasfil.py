#!/usr/bin/env python3
"""Låsfilsgenerator (U2e). CI writes lock bytes; Fable supplies the token.

token= is authoritative (rtx-ctlproto). This program NEVER invents a
token — --token is required and used verbatim. Owner defaults to the
campaign identity `fable`. Unit+ports must be an allowed deploy pair.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from d_failclosed import ALLOWED_DEPLOY_PAIRS, CAMPAIGN_OWNER


def generate_lock(
    *,
    token: str,
    unit: str,
    qwprogs_sha256: str,
    mvdsv_sha256: str,
    owner: str = CAMPAIGN_OWNER,
    ts: str | None = None,
    bridge: bool = False,
) -> str:
    tok = (token or "").strip()
    if not tok:
        raise ValueError("token krävs — generatorn författar aldrig token själv")
    if "\n" in tok or "\r" in tok:
        raise ValueError("token får inte innehålla radbrytning")
    if unit not in ALLOWED_DEPLOY_PAIRS:
        raise ValueError(f"unit {unit!r} är inte tbx-d1/d3")
    ctl, game = ALLOWED_DEPLOY_PAIRS[unit]
    for label, sha in (("qwprogs", qwprogs_sha256), ("mvdsv", mvdsv_sha256)):
        s = (sha or "").strip().lower()
        if len(s) != 64 or any(c not in "0123456789abcdef" for c in s):
            raise ValueError(f"{label}-sha256 är inte SHA-256")
    when = ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        f"owner={owner}\n"
        f"unit={unit}\n"
        f"ctl_port={ctl}\n"
        f"game_port={game}\n"
        f"token={tok}\n"
        f"qwprogs_sha256={qwprogs_sha256.strip().lower()}\n"
        f"mvdsv_sha256={mvdsv_sha256.strip().lower()}\n"
        f"ts={when}\n"
    )
    if bridge:
        body = f"{tok}\n" + body
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--token", required=True, help="kampanjtoken (Fable). Aldrig genererat här.")
    ap.add_argument("--unit", required=True, choices=sorted(ALLOWED_DEPLOY_PAIRS))
    ap.add_argument("--qwprogs-sha256", required=True)
    ap.add_argument("--mvdsv-sha256", required=True)
    ap.add_argument("--owner", default=CAMPAIGN_OWNER)
    ap.add_argument("--ts", default=None, help="ISO-8601 Z; default now UTC")
    ap.add_argument("--bridge", action="store_true", help="bare token on line 1 + eight fields")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        body = generate_lock(
            token=args.token,
            unit=args.unit,
            qwprogs_sha256=args.qwprogs_sha256,
            mvdsv_sha256=args.mvdsv_sha256,
            owner=args.owner,
            ts=args.ts,
            bridge=args.bridge,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.out:
        args.out.write_bytes(body.encode("utf-8"))
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
