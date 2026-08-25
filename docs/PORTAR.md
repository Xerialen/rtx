# Portar på mätriggen — kanonisk lista

**Detta är den enda giltiga portlistan i repot.** Datum: **2026-08-23**,
reviderad **2026-08-24** (ägarbeslut: testsvitens portar) och
**2026-08-25** (ägarbeslut: klientkontroll- och qtv-familjen, samt regeln
att självallokering är förbjuden).
Maskin: `lanister` (`100.64.0.2`).

Kopiera aldrig tabellen. Länka hit. En kopierad portlista är samma fälla
som ett kopierat kontrollvärde: den ena rättas, den andra blir kvar, och
nästa säte läser den som blev kvar. Innan detta dokument fanns bar paketet
tre sinsemellan olika listor, och minst en förbjöd en port som samma dag
kördes skarpt (issue #15).

Skript läser tabellen nedan — de hårdkodar inga egna portnummer.

## Tabellen

Formatet är kontrakt: fem kolumner, en port per rad, `klass` och `roll` ur
de slutna ordförråden nedan. En rad som inte följer formatet ska fälla den
som läser den, inte tolkas välvilligt.

| port | klass | roll | grupp | ägare / belägg |
|---|---|---|---|---|
| 27530 | forbjuden | spel | rtxfast | `fasttrack-server` (ztricks/RTXFAST) |
| 27550 | forbjuden | spel | main | main-servern; nere sedan 2026-08-15, omstart är ägarbeslut |
| 27570 | lab | spel | lab-b | lab-trio B |
| 27580 | lab | spel | lab-a | lab-trio A |
| 27592 | deploy | spel | tbx-d1 | portvaktens atomära par med 27996 |
| 27594 | deploy | spel | tbx-d3 | portvaktens atomära par med 27998 |
| 27599 | orord | spel | frammande | `/fte/qtv` pid 2481951, annan användare (container), uppe 11 dygn |
| 27700 | testsvit | spel | t3 | ägarbeslut 2026-08-24: T3:s `match_server`; se «Öppen punkt 1» |
| 27710 | testsvit | spel | t4 | ägarbeslut 2026-08-24: T4:s `frogbot_server` |
| 27960 | lab | ctl | lab-a | lab-trio A |
| 27970 | lab | ctl | lab-b | lab-trio B |
| 27980 | testsvit | ctl | rtxfast | kontrollkanalen till 27530; ägarbeslut 2026-08-24, se «Öppen punkt 2» |
| 27990 | ra-kontroll | ctl | fasttrack-ra | RA-riggens kontrollkanal; rörs endast på uttrycklig order |
| 27991 | forbjuden | ctl | main | kontrollkanalen till 27550 |
| 27996 | deploy | ctl | tbx-d1 | portvaktens atomära par med 27592 |
| 27998 | deploy | ctl | tbx-d3 | portvaktens atomära par med 27594 |
| 28000 | orord | qtv | ktx | `qtv.bin` pid 1333, syskon till KTX-paret |
| 28100 | testsvit | ctl | t3-klient-gren | T3:s **gren**klient; `[t3].control_port_base` i `testsuite/config.toml` |
| 28101 | testsvit | ctl | t3-klient-ref | T3:s **referens**klient; `control_port_base + 1` per T3:s egen konvention |
| 28110 | testsvit | ctl | t4-klient-gren | T4:s grenklient; `[t4].control_port` i `testsuite/config.toml` |
| 28150 | rigg | ctl | navdok-1-klient-a | parallellriggen `navdok-1`, sida A (loopback); `~/lab/riggar/navdok-1/RIGG.md` ändringslogg 2026-08-25T10:12Z |
| 28151 | rigg | ctl | navdok-1-klient-b | parallellriggen `navdok-1`, sida B (loopback); samma ändringslograd |
| 28160 | rigg | ctl | navdok-1-kastbot | `navdok-1`:s kastbot för KTX-lägesbyte (loopback); RIGG.md ändringslogg 2026-08-25T10:55Z |
| 28502 | orord | spel | ktx | `mvdsv -port 28502 -game ktx` pid 1331 |
| 28503 | orord | spel | ktx | `mvdsv -port 28503 -game ktx` pid 1332 |
| 29570 | lab | qtv | lab-b | lab-trio B |
| 29580 | lab | qtv | lab-a | lab-trio A |
| 29701 | testsvit | qtv | t3 | T3-serverns qtv-hubb; `qtv_streamport` i `t3.cfg` |
| 29711 | testsvit | qtv | t4 | T4-serverns qtv-hubb; `qtv_streamport` i `t4.cfg` |

`27540` (spel, `fasttrack-ra`) hör ihop med `27990` och lyder samma regel:
rörs endast på uttrycklig order. Den står inte som egen rad därför att
riggen reses via kontrollkanalen, och en rad som ingen läser är en rad som
hinner bli fel.

### Ordförråd — `klass`

| klass | betyder | vad ett skript ska göra |
|---|---|---|
| `forbjuden` | reserverad för en tjänst som inte är vår att röra, uppe eller nere | **neka** |
| `orord` | levande och pid-ägd av någon annan just nu | **neka** |
| `deploy` | portvaktens atomära deploy-par (`tbx-d1`, `tbx-d3`) | **neka** — de reses av deploy-kedjan, inte av testriggen |
| `lab` | fri att resa en mätrigg på | **tillåt** |
| `ra-kontroll` | RA-riggen | **neka utan uttrycklig order** |
| `testsvit` | upptagen av T0–T4-testsviten (ägarbeslut 2026-08-24) | **tillåt endast testsvitens runner**; alla andra skript nekar |
| `rigg` | tilldelad en **namngiven** parallellrigg under `~/lab/riggar/<namn>/` (ägarbeslut 2026-08-25) | **tillåt endast den riggens egna skript**; alla andra nekar |

De två sista har åtkomsten `egen` i `portar.py`: anroparen måste uppge radens
`grupp` exakt (`--som <grupp>`) för att släppas igenom. Den som inte säger vem
den är får nej — förvalet är vägran, inte tillåtelse.

Att en `forbjuden` port är tyst betyder inte att den är ledig. `27550` är
nere sedan 2026-08-15 och är fortfarande förbjuden: den som tar en tyst
förbjuden port tar den port ägaren kommer att starta.

Samma sak gäller `rigg`. En parallellrigg lämnas normalt **nere** mellan
körningar, så dess portar är tysta nästan jämt. Tyst ≠ ledig: porten tillhör
riggen tills registret säger något annat.

### Regeln: självallokering är förbjuden

**Ägarbeslut 2026-08-25.** Ett skript får aldrig välja ett portnummer själv,
och aldrig behandla en port som ledig därför att den är tyst.

> **En port som saknas i den här tabellen ⇒ skriptet vägrar.**
> Vägran, inte varning, inte en välvillig gissning, och inte «jag bokför den
> i mitt eget manifest i stället».

Ordningen är alltid: rad i valvet **först**, användning **sedan**. Att skriva
in en självvald port i en riggs `RIGG.md` i efterhand är inte en dokumenterad
avvikelse — det är ett regelbrott, därför att nästa säte läser valvet och ser
en ledig port som i själva verket är i bruk.

Ett manifest under `~/lab/riggar/<namn>/RIGG.md` är därmed **aldrig** en källa
till en port. Det är belägg för vad en rigg faktiskt använder — kolumnen
`ägare / belägg` pekar dit — men behörigheten kommer bara härifrån.

Kanonisk läsväg för den som står på riggen och inte i ett arbetsträd:

```sh
git fetch origin && git show origin/main:docs/PORTAR.md
```

Hela familjen `28100`–`28160` och `29701`/`29711` fördes in 2026-08-25 just
därför att den hade vuxit fram utanför valvet: verktygen använde åtta portar
som ingen rad kände till. Tre skilda fynd samma dygn är ett mönster, inte tre
enskilda misstag — och det var precis så `27980`-kollisionen uppstod.

### Kortlivade portblock

Några portar lever i sekunder: en sondbot ansluter, svarar på en fråga och
dödas. Att kräva en egen rad per sådan port hade gjort tabellen till en logg,
och en tabell som ändras varje minut är en tabell ingen litar på.

De hanteras därför som **reserverat block**, och blocket deklareras här —
vilket är vad regeln ovan kräver. Ett block är reserverat på samma sätt som
en rad: ingen annan får ta en port ur det.

| block | tillhör | vad |
|---|---|---|
| `28170`–`28182` | parallellriggen `navdok-1` | kortlivade query-/sondbotar, en per fråga, dödas efter svar |

Blocket är reserverat brett; observationen är gles. Faktiskt sedda i fas
2-serien 2026-08-25 var **`28170`, `28180`, `28181`, `28182`** — inte hela
intervallet. Bredden är avsiktlig: den som reserverar exakt de portar han råkade
använda i går tvingas ändra valvet i morgon.

Regler för ett block:

* blocket får aldrig överlappa en rad i tabellen, och aldrig ett annat block
* portarna är `ctl` och binds till loopback; ett block är inte en spelport
* en port ur ett annat rigg-block är lika nekad som en `forbjuden` rad
* ett block är ingen fribiljett att växa: behövs fler portar än blocket rymmer
  är det valvet som ändras, inte blocket som tänjs

### Ordförråd — `roll`

`spel` (mvdsv `-port`), `ctl` (kontrollkanal), `qtv`.

En lab-trio är alltid alla tre ur samma `grupp`. Halva trior är den enda
sortens misstag som annars hade kunnat resa rätt rigg på fel portar.

### Ordförråd — `grupp`

`grupp` binder ihop portar som hör till **en och samma sak**. Trio-regeln
ovan gäller `lab`: spel + ctl + qtv ur samma grupp, aldrig en halv trio.

Utanför `lab` är en grupp inte en trio, och får inte tolkas som en. Därför
bär klientkontrollportarna **egna** gruppnamn — `t3-klient-gren`,
`t3-klient-ref`, `t4-klient-gren`, `navdok-1-klient-a`, `navdok-1-klient-b`,
`navdok-1-kastbot` — i stället för att trängas i `t3`/`t4` tillsammans med
serverns spel- och qtv-port.

Det är inte kosmetik. T3 har **två** kontrollkanaler samtidigt (gren och
referens, `bas` och `bas+1`), och två portar med rollen `ctl` i samma grupp
gör begreppet grupp motsägelsefullt: en läsare som frågar «vilken är T3:s
ctl-port?» får två svar och måste gissa. Ett gruppnamn per process ger ett
svar per fråga.

## Belägg — mätt 2026-08-23T17:23:25Z

Levande lyssnare i QW-intervallet, **rotvy** (`sudo ss -tulnpH`; utan rot
visar `ss` porten men inte ägaren):

```
tcp *:27599 users:(("qtv",pid=2481951,fd=4))
tcp *:28000 users:(("qtv.bin",pid=1333,fd=7))
tcp 0.0.0.0:28502 users:(("mvdsv",pid=1331,fd=7))
tcp 0.0.0.0:28503 users:(("mvdsv",pid=1332,fd=7))
udp *:27599 users:(("qtv",pid=2481951,fd=3))
udp *:28000 users:(("qtv.bin",pid=1333,fd=3))
udp 0.0.0.0:28502 users:(("mvdsv",pid=1331,fd=4))
udp 0.0.0.0:28503 users:(("mvdsv",pid=1332,fd=4))
udp 127.0.0.1:27999 users:(("node",pid=1335,fd=26))
```

Alla andra portar i tabellen hade noll lyssnare vid samma avläsning.

Lab-triornas indelning är läst ur portvakten som kördes skarpt samma dag
(`portvakt_koll.py`, körningarna `r2q-timme` 07:22Z och `ra-reg-full`
08:25Z): `A = 27580/27960/29580`, `B = 27570/27970/29570`.
Deploy-paren är lästa ur `ALLOWED_DEPLOY_PAIRS`.

## Belägg för klientkontroll- och qtv-familjen — mätt 2026-08-25T14:21:35Z

Åtta rader tillkom 2026-08-25. Ingen av dem är ett beslut om var något ska
ligga: de skriver in var det **redan låg**. Samma rotvy som ovan visade noll
lyssnare på alla åtta vid avläsningen — familjen är konfigurerad, inte uppe,
och det är just därför den kunde växa osedd.

Belägget är alltså konfigurationen, inte lyssnarlistan:

```
$ grep -n "control_port_base\|control_port" testsuite/config.toml
30:control_port_base = 28100
41:control_port = 28110

$ grep -n "control_port_base\|control_port" testsuite/config.example.toml
7:control_port = 27980        # rtx control channel (game port + 450 by our convention)
35:control_port_base = 28100   # branch client control port; reference uses base+1
50:control_port = 28110        # control port for the branch client process

$ grep -n "qtv_streamport" ~/kbot/serverdir/t3ktx/t3.cfg ~/kbot/serverdir/t4ktx/t4.cfg
t3.cfg:66:set qtv_streamport 29701
t4.cfg:66:set qtv_streamport 29711

$ grep -n "CTL_PORTS\|PROBE_CTL" ~/lab/riggar/navdok-1/navdok_fas2.py
37:CTL_PORTS = {"brch": 28150, "ref": 28151}
109:PROBE_CTL = 28160
```

`28101` har ingen egen rad i någon konfiguration — den är **härledd**:
`control_port_base + 1`, dokumenterat i `config.example.toml` som «reference
uses base+1». En härledd port är lika upptagen som en skriven, och lättare att
missa. Den får därför en egen rad här; det är hela poängen med tabellen.

`28150`/`28151`/`28160` bokfördes ursprungligen **bara** i parallellriggens
eget manifest (`~/lab/riggar/navdok-1/RIGG.md`, ändringslogg 10:12Z och
10:55Z). Det är precis det regeln «självallokering är förbjuden» nu förbjuder:
manifestet är belägg, valvet är behörighet.

### Vad familjen betyder för «Öppen punkt 1»

`config.example.toml` säger på rad 32–33 om `match_server`: «Take the port
from `docs/PORTAR.md`; never copy a port number here» — och skriver sedan
själv `28100` och `28110` som literaler tio rader ner. Filen bryter sin egen
regel i sin egen text. Att portarna nu står i tabellen gör literalerna
redovisade, men inte riktiga: de ska hämtas härifrån som allt annat. Det är en
kodändring i testsvitens konfigurationsläsning och hör inte hemma i det här
dokumentet.

### Portar som inte stod i någon tidigare lista

Tre rader ovan är tillägg, inte avskrifter. De står här därför att de
saknades — inte därför att någon bestämt något nytt:

* **`28000`** — `qtv.bin` pid **1333**, samma pid-block som KTX-paret
  1331/1332. Lika orörbar som de, och lika lätt att ta av misstag.
* **`27599`** — `/fte/qtv` pid **2481951**, ägd av en **annan användare**
  i en container. En `ss -tulnp` utan rot visar porten men inte ägaren, så
  den har sett ledig ut för varje säte som kollat utan rot.
* **`27980`** — kontrollkanalen till den förbjudna `27530`. Den stod inte
  i någon av de tre listorna och inte i portvaktens förbjudna mängd. Den
  är satt till `forbjuden` här som **fail-closed-val**, inte som ett
  fattat beslut: att prata med en servers kontrollkanal är att röra
  servern. Se «Öppen punkt 2».

Det är hela skälet till att den här filen mäter i stället för att kopiera.

## Öppen punkt 1 — `27700`

`27700` står som **förbjuden** ovan därför att det är så den behandlades
skarpt 2026-08-23: portvakten räknar den som förbjuden, och båda dagens
körkvitton bokför den som «förbjuden … tyst».

Samtidigt beskrev `testsuite/config.example.toml` samma port som T3:s
`match_server` — «dedicated mvdsv+KTX instance». Två av repots egna filer
sade alltså emot varandra om samma port.

Motsägelsen var **inte** avgjord här. Exempelkonfigurationen har fått
värdet borttaget — fältet är obligatoriskt och tomt som filens övriga
obligatoriska fält — så att ingen kopierar ett portnummer som kanske är
fel. Vilken port T3-riggen ska stå på är ett ägar-/Fable-beslut, och när
det är fattat är det den här tabellen som ändras. Ingen annan fil.

**AVGJORD 2026-08-24 (ägarbeslut):** `27700` är T3:s `match_server` och
står som `testsvit` i tabellen. Testsvitens operativa `config.toml` på
riggen behåller värdet.

## Öppen punkt 2 — `27980` och T1/T2

`testsuite/config.example.toml` sätter `[server].control_port = 27980`,
och `[sweep].restart_cmd` startar om `fasttrack-server`. Det är samma
tjänst som äger den förbjudna `27530`. Testsviten är alltså skriven för
att mäta mot precis den server Hopparens riggregler förbjuder honom att
röra.

Det är en verklig kollision mellan två arbetssätt, inte ett skrivfel, och
den avgjordes inte här. Tabellen nekade `27980` tills någon beslutade.
Ett skript som behöver den ska vägra och säga varför — inte gissa.

**AVGJORD 2026-08-24 (ägarbeslut):** testsviten får mäta mot
fasttrack-motorns kontrollkanal `27980` och står som `testsvit` i
tabellen. Kvarstående regel: spelservern `27530` är fortsatt `forbjuden`
för alla — testsviten talar bara kontrollkanalen. En testsvitskörning
får inte pågå samtidigt som en annan mätning använder samma motor;
krocken riskbedöms per körning som alla parallella jobb.

## Öppen punkt 3 — parsern känner inte igen `testsvit` och `rigg`

**Inte avgjord här, och medvetet inte rättad här.** Den här filen ändrar
tabellen. Att ändra den kod som läser tabellen är en annan sorts handling och
går sin egen väg.

`testsuite/rig/portar.py` har ett **slutet** klassordförråd:

```python
KLASSER = frozenset({"forbjuden", "orord", "deploy", "lab", "ra-kontroll"})
```

`testsvit` fördes in i tabellen 2026-08-24 och `rigg` 2026-08-25 — ingendera
finns i ordförrådet. Parsern är fail-closed och faller på första okända
raden, alltså på `27700`, som ligger före allt som lades till 2026-08-25:

```
$ python3 testsuite/rig/portar.py --portlista docs/PORTAR.md --trior
PORTLISTA VÄGRAD: okänd klass 'testsvit' för port 27700 — tillåtna: deploy, forbjuden, lab, orord, ra-kontroll
rc=2
```

Utfallet är detsamma före och efter 2026-08-25 års revision: samma rad, samma
rc. Revisionen gör alltså varken till eller från för läsbarheten — men den
löser den inte heller, och det ska stå här och inte upptäckas av nästa säte.

Dessutom bär `testsuite/rig/test_rig.py` ett facit som blev inaktuellt redan
2026-08-24: det kräver `t[27700].klass == "forbjuden"`, medan tabellen sedan
ägarbeslutet säger `testsvit`. Även med ordförrådet utökat faller testet
därför på nästa rad — det är två fel, inte ett.

Att ingendera setts beror på att **inget CI-arbetsflöde kör riggsvitens
tester**: `build`, `lock-guard`, `ra-room-lock` och `ring2quad-lock` rör inte
`testsuite/rig/`. En grind som ingen kör är ingen grind.

Vad som krävs, som specifikation och inte som utförd ändring:

1. `KLASSER` utökas med `testsvit` och `rigg`, och `ATKOMST` får en post för
   var och en. Båda är «neka utom för sin egen ägare» — samma form som
   `ra-kontroll`, inte samma som `lab`. Assertionen
   `set(ATKOMST) == set(KLASSER)` fäller den som glömmer den ena.
2. `test_rig.py` flyttar `27700` från de förbjudnas lista till en assertion
   om `testsvit`, och får negativkontroller för de två nya klasserna på samma
   form som de befintliga.
3. Riggsvitens tester körs i CI, annars ligger nästa glapp lika tyst.

**ÅTGÄRDAD 2026-08-25.** Punkt 1 och 2 är gjorda; punkt 3 (CI) kvarstår.

`KLASSER` och `ATKOMST` känner nu båda klasserna, och `test_rig.py` har
flyttat `27700` till `testsvit`. Åtkomsten heter **`egen`** och är strängare
än `lab`, inte mildare: den som vill resa en `testsvit`- eller `rigg`-port
måste uppge radens `grupp` (`krav_tillaten(..., som="t3")`,
`portar.py --port 28150 --som navdok-1-klient-a`). Matchningen är **exakt** —
ett prefixmatchande `som` hade låtit en rigg som heter `navdok` ta
`navdok-1`:s portar. Utelämnat `som` är nej, inte ja.

Valvet går alltså att läsa igen:

```
$ python3 testsuite/rig/portar.py --portlista docs/PORTAR.md --trior
{"lab-a": {"ctl": 27960, "qtv": 29580, "spel": 27580},
 "lab-b": {"ctl": 27970, "qtv": 29570, "spel": 27570}}
rc=0
```

Regeln «port utanför valvet ⇒ vägra» är oförändrad och prövad även med `som`
satt — `som` är ingen huvudnyckel och öppnar varken `forbjuden`, `orord`,
`deploy`, `ra-kontroll` eller en oredovisad port.

**Kvar: punkt 3.** Inget CI-arbetsflöde kör fortfarande riggsvitens tester, så
nästa glapp av samma sort skulle ligga lika tyst. Att lägga till det är en
ändring i `.github/` och därmed ägarceremoni — den görs inte här.

## Öppen punkt 4 — fem portar till, mätta men inte beslutade

Vid avläsningen 2026-08-25T14:25:18Z stod fem portar till i systemd-units på
`lanister` utan rad här. **De har medvetet inte fått rader.** En rad är ett
beslut om vem porten tillhör och vad ett skript får göra med den, och det
beslutet fattas av ägaren — inte av den som råkade läsa unit-filerna. De
skrivs upp här så att nästa säte inte «upptäcker» dem en gång till.

| port | var den står | läge vid avläsningen |
|---|---|---|
| `27590` | `toolbox-b-test.service`, `mvdsv -port 27590` | `failed` |
| `27595` | `tbx-d4.service`, `mvdsv -port 27595` | `failed` |
| `27981` | `fasttrack-live-bridge.service`, `--proxy-port 27981` | `inactive` |
| `28501` | `frogpound.service`, `mvdsv -port 28501` | `inactive` |
| `28505` | `frogpound.service`, ctl enligt unitens `Description` | `inactive` |

Rå utdata, `systemctl --user show -p Description -p ExecStart -p ActiveState`:

```
frogpound.service
  argv[]=/home/xerial/frogpound/runtime/mvdsv -port 28501 +exec frogpound.cfg
  Description=Frogpound public QW server (mvdsv+rtx, game 28501, ctl 28505)
  ActiveState=inactive
fasttrack-live-bridge.service
  argv[]=... live_bridge.py --control 27980 ... --ws-port 8093 --proxy-port 27981 ...
  Description=qw-fasttrack live bridge (control 27980, ws 8093, proxy 27981)
  ActiveState=inactive
tbx-d4.service
  argv[]=.../runtime-tbx-d4/mvdsv -port 27595 +set rtx_nav_patch 0 +exec fasttrack.cfg
  ActiveState=failed
toolbox-b-test.service
  argv[]=.../runtime-tbx/mvdsv -port 27590 +exec fasttrack.cfg
  ActiveState=failed
```

Två saker är värda att lägga märke till.

`tbx-d4` (`27595`) hör till samma familj som deploy-paren `tbx-d1`
(`27592`/`27996`) och `tbx-d3` (`27594`/`27998`), som **har** rader. Ett
tredje par har alltså vuxit fram utan att paras och utan att redovisas; dess
kontrollport är okänd för det här dokumentet.

`28505` är enbart belagd av unitens `Description`, inte av dess `ExecStart` —
alltså en port som en människa har skrivit ner men som ingen kommandorad
bekräftar. Den är därför den svagast belagda raden i hela materialet, och det
är just därför den inte fått bli en rad.

Alla fem var nere vid avläsningen. Tyst är inte ledigt — se `27550`.

## Vad som inte är kanon

Daterade kvitton och granskningar under `docs/` och `reference/` bokför
vilka portar en viss körning använde den dagen. De är protokoll, inte
regler, och de skrivs aldrig om i efterhand. Står ett portnummer där och
ett annat här, gäller den här filen.

Riggarna som portarna hör till beskrivs i [riggarna](RIGGAR.md) — vilka
riggtyper som finns, hur de låses och vem som får resa vad. Även det
dokumentet är protokoll i förhållande till den här filen: står ett portnummer
där och ett annat här, gäller den här filen.
