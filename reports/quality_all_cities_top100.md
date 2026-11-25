# Intrinsic City Quality Index — Phase 1 Tier A Top 100

Three-board, absolute-anchor, no-imputation GHSL urban-centre cohort (cost/affordability, airports, life expectancy, physicians, road deaths, and WorldClim precipitation excluded). Phase 1 active construct coverage = **0.42** of the 100% intrinsic reference vector (58% named holes: full thermal UTCI, full WorldCover polygons, urban form, hospital care friction, non-heat hazards, gated transit, UNESCO heritage; GHSL UTCI>32 heat days are shown as a diagnostic column only). GHSL urban centres: **7,018** (Tier A: **3,910**); suppressed 15 km satellites: **865**; separate unmatched towns: **2,506**.

## Continent distribution vs GHSL centre base rates

| Continent | Top 100 | Top 100 Share | All GHSL Centres | Base Rate |
|---|---:|---:|---:|---:|
| Europe (EU) | 77 | 77.0% | 1059 | 15.1% |
| North America (NA) | 16 | 16.0% | 618 | 8.8% |
| Oceania (OC) | 7 | 7.0% | 35 | 0.5% |
| Asia (AS) | 0 | 0.0% | 3429 | 48.9% |
| Africa (AF) | 0 | 0.0% | 1215 | 17.3% |
| South America (SA) | 0 | 0.0% | 662 | 9.4% |

## Mandatory release diagnostics

- **Log GDP/capita correlation (Tier A):** composite `r = 0.783`, biophysical `r = 0.637`, institutional (country-level) `r = 0.467`, broadband `r = 0.688`.
- **Pathological checks:** hot cities (≥150 UTCI>32 days) with thermal_heat ≥50 = `0`; hot cities in top decile = `6`; China zero-POI rows falsely marked observed = `0`; unique GHSL centres in top 100 = `100`.

## Top 100 (Tier A GHSL urban centres)

| # | City | Country | Continent | Quality | Biophysical | Institutional | Broadband | Air | Shore | Heat | Construct Cov |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Umeå | SE | Europe (EU) | 98.9 | 99.8 | 98.1 | 97.4 | 100.0 | 99.4 | 100.0 | 0.42 |
| 2 | Jyväskylä | FI | Europe (EU) | 98.8 | 99.3 | 98.0 | 99.5 | 100.0 | 97.8 | 99.2 | 0.42 |
| 3 | Norrköping | SE | Europe (EU) | 98.7 | 98.9 | 98.1 | 100.0 | 98.3 | 100.0 | 99.2 | 0.42 |
| 4 | Oulu | FI | Europe (EU) | 98.6 | 99.0 | 98.0 | 99.2 | 98.5 | 100.0 | 100.0 | 0.42 |
| 5 | Tampere | FI | Europe (EU) | 98.0 | 97.5 | 98.0 | 100.0 | 97.1 | 98.5 | 99.2 | 0.42 |
| 6 | Västerås | SE | Europe (EU) | 97.9 | 97.2 | 98.1 | 100.0 | 96.3 | 99.2 | 98.3 | 0.42 |
| 7 | Trondheim | NO | Europe (EU) | 97.6 | 98.6 | 95.4 | 100.0 | 98.4 | 98.9 | 100.0 | 0.42 |
| 8 | Södertälje | SE | Europe (EU) | 97.4 | 96.2 | 98.1 | 100.0 | 94.5 | 100.0 | 100.0 | 0.42 |
| 9 | Tromsø | NO | Europe (EU) | 97.3 | 98.6 | 95.4 | 97.7 | 100.0 | 95.5 | 99.2 | 0.42 |
| 10 | Stockholm | SE | Europe (EU) | 96.9 | 95.3 | 98.1 | 100.0 | 93.1 | 100.0 | 100.0 | 0.56 |
| 11 | Jönköping | SE | Europe (EU) | 96.8 | 95.4 | 98.1 | 98.6 | 93.2 | 100.0 | 99.2 | 0.42 |
| 12 | Auckland | NZ | Oceania (OC) | 96.7 | 97.1 | 95.0 | 100.0 | 95.8 | 100.0 | 99.2 | 0.56 |
| 13 | Lahti | FI | Europe (EU) | 96.3 | 94.5 | 98.0 | 99.5 | 92.8 | 98.0 | 99.2 | 0.42 |
| 14 | Nanaimo | CA | North America (NA) | 96.2 | 100.0 | 89.8 | 99.1 | 100.0 | 100.0 | 100.0 | 0.42 |
| 15 | Halifax | CA | North America (NA) | 96.2 | 99.8 | 89.8 | 100.0 | 99.7 | 100.0 | 93.3 | 0.42 |
| 16 | Örebro | SE | Europe (EU) | 95.6 | 92.9 | 98.1 | 99.8 | 96.1 | 85.9 | 98.3 | 0.42 |
| 17 | Galway | IE | Europe (EU) | 95.1 | 93.9 | 95.5 | 99.0 | 91.0 | 100.0 | 98.3 | 0.42 |
| 18 | Wellington | NZ | Oceania (OC) | 95.0 | 93.9 | 95.0 | 100.0 | 91.1 | 100.0 | 100.0 | 0.42 |
| 19 | Maple Ridge | CA | North America (NA) | 94.9 | 97.3 | 89.8 | 99.4 | 96.1 | 100.0 | 88.3 | 0.42 |
| 20 | Derry | GB | Europe (EU) | 94.7 | 91.6 | 98.8 | 96.2 | 87.7 | 100.0 | 100.0 | 0.42 |
| 21 | Linköping | SE | Europe (EU) | 94.7 | 91.1 | 98.1 | 99.9 | 96.3 | 80.1 | 98.3 | 0.42 |
| 22 | Hamilton | NZ | Oceania (OC) | 94.7 | 93.4 | 95.0 | 99.3 | 90.3 | 100.0 | 95.8 | 0.42 |
| 23 | Bergen | NO | Europe (EU) | 94.5 | 93.1 | 95.4 | 98.5 | 91.4 | 96.5 | 100.0 | 0.42 |
| 24 | Peterborough | CA | North America (NA) | 94.3 | 96.3 | 89.8 | 99.2 | 94.6 | 100.0 | 89.2 | 0.42 |
| 25 | Barrie | CA | North America (NA) | 94.1 | 96.0 | 89.8 | 99.1 | 94.2 | 100.0 | 83.3 | 0.42 |
| 26 | Helsinki | FI | Europe (EU) | 94.0 | 90.0 | 98.0 | 100.0 | 88.2 | 93.9 | 98.3 | 0.56 |
| 27 | Aberdeen | GB | Europe (EU) | 93.8 | 89.8 | 98.8 | 96.5 | 86.8 | 96.1 | 100.0 | 0.42 |
| 28 | Sault Ste. Marie | CA | North America (NA) | 93.8 | 96.1 | 89.8 | 95.9 | 94.8 | 98.7 | 97.5 | 0.42 |
| 29 | Kingston | CA | North America (NA) | 93.7 | 95.0 | 89.8 | 99.7 | 92.6 | 100.0 | 86.7 | 0.42 |
| 30 | Dundee | GB | Europe (EU) | 93.6 | 89.7 | 98.8 | 95.7 | 85.3 | 99.0 | 100.0 | 0.42 |
| 31 | Carlisle | GB | Europe (EU) | 93.6 | 89.2 | 98.8 | 97.5 | 84.2 | 100.0 | 99.2 | 0.42 |
| 32 | Victoria | CA | North America (NA) | 93.6 | 94.8 | 89.8 | 99.6 | 100.0 | 83.6 | 97.5 | 0.42 |
| 33 | Palmerston North | NZ | Oceania (OC) | 93.5 | 90.9 | 95.0 | 100.0 | 89.5 | 94.0 | 97.5 | 0.42 |
| 34 | Morecambe | GB | Europe (EU) | 93.3 | 88.6 | 98.8 | 97.8 | 83.2 | 100.0 | 100.0 | 0.42 |
| 35 | Biel/Bienne | CH | Europe (EU) | 93.2 | 87.5 | 99.3 | 100.0 | 82.2 | 99.0 | 85.0 | 0.42 |
| 36 | Tartu | EE | Europe (EU) | 93.1 | 90.5 | 94.7 | 100.0 | 86.1 | 100.0 | 95.0 | 0.42 |
| 37 | Stavanger | NO | Europe (EU) | 93.1 | 90.0 | 95.4 | 100.0 | 85.9 | 98.9 | 100.0 | 0.42 |
| 38 | Littlehampton | GB | Europe (EU) | 93.1 | 87.9 | 98.8 | 98.8 | 82.4 | 99.8 | 95.8 | 0.42 |
| 39 | Napier | NZ | Oceania (OC) | 93.1 | 90.5 | 95.0 | 98.8 | 91.4 | 88.5 | 97.5 | 0.42 |
| 40 | Palencia | ES | Europe (EU) | 93.0 | 88.5 | 97.5 | 99.5 | 83.1 | 99.9 | 48.3 | 0.42 |
| 41 | Exeter | GB | Europe (EU) | 93.0 | 88.2 | 98.8 | 96.6 | 82.7 | 100.0 | 97.5 | 0.42 |
| 42 | Lower Hutt | NZ | Oceania (OC) | 93.0 | 90.0 | 95.0 | 100.0 | 90.1 | 89.8 | 100.0 | 0.42 |
| 43 | Helsingborg | SE | Europe (EU) | 93.0 | 87.9 | 98.1 | 100.0 | 82.2 | 100.0 | 99.2 | 0.42 |
| 44 | Namur | BE | Europe (EU) | 93.0 | 87.9 | 98.4 | 99.1 | 82.2 | 100.0 | 90.8 | 0.42 |
| 45 | Fredrikstad | NO | Europe (EU) | 92.9 | 89.7 | 95.4 | 100.0 | 87.6 | 94.1 | 99.2 | 0.42 |
| 46 | Hastings | GB | Europe (EU) | 92.9 | 87.3 | 98.8 | 99.8 | 81.4 | 100.0 | 95.8 | 0.42 |
| 47 | Tauranga | NZ | Oceania (OC) | 92.9 | 89.9 | 95.0 | 100.0 | 88.2 | 93.6 | 99.2 | 0.42 |
| 48 | Orléans | FR | Europe (EU) | 92.9 | 89.3 | 95.9 | 100.0 | 84.2 | 100.0 | 80.0 | 0.42 |
| 49 | Folkestone | GB | Europe (EU) | 92.9 | 87.2 | 98.8 | 100.0 | 81.3 | 100.0 | 97.5 | 0.42 |
| 50 | Blackpool | GB | Europe (EU) | 92.9 | 87.7 | 98.8 | 97.6 | 82.0 | 100.0 | 100.0 | 0.42 |
| 51 | Århus | DK | Europe (EU) | 92.8 | 86.5 | 99.8 | 100.0 | 80.2 | 100.0 | 98.3 | 0.42 |
| 52 | Besançon | FR | Europe (EU) | 92.8 | 89.5 | 95.9 | 98.1 | 85.3 | 98.4 | 71.7 | 0.42 |
| 53 | Eastbourne | GB | Europe (EU) | 92.8 | 87.1 | 98.8 | 100.0 | 81.0 | 100.0 | 95.8 | 0.42 |
| 54 | Drammen | NO | Europe (EU) | 92.8 | 89.4 | 95.4 | 100.0 | 84.4 | 100.0 | 100.0 | 0.42 |
| 55 | Newcastle upon Tyne | GB | Europe (EU) | 92.8 | 87.7 | 98.8 | 97.0 | 82.0 | 100.0 | 99.2 | 0.42 |
| 56 | Swansea | GB | Europe (EU) | 92.8 | 87.4 | 98.8 | 98.2 | 81.5 | 100.0 | 97.5 | 0.42 |
| 57 | Eugene | US | North America (NA) | 92.7 | 100.0 | 79.8 | 99.4 | 100.0 | 100.0 | 63.3 | 0.42 |
| 58 | Bournemouth | GB | Europe (EU) | 92.7 | 87.3 | 98.8 | 98.1 | 81.4 | 100.0 | 97.5 | 0.42 |
| 59 | St. John's | CA | North America (NA) | 92.7 | 93.7 | 89.8 | 97.0 | 93.5 | 94.1 | 100.0 | 0.42 |
| 60 | Paignton | GB | Europe (EU) | 92.7 | 87.5 | 98.8 | 96.8 | 81.7 | 100.0 | 99.2 | 0.42 |
| 61 | Salem | US | North America (NA) | 92.6 | 100.0 | 79.8 | 98.7 | 100.0 | 100.0 | 51.7 | 0.42 |
| 62 | Turku | FI | Europe (EU) | 92.6 | 87.4 | 98.0 | 99.4 | 94.2 | 73.0 | 98.3 | 0.42 |
| 63 | Tours | FR | Europe (EU) | 92.6 | 88.7 | 95.9 | 100.0 | 83.4 | 100.0 | 79.2 | 0.42 |
| 64 | Seattle | US | North America (NA) | 92.5 | 99.4 | 79.8 | 100.0 | 99.1 | 100.0 | 87.5 | 0.56 |
| 65 | Brighton | GB | Europe (EU) | 92.5 | 86.8 | 98.8 | 98.0 | 80.7 | 100.0 | 95.8 | 0.42 |
| 66 | London | GB | Europe (EU) | 92.4 | 86.4 | 98.8 | 99.3 | 80.1 | 100.0 | 95.0 | 0.56 |
| 67 | Southport | GB | Europe (EU) | 92.4 | 87.5 | 98.8 | 94.4 | 81.9 | 99.5 | 100.0 | 0.42 |
| 68 | Southampton | GB | Europe (EU) | 92.4 | 86.4 | 98.8 | 99.1 | 80.1 | 100.0 | 95.0 | 0.42 |
| 69 | Aalborg | DK | Europe (EU) | 92.3 | 85.6 | 99.8 | 99.5 | 79.6 | 98.6 | 98.3 | 0.42 |
| 70 | Konstanz | DE | Europe (EU) | 92.3 | 86.1 | 99.3 | 98.6 | 79.5 | 100.0 | 84.2 | 0.42 |
| 71 | Brantford | CA | North America (NA) | 92.3 | 92.3 | 89.8 | 100.0 | 88.6 | 100.0 | 75.8 | 0.42 |
| 72 | Liverpool | GB | Europe (EU) | 92.3 | 86.3 | 98.8 | 99.0 | 79.9 | 100.0 | 99.2 | 0.56 |
| 73 | Belfast | GB | Europe (EU) | 92.3 | 87.5 | 98.8 | 93.5 | 83.4 | 96.3 | 100.0 | 0.42 |
| 74 | Albacete | ES | Europe (EU) | 92.3 | 87.2 | 97.5 | 98.7 | 82.5 | 97.3 | 15.0 | 0.42 |
| 75 | Saarbrücken | DE | Europe (EU) | 92.2 | 87.0 | 99.3 | 94.0 | 81.0 | 100.0 | 85.0 | 0.42 |
| 76 | Schwerin | DE | Europe (EU) | 92.2 | 86.3 | 99.3 | 97.2 | 79.9 | 100.0 | 93.3 | 0.42 |
| 77 | Portland | US | North America (NA) | 92.2 | 99.0 | 79.8 | 99.8 | 98.5 | 100.0 | 61.7 | 0.56 |
| 78 | Plymouth | GB | Europe (EU) | 92.2 | 86.8 | 98.8 | 95.8 | 80.7 | 100.0 | 98.3 | 0.42 |
| 79 | Bognor Regis | GB | Europe (EU) | 92.2 | 86.2 | 98.8 | 98.7 | 79.7 | 100.0 | 95.0 | 0.42 |
| 80 | Cambridge | CA | North America (NA) | 92.1 | 92.4 | 89.8 | 98.1 | 88.9 | 100.0 | 81.7 | 0.42 |
| 81 | Sunderland | GB | Europe (EU) | 92.1 | 86.6 | 98.8 | 96.6 | 82.6 | 95.1 | 99.2 | 0.42 |
| 82 | Hartlepool | GB | Europe (EU) | 92.1 | 86.5 | 98.8 | 96.1 | 81.0 | 98.4 | 99.2 | 0.42 |
| 83 | Lowestoft | GB | Europe (EU) | 92.1 | 85.7 | 98.8 | 99.7 | 79.0 | 100.0 | 100.0 | 0.42 |
| 84 | Reading | GB | Europe (EU) | 92.0 | 85.7 | 98.8 | 99.7 | 79.0 | 100.0 | 95.0 | 0.42 |
| 85 | Logroño | ES | Europe (EU) | 92.0 | 86.5 | 97.5 | 99.6 | 80.3 | 100.0 | 56.7 | 0.42 |
| 86 | Trier | DE | Europe (EU) | 92.0 | 86.8 | 99.3 | 92.9 | 80.7 | 100.0 | 89.2 | 0.42 |
| 87 | Swords | IE | Europe (EU) | 92.0 | 87.8 | 95.5 | 99.9 | 83.9 | 96.1 | 100.0 | 0.42 |
| 88 | Weston-super-Mare | GB | Europe (EU) | 92.0 | 86.0 | 98.8 | 97.8 | 79.4 | 100.0 | 96.7 | 0.42 |
| 89 | Liège | BE | Europe (EU) | 91.9 | 85.8 | 98.4 | 98.9 | 79.2 | 100.0 | 90.0 | 0.42 |
| 90 | Talavera de la Reina | ES | Europe (EU) | 91.9 | 86.2 | 97.5 | 100.0 | 79.7 | 100.0 | 20.8 | 0.42 |
| 91 | Hereford | GB | Europe (EU) | 91.9 | 86.1 | 98.8 | 96.6 | 79.6 | 100.0 | 96.7 | 0.42 |
| 92 | Bamberg | DE | Europe (EU) | 91.9 | 87.6 | 99.3 | 88.5 | 81.8 | 100.0 | 84.2 | 0.42 |
| 93 | Tallinn | EE | Europe (EU) | 91.9 | 88.1 | 94.7 | 100.0 | 83.3 | 98.4 | 96.7 | 0.42 |
| 94 | Shrewsbury | GB | Europe (EU) | 91.9 | 85.8 | 98.8 | 97.7 | 79.1 | 100.0 | 96.7 | 0.42 |
| 95 | Basel | CH | Europe (EU) | 91.9 | 84.9 | 99.3 | 100.0 | 78.4 | 99.0 | 70.8 | 0.42 |
| 96 | Oslo | NO | Europe (EU) | 91.8 | 87.5 | 95.4 | 100.0 | 84.9 | 93.2 | 100.0 | 0.42 |
| 97 | Caen | FR | Europe (EU) | 91.8 | 87.3 | 95.9 | 99.4 | 81.4 | 100.0 | 91.7 | 0.42 |
| 98 | Vancouver | CA | North America (NA) | 91.8 | 91.3 | 89.8 | 100.0 | 96.6 | 80.0 | 93.3 | 0.56 |
| 99 | Runcorn | GB | Europe (EU) | 91.8 | 85.4 | 98.8 | 98.6 | 78.6 | 100.0 | 98.3 | 0.42 |
| 100 | Great Yarmouth | GB | Europe (EU) | 91.8 | 85.6 | 98.8 | 97.9 | 78.8 | 100.0 | 100.0 | 0.42 |
