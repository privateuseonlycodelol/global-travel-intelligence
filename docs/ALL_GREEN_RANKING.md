# All-encompassing green score — 1M+ cities

**Audit date:** 2026-11-25

This is the original all-component idea restored with the corrected
components:

```
all_green = 0.40 × vegetation
          + 0.20 × parks
          + 0.20 × air
          + 0.20 × water access
```

- **Vegetation** is the point-based land-only vegetation score
  (`destination_green_score.green_score_0_100`): 50% 1 km tree+shrub+grass +
  50% 15 km tree+shrub. It is a point sample, not citywide canopy.
- **Parks** is `min(100, 100 × ln(1 + park_count) / ln(501))`.
- **Air** is the fixed log scale: 100 at 5 µg/m³, 0 at 75 µg/m³.
- **Water** is the nearest coast, lake ≥10 km² or Strahler ≥4 river, with
  50 km decay. It is access, not quality.

Ranking unit: city population ≥1M, one principal city per GHSL urban centre,
25% centre/city boundary filter. 407 cities. A zero park count is
`none_listed`, so those cities are not scored.

This is the score to use when you want one number across all four data
families. It is not the same as the physical-greenery ranking, which uses the
GHSL urban-area polygon and excludes air/water, or the combined ranking, which
blends the two published scores 50/50. See
[METHODOLOGY_AUDIT.md](METHODOLOGY_AUDIT.md),
[COMBINED_RANKING.md](COMBINED_RANKING.md) and
[URBAN_AREA_AUDIT.md](URBAN_AREA_AUDIT.md).

## Top 100

| # | City | Country | All-green | Vegetation | Parks | Air | Water | Population | GHSL centre |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | Stockholm | SE | 73.7 | 40.3 | 94.8 | 93.1 | 99.9 | 1,515,017 | Stockholm |
| 2 | Montréal | CA | 72.5 | 45.9 | 97.4 | 78.9 | 94.4 | 1,762,949 | Montreal |
| 3 | Brisbane | AU | 72.2 | 31.4 | 100.0 | 98.6 | 99.4 | 2,780,063 | Brisbane |
| 4 | Oslo | NO | 72.0 | 47.8 | 83.3 | 84.9 | 96.1 | 1,082,575 | Oslo |
| 5 | Hong Kong | HK | 70.9 | 48.3 | 100.0 | 58.3 | 99.8 | 7,396,076 | Hong Kong |
| 6 | Sydney | AU | 70.7 | 32.5 | 100.0 | 95.8 | 92.6 | 5,638,830 | Sydney |
| 7 | Jacksonville | US | 68.8 | 44.5 | 73.9 | 83.1 | 97.9 | 1,009,833 | Jacksonville |
| 8 | Budapest | HU | 66.8 | 38.4 | 96.3 | 61.3 | 99.5 | 1,741,041 | Budapest |
| 9 | Berlin | DE | 66.8 | 36.0 | 93.3 | 70.7 | 97.9 | 3,426,354 | Berlin |
| 10 | Kyiv | UA | 66.3 | 42.3 | 84.8 | 63.9 | 98.4 | 2,952,301 | Kyiv |
| 11 | Ottawa | CA | 66.1 | 30.1 | 87.8 | 86.1 | 96.8 | 1,017,449 | Ottawa |
| 12 | Taipei | TW | 66.0 | 36.7 | 100.0 | 61.4 | 95.2 | 7,871,900 | Taipei |
| 13 | Auckland | NZ | 65.1 | 25.4 | 80.2 | 95.8 | 98.8 | 1,547,200 | Auckland |
| 14 | Edmonton | CA | 64.8 | 38.2 | 80.4 | 67.8 | 99.5 | 1,010,899 | Edmonton |
| 15 | London | GB | 64.4 | 21.5 | 100.0 | 80.1 | 98.9 | 8,961,989 | London |
| 16 | Melbourne | AU | 64.4 | 18.3 | 97.1 | 89.3 | 98.9 | 5,435,590 | Melbourne |
| 17 | Philadelphia | US | 64.2 | 23.8 | 91.6 | 84.6 | 97.0 | 1,573,916 | Philadelphia |
| 18 | Hiroshima | JP | 64.0 | 37.9 | 81.9 | 64.8 | 97.6 | 1,200,754 | Hiroshima |
| 19 | Kuala Lumpur | MY | 64.0 | 45.9 | 89.6 | 40.2 | 98.4 | 1,453,975 | Kuala Lumpur |
| 20 | Sapporo | JP | 63.5 | 28.5 | 92.4 | 71.2 | 96.7 | 1,973,832 | Sapporo |
| 21 | Köln | DE | 63.4 | 30.7 | 86.3 | 72.0 | 97.4 | 1,024,621 | Cologne |
| 22 | Yekaterinburg | RU | 63.2 | 40.8 | 74.6 | 63.3 | 96.6 | 1,495,066 | Yekaterinburg |
| 23 | Hamburg | DE | 63.0 | 29.2 | 83.0 | 74.2 | 99.5 | 1,973,896 | Hamburg |
| 24 | Minsk | BY | 62.8 | 34.4 | 78.4 | 67.5 | 99.5 | 1,742,124 | Minsk |
| 25 | Recife | BR | 62.8 | 29.2 | 71.8 | 85.0 | 98.7 | 1,653,461 | Recife |
| 26 | Dublin | IE | 62.8 | 25.2 | 84.2 | 82.4 | 96.7 | 1,024,027 | Dublin |
| 27 | Saint Petersburg | RU | 62.7 | 23.2 | 88.0 | 79.7 | 99.1 | 5,351,935 | Saint Petersburg |
| 28 | Santiago de los Caballeros | DO | 62.5 | 45.8 | 51.8 | 72.1 | 97.2 | 1,200,000 | Santiago de los Caballeros |
| 29 | Munich | DE | 62.5 | 27.4 | 84.7 | 75.1 | 98.0 | 1,505,005 | Munich |
| 30 | Houston | US | 62.5 | 29.3 | 80.9 | 73.0 | 99.7 | 2,314,157 | Houston |
| 31 | Fort Worth | US | 62.5 | 33.5 | 73.1 | 78.7 | 93.5 | 1,008,106 | Edgecliff Village |
| 32 | Adelaide | AU | 62.3 | 23.5 | 89.4 | 93.5 | 81.2 | 1,469,163 | Adelaide |
| 33 | Vienna | AT | 62.1 | 26.4 | 95.3 | 69.3 | 93.3 | 1,691,468 | Vienna |
| 34 | Toronto | CA | 61.9 | 16.4 | 95.3 | 84.6 | 96.9 | 2,794,356 | Toronto |
| 35 | Perth | AU | 61.5 | 13.6 | 90.1 | 93.2 | 97.3 | 2,384,371 | Perth |
| 36 | Brussels | BE | 61.5 | 21.6 | 89.1 | 75.8 | 99.2 | 1,019,022 | Brussels |
| 37 | Sanya | CN | 61.4 | 55.4 | 31.3 | 65.7 | 99.3 | 1,031,396 | Sanya |
| 38 | Belém | BR | 61.4 | 41.6 | 57.2 | 67.6 | 99.0 | 1,499,641 | Belém |
| 39 | Dallas | US | 61.3 | 25.6 | 81.9 | 76.2 | 97.4 | 1,326,087 | Dallas |
| 40 | Samara | RU | 61.3 | 38.7 | 64.2 | 68.5 | 96.7 | 1,163,399 | Samara |
| 41 | New York City | US | 61.0 | 11.5 | 100.0 | 83.5 | 98.6 | 8,804,190 | New York City |
| 42 | Copenhagen | DK | 60.9 | 17.7 | 92.3 | 76.8 | 100.0 | 1,153,615 | Copenhagen |
| 43 | Singapore | SG | 60.8 | 26.9 | 90.3 | 62.7 | 97.4 | 5,638,700 | Singapore |
| 44 | Ufa | RU | 60.8 | 36.4 | 64.5 | 69.2 | 97.5 | 1,120,547 | Ufa |
| 45 | Davao | PH | 60.4 | 41.9 | 71.3 | 48.3 | 98.5 | 1,848,947 | Davao City |
| 46 | Zamboanga | PH | 60.3 | 46.6 | 60.9 | 48.8 | 98.8 | 1,018,849 | Zamboanga City |
| 47 | Prague | CZ | 60.3 | 22.3 | 93.5 | 64.1 | 99.1 | 1,165,581 | Prague |
| 48 | Kharkiv | UA | 60.2 | 36.4 | 73.3 | 59.7 | 95.4 | 1,421,125 | Kharkiv |
| 49 | Sendai | JP | 59.7 | 28.1 | 83.2 | 73.5 | 85.7 | 1,096,704 | Sendai |
| 50 | Calgary | CA | 59.7 | 20.2 | 83.3 | 74.9 | 99.8 | 1,306,784 | Calgary |
| 51 | Warsaw | PL | 59.3 | 28.7 | 87.5 | 55.7 | 96.1 | 1,702,139 | Warsaw |
| 52 | Fukuoka | JP | 59.3 | 25.0 | 87.0 | 64.1 | 95.4 | 1,612,392 | Fukuoka |
| 53 | Paris | FR | 59.2 | 14.5 | 94.7 | 72.4 | 99.8 | 2,138,551 | Paris |
| 54 | Kazan | RU | 59.2 | 30.1 | 73.6 | 64.8 | 97.2 | 1,243,500 | Kazan |
| 55 | İzmir | TR | 59.0 | 34.2 | 91.4 | 38.8 | 96.4 | 2,938,292 | Izmir |
| 56 | Barcelona | ES | 59.0 | 20.9 | 100.0 | 61.4 | 91.7 | 1,686,208 | Barcelona |
| 57 | Krasnoyarsk | RU | 58.9 | 36.8 | 63.6 | 62.4 | 95.1 | 1,090,811 | Krasnoyarsk |
| 58 | Medellín | CO | 58.9 | 30.7 | 83.7 | 50.8 | 98.8 | 1,999,979 | Medellín |
| 59 | Nizhniy Novgorod | RU | 58.9 | 38.3 | 55.7 | 63.7 | 98.5 | 1,259,013 | Nizhny Novgorod |
| 60 | Brasília | BR | 58.6 | 39.2 | 63.2 | 64.4 | 86.9 | 2,207,718 | Brasilia |
| 61 | Batam | ID | 58.6 | 32.1 | 62.9 | 68.5 | 97.2 | 1,296,960 | Batam City |
| 62 | Bursa | TR | 58.5 | 34.5 | 87.7 | 46.3 | 89.3 | 3,101,833 | Bursa |
| 63 | Moscow | RU | 58.4 | 25.4 | 90.6 | 51.8 | 99.1 | 10,381,222 | Moscow |
| 64 | Havana | CU | 58.2 | 28.3 | 64.8 | 73.3 | 96.1 | 2,163,824 | Havana |
| 65 | Novosibirsk | RU | 57.8 | 29.8 | 70.1 | 62.7 | 96.5 | 1,612,833 | Novosibirsk |
| 66 | Salvador | BR | 57.8 | 26.6 | 65.9 | 75.9 | 93.7 | 2,711,840 | Salvador |
| 67 | Bandar Lampung | ID | 57.7 | 44.2 | 55.7 | 49.4 | 95.2 | 1,088,200 | Bandar Lampung |
| 68 | Gqeberha | ZA | 57.6 | 25.2 | 52.4 | 88.5 | 96.6 | 1,050,078 | Gqeberha |
| 69 | Voronezh | RU | 57.6 | 31.5 | 65.9 | 63.1 | 96.0 | 1,047,549 | Voronezh |
| 70 | Belgrade | RS | 57.5 | 27.1 | 84.7 | 51.8 | 96.9 | 1,273,651 | Belgrade |
| 71 | Durban | ZA | 57.5 | 32.6 | 67.6 | 57.2 | 97.7 | 3,338,026 | Durban |
| 72 | Daejeon | KR | 57.5 | 41.0 | 64.2 | 46.8 | 94.4 | 1,441,203 | Daejeon |
| 73 | Palembang | ID | 57.2 | 54.5 | 44.6 | 48.7 | 83.7 | 1,801,367 | Palembang |
| 74 | Mérida | MX | 57.1 | 29.7 | 85.5 | 60.6 | 80.1 | 1,201,000 | Mérida |
| 75 | Taichung | TW | 57.1 | 26.1 | 98.1 | 52.6 | 82.3 | 2,850,285 | Taichung |
| 76 | Porto Alegre | BR | 57.0 | 22.3 | 79.1 | 63.5 | 97.9 | 1,488,252 | Porto Alegre |
| 77 | Rome | IT | 57.0 | 20.7 | 87.5 | 61.8 | 94.3 | 2,318,895 | Rome |
| 78 | Jepara | ID | 57.0 | 45.5 | 43.6 | 51.9 | 98.4 | 1,257,912 | Jepara |
| 79 | Pekanbaru | ID | 56.8 | 39.4 | 58.1 | 55.8 | 91.2 | 1,167,599 | Pekanbaru |
| 80 | Antalya | TR | 56.7 | 26.1 | 90.4 | 45.2 | 95.9 | 1,335,002 | Antalya |
| 81 | Rio de Janeiro | BR | 56.6 | 28.1 | 77.5 | 53.6 | 95.6 | 6,747,815 | Rio de Janeiro |
| 82 | Manaus | BR | 56.6 | 39.2 | 76.0 | 35.2 | 93.1 | 2,219,580 | Manaus |
| 83 | Ulsan | KR | 56.5 | 35.2 | 60.9 | 53.9 | 97.3 | 1,098,421 | Ulsan |
| 84 | Cali | CO | 56.4 | 25.4 | 79.1 | 58.2 | 94.1 | 2,392,877 | Cali |
| 85 | Xiamen | CN | 56.2 | 42.7 | 56.2 | 40.0 | 99.4 | 4,617,251 | Xiamen City |
| 86 | Nagoya | JP | 56.0 | 15.3 | 89.4 | 67.0 | 93.0 | 2,332,176 | Nagoya |
| 87 | Chicago | US | 55.9 | 7.6 | 89.8 | 81.1 | 93.6 | 2,664,452 | Chicago |
| 88 | Cape Town | ZA | 55.9 | 17.3 | 72.2 | 77.3 | 95.1 | 4,772,846 | Cape Town |
| 89 | Maceió | BR | 55.6 | 20.0 | 56.2 | 84.8 | 97.1 | 1,031,597 | Maceió |
| 90 | Jamshedpur | IN | 55.5 | 54.1 | 63.6 | 9.0 | 96.8 | 1,339,438 | Jamshedpur |
| 91 | Chelyabinsk | RU | 55.4 | 28.8 | 62.3 | 61.0 | 96.1 | 1,202,371 | Chelyabinsk |
| 92 | Longyan | CN | 55.2 | 58.2 | 17.7 | 46.1 | 95.7 | 1,025,087 | Longyan |
| 93 | Quito | EC | 55.2 | 31.8 | 71.1 | 55.4 | 85.6 | 2,781,641 | Quito |
| 94 | Changwon | KR | 54.9 | 39.6 | 55.2 | 53.7 | 86.2 | 1,025,702 | Changwon |
| 95 | Caracas | VE | 54.9 | 40.9 | 71.5 | 47.9 | 73.1 | 3,000,000 | Caracas |
| 96 | Yangon | MM | 54.7 | 37.1 | 68.3 | 37.3 | 93.7 | 4,477,638 | Yangon |
| 97 | Volgograd | RU | 54.6 | 23.3 | 64.2 | 67.9 | 94.6 | 1,013,533 | Volgograd |
| 98 | Cần Thơ | VN | 54.5 | 36.4 | 57.2 | 45.7 | 96.8 | 1,507,187 | Cần Thơ |
| 99 | San Diego | US | 54.5 | 8.3 | 84.8 | 72.7 | 98.3 | 1,404,452 | San Diego |
| 100 | Daegu | KR | 54.4 | 31.1 | 66.9 | 50.9 | 92.2 | 2,365,523 | Daegu |

## Notes

- Stockholm is first (73.7), Montréal second (72.5), Brisbane third (72.2),
  Oslo fourth (72.0), Hong Kong fifth (70.9). The top four are within 1.7
  points, so the order is sensitive to weights.
- The point vegetation component is not citywide canopy. The urban-area
  measure in [URBAN_AREA_AUDIT.md](URBAN_AREA_AUDIT.md) is the better
  citywide denominator; this score keeps the point component so it remains
  comparable to the earlier all-encompassing version.
- All 407 cities: [`reports/all_green_all_407.csv`](../reports/all_green_all_407.csv).
- Raw values: [`reports/urban_scores_all_407.csv`](../reports/urban_scores_all_407.csv)
  and [`reports/all_green_top100.csv`](../reports/all_green_top100.csv).
