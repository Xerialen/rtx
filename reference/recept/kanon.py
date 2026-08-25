#!/usr/bin/env python3
"""Kanonisk inventering + grafidentitet, skriven ur MOTORNS kalla.

Detta ar en oberoende raknare. Den delar ingen kod med dumpverktygen; formatet
ar skrivet av ur `crates/rtx-game/src/nav_patch.rs::canonical_inventory`:

    C\\t{cell_id}\\t{x as i32}\\t{y as i32}\\t{z as i32}    for id 0..n-1, i ordning
    L\\t{from}\\t{to}\\t{kind_token}\\t{T}                  sorterade pa (from,to,kind,T)
    hopfogade med "\\n", ingen avslutande radbrytning
    T = 1 om lanken ligger i adjacensen, annars 0

Niva 1 (`stamp`) ar FNV-1a-64 over  map ++ LE32(celler) ++ LE32(lankar) ++
LE32(rj_lankar). Niva 2 (`niva2`) ar SHA-256 over inventeringen ovan.

Kostnads- och avfartsparametrar ingar INTE i nagon av hasharna — de bor i
sidotabeller. Tva grafer med samma niva-2 kan alltsa prissattas olika.

Ren biblioteksmodul: inga sidoeffekter vid import.
"""
import hashlib
import struct

__all__ = ["fnv1a64", "stamp", "inventering", "niva2"]


def fnv1a64(b: bytes) -> int:
    h = 0xCBF29CE484222325
    for c in b:
        h ^= c
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def stamp(map_name: str, cells: int, links: int, rj: int) -> str:
    """Niva 1: FNV-1a-64 over kartnamn + de tre antalen."""
    return str(fnv1a64(map_name.encode("utf-8") + struct.pack("<III", cells, links, rj)))


def inventering(cells, lrecs) -> str:
    """Den kanoniska texten. `lrecs` ar (from, to, kind, T)-tuplar."""
    rader = [f"C\t{i}\t{int(c[0])}\t{int(c[1])}\t{int(c[2])}" for i, c in enumerate(cells)]
    for src, dst, kind, t in sorted(lrecs):
        rader.append(f"L\t{src}\t{dst}\t{kind}\t{t}")
    return "\n".join(rader)


def niva2(cells, lrecs) -> str:
    """Niva 2: SHA-256 over den kanoniska inventeringen."""
    return hashlib.sha256(inventering(cells, lrecs).encode("utf-8")).hexdigest()
