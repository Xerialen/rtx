#!/usr/bin/env python3
"""Gor applicera_recept.py parametriserat pa receptfilen."""
import pathlib

p = pathlib.Path("/home/xerial/hopptraning/applicera_recept.py")
s = p.read_text()
old = 'REC = json.loads(Path("/home/xerial/hopptraning/recept-ring2quad-stang-kedjade.json").read_text())'
new = ('REC_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(\n'
       '    "/home/xerial/hopptraning/recept-ring2quad-stang-kedjade.json")\n'
       'REC = json.loads(REC_PATH.read_text())\n'
       'print("recept   ", REC_PATH)')
assert s.count(old) == 1, s.count(old)
p.write_text(s.replace(old, new, 1))
print("applicera_recept.py parametriserat")
