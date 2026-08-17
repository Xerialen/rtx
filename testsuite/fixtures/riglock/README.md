# Delade rigglås-fixturer — korskontrakt motor ⇄ runner

Låsfilen `~/lab/.rig-lock` har **två** läsare: motorns `fixa_require_lock_at`
(`crates/rtx-game/src/control.rs`) och runnerns `parse_deploy_lock`
(`testsuite/tools/d_deploy.py`). Under DOM MONTERING-V296RAM-2 visade det sig att
de var ömsesidigt oförenliga: den form som gjorde runnern nöjd — åtta
`nyckel=värde`-rader — gav motorn `owner=fable` som första fält och stängde varje
`apply`/`undo`. Ingen av läsarna kunde se det ensam, för ingen av dem läste den
andras krav.

Filerna här är den delade sanningen. **Båda sidor pekar sina tester på samma
byte**, så en ändring i endera läsaren som bryter kontraktet faller i test i
stället för på riggen.

| fixtur | form | motorn | runnern |
|---|---|---|---|
| `kampanj-atta-falt.lock` | åtta `nyckel=värde`-rader (Fables beslut) | accepterar `token=`-värdet | parsar alla åtta fälten |
| `kampanj-crlf.lock` | samma åtta fält, **CRLF**-bytes | samma token via `str::lines()` | samma token via `_rig_lock_lines` |
| `brygga-bar-forsta-rad.lock` | bar token på rad 1 + samma åtta fält | accepterar samma värde | hoppar över rad 1 (saknar `=`) och läser fälten |
| `arv-enrad.lock` | gammalt enradslås `fable 1` | accepterar hela kroppen och första fältet | avvisas (saknar obligatoriska fält) |
| `motsagelsefull-tva-token.lock` | två olika `token=`-rader | **vägrar allt** | — |

`kampanj-crlf.lock` är markerad `binary` i `.gitattributes` så
`* text=auto eol=lf` inte omskrivs till LF. Båda sidornas tester
asserterar att filen fortfarande innehåller CRLF-bytes.

## Motorns regler, i auktoritetsordning

1. Finns en `token=`-rad **är den token**, och inget annat accepteras. Ett lås som
   namnger sin token får inte kunna öppnas av vad som råkar stå först i filen.
2. Saknas `token=` gäller de gamla formerna: hela den trimmade kroppen, eller dess
   första whitespace-fält. Det är enradslåsen, och de lever kvar.
3. Två olika `token=`-rader säger inte vilken som gäller ⇒ vägran.

Radslut är `str::lines()`: bara LF och CRLF. Ensamt CR är **inte**
radbrytning (Python `splitlines()` är det). Trailing whitespace trimmas som
Rust `trim()`. Runnern speglar båda.

Fältvärdena nedan är påhittade och får aldrig vara ett riktigt kampanjtoken —
fixturerna ligger i repot.

## Var testerna sitter

- Motorn: `crates/rtx-game/src/control.rs`, `mod tests`, `riglock_*`-testerna
  (inkl. `riglock_crlf_*`) plus `rtx-ctlproto` `rig_lock_line_endings_*`.
- Runnern: `testsuite/tools/test_riglock_contract.py` mot samma katalog.
