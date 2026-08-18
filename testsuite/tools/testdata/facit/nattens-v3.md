# FACIT v3 — TIMPROV G/O/main samtidigt — FÖRSEGLAT FÖRE KÖRNING

Ägarorder 18/8 ~05:2x lokal: "Kör en timme på g och o och main samtidigt."
Startordern för main 27550/27991 är därmed ägarens (bokförs; endast denna
körning — main stängs efter armen om inget annat sägs).

## Grund
- Armar, SAMTIDIGA (start inom 60 s): d1 = fork + G-op (slut 5983/48215,
  nivå-2 38154cd7…) · d3 = fork + O-op (5983/48213, nivå-2 8297ada3…) ·
  main = main-servern med SIN binär (referensarm; endast geometri +
  observerade utfall, som T1h-r1).
- Fork-binär: qwprogs 65be9bda… (3187fa6) + mvdsv 85846500… på d1/d3.
  Recepten paav-g-v1/paav-o-v1 (Sol-kontrasignerade, SEALED_DEPLOYABLE).
- Körformat: T1h-formatet, 60 min per arm, kedjade ben, LIKA MÅNGA BEN
  PER HOPP i alla armar (ägarens stående krav). Ingen annan trafik.

## Frågor domen SKA besvara (i ordning)
F1. G mot O (samtidiga armar): skillnad i korridorfall och totala H?
F2. G och O mot main (samtidig referens): kvarstår förbättringen i
    T1h-skala?
F3. Korridorfallets frekvens per arm med KI — når någon variant K1=0
    på timskala?

## Krav per fork-arm (K1–K5 som v2; korridordef oförändrad, alla bentyper)
Domskala per variant: BÄTTRE = K1–K5 + ej sämre än den andra varianten;
OFÖRÄNDRAT/OSÄKERT = förbättring mot main men G/O-frågan fortsatt öppen;
SÄMRE = K3/K4 bruten eller signifikant sämre än den andra fork-armen.
Main döms inte — den är referens.

## Ogiltighet
Fel graf/binär per arm, brutet lås, ej samtidig start (>60 s), recept
utan kvitto, dataset utan manifest, olika benfördelning mellan armar ⇒
den armen OGILTIG. Roller som v1/v2 (QA dömer, grok2 räknar om, Fable
förseglar/sammanställer, dömer inte).
