# Benchmark-Relative H1 Inference

This analysis compares each valid H1 candidate against matched SPY and sector-ETF trades over the same H1 entry and exit dates. It does not optimize, rank, filter, or alter any strategy parameters.

## Validation checks

- No-lookahead entry decision verified: True
- Entry/exit date match verified: True
- Equal initial notional verified: True
- Stock better defined as excess_pnl > 0: True
- Duplicate candidate-id groups: 0
- Duplicate candidate-id rows: 0

## Exclusions

- Total valid H1 trades: 887
- SPY matched trades: 887
- Sector matched trades: 680
- Sector exclusions due to unknown sector: 184

## Headline results

- SPY candidate mean excess return: +0.9140%
- SPY event mean excess return: +0.4299%
- Sector candidate mean excess return: +0.1908%
- Sector event mean excess return: +0.1826%
- SPY candidate mean excess PnL: $92.93
- Sector candidate mean excess PnL: $19.68

## Positive-excess exact binomial tests

- SPY candidate win rate: 49.83%
- Sector candidate win rate: 49.85%
- SPY binomial p-value: 0.553415
- Sector binomial p-value: 0.545790

## Largest positive excess-return observations

 benchmark candidate_id market_id symbol entry_date exit_date_t_minus_1  excess_return   excess_pnl
       SPY        C0075    569187   PGEN 2025-07-29          2025-08-26       1.692510 16930.782731
sector_etf        C0075    569187   PGEN 2025-07-29          2025-08-26       1.688604 16885.855931
       SPY        C0531   1359830    USO 2026-02-27          2026-03-30       0.662644  6593.601956
       SPY        C1033   1468061    USO 2026-03-02          2026-03-30       0.568011  5619.294891
       SPY        C0637   1472026    USO 2026-03-03          2026-03-30       0.510167  5033.202712
sector_etf        C0072    569182   TNXP 2025-07-29          2025-08-14       0.400797  3998.004974
       SPY        C0072    569182   TNXP 2025-07-29          2025-08-14       0.382061  3817.540483
       SPY        C0985   2297855    HPE 2026-05-19          2026-05-29       0.288137  2889.349757
       SPY        C0999    599915   BLSH 2025-09-17          2025-09-23       0.272249  2708.094044
sector_etf        C1062   2110078   CRCL 2026-04-30          2026-05-08       0.267759  2676.000793

## Largest negative excess-return observations

 benchmark candidate_id                                                          market_id symbol entry_date exit_date_t_minus_1  excess_return   excess_pnl
sector_etf        C0459                                                            1268878   RDDT 2026-01-26          2026-02-04      -0.276877 -2720.255847
       SPY        C0459                                                            1268878   RDDT 2026-01-26          2026-02-04      -0.275672 -2710.380803
       SPY        C1105 0x348cd9adf4f6855f58bd9c6dbf9ff251c4142ef77233a5dc95c65b4b61cd2187    USO 2026-05-12          2026-06-29      -0.261614 -2603.837628
       SPY        C0187                                                             643018   IART 2025-10-23          2025-10-31      -0.259266 -2581.367631
       SPY        C0090                                                             599654    KMX 2025-09-18          2025-10-01      -0.241932 -2409.430968
sector_etf        C0090                                                             599654    KMX 2025-09-18          2025-10-01      -0.235333 -2343.958334
sector_etf        C0187                                                             643018   IART 2025-10-23          2025-10-31      -0.231694 -2315.047907
       SPY        C0479                                                            1293441   COIN 2026-01-29          2026-02-11      -0.227728 -2268.929304
sector_etf        C0479                                                            1293441   COIN 2026-01-29          2026-02-11      -0.215546 -2146.600776
sector_etf        C0452                                                            1254013    EFX 2026-01-26          2026-02-03      -0.211507 -2086.738461

## Sensitivity: symmetric trimming

 benchmark                 level             variant  trim_fraction  trimmed_mean_excess_return
       SPY candidate_observation symmetric_trim_1pct           0.01                    0.006029
       SPY candidate_observation symmetric_trim_5pct           0.05                    0.003576
       SPY        economic_event symmetric_trim_1pct           0.01                    0.000407
       SPY        economic_event symmetric_trim_5pct           0.05                   -0.001374
sector_etf candidate_observation symmetric_trim_1pct           0.01                   -0.000406
sector_etf candidate_observation symmetric_trim_5pct           0.05                   -0.000306
sector_etf        economic_event symmetric_trim_1pct           0.01                   -0.000497
sector_etf        economic_event symmetric_trim_5pct           0.05                   -0.000398

## Notes

- Event-level results use equal-weight economic events derived from the same economic_event_id grouping as the H1 analysis.
- Sector ETF rows exclude Unknown-sector candidates entirely; they are not reassigned to SPY.
- The analysis uses existing H1 trade outputs and the same transaction-cost function.
