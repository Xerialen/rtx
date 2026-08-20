#!/usr/bin/env python3
"""FIX: teleportkanten kapas av passet som ar skrivet for att skydda den.

`add_teleports` kapar medvetet trigger-cellens alla utgangar UTOM teleporten:
"Keep the incoming links: walking *in* is how the teleporter is used. Drop the
outgoing ones, so the only exit A* can plan is the teleport itself, which is the
truth."

Direkt darefter kor `prune_links_through_teleports()` map-brett och kapar varje
lank vars stracka korsar en teleportvolym i XY *utan att sluta* i en volym.
Teleportlanken BORJAR i volymen (kallcellen ar utcarvad inuti den) och slutar pa
destinationsplattan utanfor. Alltsa:
  - `in_box(b, ..)` falskt  (destinationen ligger inte i volymen)
  - vaningstestet slapper igenom (kalla z -16, volym kring z -16)
  - `segment_hits_box_xy` sant  (strackan startar inne i boxen)
=> teleportlanken kapas av just det pass vars egen doc-kommentar sager
   "A link that *ends* inside a volume is kept ... without it no bot could ever
   use one".

Matt pa dm3: kartans ENDA teleportlank (36314, cell 4633 -> 1330) har T=0, och
trigger-cellen har noll traverserbara utgangar. A* kan darfor aldrig planera
genom en teleport. Agarens hopp 2 gar genom teleporten, sa hoppet ar
strukturellt omojligt for planeraren i dagslaget: boten vandrar i RA-rummet och
tar till slut den langa vagen genom tunneln.

Fixen ar minsta mojliga och foljer passets egen avsikt: en Teleport-lank ar
volymens sanktionerade utgang och undantas fran passet. Ingen annan lanktyp
paverkas, inga cellantal eller lank-id andras — bara adjacensmedlemskapet for
teleportlankar. Nivå-1 ar darfor oforandrad; nivå-2 andras, vilket ar precis vad
nivå-2 finns for.
"""
import pathlib

ROOT = pathlib.Path("/home/xerial/rtx-ring2quad")


def patcha(rel, gammal, ny):
    p = ROOT / rel
    s = p.read_text()
    n = s.count(gammal)
    assert n == 1, f"{rel}: ankaret traffade {n} ganger, vill ha exakt 1"
    p.write_text(s.replace(gammal, ny, 1))
    print("patchad", rel)


patcha(
    "crates/rtx-nav/src/navmesh/splice.rs",
    """                .filter(|&li| {
                    let link = self.links[li as usize];
                    let b = self.cells[link.to as usize].origin;
                    !vols.iter().enumerate().any(|(vi, &(lo, hi))| {""",
    """                .filter(|&li| {
                    let link = self.links[li as usize];
                    // The teleport link is the volume's own sanctioned exit — the one thing
                    // `add_teleports` just went out of its way to keep. It *starts* inside the
                    // trigger and ends on the destination pad, so the "crosses without ending in
                    // one" test below matches it and cuts it, leaving the trigger cell with no
                    // traversable exit at all and no teleport A* can ever plan through. Measured on
                    // dm3: the map's only teleport link came out T=0 and the trigger cell out-degree
                    // 0. Exempt it, which is what the pass's own doc comment already promises.
                    if link.kind == LinkKind::Teleport {
                        return true;
                    }
                    let b = self.cells[link.to as usize].origin;
                    !vols.iter().enumerate().any(|(vi, &(lo, hi))| {""",
)

print("KLART")
