#!/usr/bin/env python3
"""Källkravet i facitformatet: observed får ALDRIG bli expected.

DET HÄR ÄR PROJEKTETS ÅTERKOMMANDE FEL, inte ett hypotetiskt. `on_expected` fylls i
från en körning som redan gjorts, facitet börjar beskriva vad som hände i stället
för vad som krävdes, och mätningen mäter sig själv. Konventionen har funnits länge
(`on_expected_note: "never from a judged --run"`) men den har varit prosa, och
prosa går inte att vägra på.

Kravet: ett facit som förseglas måste bära ett maskinläsbart källblock.

    {"schema": "facit-kalla/1",
     "expected_source": "derived" | "pre-measured" | "none",
     "never_from_judged_run": true,
     ... beroende på källa ...}

`derived`      värdena är HÄRLEDDA (transformator, kontraktsräkning, geometri).
               Kräver `derived_from`: vad härledningen utgick från.
`pre-measured` värdena är mätta FÖRE förseglingen, på en icke-dömd körning.
               Kräver `measured_at` + `measured_by`. Tidsstämpeln måste ligga före
               förseglingen — en mätning daterad efter förseglingen ÄR observed
               som blivit expected, per definition.
`none`         facitet bär inga förväntade värden (ett rent kontrakt). Ett
               uttryckligt påstående, inte tystnad.

Bälte och hängslen: tidsstämpeln OCH källfältet. Fältet fångar den som inte tänkt
efter; tidsstämpeln fångar den som tänkt efter och ändå tog värdet ur fel körning.
Valfria `sources: [{path, sha256}]` pinnar dessutom byten — då går det att visa i
efterhand att facitet förseglades mot exakt de filerna och inte mot en omkörning.

ORDFÖRRÅDET ÄR SLUTET. Första versionen granskade bara nycklar med `*_at`-suffix,
och deepseeks prob mot `26509ee` visade vad det lämnade öppet: ett ärligt
`measured_at` plus ett nyare `measured_when` gick rakt igenom — en vilseledande
tidsstämpel under ett namn vakten inte kände igen, obokförd i kvittot. En namnlista
över tidsfält hade bara flyttat gränsen. Det som håller är att blocket inte får bära
något vi inte förstår: okänd nyckel är en vägran, i blocket och i varje `sources`-post.

Ingen riggkontakt: läser bara den fil den får.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

SCHEMA = "facit-kalla/1"
NYCKEL = "forsegling_kalla"

#: Slutet ordförråd. Ett okänt värde är ett stavfel eller en ny idé — bådadera ska
#: stoppa, inte glida igenom som något närliggande.
KALLOR = ("derived", "pre-measured", "none")

#: Nycklar varje källblock får bära, oavsett källa.
GEMENSAMMA_NYCKLAR = frozenset({"schema", "expected_source", "never_from_judged_run", "note", "sources"})

#: Nycklar som hör till EN källa. `derived` får inte bära `measured_at`: har du mätt
#: något är källan `pre-measured`, och att hedga mellan de två är precis det
#: källkravet finns för att stoppa.
NYCKLAR_PER_KALLA = {
    "derived": frozenset({"derived_from"}),
    "pre-measured": frozenset({"measured_at", "measured_by"}),
    "none": frozenset(),
}

#: Nycklar en post i `sources` får bära. Sluten av samma skäl som blocket självt.
SOURCE_NYCKLAR = frozenset({"path", "sha256", "note"})

_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


class Vagran(Exception):
    """Facitet uppfyller inte källkravet."""


def _parse_ts(s: str, vad: str) -> _dt.datetime:
    txt = (s or "").strip()
    if not txt:
        raise Vagran(f"{vad} saknas")
    try:
        t = _dt.datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Vagran(f"{vad} {txt!r} är inte ISO-8601: {exc}") from exc
    if t.tzinfo is None:
        raise Vagran(f"{vad} {txt!r} saknar tidszon — kräv Z, annars är ordningen en gissning")
    return t.astimezone(_dt.timezone.utc)


def extrahera(text: str) -> dict:
    """Källblocket ur en facitfil. JSON-dokument bär det under `forsegling_kalla`;
    markdown bär det i ett ```json-block med rätt schema."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        doc = None
    if isinstance(doc, dict) and NYCKEL in doc:
        block = doc[NYCKEL]
        if not isinstance(block, dict):
            raise Vagran(f"{NYCKEL} är inte ett objekt")
        return block

    hittade = []
    for m in _FENCE.finditer(text):
        try:
            b = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(b, dict) and b.get("schema") == SCHEMA:
            hittade.append(b)
    if not hittade:
        raise Vagran(
            f"facitet saknar källblock ({SCHEMA}). Ett facit som förseglas måste säga var "
            f"dess förväntade värden kommer ifrån — annars går det inte att skilja ett krav "
            f"från en avläsning. Lägg till ett ```json-block, eller nyckeln {NYCKEL!r} i ett "
            f"JSON-facit."
        )
    if len(hittade) > 1 and any(b != hittade[0] for b in hittade[1:]):
        raise Vagran("facitet har flera OLIKA källblock — vilket gäller?")
    return hittade[0]


def validera(block: dict, sealed_at: str, facit_dir: Path) -> list[str]:
    """Kontrollera blocket mot förseglingsögonblicket. Returnerar noteringar för
    kvittot; kastar `Vagran` när något är fel."""
    if block.get("schema") != SCHEMA:
        raise Vagran(f"källblockets schema är {block.get('schema')!r}, väntade {SCHEMA}")

    kalla = block.get("expected_source")
    if kalla not in KALLOR:
        raise Vagran(f"expected_source {kalla!r} — kända: {'/'.join(KALLOR)}")

    if block.get("never_from_judged_run") is not True:
        raise Vagran(
            "never_from_judged_run måste vara true. Ett dömt körutdata får aldrig bli "
            "förväntat värde; att inte påstå det är att inte ha tagit ställning."
        )

    # SLUTET ORDFÖRRÅD (deepseeks prob mot 26509ee). Förr granskades bara nycklar med
    # `*_at`-suffix, så ett ärligt `measured_at` plus ett nyare `measured_when` gick
    # igenom obemärkt — en vilseledande tidsstämpel under ett namn hängslet inte kände
    # igen. En namnlista över tidsfält hade bara flyttat gränsen; det som håller är att
    # blocket inte får bära något vi inte förstår. Okänd nyckel = vägran.
    tillatna = GEMENSAMMA_NYCKLAR | NYCKLAR_PER_KALLA[kalla]
    okanda = sorted(set(block) - tillatna)
    if okanda:
        annan_kalla = sorted(
            n
            for n in okanda
            for k, nycklar in NYCKLAR_PER_KALLA.items()
            if k != kalla and n in nycklar
        )
        if annan_kalla:
            raise Vagran(
                f"{', '.join(annan_kalla)} hör till en annan expected_source än {kalla!r}. "
                f"Har du mätt något är källan 'pre-measured' — deklarera det i stället för "
                f"att bära fälten från två källor samtidigt."
            )
        raise Vagran(
            f"okända nycklar i källblocket: {', '.join(okanda)}. Tillåtna för {kalla!r}: "
            f"{', '.join(sorted(tillatna))}. Ett fält ingen granskar kan bära vad som helst — "
            f"en vilseledande tidsstämpel under ett namn vakten inte känner igen, till exempel."
        )

    seal_t = _parse_ts(sealed_at, "sealed_at")
    noter: list[str] = []

    if kalla == "derived":
        harledd = block.get("derived_from")
        if not isinstance(harledd, list) or not harledd or not all(isinstance(x, str) and x.strip() for x in harledd):
            raise Vagran("derived kräver derived_from: en icke-tom lista av vad härledningen utgick från")
        noter.append(f"derived_from={len(harledd)} källa/or")

    if kalla == "pre-measured":
        matt = _parse_ts(block.get("measured_at", ""), "measured_at")
        if not str(block.get("measured_by", "")).strip():
            raise Vagran("pre-measured kräver measured_by")
        if matt >= seal_t:
            raise Vagran(
                f"measured_at {matt.isoformat()} ligger inte före förseglingen {seal_t.isoformat()} — "
                f"värden mätta efter (eller vid) förseglingen är observed som blivit expected"
            )
        noter.append(f"measured_at={matt.isoformat()}")

    # Varje tidsstämpel i blocket måste ligga före förseglingen. Ordförrådet ovan gör
    # att det INTE finns några andra fält än de kända — men en känd nyckel kan
    # fortfarande bära ett datum åt fel håll, och det är vad den här loopen är till för.
    for nyckel, v in sorted(block.items()):
        if nyckel.endswith("_at") and isinstance(v, str):
            t = _parse_ts(v, nyckel)
            if t > seal_t:
                raise Vagran(
                    f"{nyckel} {t.isoformat()} är nyare än förseglingen {seal_t.isoformat()} — "
                    f"facitet pekar på körutdata som inte fanns när det förseglades"
                )

    # Hängslet: när blocket pinnar byten kontrolleras de. Frivilligt, för att inte
    # blockera migrering — men pinnat är pinnat.
    sources = block.get("sources")
    if sources is not None:
        if not isinstance(sources, list):
            raise Vagran("sources måste vara en lista av {path, sha256}")
        for i, s in enumerate(sources):
            if not isinstance(s, dict) or "path" not in s or "sha256" not in s:
                raise Vagran(f"sources[{i}] måste ha path och sha256")
            okanda_s = sorted(set(s) - SOURCE_NYCKLAR)
            if okanda_s:
                raise Vagran(
                    f"sources[{i}]: okända nycklar {', '.join(okanda_s)}. Tillåtna: "
                    f"{', '.join(sorted(SOURCE_NYCKLAR))}."
                )
            p = Path(s["path"])
            if not p.is_absolute():
                p = facit_dir / p
            if not p.is_file():
                raise Vagran(f"sources[{i}]: {p} finns inte — facitet pinnar en fil som saknas")
            har = hashlib.sha256(p.read_bytes()).hexdigest()
            if har != s["sha256"]:
                raise Vagran(
                    f"sources[{i}]: {p} är {har}, facitet pinnar {s['sha256']} — källan har ändrats "
                    f"sedan facitet skrevs"
                )
        noter.append(f"sources={len(sources)} pinnade")

    return noter


def granska(facit: Path, sealed_at: str) -> tuple[dict, list[str]]:
    """Läs, extrahera och validera. Kastar `Vagran` om facitet inte får förseglas."""
    block = extrahera(facit.read_text(encoding="utf-8", errors="replace"))
    noter = validera(block, sealed_at, facit.parent)
    return block, noter
