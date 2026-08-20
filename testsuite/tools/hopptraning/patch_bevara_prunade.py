#!/usr/bin/env python3
"""FIX: en lankborttagning far inte ateruppvacka de carve-rensade lankarna.

`remove_links_by_id` komprimerar lankarrayen och bygger om adjacensen fran noll
med `push_link` — och `push_link` lagger IN varje lank i adjacensen. Foljden ar
att de lankar carven avsiktligt rensat ur adjacensen (T=0, men kvar i arrayen)
blir traverserbara igen sa fort nagon tar bort en enda annan lank.

Pa den har riggen ar det 15 lankar. Att stanga tva oflygbara korsningar skulle
alltsa samtidigt oppna 15 kanter som kartbygget medvetet skurit av — bland dem
teleportcellernas utgangar, som ingen spelare kan ga. Det ar en bieffekt langt
utanfor atgarden, och det ar samma T-ateruppstandelse som facitmallens klausul 6
ar skriven ur.

Harledningen visar skillnaden svart pa vitt (~/hopptraning/harlett-slutlage.json):
  A) prunade bevaras : nivå-2 b440dfe4...
  B) dagens beteende : nivå-2 ff732a9a...  + 15 ateruppstandna

Fixen: las av vilka lankar som star utanfor adjacensen INNAN komprimeringen, och
ta bort deras nya index ur adjacensen efter ombygget. Arrayen, id-omrappningen
och sidotabellerna ar oforandrade — bara adjacensmedlemskapet bevaras.
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
    "crates/rtx-nav/src/navmesh/mod.rs",
    """        let mut removed = Vec::with_capacity(seen.len());
        let mut kept: Vec<Link> = Vec::with_capacity(self.links.len() - seen.len());""",
    """        // Which links are currently *out* of the adjacency — the ones a carve severed on purpose
        // (T=0 in the canonical inventory). The rebuild below runs every kept link through
        // `push_link`, which puts it back in, so without this they silently become traversable again
        // and one removal re-opens every carved edge in the graph.
        let in_adj: std::collections::HashSet<u32> = self.adjacency.iter().flatten().copied().collect();
        let pruned_before: Vec<u32> = (0..self.links.len() as u32).filter(|i| !in_adj.contains(i)).collect();
        let mut removed = Vec::with_capacity(seen.len());
        let mut kept: Vec<Link> = Vec::with_capacity(self.links.len() - seen.len());""",
)

patcha(
    "crates/rtx-nav/src/navmesh/mod.rs",
    """        for link in kept {
            self.push_link(link);
        }
        // Per-link columns (not SideTable): compact in the same old→new order.""",
    """        for link in kept {
            self.push_link(link);
        }
        // Restore the carve: a link that was pruned before is pruned after. Its id moved with the
        // compaction, so map through `old_to_new` — a pruned link that was itself removed simply has
        // no new id and needs nothing.
        for old in pruned_before {
            if let Some(new) = old_to_new[old as usize] {
                let from = self.links[new as usize].from as usize;
                self.adjacency[from].retain(|&li| li != new);
            }
        }
        // Per-link columns (not SideTable): compact in the same old→new order.""",
)

# Enhetstest: en prunad lank ska overleva en borttagning som prunad.
patcha(
    "crates/rtx-nav/src/navmesh/mod.rs",
    """        assert!(g.pruned_out_links(1).is_empty());
    }
}""",
    """        assert!(g.pruned_out_links(1).is_empty());
    }

    #[test]
    fn removing_a_link_does_not_resurrect_the_carved_ones() {
        // Three cells in a row, walks both ways, plus a spare edge to delete. Prune 0->1 the way the
        // teleport carve does: out of the adjacency, still in the array.
        let mut g = NavGraph::test_graph(
            vec![
                Cell { origin: Vec3::new(0.0, 0.0, 0.0), ..Default::default() },
                Cell { origin: Vec3::new(32.0, 0.0, 0.0), ..Default::default() },
                Cell { origin: Vec3::new(64.0, 0.0, 0.0), ..Default::default() },
            ],
            vec![
                Link { from: 0, to: 1, kind: LinkKind::Walk, cost: 1.0 },
                Link { from: 1, to: 2, kind: LinkKind::Walk, cost: 1.0 },
                Link { from: 2, to: 1, kind: LinkKind::Walk, cost: 1.0 },
            ],
        );
        g.adjacency[0].retain(|&li| li != 0);
        assert_eq!(g.pruned_out_links(0), vec![0], "0->1 starts pruned");

        // Delete an unrelated link. The pruned one keeps its id-slot semantics and must stay pruned.
        g.remove_links_by_id(&[2]).expect("remove the spare edge");
        assert_eq!(g.links.len(), 2, "the array shrank by exactly the removed link");
        assert_eq!(
            g.pruned_out_links(0),
            vec![0],
            "the carved link came back into the adjacency — one removal re-opened it"
        );
    }
}""",
)

print("KLART")
