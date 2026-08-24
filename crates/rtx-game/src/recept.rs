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
//! * **Tom dir är no-op som sökväg.** `rtx_recept_dir=""` ⇒ ingen katalogscan
//!   (facit §3.4). På dm3 anropar navbygget samma `applicera` mot *inbäddade*
//!   climb+väst-bytes (K2 bake, ägarorder 22/8) — inte en andra plantkärna.
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
//!
//! ## Två stramningar efter QA-domen 2026-08-21
//!
//! * **K2 — grafkonstanterna binds på full längd.** `bas`/`efter` matchades på
//!   prefix, och QA visade att åtta hextecken räckte för att passera facit §7
//!   test 4. Nu krävs 64 hextecken och exakt likhet; en förkortad konstant
//!   avvisas som ogiltig indata i stället för att nästan stämma.
//! * **K1 — slutlägesjämförelsen görs per fil.** `recept.last()` läste bara
//!   sista filens `efter`, så en kedja utan `efter` i sista filen jämförde
//!   ingenting alls. Nu prövas **varje** fils `efter` efter just den filens
//!   steg; sista filens `efter` är därmed hela kedjans slutläge, vilket är
//!   design v2 §4.4:s krav som specialfall.

use rtx_nav::navmesh::NavGraph;

use crate::graph_ident::graph_content_hash;
use rtx_ctlproto::RemoveLinkSpec;

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
    /// Compile-time inbäddade climb+väst-bytes. Ingen sökväg, ingen katalogscan,
    /// aldrig vf5. Bara dm3-defaultgrafen när cvaren är tom.
    Inbaddad,
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

/// Nivå-2 efter bakad K2 på dm3 (climb+väst, fem PlanLink). Identitet, inte 99 %.
pub const K2_BAKE_NIVA2: &str = "feeea6b41284a1cddf3907f2d9e1ff668b48da524b865530df81925b997dbaa9";

const INBADDAD_MANIFEST: &[u8] = include_bytes!("../../../reference/recept/manifest.json");
const INBADDAD_CLIMB: &[u8] = include_bytes!("../../../reference/recept/ra_climb_planted.json");
const INBADDAD_VAST: &[u8] = include_bytes!("../../../reference/recept/vast_296_planted.json");

/// Filläsning mot de inbäddade climb+väst-bytena. Namngiven, inte katalogscan:
/// vf5 och andra filer i `reference/recept/` returnerar `None`.
pub fn las_inbaddad(namn: &str) -> Option<Vec<u8>> {
    match namn {
        "manifest.json" => Some(INBADDAD_MANIFEST.to_vec()),
        "ra_climb_planted.json" => Some(INBADDAD_CLIMB.to_vec()),
        "vast_296_planted.json" => Some(INBADDAD_VAST.to_vec()),
        _ => None,
    }
}

/// Vilken källa defaultgrafen ska läsa.
///
/// Tom dir är no-op som *sökväg* (facit §3.4). På dm3 byts den mot inbäddade
/// climb+väst-bytes (K2 bake). Satt dir ⇒ befintlig autostart, ingen bake —
/// annars dubbelplant.
pub fn defaultgraf_kalla(dir: &str, karta: &str) -> Option<Kalla> {
    // RA-room lock — ring2quad får inte revert
    match resolvera(dir) {
        Kalla::Av if karta == "dm3" => Some(Kalla::Inbaddad),
        Kalla::Av => None,
        k => Some(k),
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
    /// `Some` = `RemoveLinks` (namngiven fil via `rtx_recept_dir`). `None` = PlanLink.
    pub remove: Option<Vec<RemoveLinkSpec>>,
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
    Some([a[0].as_f64()? as f32, a[1].as_f64()? as f32, a[2].as_f64()? as f32])
}

/// Läs manifestet: karta → filer i tillämpad ordning.
///
/// Manifest, inte katalogskanning: ordningen är betydelsebärande, eftersom en
/// plantering resolverar sina ändpunkter genom `nearest` och därför kan svara
/// annorlunda efter en tidigare plantering. Katalogordning är filsystemets
/// godtycke; manifestet är ett beslut. En kvarglömd fil i katalogen blir en
/// no-op i stället för en tyst grafändring.
pub fn las_manifest(bytes: &[u8], karta: &str) -> Result<Vec<String>, String> {
    let v: serde_json::Value = serde_json::from_slice(bytes).map_err(|e| format!("manifest.json: {e}"))?;
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
    las_recept_ex(bytes, filnamn, true)
}

/// `tillat_remove`: namngivna filer via `rtx_recept_dir` får `RemoveLinks`.
/// Inbäddad källa (K2-bake) stannar PlanLink-only.
pub fn las_recept_ex(bytes: &[u8], filnamn: &str, tillat_remove: bool) -> Result<Recept, String> {
    let v: serde_json::Value = serde_json::from_slice(bytes).map_err(|e| format!("{filnamn}: {e}"))?;
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
            match op {
                "PlanLink" => steg.push(las_steg(namn, s, filnamn)?),
                "RemoveLinks" if tillat_remove => steg.push(las_remove_steg(namn, s, filnamn)?),
                "RemoveLinks" => {
                    return Err(format!("{filnamn}: op RemoveLinks stöds inte på inbäddad källa"));
                }
                _ => return Err(format!("{filnamn}: op {op} stöds inte")),
            }
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
        remove: None,
    })
}

fn las_remove_steg(namn: &str, s: &serde_json::Value, filnamn: &str) -> Result<Steg, String> {
    let lista = s
        .get("lankar")
        .and_then(|v| v.as_array())
        .ok_or_else(|| format!("{filnamn}: {namn} saknar \"lankar\""))?;
    if lista.is_empty() {
        return Err(format!("{filnamn}: {namn} RemoveLinks utan länkar"));
    }
    let mut lankar = Vec::with_capacity(lista.len());
    for (i, L) in lista.iter().enumerate() {
        let id = L
            .get("id")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| format!("{filnamn}: {namn} lankar[{i}] saknar id"))? as u32;
        let from = L
            .get("from")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| format!("{filnamn}: {namn} lankar[{i}] saknar from"))? as u32;
        let to = L
            .get("to")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| format!("{filnamn}: {namn} lankar[{i}] saknar to"))? as u32;
        let kind = L
            .get("kind")
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("{filnamn}: {namn} lankar[{i}] saknar kind"))?
            .to_string();
        lankar.push(RemoveLinkSpec { id, from, to, kind });
    }
    Ok(Steg {
        namn: namn.to_string(),
        from: [0.0; 3],
        takeoff: [0.0; 3],
        tgt: [0.0; 3],
        v_req: 0.0,
        gain: 0.0,
        remove: Some(lankar),
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
        let tillat_remove = !matches!(kalla, Kalla::Inbaddad);
        match las_recept_ex(&bytes, fil, tillat_remove) {
            Ok(r) => recept.push(r),
            Err(e) => return Utfall::hoppat_over(karta, filer.clone(), e, bas_hash),
        }
    }

    // Bindningen prövas FÖRE första steget. Ett recept som tar bort eller
    // planterar hör till en grafidentitet; en annan motorversion bygger en annan
    // graf, och då är stegens ändpunkter inte längre samma punkter.
    for (fil, r) in filer.iter().zip(&recept) {
        if let Some(vantad) = &r.bas {
            if !ar_full_hex64(vantad) {
                return Utfall::hoppat_over(
                    karta,
                    filer.clone(),
                    format!("ogiltig bas-konstant i {fil}: {vantad} — kräver full 64-teckens hex"),
                    bas_hash,
                );
            }
            if !vantad.eq_ignore_ascii_case(&bas_hash) {
                return Utfall::hoppat_over(
                    karta,
                    filer.clone(),
                    format!("fel bas: graf {}, {fil} väntar {vantad}", kort(&bas_hash)),
                    bas_hash,
                );
            }
        }
    }

    // Klontransaktionen.
    let mut klon = graph.kopia();
    let mut lankar = 0u32;
    let mut slut_hash = String::new();
    for (i, (fil, r)) in filer.iter().zip(&recept).enumerate() {
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
        // K1/L9 (QA-domen 2026-08-21). **Varje fils `efter` prövas efter just
        // den filens steg.** `recept.last()` var den bokstavliga läsningen av
        // design v2 §4.4 (*"jämför resultatet mot `efter.niva2_sha256` efter
        // sista"*), men den kan inte förenas med facit §2 punkt 3, som kräver
        // `bas` **och** `efter` i **båda** receptfilerna: under `last()` vore
        // den första filens `efter` en dekoration motorn aldrig läste. Den
        // filvisa läsningen uppfyller båda — sista filens `efter` **är** hela
        // kedjans slutläge, så §4.4 står som specialfall — och den stänger
        // QA:s K1: en kedja där bara den första filen bär `efter` får inte
        // längre passera oprövad.
        let ar_sista = i + 1 == filer.len();
        if r.efter.is_some() || ar_sista {
            let h = klon.innehallshash();
            if let Some(vantad) = &r.efter {
                if !ar_full_hex64(vantad) {
                    return Utfall::hoppat_over(
                        karta,
                        filer.clone(),
                        format!("ogiltig efter-konstant i {fil}: {vantad} — kräver full 64-teckens hex"),
                        bas_hash,
                    );
                }
                if !vantad.eq_ignore_ascii_case(&h) {
                    return Utfall::hoppat_over(
                        karta,
                        filer.clone(),
                        format!("fel slutläge efter {fil}: graf {}, filen väntar {vantad}", kort(&h)),
                        bas_hash,
                    );
                }
            }
            if ar_sista {
                slut_hash = h;
            }
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

/// Är `v` en full grafkonstant — 64 hextecken, ingenting annat?
///
/// **K2 (QA-domen 2026-08-21).** Grinden var en prefixgrind
/// (`bas_hash.starts_with(vantad.trim_end_matches('…'))`), och QA visade att
/// **åtta hextecken räckte** för att passera facit §7 test 4. Full längd binds
/// nu i koden i stället för i papperet: en förkortad konstant är inte "nästan
/// rätt", den är ogiltig indata. Att avvisa den högljutt är samma fail-open
/// som resten av §4 — rå karta och en rad, aldrig ett tyst godkännande.
fn ar_full_hex64(v: &str) -> bool {
    v.len() == 64 && v.bytes().all(|b| b.is_ascii_hexdigit())
}

fn sokvag(kalla: &Kalla, fil: &str) -> String {
    match kalla {
        Kalla::Av | Kalla::Inbaddad => fil.to_string(),
        Kalla::Absolut(r) | Kalla::Gamedir(r) => format!("{r}/{fil}"),
    }
}

fn kort(h: &str) -> String {
    h.chars().take(8).collect::<String>() + "…"
}

/// Ska kartladdningen avbrytas i stallet for att kora ra karta?
///
/// Sant bara nar `rtx_recept_krav` ar satt OCH receptet faktiskt inte kunde
/// appliceras. Ett utfall utan skal ar "av" eller "ingen post for kartan" — inget
/// fel, och da avbryts ingenting oavsett kravflaggan. Aven i kravlaget: ingen panik,
/// bara en navmeshfri karta och samma loggrad.
pub fn ska_avbryta(u: &Utfall, krav: bool) -> bool {
    krav && u.utfall != UTFALL_APPLICERAT && u.skal.is_some()
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
        assert_eq!(resolvera("/opt/recept"), Kalla::Absolut("/opt/recept".into()));
        // Relativt tolkas som gamedir-relativt — ALDRIG som arbetskatalogsrelativt.
        assert_eq!(resolvera("reference/recept"), Kalla::Gamedir("reference/recept".into()));
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
        let m: std::collections::HashMap<String, Vec<u8>> =
            filer.iter().map(|(n, b)| (n.to_string(), b.clone())).collect();
        move |n: &str| m.get(n).cloned()
    }

    /// Ett steg med samma kropp som fixturrecepten nedan, så att facit och
    /// receptfil planterar exakt samma sak.
    fn steg(namn: &str) -> Steg {
        Steg {
            namn: namn.to_string(),
            from: [0.0; 3],
            takeoff: [0.0; 3],
            tgt: [0.0; 3],
            v_req: 400.0,
            gain: 6.0,
            remove: None,
        }
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
    ///
    /// Konstanten är **full längd** och fel — annars provas K2:s längdgren i
    /// stället för den återrullning testet är skrivet för.
    #[test]
    fn fel_slutlage_rullar_tillbaka_allt() {
        let mut g = tom();
        let fore = g.clone();
        let recept = format!(
            "{{\"efter\":{{\"niva2_sha256\":\"{}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}",
            "d".repeat(64)
        );
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.into_bytes()),
        ]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER);
        assert!(u.skal.as_ref().unwrap().contains("fel slutläge"), "{:?}", u.skal);
        assert_eq!(g, fore, "originalet ska vara orört");
    }

    /// **K2, bas-grenen.** QA visade 2026-08-21 att grinden var en prefixgrind
    /// och att **åtta hextecken räckte** för att passera facit §7 test 4. De
    /// åtta tecknen är här grafens EGNA första åtta — den enda förkortning som
    /// någonsin passerade — och de ska nu fällas på längd, inte på innehåll.
    #[test]
    fn bas_grinden_kraver_full_langd() {
        let mut g = tom();
        let bas = g.innehallshash();
        assert_eq!(bas.len(), 64, "attrappen ska efterlikna en riktig sha256");
        for forkortad in [&bas[..8], &bas[..63], ""] {
            let recept = format!(
                "{{\"bas\":{{\"niva2_sha256\":\"{forkortad}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
            );
            let las = las_fran(&[
                ("manifest.json", manifest_med(&["a.json"])),
                ("a.json", recept.into_bytes()),
            ]);
            let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
            assert_eq!(u.utfall, UTFALL_HOPPAT_OVER, "förkortad bas {forkortad:?} måste fällas");
            assert!(
                u.skal.as_ref().unwrap().contains("ogiltig bas-konstant"),
                "{:?}",
                u.skal
            );
            assert!(g.lankar.is_empty(), "grafen ska vara orörd");
        }
        // Positiv motpol: full längd och rätt värde passerar, annars provar
        // testet bara att allt fälls.
        let recept = format!(
            "{{\"bas\":{{\"niva2_sha256\":\"{bas}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
        );
        let las = las_fran(&[
            ("manifest.json", manifest_med(&["a.json"])),
            ("a.json", recept.into_bytes()),
        ]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_APPLICERAT, "{:?}", u.skal);
    }

    /// **K2, efter-grenen.** Samma grind på slutläget: en förkortning av det
    /// RÄTTA slutvärdet ska fällas, och stora bokstäver i det fullängdiga ska
    /// inte göra det.
    #[test]
    fn efter_grinden_kraver_full_langd() {
        let mut g = tom();
        let fore = g.clone();
        let mut facit = tom();
        plantera_ok(&mut facit, &steg("P1")).unwrap();
        let slut = facit.innehallshash();

        let kor = |g: &mut FakeGraf, varde: &str| {
            let recept = format!(
                "{{\"efter\":{{\"niva2_sha256\":\"{varde}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
            );
            let las = las_fran(&[
                ("manifest.json", manifest_med(&["a.json"])),
                ("a.json", recept.into_bytes()),
            ]);
            applicera("dm3", &Kalla::Absolut("/r".into()), las, g, plantera_ok)
        };

        let u = kor(&mut g, &slut[..8]);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER, "8 hextecken får inte räcka");
        assert!(
            u.skal.as_ref().unwrap().contains("ogiltig efter-konstant"),
            "{:?}",
            u.skal
        );
        assert_eq!(g, fore, "originalet ska vara orört");

        let u = kor(&mut g, &slut.to_uppercase());
        assert_eq!(u.utfall, UTFALL_APPLICERAT, "{:?}", u.skal);
    }

    /// **K1/L9.** Varje fils `efter` prövas efter just den filens steg.
    ///
    /// Provet har tre led, och det är det tredje som binder semantiken:
    /// 1. rätt mellanläge i fil 1 ⇒ kedjan går igenom,
    /// 2. **fel** mellanläge i fil 1 ⇒ HOPPAS ÖVER,
    /// 3. och då har fil 2:s steg **aldrig körts** — grinden fyrar mellan
    ///    filerna, inte efter hela kedjan. En implementation som bara läste
    ///    sista filens `efter` skulle släppa igenom led 2.
    #[test]
    fn efter_provas_per_fil_i_kedjan() {
        let mut facit = tom();
        plantera_ok(&mut facit, &steg("P1")).unwrap();
        let mellan = facit.innehallshash();
        plantera_ok(&mut facit, &steg("P2")).unwrap();
        let slut = facit.innehallshash();

        let bygg = |efter_a: &str| {
            let a = format!(
                "{{\"efter\":{{\"niva2_sha256\":\"{efter_a}\"}},\"P1\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
            );
            let b = "{\"P2\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}";
            las_fran(&[
                ("manifest.json", manifest_med(&["a.json", "b.json"])),
                ("a.json", a.into_bytes()),
                ("b.json", b.as_bytes().to_vec()),
            ])
        };

        let mut g = tom();
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), bygg(&mellan), &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_APPLICERAT, "{:?}", u.skal);
        assert_eq!(u.slut_hash, slut);

        // Fel mellanläge: kedjan ska brytas efter fil 1, och P2 aldrig planteras.
        let mut g = tom();
        let fore = g.clone();
        let sedda = std::cell::RefCell::new(Vec::new());
        let u = applicera(
            "dm3",
            &Kalla::Absolut("/r".into()),
            bygg(&slut),
            &mut g,
            |g: &mut FakeGraf, s: &Steg| {
                sedda.borrow_mut().push(s.namn.clone());
                plantera_ok(g, s)
            },
        );
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER, "{:?}", u.skal);
        let skal = u.skal.as_ref().unwrap();
        assert!(skal.contains("fel slutläge efter a.json"), "{skal}");
        assert_eq!(
            *sedda.borrow(),
            vec!["P1".to_string()],
            "fil 2 får inte ha körts när fil 1:s efter föll"
        );
        assert_eq!(g, fore, "originalet ska vara orört");
    }

    /// Motpolen till provet ovan: samma två filer, `efter` på den SISTA. Två
    /// led, och det andra är det som gör provet till en grind:
    ///
    /// 1. rätt slutläge ⇒ kedjan går igenom,
    /// 2. **mellanläget** (grafen efter bara fil 1) som `efter` ⇒ HOPPAS ÖVER.
    ///
    /// Led 2 är det som binder `recept.last()`: en implementation som prövade
    /// FÖRSTA filens `efter` skulle inte hitta något att pröva alls och släppa
    /// igenom mellanläget.
    #[test]
    fn efter_i_sista_filen_provas_over_hela_kedjan() {
        let mut facit = tom();
        plantera_ok(&mut facit, &steg("P1")).unwrap();
        let mellan = facit.innehallshash();
        plantera_ok(&mut facit, &steg("P2")).unwrap();
        let slut = facit.innehallshash();
        assert_ne!(mellan, slut, "fixturen måste skilja mellanläge från slutläge");

        let bygg = |efter: &str| {
            let a = "{\"P1\":{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}";
            let b = format!(
                "{{\"efter\":{{\"niva2_sha256\":\"{efter}\"}},\"P2\":{{\"frm\":[0,0,0],\"takeoff\":[1,0,0],\"tgt\":[2,0,0],\"v_req\":400.0,\"gain\":6.0}}}}"
            );
            las_fran(&[
                ("manifest.json", manifest_med(&["a.json", "b.json"])),
                ("a.json", a.as_bytes().to_vec()),
                ("b.json", b.into_bytes()),
            ])
        };

        let mut g = tom();
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), bygg(&slut), &mut g, plantera_ok);
        assert_eq!(u.utfall, UTFALL_APPLICERAT, "{:?}", u.skal);
        assert_eq!(u.lankar, 2);
        assert_eq!(u.slut_hash, slut, "sista filens efter är hela kedjans slutläge");

        let mut g = tom();
        let fore = g.clone();
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), bygg(&mellan), &mut g, plantera_ok);
        assert_eq!(
            u.utfall, UTFALL_HOPPAT_OVER,
            "mellanläget får inte godtas som slutläge: {:?}",
            u.skal
        );
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

    /// Facit §3.4: `rtx_recept_dir` ar tom och `rtx_recept_krav` av i tabellen —
    /// ett rent bygge utan cvarer beter sig exakt som forut.
    #[test]
    fn cvarerna_ar_av_som_default() {
        let mut sett = 0;
        for (namn, seed) in crate::cvars::RTX_CVAR_DEFAULTS {
            match (*namn, seed) {
                ("rtx_recept_dir", crate::cvars::CvarSeed::Str(v)) => {
                    assert_eq!(*v, "", "receptvagen far inte vara pa som default");
                    sett += 1;
                }
                ("rtx_recept_krav", crate::cvars::CvarSeed::Bool(v)) => {
                    assert!(!*v, "kravlaget far inte vara default");
                    sett += 1;
                }
                _ => {}
            }
        }
        assert_eq!(sett, 2, "bada cvarerna ska sta i tabellen");
    }

    /// Kravgrinden: avbryt bara nar receptet faktiskt failade OCH kravet ar satt.
    #[test]
    fn kravgrinden_avbryter_bara_vid_verkligt_fel() {
        let fel = Utfall::hoppat_over("dm3", vec![], "fel bas".into(), "x".into());
        let av = Utfall::inget("dm3");
        let ok = Utfall {
            utfall: UTFALL_APPLICERAT.to_string(),
            ..Default::default()
        };
        assert!(ska_avbryta(&fel, true), "fel + krav ska avbryta");
        assert!(!ska_avbryta(&fel, false), "utan krav kors ra karta");
        assert!(!ska_avbryta(&av, true), "av ar inget fel");
        assert!(!ska_avbryta(&ok, true), "lyckat recept avbryter aldrig");
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

    #[test]
    fn defaultgraf_tom_dir_dm3_ar_inbaddad() {
        assert_eq!(defaultgraf_kalla("", "dm3"), Some(Kalla::Inbaddad));
        assert_eq!(defaultgraf_kalla("   ", "dm3"), Some(Kalla::Inbaddad));
        assert_eq!(defaultgraf_kalla("", "dm6"), None);
        assert_eq!(
            defaultgraf_kalla("/s5/recept", "dm3"),
            Some(Kalla::Absolut("/s5/recept".into())),
            "satt dir: autostart, ingen bake"
        );
        assert_eq!(
            defaultgraf_kalla("reference/recept", "dm3"),
            Some(Kalla::Gamedir("reference/recept".into()))
        );
        assert_eq!(resolvera(""), Kalla::Av, "resolvera själv producerar inte Inbaddad");
    }

    #[test]
    fn inbaddad_las_aldrig_vf5_eller_katalogscan() {
        assert!(las_inbaddad("manifest.json").is_some());
        assert!(las_inbaddad("ra_climb_planted.json").is_some());
        assert!(las_inbaddad("vast_296_planted.json").is_some());
        assert!(las_inbaddad("vf5_ring2quad.json").is_none(), "vf5 får inte inbakas");
        assert!(las_inbaddad("vf5_ring2quad_forkmain.json").is_none());
        assert!(las_inbaddad(".").is_none());
        let filer = las_manifest(&las_inbaddad("manifest.json").unwrap(), "dm3").unwrap();
        assert_eq!(
            filer,
            vec!["ra_climb_planted.json".to_string(), "vast_296_planted.json".to_string()]
        );
        assert!(!filer.iter().any(|f| f.contains("vf5")));
        let vast = las_recept(&las_inbaddad("vast_296_planted.json").unwrap(), "vast_296_planted.json").unwrap();
        assert_eq!(vast.efter.as_deref(), Some(K2_BAKE_NIVA2));
        assert_eq!(vast.steg.len(), 1);
        let climb = las_recept(&las_inbaddad("ra_climb_planted.json").unwrap(), "ra_climb_planted.json").unwrap();
        assert_eq!(climb.steg.len(), 4);
    }

    fn kind_ur_token(t: &str) -> rtx_nav::navmesh::LinkKind {
        use rtx_nav::navmesh::LinkKind;
        match t {
            "walk" => LinkKind::Walk,
            "step" => LinkKind::Step,
            "drop" => LinkKind::Drop,
            "jump" => LinkKind::JumpGap,
            "doublejump" => LinkKind::DoubleJump,
            "speedjump" => LinkKind::SpeedJump,
            "plat" => LinkKind::Plat,
            "teleport" => LinkKind::Teleport,
            "hook" => LinkKind::Hook,
            "rocketjump" => LinkKind::RocketJump,
            "swim" => LinkKind::Swim,
            annat => panic!("okänd länksort i fixturen: {annat}"),
        }
    }

    /// Målträdets dm3-defaultgraf, 5977/48207, nivå-2 `58787ce0…`.
    fn dm3_fork_bas() -> rtx_nav::navmesh::NavGraph {
        use rtx_nav::navmesh::{Cell, Link, NavGraph};
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixturer/dm3-fork-bas.tsv"))
            .expect("dm3-fork-bas.tsv saknas");
        let mut celler = Vec::new();
        let mut i_adjacens = Vec::new();
        let mut prunade = Vec::new();
        for rad in text.lines() {
            let f: Vec<&str> = rad.split('\t').collect();
            match f[0] {
                "C" => celler.push(Cell {
                    origin: glam::Vec3::new(f[1].parse().unwrap(), f[2].parse().unwrap(), f[3].parse().unwrap()),
                    gx: 0,
                    gy: 0,
                }),
                "L" => {
                    let l = Link {
                        from: f[1].parse().unwrap(),
                        to: f[2].parse().unwrap(),
                        kind: kind_ur_token(f[3]),
                        cost: 0.0,
                    };
                    if f[4] == "1" {
                        i_adjacens.push(l);
                    } else {
                        prunade.push(l);
                    }
                }
                annat => panic!("okänd rad i fixturen: {annat}"),
            }
        }
        let mut g = NavGraph::test_graph(celler, i_adjacens);
        for l in prunade {
            g.links.push(l);
        }
        g.reindex_grid();
        g
    }

    const BAS_NIVA2: &str = "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9";
    const EFTER_NIVA2: &str = "dcb487f79abdd4157eda0637d617ea8ddd17764e56ad68e3f49e53e5f21dd87a";

    #[test]
    fn k2_bake_identitet_feeea6b4_och_mutation_andrar_hash() {
        use crate::graph_ident::graph_content_hash;
        let mut g = dm3_fork_bas();
        assert_eq!(g.cells.len(), 5977, "fixturens cellantal");
        assert_eq!(g.links.len(), 48207, "fixturens länkantal inkl. prunade");
        assert_eq!(graph_content_hash(&g), BAS_NIVA2, "basgrafen ska vara 58787ce0");

        let plantera = |g: &mut rtx_nav::navmesh::NavGraph, s: &Steg| {
            crate::control::plant_speed_jump_link(
                g,
                glam::Vec3::from(s.from),
                glam::Vec3::from(s.takeoff),
                glam::Vec3::from(s.tgt),
                s.v_req,
                s.gain,
                800.0,
            )
            .map(|p| p.link)
        };
        let u = applicera("dm3", &Kalla::Inbaddad, las_inbaddad, &mut g, plantera);
        assert_eq!(
            u.utfall, UTFALL_APPLICERAT,
            "bake ska gå samma applicera-väg: {:?}",
            u.skal
        );
        assert_eq!(u.lankar, 5);
        assert_eq!(g.cells.len(), 5977);
        assert_eq!(g.links.len(), 48212);
        assert_eq!(
            graph_content_hash(&g),
            K2_BAKE_NIVA2,
            "bakad graf är identitet feeea6b4, inte 99 %"
        );
        assert_eq!(u.slut_hash, K2_BAKE_NIVA2);

        let fore = graph_content_hash(&g);
        g.links.last_mut().expect("planterad länk").to += 1;
        let efter = graph_content_hash(&g);
        assert_ne!(efter, fore, "mutation av inbakad länk måste ändra hashen");
        assert_ne!(efter, K2_BAKE_NIVA2, "muterad graf får inte längre vara feeea6b4");
    }

    #[test]
    fn las_recept_accepterar_removelinks_pa_namngiven_fil() {
        let bytes = br#"{
            "steg":[{"op":"RemoveLinks","namn":"grop","lankar":[
                {"id":1,"from":10,"to":11,"kind":"walk"}
            ]}]
        }"#;
        let r = las_recept(bytes, "n.json").unwrap();
        assert_eq!(r.steg.len(), 1);
        let s = &r.steg[0];
        assert_eq!(s.namn, "grop");
        let ls = s.remove.as_ref().expect("RemoveLinks");
        assert_eq!(ls.len(), 1);
        assert_eq!(ls[0].id, 1);
        assert_eq!(ls[0].from, 10);
        assert_eq!(ls[0].to, 11);
        assert_eq!(ls[0].kind, "walk");
    }

    #[test]
    fn las_recept_inbaddad_vagrar_removelinks() {
        let bytes = br#"{
            "steg":[{"op":"RemoveLinks","namn":"x","lankar":[
                {"id":0,"from":0,"to":1,"kind":"walk"}
            ]}]
        }"#;
        let e = las_recept_ex(bytes, "vf5.json", false).unwrap_err();
        assert!(e.contains("inbäddad"), "{e}");
    }

    #[test]
    fn las_recept_vagrar_tom_removelinks_och_okand_op() {
        let tom = br#"{"steg":[{"op":"RemoveLinks","namn":"x","lankar":[]}]}"#;
        let e = las_recept(tom, "n.json").unwrap_err();
        assert!(e.contains("utan länkar"), "{e}");
        let okand = br#"{"steg":[{"op":"ShelfPatch","namn":"x"}]}"#;
        let e = las_recept(okand, "n.json").unwrap_err();
        assert!(e.contains("stöds inte"), "{e}");
    }

    #[test]
    fn namngiven_removelinks_applicerar_ratt_idn_failclosed_okanda() {
        use rtx_nav::navmesh::{Link, LinkKind, NavGraph};
        let bytes_ok = br#"{
            "steg":[{"op":"RemoveLinks","namn":"x","lankar":[
                {"id":0,"from":0,"to":1,"kind":"walk"}
            ]}]
        }"#;
        let bytes_bad = br#"{
            "steg":[{"op":"RemoveLinks","namn":"x","lankar":[
                {"id":0,"from":9,"to":1,"kind":"walk"}
            ]}]
        }"#;
        let manifest = manifest_med(&["n.json"]);
        let plantera = |g: &mut NavGraph, s: &Steg| -> Result<u32, String> {
            match &s.remove {
                Some(ls) => crate::control::remove_links_anchored(g, ls),
                None => Err("förväntade RemoveLinks".into()),
            }
        };
        let mut g = NavGraph::from_topology(
            &[glam::Vec3::ZERO, glam::Vec3::new(32.0, 0.0, 0.0)],
            &[Link {
                from: 0,
                to: 1,
                kind: LinkKind::Walk,
                cost: 1.0,
            }],
        );
        g.rebuild_derived();
        let las = las_fran(&[("manifest.json", manifest.clone()), ("n.json", bytes_ok.to_vec())]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g, plantera);
        assert_eq!(u.utfall, UTFALL_APPLICERAT, "{:?}", u.skal);
        assert!(g.links.is_empty());

        let mut g2 = NavGraph::from_topology(
            &[glam::Vec3::ZERO, glam::Vec3::new(32.0, 0.0, 0.0)],
            &[Link {
                from: 0,
                to: 1,
                kind: LinkKind::Walk,
                cost: 1.0,
            }],
        );
        g2.rebuild_derived();
        let fore = g2.links.len();
        let las = las_fran(&[("manifest.json", manifest), ("n.json", bytes_bad.to_vec())]);
        let u = applicera("dm3", &Kalla::Absolut("/r".into()), las, &mut g2, plantera);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER, "{:?}", u);
        assert!(u.skal.as_deref().unwrap().contains("ankaret håller inte"));
        assert_eq!(g2.links.len(), fore, "fail-closed: okänt ankare muterar inget");
    }

    #[test]
    fn inbaddad_applicera_hoppar_over_removelinks() {
        use rtx_nav::navmesh::{Link, LinkKind, NavGraph};
        let bytes = br#"{
            "steg":[{"op":"RemoveLinks","namn":"x","lankar":[
                {"id":0,"from":0,"to":1,"kind":"walk"}
            ]}]
        }"#;
        let manifest = br#"{"kartor":{"dm3":[{"fil":"x.json","ordning":1}]}}"#;
        let las = las_fran(&[("manifest.json", manifest.to_vec()), ("x.json", bytes.to_vec())]);
        let mut g = NavGraph::from_topology(
            &[glam::Vec3::ZERO, glam::Vec3::new(32.0, 0.0, 0.0)],
            &[Link {
                from: 0,
                to: 1,
                kind: LinkKind::Walk,
                cost: 1.0,
            }],
        );
        let plantera = |_: &mut NavGraph, _: &Steg| -> Result<u32, String> { Ok(0) };
        let u = applicera("dm3", &Kalla::Inbaddad, las, &mut g, plantera);
        assert_eq!(u.utfall, UTFALL_HOPPAT_OVER, "{:?}", u);
        assert!(
            u.skal.as_deref().unwrap().contains("inbäddad"),
            "{:?}",
            u.skal
        );
        assert_eq!(g.links.len(), 1, "inbäddad RemoveLinks får inte mutera");
    }

    /// RA-rummets strömbrytare. Prefix `RA_ROOM_LOCK` så en ring2quad-PR som
    /// släcker F1-default, bake eller släpper in vf5 syns i `cargo test`.
    mod ra_room_kontrakt {
        use super::*;
        use crate::cvars::{default_of, CvarSeed};
        use crate::graph_ident::graph_content_hash;

        #[test]
        fn edge_narrow_true() {
            assert_eq!(
                default_of("rtx_bot_edge_narrow"),
                Some(CvarSeed::Bool(true)),
                "RA_ROOM_LOCK: edge_narrow≠true"
            );
        }

        #[test]
        fn walkplan_true() {
            assert_eq!(
                default_of("rtx_bot_walkplan"),
                Some(CvarSeed::Bool(true)),
                "RA_ROOM_LOCK: walkplan≠true"
            );
        }

        #[test]
        fn walkdiag_false() {
            assert_eq!(
                default_of("rtx_bot_walkdiag"),
                Some(CvarSeed::Bool(false)),
                "RA_ROOM_LOCK: walkdiag≠false"
            );
        }

        #[test]
        fn bot_count_noll() {
            assert_eq!(
                default_of("rtx_bot_count"),
                Some(CvarSeed::Float(0.0)),
                "RA_ROOM_LOCK: bot_count≠0"
            );
        }

        #[test]
        fn tom_dir_dm3_inbaddad() {
            assert_eq!(
                default_of("rtx_recept_dir"),
                Some(CvarSeed::Str("")),
                "RA_ROOM_LOCK: rtx_recept_dir≠tom"
            );
            assert_eq!(
                defaultgraf_kalla("", "dm3"),
                Some(Kalla::Inbaddad),
                "RA_ROOM_LOCK: tom dir+dm3 inte Inbaddad"
            );
            assert_eq!(
                defaultgraf_kalla("   ", "dm3"),
                Some(Kalla::Inbaddad),
                "RA_ROOM_LOCK: tom dir+dm3 inte Inbaddad"
            );
        }

        #[test]
        fn vf5_inte_inbaddad() {
            for namn in ["vf5_ring2quad.json", "vf5_ring2quad_forkmain.json", "vf5_anything.json"] {
                assert!(
                    las_inbaddad(namn).is_none(),
                    "RA_ROOM_LOCK: vf5 läses inbäddat ({namn})"
                );
            }
        }

        #[test]
        fn k2_bake_hash_feeea6b4() {
            let mut g = dm3_fork_bas();
            let plantera = |g: &mut rtx_nav::navmesh::NavGraph, s: &Steg| {
                crate::control::plant_speed_jump_link(
                    g,
                    glam::Vec3::from(s.from),
                    glam::Vec3::from(s.takeoff),
                    glam::Vec3::from(s.tgt),
                    s.v_req,
                    s.gain,
                    800.0,
                )
                .map(|p| p.link)
            };
            let u = applicera("dm3", &Kalla::Inbaddad, las_inbaddad, &mut g, plantera);
            assert_eq!(
                graph_content_hash(&g),
                K2_BAKE_NIVA2,
                "RA_ROOM_LOCK: K2-bake-hash ≠ feeea6b4…"
            );
            assert_eq!(u.slut_hash, K2_BAKE_NIVA2, "RA_ROOM_LOCK: K2-bake-hash ≠ feeea6b4…");
            assert_eq!(g.cells.len(), 5977, "RA_ROOM_LOCK: K2-bake-hash ≠ feeea6b4… (celler)");
            assert_eq!(g.links.len(), 48212, "RA_ROOM_LOCK: K2-bake-hash ≠ feeea6b4… (länkar)");
        }
    }

    /// K7 — hela ring2quad-graftransformen reproducerad OFFLINE mot fixturen,
    /// geometriankrad.
    ///
    /// Receptet deklarerar sin egen metod: «Ankaret ar GEOMETRIN, inte id:na».
    /// Det ar ingen stilfraga har. Fixturens LANK-index ar **permuterade** mot
    /// receptets deklarerade id: 34501, 34503, 35683, 35761, 35762, 35592,
    /// 35738 later i fixturen pa 13967, 14061, 13879, 10224, 10298, 13876,
    /// 11944. En id-baserad applicering hade tagit fel lankar — och fallts av
    /// ankargrinden, inte tyst lyckats. Nivan-2-hashen ar permutationsokanslig
    /// (`canonical_inventory` sorterar), sa hash-assertionen star anda.
    ///
    /// Ordningen ar last: de sju loses upp FORE planteringen. Den planterade
    /// lanken far samma fran/mal-koordinater som spec 35738
    /// («originalavfarten»), sa en upplosning efterat hade gett tva traffar pa
    /// den specen och en tvetydig borttagning.
    #[test]
    fn ring2quad_offline_repro_geometriankrad() {
        use crate::graph_ident::graph_content_hash;

        let mut g = dm3_fork_bas();
        assert_eq!(g.cells.len(), 5977, "RING2QUAD_LOCK: fixturens cellantal");
        assert_eq!(g.links.len(), 48207, "RING2QUAD_LOCK: fixturens lankantal inkl. prunade");
        assert_eq!(graph_content_hash(&g), BAS_NIVA2, "RING2QUAD_LOCK: basgrafen ar 58787ce0");

        let text = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../reference/recept/vf5_ring2quad_forkmain.json"
        ))
        .expect("RING2QUAD_LOCK: vf5_ring2quad_forkmain.json saknas");
        let r = las_recept(text.as_bytes(), "vf5_ring2quad_forkmain.json")
            .expect("RING2QUAD_LOCK: receptet ska ga att lasa");
        assert_eq!(r.steg.len(), 2, "RING2QUAD_LOCK: receptet har tva steg");
        assert_eq!(r.bas.as_deref(), Some(BAS_NIVA2), "RING2QUAD_LOCK: receptets bas ar 58787ce0");

        let plan = r.steg.iter().find(|s| s.remove.is_none())
            .expect("RING2QUAD_LOCK: receptet har ett PlanLink-steg");
        let specar = r.steg.iter().find_map(|s| s.remove.as_ref())
            .expect("RING2QUAD_LOCK: receptet har ett RemoveLinks-steg");
        assert_eq!(specar.len(), 7, "RING2QUAD_LOCK: exakt sju lankar tas bort");

        // Geometrin star i radokumentet: `fran_pos` / `mal_pos`.
        let raw: serde_json::Value =
            serde_json::from_str(&text).expect("RING2QUAD_LOCK: receptet ar giltig JSON");
        let rm_raw = raw["steg"].as_array().expect("RING2QUAD_LOCK: steg ar en lista")
            .iter().find(|s| s["op"] == "RemoveLinks")
            .expect("RING2QUAD_LOCK: RemoveLinks-steget finns i radokumentet");
        let geom = rm_raw["lankar"].as_array()
            .expect("RING2QUAD_LOCK: RemoveLinks listar lankar");
        assert_eq!(geom.len(), specar.len(), "RING2QUAD_LOCK: lika manga specar i bada vyerna");

        let vek = |v: &serde_json::Value| -> glam::Vec3 {
            let a = v.as_array().expect("RING2QUAD_LOCK: position ar en lista");
            glam::Vec3::new(
                a[0].as_f64().unwrap() as f32,
                a[1].as_f64().unwrap() as f32,
                a[2].as_f64().unwrap() as f32,
            )
        };

        let mut ids: Vec<u32> = Vec::with_capacity(7);
        for (spec, rad) in specar.iter().zip(geom.iter()) {
            let fran = vek(&rad["fran_pos"]);
            let mal = vek(&rad["mal_pos"]);
            let kind = kind_ur_token(&spec.kind);
            let traffar: Vec<u32> = g
                .links
                .iter()
                .enumerate()
                .filter(|(_, l)| {
                    l.kind == kind
                        && g.cells[l.from as usize].origin == fran
                        && g.cells[l.to as usize].origin == mal
                })
                .map(|(i, _)| i as u32)
                .collect();
            assert_eq!(
                traffar.len(), 1,
                "RING2QUAD_LOCK: geometrin {fran:?} -> {mal:?} ({kind:?}) ska traffa exakt en lank"
            );
            let id = traffar[0];
            // Cellankaret ur den PARSADE specen maste peka pa samma lank.
            // Cell-id ar stabila mellan fixtur och motor; det ar LANK-id som
            // ar permuterade (A2).
            let l = g.links[id as usize];
            assert_eq!(
                (l.from, l.to), (spec.from, spec.to),
                "RING2QUAD_LOCK: geometrin och receptets cellankare ska peka pa samma lank"
            );
            ids.push(id);
        }
        assert_eq!(
            ids,
            vec![13967, 14061, 13879, 10224, 10298, 13876, 11944],
            "RING2QUAD_LOCK: geometrin loser upp fixturens EGNA lank-index (A2)"
        );
        let deklarerade: Vec<u32> = specar.iter().map(|s| s.id).collect();
        assert_eq!(
            deklarerade,
            vec![34501, 34503, 35683, 35761, 35762, 35592, 35738],
            "RING2QUAD_LOCK: receptets deklarerade lank-id"
        );
        assert_ne!(
            ids, deklarerade,
            "RING2QUAD_LOCK: A2 — fixturens lank-id ar permuterade mot receptets; \
             gar de nagon gang ihop ar fixturen utbytt och K7 provar inte langre \
             det den pastar sig prova"
        );

        // PlanLink: samma vag som `applicera` anvander.
        crate::control::plant_speed_jump_link(
            &mut g,
            glam::Vec3::from(plan.from),
            glam::Vec3::from(plan.takeoff),
            glam::Vec3::from(plan.tgt),
            plan.v_req,
            plan.gain,
            800.0,
        )
        .expect("RING2QUAD_LOCK: planteringen ska lyckas");
        assert_eq!(g.links.len(), 48208, "RING2QUAD_LOCK: 48207 + 1 planterad");

        // RemoveLinks: aterupplivar prunade lankar och remappar sidotabeller.
        let borttagna = g
            .remove_links_by_id(&ids)
            .expect("RING2QUAD_LOCK: borttagningen ska lyckas");
        assert_eq!(borttagna.len(), 7, "RING2QUAD_LOCK: sju lankar borttagna");

        assert_eq!(g.cells.len(), 5977, "RING2QUAD_LOCK: cellantalet oforandrat");
        assert_eq!(
            g.links.len(), 48201,
            "RING2QUAD_LOCK: 48207 + 1 planterad - 7 borttagna = 48201"
        );
        assert_eq!(
            graph_content_hash(&g), EFTER_NIVA2,
            "RING2QUAD_LOCK: slutidentiteten ar dcb487f7"
        );
        assert_eq!(r.efter.as_deref(), Some(EFTER_NIVA2), "RING2QUAD_LOCK: receptets efter-hash");
    }
}
