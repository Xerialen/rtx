// SPDX-License-Identifier: AGPL-3.0-or-later

//! Operator-set per-link A* surcharges — the engine side of [`rtx_ctlproto::Cmd::Recost`].
//!
//! # Why this is not a graph edit
//!
//! Making a link expensive has two possible homes and only one of them is safe for measurement.
//! `Link.cost` is *structure*: it travels in the graph's level-2 identity hash, so writing to it
//! would move the identity of the graph the server ships and invalidate every measurement corpus
//! pinned against the old one. `LinkCosts::penalties` is *runtime pricing*: A* already carries a
//! per-link-index surcharge there (`rtx-nav`'s `NavGraph::link_extra`), the graph is untouched,
//! and an arm can be priced and unpriced without anything downstream being cassated.
//!
//! So the table here is runtime state on `GameState`, and it is fed into the pricing that
//! `bot_link_pricing` already assembles.
//!
//! # The three properties this module exists to hold
//!
//! **The anchor gate.** A bare link id means nothing across graphs — index 36314 is a different
//! link in a different build. Every spec carries `from`/`to`/`kind` that must stand at that id,
//! and every spec is checked before any is applied, so a mismatched batch changes nothing at all.
//!
//! **One extra per link.** `NavGraph::link_extra` scans `penalties` and **breaks at the first
//! match** — a second entry for the same link is silently ignored, not summed. That is why the
//! bot side merges rather than pushes (`merge_link_penalty`), and why this module refuses a batch
//! that names the same link twice instead of quietly honouring one of them.
//!
//! **Non-negative seconds.** A*'s straight-line heuristic is admissible only while every cost
//! term is non-negative. A negative surcharge would not make a link cheap; it would make routes
//! wrong, silently and only sometimes. Refused at the gate.

use sha2::{Digest, Sha256};

use rtx_ctlproto as proto;
use rtx_nav::navmesh::NavGraph;

/// The live operator surcharge table, plus the provenance a measurement band stamps.
///
/// Lives on `GameState` rather than in `rtx-nav`'s `NavState` for the same reason `recept` does:
/// nothing else in `rtx-nav` is to be touched. Reset on map load — the table is anchored to one
/// graph, and a new map is a new graph.
#[derive(Default, Clone, Debug, PartialEq)]
pub(crate) struct RecostTable {
    /// `(link idx, extra seconds)`, sorted by link idx, at most one entry per link.
    entries: Vec<(u32, f32)>,
    /// Level-2 identity of the graph the entries were anchored against.
    graph_hash: String,
    /// SHA-256 over `graph_hash` and `entries`. Empty while the table is empty.
    hash: String,
}

impl RecostTable {
    pub(crate) fn entries(&self) -> &[(u32, f32)] {
        &self.entries
    }

    pub(crate) fn graph_hash(&self) -> &str {
        &self.graph_hash
    }

    /// The stamp. Empty string while no pricing is in force — never a hash of nothing, because a
    /// band quoting a hash must mean "this regime ran", not "some regime may have".
    pub(crate) fn hash(&self) -> &str {
        &self.hash
    }

    /// Replace the whole table. `entries` must already be gate-checked by [`recost_anchored`].
    pub(crate) fn set(&mut self, mut entries: Vec<(u32, f32)>, graph_hash: String) {
        entries.sort_unstable_by_key(|&(li, _)| li);
        self.hash = stamp(&graph_hash, &entries);
        self.entries = entries;
        self.graph_hash = graph_hash;
    }

    /// The wire form of the table, for a reply or a status frame.
    pub(crate) fn as_entries(&self) -> Vec<proto::RecostEntry> {
        self.entries
            .iter()
            .map(|&(link, extra_sec)| proto::RecostEntry { link, extra_sec })
            .collect()
    }
}

/// The provenance stamp: SHA-256 over the graph identity **and** the priced table.
///
/// Both halves are load-bearing. The same surcharges against a different graph are a different
/// regime (the ids mean different links), and the same graph with different surcharges obviously
/// is too — so a band that quotes this hash has pinned both. Seconds go in by their exact bit
/// pattern rather than a rounded rendering, so two tables hash alike only if they really are alike.
fn stamp(graph_hash: &str, entries: &[(u32, f32)]) -> String {
    if entries.is_empty() {
        return String::new();
    }
    let mut text = format!("recost-v1\tgraph\t{graph_hash}\n");
    for &(li, extra) in entries {
        text.push_str(&format!("L\t{li}\t{:08x}\n", extra.to_bits()));
    }
    let mut h = Sha256::new();
    h.update(text.as_bytes());
    format!("{:x}", h.finalize())
}

/// Verify every spec against the live graph, then return the table it describes.
///
/// All-or-nothing: this returns `Err` without the caller having touched anything, so a batch with
/// one bad spec leaves the engine exactly as it was. The graph is taken by shared reference
/// because nothing here may modify it — that is the guarantee, expressed in the signature.
pub(crate) fn recost_anchored(g: &NavGraph, specs: &[proto::RecostSpec]) -> Result<Vec<(u32, f32)>, String> {
    if specs.is_empty() {
        return Err("Recost utan länkar".into());
    }
    let mut out: Vec<(u32, f32)> = Vec::with_capacity(specs.len());
    for spec in specs {
        let Some(want_kind) = crate::graph_ident::kind_from_token(&spec.kind) else {
            return Err(format!("okänd länkart {:?} på id {}", spec.kind, spec.id));
        };
        if !spec.extra_sec.is_finite() {
            return Err(format!("id {}: extra_sec {} är inte ändlig", spec.id, spec.extra_sec));
        }
        // A* stays optimal only while every cost term is non-negative (see `LinkCosts`).
        if spec.extra_sec < 0.0 {
            return Err(format!(
                "id {}: extra_sec {} är negativ — A*:s heuristik kräver icke-negativa termer",
                spec.id, spec.extra_sec
            ));
        }
        // `link_extra` breaks at the first matching entry, so a duplicate would be silently
        // dropped rather than summed. Say so instead of picking one.
        if out.iter().any(|&(li, _)| li == spec.id) {
            return Err(format!("id {} nämns två gånger i samma Recost", spec.id));
        }
        match g.links.get(spec.id as usize) {
            None => {
                return Err(format!("länk-id {} finns inte ({} länkar)", spec.id, g.links.len()));
            }
            Some(l) if l.from == spec.from && l.to == spec.to && l.kind == want_kind => {
                out.push((spec.id, spec.extra_sec));
            }
            Some(l) => {
                return Err(format!(
                    "ankaret håller inte: id {} är {}->{} {}, Recost säger {}->{} {}",
                    spec.id,
                    l.from,
                    l.to,
                    crate::graph_ident::kind_token(l.kind),
                    spec.from,
                    spec.to,
                    spec.kind
                ));
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use glam::Vec3;
    use rtx_nav::navmesh::{Link, LinkCosts, LinkKind};

    /// Three cells in a row, two walk links: 0->1 (id 0) and 1->2 (id 1).
    fn graph() -> NavGraph {
        NavGraph::from_topology(
            &[
                Vec3::new(0.0, 0.0, 0.0),
                Vec3::new(32.0, 0.0, 0.0),
                Vec3::new(64.0, 0.0, 0.0),
            ],
            &[
                Link {
                    from: 0,
                    to: 1,
                    kind: LinkKind::Walk,
                    cost: 1.0,
                },
                Link {
                    from: 1,
                    to: 2,
                    kind: LinkKind::Walk,
                    cost: 1.0,
                },
            ],
        )
    }

    fn spec(id: u32, from: u32, to: u32, kind: &str, extra: f32) -> proto::RecostSpec {
        proto::RecostSpec {
            id,
            from,
            to,
            kind: kind.into(),
            extra_sec: extra,
        }
    }

    #[test]
    fn a_matching_anchor_prices_the_link() {
        let g = graph();
        let out = recost_anchored(&g, &[spec(1, 1, 2, "walk", 4.0)]).expect("anchor holds");
        assert_eq!(out, vec![(1, 4.0)]);
    }

    /// **Falskt negativ 1.** An id that is not in the base graph is refused, and nothing is set.
    #[test]
    fn an_id_outside_the_graph_is_refused() {
        let g = graph();
        let err = recost_anchored(&g, &[spec(36314, 1, 2, "walk", 4.0)]).unwrap_err();
        assert!(err.contains("finns inte"), "{err}");
    }

    /// The id exists but names a different link than the caller pinned — a raw id from another
    /// graph. Refused on the anchor, which is the whole reason the anchor is on the wire.
    #[test]
    fn a_mismatched_anchor_is_refused() {
        let g = graph();
        let err = recost_anchored(&g, &[spec(1, 0, 1, "walk", 4.0)]).unwrap_err();
        assert!(err.contains("ankaret håller inte"), "{err}");

        let err = recost_anchored(&g, &[spec(1, 1, 2, "teleport", 4.0)]).unwrap_err();
        assert!(err.contains("ankaret håller inte"), "{err}");
    }

    /// All-or-nothing: one bad spec in a batch and the whole batch is refused.
    #[test]
    fn one_bad_spec_refuses_the_whole_batch() {
        let g = graph();
        let err = recost_anchored(&g, &[spec(0, 0, 1, "walk", 2.0), spec(1, 0, 1, "walk", 4.0)]).unwrap_err();
        assert!(err.contains("ankaret håller inte"), "{err}");
    }

    /// A negative surcharge would break A*'s admissibility rather than making a link cheap.
    #[test]
    fn a_negative_surcharge_is_refused() {
        let g = graph();
        let err = recost_anchored(&g, &[spec(1, 1, 2, "walk", -1.0)]).unwrap_err();
        assert!(err.contains("negativ"), "{err}");
    }

    #[test]
    fn a_non_finite_surcharge_is_refused() {
        let g = graph();
        for bad in [f32::NAN, f32::INFINITY] {
            let err = recost_anchored(&g, &[spec(1, 1, 2, "walk", bad)]).unwrap_err();
            assert!(err.contains("ändlig"), "{err}");
        }
    }

    /// The nav query keeps one extra per link and breaks at the first match, so a duplicate
    /// would be silently dropped. Refuse rather than honour an arbitrary one.
    #[test]
    fn the_same_link_twice_is_refused() {
        let g = graph();
        let err = recost_anchored(&g, &[spec(1, 1, 2, "walk", 2.0), spec(1, 1, 2, "walk", 3.0)]).unwrap_err();
        assert!(err.contains("två gånger"), "{err}");
    }

    #[test]
    fn an_empty_batch_is_refused() {
        let g = graph();
        assert!(recost_anchored(&g, &[]).is_err());
    }

    /// The stamp binds the table *and* the graph it was anchored against, and moves when either
    /// does. Without both halves a band could quote a hash that two different regimes share.
    #[test]
    fn the_stamp_binds_both_the_graph_and_the_table() {
        let a = stamp("graf-a", &[(1, 4.0)]);
        assert_eq!(a, stamp("graf-a", &[(1, 4.0)]), "deterministisk");
        assert_ne!(a, stamp("graf-b", &[(1, 4.0)]), "grafidentiteten bidrar");
        assert_ne!(a, stamp("graf-a", &[(2, 4.0)]), "länk-id bidrar");
        assert_ne!(a, stamp("graf-a", &[(1, 4.5)]), "sekunderna bidrar");
        assert_ne!(a, stamp("graf-a", &[(1, 4.0), (2, 1.0)]), "en extra länk bidrar");
    }

    /// An unpriced engine stamps nothing. A hash over an empty table would be a constant that
    /// looks like provenance while asserting nothing.
    #[test]
    fn an_empty_table_has_no_stamp() {
        assert_eq!(stamp("graf-a", &[]), "");
        assert_eq!(RecostTable::default().hash(), "");
    }

    /// Two branches of exactly equal cost, so a tie-break decides which one A* takes.
    fn diamond() -> NavGraph {
        let w = |from: u32, to: u32| Link {
            from,
            to,
            kind: LinkKind::Walk,
            cost: 1.0,
        };
        NavGraph::from_topology(
            &[
                Vec3::new(0.0, 0.0, 0.0),
                Vec3::new(100.0, 50.0, 0.0),
                Vec3::new(100.0, -50.0, 0.0),
                Vec3::new(200.0, 0.0, 0.0),
            ],
            // link 0: 0→1, link 1: 1→3 (upper) · link 2: 0→2, link 3: 2→3 (lower)
            &[w(0, 1), w(1, 3), w(0, 2), w(2, 3)],
        )
    }

    /// A priced link stays diverted for **every** bot's jitter seed.
    ///
    /// Jitter exists to break ties between near-equal corridors, and it scales against a link's
    /// *base* cost — never against an operator surcharge. The consequence is what a measurement
    /// arm depends on: pricing a link is deterministic across bots. If jitter were scaled by the
    /// surcharge instead, a big penalty would buy a big random term and the arm would divert for
    /// some bots and not others, which is the one failure a per-arm comparison cannot survive.
    ///
    /// The first half is the negative control, and it is the reason this test is not vacuous:
    /// with the branches tied and no penalty, jitter really does send different seeds different
    /// ways. So the second half's unanimity is the penalty's doing, not a dead jitter term.
    #[test]
    fn a_priced_link_is_diverted_for_every_jitter_seed() {
        let g = diamond();
        let seeds: Vec<u32> = (1..=64).collect();

        let mut upper = 0;
        let mut lower = 0;
        for &jitter_seed in &seeds {
            let costs = LinkCosts {
                jitter_seed,
                ..Default::default()
            };
            match g.find_path(0, 3, &costs).expect("a route exists")[0] {
                0 => upper += 1,
                2 => lower += 1,
                other => panic!("unexpected first link {other}"),
            }
        }
        assert!(
            upper > 0 && lower > 0,
            "negative control: jitter must actually break the tie both ways \
             (upper={upper}, lower={lower}) — otherwise the assertion below proves nothing"
        );

        // Price the upper branch. Every seed must now take the lower one.
        let priced = [(0u32, 5.0f32)];
        for &jitter_seed in &seeds {
            let costs = LinkCosts {
                penalties: &priced,
                jitter_seed,
                ..Default::default()
            };
            assert_eq!(
                g.find_path(0, 3, &costs).expect("a route exists")[0],
                2,
                "seed {jitter_seed} did not honour the surcharge — pricing must not depend on \
                 which bot is asking"
            );
        }
    }

    /// The surcharge rides `LinkCosts::penalties`, so it leaves the graph alone.
    ///
    /// This is the property that lets an arm be priced without cassating a corpus: the level-2
    /// identity of the graph is the same before and after, because nothing was written to
    /// `Link.cost`.
    #[test]
    fn pricing_does_not_touch_the_graph() {
        let g = diamond();
        let before = crate::graph_ident::graph_content_hash(&g);
        let costs_before: Vec<f32> = g.links.iter().map(|l| l.cost).collect();

        let entries = recost_anchored(&g, &[spec(0, 0, 1, "walk", 5.0)]).expect("anchor holds");
        let mut table = RecostTable::default();
        table.set(entries, before.clone());

        assert_eq!(
            crate::graph_ident::graph_content_hash(&g),
            before,
            "grafidentiteten får inte röra sig av en prissättning"
        );
        assert_eq!(
            g.links.iter().map(|l| l.cost).collect::<Vec<_>>(),
            costs_before,
            "Link.cost är struktur och ska vara orörd"
        );
        assert_eq!(table.entries(), &[(0, 5.0)]);
    }

    /// The table is stored sorted, so the stamp does not depend on the order the operator
    /// happened to list the links in — two operators asking for the same regime agree.
    #[test]
    fn the_table_is_order_independent() {
        let mut a = RecostTable::default();
        a.set(vec![(2, 1.0), (1, 4.0)], "graf-a".into());
        let mut b = RecostTable::default();
        b.set(vec![(1, 4.0), (2, 1.0)], "graf-a".into());
        assert_eq!(a.entries(), b.entries());
        assert_eq!(a.hash(), b.hash());
        assert_eq!(a.entries(), &[(1, 4.0), (2, 1.0)]);
    }

    /// The wire form says exactly what the table holds — the readout does not lie.
    ///
    /// This is what a band's stamp is worth: Hopparen reads `(link, extra_sec)` off the reply or
    /// the status frame and writes it on the band. If the projection dropped, reordered or
    /// rounded an entry, every band stamped from it would misdescribe the regime it ran under,
    /// and the misdescription would be invisible — the numbers would still look like numbers.
    #[test]
    fn the_wire_form_mirrors_the_table_exactly() {
        let mut t = RecostTable::default();
        t.set(vec![(9, 1.5), (1, 4.0), (36314, 0.0)], "graf-a".into());

        let wire = t.as_entries();
        assert_eq!(wire.len(), t.entries().len(), "readouten tappar eller hittar på rader");
        for (w, &(link, extra)) in wire.iter().zip(t.entries()) {
            assert_eq!(w.link, link, "länk-id måste följa med orört");
            assert_eq!(
                w.extra_sec.to_bits(),
                extra.to_bits(),
                "sekunderna måste följa med bit för bit, inte avrundade"
            );
        }
        // Sorted, so two readouts of the same regime are byte-identical.
        assert_eq!(wire.iter().map(|e| e.link).collect::<Vec<_>>(), vec![1, 9, 36314]);
        // A zero surcharge is a real entry, not an absence: the operator said "price this link at
        // nothing", and a band must be able to tell that apart from an unpriced link.
        assert_eq!(wire[2].extra_sec, 0.0);
        assert!(!t.hash().is_empty(), "en satt tabell har en stämpel");
    }
}
