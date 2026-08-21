// SPDX-License-Identifier: AGPL-3.0-or-later

//! Automatisk receptapplicering vid kartladdning.
//!
//! Bygger `WORK_LOGS/facit-receptautostart-v2.md`, som binder
//! `PLANS/2026-08-20-designforslag-receptautostart-v2.md`. Ett **recept** är en
//! namngiven, ordnad följd av planterade länkar — riggdata, inte kod. Två riggar
//! med identisk binär beter sig olika om den ena kör ett recept, och den
//! skillnaden är osynlig i binärens sha256. Därav loggraden och deklarationen.
//!
//! Fyra egenskaper är bindande och prövas var för sig i `tests`:
//!
//! * **Av som default.** Tom `rtx_recept_dir` ⇒ ingenting händer, tyst (facit §3.4).
//! * **Sökvägen är absolut eller gamedir-relativ, aldrig arbetskatalogsrelativ**,
//!   med tre lägen och inget tyst mellanläge (facit §3.5).
//! * **Allt-eller-inget via klon**: stegen muterar en kopia som publiceras först
//!   när slutläget validerat (facit §3.3).
//! * **Fail-open**: varje fel ger rå karta och en högljudd rad — aldrig panik,
//!   aldrig ett halvapplicerat läge (facit §4). Motorns egen navpatch gör
//!   likadant; det här är konsistens, inte avvikelse.
//!
//! Modulen är avsiktligt fri från motorberoenden: filläsning och plantering
//! kommer in som slutningar. Därför kan hela beslutstabellen provas utan server.

use rtx_nav::navmesh::NavGraph;

use crate::graph_ident::graph_content_hash;

/// Det lilla grafkontrakt appliceringen behöver: identiteten och en kopia.
///
/// Finns för att beslutstabellen — bindningen, klontransaktionen och
/// återrullningen — ska kunna provas utan att en hel `NavGraph` byggs. Motorns
/// implementation nedan är den enda i drift; testerna använder en attrapp och
/// prövar därför *logiken*, inte hashfunktionen (den prövas för sig i
/// [`crate::graph_ident`]).
pub trait Graf {
    fn innehallshash(&self) -> String;
    fn kopia(&self) -> Self;
}

impl Graf for NavGraph {
    fn innehallshash(&self) -> String {
        graph_content_hash(self)
    }
    fn kopia(&self) -> Self {
        self.clone()
    }
}

pub const UTFALL_APPLICERAT: &str = "applicerat";
pub const UTFALL_HOPPAT_OVER: &str = "hoppat_over";
pub const UTFALL_INGET: &str = "inget";

/// Vad som faktiskt hände, i den form deklarationen ska bära (facit §13).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Utfall {
    pub karta: String,
    pub filer: Vec<String>,
    pub utfall: String,
    pub skal: Option<String>,
    pub bas_hash: String,
    pub slut_hash: String,
    pub lankar: u32,
}

impl Utfall {
    fn inget(karta: &str) -> Self {
        Self {
            karta: karta.to_string(),
            utfall: UTFALL_INGET.to_string(),
            ..Default::default()
        }
    }

    fn hoppat_over(karta: &str, filer: Vec<String>, skal: String, bas_hash: String) -> Self {
        Self {
            karta: karta.to_string(),
            filer,
            utfall: UTFALL_HOPPAT_OVER.to_string(),
            skal: Some(skal),
            bas_hash,
            ..Default::default()
        }
    }

    /// Sant när utfallet ska skrivas till konsolen. Facit §4: raden skrivs alltid
    /// när utfallet inte är "av". Villkoras den raden faller nk3.
    pub fn ska_loggas(&self) -> bool {
        self.utfall != UTFALL_INGET || self.skal.is_some()
    }
}

/// Var recepten ska läsas ifrån (facit §3.5).
#[derive(Clone, Debug, PartialEq)]
pub enum Kalla {
    /// Tom cvar: funktionen är helt av.
    Av,
    /// Absolut sökväg i filsystemet.
    Absolut(String),
    /// Gamedir-relativ: läses genom motorns egen filsökväg, som söker gamedir
    /// och därefter basspelet. **Aldrig** relativt processens arbetskatalog —
    /// det är en sökväg som fungerar på riggen och tiger någon annanstans.
    Gamedir(String),
}

/// Tolka cvarens värde. Rena strängoperationer, så regeln kan provas utan filsystem.
pub fn resolvera(dir: &str) -> Kalla {
    let d = dir.trim();
    if d.is_empty() {
        return Kalla::Av;
    }
    if d.starts_with('/') {
        Kalla::Absolut(d.trim_end_matches('/').to_string())
    } else {
        Kalla::Gamedir(d.trim_end_matches('/').to_string())
    }
}

/// Ett planteringssteg, som det står i receptfilen.
#[derive(Clone, Debug, PartialEq)]
pub struct Steg {
    pub namn: String,
    pub from: [f32; 3],
    pub takeoff: [f32; 3],
    pub tgt: [f32; 3],
    pub v_req: f32,
    pub gain: f32,
}

/// Ett inläst recept.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Recept {
    pub bas: Option<String>,
    pub efter: Option<String>,
    pub steg: Vec<Steg>,
}

fn f3(v: &serde_json::Value) -> Option<[f32; 3]> {
    let a = v.as_array()?;
    if a.len() != 3 {
        return None;
    }
    Some([
        a[0].as_f64()? as f32,
        a[1].as_f64()? as f32,
        a[2].as_f64()? as f32,
    ])
}

/// Läs manifestet: karta → filer i tillämpad ordning.
///
/// Manifest, inte katalogskanning: ordningen är betydelsebärande, eftersom en
/// plantering resolverar sina ändpunkter genom `nearest` och därför kan svara
/// annorlunda efter en tidigare plantering. Katalogordning är filsystemets
/// godtycke; manifestet är ett beslut. En kvarglömd fil i katalogen blir en
/// no-op i stället för en tyst grafändring.
pub fn las_manifest(bytes: &[u8], karta: &str) -> Result<Vec<String>, String> {
    let v: serde_json::Value =
        serde_json::from_slice(bytes).map_err(|e| format!("manifest.json: {e}"))?;
    let kartor = v
        .get("kartor")
        .and_then(|k| k.as_object())
        .ok_or_else(|| "manifest.json: fältet \"kartor\" saknas".to_string())?;
    let Some(poster) = kartor.get(karta).and_then(|p| p.as_array()) else {
        return Ok(Vec::new());
    };
    let mut med: Vec<(i64, String)> = Vec::new();
    for p in poster {
        let fil = p
            .get("fil")
            .and_then(|f| f.as_str())
            .ok_or_else(|| format!("manifest.json: post för {karta} saknar \"fil\""))?;
        let ordning = p.get("ordning").and_then(|o| o.as_i64()).unwrap_or(i64::MAX);
        med.push((ordning, fil.to_string()));
    }
    med.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(med.into_iter().map(|(_, f)| f).collect())
}

/// Läs ett recept. Två former stöds, båda som de redan ligger versionerade:
/// planteringstabellen (`namn -> {frm|from, takeoff, tgt, v_req, gain}`) och
/// stegformen (`{bas, efter, steg:[…]}`).
pub fn las_recept(bytes: &[u8], filnamn: &str) -> Result<Recept, String> {
    let v: serde_json::Value =
        serde_json::from_slice(bytes).map_err(|e| format!("{filnamn}: {e}"))?;
    let obj = v
        .as_object()
        .ok_or_else(|| format!("{filnamn}: inte ett JSON-objekt"))?;

    let bas = v
        .get("bas")
        .and_then(|b| b.get("niva2_sha256"))
        .and_then(|s| s.as_str())
        .map(str::to_string);
    let efter = v
        .get("efter")
        .and_then(|b| b.get("niva2_sha256"))
        .and_then(|s| s.as_str())
        .map(str::to_string);

    let mut steg = Vec::new();
    if let Some(lista) = v.get("steg").and_then(|s| s.as_array()) {
        for s in lista {
            let namn = s.get("namn").and_then(|n| n.as_str()).unwrap_or("steg");
            let op = s.get("op").and_then(|o| o.as_str()).unwrap_or("PlanLink");
            if op != "PlanLink" {
                return Err(format!("{filnamn}: op {op} stöds inte i etapp 1"));
            }
            steg.push(las_steg(namn, s, filnamn)?);
        }
        return Ok(Recept { bas, efter, steg });
    }

    // Planteringstabell. Nycklarna sorteras så inläsningen är deterministisk;
    // en tabell som behöver en annan ordning ska skrivas som stegrecept.
    let mut nycklar: Vec<&String> = obj.keys().collect();
    nycklar.sort();
    for namn in nycklar {
        let s = &obj[namn];
        if !s.is_object() || (s.get("frm").is_none() && s.get("from").is_none()) {
            continue;
        }
        steg.push(las_steg(namn, s, filnamn)?);
    }
    if steg.is_empty() {
        return Err(format!("{filnamn}: inga planteringssteg"));
    }
    Ok(Recept { bas, efter, steg })
}

fn las_steg(namn: &str, s: &serde_json::Value, filnamn: &str) -> Result<Steg, String> {
    let hamta = |nyckel: &str| -> Result<[f32; 3], String> {
        s.get(nyckel)
            .and_then(f3)
            .ok_or_else(|| format!("{filnamn}: {namn} saknar giltig \"{nyckel}\""))
    };
    let from = s
        .get("frm")
        .or_else(|| s.get("from"))
        .and_then(f3)
        .ok_or_else(|| format!("{filnamn}: {namn} saknar giltig \"from\""))?;
    Ok(Steg {
        namn: namn.to_string(),
        from,
        takeoff: hamta("takeoff")?,
        tgt: hamta("tgt")?,
        v_req: s
            .get("v_req")
            .and_then(|v| v.as_f64())
            .ok_or_else(|| format!("{filnamn}: {namn} saknar \"v_req\""))? as f32,
        gain: s
            .get("gain")
            .and_then(|v| v.as_f64())
            .ok_or_else(|| format!("{filnamn}: {namn} saknar \"gain\""))? as f32,
    })
}

/// Kör receptet för `karta` mot `graph`.
///
/// `las` hämtar en fil ur källan (`None` = finns inte / gick inte att läsa).
/// `plantera` planterar ett steg i grafen och svarar med länk-id.
///
/// Grafen muteras **bara** vid fullt validerat slutläge: stegen körs på en klon
/// som ersätter originalet sist. Ett fel någonstans lämnar därför originalet
/// orört, härledda tabeller inkluderade — det finns ingen väg till ett halvläge.
pub fn applicera<G, L, P>(karta: &str, kalla: &Kalla, las: L, graph: &mut G, plantera: P) -> Utfall
where
    G: Graf,
    L: Fn(&str) -> Option<Vec<u8>>,
    P: Fn(&mut G, &Steg) -> Result<u32, String>,
{
    if matches!(kalla, Kalla::Av) {
        return Utfall::inget(karta);
    }
    let bas_hash = graph.innehallshash();

    let Some(mbytes) = las("manifest.json") else {
        return Utfall::hoppat_over(
            karta,
            Vec::new(),
            format!("{} går inte att läsa", sokvag(kalla, "manifest.json")),
            bas_hash,
        );
    };
    let filer = match las_manifest(&mbytes, karta) {
        Ok(f) => f,
        Err(e) => return Utfall::hoppat_over(karta, Vec::new(), e, bas_hash),
    };
    if filer.is_empty() {
        // Ingen post för kartan är inget fel: receptet gäller inte här.
        return Utfall::inget(karta);
    }

    let mut recept = Vec::new();
    for fil in &filer {
        let Some(bytes) = las(fil) else {
            return Utfall::hoppat_over(
                karta,
                filer.clone(),
                format!("{} går inte att läsa", sokvag(kalla, fil)),
                bas_hash,
            );
        };
        match las_recept(&bytes, fil) {
            Ok(r) => recept.push(r),
            Err(e) => return Utfall::hoppat_over(karta, filer.clone(), e, bas_hash),
        }
    }

    // Bindningen prövas FÖRE första steget. Ett recept som tar bort eller
    // planterar hör till en grafidentitet; en annan motorversion bygger en annan
    // graf, och då är stegens ändpunkter inte längre samma punkter.
    for (fil, r) in filer.iter().zip(&recept) {
        if let Some(vantad) = &r.bas {
            if !bas_hash.starts_with(vantad.trim_end_matches('…')) {
                return Utfall::hoppat_over(
                    karta,
                    filer.clone(),
                    format!(
                        "fel bas: graf {}, {fil} väntar {vantad}",
                        kort(&bas_hash)
                    ),
                    bas_hash,
                );
            }
        }
    }

    // Klontransaktionen.
    let mut klon = graph.kopia();
    let mut lankar = 0u32;
    for (fil, r) in filer.iter().zip(&recept) {
        for steg in &r.steg {
            if let Err(e) = plantera(&mut klon, steg) {
                return Utfall::hoppat_over(
                    karta,
                    filer.clone(),
                    format!("{fil}: steget {} felade: {e}", steg.namn),
                    bas_hash,
                );
            }
            lankar += 1;
        }
    }
    let slut_hash = klon.innehallshash();
    if let Some(vantad) = recept.last().and_then(|r| r.efter.as_ref()) {
        if !slut_hash.starts_with(vantad.trim_end_matches('…')) {
            return Utfall::hoppat_over(
                karta,
                filer.clone(),
                format!(
                    "fel slutläge: graf {}, receptet väntar {vantad}",
                    kort(&slut_hash)
                ),
                bas_hash,
            );
        }
    }

    *graph = klon;
    Utfall {
        karta: karta.to_string(),
        filer,
        utfall: UTFALL_APPLICERAT.to_string(),
        skal: None,
        bas_hash,
        slut_hash,
        lankar,
    }
}

fn sokvag(kalla: &Kalla, fil: &str) -> String {
    match kalla {
        Kalla::Av => fil.to_string(),
        Kalla::Absolut(r) | Kalla::Gamedir(r) => format!("{r}/{fil}"),
    }
}

fn kort(h: &str) -> String {
    h.chars().take(8).collect::<String>() + "…"
}

/// Konsolraden. En rad, samma form i alla utfall, och den ska räcka för att
/// avgöra vad som kördes utan att någon loggar in på riggen (facit §4).
pub fn konsolrad(u: &Utfall, celler: u32, lankar: u32) -> String {
    match u.utfall.as_str() {
        UTFALL_APPLICERAT => format!(
            "rtx: recept: {} <- {}: OK {} länkar, graf {celler}c/{lankar}l {}\n",
            u.karta,
            u.filer.join("+"),
            u.lankar,
            kort(&u.slut_hash),
        ),
        _ => format!(
            "rtx: recept: {}{}: HOPPAS ÖVER ({}) — kör rå karta\n",
            u.karta,
            if u.filer.is_empty() {
                String::new()
            } else {
                format!(" <- {}", u.filer.join("+"))
            },
            u.skal.as_deref().unwrap_or("okänt skäl"),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const MANIFEST: &[u8] = br#"{"schema":"rtx-recept-manifest/1","kartor":{
        "dm3":[{"fil":"b.json","ordning":2},{"fil":"a.json","ordning":1}]}}"#;

    const TABELL: &[u8] = br#"{"P1 z=60":{"frm":[1,2,3],"takeoff":[4,5,6],"tgt":[7,8,9],
        "v_req":490.0,"gain":6.0}}"#;

    /// Facit §3.4 och §3.5: tre lägen, inget tyst mellanläge.
    #[test]
    fn sokvagsregeln_har_tre_lagen() {
        assert_eq!(resolvera(""), Kalla::Av);
        assert_eq!(resolvera("   "), Kalla::Av);
        assert_eq!(
            resolvera("/opt/recept"),
            Kalla::Absolut("/opt/recept".into())
        );
        // Relativt tolkas som gamedir-relativt — ALDRIG som arbetskatalogsrelativt.
        assert_eq!(
            resolvera("reference/recept"),
            Kalla::Gamedir("reference/recept".into())
        );
        assert_eq!(resolvera("/opt/recept/"), Kalla::Absolut("/opt/recept".into()));
    }

    /// Facit §7 test 2: okänd karta ⇒ inget recept, inget fel.
    #[test]
    fn okand_karta_ger_inget_recept() {
        assert!(las_manifest(MANIFEST, "dm6").unwrap().is_empty());
    }

    /// Manifestets ordning är betydelsebärande och styr, inte filnamnen.
    #[test]
    fn manifestet_bestammer_ordningen() {
        assert_eq!(las_manifest(MANIFEST, "dm3").unwrap(), vec!["a.json", "b.json"]);
    }

    /// Facit §7 test 3: trasig JSON ⇒ HOPPAS ÖVER, ingen panik.
    #[test]
    fn trasig_json_ger_fel_inte_panik() {
        assert!(las_manifest(b"{ inte json", "dm3").is_err());
        assert!(las_recept(b"{ inte json", "a.json").is_err());
    }

    #[test]
    fn planteringstabell_lases() {
        let r = las_recept(TABELL, "a.json").unwrap();
        assert_eq!(r.steg.len(), 1);
        assert_eq!(r.steg[0].namn, "P1 z=60");
        assert_eq!(r.steg[0].v_req, 490.0);
        assert_eq!(r.steg[0].from, [1.0, 2.0, 3.0]);
    }

    #[test]
    fn steg_utan_v_req_avvisas() {
        let bad = br#"{"P1":{"frm":[1,2,3],"takeoff":[4,5,6],"tgt":[7,8,9],"gain":6.0}}"#;
        assert!(las_recept(bad, "a.json").unwrap_err().contains("v_req"));
    }

    /// Facit §4: raden skrivs alltid när utfallet inte är "av". Görs den
    /// villkorlig faller nk3.
    #[test]
    fn loggraden_skrivs_for_allt_utom_av() {
        assert!(!Utfall::inget("dm3").ska_loggas());
        assert!(Utfall::hoppat_over("dm3", vec![], "fel bas".into(), "abc".into()).ska_loggas());
        let ok = Utfall {
            utfall: UTFALL_APPLICERAT.to_string(),
            ..Default::default()
        };
        assert!(ok.ska_loggas());
    }


    /// Attrappgraf: hashen är en ren funktion av innehållet, precis som den
    /// riktiga. Räcker för att pröva beslutstabellen; hashfunktionen själv
    /// prövas i `graph_ident`.
    #[derive(Clone, Debug, PartialEq)]
    struct FakeGraf {
        lankar: Vec<String>,
    }
    impl Graf for FakeGraf {
        fn innehallshash(&self) -> String {
            // Enkel men innehållsberoende: räcker för transaktionslogiken.
            let mut h: u64 = 1469598103934665603;
            for l in &self.lankar {
                for b in l.as_bytes() {
                    h ^= *b as u64;
                    h = h.wrapping_mul(1099511628211);
                }
            }
            format!("{h:016x}{h:016x}{h:016x}{h:016x}")
        }
        fn kopia(&self) -> Self {
            self.clone()
        }
    }

    fn tom() -> FakeGraf {
        FakeGraf { lankar: Vec::new() }
    }

    fn las_fran(filer: &[(&'static str, Vec<u8>)]) -> impl Fn(&str) -> Option<Vec<u8>> {
        let m: std::collections::HashMap<String, Vec<u8>> = filer
            .iter()
            .map(|(n, b)| (n.to_string(), b.clone()))
            .collect();
        move |n: &str| m.get(n).cloned()
    }

    fn plantera_ok(g: &mut FakeGraf, s: &Steg) -> Result<u32, String> {
        g.lankar.push(s.namn.clone());
        Ok((g.lankar.len() - 1) as u32)
    }

    fn manifest_med(filer: &[&str]) -> Vec<u8> {
        let poster: Vec<String> = filer
            .iter()
            .enumerate()
            .map(|(i, f)| format!("{{\"fil\":\"{f}\",\"ordning\":{}}}", i + 1))
            .collect();
        format!("{{\"kartor\":{{\"dm3\":[{}]}}}}", poster.join(",")).into_bytes()
    }

    /// Facit §3.4: av som default. Tom cvar ⇒ grafen orörd, tyst utfall.
    #[test]
    fn av_som_default_ror_ingenting() {
        let mut g = tom();
        let u = applicera("dm3", &Kalla::Av, |_| None, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_INGET);
        assert!(!u.ska_loggas());
        assert!(g.lankar.is_empty());
    }

    /// Facit §7 test 7: satt men oresolverbar ⇒ rå karta OCH loggrad.
    #[test]
    fn oresolverbar_kalla_ger_rakarta_och_rad() {
        let mut g = tom();
        let u = applicera(
            "dm3",
            &Kalla::Absolut("/finns/inte".into()),
            |_| None,
            &mut g,
            plantera_ok,
        );
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER);
        assert!(u.ska_loggas(), "tystnad är förbjuden här");
        assert!(u.skal.as_ref().unwrap().contains("/finns/inte/manifest.json"));
        assert!(g.lankar.is_empty());
    }

    /// Facit §7 test 4: fel bas ⇒ inget steg körs.
    #[test]
    fn fel_bas_kor_inget_steg() {
        let mut g = tom();
        let bas = g.innehallshash();
        let fel = format!("ff{}", &bas[2..]);
        let recept = format!(
            "{{\"bas\":{{\"niva2_sha256\":\"{fel}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
        );
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.into_bytes()),
        ]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER);
        assert!(u.skal.as_ref().unwrap().contains("fel bas"), "{:?}", u.skal);
        assert!(g.lankar.is_empty(), "grafen ska vara orörd");
    }

    /// Facit §7 test 5: stegen lyckas men slutläget matchar inte ⇒ allt tillbaka.
    #[test]
    fn fel_slutlage_rullar_tillbaka_allt() {
        let mut g = tom();
        let fore = g.clone();
        let recept = "{\"efter\":{\"niva2_sha256\":\"deadbeef\"},\"P1\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}";
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.as_bytes().to_vec()),
        ]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER);
        assert!(u.skal.as_ref().unwrap().contains("fel slutläge"), "{:?}", u.skal);
        assert_eq!(g, fore, "originalet ska vara orört");
    }

    /// Facit §7 test 6: ett steg felar mitt i ⇒ allt tillbaka, inget halvläge.
    #[test]
    fn steg_som_felar_mitt_i_rullar_tillbaka_allt() {
        let mut g = tom();
        let fore = g.clone();
        let recept = "{\"P1\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0},\"P2\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}";
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.as_bytes().to_vec()),
        ]);
        let u = applicera(
            "dm3",
            &Kalla::Absolut("/r".into()),
            las,
            &mut g,
            |g: &mut FakeGraf, s: &Steg| {
                if s.namn == "P2" {
                    return Err("ogiltig tgt".into());
                }
                g.lankar.push(s.namn.clone());
                Ok(0)
            },
        );
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER);
        assert!(u.skal.as_ref().unwrap().contains("P2"), "{:?}", u.skal);
        assert_eq!(g, fore, "P1 får inte ligga kvar");
    }

    /// Den lyckade vägen: alla steg, slutläget publicerat, deklarationen ifylld.
    #[test]
    fn lyckad_applicering_publicerar_klonen() {
        let mut g = tom();
        let recept = "{\"P1\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0},\"P2\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}";
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.as_bytes().to_vec()),
        ]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_APPLICERAT);
        assert_eq!(u.lankar, 2);
        assert_eq!(g.lankar, vec!["P1".to_string(), "P2".to_string()]);
        assert_ne!(u.bas_hash, u.slut_hash, "identiteten ska ha ändrats");
        assert!(u.ska_loggas());
    }

    /// nk5 (facit §10): en bunden konstant som hör till FEL graf ska fälla
    /// kontrollen. Matas bindningen med en annan grafs identitet får den aldrig
    /// passera — det var v1:s blockerande fel.
    #[test]
    fn nk5_konstant_fran_fel_graf_faller() {
        let mut annan = FakeGraf {
            lankar: vec!["nagot".into()],
        };
        let annan_hash = annan.innehallshash();
        let mut g = tom();
        assert_ne!(annan_hash, g.innehallshash(), "fixturerna måste skilja sig");
        let recept = format!(
            "{{\"bas\":{{\"niva2_sha256\":\"{annan_hash}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
        );
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.into_bytes()),
        ]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER, "fel graf måste fälla");
        assert!(u.skal.as_ref().unwrap().contains("fel bas"));
        annan.lankar.clear();
    }

    #[test]
    fn konsolraden_bar_utfallet() {
        let u = Utfall {
            karta: "dm3".into(),
            filer: vec!["a.json".into(), "b.json".into()],
            utfall: UTFALL_APPLICERAT.into(),
            slut_hash: "a1b2c3d4e5".into(),
            lankar: 5,
            ..Default::default()
        };
        let rad = konsolrad(&u, 5977, 48212);
        assert!(rad.contains("dm3 <- a.json+b.json"), "{rad}");
        assert!(rad.contains("OK 5 länkar"), "{rad}");
        assert!(rad.contains("5977c/48212l"), "{rad}");
        assert!(rad.ends_with('\n'));

        let h = Utfall::hoppat_over("dm3", vec!["a.json".into()], "fel bas".into(), "x".into());
        let rad = konsolrad(&h, 5977, 48207);
        assert!(rad.contains("HOPPAS ÖVER (fel bas)"), "{rad}");
        assert!(rad.contains("kör rå karta"), "{rad}");
    }
}
