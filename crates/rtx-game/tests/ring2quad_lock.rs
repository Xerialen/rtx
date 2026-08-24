// SPDX-License-Identifier: AGPL-3.0-or-later

//! RING2QUAD lock (K8). Egen fil av samma skal som `ra_room_lock.rs`,
//! `recost_lock.rs` och `plan_tick_lock.rs` ar sina: en pinne som bor inne i
//! filen den pinnar kan tystas av en andring som flyttar kallan och pinnen i
//! samma commit. En PR som raderar lib-testerna faller anda pa metaraknaren
//! har.
//!
//! Vad som INTE star har, och varfor:
//!
//! * cvar-fron (`rtx_bot_edge_narrow` m.fl.) — `ra-room-lock` ager dem redan
//!   (K10). Dubbellasning pa en delad fil laser nasta modul
//!   (`DM3-RORELSE` §5 «Las smalt»). Kravet uppfylls i stallet av att
//!   `ring2quad-lock` kors pa VARJE PR utan path-filter, sa en
//!   `cvars.rs`-only-PR anda kor hela det har laset medan `ra-room-lock`
//!   faller frovandningen.
//! * `vf5_inte_inbaddad` / `inbaddad_las_aldrig_vf5_eller_katalogscan` — redan
//!   required via `ra-room-lock` (K6.3). Refereras, pinnas inte tva ganger.
//! * predikatets ovriga karnkonstanter (fallhojd, vinklar, budget,
//!   start-/malkoordinater) — de bor i riggens `hoppa.py` och i orderdokument,
//!   inte i rtx-tradet. Ett CI-jobb har kan inte pinna dem; de star i stallet
//!   i varje ring2quad-order (K5).

use std::fs;
use std::path::PathBuf;

use sha2::{Digest, Sha256};

/// Reporoten, harledd ur kretsens manifestkatalog.
fn rot() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn las(rel: &str) -> String {
    let p = rot().join(rel);
    fs::read_to_string(&p)
        .unwrap_or_else(|e| panic!("RING2QUAD_LOCK: kan inte lasa {}: {e}", p.display()))
}

fn sha256(rel: &str) -> String {
    let p = rot().join(rel);
    let b = fs::read(&p)
        .unwrap_or_else(|e| panic!("RING2QUAD_LOCK: kan inte lasa {}: {e}", p.display()));
    format!("{:x}", Sha256::digest(&b))
}

/// Exakt en forekomst av `nal` i `hay`, annars panik med raknaren.
///
/// «Exakt en», inte «finns»: en andra, ljugande literal nagon annanstans i
/// filen hade annars uppfyllt en `contains`-kontroll medan den forsta bar fel
/// varde. Bygg arligt, ljug efterat ar precis den formen det har maste vagra.
fn exakt_en(hay: &str, nal: &str, vad: &str) {
    let n = hay.matches(nal).count();
    assert_eq!(n, 1, "RING2QUAD_LOCK: {vad} — vantade exakt en `{nal}`, raknare={n}");
}

// ---------------------------------------------------------------------------
// K5 — karnkonstanten LIP_REACH = 28.0, med VARDE
// ---------------------------------------------------------------------------

/// Receptets `PlanLink`-motiv sager att sydmissarna lag pa 27,1 och 27,65
/// «knappt innanfor» och att avfartslinjen darfor flyttades 12 u. Andras 28,0
/// dor 20/20 **tyst**.
///
/// Det befintliga `bhop::tests::lip_reach_matches_the_curl_certifier` pinnar
/// bara att de tva konstanterna ar lika **med varandra** — andras bada till
/// 30,0 forblir det gront. Darfor pinnas VARDET har, som kalltext. Ett test
/// som laser `LIP_REACH` foljer med vardet nar det andras och pinnar ingenting.
#[test]
fn lip_reach_ar_28_i_kalltext() {
    let bhop = las("crates/rtx-game/src/bot/bhop.rs");
    exakt_en(&bhop, "pub const LIP_REACH: f32 = 28.0;", "bhop.rs LIP_REACH");

    let fysik = las("crates/rtx-nav/src/navmesh/physics.rs");
    exakt_en(
        &fysik,
        "pub const CURL_LIP_REACH: f32 = 28.0;",
        "physics.rs CURL_LIP_REACH",
    );
}

// ---------------------------------------------------------------------------
// K6 — receptets identitet OCH innehall
// ---------------------------------------------------------------------------

const RECEPT: &str = "reference/recept/vf5_ring2quad_forkmain.json";

/// Sha256 pinnar identiteten. Innehallsasserterna gor en rod grind LASBAR:
/// utan dem sager en trasig pinne bara «sha skiljer», och nasta person far
/// gissa vad som andrades.
#[test]
fn receptets_identitet_och_innehall() {
    assert_eq!(
        sha256(RECEPT),
        "471e9b85612045e0b14593a4882a75f4a634c460326ed1ca925d3c9c4efd2da7",
        "RING2QUAD_LOCK: receptets sha256"
    );

    let t = las(RECEPT);
    let j: serde_json::Value =
        serde_json::from_str(&t).expect("RING2QUAD_LOCK: receptet ar giltig JSON");

    let steg = j["steg"].as_array().expect("RING2QUAD_LOCK: steg ar en lista");
    let ops: Vec<&str> = steg.iter().map(|s| s["op"].as_str().unwrap_or("?")).collect();
    assert_eq!(
        ops,
        vec!["PlanLink", "RemoveLinks"],
        "RING2QUAD_LOCK: receptets op-arter och deras ordning"
    );

    let plan = &steg[0];
    assert_eq!(plan["fran_cell"].as_u64(), Some(1450), "RING2QUAD_LOCK: PlanLink fran_cell");
    assert_eq!(plan["mal_cell"].as_u64(), Some(2083), "RING2QUAD_LOCK: PlanLink mal_cell");
    assert_eq!(
        plan["takeoff"].as_array().map(|a| a
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect::<Vec<f64>>()),
        Some(vec![454.7, 153.3, 56.0]),
        "RING2QUAD_LOCK: PlanLink takeoff"
    );
    assert_eq!(plan["v_req"].as_f64(), Some(419.33), "RING2QUAD_LOCK: PlanLink v_req");
    assert_eq!(plan["gain"].as_f64(), Some(6.0), "RING2QUAD_LOCK: PlanLink gain");

    let lankar = steg[1]["lankar"]
        .as_array()
        .expect("RING2QUAD_LOCK: RemoveLinks listar lankar");
    let ids: Vec<u64> = lankar.iter().map(|l| l["id"].as_u64().unwrap()).collect();
    assert_eq!(
        ids,
        vec![34501, 34503, 35683, 35761, 35762, 35592, 35738],
        "RING2QUAD_LOCK: de sju lank-id som tas bort"
    );

    assert_eq!(j["bas"]["celler"].as_u64(), Some(5977), "RING2QUAD_LOCK: bas celler");
    assert_eq!(
        j["bas"]["lankar_inkl_prunade"].as_u64(),
        Some(48207),
        "RING2QUAD_LOCK: bas lankar"
    );
    assert_eq!(
        j["bas"]["niva2_sha256"].as_str(),
        Some("58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"),
        "RING2QUAD_LOCK: bas niva-2"
    );
    assert_eq!(j["efter"]["celler"].as_u64(), Some(5977), "RING2QUAD_LOCK: efter celler");
    assert_eq!(
        j["efter"]["lankar_live"].as_u64(),
        Some(48201),
        "RING2QUAD_LOCK: efter lankar"
    );
    assert_eq!(
        j["efter"]["niva2_sha256"].as_str(),
        Some("dcb487f79abdd4157eda0637d617ea8ddd17764e56ad68e3f49e53e5f21dd87a"),
        "RING2QUAD_LOCK: efter niva-2"
    );
}

// ---------------------------------------------------------------------------
// C-instrumentet — pinnat efter QA-varvet
// ---------------------------------------------------------------------------

/// Fixklassbeslutet ar C (Sol GODKANN rev2). Instrumentet som producerar
/// C-talen ar darmed lastbarande och pinnas som fil.
///
/// Vardena ar de NYA efter QA-domen `2026-08-24-qa-dom-c-instrument.md`:
/// det forsta varvets `5331eb5b…` bar en PlanTick-avlasning som gav
/// `0 planerade` — ett positivt felaktigt pastaende som pekade at exakt det
/// hall Sols rev2 faller. Att pinna det hade gjort felet varaktigt.
#[test]
fn c_instrumentet_ar_pinnat() {
    for (fil, vantad, vad) in [
        (
            "testsuite/tools/fixklass_c.py",
            "c398ead95ba7afcaa392c40169a5148d475a238d391341b907b107d01b61e358",
            "C-instrumentet",
        ),
        (
            "testsuite/tools/mutationsprov_c.py",
            "cd11de36b7d0de0e60523cd6f1ce2caec69bb50a36f5f50df963d1ac3dbabe0f",
            "C-instrumentets mutationsprov",
        ),
        (
            "testsuite/tools/proveniens/arm-r.json",
            "5d1c6ab780869484fbab735f29161fe98e1b0686f73a4631449e2d48205e84b7",
            "ARM-R:s proveniensartefakt",
        ),
    ] {
        assert_eq!(sha256(fil), vantad, "RING2QUAD_LOCK: {vad} ({fil})");
    }

    // Artefakten ska vara forseglad, och sigillet ska stamma. Sidofilen ar
    // sjalvforseglande — den fangar drift, inte byte — sa filpinnen ovan ar
    // det som fangar ett avsiktligt byte.
    let sidofil = las("testsuite/tools/proveniens/arm-r.json.sha256");
    assert!(
        sidofil.starts_with("5d1c6ab780869484fbab735f29161fe98e1b0686f73a4631449e2d48205e84b7"),
        "RING2QUAD_LOCK: proveniensartefaktens sigill stammer inte: {sidofil:?}"
    );

    // Ingen hardkodad armidentitet i instrumentet (Sols villkor 5): den enda
    // vagen till den tillatna kombi-identiteten ar proveniensartefakten.
    let instr = las("testsuite/tools/fixklass_c.py");
    assert!(
        !instr.contains("ac0f7386"),
        "RING2QUAD_LOCK: ARM-R:s identitet far inte sta i instrumentet — \
         den kommer ur proveniensartefakten"
    );
}

// ---------------------------------------------------------------------------
// Kedjeprotokollet §29.2
// ---------------------------------------------------------------------------

/// Kedjedefinitionen bor i orderdokumenten i `buzz-4on4`, som inte ar en
/// git-spegel av det har repot och som CI inte kan lasa. Den pinnas darfor
/// har som TEXT plus kalldokumentets sha256 — sa att en tyst omdefinition av
/// «hel kedja» syns som en rod grind i stallet for som en tystare siffra.
///
/// Kalla: `WORK_LOGS/2026-08-21-order-s4b-ring2quad.md`,
/// sha256 `16fe2a8358262e6667232da55dff9eb35f7db5fadaa2a126a5bf5b37a1fb2b75`.
const KEDJA_29_2: &str = "\
ett kedjeforsok = de tre benen i foljd, vart och ett teleporterat till sin \
egen startpunkt precis som nar hoppen mattes var for sig. Kedjan raknas som \
hel endast om alla tre benen lyckas; fall var som helst bryter hela \
kedjeforsoket direkt och boten parkeras.";

#[test]
fn kedjeprotokollet_29_2_ar_pinnat() {
    // Tre ben, alla tre maste lyckas, fall bryter direkt.
    for bit in [
        "de tre benen i foljd",
        "teleporterat till sin egen startpunkt",
        "endast om alla tre benen lyckas",
        "bryter hela kedjeforsoket direkt",
    ] {
        assert!(
            KEDJA_29_2.contains(bit),
            "RING2QUAD_LOCK: kedjedefinitionen §29.2 saknar «{bit}»"
        );
    }
    // Inte sammanhangande fardvag — den lasningen ar uttryckligen utesluten.
    assert!(
        !KEDJA_29_2.contains("sammanhangande"),
        "RING2QUAD_LOCK: §29.2 ar INTE en sammanhangande fardvag"
    );
}

// ---------------------------------------------------------------------------
// Metalas (K8) — filen och dess raknare
// ---------------------------------------------------------------------------

/// Raknaren gor att en PR som tommer den har filen faller aven om den lamnar
/// filen kvar. YAML-steget rakar samma prefix utifran.
#[test]
fn metalas_prefixraknare() {
    let sjalv = las("crates/rtx-game/tests/ring2quad_lock.rs");
    let n = sjalv.matches("RING2QUAD_LOCK").count();
    assert!(
        n >= 25,
        "RING2QUAD_LOCK: metaraknaren — vantade minst 25 forekomster, fick {n}"
    );
}
