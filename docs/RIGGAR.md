# Riggarna — vilka de är, vem som får resa dem, hur de låses

Maskin: `lanister` (`100.64.0.2`). Skriven 2026-08-25 på ägarbeslut.

En «rigg» är en QuakeWorld-server som körs för att **mäta** något, plus de
klienter och det lås som hör till. Det här dokumentet beskriver vilka sorters
riggar som finns, hur de skiljs åt, och vilka regler som gäller för alla.

**Portnummer står inte här.** De står i [portvalvet](PORTAR.md), som är repots
enda giltiga portlista. Det här dokumentet är protokoll i förhållande till
valvet: står ett portnummer här och ett annat där, gäller valvet. Skälet är
detsamma som alltid — en kopierad portlista är samma fälla som ett kopierat
kontrollvärde: den ena rättas, den andra blir kvar, och nästa säte läser den
som blev kvar.

Riggarnas skript beskrivs i [`testsuite/rig/README.md`](../testsuite/rig/README.md);
den filen upprepas inte här.

---

## De tre riggtyperna

De skiljs åt av **vem som äger dem** och **vad ett misstag kostar**, inte av
vilken mjukvara som körs. Alla tre kör mvdsv.

### 1. Huvudmätriggen

Den dömda mätningens rigg. RA-riggen (`fasttrack-ra`) och lab-triorna hör hit,
liksom portvaktens deploy-par. Det är den enda riggtyp vars siffror går vidare
som mätvärden utan vidare kvalificering.

* Den skyddas av det **globala** låset `~/lab/.rig-lock`.
* Lab-triorna heter `lab-a` och `lab-b`. En trio är alltid **spel + ctl + qtv
  ur samma grupp**. En halv trio är den enda sortens misstag som annars hade
  kunnat resa rätt rigg på fel portar, och den vägras därför av
  `testsuite/rig/portar.py` innan något startas.
* RA-riggens kontrollkanal rörs **endast på uttrycklig order** — den har egen
  klass i valvet (`ra-kontroll`) just för att en `tillåt`/`neka` inte räcker.
* Efter varje omstart är allt planterat borta: PlanLink/PlanDrop lever i
  minnet. Replantering och stampverifiering hör till omstarten, inte till
  felsökningen efteråt.

### 2. Testsvitens servrar

T0–T4-testsvitens egna servrar. De reses av testsvitens runner och av ingen
annan. I valvet bär de klassen `testsvit`, som betyder **tillåt endast
testsvitens runner; alla andra skript nekar**.

De består av mer än en server per steg, och det är den insikten som saknades i
valvet fram till 2026-08-25:

| del | vad |
|---|---|
| matchservern | dedikerad `mvdsv`+KTX-instans, en per teststeg |
| qtv-hubben | serverns `qtv_streamport`, satt i stegets `.cfg` |
| klientkontrollen | **en ctl-port per klientprocess**, inte per server |

T3 kör två klienter samtidigt — en **gren**klient och en **referens**klient —
och behöver därför två kontrollportar. Konventionen är `bas` och `bas + 1`.
Den andra porten står inte skriven i någon konfigurationsfil; den är härledd.
En härledd port är lika upptagen som en skriven, och lättare att missa — den
har därför en egen rad i valvet.

T1/T2 reser ingen egen server. De mäter mot fasttrack-motorns kontrollkanal.
Spelservern i den familjen är `forbjuden` för alla; testsviten talar bara
kontrollkanalen. En testsvitskörning får inte pågå samtidigt som en annan
mätning använder samma motor.

### 3. Parallellriggar

En parallellrigg är en tillfällig, namngiven mätrigg som ett annat säte reser
**vid sidan av** huvudmätningen, under eget lås och på egna portar. Den
infördes 2026-08-25 för att låta två säten mäta samtidigt utan att den ena
kan förstöra den andras körning.

Konventionen är katalogbaserad:

```
~/lab/riggar/
├── REGISTER.md            # append-only register över ALLA parallellriggar
└── <riggnamn>/
    ├── RIGG.md            # manifestet — kontraktet för just den här riggen
    ├── SHA256-KONTRAKT    # filer + hashar; läses vid VARJE start
    ├── start.sh  stop.sh
    ├── RIGGLÅS            # finns bara medan riggen är uppe
    ├── LÅSLOGG.md         # append-only historik: START/STOPP med portbevis
    ├── bin/               # egna frysta binärkopior
    ├── serverdir/         # EGEN kopia — aldrig delad skrivyta
    └── kvitton/
```

Fyra regler bär hela konstruktionen:

**Manifestet är kontraktet.** `RIGG.md` säger vad riggen är: portar, kärnband,
unit, avvikelser mot mallen den kopierades ur. `SHA256-KONTRAKT` läses av
`start.sh` vid varje start — drift mot kontraktet är STOPP och rapport, aldrig
tyst omskrivning.

**Aldrig delad skrivyta.** `serverdir/` är riggens egen kopia. Två riggar som
skriver demos i samma katalog skriver över varandras bevis.

**Manifestet är aldrig en källa till en port.** Det är belägg för vad riggen
använder; behörigheten kommer bara ur valvet, och raden i valvet ska finnas
*före* användningen. Se «Självallokering» nedan.

**Registret ändras aldrig, det växer.** En rad läggs till i `REGISTER.md` när
riggen skapas. Avveckling är en **ny** rad, inte en struken.

Läge 2026-08-25T14:25:04Z: en parallellrigg är registrerad, `navdok-1`
(registrerad 09:52:51Z, ägare Navmeshdoktorn). Den är **nere** — inget
`RIGGLÅS` finns i katalogen — vilket är normalläget mellan körningar.

---

## Låskonventionerna

Tre lås på tre olika saker. Att blanda ihop dem är den dyraste sortens fel,
för ett lås på fel resurs ser ut att fungera ända tills två körningar möts.

| lås | skyddar | tas av | frigörs av |
|---|---|---|---|
| `~/lab/.rig-lock` | **huvudmätriggen** | den som mäter | samma säte, när mätningen är klar |
| `~/lab/riggar/<namn>/RIGGLÅS` | **en parallellrigg** | riggens `start.sh` | riggens `stop.sh`, med portbevis |
| `~/lab/riggar/REGISTER.md` | *inget* — det är ett register, inte ett lås | — | — |

Reglerna:

* Låsfilen innehåller namn **och riktigt PID-nummer**. Literalen `$$` i en
  låsfil är ett trasigt lås som ser giltigt ut.
* Finns låset redan — **vänta**. Ta det aldrig över utan uttryckligt beslut.
* `pgrep` räcker inte som livskontroll, av två skäl. Plantering och
  certifiering syns inte alltid i processlistan; och `pgrep -f` i en
  ssh-kedja **matchar sitt eget kommando**, vilket ger ett falskt positivt som
  låser loopen och gör lägesrapporten osann. Vänta på MainPID i `/proc`.
* En parallellrigg **läser** det globala låset men rör det aldrig, och vägrar
  starta om det skulle nämna hennes egna portar.
* `stop.sh` bevisar att portarna är tysta *innan* låset släpps, och skriver
  bevisraden i `LÅSLOGG.md`. Ett släppt lås utan portbevis är ett påstående,
  inte ett kvitto.
* En tom låsfil är en **avvikelse**, aldrig ett ledigt lås. `: > LÅSFIL` är
  därför inte ett sätt att frigöra ett lås.

### Triolås — föreslaget, inte infört

En revision av parallellriggskonventionen föreslår att låset flyttas från
riggkatalogen till **trion** (`~/lab/riggar/TRIOLÅS-<a|b>`), eftersom trion är
den delade resursen — två riggkataloger kan hålla var sitt `RIGGLÅS` och ändå
sikta på samma portar. Förslaget innehåller också atomär tagning med
`set -o noclobber` (`O_EXCL`), giltighetstid i låset, och `ExecStopPost` som
släpper låset även när riggen dör på sitt tidstak.

**Det är ett förslag och inget annat.** Det är inte installerat, inga
`TRIOLÅS`-filer finns på riggen (kontrollerat 2026-08-25T14:25:04Z), och
införandet är ett ägarbeslut. Det står här för att den som läser `RIGGLÅS` i
en riggkatalog ska veta att konventionen kan komma att flyttas — inte för att
den redan har flyttats.

---

## Portklasserna, kort

Klassen i valvet säger vad ett skript ska **göra**, inte vad porten heter.
Fullständig tabell och ordförråd står i [portvalvet](PORTAR.md); det här är
kartan mellan riggtyp och klass:

| riggtyp | klass i valvet | innebörd |
|---|---|---|
| huvudmätriggen, lab-triorna | `lab` | tillåt |
| huvudmätriggen, RA | `ra-kontroll` | neka utan uttrycklig order |
| portvaktens deploy-par | `deploy` | neka — de reses av deploy-kedjan |
| testsvitens servrar och klienter | `testsvit` | endast testsvitens runner |
| parallellriggar | `rigg` | endast den namngivna riggens egna skript |
| främmande tjänster | `forbjuden`, `orord` | neka |

`forbjuden` och `orord` skiljs åt av *varför*: `orord` är levande och pid-ägd
av någon annan just nu, `forbjuden` är reserverad oavsett om den är uppe eller
nere. Att en `forbjuden` port är tyst betyder inte att den är ledig — den som
tar en tyst förbjuden port tar den port ägaren kommer att starta.

Samma sak gäller `rigg`: en parallellrigg lämnas normalt nere mellan
körningar, så dess portar är tysta nästan jämt.

---

## Regler som gäller alla riggtyper

### Självallokering är förbjuden

**Ägarbeslut 2026-08-25.** Ett skript väljer aldrig ett portnummer själv.

> En port som saknas i [portvalvet](PORTAR.md) ⇒ skriptet vägrar.

Att i stället bokföra en självvald port i riggens eget manifest är inte en
dokumenterad avvikelse utan ett regelbrott: nästa säte läser valvet, ser en
ledig port, och tar en som är i bruk. Ordningen är alltid rad i valvet först,
användning sedan.

Stöter du på en oredovisad port i en mall du kopierar: byt den **i din kopia**,
och rapportera avvikelsen. Alltid båda — en tyst rättning i kopian lämnar
mallen fel för nästa som kopierar den.

Den som står på riggen och inte i ett arbetsträd läser valvet så här:

```sh
git fetch origin && git show origin/main:docs/PORTAR.md
```

### `systemctl enable`, `disable`, `daemon-reload` och `mask` rörs aldrig

Armerade drop-ins aktiveras **retroaktivt** av en reload. Den knappen tillhör
riggsätet och ingen annan. `testsuite/rig/aterstall.py` vägrar verben oavsett
flaggor, och ett test letar efter dem i skriptfilerna.

### En grind som ingen sett falla räknas som frånvarande

Innan en rigg räknas som rest ska varje vägransväg i `start.sh` och `stop.sh`
ha setts fälla på känt trasig indata: befintligt lås, lyssnare på spelporten,
förvanskat `SHA256-KONTRAKT`. Efterläget återställs exakt och verifieras tyst.
Ett grönt resultat från ett skript som aldrig negativkontrollerats är
overifierat, inte godkänt.

### En rigg som inte svarar rapporteras aldrig som klar

`systemd-run` återvänder när **uniten** är startad, inte när **servern** lever.
Utan livsgrind går det utmärkt att skriva «riggen klar», rc=0, med noll
lyssnare och en failad unit. Livsgrinden väntar på MainPID i `/proc` **och** en
lyssnare på spelporten, och faller direkt om uniten går till
`failed`/`inactive`. En failad transient unit ligger dessutom kvar under sitt
namn och vägras av nästa `systemd-run` — `reset-failed` hör därför till
städningen, inte bara till felhanteringen.

### Kärnbandet är fotavtrycksbegränsning, inte mätisolering

En parallellrigg kan pinnas till ett kärnband med `taskset` för att begränsa
vad den stör. Det gör den **inte** isolerad: om den samtidiga mätningens
processer är opinnade kan de fortfarande vandra in på samma kärnor. Samdrift
kräver antingen pinning av båda sidor eller en ärlig deklaration med
före/efter-mått. Att kalla ett kärnband för isolering är att lova något det
inte ger.
