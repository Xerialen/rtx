# Referensexempel RA-room (Xerial, 2026-08-11)

Människoreferenser för RA-rummet på dm3, inspelade via kontrollkanalens
telemetri (labctl tail, Pmove ent 2). Alla tider mätta från platån/golvet.

## Upp
- `xerial-ra2-20260811.jsonl.gz` — klättersekvensen som P1-P4 planterades ur
  (plant_ra_climb.py). Fyra hopp z=60->331, total flygtid ~4,05 s.

## Ner
- `xerial-ra-down-20260811.jsonl.gz` — tre nedhopp från platån:
  RA->rox/RL-paden ~2,4 s, RA->tunneln ~2,1 s, RA->väst [-616,-251] ~3,4 s.
- `xerial-ra-ring-20260811.jsonl.gz` — RA->entrén mot ringsidan [288,-160,56]:
  direktdrop östkanten ~3,5 s, samt varianten via 264-ledgen [337,-531,264].

## Overlays (qw-nav-overlay/1, nav3d.html)
- `ra-climb.json` — P1-P4 med lip/mål + RA/ring-markörer
- `xerial-ra-down.json` — de tre nedhoppen
- `xerial-ra-ring.json` — båda ringside-varianterna

Botbaslinje samma dag (RA-room @ cc5fa8e, stamp 5978/48208):
- Platå->ringentré: 12/12 men 5,2-9,5 s (median ~7,4) mot människans 3,5 s
- Goto RA från golv: 24/24 10-25 s via västspiralen; A* tar aldrig P1-P4
- Startceller för nedvägstest: 1372/1373/1375/1420/1421/1422
