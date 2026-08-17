"""Portvakt för timtest_d. Fail-closed: RA/main och allt utanför tbx-D,
utom en explicit referensarm.

Tillåtet mätarm: ctl 27996–27999, game 27592–27595 (tbx).
Tillåtet referensarm: REF_PAIRS — bara de paren, märks 'referens'.
Alltid förbjudet: 27990, 27993, 27540, 27570 (även om de skulle
hamna i ett intervall — de gör det inte, men vägras med eget skäl).
"""
from __future__ import annotations

FORBIDDEN_CTL = frozenset({27990, 27993})
FORBIDDEN_GAME = frozenset({27540, 27570})
CTL_LO, CTL_HI = 27996, 27999
GAME_LO, GAME_HI = 27592, 27595
REF_PAIRS = frozenset({(27991, 27550)})

EXIT_REFUSED = 2


def port_fel(port: int | None, game_port: int | None) -> str | None:
    """Returnera felsträng eller None om båda portarna är tillåtna.

    None betyder släppt: antingen tbx-mätarm eller REF_PAIRS (referensarm).
    Använd port_arm() för att se vilken.
    """
    if port is None or game_port is None:
        return "ctl- och game-port krävs"
    try:
        port = int(port)
        game_port = int(game_port)
    except (TypeError, ValueError):
        return "portar måste vara heltal"
    if port in FORBIDDEN_CTL or game_port in FORBIDDEN_CTL:
        return "RA/main ctl-port (%s) — dedicated D only" % port
    if game_port in FORBIDDEN_GAME or port in FORBIDDEN_GAME:
        return "RA/main game-port (%s) — dedicated D only" % game_port
    if (port, game_port) in REF_PAIRS:
        return None
    if not (CTL_LO <= port <= CTL_HI):
        return "ctl-port %s utanför tbx %s–%s" % (port, CTL_LO, CTL_HI)
    if not (GAME_LO <= game_port <= GAME_HI):
        return "game-port %s utanför tbx %s–%s" % (game_port, GAME_LO, GAME_HI)
    return None


def port_arm(port: int | None, game_port: int | None) -> str | None:
    """'referens' | 'tbx' om paret släpps, annars None.

    Domaren kan märka armen med denna flagga. Vägrat par ger None.
    """
    if port_fel(port, game_port) is not None:
        return None
    pair = (int(port), int(game_port))
    return "referens" if pair in REF_PAIRS else "tbx"
