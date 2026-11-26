# Intrinsic City Quality Index — Phase 2 Tier A Top 100

Three-board, absolute-anchor, no-imputation GHSL urban-centre cohort (cost/affordability, airports, life expectancy, physicians, road deaths, and WorldClim precipitation excluded). Phase 2 active construct coverage = **0.62** of the 100% intrinsic reference vector (Air 15%, WorldCover 10 m Greenery 14%, Waterfront 7%, Homicide 9%, JMP WASH 6%, UNESCO Heritage 6%, Broadband 5%; 38% named holes: full thermal UTCI, urban form, hospital care friction, non-heat hazards, gated transit). GHSL urban centres: **7,018** (Tier A: **3,910**); suppressed 15 km satellites: **865**; separate unmatched towns: **2,506**.

## Continent distribution vs GHSL centre base rates

| Continent | Top 100 | Top 100 Share | All GHSL Centres | Base Rate |
|---|---:|---:|---:|---:|
| Europe (EU) | 76 | 76.0% | 1059 | 15.1% |
| North America (NA) | 21 | 21.0% | 618 | 8.8% |
| Oceania (OC) | 3 | 3.0% | 35 | 0.5% |
| Asia (AS) | 0 | 0.0% | 3429 | 48.9% |
| Africa (AF) | 0 | 0.0% | 1215 | 17.3% |
| South America (SA) | 0 | 0.0% | 662 | 9.4% |

## Mandatory release diagnostics

- **Log GDP/capita correlation (Tier A):** composite `r = 0.713`, biophysical `r = 0.577`, institutional (country-level) `r = 0.467`, broadband `r = 0.688`.
- **Pathological checks:** hot cities (≥150 UTCI>32 days) with thermal_heat ≥50 = `0`; hot cities in top decile = `21`; China zero-POI rows falsely marked observed = `0`; unique GHSL centres in top 100 = `100`.

## Top 100 (Tier A GHSL urban centres)

| # | City | Country | Continent | Quality | Biophysical | Institutional | Services | Air | Green | Shore | Heritage | Broadband |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Stockholm | SE | Europe (EU) | 96.4 | 97.1 | 98.1 | 91.8 | 93.1 | 100.0 | 100.0 | 85.0 | 100.0 |
| 2 | Jyväskylä | FI | Europe (EU) | 96.3 | 99.6 | 98.0 | 83.4 | 100.0 | 100.0 | 97.8 | 70.0 | 99.5 |
| 3 | Helsinki | FI | Europe (EU) | 94.5 | 93.9 | 98.0 | 91.8 | 88.2 | 100.0 | 93.9 | 85.0 | 100.0 |
| 4 | Umeå | SE | Europe (EU) | 93.5 | 99.9 | 98.1 | 66.1 | 100.0 | 100.0 | 99.4 | 40.0 | 97.4 |
| 5 | Bergen | NO | Europe (EU) | 93.4 | 95.8 | 95.4 | 82.9 | 91.4 | 100.0 | 96.5 | 70.0 | 98.5 |
| 6 | Oulu | FI | Europe (EU) | 93.3 | 99.4 | 98.0 | 66.9 | 98.5 | 100.0 | 100.0 | 40.0 | 99.2 |
| 7 | Tampere | FI | Europe (EU) | 92.8 | 98.5 | 98.0 | 67.3 | 97.1 | 100.0 | 98.5 | 40.0 | 100.0 |
| 8 | Trondheim | NO | Europe (EU) | 92.6 | 99.1 | 95.4 | 67.3 | 98.4 | 100.0 | 98.9 | 40.0 | 100.0 |
| 9 | Biel/Bienne | CH | Europe (EU) | 92.5 | 92.4 | 99.3 | 83.6 | 82.2 | 100.0 | 99.0 | 70.0 | 100.0 |
| 10 | Södertälje | SE | Europe (EU) | 92.4 | 97.7 | 98.1 | 67.3 | 94.5 | 100.0 | 100.0 | 40.0 | 100.0 |
| 11 | Namur | BE | Europe (EU) | 92.3 | 92.6 | 98.4 | 83.2 | 82.2 | 100.0 | 100.0 | 70.0 | 99.1 |
| 12 | Västerås | SE | Europe (EU) | 92.1 | 97.2 | 98.1 | 67.3 | 96.3 | 97.1 | 99.2 | 40.0 | 100.0 |
| 13 | London | GB | Europe (EU) | 92.1 | 86.9 | 98.8 | 99.7 | 80.1 | 87.7 | 100.0 | 100.0 | 99.3 |
| 14 | Jönköping | SE | Europe (EU) | 92.0 | 97.2 | 98.1 | 66.6 | 93.2 | 100.0 | 100.0 | 40.0 | 98.6 |
| 15 | Konstanz | DE | Europe (EU) | 91.9 | 91.5 | 99.3 | 83.0 | 79.5 | 100.0 | 100.0 | 70.0 | 98.6 |
| 16 | Tromsø | NO | Europe (EU) | 91.8 | 98.2 | 95.4 | 66.2 | 100.0 | 97.5 | 95.5 | 40.0 | 97.7 |
| 17 | Lahti | FI | Europe (EU) | 91.7 | 96.6 | 98.0 | 67.0 | 92.8 | 100.0 | 98.0 | 40.0 | 99.5 |
| 18 | Trier | DE | Europe (EU) | 91.7 | 91.9 | 99.3 | 80.4 | 80.7 | 100.0 | 100.0 | 70.0 | 92.9 |
| 19 | Nanaimo | CA | North America (NA) | 91.6 | 100.0 | 89.8 | 66.9 | 100.0 | 100.0 | 100.0 | 40.0 | 99.1 |
| 20 | Halifax | CA | North America (NA) | 91.6 | 99.9 | 89.8 | 67.3 | 99.7 | 100.0 | 100.0 | 40.0 | 100.0 |
| 21 | Tallinn | EE | Europe (EU) | 91.6 | 92.7 | 94.7 | 83.6 | 83.3 | 100.0 | 98.4 | 70.0 | 100.0 |
| 22 | Besançon | FR | Europe (EU) | 91.5 | 92.3 | 95.9 | 82.8 | 85.3 | 96.6 | 98.4 | 70.0 | 98.1 |
| 23 | Bern | CH | Europe (EU) | 91.3 | 90.3 | 99.3 | 83.6 | 76.8 | 100.0 | 100.0 | 70.0 | 100.0 |
| 24 | Lausanne | CH | Europe (EU) | 91.2 | 90.1 | 99.3 | 83.6 | 76.2 | 100.0 | 100.0 | 70.0 | 100.0 |
| 25 | Zürich | CH | Europe (EU) | 91.1 | 89.9 | 99.3 | 83.6 | 75.9 | 100.0 | 100.0 | 70.0 | 100.0 |
| 26 | Köln | DE | Europe (EU) | 91.0 | 87.9 | 99.3 | 90.0 | 72.0 | 100.0 | 97.7 | 85.0 | 96.0 |
| 27 | Berlin | DE | Europe (EU) | 91.0 | 87.7 | 99.3 | 90.4 | 70.7 | 100.0 | 99.6 | 85.0 | 96.8 |
| 28 | Geneva | CH | Europe (EU) | 90.9 | 89.6 | 99.3 | 83.6 | 75.2 | 100.0 | 100.0 | 70.0 | 100.0 |
| 29 | Kassel | DE | Europe (EU) | 90.8 | 90.8 | 99.3 | 79.2 | 82.4 | 99.3 | 91.9 | 70.0 | 90.3 |
| 30 | Wellington | NZ | Oceania (OC) | 90.8 | 96.3 | 95.0 | 67.3 | 91.1 | 100.0 | 100.0 | 40.0 | 100.0 |
| 31 | Schwerin | DE | Europe (EU) | 90.8 | 89.8 | 99.3 | 82.4 | 79.9 | 95.2 | 100.0 | 70.0 | 97.2 |
| 32 | Koblenz | DE | Europe (EU) | 90.7 | 90.2 | 99.3 | 80.9 | 77.3 | 100.0 | 98.1 | 70.0 | 94.0 |
| 33 | Bremen | DE | Europe (EU) | 90.7 | 90.0 | 99.3 | 81.5 | 75.9 | 100.0 | 100.0 | 70.0 | 95.3 |
| 34 | Maple Ridge | CA | North America (NA) | 90.7 | 98.4 | 89.8 | 67.0 | 96.1 | 100.0 | 100.0 | 40.0 | 99.4 |
| 35 | Paris | FR | Europe (EU) | 90.5 | 85.3 | 95.9 | 100.0 | 72.4 | 91.8 | 100.0 | 100.0 | 100.0 |
| 36 | Potsdam | DE | Europe (EU) | 90.2 | 89.4 | 99.3 | 80.5 | 74.6 | 100.0 | 100.0 | 70.0 | 93.1 |
| 37 | Bonn | DE | Europe (EU) | 90.1 | 89.6 | 99.3 | 79.4 | 75.2 | 100.0 | 99.6 | 70.0 | 90.6 |
| 38 | Hanau am Main | DE | Europe (EU) | 90.0 | 89.4 | 99.3 | 79.4 | 76.3 | 98.7 | 98.8 | 70.0 | 90.7 |
| 39 | Québec | CA | North America (NA) | 89.9 | 91.8 | 89.8 | 83.5 | 85.4 | 94.6 | 100.0 | 70.0 | 99.8 |
| 40 | Victoria | CA | North America (NA) | 89.8 | 96.8 | 89.8 | 67.1 | 100.0 | 100.0 | 83.6 | 40.0 | 99.6 |
| 41 | Lübeck | DE | Europe (EU) | 89.8 | 88.2 | 99.3 | 81.9 | 77.0 | 97.7 | 93.2 | 70.0 | 96.2 |
| 42 | Gdynia | PL | Europe (EU) | 89.5 | 88.5 | 97.3 | 82.4 | 76.6 | 100.0 | 90.9 | 70.0 | 97.4 |
| 43 | Lower Hutt | NZ | Oceania (OC) | 89.4 | 93.9 | 95.0 | 67.3 | 90.1 | 100.0 | 89.8 | 40.0 | 100.0 |
| 44 | Hastings | GB | Europe (EU) | 89.4 | 92.3 | 98.8 | 67.2 | 81.4 | 100.0 | 100.0 | 40.0 | 99.8 |
| 45 | Swansea | GB | Europe (EU) | 89.3 | 92.3 | 98.8 | 66.4 | 81.5 | 100.0 | 100.0 | 40.0 | 98.2 |
| 46 | Eugene | US | North America (NA) | 89.3 | 100.0 | 79.8 | 67.0 | 100.0 | 100.0 | 100.0 | 40.0 | 99.4 |
| 47 | Paignton | GB | Europe (EU) | 89.2 | 92.4 | 98.8 | 65.8 | 81.7 | 100.0 | 100.0 | 40.0 | 96.8 |
| 48 | Salem | US | North America (NA) | 89.2 | 100.0 | 79.8 | 66.7 | 100.0 | 100.0 | 100.0 | 40.0 | 98.7 |
| 49 | Turku | FI | Europe (EU) | 89.2 | 92.3 | 98.0 | 67.0 | 94.2 | 99.9 | 73.0 | 40.0 | 99.4 |
| 50 | Salzburg | AT | Europe (EU) | 89.2 | 89.7 | 99.5 | 73.4 | 75.3 | 100.0 | 100.0 | 70.0 | 77.4 |
| 51 | Seattle | US | North America (NA) | 89.1 | 99.6 | 79.8 | 67.3 | 99.1 | 100.0 | 100.0 | 40.0 | 100.0 |
| 52 | Southampton | GB | Europe (EU) | 89.0 | 91.7 | 98.8 | 66.9 | 80.1 | 100.0 | 100.0 | 40.0 | 99.1 |
| 53 | Peterborough | CA | North America (NA) | 89.0 | 95.4 | 89.8 | 66.9 | 94.6 | 93.9 | 100.0 | 40.0 | 99.2 |
| 54 | Saarbrücken | DE | Europe (EU) | 88.9 | 92.1 | 99.3 | 64.5 | 81.0 | 100.0 | 100.0 | 40.0 | 94.0 |
| 55 | Portland | US | North America (NA) | 88.9 | 99.4 | 79.8 | 67.2 | 98.5 | 100.0 | 100.0 | 40.0 | 99.8 |
| 56 | St. John's | CA | North America (NA) | 88.9 | 95.6 | 89.8 | 65.9 | 93.5 | 98.5 | 94.1 | 40.0 | 97.0 |
| 57 | Tartu | EE | Europe (EU) | 88.9 | 88.0 | 94.7 | 83.6 | 86.1 | 84.1 | 100.0 | 70.0 | 100.0 |
| 58 | Würzburg | DE | Europe (EU) | 88.8 | 86.9 | 99.3 | 81.0 | 79.7 | 90.6 | 94.7 | 70.0 | 94.2 |
| 59 | Reading | GB | Europe (EU) | 88.8 | 91.2 | 98.8 | 67.1 | 79.0 | 100.0 | 100.0 | 40.0 | 99.7 |
| 60 | Nice | FR | Europe (EU) | 88.8 | 87.6 | 95.9 | 82.8 | 70.9 | 100.0 | 98.6 | 70.0 | 98.3 |
| 61 | Madison | US | North America (NA) | 88.7 | 94.1 | 79.8 | 83.0 | 85.9 | 100.0 | 100.0 | 70.0 | 98.7 |
| 62 | Basel | CH | Europe (EU) | 88.7 | 90.8 | 99.3 | 67.3 | 78.4 | 100.0 | 99.0 | 40.0 | 100.0 |
| 63 | Oslo | NO | Europe (EU) | 88.7 | 92.4 | 95.4 | 67.3 | 84.9 | 100.0 | 93.2 | 40.0 | 100.0 |
| 64 | Vancouver | CA | North America (NA) | 88.6 | 94.7 | 89.8 | 67.3 | 96.6 | 100.0 | 80.0 | 40.0 | 100.0 |
| 65 | Bellingham | US | North America (NA) | 88.6 | 99.2 | 79.8 | 65.5 | 100.0 | 100.0 | 96.1 | 40.0 | 96.1 |
| 66 | Anchorage | US | North America (NA) | 88.5 | 100.0 | 79.8 | 62.7 | 100.0 | 100.0 | 100.0 | 40.0 | 90.0 |
| 67 | Ulm | DE | Europe (EU) | 88.5 | 87.0 | 99.3 | 78.5 | 81.2 | 86.7 | 100.0 | 70.0 | 88.8 |
| 68 | Genoa | IT | Europe (EU) | 88.4 | 87.2 | 95.0 | 83.6 | 69.3 | 100.0 | 100.0 | 70.0 | 100.0 |
| 69 | Gothenburg | SE | Europe (EU) | 88.4 | 90.8 | 98.1 | 67.3 | 83.4 | 100.0 | 88.4 | 40.0 | 100.0 |
| 70 | Funchal | PT | Europe (EU) | 88.4 | 90.9 | 97.8 | 67.3 | 83.1 | 100.0 | 89.7 | 40.0 | 100.0 |
| 71 | Düsseldorf | DE | Europe (EU) | 88.4 | 85.9 | 99.3 | 81.4 | 70.0 | 96.4 | 99.3 | 70.0 | 95.1 |
| 72 | Liège | BE | Europe (EU) | 88.3 | 90.6 | 98.4 | 66.8 | 79.2 | 98.2 | 100.0 | 40.0 | 98.9 |
| 73 | Örebro | SE | Europe (EU) | 88.2 | 90.4 | 98.1 | 67.2 | 96.1 | 86.5 | 85.9 | 40.0 | 99.8 |
| 74 | Plymouth | GB | Europe (EU) | 88.1 | 90.6 | 98.8 | 65.4 | 80.7 | 96.6 | 100.0 | 40.0 | 95.8 |
| 75 | Portland | US | North America (NA) | 88.0 | 97.9 | 79.8 | 66.9 | 94.9 | 100.0 | 100.0 | 40.0 | 99.3 |
| 76 | Munich | DE | Europe (EU) | 88.0 | 89.6 | 99.3 | 67.3 | 75.1 | 100.0 | 99.9 | 40.0 | 100.0 |
| 77 | Philadelphia | US | North America (NA) | 88.0 | 92.9 | 79.8 | 83.0 | 84.6 | 100.0 | 96.4 | 70.0 | 98.7 |
| 78 | Kiel | DE | Europe (EU) | 88.0 | 89.6 | 99.3 | 67.0 | 75.1 | 100.0 | 100.0 | 40.0 | 99.4 |
| 79 | Essen | DE | Europe (EU) | 87.9 | 83.1 | 99.3 | 88.0 | 70.6 | 100.0 | 76.2 | 85.0 | 91.7 |
| 80 | Drammen | NO | Europe (EU) | 87.9 | 91.1 | 95.4 | 67.3 | 84.4 | 93.9 | 100.0 | 40.0 | 100.0 |
| 81 | Prague | CZ | Europe (EU) | 87.8 | 84.9 | 97.9 | 83.6 | 64.1 | 99.7 | 100.0 | 70.0 | 100.0 |
| 82 | Bilbao | ES | Europe (EU) | 87.8 | 82.7 | 97.5 | 91.5 | 79.4 | 77.4 | 100.0 | 85.0 | 99.2 |
| 83 | New Haven | US | North America (NA) | 87.8 | 97.4 | 79.8 | 67.1 | 94.8 | 100.0 | 97.9 | 40.0 | 99.6 |
| 84 | New York City | US | North America (NA) | 87.7 | 89.8 | 79.8 | 91.8 | 83.5 | 91.5 | 100.0 | 85.0 | 99.9 |
| 85 | Bournemouth | GB | Europe (EU) | 87.6 | 89.5 | 98.8 | 66.4 | 81.4 | 92.9 | 100.0 | 40.0 | 98.1 |
| 86 | Exeter | GB | Europe (EU) | 87.6 | 89.7 | 98.8 | 65.7 | 82.7 | 92.0 | 100.0 | 40.0 | 96.6 |
| 87 | Vilnius | LT | Europe (EU) | 87.6 | 88.3 | 88.9 | 83.6 | 71.9 | 100.0 | 100.0 | 70.0 | 100.0 |
| 88 | Antibes | FR | Europe (EU) | 87.6 | 90.5 | 95.9 | 66.6 | 77.3 | 100.0 | 100.0 | 40.0 | 98.4 |
| 89 | Luzern | CH | Europe (EU) | 87.5 | 88.8 | 99.3 | 67.3 | 73.2 | 100.0 | 100.0 | 40.0 | 100.0 |
| 90 | Warrington | GB | Europe (EU) | 87.5 | 89.2 | 98.8 | 66.7 | 78.3 | 95.4 | 100.0 | 40.0 | 98.7 |
| 91 | Budapest | HU | Europe (EU) | 87.3 | 83.9 | 98.3 | 83.6 | 61.3 | 100.0 | 100.0 | 70.0 | 100.0 |
| 92 | Arnhem | NL | Europe (EU) | 87.3 | 83.4 | 99.5 | 83.6 | 74.2 | 84.9 | 100.0 | 70.0 | 100.0 |
| 93 | Cergy-Pontoise | FR | Europe (EU) | 87.3 | 89.8 | 95.9 | 67.3 | 77.1 | 100.0 | 96.9 | 40.0 | 100.0 |
| 94 | Leipzig | DE | Europe (EU) | 87.3 | 89.5 | 99.3 | 63.8 | 76.0 | 100.0 | 97.3 | 40.0 | 92.4 |
| 95 | Haninge | SE | Europe (EU) | 87.3 | 88.9 | 98.1 | 67.3 | 93.3 | 100.0 | 57.1 | 40.0 | 100.0 |
| 96 | Lawrence | US | North America (NA) | 87.3 | 96.8 | 79.8 | 66.2 | 92.3 | 100.0 | 100.0 | 40.0 | 97.6 |
| 97 | Kingston | CA | North America (NA) | 87.2 | 87.3 | 89.8 | 83.5 | 92.6 | 75.3 | 100.0 | 70.0 | 99.7 |
| 98 | Auckland | NZ | Oceania (OC) | 87.2 | 90.1 | 95.0 | 67.3 | 95.8 | 79.0 | 100.0 | 40.0 | 100.0 |
| 99 | Telford | GB | Europe (EU) | 87.2 | 83.7 | 98.8 | 82.7 | 76.4 | 100.0 | 67.1 | 70.0 | 97.9 |
| 100 | Frankfurt am Main | DE | Europe (EU) | 87.1 | 88.6 | 99.3 | 65.5 | 72.7 | 100.0 | 100.0 | 40.0 | 96.0 |
