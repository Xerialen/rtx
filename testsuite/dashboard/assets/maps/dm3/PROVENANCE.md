# dm3 map assets — provenance

| file | source | generated |
|---|---|---|
| graph.json | rtx control channel `graph` dump, meganav navmesh (4635 cells) | 2026-07-26, fasttrack lab |
| entities.json | dm3.bsp entity lump extraction (items/spawns/teleports) | 2026-07-26 |
| linkgeo-meganav.json | link geometry resolved from the live meganav graph via cellbyid (49/60 links with >=3 firings in run 5) | 2026-07-27 |

Cell ids are graph-version-specific: the meganav graph (4635 cells) does not share
ids with the pre-meganav graph (4631). Snapshot writers must record which graph they
ran against; the dashboard resolves positions from the snapshot's own embedded `pos`
when present, falling back to these assets.
