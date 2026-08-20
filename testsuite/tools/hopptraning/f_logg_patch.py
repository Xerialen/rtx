#!/usr/bin/env python3
"""f_logg: rapportera BAS-id, inte live-id.

Fallan: lank-id komprimeras nar lankar tas bort, sa live-id != bas-id. Live 35736
i vF1 ar bas 35738 — den goda korsningen. Utan mappningen jamfor man aplen med
paron mellan varv.
"""
import pathlib
p = pathlib.Path("/home/xerial/hopptraning/f_logg.py")
s = p.read_text()
s = s.replace('''SJ_GOD = {35736, 35737, 35738}
SJ_FORBJUDNA = {34501, 34503}
d = Path(sys.argv[1])''',
'''SJ_GOD = {35736, 35737, 35738}          # BAS-id
SJ_FORBJUDNA = {34501, 34503}           # BAS-id
d = Path(sys.argv[1])
BORT = {int(x) for x in sys.argv[2:]}   # bas-id som tagits bort i detta varv

_g = json.loads(Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json").read_bytes())
_kvar = [i for i in sorted(_g["link_ids"]) if i not in BORT]

def bas(live):
    """Live-index -> bas-id. Lankarrayen komprimeras vid remove, sa id:n skiftar."""
    return _kvar[live] if live is not None and live < len(_kvar) else live''', 1)
s = s.replace('''    a = sj[0][1] if sj else None''', '''    a = bas(sj[0][1]) if sj else None''', 1)
p.write_text(s)
print("f_logg rapporterar nu bas-id")
