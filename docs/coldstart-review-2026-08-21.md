# Kallstartsgranskning av `git@github.com:Xerialen/rtx.git`

> **Status 2026-08-21:** P0-1–P0-5 åtgärdade, commits `a8e4f8d`/`e924d82`/`d93c8e7`/`2cea442`
> + taggarna `arkiv/91a6e34`, `arkiv/86f7f11`; `c8a20fb` bokförd förlorad.

**Datum:** 2026-08-21
**Granskare:** fristående agent utan förkunskaper om projektet
**Granskningsobjekt:** enbart en färsk klon, `/tmp/coldstart-rtx` på lanister
(`git clone` 2026-08-21 16:53, HEAD = `bd838cc`, 338 filer, 22 MB, 23 fjärrgrenar, **0 taggar**)
**Metod:** läsning av klonen + praktiska prov (bygge, tester, selftester, torrkörningar)
redovisade med kommando och rå utdata nedan.

---

## Huvuddom

### RÄCKER INTE

Repot räcker för att **bygga och köra spelmodulen** — det är verifierat, och den delen är
ovanligt välskött. Det räcker **inte** för att ta över *utvecklingen mot projektets mål*, av
fyra skäl som var för sig är blockerande:

1. **Målen står ingenstans.** README beskriver vad produkten *är*, aldrig vad som ska uppnås
   härnäst eller vad "klart" betyder. Ingen roadmap, ingen status, ingen öppen punktlista.
2. **Mätkanonen finns inte i någon gren.** Tre av rollfilerna utpekar
   `reference/ra-room/README.md` som "ENDA giltiga mätreferensen". Den filen existerar inte i
   `origin/main` och inte i något objekt i historiken.
3. **Det levande arbetet ligger på okartlagda grenar.** Sista *kodcommit* på main är
   2026-08-01. `origin/ring2quad` ligger **206 commits före main** (580 filer, +70 431 rader)
   och `origin/lagbench-p3` 181 före, båda daterade 15–21 augusti. Inget dokument i repot säger
   vilken gren som är levande, dömd eller övergiven. En ny agent börjar per automatik på main
   och saknar då hela mätverktygslådan.
4. **Rollfilerna i `.claude/agents/` beordrar läsning av filer som inte finns.** De är inte bara
   ofullständiga — de är aktivt vilseledande.

Nyansen är viktig: det här är inte ett dåligt repo. Det är ett *utmärkt produktrepo* som saknar
allt som gör det till ett *projektrepo*. Grinden mellan de två är där kallstarten dör.

---

## 1. Vad ÄR projektet, och var står målen?

**Vad det är: entydigt och lätt hittat.** `README.md` svarar på första raden — en QuakeWorld-
spelmodul i native Rust (`cdylib`, pr2 GAME_API_VERSION 16), med UT-rörelse ovanpå och navmesh-
botar. Dokumenttabellen (README rad 62–72) leder till sju ämnesdokument i `docs/`, alla finns,
och **inga trasiga interna markdown-länkar** (kontrollerat maskinellt över alla .md-filer).
`docs/bot-architecture.md` är 49 kB och förklarar hjärnan på riktigt. Det här är bättre än 95 %
av vad som brukar möta en ny agent.

**Var målen står: ingenstans.** Sökning över alla .md efter `roadmap|open work|TODO|milestone|
the goal|mål` ger fem träffar, varav fyra är ordet "goal" i en annan betydelse (goal cell,
goal selection) och en är `docs/development.md:217` — "Adding an equivalent set for the
speed-jump envelope … is open work". Det är projektets enda skrivna öppna punkt.

Det närmaste ett måldokument är `docs/baseline/README.md`, som i praktiken *är* en målformulering
(bot vs människa: p50 1,25× tid, 0,87× fart, 89 % framkomst, "the open lead" i rad 91–94). Men
det står som en historisk mätrapport, inte som ett mål, och den kan inte reproduceras — se §3.

Rollfilerna talar om "RA-99 %-spåret", "K2-baslinjen", "kanonens 70 u-toppdisk" som om de vore
kända storheter. För en ny agent är de innehållslösa strängar.

> **Belägg:** `grep -rIn "roadmap\|open work\|TODO\|milestone\|the goal" --include=*.md .`
> → 5 träffar, ingen är en målformulering. `reference/recept/README.md:60` nämner
> "kanonens 70 u-toppdisk" utan att kanonen finns i repot.

---

## 2. Bygga och köra — **detta fungerar, verifierat**

Enda området där domen är ren. Kommandon körda i den färska klonen:

```
$ cargo build --locked
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 8.54s
real	0m8.560s
```

```
$ cargo test --locked --workspace
test result: ok. 470 passed; 0 failed; 0 ignored ...   (rtx-game)
test result: ok. 121 passed; 0 failed; 0 ignored ...
test result: ok. 119 passed; 0 failed; 0 ignored ...
... 20 testbinärer, 0 failed, exit 0
```

`README.md:44` ("Quick start") och `docs/development.md:29` ("Building") går att följa ordagrant.
`Cargo.lock` är incheckad, `--locked` fungerar, CI (`.github/workflows/build.yml`) bygger samma
sak för tre plattformar. `crates/*/` matchar dokumentets krattabell exakt.

**Men "köra" slutar vid biblioteksfilen.** README rad 47: `cp target/release/librtx.so
/path/to/server/qw/qwprogs.so`. Vilken server? `AGENTS.md:3–21` beskriver `playground/` —
gitignorerad, ej incheckad, kräver `mvdsv`-binär plus `id1/pak0.pak` och `id1/PAK1.PAK`.
Ingen anskaffningsväg, inget skript, ingen länk, inga kartor. Utan den katalogen finns:
ingen server, ingen MCP (`.mcp.json` startar `rtx-mcp` som i sin tur kräver playground),
ingen T1, ingen T2, ingen navmesh att titta på i `rtx-nav-view`.

Två småsaker: `cargo` ligger inte i PATH för icke-interaktiv ssh på riggen (miljöfråga, inte
repots fel), och det finns **ingen `rust-toolchain.toml`** — CI använder `dtolnay/rust-toolchain@stable`,
så bygget driver med tiden.

---

## 3. Mäta och döma — **det avgörande hålet**

### 3a. Det som faktiskt finns och fungerar

Testsviten under `testsuite/` är portabel, ren standardbiblioteks-Python och **negativkontrollerad
på riktigt**. Tre selftester körda i den färska klonen:

```
$ python3 testflow.py selftest
selftest PASS: 15 valid fixture(s) accepted; 35 broken fixture(s) rejected      (exit 0)

$ python3 dashboard/build_dashboard.py --selftest
dashboard selftest: PASS                                                        (exit 0)

$ python3 tools/powerup_watch.py --selftest
powerup-watch selftest: PASS                                                    (exit 0)

$ python3 dashboard/build_dashboard.py --evidence-dir dashboard/fixtures --output /tmp/x.html
built /tmp/rtx-dash-fixture.html from 18 evidence files in 10 build groups (1 warnings)
```

35 avsiktligt trasiga fixtures under `testsuite/schema/fixtures/broken/` som alla *avvisas* —
det är exakt den bevisföring som saknas i de flesta projekt. `testsuite/docs/RUNBOOK.md` (482
rader) och `testsuite/schema/SCHEMA.md` (418 rader) är seriösa dokument.

### 3b. Det som är dött vid kallstart

**Kanonen finns inte.** `.claude/agents/hopparen.md:15`, `navmeshdoktor.md:23` och
`fable-orkestratorn.md:32` pekar alla på `reference/ra-room/README.md` som enda giltiga
mätreferens. Kontroll:

```
$ ls reference/ra-room  →  SAKNAS
$ git log --all --oneline -- reference/ra-room  →  (tomt)
```

Den finns alltså inte i någon gren, i ingen commit, i hela historiken.

**Demokorpusen — grunden under både baslinjen och fysikkonstanterna — saknas.**
`docs/baseline/README.md:4` mäter mot `demos/20260507-2107_4on4_]sr[_vs_book[dm3].mvd`.
`/demos` är gitignorerad (`.gitignore:6`). Värre: **motorkonstanter** är kalibrerade mot demos
som inte följer med:

- `crates/rtx-nav/src/navmesh/physics.rs:72` — "Ground truth for both: `demos/dm3_rastairs.qwd`, `demos/dm3_rlstrafejump.qwd`"
- `crates/rtx-nav/src/navmesh/physics.rs:91` — "Calibrated against `demos/dm3_rastairs.qwd`, not intuition"
- `crates/rtx-nav/src/strafe.rs:159`, `crates/rtx-nav/src/navmesh/mod.rs:2968` — samma

En ny agent kan alltså inte pröva om en konstantändring är rätt eller fel.

**De pinnade commit-SHA:na finns inte.** Varje mätrapport i repot pinnar sin mätning mot en
commit. Kontroll med `git cat-file -t`:

| SHA | var det står | finns i repot? |
|---|---|---|
| `c8a20fb` | `docs/baseline/README.md:3` ("Taken on c8a20fb") | **NEJ** |
| `91a6e34` | `reference/recept/vf5_ring2quad.json:50` ("kanonen 91a6e34") | **NEJ** |
| `86f7f11` | `reference/recept/README.md:51` (K2-baslinjen) | **NEJ** |
| `1cc87180615f` | `reference/recept/README.md:61` (arm A:s binär) | **NEJ** |
| `4f0b910` | `reference/recept/README.md:121` (vF5:s basgraf) | ja |
| `cc5fa8e` | `reference/recept/README.md:42` | ja |

Tre av sex mätreferenser pekar på commits som inte finns — de låg på lokala grenar som aldrig
pushades. Baslinjen i `docs/baseline/` går därmed inte att reproducera *ens med demofilerna*.

**Recept-verktyget kör inte utanför ägarens maskin.** `reference/recept/applicera_recept.py:127`:

```python
sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab
```

Importen ligger *före* `--torrkor`-grenen, så även torrkörningen dör. Prövat: på lanister
lyckas den (katalogen finns där), på en maskin utan den:

```
$ python3 ... --torrkor   # med labctl maskerat
  File "applicera_recept.py", line 128, in mot_rigg
    from labctl import Lab
ModuleNotFoundError: No module named labctl
```

`/home/xerial/rtx-tools` är en helt egen verktygslåda (HANDOVER.md, NAV_DIAGNOSTIC_TOOLCHAIN.md,
labctl.py, ctl.py, cellcheck.py, descent.py …) som inte följer med repot.
`--verifiera-offline` kräver dessutom `dm3-full-graph.json` som inte heller finns.

**T3/T4 är rena infrastrukturberoenden.** `RUNBOOK.md §8` beskriver i prosa hur en dedikerad
mvdsv+KTX-instans ska riggas — privat gamedir, `k_noframechecks 1`, `k_lockmode 0`,
`k_count 45`, återställningskedjan efter varje match. Inget av det är skriptat i repot. T4
kräver dessutom KTX:s `bots/`-datakatalog (finns inte). `[tools].qw_analyze` pekar på en
`qw-analyze`-binär (finns inte).

**Publiceringskedjan finns inte.** `RUNBOOK.md:412`:
`SUITE_DIR="$SUITE" /path/to/dashboard-gate/publish.sh` — publiceraren, Cloudflare-workern,
OAuth-grinden och demo-spelaren som alla evidenslänkar pekar på ligger utanför repot. Steg 12–13
i runbooken är oexekverbara.

### 3c. Grinden som ljuger grönt

`cargo test` gav `0 failed` — men **23 tester avbryter tyst och räknas som godkända** när deras
miljövariabel saknas:

```
$ grep -rIn "env::var(\"RTX_TEST" --include=*.rs crates | wc -l
23
```

Mönstret (t.ex. `crates/rtx-nav/src/bsp.rs:914`, `crates/rtx-nav/src/navmesh/mod.rs:2365`):

```rust
let Ok(path) = std::env::var("RTX_TEST_BSP") else {
    eprintln!("RTX_TEST_BSP unset - skipping");
    return;                      // ← räknas som PASS
};
```

CI sätter aldrig `RTX_TEST_BSP`, `RTX_TEST_MAPS`, `RTX_TEST_DEMOS`, `RTX_TEST_BASEDIR`,
`RTX_TEST_WAYPOINTS` eller `RTX_TEST_QW_CAPTURE` — de kartor och demos de behöver finns inte i
repot. Grönt T0 täcker alltså **inte** navmesh-byggaren mot en riktig BSP, vilket är precis den
kod projektet handlar om. Endast en av de sex variablerna är dokumenterad
(`docs/development.md:224`).

CI kör inte heller `cargo test -p rtx-mcp` — trots att `RUNBOOK.md:150` kräver
`rtx_mcp.tests > 0` — och inte `cargo fmt --check`, trots regeln i `AGENTS.md:197`.

### 3d. Stale konstanter i incheckad mätdata

`testsuite/dashboard/assets/maps/dm3/PROVENANCE.md` säger att `graph.json` är en dump av
"meganav navmesh (4635 cells)" från 2026-07-26. `reference/recept/README.md:42` säger att main
(`cc5fa8e`) bygger **5977 celler / 48207 länkar** för dm3. Ordet "meganav" förekommer i övrigt
bara i två .md-filer och i noll rader Rust på main. Dashboardens kartgeometri beskriver alltså
en graf som nuvarande kod inte bygger, och cell-id:n är enligt filens egen varning
grafversionsspecifika.

---

## 4. Roller och processer — **aktivt vilseledande**

`CLAUDE.md` innehåller en rad: `@AGENTS.md`. `AGENTS.md` (213 rader) är utmärkt och nästan helt
självbärande — verktygsbeskrivningar, kända korridorer med koordinater, hur man läser demos, hur
man dömer en strategiändring. Den delen håller.

`.claude/agents/` däremot (7 rollfiler, tillagda 2026-08-21, de två senaste commitsen på main)
är oanvändbara. Varenda utpekad kanonisk fil saknas:

| rollfil | pekar på | finns? |
|---|---|---|
| `navmeshdoktor.md:10–13` | `navmesh-doctor/NAVMESHDOCTOR.md`, `NAVMESHDIAGNOSTICS.md`, `TOOLMANIFEST.md`, `runbooks/` | **NEJ** (ingen gren) |
| `navmeshdoktor.md:23`, `hopparen.md:15`, `fable-orkestratorn.md:32` | `reference/ra-room/README.md` | **NEJ** |
| `hopparen.md:13`, `kodaren.md:28`, `fable-orkestratorn.md:32` | `GOTCHAS.md` | **NEJ** |
| `kodaren.md:28`, `fable-orkestratorn.md:32` | `PLANS/RA_STATUS.md` | **NEJ** |
| `hopparen.md:17`, `kodaren.md:28`, `qa-domaren.md:20` | `WORK_LOGS/` | **NEJ** |
| `demobyggaren.md:15` | `GUIDES/VERKTYGSGRINDAR.md` | **NEJ** |

`navmeshdoktor.md` säger i versaler: "DIN IDENTITET OCH MANUAL ÄR KANONISKT DOKUMENTERAD I
REPOT — LÄS FÖRST". Filen finns inte. Rollen är därmed odefinierad i sak; skalet säger bara
vem den rapporterar till.

Utöver filerna förutsätter rollerna en organisation som inte följer med: **Sols kontrasignatur**,
**grok-validering**, **ägaren**, riggen `lanister`, rigglåset `~/lab/.rig-lock`, förbjudna portar
(27550, 27991, 27530, 27700, 28502, 28503), `herdr wN:pB`-panelen med en lokal Qwen3.8-27B, och
två dashboards (Discord/workers.dev respektive "ägarens 📺-tavla") som ingendera finns i repot.
`fable-orkestratorn.md:19` kallar domkedjan "helig" — förseglat facit (0444 + sha-sidofil) → QA →
Sol → mätning → grok → ägarrapport. Fyra av sex led är personer eller system som är borta.

Två av rollfilerna deklarerar `model: fable`, vilket inte är ett standardvärde i Claude Codes
agentschema (`sonnet`/`opus`/`haiku`/`inherit`). `qwen-forensikern.md` saknar `model:` helt och
inleder med en varning om att den *inte* får startas som Claude-subagent — vilket den ändå kommer
att kunna startas som, eftersom den ligger i `.claude/agents/`.

---

## 5. Pågående arbete — **omöjligt att avgöra vad som lever**

23 fjärrgrenar, **noll taggar**, noll dokument som beskriver dem. Mätt läge:

| gren | senast | före main | efter main |
|---|---|---|---|
| `ring2quad` | 2026-08-20 | **206** | 3 |
| `lagbench-p3` | 2026-08-20 | **181** | 3 |
| `toolbox/d-drift` | 2026-08-17 | 109 | 3 |
| `jumps-on-main-pr` | 2026-07-25 | 108 | 141 |
| `pr6-all-jumps` | 2026-07-25 | 106 | 143 |
| `merge-trial` | 2026-07-23 | 77 | 183 |
| `ra-tunnel-on-main` | 2026-07-21 | 41 | 183 |
| `focus-controller` | 2026-07-22 | 32 | 259 |
| `toolbox/b-planner-telemetry` | 2026-08-16 | 16 | 3 |
| `bsp-probe` | 2026-07-22 | 12 | 259 |
| `dm3-westshelf-navpatch`, `toolbox/d-navpatch-rebase` | 2026-08-03 | 7 | 3 |
| `receptautostart` | 2026-08-21 | 3 | 2 |
| `mallinjefix`, `lagbench-p3-bench`, `toolbox/dashboard-i-classes` | — | 1 | 1–3 |
| `chain-entry-gate`, `sj-abort-grounded`, `plan-cell`, `meganav-plus-telemetry`, `recept-i-tradet`, `testsuite` | — | **0** | 2–134 |

Sex grenar är helt sammanslagna och borde ha städats. Fem är hopplöst efter (100–259 commits
bakom). Och två är enorma och färska — men **inget säger om de är på väg in eller är dömda.**

Det tyngsta fyndet i den här kategorin: **main är ett skal.**

```
$ git diff --stat origin/main origin/ring2quad | tail -1
580 files changed, 70431 insertions(+), 967 deletions(-)

$ git diff --name-only origin/main origin/ring2quad | cut -d/ -f1-2 | sort | uniq -c | sort -rn
    520 testsuite/tools      ← hela mätverktygslådan
     14 crates/rtx-game
      7 .claude/agents
      6 crates/ben3d         ← en hel crate som inte finns på main
      2 tools/gates          ← facit_lint.py, forsegla_facit.sh (förseglingskedjan!)
```

Förseglingsverktygen som `kodaren.md:19` och `qa-domaren.md:15` gör till ovillkorlig regel
(`0444 + sha256-sidofil`) ligger i `tools/gates/` — **som bara finns på grenarna, inte på main.**
Regeln är alltså omöjlig att följa på main.

Sista *kodcommit* på main är `e2d6c1c` 2026-08-01. De tre commits som följer är
`reference/recept` (20/8) och två `docs(agents)` (21/8). Tjugo dagars kodarbete ligger på
okartlagda grenar.

---

## 6. Det osynliga — allt repot förutsätter men inte innehåller

**Binärer och motorer**
- `mvdsv` (QuakeWorld-server med pr2/API 16) — ingen version, ingen hämtväg
- KTX (`k_*`-cvarerna genomsyrar T3/T4) — ingen version pinnad
- KTX:s frogbot-`bots/`-datakatalog (T4)
- `qw-analyze`-binär (T3 combat lock) + valfri qw-analyze REST-instans

**Speldata**
- `id1/pak0.pak`, `id1/PAK1.PAK` (upphovsrättsskyddat — kan inte checkas in, men *hämtvägen* kan dokumenteras)
- `qw/maps/*.bsp`: dm3, aerowalk, bravado, 100m, dm4, dm6, e1m2
- KTX:s handskrivna `waypoints/*.bot` (för `rtx-waypoint-check`)

**Mätdata och facit**
- `demos/20260507-2107_4on4_]sr[_vs_book[dm3].mvd` (hela baslinjen)
- `demos/dm3_rlstrafejump.qwd`, `demos/dm3_rastairs.qwd` (motorkonstanternas ground truth)
- Ägarens 18 ruttdemos (`generate_from_routes.py:118`: "lanister:~/dm3-drillar")
- `reference/ra-room/README.md` — kanonen
- `dm3-full-graph.json` (recept-verifiering)
- Commits `c8a20fb`, `91a6e34`, `86f7f11`, `1cc87180615f`

**Verktygskedjor**
- `/home/xerial/rtx-tools/` (labctl m.fl.) — hårdkodad i `applicera_recept.py:127`
- `navmesh-doctor/` (identitet, diagnostik, TOOLMANIFEST, runbooks)
- `tools/gates/` (förseglingskedjan) — finns bara på grenar
- `testsuite/tools/` (520 filer) — finns bara på grenar
- `dashboard-gate/publish.sh` + Cloudflare-worker + demo-spelare + OAuth-grind

**Kunskap och organisation**
- `GOTCHAS.md`, `PLANS/RA_STATUS.md`, `WORK_LOGS/`, `GUIDES/VERKTYGSGRINDAR.md`
- Riggen `lanister`, rigglåset `~/lab/.rig-lock`, portkartan, systemd-units
  (`fasttrack-server`, `rtx-t3-server`, `rtx-t3-hub`, `rtx-t3-qtvtunnel`, `ra-drill-*`)
- Sol, Grok, ägaren, `herdr`-panelerna, den lokala Qwen-instansen
- `navpatch:dm3-pentlift-rj` och dess vittnes-cvar `rtx_rj_cost_scale` — verifierat: cvaren
  finns i noll rader Rust på main, endast på `origin/jumps-on-main-pr` (`8d76166`). Drillen
  `rj_pent_to_lifts_to_window_to_quad` är därmed permanent avstådd på main, utan att någon
  fil i repot förklarar varför.

---

## Åtgärdslista

23 leverabler. Varje punkt är en fil eller ett skript som ska existera när den är klar.

### P0 — kallstart omöjlig utan dessa (5)

**P0-1. `docs/GOALS.md`** — projektets uttalade mål, mätbart. Vad är "klart"? Vilket tal ska
flyttas? `docs/baseline/README.md` rad 16–32 innehåller redan siffrorna; formulera dem som mål
med acceptanskriterium. Utan detta vet en ny agent inte vad den ska göra.

**P0-2. `docs/BRANCHES.md`** — grenregister. En rad per fjärrgren: *levande / dömd / övergiven /
sammanslagen*, vad den innehåller, vem som ägde den, vad nästa steg är. Rensa samtidigt de sex
grenar som är 0 före main. Måste namnge att `testsuite/tools/` (520 filer), `tools/gates/` och
`crates/ben3d` bara finns på grenar, och vad planen är för dem.

**P0-3. `reference/ra-room/README.md` — kanonen in i repot** (eller ett explicit dokument som
säger att den är förlorad och vad som ersätter den). Tre rollfiler kallar den "ENDA giltiga
mätreferensen"; just nu är den enda giltiga mätreferensen en tom sträng.

**P0-4. Laga eller ta bort `.claude/agents/`.** Antingen checkas `navmesh-doctor/`,
`GOTCHAS.md`, `PLANS/RA_STATUS.md`, `WORK_LOGS/` och `GUIDES/VERKTYGSGRINDAR.md` in — eller så
skrivs rollfilerna om mot det som faktiskt finns. En rollfil som i versaler beordrar läsning av
en obefintlig manual är sämre än ingen rollfil. Ta samtidigt bort eller flytta
`qwen-forensikern.md`, som själv säger att den inte får startas som Claude-subagent.

**P0-5. `scripts/bootstrap-playground.sh`** — reser `playground/` från noll: hämtar/bygger
mvdsv (pinnad version), verifierar `id1/pak0.pak` + `PAK1.PAK` (dokumenterad hämtväg + sha256,
inte incheckat), hämtar de sju testkartorna, och avslutar med ett rökprov som visar en
kontrollkanal som svarar. Utan detta finns ingen server, ingen MCP, ingen T1, ingen T2.

### P1 — allt mätarbete blockerat utan dessa (6)

**P1-1. Ersätt `/home/xerial/rtx-tools`-beroendet.** `reference/recept/applicera_recept.py:127`
— checka in `labctl` under `reference/recept/` eller skriv om mot standardbiblioteket. Bevisat
brutet utanför ägarens maskin (ModuleNotFoundError, även i `--torrkor`).

**P1-2. `docs/baseline/DEMOS.md` + hämtväg för demokorpusen.** 4on4-MVD:n,
`dm3_rlstrafejump.qwd`, `dm3_rastairs.qwd` och ägarens 18 ruttdemos — via Git LFS eller en
publicerad URL med sha256 per fil. Fyra källfiler i `rtx-nav` kalibrerar konstanter mot dem.

**P1-3. `docs/navpatch-dm3-pentlift.md`** — vad kapabiliteten `navpatch:dm3-pentlift-rj` är,
att dess vittnes-cvar `rtx_rj_cost_scale` bara finns på `origin/jumps-on-main-pr` (`8d76166`),
och vad som ska hända: slås in eller drillen dras tillbaka. Idag är drillen tyst avstådd för
alltid.

**P1-4. `testsuite/publish/`** — publiceringskedjan (`publish.sh`, worker-konfig, demo-spelare)
in i repot, eller RUNBOOK §12–13 omskrivet så att stegen är utförbara. Just nu står
`/path/to/dashboard-gate/publish.sh` som en instruktion till en fil ingen kan hitta.

**P1-5. Ersätt WORK_LOGS-hänvisningarna i `reference/recept/`.** `README.md:56` och
`vf5_ring2quad.json:50` pekar båda på `WORK_LOGS/2026-08-19-hopptraning-ring2quad.md` §30–§33 —
den enda evidensen för att recepten certades. Flytta in relevanta avsnitt eller markera
siffrorna som obestyrkta.

**P1-6. `testsuite/rig/`** — skript som reser T3- och T4-riggarna (privat gamedir, KTX-konfig
inklusive hela återställningskedjan, frogbot-data). RUNBOOK §8 beskriver 15 fallgropar i prosa;
prosa är inte en rigg.

### P2 — grinden ljuger tills dessa är gjorda (5)

**P2-1. Pinna om eller markera de försvunna mätcommitsen.** `docs/baseline/README.md:3`
(`c8a20fb`), `reference/recept/README.md:51,61` (`86f7f11`, `1cc87180615f`),
`vf5_ring2quad.json:50` (`91a6e34`). Antingen pusha commitsen eller skriv ut att mätningen inte
är reproducerbar.

**P2-2. Gör de 23 miljöstyrda testerna ärliga.** Byt `eprintln! + return` mot `#[ignore]`, eller
låt CI förse dem med en BSP. Idag rapporterar `cargo test` 0 failed medan navmesh-byggaren aldrig
mötte en karta. Dokumentera samtidigt alla sex `RTX_TEST_*`-variabler på ett ställe.

**P2-3. Utöka `.github/workflows/build.yml`** med `cargo test -p rtx-mcp` (RUNBOOK:150 kräver
`rtx_mcp.tests > 0`), `cargo fmt --check` (AGENTS.md:197) och de tre Python-selftesterna.

**P2-4. Uppdatera eller märk `testsuite/dashboard/assets/maps/dm3/`.** `graph.json` är en
meganav-dump (4635 celler, 2026-07-26); main bygger 5977/48207. Regenerera mot main eller skriv
i PROVENANCE.md att assets är historiska.

**P2-5. `rust-toolchain.toml`** — pinna verktygskedjan. Ett `--locked`-bygge mot rullande stable
driver isär över tid.

### P3 — dokumentdrift, kostar timmar men inte dagar (4)

**P3-1. `docs/cvars.md` mot koden.** Verifierat: 11 registrerade cvars saknas i dokumentet
(`rtx_bot_power_team`, `rtx_telemetry`, `rtx_bot_power`, `rtx_bot_power_goals`, `rtx_bot_pack`,
`rtx_bot_hopplan`, `rtx_bot_walkplan`, `rtx_bot_auditlog`, `rtx_bot_chain_entry_gate`,
`rtx_bot_sj_abort_grounded`, `rtx_match_resume`) och **en dokumenterad finns inte alls**:
`docs/cvars.md:92` `rtx_bot_ledgecap` — noll träffar i `crates/`. `rtx_bot_power_team` är
särskilt illa: `AGENTS.md:84` gör den till förutsättningen för hela split-team-mätningen.

**P3-2. `AGENTS.md:193–195`** säger att `cargo build` täcker "the game module, its nav core, and
the wire codec". `Cargo.toml` `default-members` innehåller sju crates, inklusive `rtx-auditlog`,
`rtx-ctlproto`, `rtx-demo-tool` och `rtx-waypoint-check`.

**P3-3. `testsuite/config.example.toml`** saknar `tools.mvd_api` och `tools.mvd_cache_dir`, som
`testsuite/README.md:128–132` dokumenterar och `runlib.py:123` accepterar.

**P3-4. Felmeddelanden med kontext.** `python3 testflow.py t1 --quick` mot exempelkonfigen ger
bara `error: [Errno 111] Connection refused` — ingen värd, ingen port, ingen ledtråd om att en
rtx-server med kontrollkanal krävs.

### P4 — trevligt att ha (3)

**P4-1. Tagga baslinjerna.** Noll taggar i repot. `k1b`, `k2`, `baseline-dm3-4on4` som taggar
gör dem utcheckbara utan att man behöver kunna en SHA utantill.

**P4-2. Språkpolicy + ordlista.** Repot blandar engelska (README, docs, kod) och svenska
(rollfiler, recept, drillnamn, `checks.py`-etiketter som `AVSTÅDD`). En kort `docs/GLOSSARY.md`
sv↔en över drill-, rutt- och rumsnamnen (`ring`, `pent`, `quad`, `RA`, `västhyllan`, `lådan`)
sparar en ny agent mycket gissande.

**P4-3. Korta ned ingången till testsviten.** `testsuite/README.md` är 550 rader innan man vet
vad man ska köra. Bryt ut 20 rader "kom igång" överst; behåll resten som referens.

---

## Sammanfattning per prioritet

| prioritet | antal | innebörd |
|---|---|---|
| **P0** | 5 | kallstart omöjlig utan dessa |
| **P1** | 6 | allt mätarbete blockerat |
| **P2** | 5 | grindar och mätvärden opålitliga |
| **P3** | 4 | dokumentdrift mot koden |
| **P4** | 3 | trevligt att ha |
| **totalt** | **23** | |

Rimlig omfattning för P0+P1: ett par dagars arbete för någon som fortfarande minns projektet.
Efter att den sista minns det är flera av punkterna — kanonen, demokorpusen, de tre försvunna
commitsen — **oåterkalleligt förlorade**. Det är den enda delen av den här rapporten som är
brådskande.

---

*Granskningen utfördes uteslutande mot `/tmp/coldstart-rtx` (färsk klon på lanister). Ingen fil
utanför klonen lästes. Alla kommandon i rapporten kördes av granskaren i sessionen; utdata är
klistrad rå.*
