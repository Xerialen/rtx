# `testsuite/rig/` — T3- och T4-riggarna som skript

`testsuite/README.md` beskriver riggen i prosa och räknar upp de fällor
som gör att en rigg fungerar första matchen och sedan slutar. Prosa är
inte en rigg. Det här är samma rigg som kod, där varje fälla är en
assertion som faller — inte en mening någon ska komma ihåg att läsa.

## Filerna

| fil | vad |
|---|---|
| `res_t3.sh`, `res_t4.sh` | reser respektive rigg; tunna omslag |
| `res.sh` | hela proceduren; **en** kopia, för två glider isär |
| `portar.py` | läser `docs/PORTAR.md`. Enda källan till portnummer |
| `gamedir.py` | bygger den privata gamediren; fällorna som assertions |
| `riggvakt.py` | förvillkorskontroll: lås, portar, pid-ägda tjänster, data |
| `aterstall.py` | återställningskedjan (RUNBOOK §14) |
| `aterstall.sh` | omslag för kedjan |
| `frogbot-bots.sha256` | manifest över KTX:s `bots/`-data (pekare + hash) |
| `test_rig.py` | enhetstester **och** negativkontroller |

## Två saker skripten aldrig gör

**Hårdkodar portnummer.** Alla portar kommer ur `docs/PORTAR.md`.
`test_rig.py` letar efter siffror i intervallet i skriptens egen kod och
faller om någon smugit in en. En kopierad portlista är samma fälla som ett
kopierat kontrollvärde.

**Rör `systemctl enable`, `disable`, `daemon-reload` eller `mask`.**
Armerade drop-ins aktiveras retroaktivt av en reload, och den knappen är
riggsätets. `aterstall._systemctl` vägrar verben oavsett flaggor, och ett
test letar efter dem i skriptfilerna.

## Torrkörning är default

Varken `res_t3.sh`, `res_t4.sh` eller `aterstall.sh` rör något skarpt utan
`--verkstall`. Utan flaggan läses portlistan, prövas förvillkoren, byggs
gamediren i katalogen du pekar ut, och kedjan säger vad den *skulle* göra.

Inga sökvägar har defaults. En bekväm default för var bokföringen hamnar
skriver bokföringen på fel ställe tyst, och det är en klass av fel ingen
grind fångar. Saknas en flagga säger vägran vilken.

## STOPP-lägena

Riggen reses inte om något av detta gäller:

* portlistan går inte att läsa, eller en vald port står inte i den
  (**oredovisad port är inte ledig port** — det var precis så `28000` och
  `27599` kunde se lediga ut)
* en vald port är `forbjuden`, `orord`, `deploy` eller `ra-kontroll`
* en vald port har lyssnare just nu
* portarna kommer ur olika grupper, eller är en halv trio
* rigglåset saknas, är tomt, eller hålls av en annan körning
* en pid-ägd port står bland våra val
* T4 utan `bots/`-data, eller med data som inte stämmer med manifestet
* `qw_analyze` pekar på något som inte finns, inte är körbart, eller har
  fel sha256
* kvittokatalogen har redan en ögonblicksbild (en andra bild över den
  första gör återställningen omöjlig — då är «före» i själva verket
  «efter»)
* `--gamedir` ligger inte direkt under mvdsv:s basedir. mvdsv löser
  `-game <namn>` mot `<basedir>/<namn>`, så en «privat» gamedir någon
  annanstans hittas inte alls — servern faller tillbaka på det delade
  trädet, vilket är precis det gamediren finns för att undvika

## De 15 fallgroparna, och var de blev kod

Körordningen bär alltihop: KTX kör om `server.cfg` → `mvdsv.cfg`,
`ktx.cfg`, `pwd.cfg` efter **varje** match och stampar därefter sin egen
usermode-default. Den enda fil som körs efter båda är
`configs/usermodes/<mode>/default.cfg`.

| # | fälla | var den blev kod |
|---|---|---|
| 1 | delad labbserver i stället för privat gamedir | `bygg()` vägrar mål = källa eller mål inuti källan |
| 2 | usermode ≠ `<n>on<n>` | `Riggval.mode()` härleds ur `seats_per_side` |
| 3 | `k_matchless 1` tvingar `teamplay 0` | `overrides()` |
| 4 | timelimit överlever inte mode-ominitieringen | skrivs i sist körda filen, aldrig någon annanstans |
| 5 | `k_noframechecks 0` sparkar headless klienter ~90 s in i match **två** | `granska()` steg 1–3 |
| 6 | `k_lockmode 1` släpper tyst klienterna vid join | `overrides()` |
| 7 | frogbots i T3 / master­servrar | `granska()` steg 4 + 6 |
| 8 | bara boot-cfg granskad, inte hela reset-kedjan | `granska()` steg 3 läser **hela** `RESETKEDJAN` |
| 9 | `pwd.cfg` nollställer `rcon_password` | `RIGGKRITISKA` + steg 3 |
| 10 | `mvdsv.cfg` slår på `sv_crypt_rcon`, flyttar `sv_demodir` | `RIGGKRITISKA` + steg 3 |
| 11 | riggkritiska värden på fel ställe | steg 1: alla måste stå i sist körda filen |
| 12 | `k_overtime 1`/`k_exttime 3` — osynligt till första oavgjorda matchen | `overrides()` sätter 0/0 |
| 13 | spöken efter rivna klienter; trånga platser | `maxclients 16`, `sv_timeout 30` |
| 14 | demoinfo-formatet — enda poängoraklet | `k_demotxt_format json` + demokatalogen granskas |
| 15 | T4: `bots/` saknas, `k_membercount 3` vägrar tyst fjärde boten | `bygg()`/`granska()` T4-grenen |

Steg 3 är den generella formen: **varje** riggkritisk cvar som reset-kedjan
sätter måste stampas om av vår sist körda fil. Den fångar också fällor som
ingen skrivit ner än.

## Extern data — pekare och hash, inte incheckat

Binärdata checkas inte in. Det som står i repot är var datat finns och vad
det ska hasha till.

| vad | var (lanister) | hash |
|---|---|---|
| KTX frogbot-data | `/home/xerial/nquakesv/ktx/bots` | `frogbot-bots.sha256`, 128 filer |
| `qw-analyze` v21 | `/home/xerial/kbot/qw-analyze-v21` | `fc3fd34be9323d67c9275af1acd4830df20f2ec14c4145f35aa1a8a8a062b0b9` |
| mvdsv | `/home/xerial/nquakesv/mvdsv` → `mvdsv-1.20-dev-03d482` | hashas av riggsätet vid körning |
| delat KTX-träd | `/home/xerial/nquakesv/ktx` | kopieras, redigeras aldrig |

`[tools].qw_analyze` i `config.example.toml` är en **pekare**; upplösningen
är v21, den version `testsuite/README.md` beskriver («qw-analyze v21 emits
no dedicated shot stream»). `riggvakt.py --qw-analyze-sha256` binder den.

Manifestet regenereras med, körd i `bots/`:

```sh
find . -type f -printf '%P\n' | LC_ALL=C sort \
  | xargs -d '\n' sha256sum > frogbot-bots.sha256
```

**Rcon-lösenordet skrivs aldrig i repot och aldrig på en kommandorad.**
`gamedir.py --rcon-password-fil` läser det ur en fil utanför trädet och
vägrar om den saknas eller är tom.

## Tester

```sh
python3 -m unittest discover -s testsuite/rig -t testsuite/rig -v
```

Offline. Ingen rigg, ingen server, ingen port reses. Varje assertion som
påstår sig bevaka en fälla har ett test som matar den känt trasig indata
och kräver att den faller — en grind som aldrig setts falla är en grön
lampa, inte en grind.
