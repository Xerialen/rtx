---
name: skarmdumpsvalidering
description: Use BEFORE delivering anything user-facing to the owner — artifacts, hub links, dashboards, web pages, clips. Validates with real rendering and screenshots: headless browser, phone viewport first, test-clicks through every promised path, 1 fps event windows, pixel-diff for playback, and eye-checking every image. A delivery without this validation is undelivered.
---

# Skärmdumpsvalidering — inget levereras osett

Ägarregel 2026-08-25 (bindande): "validera själv med skärmdump,
testklick, alltihop INNAN du ger till mig. Anta att ägaren är
dyslektiker och vill ha så få ord som möjligt och så mycket visuell
information som möjligt." Kodgranskning/Node-stub räcker INTE — den
missade att en 11,8 MB-artefakt inte visade något alls på telefon.

## Verktyget (finns redan på pinnacle)

```bash
# Engångs-setup i egen scratchpadkatalog:
npm init -y && npm install --no-audit playwright-core
# Chromium: ~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome
# Snabb stillbild utan Playwright:
google-chrome --headless=new --no-sandbox --window-size=390,844 \
  --screenshot=ut.png --virtual-time-budget=15000 "<URL eller file://...>"
```

Playwright-mall: scratchpadmönstren `falldom-e2e/verif.mjs` (klickflöde,
nätverkslogg, pixeldiff) och `sweep-f2.mjs` (1 bild/s-fönster). Launch:
`chromium.launch({executablePath: HOME+'/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome', args:['--no-sandbox']})`.

## Protokollet

1. **Telefon först.** Viewport 390×844 FÖRE desktop 1280×800 — ägaren
   läser oftast på telefon.
2. **Testklicka varje utlovad väg.** Sida-laddas ≠ fungerar. Klicka
   knappen, följ länken, öppna panelen — i headless, med nätverkslogg.
3. **Spelar det? Bevisa rörelse.** Två skärmdumpar med 5 s mellanrum,
   pixeldiff > tröskel = rörlig bild. Verifiera i nätverksloggen att
   RÄTT fil hämtades (200, exakt URL).
4. **Händelsefönster: 1 bild/s, case−3 s … case+3 s.** Räkna fram
   händelsens tid ur banddata, fota varje sekund i ett fönster med
   marginal, kalibrera mot klocka i bild (demoklocka/UI-klocka), och
   BEDÖM ur bilderna innan ägaren gör det.
5. **Ögonkontroll av varje bild.** Read-verktyget på varje PNG som
   levereras eller ligger till grund för ett påstående. Osedd bild
   skickas inte.
6. **Negativkontroll.** Kör samma harness mot en känd-dålig version/URL
   och se den FALLA (fel fil ⇒ ingen hämtning; gammal sida ⇒ block
   saknas). Grönt utan fällt negativfall är overifierat.

## Plattformsfakta (dyrköpta)

- **claude.ai-artefakter får ALDRIG URL-query-parametrar** — visaren
  släpper inte igenom dem till iframen. Bygg startpanel/meny i sidan
  i stället. Att sidans kod hanterar en parameter bevisar inte att
  parametern når sidan.
- Publicerade artefakt-URL:er kan inte renderas i headless (auth) —
  skärmdumpa källfilen och notera avvikelsen; innehållet i publicerade
  bytes verifieras separat via WebFetch.
- Tunga sidor (>2 MB, canvas/WebGL) kan vara döda på mobil trots
  perfekt headless-rendering — beslutssidor ska vara lätta, statiska
  och bildburna; tunga viewers är skrivbordsverktyg.

## Leveransformen till ägaren

Bilder i chatten (SendUserFile, render) + få enkla ord + tydliga
alternativ per beslut. Skärmdumpsbeviset följer med leveransen.
Testet: "Går det att förstå vad som pågår ur bilderna ensamma?"

Se även: `.claude/skills/hubb-klipplank/SKILL.md`, minnet
`feedback-artefakt-sjalvvalidering`.
