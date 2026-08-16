"""Portvakt för timtest_d. Fail-closed: RA/main och allt utanför tbx-D.

Tillåtet: ctl 27996–27999, game 27592–27595.
Alltid förbjudet: 27990, 27993, 27540, 27570 (även om de skulle
hamna i ett intervall — de gör det inte, men vägras med eget skäl).
"""
from __future__ import annotations

FORBIDDEN_CTL = frozenset({27990, 27993})
FORBIDDEN_GAME = frozenset({27540, 27570})
CTL_LO, CTL_HI = 27996, 27999
GAME_LO, GAME_HI = 27592, 27595

EXIT_REFUSED = 2


def port_fel(port: int | None, game_port: int | None) -> str | None:
    """Returnera felsträng eller None om båda portarna är tillåtna."""
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
    if not (CTL_LO <= port <= CTL_HI):
        return "ctl-port %s utanför tbx %s–%s" % (port, CTL_LO, CTL_HI)
    if not (GAME_LO <= game_port <= GAME_HI):
        return "game-port %s utanför tbx %s–%s" % (game_port, GAME_LO, GAME_HI)
    return None
