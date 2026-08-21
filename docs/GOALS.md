# docs/GOALS.md

**UTKAST — attesteras av ägaren.** Detta dokument formulerar projektets
mätbara mål utifrån kallstartsgranskningen 2026-08-21
(`WORK_LOGS/2026-08-21-kallstartsgranskning.md`, P0-1) och kanonen i
`reference/ra-room/README.md`. Det ersätter ingen tidigare ägarordning;
det gör explicit vad som redan var underförstått.

## Mål A — RA-rummet ≥ 99 % lyckade försök per ben

Mätt mot kanonen: `reference/ra-room/README.md` (rummets gränser,
in/upp/ut-mål och facittider — "@ 91a6e34").

- **Ben:** UT→ring, UT→tunnel, UT→väst/sng, IN ring→topp, IN
  tunnel→topp, IN väst→topp (samma sex ben som facittabellen i
  kanonen).
- **Acceptanskriterium:** inte falla, inte fastna. Tider är
  sekundära — de finns i kanonen som referens, inte som gate.
- **Mätpunkt:** fork/main. Ett ben räknas godkänt när ≥ 99 % av
  försöken i en mätomgång klarar acceptanskriteriet för det benet.
- **Status vid granskningstillfället:** ej mätbar på main — main
  saknar mätverktygslådan (`testsuite/tools/`, 520 filer) som endast
  finns på grenar. Se `docs/BRANCHES.md`.

## Mål B — ring2quad-kedjan 12/12 hela kedjor

- **Mätpunkt:** fork/main.
- **Acceptanskriterium:** 12 av 12 hela kedjor (ring→quad, komplett
  rutt utan avbrott) godkända i en mätomgång.
- **Status vid granskningstillfället:** arbetet ligger på
  `origin/ring2quad` (206 commits före main), inte på main. Se
  `docs/BRANCHES.md` för grenens status.

## Vad detta INTE är

Detta dokument sätter inte prioritet mellan mål A och B, ändrar inte
recepten i `reference/recept/`, och rör inte facit eller mätgrenarna.
Det är en nedskrivning av mål som annars bara fanns i rollfilernas
underförstådda språk ("RA-99 %-spåret", "K2-baslinjen").

---
Källor: `WORK_LOGS/2026-08-21-kallstartsgranskning.md` P0-1,
`reference/ra-room/README.md`, `docs/baseline/README.md`.
