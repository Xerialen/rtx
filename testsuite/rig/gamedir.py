#!/usr/bin/env python3
"""Bygger den privata gamediren för T3/T4 och bevisar att den håller.

`testsuite/README.md` beskriver riggen i prosa och räknar upp de fällor
som gör att en rigg fungerar första matchen och sedan slutar. Prosa är
inte en rigg. Varje fälla här är en assertion som faller, inte en mening
någon ska komma ihåg att läsa.

Den bärande insikten är körordningen: KTX kör om hela reset-kedjan
(`server.cfg` → `mvdsv.cfg`, `ktx.cfg`, `pwd.cfg`) efter *varje* match,
och stampar därefter sin egen usermode-default. Den enda fil som körs
efter båda är `configs/usermodes/<mode>/default.cfg`. Allt riggkritiskt
måste stå där — annars gäller det bara fram till första matchslutet.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: Cvars som avgör om riggen mäter eller ljuger. Sätter reset-kedjan någon
#: av dem måste vår sist körda fil sätta om den — annars är den bara satt
#: fram till första matchslutet.
RIGGKRITISKA = frozenset(
    {
        "timelimit",
        "maxclients",
        "k_matchless",
        "k_lockmode",
        "k_noframechecks",
        "k_membercount",
        "k_count",
        "k_overtime",
        "k_exttime",
        "k_demotxt_format",
        "k_fb_enabled",
        "rcon_password",
        "sv_crypt_rcon",
        "sv_timeout",
        "sv_demodir",
    }
)

#: Filerna KTX kör om efter varje match. `server.cfg` kör de andra.
RESETKEDJAN = ("server.cfg", "mvdsv.cfg", "ktx.cfg", "pwd.cfg")


class Riggfel(Exception):
    """Riggen håller inte. Alltid ett stopp."""


@dataclass(frozen=True)
class Riggval:
    tier: str  # "t3" eller "t4"
    seats_per_side: int
    timelimit_min: int
    demodir: str
    rcon_password: str

    def mode(self) -> str:
        return "%don%d" % (self.seats_per_side, self.seats_per_side)


def cvar_namn_i_rad(rad: str) -> str | None:
    """Namnet på den riggkritiska cvar raden sätter, annars None."""
    rad = rad.split("//", 1)[0].strip()
    if not rad:
        return None
    delar = rad.split()
    if delar[0] == "set":
        delar = delar[1:]
    if len(delar) < 2:
        return None
    namn = delar[0].lower()
    return namn if namn in RIGGKRITISKA else None


def cvars_i_cfg(text: str) -> dict[str, str]:
    """Plockar ut cvar-sättningar ur en .cfg. `set NAMN VARDE` och `NAMN VARDE`."""
    ut: dict[str, str] = {}
    for rad in text.splitlines():
        rad = rad.split("//", 1)[0].strip()
        if not rad:
            continue
        delar = rad.split()
        if delar[0] == "set":
            delar = delar[1:]
        if len(delar) < 2:
            continue
        namn = delar[0].lower()
        if namn in RIGGKRITISKA:
            ut[namn] = " ".join(delar[1:]).strip('"')
    return ut


def overrides(val: Riggval) -> dict[str, str]:
    """Exakt de värden riggen kräver, med skälet i kommentaren där det behövs."""
    o = {
        # Matchläge. `k_matchless 1` tvingar teamplay 0 och gör 4on4 meningslöst.
        "k_matchless": "0",
        # Timelimit måste överleva KTX:s mode-ominitiering — därför här och
        # ingen annanstans.
        "timelimit": str(val.timelimit_min),
        # Headless klienter trippar fps-kontrollen. Stock `ktx.cfg` sätter 0,
        # så utan omstampning sparkas alla klienter ~90 s in i match TVÅ.
        "k_noframechecks": "1",
        # Låst läge släpper tyst nätverksklienterna när de joinar.
        "k_lockmode": "0",
        # Vid 3 vägrar KTX tyst den fjärde boten.
        "k_membercount": str(val.seats_per_side),
        # rtx-klienterna bygger navmesh efter join; med stock nedräkning
        # börjar matchen innan meshen finns och laget står kvar på spawn.
        "k_count": "45",
        # Stock `ktx.cfg` stampar 1/3. Osynligt till första oavgjorda matchen,
        # då overtime spränger runnerns matchslutsfönster.
        "k_overtime": "0",
        "k_exttime": "0",
        # Demoinfo-.txt:n bredvid MVD:n är enda poängorakel.
        "k_demotxt_format": "json",
        # Rivna headless klienter lämnar spöken (de har ingen disconnect-väg).
        # Trånga platser knuffar nästa körnings fjärde bot till en åskådarplats
        # där den tyst aldrig spelar.
        "maxclients": "16",
        "sv_timeout": "30",
        # Stock `mvdsv.cfg` slår på rcon-kryptering och pekar om demokatalogen;
        # efter första reset läser poängoraklet en tom katalog.
        "sv_crypt_rcon": "0",
        "sv_demodir": val.demodir,
        "rcon_password": val.rcon_password,
        # T3 kör utan frogbots, T4 med.
        "k_fb_enabled": "1" if val.tier == "t4" else "0",
    }
    if set(o) != set(RIGGKRITISKA):
        saknas = RIGGKRITISKA - set(o)
        raise Riggfel("overrides täcker inte alla riggkritiska cvars: %s" % sorted(saknas))
    return o


def skriv_default_cfg(val: Riggval) -> str:
    o = overrides(val)
    rader = [
        "// GENERERAD av testsuite/rig/gamedir.py — redigera inte för hand.",
        "// Detta är den enda fil KTX kör efter BÅDE reset-kedjan och sin egen",
        "// mode-ominitiering. Allt riggkritiskt står därför här.",
        "//",
        "// Tier: %s   usermode: %s" % (val.tier, val.mode()),
        "",
        "set k_pow 1",
        "",
    ]
    for namn in sorted(o):
        varde = o[namn]
        if namn == "rcon_password":
            rader.append('%s "%s"' % (namn, varde))
        elif namn in {"timelimit", "maxclients", "sv_timeout", "sv_crypt_rcon", "sv_demodir"}:
            rader.append("%s %s" % (namn, varde))
        else:
            rader.append("set %s %s" % (namn, varde))
    return "\n".join(rader) + "\n"


def bygg(kalla: Path, mal: Path, val: Riggval, bots_kalla: Path | None) -> list[str]:
    """Bygger den privata gamediren. Returnerar en logg över vad som gjordes."""
    logg: list[str] = []

    # Fälla: en delad labbserver. Den privata gamediren får aldrig vara
    # källträdet, och aldrig ligga i det.
    kalla = kalla.resolve()
    mal = mal.resolve()
    if mal == kalla:
        raise Riggfel("privat gamedir är samma katalog som källträdet: %s" % mal)
    if kalla in mal.parents:
        raise Riggfel(
            "privat gamedir %s ligger inuti det delade källträdet %s — "
            "en match hade skrivit i det delade trädet" % (mal, kalla)
        )
    if not (kalla / "configs" / "usermodes").is_dir():
        raise Riggfel("källträdet saknar configs/usermodes: %s" % kalla)

    mal.mkdir(parents=True, exist_ok=True)

    # configs/-trädet och root-.cfg:erna kopieras, så att inget delat redigeras.
    shutil.copytree(kalla / "configs", mal / "configs", dirs_exist_ok=True)
    logg.append("kopierade configs/")
    for cfg in sorted(kalla.glob("*.cfg")):
        shutil.copy2(cfg, mal / cfg.name)
    logg.append("kopierade %d root-.cfg" % len(list(kalla.glob("*.cfg"))))

    # Inga masterservrar: en mätrigg annonserar sig inte publikt.
    server_cfg = mal / "server.cfg"
    if server_cfg.is_file():
        text = server_cfg.read_text(encoding="utf-8", errors="replace")
        rader = []
        for rad in text.splitlines():
            if rad.strip().lower().startswith("setmaster "):
                rader.append("setmaster        // mätrigg: inga masterservrar")
                logg.append("nollade setmaster i server.cfg")
            else:
                rader.append(rad)
        server_cfg.write_text("\n".join(rader) + "\n", encoding="utf-8")

    # Den sist körda filen.
    mode_dir = mal / "configs" / "usermodes" / val.mode()
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "default.cfg").write_text(skriv_default_cfg(val), encoding="utf-8")
    logg.append("skrev configs/usermodes/%s/default.cfg" % val.mode())

    # Avväpna de andra lägena. Byter servern usermode är det DEN modens
    # default.cfg KTX kör sist, och vår är aldrig med — ett lägesbyte hade
    # tagit bort riggens överstyrningar utan att något syntes. Det delade
    # trädet rörs inte; det här är vår privata kopia, och den finns bara
    # för mätningen.
    var_cfg = (mode_dir / "default.cfg").resolve()
    avvapnade = 0
    for p in sorted((mal / "configs" / "usermodes").rglob("*.cfg")):
        if p.resolve() == var_cfg:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        behall, strukna = [], []
        for rad in text.splitlines():
            namn = cvar_namn_i_rad(rad)
            if namn:
                strukna.append(namn)
            else:
                behall.append(rad)
        if strukna:
            behall.append(
                "// %s struken av testsuite/rig: ett lägesbyte får inte "
                "avväpna mätriggen" % ", ".join(sorted(set(strukna)))
            )
            p.write_text("\n".join(behall) + "\n", encoding="utf-8")
            avvapnade += 1
    if avvapnade:
        logg.append("avväpnade %d andra usermode-cfg" % avvapnade)

    (mal / val.demodir).mkdir(parents=True, exist_ok=True)
    logg.append("skapade demokatalog %s/" % val.demodir)

    if val.tier == "t4":
        if bots_kalla is None:
            raise Riggfel("T4 kräver frogbot-datat (bots/) — ingen källa angiven")
        bots_kalla = bots_kalla.resolve()
        if not (bots_kalla / "maps").is_dir():
            raise Riggfel("frogbot-datat saknar maps/: %s" % bots_kalla)
        # Symlänk, inte kopia: datat är KTX:s och ska inte dubbleras.
        lank = mal / "bots"
        if lank.is_symlink() or lank.exists():
            if lank.is_symlink():
                lank.unlink()
            else:
                raise Riggfel("%s finns redan och är ingen symlänk" % lank)
        lank.symlink_to(bots_kalla)
        logg.append("symlänkade bots/ -> %s" % bots_kalla)

    return logg


def granska(mal: Path, val: Riggval) -> list[str]:
    """Bevisar att den byggda gamediren håller. Kastar Riggfel annars."""
    mal = Path(mal).resolve()
    bevis: list[str] = []

    mode_cfg = mal / "configs" / "usermodes" / val.mode() / "default.cfg"
    if not mode_cfg.is_file():
        raise Riggfel(
            "sist körda filen saknas: %s — utan den gäller inga överstyrningar "
            "efter första matchslutet" % mode_cfg
        )
    vara = cvars_i_cfg(mode_cfg.read_text(encoding="utf-8"))

    # 1. Alla riggkritiska cvars satta i den sist körda filen.
    saknas = sorted(RIGGKRITISKA - set(vara))
    if saknas:
        raise Riggfel("sist körda filen sätter inte: %s" % ", ".join(saknas))
    bevis.append("alla %d riggkritiska cvars satta i %s" % (len(RIGGKRITISKA), mode_cfg.name))

    # 2. Värdena är de riggen kräver.
    vill = overrides(val)
    fel = {n: (vara[n], vill[n]) for n in vill if vara[n] != vill[n]}
    if fel:
        raise Riggfel(
            "fel värden: %s"
            % ", ".join("%s=%s (vill ha %s)" % (n, a, b) for n, (a, b) in sorted(fel.items()))
        )
    bevis.append("alla värden stämmer med riggkravet")

    # 3. Hela reset-kedjan granskad, inte bara boot-cfg:n.
    #
    #    Steg 1 gör själva omstampningen bevisad: står alla riggkritiska
    #    cvars i den sist körda filen, så är varje värde kedjan sätter
    #    överskrivet. Det som steg 1 *inte* kan se är om vår fil slutar
    #    vara den sist körda. Två sätt att förlora den ordningen:

    #    3a. Kedjan kör vår fil själv. Då körs den FÖRE KTX:s
    #        mode-ominitiering i stället för efter, och ominitieringen
    #        stampar tillbaka. Filen ser rätt ut och riggen är ändå av.
    var_mode_vag = "usermodes/%s/default.cfg" % val.mode()
    for namn in RESETKEDJAN:
        p = mal / namn
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for rad in text.splitlines():
            s = rad.split("//", 1)[0].strip()
            if s.lower().startswith("exec ") and var_mode_vag in s:
                raise Riggfel(
                    "%s kör vår sist körda fil (%r) — då körs den före KTX:s "
                    "mode-ominitiering och stampas tillbaka" % (namn, s)
                )
        deras = cvars_i_cfg(text)
        krockar = {n: deras[n] for n in deras if deras[n] != vara.get(n)}
        if krockar:
            bevis.append(
                "%s sätter %s — omstampad av %s"
                % (namn, ", ".join(sorted(krockar)), mode_cfg.name)
            )

    #    3b. Servern kör ett annat usermode än vårt. Då är det DEN modens
    #        default.cfg som körs sist, och vår är aldrig med. Ett läge som
    #        sätter något riggkritiskt annorlunda kan alltså avväpna riggen
    #        tyst vid ett lägesbyte.
    mode_rot = mal / "configs" / "usermodes"
    if mode_rot.is_dir():
        andra = [p for p in sorted(mode_rot.rglob("*.cfg")) if p.resolve() != mode_cfg.resolve()]
        for p in andra:
            deras = cvars_i_cfg(p.read_text(encoding="utf-8", errors="replace"))
            krockar = sorted(n for n, v in deras.items() if v != vara.get(n))
            if krockar:
                raise Riggfel(
                    "%s sätter %s annorlunda än riggen kräver — byter servern "
                    "läge körs den filen sist i stället för vår, och riggen är "
                    "avväpnad utan att något syns"
                    % (p.relative_to(mal), ", ".join(krockar))
                )
        bevis.append("inget av %d andra usermode-cfg avväpnar riggen" % len(andra))

    # 4. Inga masterservrar.
    server_cfg = mal / "server.cfg"
    if server_cfg.is_file():
        for rad in server_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            s = rad.split("//", 1)[0].strip()
            if s.lower().startswith("setmaster") and len(s.split()) > 1:
                raise Riggfel("server.cfg annonserar mot masterservrar: %r" % s)
        bevis.append("inga masterservrar")

    # 5. Demokatalogen finns — poängoraklet läser den.
    if not (mal / val.demodir).is_dir():
        raise Riggfel("demokatalogen %s saknas — poängoraklet får inget att läsa" % val.demodir)
    bevis.append("demokatalog %s/ finns" % val.demodir)

    # 6. T4: frogbot-datat på plats.
    if val.tier == "t4":
        bots = mal / "bots"
        if not bots.is_dir():
            raise Riggfel("T4 utan frogbot-data: %s saknas" % bots)
        if not (bots / "maps").is_dir():
            raise Riggfel("frogbot-datat saknar maps/")
        bevis.append("frogbot-data på plats")
    else:
        if vara["k_fb_enabled"] != "0":
            raise Riggfel("T3 med frogbots påslagna")
        bevis.append("T3 utan frogbots")

    return bevis


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", required=True, choices=["t3", "t4"])
    ap.add_argument("--kalla", required=True, help="delat KTX-träd att kopiera FRÅN")
    ap.add_argument("--gamedir", required=True, help="privat gamedir att bygga")
    ap.add_argument("--seats-per-side", required=True, type=int)
    ap.add_argument("--timelimit-min", required=True, type=int)
    ap.add_argument("--demodir", required=True)
    ap.add_argument(
        "--rcon-password-fil",
        required=True,
        help="fil med rcon-lösenordet. Aldrig på kommandoraden, aldrig i repot.",
    )
    ap.add_argument("--bots", help="KTX:s bots/-katalog (krävs för t4)")
    ap.add_argument("--endast-granska", action="store_true")
    args = ap.parse_args(argv)

    try:
        pwfil = Path(args.rcon_password_fil)
        if not pwfil.is_file():
            raise Riggfel("rcon-lösenordsfilen saknas: %s" % pwfil)
        losen = pwfil.read_text(encoding="utf-8").strip()
        if not losen:
            raise Riggfel("rcon-lösenordsfilen är tom: %s" % pwfil)

        val = Riggval(
            tier=args.tier,
            seats_per_side=args.seats_per_side,
            timelimit_min=args.timelimit_min,
            demodir=args.demodir,
            rcon_password=losen,
        )
        if not args.endast_granska:
            for rad in bygg(Path(args.kalla), Path(args.gamedir), val, Path(args.bots) if args.bots else None):
                print("  %s" % rad)
        for rad in granska(Path(args.gamedir), val):
            print("  OK: %s" % rad)
    except Riggfel as exc:
        print("RIGG VÄGRAD: %s" % exc, file=sys.stderr)
        return 2
    print("gamedir OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
