# BusBidder Spain - experiment results

## E1 Winning company per profile across all 15 destinations (12-14 July 2027)

| Profile | Camino Express | CostaLink | SolRuta | VerdeBus |
|---|---|---|---|---|
| Example traveller (specification) | 2 | 6 | 5 | 2 |
| Budget traveller | 1 | 3 | 11 | 0 |
| Business traveller | 13 | 0 | 0 | 2 |
| Flexibility-focused tourist | 0 | 0 | 0 | 15 |
| Environment-focused traveller | 0 | 0 | 0 | 15 |

Robust winners (unchanged under +/-10 pp weight shifts), out of 15 destinations: Example traveller (specification) 3, Budget traveller 5, Business traveller 8, Flexibility-focused tourist 7, Environment-focused traveller 0

## E2 Price-weight sweep, example traveller, Madrid -> Malaga

| price weight | top offer | company | departs | EUR | minutes | score |
|---|---|---|---|---|---|---|
| 10% | VER-MAL-0018 | VerdeBus | Wed 14 07:40 | 49.75 | 420 | 80.4 |
| 20% | COS-MAL-0020 | CostaLink | Tue 13 06:25 | 33.25 | 420 | 77.2 |
| 30% | COS-MAL-0020 | CostaLink | Tue 13 06:25 | 33.25 | 420 | 77.8 |
| 40% | COS-MAL-0020 | CostaLink | Tue 13 06:25 | 33.25 | 420 | 78.4 |
| 50% | COS-MAL-0020 | CostaLink | Tue 13 06:25 | 33.25 | 420 | 78.9 |
| 60% | COS-MAL-0020 | CostaLink | Tue 13 06:25 | 33.25 | 420 | 79.5 |
| 70% | SOL-MAL-0014 | SolRuta | Tue 13 06:50 | 30.60 | 490 | 83.3 |

## E3 Robust vs plain min-max normalisation, example traveller

| destination | robust winner | EUR | plain min-max winner | EUR | winner | top-5 overlap |
|---|---|---|---|---|---|---|
| Barcelona | SOL-BAR-0020 | 35.60 | SOL-BAR-0020 | 35.60 | same | 5 |
| Valencia | COS-VAL-9001 | 19.50 | COS-VAL-9001 | 19.50 | same | 5 |
| Seville | SOL-SEV-0010 | 30.60 | SOL-SEV-0010 | 30.60 | same | 5 |
| Malaga | COS-MAL-0020 | 33.25 | COS-MAL-0020 | 33.25 | same | 5 |
| Granada | CAM-GRA-0007 | 31.40 | CAM-GRA-0007 | 31.40 | same | 5 |
| Cordoba | CAM-COR-0007 | 29.70 | CAM-COR-0007 | 29.70 | same | 5 |
| Bilbao | COS-BIL-0020 | 24.85 | COS-BIL-0020 | 24.85 | same | 5 |
| San Sebastian | COS-SSE-0022 | 28.10 | COS-SSE-0022 | 28.10 | same | 4 |
| Zaragoza | VER-ZAR-0012 | 25.95 | VER-ZAR-0012 | 25.95 | same | 5 |
| Alicante | COS-ALI-0024 | 27.10 | COS-ALI-0024 | 27.10 | same | 4 |
| Murcia | SOL-MUR-0014 | 25.10 | SOL-MUR-0014 | 25.10 | same | 5 |
| Salamanca | SOL-SAL-0018 | 12.00 | SOL-SAL-0018 | 12.00 | same | 5 |
| Toledo | VER-TOL-0010 | 7.50 | CAM-TOL-0007 | 6.60 | DIFFERENT | 1 |
| Santiago de Compostela | SOL-SCQ-0018 | 32.95 | SOL-SCQ-0018 | 32.95 | same | 5 |
| Cadiz | COS-CAD-0024 | 40.30 | COS-CAD-0024 | 40.30 | same | 5 |

Winner differs for 1 of 15 destinations.

## E4 Effective influence: mean standard deviation of weighted contribution (points)

| normalisation | price (40%) | duration (25%) | departure (15%) | flexibility (20%) |
|---|---|---|---|---|
| robust | 10.07 | 6.46 | 6.89 | 5.41 |
| plain min-max | 10.41 | 6.68 | 6.89 | 5.41 |

## E5 Outlier stress test: Madrid -> Malaga without budget, one EUR 180 offer injected

| setting | winner | price-score range of normal offers | std dev | top 3 |
|---|---|---|---|---|
| robust, no outlier | COS-MAL-0020 | 0-100 | 25.7 | COS-MAL-0020 COS-MAL-0026 COS-MAL-0014 |
| robust, with outlier | COS-MAL-0020 | 4-100 | 24.6 | COS-MAL-0020 COS-MAL-0026 COS-MAL-0014 |
| plain, no outlier | COS-MAL-0020 | 0-100 | 25.5 | COS-MAL-0020 COS-MAL-0026 COS-MAL-0014 |
| plain, with outlier | CAM-MAL-0022 | 80-100 | 5.1 | CAM-MAL-0022 CAM-MAL-0026 CAM-MAL-0014 |
