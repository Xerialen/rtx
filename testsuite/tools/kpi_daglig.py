#!/usr/bin/env python3
"""Daily KPI script K1–K8 (U3, efterlevnadslager 2).

Data sources live in kpi_config.json — not hardcoded. A field that
cannot be measured mechanically is ``OMÄTT`` plus why. No invented
proxies. No rig, never ~/lab.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "kpi_config.json"
DEFAULT_DOMFIL = Path("/home/xerial/dev/buzz-4on4/WORK_LOGS/kimi-testprotokoll-domar.md")
DEFAULT_WORKLOGS = Path("/home/xerial/dev/buzz-4on4/WORK_LOGS")
DEFAULT_OUT = DEFAULT_WORKLOGS / "kpi"

HEAD = re.compile(r"^## DOM\s+(\S+)\s+—\s+(.*)$")
DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
PASS_DEFAULT = ("GRÖNT", "GODKÄND")

SCHEMA = "kpi-daglig/1"


def load_config(path: Path | None) -> dict[str, Any]:
    p = path or DEFAULT_CONFIG
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _unmeasured(kid: str, source: str, why: str, **extra: Any) -> dict[str, Any]:
    row = {
        "id": kid,
        "status": "OMÄTT",
        "value": None,
        "why": why,
        "source": source,
        "alarm": False,
    }
    row.update(extra)
    return row


def _measured(kid: str, source: str, value: Any, *, alarm: bool, unit: str, target: str, **extra: Any) -> dict[str, Any]:
    row = {
        "id": kid,
        "status": "MÄTT",
        "value": value,
        "unit": unit,
        "target": target,
        "source": source,
        "alarm": alarm,
        "why": None,
    }
    row.update(extra)
    return row


def _heading_pass(rest: str, markers: list[str]) -> bool:
    u = rest.upper()
    if any(m in u for m in ("UNDERKÄND", "UNDERKAND", "RÖTT", "STOPP")):
        return False
    return any(m.upper() in u for m in markers)


def parse_pass_dates(text: str, *, markers: list[str], heading_regex: str | None) -> list[tuple[date, str]]:
    rx = re.compile(heading_regex) if heading_regex else None
    out: list[tuple[date, str]] = []
    for line in text.splitlines():
        m = HEAD.match(line.strip())
        if not m:
            continue
        raw, rest = m.group(1), m.group(2)
        if rx and not rx.search(raw) and not rx.search(rest):
            continue
        if not _heading_pass(rest, markers):
            continue
        dm = DATE.search(line)
        if not dm:
            continue
        out.append((date.fromisoformat(dm.group(1)), raw))
    return out


def daily_cost_usd(cfg: dict[str, Any]) -> tuple[float | None, str]:
    costs = cfg.get("costs_usd_per_month") or {}
    days = float(cfg.get("days_per_month") or 30)
    known = [float(v) for v in costs.values() if isinstance(v, (int, float))]
    missing = [k for k, v in costs.items() if v is None]
    if not known:
        return None, "inga numeriska månadskostnader i config"
    daily = sum(known) / days
    note = f"summa kända månadskostnader {sum(known)} USD / {int(days)}"
    if missing:
        note += f"; saknas i config: {', '.join(missing)}"
    return daily, note


def k1(cfg: dict[str, Any], dom_text: str, as_of: date) -> dict[str, Any]:
    spec = cfg.get("k1") or {}
    source = spec.get("source") or "domfil_pass_headings"
    markers = list(spec.get("pass_markers") or PASS_DEFAULT)
    rows = parse_pass_dates(dom_text, markers=markers, heading_regex=spec.get("heading_regex"))
    if not rows:
        return _unmeasured("K1", source, "ingen PASS-rubrik med datum i domfilen")
    last_day, last_id = max(rows, key=lambda r: r[0])
    days = (as_of - last_day).days
    target = int(spec.get("target_max_days") or 1)
    return _measured(
        "K1", source, days,
        alarm=days > target,
        unit="dagar",
        target=f"<={target}",
        last_date=last_day.isoformat(),
        last_punkt=last_id,
    )


def k2(cfg: dict[str, Any], _dom_text: str) -> dict[str, Any]:
    spec = cfg.get("k2") or {}
    source = spec.get("source") or "domfil_order_to_first_figure"
    return _unmeasured(
        "K2",
        source,
        "ingen maskinläsbar beställningstid för dömd körning i domfilen "
        "(TTFN kräver order-ts och första-siffra-ts; bara domrubrikens "
        "klockslag finns och de är inte parade)",
        target=f"<={spec.get('target_max_min', 45)} min",
    )


def _git_commits(repo: Path, since: date) -> list[dict[str, Any]]:
    if not repo or not repo.exists():
        return []
    try:
        raw = subprocess.check_output(
            [
                "git", "-C", str(repo), "log",
                f"--since={since.isoformat()}",
                "--format=%H%x09%s",
                "--name-only",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    commits: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in raw.splitlines():
        if "\t" in line and re.match(r"^[0-9a-f]{8,}\t", line):
            sha, subj = line.split("\t", 1)
            cur = {"sha": sha, "subject": subj, "files": []}
            commits.append(cur)
            continue
        if cur is not None and line.strip():
            cur["files"].append(line.strip())
    return commits


def k3(cfg: dict[str, Any], repo: Path | None, as_of: date) -> dict[str, Any]:
    spec = cfg.get("k3") or {}
    source = spec.get("source") or "git_log_path_class"
    if repo is None:
        return _unmeasured("K3", source, "inget --repo (git-logg finns inte på pinnacle)")
    window = int(spec.get("window_days") or 1)
    since = as_of - timedelta(days=window - 1)
    commits = _git_commits(repo, since)
    if not commits:
        return _unmeasured("K3", source, f"inga commits i --repo sedan {since.isoformat()}")
    app_pref = list(spec.get("apparat_prefixes") or [])
    pro_pref = list(spec.get("produkt_prefixes") or [])
    n_app = n_pro = 0
    for c in commits:
        for f in c["files"]:
            if any(f.startswith(p) for p in app_pref):
                n_app += 1
            elif any(f.startswith(p) for p in pro_pref):
                n_pro += 1
    denom = n_app + n_pro
    if denom == 0:
        return _unmeasured(
            "K3", source,
            "commits i fönstret men inga filer matchade apparat-/produkt-prefix",
            n_commits=len(commits),
        )
    ratio = n_app / denom
    target = float(spec.get("target_max_ratio") or 0.4)
    return _measured(
        "K3", source, round(ratio, 4),
        alarm=ratio >= target,
        unit="apparat/(apparat+produkt)",
        target=f"<{target}",
        n_apparat_files=n_app,
        n_produkt_files=n_pro,
        n_commits=len(commits),
    )


def k4(cfg: dict[str, Any]) -> dict[str, Any]:
    spec = cfg.get("k4") or {}
    source = spec.get("source") or "daily_cost_over_measured_minutes"
    if spec.get("measured_minutes_glob"):
        # glob configured but we still need files; handled by caller via extra
        pass
    daily, note = daily_cost_usd(cfg)
    return _unmeasured(
        "K4",
        source,
        "uppmätta minuter saknas (ingen T20m/T1h-kvittoglob i config, "
        "och scriptet hittar inte på minuter ur T1h-mappar). "
        + (f"daglig kostnad ur config: {daily:.2f} USD ({note})" if daily is not None else note),
        target=f"<={spec.get('target_max', 3)}",
        daily_cost_usd=daily,
    )


def k5(cfg: dict[str, Any], repo: Path | None, as_of: date) -> dict[str, Any]:
    spec = cfg.get("k5") or {}
    source = spec.get("source") or "daily_cost_over_agent_hours"
    daily, note = daily_cost_usd(cfg)
    idle_days = int(spec.get("uppsagning_idle_days") or 3)
    flags: list[str] = []
    idle = None
    if repo is not None:
        commits = _git_commits(repo, as_of - timedelta(days=idle_days))
        idle = len(commits) == 0
        if idle:
            flags.append(f"uppsagning: 0 leverans-commits på {idle_days} dygn")
    else:
        flags.append("idle-check OMÄTT (inget --repo)")
    if spec.get("transcript_buckets") is None:
        return _unmeasured(
            "K5",
            source,
            "inga 10-minuters transkript-buckets (herdr har ingen tidsrad; "
            "session-loggar är inte den specificerade metoden). "
            + (f"daglig kostnad ur config: {daily:.2f} USD ({note})" if daily is not None else note),
            target=f"kr/h, prövning >{spec.get('provning_over', 8)}",
            daily_cost_usd=daily,
            flags=flags,
            idle_3d=idle,
            alarm=bool(idle),
        )
    return _unmeasured("K5", source, "transcript_buckets pekar men läsaren är inte implementerad")


def k6(cfg: dict[str, Any]) -> dict[str, Any]:
    spec = cfg.get("k6") or {}
    source = spec.get("source") or "rig_hours_per_figure"
    return _unmeasured(
        "K6",
        source,
        "ingen maskinläsbar riggtidslogg (rig-lock-tider ligger under ~/lab "
        "som inte får läsas). Ingen siffra att dividera med.",
        target=f"<={spec.get('target_max', 1)}",
    )


def _files_on_day(root: Path, globs: list[str], day: date) -> list[Path]:
    if not root.is_dir():
        return []
    day_s = day.isoformat()
    hits: list[Path] = []
    for p in root.iterdir():
        if not p.is_file():
            continue
        if not any(fnmatch(p.name, g) for g in globs):
            continue
        if day_s in p.name:
            hits.append(p)
    return hits


def k7(cfg: dict[str, Any], worklogs: Path | None, repo: Path | None, as_of: date) -> dict[str, Any]:
    spec = cfg.get("k7") or {}
    source = spec.get("source") or "ceremony_files_over_delivery_commits"
    if worklogs is None or not worklogs.is_dir():
        return _unmeasured("K7", source, "ingen --worklogs-katalog")
    cer_globs = list(spec.get("ceremony_globs") or ["*rapport*", "*review*", "*handoff*"])
    ceremony = _files_on_day(worklogs, cer_globs, as_of)
    if repo is None:
        return _unmeasured(
            "K7", source,
            "ceremonifiler räknade men nämnare (leverans-commits) kräver --repo",
            n_ceremony=len(ceremony),
            target=f"<{spec.get('target_max', 2)}",
        )
    commits = _git_commits(repo, as_of)
    n_del = len(commits)
    if n_del == 0:
        return _unmeasured(
            "K7", source,
            f"{len(ceremony)} ceremonifiler {as_of.isoformat()} men 0 commits i --repo samma dygn (kvot odefinierad)",
            n_ceremony=len(ceremony),
        )
    q = len(ceremony) / n_del
    target = float(spec.get("target_max") or 2)
    return _measured(
        "K7", source, round(q, 4),
        alarm=q >= target,
        unit="ceremonifiler/leveranscommit",
        target=f"<{target}",
        n_ceremony=len(ceremony),
        n_delivery=n_del,
    )


def k8(cfg: dict[str, Any], worklogs: Path | None, as_of: date) -> dict[str, Any]:
    spec = cfg.get("k8") or {}
    source = spec.get("source") or "handover_files_and_fable_time_share"
    if worklogs is None or not worklogs.is_dir():
        return _unmeasured("K8", source, "ingen --worklogs-katalog")
    glob = spec.get("handover_glob") or "*handoff*"
    hands = _files_on_day(worklogs, [glob], as_of)
    cap = int(spec.get("target_max_handovers") or 15)
    handover_alarm = len(hands) > cap
    share = _unmeasured(
        "K8b",
        source,
        "Fable-andel av teamtid kräver 10-minutersbuckets ur herdr/transkript; "
        "herdr-state har ingen tidsrad",
    )
    return _measured(
        "K8", source, len(hands),
        alarm=handover_alarm,
        unit="överlämningsfiler/dygn",
        target=f"<={cap}",
        files=[p.name for p in sorted(hands)],
        fable_share=share,
    )


def compute(
    *,
    cfg: dict[str, Any],
    as_of: date,
    dom_text: str,
    repo: Path | None,
    worklogs: Path | None,
) -> dict[str, Any]:
    rows = {
        "K1": k1(cfg, dom_text, as_of),
        "K2": k2(cfg, dom_text),
        "K3": k3(cfg, repo, as_of),
        "K4": k4(cfg),
        "K5": k5(cfg, repo, as_of),
        "K6": k6(cfg),
        "K7": k7(cfg, worklogs, repo, as_of),
        "K8": k8(cfg, worklogs, as_of),
    }
    alarms = [kid for kid, r in rows.items() if r.get("alarm")]
    kalla = {kid: r.get("source") for kid, r in rows.items()}
    return {
        "schema": SCHEMA,
        "as_of": as_of.isoformat(),
        "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kpis": rows,
        "alarms": alarms,
        "kalla": kalla,
    }


def format_summary(doc: dict[str, Any]) -> str:
    lines = [f"KPI {doc['as_of']}  larm={doc['alarms'] or 'inga'}"]
    for kid, r in doc["kpis"].items():
        if r["status"] == "OMÄTT":
            lines.append(f"  {kid} OMätt — {r['why']}")
        else:
            flag = " LARM" if r.get("alarm") else ""
            lines.append(
                f"  {kid} {r['value']} {r.get('unit', '')} (mål {r.get('target')}){flag}"
            )
    return "\n".join(lines) + "\n"


def write_outputs(doc: dict[str, Any], outdir: Path) -> tuple[Path, Path | None]:
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / "daglig.jsonl"
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    larm_path = None
    if doc["alarms"]:
        larm_path = outdir / f"LARM-{doc['as_of']}.md"
        bits = [f"# KPI-LARM {doc['as_of']}", ""]
        for kid in doc["alarms"]:
            r = doc["kpis"][kid]
            bits.append(f"- **{kid}** {r.get('value')} {r.get('unit', '')} "
                        f"(mål {r.get('target')}; källa {r.get('source')})")
        larm_path.write_text("\n".join(bits) + "\n", encoding="utf-8")
    return jsonl, larm_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--domfil", type=Path, default=DEFAULT_DOMFIL if DEFAULT_DOMFIL.is_file() else None)
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--worklogs", type=Path, default=DEFAULT_WORKLOGS if DEFAULT_WORKLOGS.is_dir() else None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT if DEFAULT_WORKLOGS.is_dir() else None)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--dry", action="store_true", help="compute only, do not append jsonl")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.domfil is None or not args.domfil.is_file():
        ap.error("--domfil required")
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    doc = compute(
        cfg=cfg,
        as_of=as_of,
        dom_text=args.domfil.read_text(encoding="utf-8"),
        repo=args.repo,
        worklogs=args.worklogs,
    )
    print(format_summary(doc), end="")
    if not args.dry:
        if args.out is None:
            ap.error("--out required unless --dry")
        write_outputs(doc, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
