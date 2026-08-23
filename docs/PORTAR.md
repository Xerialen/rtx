# Portar på mätriggen — kanonisk lista

**Detta är den enda giltiga portlistan i repot.** Datum: **2026-08-23**.
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
| 27700 | forbjuden | spel | t3 | se «Öppen punkt 1» |
| 27960 | lab | ctl | lab-a | lab-trio A |
| 27970 | lab | ctl | lab-b | lab-trio B |
| 27980 | forbjuden | ctl | rtxfast | kontrollkanalen till 27530; se «Öppen punkt 2» |
| 27990 | ra-kontroll | ctl | fasttrack-ra | RA-riggens kontrollkanal; rörs endast på uttrycklig order |
| 27991 | forbjuden | ctl | main | kontrollkanalen till 27550 |
| 27996 | deploy | ctl | tbx-d1 | portvaktens atomära par med 27592 |
| 27998 | deploy | ctl | tbx-d3 | portvaktens atomära par med 27594 |
| 28000 | orord | qtv | ktx | `qtv.bin` pid 1333, syskon till KTX-paret |
| 28502 | orord | spel | ktx | `mvdsv -port 28502 -game ktx` pid 1331 |
| 28503 | orord | spel | ktx | `mvdsv -port 28503 -game ktx` pid 1332 |
| 29570 | lab | qtv | lab-b | lab-trio B |
| 29580 | lab | qtv | lab-a | lab-trio A |

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

Att en `forbjuden` port är tyst betyder inte att den är ledig. `27550` är
nere sedan 2026-08-15 och är fortfarande förbjuden: den som tar en tyst
förbjuden port tar den port ägaren kommer att starta.

### Ordförråd — `roll`

`spel` (mvdsv `-port`), `ctl` (kontrollkanal), `qtv`.

En lab-trio är alltid alla tre ur samma `grupp`. Halva trior är den enda
sortens misstag som annars hade kunnat resa rätt rigg på fel portar.

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

Motsägelsen är **inte** avgjord här. Exempelkonfigurationen har fått
värdet borttaget — fältet är obligatoriskt och tomt som filens övriga
obligatoriska fält — så att ingen kopierar ett portnummer som kanske är
fel. Vilken port T3-riggen ska stå på är ett ägar-/Fable-beslut, och när
det är fattat är det den här tabellen som ändras. Ingen annan fil.

## Öppen punkt 2 — `27980` och T1/T2

`testsuite/config.example.toml` sätter `[server].control_port = 27980`,
och `[sweep].restart_cmd` startar om `fasttrack-server`. Det är samma
tjänst som äger den förbjudna `27530`. Testsviten är alltså skriven för
att mäta mot precis den server Hopparens riggregler förbjuder honom att
röra.

Det är en verklig kollision mellan två arbetssätt, inte ett skrivfel, och
den avgörs inte här. Tabellen nekar `27980` tills någon beslutar. Ett
skript som behöver den ska vägra och säga varför — inte gissa.

## Vad som inte är kanon

Daterade kvitton och granskningar under `docs/` och `reference/` bokför
vilka portar en viss körning använde den dagen. De är protokoll, inte
regler, och de skrivs aldrig om i efterhand. Står ett portnummer där och
ett annat här, gäller den här filen.
