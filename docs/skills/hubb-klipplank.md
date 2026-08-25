---
name: hubb-klipplank
description: Use when the owner needs a clickable link that opens a SPECIFIC recorded moment (a case, fall, event, attempt) in the hemmahubb demo player and plays it — one link per decision case, with exact demo-clock times. Covers mapping a band/attempt to its MVD, copying it to the hub, building the demo-player URL, and calibrating event times. Not for artifacts, static images, or live QTV.
---

# Hubb-klipplänk — från case till spelbar länk med tid

Beprövad kedja 2026-08-25 (falldomen F1–F3). Ägarregel: **EN länk per
beslutscase**, och hela flödet tryck→rätt klipp→spelar ska valideras
med `skarmdumpsvalidering`-skillen INNAN ägaren får länken.

## Länkformatet (verifierat)

```
http://192.168.86.34:8095/demo-player/?demoUrl=<URL-ENKODAD absolut mvd-URL>&map=<karta>&name=<etikett>
```

- Hubben = lanister:8095 (LAN; Tailscale http://100.64.0.2:8095/).
- `demoUrl` MÅSTE vara URL-enkodad absolut URL, t.ex.
  `http%3A%2F%2F192.168.86.34%3A8095%2Fdemos%2F<fil>.mvd`.
- Övriga parametrar spelaren läser: `name`, `map`, `duration`,
  `countdown`, `width`, `height`. Ingen seek-parameter finns —
  ge ägaren tidsmarkeringar i text i stället.

## Steg

1. **Identifiera bandet.** Case-id (t.ex. `grans-over-A-attempt_0003`)
   → arm + försöksnummer. Källa: buntens `data/<ARM>/attempt_NNNN.jsonl`.
2. **Mappa försök → MVD.** Läs `data/<ARM>/forsok.jsonl`; radens fält
   `nr` (globalt försöksnr) → `block` → demofilen
   `data/<ARM>/mvd/<prefix>-b<block padda 2>.mvd`; `j_block` = vilket
   försök i klippet. OBS: fältet `i` återanvänds per block — matcha på
   `nr`, aldrig på `i`.
3. **Kopiera till hubben.** Lägg kopian i
   `lanister:~/local-hub/web/demos/` (serveras som `/demos/<fil>` av
   serve.py :8099 bakom nginx-splittern :8095). Namnge beskrivande
   (`falldom-f2-pakanten-a01.mvd`). **`sha256sum` på kopia OCH original
   — måste vara identiska.** Röer ALDRIG buntens original eller SUMS.
4. **Kalibrera tiden.** Spelaren visar demoklockan överst i bild.
   Demoklockan 00:00 = blockets första försöksstart; försök N:s start =
   summan av föregående försöks `ts_start`-differenser (ur forsok.jsonl).
   Händelsens klipptid = försöksstartens offset + bandets `t`.
   Verifiera ALLTID mot demoklockan i skärmdump (falldomen:
   väggtid ≈ demotid + 2 s laddning) — anta aldrig offset.
5. **Leverera:** en länk per case + demoklocksmärke ("titta vid 00:31")
   + en rad vad ägaren ska se. Få ord, stor tydlighet.

## Negativkontroll (obligatorisk)

- Fel filnamn i `demoUrl` ⇒ spelaren ska INTE spela (verifiera 404 i
  nätverksloggen). Grönt flöde utan detta prov är overifierat.
- Verifiera i nätverksloggen att EXAKT den avsedda mvd-filen hämtas
  med 200 — inte en cachead/annan fil.

## Hårda regler

- Portvalvet (`docs/PORTAR.md` i rtx-repot) rörs inte — hubben kör
  redan; inga nya portar/tjänster får resas för detta.
- Kopior är presentation; mätkedjans proveniens ligger kvar i bunten.
  Ange alltid källbunt + sha i bokföringen.
- Länkarna kräver hemmanätet/Tailscale — säg det till ägaren.

Se även: `.claude/skills/skarmdumpsvalidering/SKILL.md` (obligatorisk
validering), minnet `hemmahubben`, `GUIDES/HANDOVER.md` (porttabellen:
8095 local-hub + demospelare, 8088 clipshot, 8080 mvd-api).
