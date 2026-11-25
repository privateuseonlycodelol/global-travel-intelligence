# Objective city quality index

**Audit date:** 2026-11-25

This is a transparent, objective composite for GHSL urban centres with
population ≥1M. It is not a subjective "best city" claim; it is a weighted
summary of the measured data we hold.

## Dimensions and weights

| Dimension | Weight | Components |
|---|---:|---|
| Environment & parks | 25% | physical urban greenery 40%, mapped parks 20%, air 25%, water access 15% |
| Culture & food | 20% | culture venues 50%, restaurants 30%, cuisine variety 20% (percentiles) |
| Safety & health | 15% | homicide 35% (lower), road deaths 25% (lower), life expectancy 25%, physicians 15% |
| Affordability | 10% | country price level 70% (lower), out-of-pocket health spending 30% (lower) |
| Walkability | 10% | How Walkable 0–100 score |
| Public transport | 10% | mapped public-transport stops within 5 km (percentile); frequency not included |
| Climate | 5% | comfortable annual high 70%, rainy days 30% |
| Connectivity | 5% | download 50%, upload 20%, latency 30% (lower) |

Scores are 0–100. Culture/food/safety/affordability/connectivity use
percentile ranks within the 516-centre cohort. Environment, walkability and
climate use absolute 0–100 scores.

Missing dimensions are dropped and the remaining weights renormalized.
`data_completeness` is the share of the 1.0 total weight available. The
headline ranking requires **completeness ≥0.90**; 445 of 516 centres qualify.
71 are listed separately, including Taipei, Taichung, Kaohsiung, Tainan,
Moscow and Saint Petersburg.

## Formula

```
quality = 0.25 × environment
        + 0.20 × culture_food
        + 0.15 × safety_health
        + 0.10 × affordability
        + 0.10 × walkability
        + 0.10 × public_transport
        + 0.05 × climate
        + 0.05 × connectivity
```

Environment:

```
environment = 0.40 × physical_green_score
            + 0.20 × park_score
            + 0.25 × air_score
            + 0.15 × water_score
```

Park score is `min(100, 100 × ln(1 + park_count) / ln(501))`; a zero park count
is `none_listed` and the component is omitted, not scored as zero.

Climate comfort:

```
comfort = max(0, 1 − 0.10 × max(20 − avg_high, avg_high − 26, 0))
rain    = max(0, 1 − rainy_days / 20)
climate = 100 × (0.70 × comfort + 0.30 × rain)
```

## Top 100

| # | City | Country | Quality | Environment | Culture/food | Safety/health | Affordability | Walkability | Public transport | Climate | Connectivity |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Barcelona | ES | 79.6 | 64.1 | 97.1 | 87.4 | 34.4 | 91.0 | 98.7 | 89.2 | 83.8 |
| 2 | London | GB | 79.5 | 75.7 | 99.5 | 81.9 | 30.5 | 98.0 | 100.0 | 47.4 | 64.3 |
| 3 | Paris | FR | 79.4 | 69.2 | 95.3 | 79.8 | 37.4 | 99.0 | 99.4 | 57.7 | 93.1 |
| 4 | Madrid | ES | 79.3 | 62.8 | 95.7 | 87.4 | 34.4 | 92.0 | 97.4 | 90.1 | 89.1 |
| 5 | Hong Kong | HK | 78.2 | 70.9 | 91.6 | 95.3 | 11.6 | 84.0 | 98.9 | 75.0 | 93.0 |
| 6 | Berlin | DE | 77.8 | 74.5 | 97.1 | 84.1 | 36.2 | 90.0 | 96.3 | 49.3 | 49.3 |
| 7 | Sydney | AU | 77.8 | 76.9 | 94.0 | 85.0 | 29.1 | 81.0 | 90.5 | 85.9 | 53.4 |
| 8 | Melbourne | AU | 77.2 | 74.0 | 93.5 | 85.0 | 29.1 | 87.0 | 87.0 | 79.0 | 58.7 |
| 9 | Stockholm | SE | 76.9 | 82.8 | 91.3 | 86.3 | 31.6 | 79.0 | 89.3 | 18.0 | 82.6 |
| 10 | Tokyo | JP | 76.7 | 58.1 | 87.8 | 92.8 | 36.4 | 98.0 | 99.8 | 81.3 | 65.3 |
| 11 | Singapore | SG | 76.7 | 63.5 | 94.4 | 93.4 | 34.0 | 81.0 | 93.1 | 43.7 | 99.1 |
| 12 | Brussels | BE | 76.7 | 69.2 | 94.6 | 81.1 | 30.8 | 94.0 | 97.8 | 48.5 | 72.2 |
| 13 | Rome | IT | 76.6 | 63.9 | 84.6 | 87.0 | 32.2 | 94.0 | 94.8 | 86.7 | 84.5 |
| 14 | Brisbane | AU | 76.5 | 77.9 | 85.8 | 85.0 | 29.1 | 82.0 | 89.3 | 84.5 | 57.7 |
| 15 | Milan | IT | 76.0 | 58.2 | 88.3 | 87.0 | 32.2 | 94.0 | 95.7 | 77.1 | 93.7 |
| 16 | New York City | US | 76.0 | 74.3 | 99.3 | 60.4 | 29.9 | 93.0 | 99.1 | 48.7 | 76.6 |
| 17 | Valencia | ES | 75.8 | 61.3 | 89.2 | 87.4 | 34.4 | 82.0 | 91.1 | 91.2 | 85.0 |
| 18 | Vienna | AT | 75.8 | 66.5 | 94.9 | 84.9 | 31.6 | 91.0 | 96.5 | 51.6 | 59.3 |
| 19 | Washington | US | 75.7 | 81.9 | 95.8 | 60.4 | 29.9 | 79.0 | 89.0 | 76.2 | 66.9 |
| 20 | Lisbon | PT | 75.5 | 67.4 | 85.3 | 84.8 | 32.0 | 85.0 | 88.3 | 83.0 | 83.9 |
| 21 | Copenhagen | DK | 75.4 | 71.4 | 92.1 | 85.9 | 30.1 | 87.0 | 91.7 | 26.0 | 80.8 |
| 22 | Amsterdam | NL | 75.3 | 68.6 | 95.4 | 85.0 | 33.1 | 87.0 | 88.6 | 37.3 | 71.5 |
| 23 | Hamburg | DE | 75.2 | 74.5 | 91.1 | 84.1 | 36.2 | 80.0 | 94.4 | 36.8 | 55.8 |
| 24 | Budapest | HU | 75.1 | 68.7 | 92.2 | 74.0 | 38.2 | 88.0 | 85.6 | 64.8 | 78.7 |
| 25 | Prague | CZ | 75.0 | 69.9 | 94.2 | 83.2 | 38.0 | 85.0 | 84.3 | 45.5 | 63.3 |
| 26 | Munich | DE | 75.0 | 70.2 | 91.6 | 84.1 | 36.2 | 87.0 | 95.0 | 34.4 | 58.6 |
| 27 | Osaka | JP | 74.8 | 58.5 | 84.9 | 92.8 | 36.4 | 94.0 | 99.6 | 76.2 | 49.0 |
| 28 | Vancouver | CA | 74.7 | 77.7 | 87.6 | 75.1 | 31.6 | 83.0 | 94.2 | 30.6 | 81.3 |
| 29 | Bucharest | RO | 74.6 | 57.0 | 87.3 | 70.6 | 54.8 | 88.0 | 95.2 | 80.0 | 89.2 |
| 30 | Warsaw | PL | 74.4 | 67.1 | 89.4 | 79.9 | 39.4 | 87.0 | 91.7 | 41.2 | 78.8 |
| 31 | Manchester | GB | 74.4 | 73.2 | 88.3 | 81.9 | 30.5 | 86.0 | 95.5 | 25.0 | 73.2 |
| 32 | Philadelphia | US | 74.3 | 74.1 | 95.1 | 60.4 | 29.9 | 89.0 | 89.7 | 69.0 | 68.3 |
| 33 | Toronto | CA | 74.2 | 71.0 | 94.8 | 75.1 | 31.6 | 82.0 | 87.5 | 27.8 | 95.5 |
| 34 | Köln | DE | 74.2 | 72.0 | 88.8 | 84.1 | 36.2 | 83.0 | 92.2 | 47.4 | 45.2 |
| 35 | Boston | US | 73.2 | 76.9 | 91.2 | 60.4 | 29.9 | 89.0 | 93.5 | 47.4 | 60.3 |
| 36 | Nagoya | JP | 73.2 | 54.9 | 80.7 | 92.8 | 36.4 | 92.0 | 98.5 | 79.8 | 53.7 |
| 37 | Seattle | US | 73.1 | 81.9 | 88.8 | 60.4 | 29.9 | 80.0 | 84.0 | 43.8 | 85.1 |
| 38 | Lyon | FR | 72.9 | 64.6 | 83.7 | 79.8 | 37.4 | 83.0 | 83.4 | 60.5 | 94.1 |
| 39 | Portland | US | 72.9 | 81.4 | 87.8 | 60.4 | 29.9 | 75.0 | 87.7 | 54.5 | 79.3 |
| 40 | Athens | GR | 72.3 | 55.4 | 87.5 | 83.7 | 24.2 | 94.0 | 86.3 | 90.8 | 68.9 |
| 41 | Montréal | CA | 72.2 | 70.1 | 93.5 | 75.1 | 31.6 | 82.0 | 84.3 | 22.7 | 76.3 |
| 42 | Naples | IT | 71.9 | 60.1 | 74.8 | 87.0 | 32.2 | 86.0 | 85.2 | 86.5 | 83.6 |
| 43 | Buenos Aires | AR | 71.7 | 59.3 | 86.8 | 55.5 | 50.8 | 90.0 | 92.9 | 89.4 | 68.1 |
| 44 | Istanbul | TR | 71.6 | 58.7 | 90.2 | 57.2 | 59.6 | 97.0 | — | 80.2 | 69.9 |
| 45 | Chicago | US | 71.4 | 67.9 | 90.3 | 60.4 | 29.9 | 91.0 | 97.2 | 35.8 | 74.9 |
| 46 | Liverpool | GB | 71.3 | 68.9 | 81.7 | 81.9 | 30.5 | 85.0 | 90.8 | 25.5 | 71.9 |
| 47 | Dublin | IE | 71.3 | 71.2 | 81.7 | 87.5 | 34.8 | 82.0 | 78.5 | 24.9 | 65.0 |
| 48 | Rotterdam | NL | 71.2 | 68.1 | 83.2 | 85.0 | 33.1 | 77.0 | 82.9 | 38.2 | 70.2 |
| 49 | Porto | PT | 70.8 | 64.0 | 76.2 | 84.8 | 32.0 | — | 82.6 | 78.1 | 88.4 |
| 50 | Jakarta | ID | 70.7 | 51.0 | 89.2 | 61.2 | 68.0 | 91.0 | 98.3 | 43.8 | 60.9 |
| 51 | Fukuoka | JP | 70.6 | 56.0 | 73.8 | 92.8 | 36.4 | 87.0 | 91.7 | 82.4 | 45.2 |
| 52 | Perth | AU | 70.4 | 67.2 | 78.0 | 85.0 | 29.1 | 75.0 | 79.2 | 90.4 | 48.4 |
| 53 | Turin | IT | 70.4 | 56.5 | 77.9 | 87.0 | 32.2 | 85.0 | 83.2 | 64.6 | 87.3 |
| 54 | Denver | US | 70.3 | 69.2 | 86.8 | 60.4 | 29.9 | 76.0 | 82.3 | 68.4 | 87.1 |
| 55 | Austin | US | 70.2 | 72.4 | 84.9 | 60.4 | 29.9 | 71.0 | 81.6 | 78.0 | 78.6 |
| 56 | São Paulo | BR | 70.2 | 57.4 | 94.2 | 35.8 | 51.6 | 95.0 | 92.7 | 76.3 | 78.9 |
| 57 | Baltimore | US | 70.2 | 73.4 | 87.5 | 60.4 | 29.9 | 77.0 | 78.5 | 69.4 | 65.6 |
| 58 | Santiago | CL | 70.2 | 51.6 | 90.2 | 57.4 | 26.7 | 86.0 | 96.8 | 95.7 | 98.1 |
| 59 | Houston | US | 70.0 | 70.1 | 85.0 | 60.4 | 29.9 | 79.0 | 77.8 | 75.6 | 79.3 |
| 60 | Leeds | GB | 70.0 | 71.0 | 76.1 | 81.9 | 30.5 | 80.0 | 84.9 | 33.2 | 69.7 |
| 61 | Kuala Lumpur | MY | 69.9 | 58.5 | 91.8 | 51.2 | 57.5 | 79.0 | 96.1 | 45.1 | 75.3 |
| 62 | Dallas | US | 69.8 | 66.9 | 84.9 | 60.4 | 29.9 | 75.0 | 82.0 | 87.7 | 80.0 |
| 63 | Hiroshima | JP | 69.7 | 64.0 | 67.3 | 92.8 | 36.4 | 76.0 | 90.3 | 74.5 | 47.8 |
| 64 | Helsinki | FI | 69.7 | 77.3 | 84.4 | 82.7 | 31.8 | 79.0 | 61.1 | 12.5 | 65.6 |
| 65 | Seoul | KR | 69.4 | 57.3 | 86.0 | 86.6 | 25.2 | — | 85.6 | 62.1 | 75.5 |
| 66 | Los Angeles | US | 69.4 | 54.6 | 91.5 | 60.4 | 29.9 | 82.0 | 88.0 | 95.7 | 72.0 |
| 67 | Birmingham | GB | 69.3 | 66.1 | 85.9 | 81.9 | 30.5 | 82.0 | — | 38.0 | 65.1 |
| 68 | Belgrade | RS | 69.1 | 63.5 | 77.9 | 66.4 | 49.4 | 77.0 | 76.0 | 78.3 | 70.7 |
| 69 | Minneapolis | US | 69.1 | 75.8 | 85.2 | 60.4 | 29.9 | 72.0 | 79.5 | 34.9 | 82.6 |
| 70 | Sofia | BG | 68.9 | 57.8 | 80.9 | 71.7 | 39.9 | 82.0 | 86.3 | 57.0 | 77.3 |
| 71 | Tel Aviv | IL | 68.9 | 55.3 | 82.9 | 81.9 | 24.2 | 81.0 | 70.3 | 90.5 | 83.4 |
| 72 | Naha | JP | 68.9 | 63.0 | 65.3 | 92.8 | 36.4 | 85.0 | 79.9 | 76.9 | 44.1 |
| 73 | Bandung | ID | 68.8 | 52.9 | 80.9 | 61.2 | 68.0 | 87.0 | 86.3 | 70.0 | 52.4 |
| 74 | Medellín | CO | 68.8 | 62.7 | 87.1 | 40.7 | 64.5 | 80.0 | 75.2 | 70.0 | 82.1 |
| 75 | Bangkok | TH | 68.7 | 56.1 | 91.2 | 28.5 | 75.1 | 92.0 | 98.1 | 26.9 | 85.7 |
| 76 | İzmir | TR | 68.6 | 56.4 | 76.7 | 57.2 | 59.6 | 91.0 | 73.0 | 91.1 | 74.5 |
| 77 | Auckland | NZ | 68.4 | 73.7 | 73.7 | 77.6 | 31.8 | 69.0 | 61.1 | 71.9 | 75.6 |
| 78 | Sendai | JP | 68.0 | 57.2 | 70.4 | 92.8 | 36.4 | 79.0 | 93.3 | 35.9 | 61.7 |
| 79 | Sapporo | JP | 68.0 | 59.4 | 72.9 | 92.8 | 36.4 | 89.0 | 90.8 | 9.5 | 51.6 |
| 80 | Miami | US | 67.9 | 64.6 | 87.3 | 60.4 | 29.9 | 75.0 | 76.9 | 65.7 | 75.4 |
| 81 | Essen | DE | 67.5 | 69.2 | 72.1 | 84.1 | 36.2 | 71.0 | 81.3 | 44.6 | 41.7 |
| 82 | Antalya | TR | 67.5 | 61.4 | 74.6 | 57.2 | 59.6 | 86.0 | 70.3 | 89.2 | 51.6 |
| 83 | Kyiv | UA | 67.3 | 66.7 | 80.5 | 57.0 | 50.4 | 72.0 | 80.2 | 38.5 | 77.1 |
| 84 | Ankara | TR | 67.1 | 58.1 | 80.8 | 57.2 | 59.6 | 91.0 | — | 72.7 | 49.3 |
| 85 | Tuen Mun | HK | 66.9 | 64.3 | 59.5 | 95.3 | 11.6 | — | 80.8 | 78.9 | 96.3 |
| 86 | Quezon City | PH | 66.9 | 55.8 | 90.7 | 39.8 | 49.7 | 90.0 | 90.1 | 43.3 | 73.2 |
| 87 | Bogotá | CO | 66.5 | 55.3 | 86.7 | 40.7 | 64.5 | 81.0 | 73.0 | 70.0 | 78.2 |
| 88 | Curitiba | BR | 66.5 | 57.5 | 88.1 | 35.8 | 51.6 | 84.0 | 76.0 | 75.5 | 83.4 |
| 89 | San José | CR | 66.2 | 64.1 | 77.4 | 51.0 | 35.2 | 79.0 | 88.0 | 58.2 | 78.3 |
| 90 | Rio de Janeiro | BR | 66.2 | 60.1 | 80.0 | 35.8 | 51.6 | 84.0 | 86.8 | 74.9 | 76.6 |
| 91 | Detroit | US | 66.0 | 69.5 | 81.1 | 60.4 | 29.9 | 66.0 | 79.7 | 41.6 | 74.2 |
| 92 | Porto Alegre | BR | 65.7 | 62.9 | 80.6 | 35.8 | 51.6 | 86.0 | 64.4 | 82.9 | 82.4 |
| 93 | Calgary | CA | 65.6 | 67.1 | 76.6 | 75.1 | 31.6 | 69.0 | 70.3 | 16.0 | 87.3 |
| 94 | Kitakyushu | JP | 65.3 | 55.3 | 59.6 | 92.8 | 36.4 | 75.0 | 82.0 | 80.3 | 45.9 |
| 95 | Hanoi | VN | 65.2 | 51.0 | 90.9 | 30.6 | 62.6 | 87.0 | 76.9 | 63.3 | 77.2 |
| 96 | Yogyakarta | ID | 65.0 | 55.6 | 73.8 | 61.2 | 68.0 | 86.0 | 72.0 | 47.9 | 43.7 |
| 97 | Sacramento | US | 64.9 | 66.4 | 72.2 | 60.4 | 29.9 | 62.0 | 74.3 | 92.6 | 70.5 |
| 98 | Mexico City | MX | 64.9 | 49.9 | 88.8 | 40.3 | 24.5 | 95.0 | 95.9 | 80.2 | 60.7 |
| 99 | San Jose | US | 64.8 | 57.8 | 77.2 | 60.4 | 29.9 | 67.0 | 75.6 | 93.4 | 78.5 |
| 100 | Kolkata | IN | 64.5 | 57.7 | 75.2 | 41.5 | 67.7 | 81.0 | 94.0 | 41.5 | 49.8 |

## Sensitivity

| Weighting | Top five |
|---|---|
| Headline 25/20/15/10/10/10/5/5 | Barcelona, London, Paris, Madrid, Hong Kong |
| Equal across eight dimensions | Madrid, Barcelona, Paris, Rome, Milan |
| Culture-heavy | Barcelona, Madrid, London, Paris, Hong Kong |
| Environment-heavy | London, Sydney, Stockholm, Paris, Barcelona |
| Transit-heavy | Barcelona, Paris, Madrid, London, Hong Kong |
| Safety-heavy | Barcelona, Madrid, Hong Kong, Paris, London |

Barcelona is first under the headline, culture-heavy, transit-heavy and
safety-heavy weightings; second under equal; fifth under environment-heavy.
Madrid is first under equal and second in most others. The top four —
Barcelona 79.6, London 79.5, Paris 79.4, Madrid 79.3 — are effectively tied.
London/Sydney/Stockholm lead when environment is weighted more heavily.

## What this is not

- **Not a subjective quality judgment.** It does not measure scenery, vibe,
  food quality, safety perception, social life or personal fit.
- **Not population-weighted within cities.** GHSL centre population is the
  eligibility threshold, not a weight.
- **Not free of coverage bias.** Culture/food counts are denser in the US and
  Europe; walkability covers 251 of 516 centres; connectivity is Ookla
  coverage. Cities in data-poor countries can be under-ranked or excluded.
- **Not a city-boundary canopy measure.** Environment uses the GHSL polygon
  for physical greenery, but parks/air/water are point/proxy measures.
- **Public transport is availability, not frequency.** It counts mapped stops
  within 5 km (Overture). GTFS headway exists for only 6 of the 516 centres, so
  frequency/reliability is not in the score.
- **Not including everyday 300 m/500 m exposure or 300 m green access.** Those
  are blocked by the missing global GHS-POP 100 m grid and green-space
  polygons; no proxy is substituted.
- **Country-level safety and affordability.** All cities in a country share
  the same homicide, road-death, life-expectancy, physician, price-level and
  out-of-pocket values.

## Raw data and reproduce

- Raw scores: `reports/quality_all_445.csv`
- Top 100: `reports/quality_top100.csv` / `.md`
- Excluded low completeness: `reports/quality_excluded_low_completeness.csv`
- Index build: `bootstrap_quality_index.py`
- Ranking code: `reports/generate_quality_rankings.py`

```bash
python bootstrap_quality_index.py
python reports/generate_quality_rankings.py \
  --db-url "postgresql:///travel_global?host=/var/run/postgresql&port=5432"
```

## All-city extension

The same dimensions are also scored for **all 13,101 destinations**. After
deduplicating to one principal city per GHSL urban centre (or the city itself
when no centre is mapped), **10,389 principal cities** have a score. **2,316**
meet the 0.90 completeness threshold.

Top of the all-city list:

| # | City | Country | Quality | Environment | Culture/food | Safety/health | Affordability | Walkability | Public transport | Climate | Connectivity |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Barcelona | ES | 80.6 | 64.1 | 99.7 | 87.5 | 37.7 | 91.0 | 99.8 | 89.2 | 84.0 |
| 2 | Madrid | ES | 80.5 | 62.8 | 99.2 | 87.5 | 37.7 | 92.0 | 99.6 | 90.1 | 88.2 |
| 3 | Paris | FR | 80.1 | 69.2 | 98.2 | 77.5 | 41.4 | 99.0 | 99.9 | 57.7 | 92.9 |
| 4 | Hong Kong | HK | 80.0 | 70.9 | 96.4 | 96.2 | 17.9 | 84.0 | 99.8 | 75.0 | 92.6 |
| 5 | Brisbane | AU | 79.9 | 77.9 | 98.5 | 84.2 | 29.4 | 82.0 | 98.4 | 84.5 | 58.6 |

**Malmö:** quality **69.5**, rank **376 of 2,316** cities with completeness
≥0.90 (completeness 1.00). Its dimensions are environment 52.1, culture/food
95.8, walkability 66, public transport 93.3 and climate 25.2.

The all-city environment is a mixed source: GHSL polygon physical greenery
for 516 centres, the point-based environmental score for the 5,000 cities that
have it, and null otherwise. It is not directly comparable to the 516-centre
index above. The all-city list is context for smaller cities, not a claim that
a 200k city has the same data quality as a 2M centre.

Files: `reports/quality_all_cities.csv`,
`reports/quality_all_cities_top100.csv`,
`reports/quality_all_cities_low_completeness.csv`.
