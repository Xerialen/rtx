# ADR-001 — RA-room default är ett kontrakt

## Context

S5 n=100 giltiga/ben gav 592/600 (98,7 %) med F1 på. Ägare 22/8: det är
SUCCESS; 99/ben är stängt. PR #46 la fröna och `recept::tests::ra_room_kontrakt`
på `main` (`205fc8af`). RING2QUAD kommer efter och får inte vända RA-rummets
lastbärare tyst. Unit-testet *kan* falla (`Bool(true)`→`false` gav
`RA_ROOM_LOCK: edge_narrow≠true`) men blockerar inte merge om checken inte
är required, om testet raderas i samma PR, eller om jobbet skippas.

## Decision

`rtx_bot_edge_narrow` default **true** är ett kontrakt, inte en UI-knapp.
Ändring av lås 1–8 i `DEFAULT-KONTRAKT.md` kräver ny RA-omkörning, uppdaterad
kontraktfil, och grön check `ra-room-lock` som *avsiktligt* skrivits om — inte
en grön svit efter tyst revert.

Kedja krav → test → check-namn:

| Lås | Krav | Test | Check |
|---|---|---|---|
| 1 | tom dir+dm3 = inbäddad bake | `recept::tests::ra_room_kontrakt::tom_dir_dm3_inbaddad` | `ra-room-lock` |
| 2 | mållinje c1–c5 | `control::tests::c1_…`–`c5_…` (`--exact`, fail-om-tomt) | `ra-room-lock` |
| 3 | `rtx_bot_edge_narrow` default true | `ra_room_kontrakt::edge_narrow_true` + token i `cvars.rs` + `tests/ra_room_lock.rs` | `ra-room-lock` |
| 4 | `rtx_bot_walkplan` default true | `ra_room_kontrakt::walkplan_true` + token | `ra-room-lock` |
| 5 | K2-bake hash `feeea6b4…` | `ra_room_kontrakt::k2_bake_hash_feeea6b4` | `ra-room-lock` |
| 6 | vf5 inte inbäddad | `ra_room_kontrakt::vf5_inte_inbaddad` | `ra-room-lock` |
| 7 | `walkdiag` default false | `ra_room_kontrakt::walkdiag_false` + token | `ra-room-lock` |
| 8 | `rtx_bot_count` default 0 | `ra_room_kontrakt::bot_count_noll` + token | `ra-room-lock` |

Jobbet `ra-room-lock` har inget path-filter, inget skip-`if:`, inget
`continue-on-error`. Settings (required check + no bypass) är ägarled.

## Consequences

RING2QUAD får sätta cvarer i *sin* cfg/rigg, inte i `RTX_CVAR_DEFAULTS`.
vf5 stannar utanför `las_inbaddad`. Bake för tom dir på dm3 stannar.
`walkdiag` stannar false. `rtx_bot_count` stannar 0. Markdown, ADR och
PR-checkbox fäller inte binären; named check utan bypass gör det.
CODEOWNERS flaggar hunken på en 1-persons-fork, den är inte mergegrinden.
Dummy-PR som vänder fröet till `Bool(false)` är negativkontrollen efter
Settings — inte del av denna PR.

---

# Addendum 2026-08-23 — grenskyddet som kontrakt

Ovanstaende ADR sager *vad* som ar last. Det har addendumet sager *hur*
laset halls: rulesetet, check-namnkontraktet, negativkontrollerna och
ceremonierna. Texten ovanfor `---` ar oforandrad.

## Ruleset 21201527 — vad som bar, och varfor

Rulesetet heter `ra-room-lock`, target `branch`, condition
`refs/heads/main`, `enforcement: active`, `bypass_actors: []`
(`current_user_can_bypass: never`). Efter andring A+B (23/8) bar det fyra
regler:

| Regel | Parametrar | Varfor |
|---|---|---|
| `required_status_checks` | context `ra-room-lock`, `integration_id 15368`, `strict_required_status_checks_policy: true` | Laset maste vara gront, fran ratt app, pa en gren som ar up-to-date |
| `non_fast_forward` | — | Stanger hal 1 |
| `deletion` | — | Kosmetiskt for default-gren (se restrisker) |
| `pull_request` | approvals **0**, `require_last_push_approval: false`, `require_code_owner_review: false`, `required_review_thread_resolution: false`, `dismiss_stale_reviews_on_push: false`, `allowed_merge_methods: [merge, squash, rebase]` | Stanger hal 2 utan att frysa main |

**De tva halen var live-belagda, inte teoretiska.** Commit `e7e2762` bar en
akta gron `ra-room-lock` och kunde 23/8 BADE force-pushas over main (vilket
raderar merge-historik) OCH fast-forward-direktpushas till main forbi varje
grind. `non_fast_forward` stanger det forsta, krav-pa-PR det andra.

Varje falt i `pull_request` star pa `false` med flit: ett felaktigt `true`
pa en 1-persons-fork (t.ex. `require_last_push_approval`) fryser main
permanent, eftersom det inte finns nagon andra manniska som kan godkanna.
Approvals ar `0` av samma skal. Grinden ar checkarna - inte granskningen.

**App-pinnen `integration_id 15368` ar hygien, inte sakerhet.** Den binder
contexten `ra-room-lock` till appen `github-actions`, sa att en *commit
status* med samma namn fran en PAT inte raknas. Den ar numera **bevisad,
inte bara dokumenterad**: 23/8 postades en forfalskad gron status
`ra-room-lock` pa dummyns HEAD `a221fba` (PR #50). Utfall: den akta
check-runen fran app 15368 stod kvar pa `failure` och PR:en rapporterade
ordagrant `mergeStateStatus: BLOCKED` med `mergeable: MERGEABLE`. Pinnen
skyddar dock inte mot samma admin-token som kan radera hela regeln - se
restrisker.

## Check-namnkontraktet

**`jobs.<id>.name` ar den required contexten. Filnamnet ar det inte.**

Ett required status check-krav refererar en *context-strang*. Den strangen
kommer fran jobbets `name:`. Doper man om jobbet, eller tar bort `name:` sa
att job-id:t blir contexten, sa slutar den required checken att dyka upp -
och en check som aldrig rapporterar blockerar i praktiken (pending), medan
en check som *skippas* raknas som **pass**. Darav forbudslistan, som galler
varje last workflow (`ra-room-lock.yml`, `lock-guard.yml`, kommande
`ring2quad-lock.yml`):

- inget `paths:` / `paths-ignore:` (filtrerat jobb = skippat = pass)
- inget `continue-on-error`
- inget `if:` som kan skippa jobbet eller fallsteget
- `jobs.<id>.name` skrivs ut explicit och ar identisk med contexten

## lock-guard — vakt mot workflow-manipulation

Halet: en PR kunde tomma `ra-room-lock.yml` pa allt innehall men behalla
jobbnamnet. Resultatet blir en **akta** gron check fran app 15368 - den
passerar bade required-kravet och app-pinnen, for checken ar genuin. Den
mater bara ingenting langre.

`lock-guard` faller varje PR vars andrade filvagar ror
`^\.github/workflows/` eller `^\.github/CODEOWNERS$`.

Tva konstruktionsval barn:

1. **Trigger `pull_request_target`, inte `pull_request`.** Endast
   `pull_request_target` kor *basgrenens* version av workflow-filen. Med
   `pull_request` skulle PR:en leverera sin egen vakt och kunna avvapna
   den i samma commit.
2. **Jobbet checkar aldrig ut och exekverar aldrig PR-kod.** Priset for
   `pull_request_target` ar att kontexten normalt far en skrivtoken; en
   `actions/checkout` av PR-huvudet foljd av valfritt bygg-/teststeg vore
   fjarrkodexekvering med skrivrattigheter. Vakten laser darfor bara
   *filvagar som text* via `gh api .../pulls/N/files --paginate`, och
   workflow-filen skruvar dessutom ner `permissions` till `contents: read`
   + `pull-requests: read`.

Vakten ar **fail-closed**: den faller ocksa nar den inte kan lita pa sitt
eget underlag - ogiltigt eller saknat `changed_files`, noll andrade filer,
eller en fillista som ar kortare an vad PR-objektet uppger (trunkering;
files-endpointen slutar leverera vid 3000 filer). `previous_filename` laggs
till i listan, annars kan en workflow smitas ut ur katalogen via
omdopning och undga vakten.

## Negativkontroller: recept och kadens

En grind man inte sett FALLA ar overifierad. Tva olika kontroller, tva
olika kadenser - de mater olika saker och ersatter inte varandra.

**(i) Instrumentkontroll — efter varje merge till main.**
Mater att *checken sjalv* fortfarande kan falla.
Recept for `ra-room-lock`: `workflow_dispatch` mot en muterad gren (t.ex.
frot `rtx_bot_edge_narrow` vant till `Bool(false)`).
Krav: korningens `conclusion == FAILURE`. Gron eller skippad = underkant
instrument.

`lock-guard` har ingen `workflow_dispatch` - den behover ett PR-nummer for
att ha nagot att diffa, och en dispatch-variant skulle krava ett `if:` som
forbudslistan inte tillater. Dess instrumentkontroll ar i stallet en
slask-PR som ror en fil under `.github/workflows/`; krav: `lock-guard`
far `conclusion == FAILURE`. Beslutslogiken ar dessutom negativkontrollerad
utanfor GitHub mot fejkade fillistor (bade traff och icke-traff, plus
narmissar som `.github/CODEOWNERS.md` och `docs/.github/workflows/…` som
INTE ska falla).

**(ii) Ruleset-kontroll — efter varje regelandring.**
Mater att *rulesetet* fortfarande blockerar. En grind som inte setts falla
EFTER en andring ar overifierad, aven om den foll fore.
Recept: en dummy-PR som bryter laset, med grenen **up-to-date mot main**.
Krav: ordagrant `mergeStateStatus == BLOCKED`.

> `BEHIND` och `UNSTABLE` duger **inte**. `BEHIND` betyder bara att grenen
> ligger efter main (det ar `strict`-policyn som talar, inte laset) och
> `UNSTABLE` att nagon icke-required check ar rod. Ingetdera bevisar att
> det ar `ra-room-lock` som stoppar merge. Las av med
> `gh pr view <n> --json mergeStateStatus,mergeable`.

## Ceremoni: legitim andring av en last workflow

Andringar under `.github/workflows/**` faller av `lock-guard` - med flit.
Nar en andring anda ska in ar det en agarceremoni, i den har ordningen:

1. **Agar-GO** pa den konkreta diffen (ingen agent beslutar detta sjalv).
2. **Temporart lyft**: agaren tar bort `lock-guard` ur ruleset 21201527
   (eller sanker `enforcement`). Endast agaren.
3. **Andra** workflow-filen, i en PR som annars ar sa liten som mojligt.
4. **Ny negativkontroll** av den andrade checken - bada kadenserna ovan,
   mot den SKEPPADE konfigurationen. Gammalt kvitto galler inte.
5. **Aterstall** rulesetet till pinnat lage och bekrafta med en
   uppslagning av `gh api repos/Xerialen/rtx/rulesets/21201527`.

Fonstret mellan steg 2 och 5 ar den enda stund main star oskyddad. Det
halls kort och det bokfors.

## Nodutgangen

Om Actions ligger nere ar main omergbar. Da - och bara da - far
`enforcement` sankas, och **endast av agaren**. Villkoret for att hoja
igen: **varje sankning kraver en ny negativkontroll fore hojning**, enligt
recept (ii). Skalet ar att ett ruleset som varit sankt inte har nagot
giltigt kvitto pa att det fortfarande blockerar.

## Pre-registrering: `ring2quad-lock`

Namnet och kontraktet registreras har i forvag sa att bygget senare inte
uppfinner nagot eget. **Bygget ar agargrindat och ingar inte i den har
andringen.**

- Egen workflow-fil: `.github/workflows/ring2quad-lock.yml`.
- `jobs.ring2quad-lock.name: ring2quad-lock` = required context
  `ring2quad-lock`. Samma namnkontrakt som ovan.
- Samma forbudslista (inga `paths:`, inget `continue-on-error`, inga
  skippande `if:`).
- Infors som **en rads addition** i `required_status_checks` i befintligt
  ruleset 21201527, med `integration_id: 15368`. Inget nytt ruleset.
- Samma tva negativkontroller innan den gors required.

## Restrisker — uttalade, inte bortskrivna

- **Agarens admin-token kan allt.** Den kan radera rulesetet, andra
  checkarna och forfalska statusar. Hela det har skyddet riktar sig mot
  **agentfel, misstag och lackt agent-token** - inte mot agaren sjalv. Det
  ar en verklig grans, inte en formalitet.
- **Satena kor idag med agarens admin-token.** Beslutad atgard: fine-
  grained PAT med `contents` + `pull-requests`, utan `administration`, till
  agentsatena. Den ar **agarledd och inte genomford i och med den har
  andringen**. Tills den ar pa plats ar avstandet mellan "agent" och
  "admin" en konvention, inte en sparr.
- **`deletion`-regeln ar kosmetisk** for default-grenen: GitHub vagrar anda
  radera den. Den behalls for att den kostar noll och skyddar om main nagon
  gang slutar vara default.
- **Actions-avbrott fryser merge** tills agaren sanker `enforcement`. Den
  risken fanns fore A+B ocksa; skillnaden ar att proceduren nu ar skriven.
- **`lock-guard` skyddar filvagar, inte semantik.** En PR som forsamrar
  `ra-room-lock` utan att roa en fil under `.github/` finns inte - men en
  PR som forsamrar de *tester* checken kor gor det. Det ar CODEOWNERS-
  flaggan och `ra-room-lock`s egna meta-steg som tacker den ytan.
