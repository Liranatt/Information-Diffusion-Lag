# CEM Loss Attribution Report

## baseline / QQQ

**OOS trades: 204** | Total stock PnL: $16,136 | Index component: $15,042 | Selection component: $1,094

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-11,064 |
| Loss from bad selection | $-46,037 |
| Opportunity cost (positive stock, underperformed) | $-1,273 |
|  |  |
| Absolute losing trades | 90 / 204 (44.1%) |
| Losers that beat the index | 4 / 90 (4.4%) |
| Losers that underperformed index | 86 |
| Positive trades that underperformed index | 14 |
| Positive trades that beat index | 100 |
| Trades that added value vs index | 51.0% |
|  |  |
| Avg selection component PnL | $5 |
| Median selection component PnL | $2 |
| Avg index component PnL | $74 |
| Median index component PnL | $66 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-46,037 from bad selection vs $-11,064 from index-down moves).
- Only 4% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (100) than underperformed it (14) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($1,094), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 107 | $-5,168 | $-3,090 | $-2,078 | 46.7% |
| Late (Mar 22+) | 97 | $21,304 | $18,132 | $3,172 | 55.7% |

## t1_frictionpenalty / QQQ

**OOS trades: 153** | Total stock PnL: $28,080 | Index component: $17,094 | Selection component: $10,986

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-8,331 |
| Loss from bad selection | $-43,475 |
| Opportunity cost (positive stock, underperformed) | $-3,099 |
|  |  |
| Absolute losing trades | 65 / 153 (42.5%) |
| Losers that beat the index | 4 / 65 (6.2%) |
| Losers that underperformed index | 61 |
| Positive trades that underperformed index | 12 |
| Positive trades that beat index | 76 |
| Trades that added value vs index | 52.3% |
|  |  |
| Avg selection component PnL | $72 |
| Median selection component PnL | $18 |
| Avg index component PnL | $112 |
| Median index component PnL | $64 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-43,475 from bad selection vs $-8,331 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (76) than underperformed it (12) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($10,986), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 81 | $1,192 | $-5,060 | $6,252 | 56.8% |
| Late (Mar 22+) | 72 | $26,888 | $22,154 | $4,733 | 47.2% |

## t1_t2 / QQQ

**OOS trades: 208** | Total stock PnL: $26,381 | Index component: $15,711 | Selection component: $10,670

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-11,523 |
| Loss from bad selection | $-38,123 |
| Opportunity cost (positive stock, underperformed) | $-2,310 |
|  |  |
| Absolute losing trades | 66 / 208 (31.7%) |
| Losers that beat the index | 4 / 66 (6.1%) |
| Losers that underperformed index | 62 |
| Positive trades that underperformed index | 23 |
| Positive trades that beat index | 119 |
| Trades that added value vs index | 59.1% |
|  |  |
| Avg selection component PnL | $51 |
| Median selection component PnL | $92 |
| Avg index component PnL | $76 |
| Median index component PnL | $72 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-38,123 from bad selection vs $-11,523 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (119) than underperformed it (23) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($10,670), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 111 | $314 | $-4,049 | $4,363 | 59.5% |
| Late (Mar 22+) | 97 | $26,067 | $19,760 | $6,307 | 58.8% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 36 | $172 | $1,184 | $-1,012 | 50.0% |
| 4 | 127 | $10,235 | $7,616 | $2,619 | 57.5% |
| 5 | 45 | $15,974 | $6,911 | $9,063 | 71.1% |

## t1_t2_t3 / QQQ

**OOS trades: 183** | Total stock PnL: $22,045 | Index component: $16,409 | Selection component: $5,636

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,100 |
| Loss from bad selection | $-38,401 |
| Opportunity cost (positive stock, underperformed) | $-1,243 |
|  |  |
| Absolute losing trades | 60 / 183 (32.8%) |
| Losers that beat the index | 4 / 60 (6.7%) |
| Losers that underperformed index | 56 |
| Positive trades that underperformed index | 15 |
| Positive trades that beat index | 108 |
| Trades that added value vs index | 61.2% |
|  |  |
| Avg selection component PnL | $31 |
| Median selection component PnL | $21 |
| Avg index component PnL | $90 |
| Median index component PnL | $54 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-38,401 from bad selection vs $-9,100 from index-down moves).
- Only 7% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (108) than underperformed it (15) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($5,636), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 95 | $4,575 | $-1,130 | $5,706 | 62.1% |
| Late (Mar 22+) | 88 | $17,470 | $17,540 | $-70 | 60.2% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 24 | $-253 | $1,245 | $-1,498 | 62.5% |
| 4 | 117 | $10,762 | $8,340 | $2,423 | 59.8% |
| 5 | 42 | $11,536 | $6,825 | $4,711 | 64.3% |

## t1_t2_t3_t4 / QQQ

**OOS trades: 190** | Total stock PnL: $22,805 | Index component: $13,023 | Selection component: $9,782

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-8,262 |
| Loss from bad selection | $-29,914 |
| Opportunity cost (positive stock, underperformed) | $-1,067 |
|  |  |
| Absolute losing trades | 68 / 190 (35.8%) |
| Losers that beat the index | 4 / 68 (5.9%) |
| Losers that underperformed index | 64 |
| Positive trades that underperformed index | 17 |
| Positive trades that beat index | 105 |
| Trades that added value vs index | 57.4% |
|  |  |
| Avg selection component PnL | $51 |
| Median selection component PnL | $26 |
| Avg index component PnL | $69 |
| Median index component PnL | $44 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-29,914 from bad selection vs $-8,262 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (105) than underperformed it (17) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($9,782), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 107 | $-1,071 | $-2,371 | $1,300 | 55.1% |
| Late (Mar 22+) | 83 | $23,876 | $15,393 | $8,482 | 60.2% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 33 | $222 | $-583 | $805 | 57.6% |
| 4 | 118 | $3,456 | $7,517 | $-4,061 | 52.5% |
| 5 | 39 | $19,127 | $6,089 | $13,038 | 71.8% |

## t1_t3 / QQQ

**OOS trades: 148** | Total stock PnL: $23,145 | Index component: $13,929 | Selection component: $9,215

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,643 |
| Loss from bad selection | $-32,161 |
| Opportunity cost (positive stock, underperformed) | $-1,046 |
|  |  |
| Absolute losing trades | 62 / 148 (41.9%) |
| Losers that beat the index | 7 / 62 (11.3%) |
| Losers that underperformed index | 55 |
| Positive trades that underperformed index | 10 |
| Positive trades that beat index | 76 |
| Trades that added value vs index | 56.1% |
|  |  |
| Avg selection component PnL | $62 |
| Median selection component PnL | $37 |
| Avg index component PnL | $94 |
| Median index component PnL | $51 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-32,161 from bad selection vs $-7,643 from index-down moves).
- Only 11% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (76) than underperformed it (10) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($9,215), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 71 | $-1,424 | $-3,169 | $1,744 | 56.3% |
| Late (Mar 22+) | 77 | $24,569 | $17,098 | $7,471 | 55.8% |

## t2_t3 / QQQ

**OOS trades: 164** | Total stock PnL: $27,279 | Index component: $10,833 | Selection component: $16,446

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-6,606 |
| Loss from bad selection | $-26,968 |
| Opportunity cost (positive stock, underperformed) | $-1,357 |
|  |  |
| Absolute losing trades | 84 / 164 (51.2%) |
| Losers that beat the index | 7 / 84 (8.3%) |
| Losers that underperformed index | 77 |
| Positive trades that underperformed index | 9 |
| Positive trades that beat index | 71 |
| Trades that added value vs index | 47.6% |
|  |  |
| Avg selection component PnL | $100 |
| Median selection component PnL | $-7 |
| Avg index component PnL | $66 |
| Median index component PnL | $39 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-26,968 from bad selection vs $-6,606 from index-down moves).
- Only 8% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (71) than underperformed it (9) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($16,446), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 82 | $-7,985 | $-2,743 | $-5,241 | 43.9% |
| Late (Mar 22+) | 82 | $35,264 | $13,577 | $21,687 | 51.2% |

## t2_trainwindows / QQQ

**OOS trades: 149** | Total stock PnL: $35,151 | Index component: $15,136 | Selection component: $20,016

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,542 |
| Loss from bad selection | $-33,614 |
| Opportunity cost (positive stock, underperformed) | $-2,131 |
|  |  |
| Absolute losing trades | 72 / 149 (48.3%) |
| Losers that beat the index | 7 / 72 (9.7%) |
| Losers that underperformed index | 65 |
| Positive trades that underperformed index | 12 |
| Positive trades that beat index | 65 |
| Trades that added value vs index | 48.3% |
|  |  |
| Avg selection component PnL | $134 |
| Median selection component PnL | $-38 |
| Avg index component PnL | $102 |
| Median index component PnL | $64 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-33,614 from bad selection vs $-7,542 from index-down moves).
- Only 10% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (65) than underperformed it (12) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($20,016), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 78 | $-5,078 | $-3,418 | $-1,660 | 47.4% |
| Late (Mar 22+) | 71 | $40,229 | $18,554 | $21,675 | 49.3% |

## t3_kelly / QQQ

**OOS trades: 149** | Total stock PnL: $9,660 | Index component: $9,424 | Selection component: $236

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-4,517 |
| Loss from bad selection | $-30,076 |
| Opportunity cost (positive stock, underperformed) | $-1,180 |
|  |  |
| Absolute losing trades | 75 / 149 (50.3%) |
| Losers that beat the index | 6 / 75 (8.0%) |
| Losers that underperformed index | 69 |
| Positive trades that underperformed index | 11 |
| Positive trades that beat index | 63 |
| Trades that added value vs index | 46.3% |
|  |  |
| Avg selection component PnL | $2 |
| Median selection component PnL | $-19 |
| Avg index component PnL | $63 |
| Median index component PnL | $40 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-30,076 from bad selection vs $-4,517 from index-down moves).
- Only 8% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (63) than underperformed it (11) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($236), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 75 | $-6,504 | $-2,236 | $-4,268 | 46.7% |
| Late (Mar 22+) | 74 | $16,165 | $11,661 | $4,504 | 46.0% |

## t4_geopriority / QQQ

**OOS trades: 182** | Total stock PnL: $26,839 | Index component: $17,056 | Selection component: $9,783

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,718 |
| Loss from bad selection | $-39,399 |
| Opportunity cost (positive stock, underperformed) | $-902 |
|  |  |
| Absolute losing trades | 79 / 182 (43.4%) |
| Losers that beat the index | 6 / 79 (7.6%) |
| Losers that underperformed index | 73 |
| Positive trades that underperformed index | 10 |
| Positive trades that beat index | 93 |
| Trades that added value vs index | 54.4% |
|  |  |
| Avg selection component PnL | $54 |
| Median selection component PnL | $42 |
| Avg index component PnL | $94 |
| Median index component PnL | $80 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-39,399 from bad selection vs $-9,718 from index-down moves).
- Only 8% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (93) than underperformed it (10) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($9,783), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 92 | $-2,038 | $-3,190 | $1,152 | 48.9% |
| Late (Mar 22+) | 90 | $28,878 | $20,246 | $8,632 | 60.0% |

## baseline / SPY

**OOS trades: 201** | Total stock PnL: $25,795 | Index component: $10,269 | Selection component: $15,526

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,332 |
| Loss from bad selection | $-41,278 |
| Opportunity cost (positive stock, underperformed) | $-387 |
|  |  |
| Absolute losing trades | 84 / 201 (41.8%) |
| Losers that beat the index | 3 / 84 (3.6%) |
| Losers that underperformed index | 81 |
| Positive trades that underperformed index | 8 |
| Positive trades that beat index | 109 |
| Trades that added value vs index | 55.7% |
|  |  |
| Avg selection component PnL | $77 |
| Median selection component PnL | $95 |
| Avg index component PnL | $51 |
| Median index component PnL | $51 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-41,278 from bad selection vs $-9,332 from index-down moves).
- Only 4% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (109) than underperformed it (8) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($15,526), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 99 | $-6,333 | $-3,586 | $-2,746 | 48.5% |
| Late (Mar 22+) | 102 | $32,128 | $13,855 | $18,272 | 62.8% |

## t1_frictionpenalty / SPY

**OOS trades: 169** | Total stock PnL: $27,364 | Index component: $8,712 | Selection component: $18,652

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-5,453 |
| Loss from bad selection | $-28,339 |
| Opportunity cost (positive stock, underperformed) | $-935 |
|  |  |
| Absolute losing trades | 69 / 169 (40.8%) |
| Losers that beat the index | 4 / 69 (5.8%) |
| Losers that underperformed index | 65 |
| Positive trades that underperformed index | 10 |
| Positive trades that beat index | 90 |
| Trades that added value vs index | 55.6% |
|  |  |
| Avg selection component PnL | $110 |
| Median selection component PnL | $78 |
| Avg index component PnL | $52 |
| Median index component PnL | $46 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-28,339 from bad selection vs $-5,453 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (90) than underperformed it (10) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($18,652), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 85 | $-141 | $-2,848 | $2,708 | 55.3% |
| Late (Mar 22+) | 84 | $27,505 | $11,560 | $15,945 | 56.0% |

## t1_t2 / SPY

**OOS trades: 218** | Total stock PnL: $27,117 | Index component: $11,847 | Selection component: $15,269

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,075 |
| Loss from bad selection | $-38,989 |
| Opportunity cost (positive stock, underperformed) | $-696 |
|  |  |
| Absolute losing trades | 80 / 218 (36.7%) |
| Losers that beat the index | 2 / 80 (2.5%) |
| Losers that underperformed index | 78 |
| Positive trades that underperformed index | 7 |
| Positive trades that beat index | 131 |
| Trades that added value vs index | 61.0% |
|  |  |
| Avg selection component PnL | $70 |
| Median selection component PnL | $128 |
| Avg index component PnL | $54 |
| Median index component PnL | $52 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-38,989 from bad selection vs $-9,075 from index-down moves).
- Only 2% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (131) than underperformed it (7) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($15,269), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 112 | $-1,933 | $-3,188 | $1,254 | 57.1% |
| Late (Mar 22+) | 106 | $29,050 | $15,035 | $14,015 | 65.1% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 35 | $-2,261 | $1,079 | $-3,340 | 48.6% |
| 4 | 135 | $11,213 | $6,362 | $4,851 | 60.0% |
| 5 | 48 | $18,166 | $4,407 | $13,759 | 72.9% |

## t1_t2_t3 / SPY

**OOS trades: 224** | Total stock PnL: $24,076 | Index component: $9,781 | Selection component: $14,295

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,018 |
| Loss from bad selection | $-32,870 |
| Opportunity cost (positive stock, underperformed) | $-840 |
|  |  |
| Absolute losing trades | 80 / 224 (35.7%) |
| Losers that beat the index | 2 / 80 (2.5%) |
| Losers that underperformed index | 78 |
| Positive trades that underperformed index | 9 |
| Positive trades that beat index | 135 |
| Trades that added value vs index | 61.2% |
|  |  |
| Avg selection component PnL | $64 |
| Median selection component PnL | $42 |
| Avg index component PnL | $44 |
| Median index component PnL | $21 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-32,870 from bad selection vs $-7,018 from index-down moves).
- Only 2% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (135) than underperformed it (9) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($14,295), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 124 | $-3,774 | $-1,505 | $-2,268 | 58.1% |
| Late (Mar 22+) | 100 | $27,849 | $11,286 | $16,563 | 65.0% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 33 | $-580 | $645 | $-1,225 | 54.5% |
| 4 | 156 | $5,227 | $6,125 | $-898 | 59.6% |
| 5 | 35 | $19,429 | $3,010 | $16,419 | 74.3% |

## t1_t2_t3_t4 / SPY

**OOS trades: 203** | Total stock PnL: $20,930 | Index component: $6,591 | Selection component: $14,339

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-8,777 |
| Loss from bad selection | $-31,177 |
| Opportunity cost (positive stock, underperformed) | $-356 |
|  |  |
| Absolute losing trades | 70 / 203 (34.5%) |
| Losers that beat the index | 4 / 70 (5.7%) |
| Losers that underperformed index | 66 |
| Positive trades that underperformed index | 6 |
| Positive trades that beat index | 127 |
| Trades that added value vs index | 64.5% |
|  |  |
| Avg selection component PnL | $71 |
| Median selection component PnL | $74 |
| Avg index component PnL | $32 |
| Median index component PnL | $25 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-31,177 from bad selection vs $-8,777 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (127) than underperformed it (6) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($14,339), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 107 | $1,814 | $-3,812 | $5,625 | 67.3% |
| Late (Mar 22+) | 96 | $19,117 | $10,403 | $8,714 | 61.5% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 33 | $110 | $93 | $17 | 63.6% |
| 4 | 127 | $4,090 | $4,063 | $28 | 63.0% |
| 5 | 43 | $16,730 | $2,435 | $14,295 | 69.8% |

## t1_t3 / SPY

**OOS trades: 166** | Total stock PnL: $26,403 | Index component: $9,068 | Selection component: $17,335

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-5,051 |
| Loss from bad selection | $-31,756 |
| Opportunity cost (positive stock, underperformed) | $-489 |
|  |  |
| Absolute losing trades | 70 / 166 (42.2%) |
| Losers that beat the index | 4 / 70 (5.7%) |
| Losers that underperformed index | 66 |
| Positive trades that underperformed index | 10 |
| Positive trades that beat index | 86 |
| Trades that added value vs index | 54.2% |
|  |  |
| Avg selection component PnL | $104 |
| Median selection component PnL | $17 |
| Avg index component PnL | $55 |
| Median index component PnL | $33 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-31,756 from bad selection vs $-5,051 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (86) than underperformed it (10) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($17,335), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 86 | $-2,039 | $-1,888 | $-152 | 52.3% |
| Late (Mar 22+) | 80 | $28,442 | $10,956 | $17,487 | 56.2% |

## t2_t3 / SPY

**OOS trades: 165** | Total stock PnL: $11,813 | Index component: $7,766 | Selection component: $4,047

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-4,755 |
| Loss from bad selection | $-28,614 |
| Opportunity cost (positive stock, underperformed) | $-482 |
|  |  |
| Absolute losing trades | 79 / 165 (47.9%) |
| Losers that beat the index | 8 / 79 (10.1%) |
| Losers that underperformed index | 71 |
| Positive trades that underperformed index | 9 |
| Positive trades that beat index | 77 |
| Trades that added value vs index | 51.5% |
|  |  |
| Avg selection component PnL | $25 |
| Median selection component PnL | $7 |
| Avg index component PnL | $47 |
| Median index component PnL | $30 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-28,614 from bad selection vs $-4,755 from index-down moves).
- Only 10% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (77) than underperformed it (9) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($4,047), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 85 | $-2,463 | $-1,571 | $-892 | 50.6% |
| Late (Mar 22+) | 80 | $14,276 | $9,337 | $4,939 | 52.5% |

## t2_trainwindows / SPY

**OOS trades: 153** | Total stock PnL: $27,470 | Index component: $9,314 | Selection component: $18,155

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-6,508 |
| Loss from bad selection | $-37,797 |
| Opportunity cost (positive stock, underperformed) | $-1,007 |
|  |  |
| Absolute losing trades | 73 / 153 (47.7%) |
| Losers that beat the index | 8 / 73 (11.0%) |
| Losers that underperformed index | 65 |
| Positive trades that underperformed index | 4 |
| Positive trades that beat index | 76 |
| Trades that added value vs index | 54.9% |
|  |  |
| Avg selection component PnL | $119 |
| Median selection component PnL | $61 |
| Avg index component PnL | $61 |
| Median index component PnL | $55 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-37,797 from bad selection vs $-6,508 from index-down moves).
- Only 11% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (76) than underperformed it (4) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($18,155), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 81 | $-9,293 | $-2,778 | $-6,515 | 48.1% |
| Late (Mar 22+) | 72 | $36,763 | $12,092 | $24,671 | 62.5% |

## t3_kelly / SPY

**OOS trades: 163** | Total stock PnL: $12,711 | Index component: $8,316 | Selection component: $4,395

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-4,791 |
| Loss from bad selection | $-29,927 |
| Opportunity cost (positive stock, underperformed) | $-582 |
|  |  |
| Absolute losing trades | 74 / 163 (45.4%) |
| Losers that beat the index | 8 / 74 (10.8%) |
| Losers that underperformed index | 66 |
| Positive trades that underperformed index | 10 |
| Positive trades that beat index | 79 |
| Trades that added value vs index | 53.4% |
|  |  |
| Avg selection component PnL | $27 |
| Median selection component PnL | $24 |
| Avg index component PnL | $51 |
| Median index component PnL | $34 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-29,927 from bad selection vs $-4,791 from index-down moves).
- Only 11% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (79) than underperformed it (10) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($4,395), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 79 | $-4,594 | $-1,751 | $-2,844 | 53.2% |
| Late (Mar 22+) | 84 | $17,305 | $10,067 | $7,238 | 53.6% |

## t4_geopriority / SPY

**OOS trades: 197** | Total stock PnL: $23,169 | Index component: $11,040 | Selection component: $12,129

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,190 |
| Loss from bad selection | $-42,249 |
| Opportunity cost (positive stock, underperformed) | $-332 |
|  |  |
| Absolute losing trades | 80 / 197 (40.6%) |
| Losers that beat the index | 2 / 80 (2.5%) |
| Losers that underperformed index | 78 |
| Positive trades that underperformed index | 6 |
| Positive trades that beat index | 111 |
| Trades that added value vs index | 57.4% |
|  |  |
| Avg selection component PnL | $62 |
| Median selection component PnL | $92 |
| Avg index component PnL | $56 |
| Median index component PnL | $49 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-42,249 from bad selection vs $-9,190 from index-down moves).
- Only 2% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (111) than underperformed it (6) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($12,129), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 98 | $-3,823 | $-3,509 | $-315 | 52.0% |
| Late (Mar 22+) | 99 | $26,993 | $14,548 | $12,444 | 62.6% |

## Cross-Config OOS Comparison

| Config | Bench | N | Stock PnL | Index Comp | Select Comp | Sel/Trade | % Beat Index | % Losers Beat |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | QQQ | 204 | $16,136 | $15,042 | $1,094 | $5 | 51.0% | 4.4% |
| baseline | SPY | 201 | $25,795 | $10,269 | $15,526 | $77 | 55.7% | 3.6% |
| t1_frictionpenalty | QQQ | 153 | $28,080 | $17,094 | $10,986 | $72 | 52.3% | 6.2% |
| t1_frictionpenalty | SPY | 169 | $27,364 | $8,712 | $18,652 | $110 | 55.6% | 5.8% |
| t1_t2 | QQQ | 208 | $26,381 | $15,711 | $10,670 | $51 | 59.1% | 6.1% |
| t1_t2 | SPY | 218 | $27,117 | $11,847 | $15,269 | $70 | 61.0% | 2.5% |
| t1_t2_t3 | QQQ | 183 | $22,045 | $16,409 | $5,636 | $31 | 61.2% | 6.7% |
| t1_t2_t3 | SPY | 224 | $24,076 | $9,781 | $14,295 | $64 | 61.2% | 2.5% |
| t1_t2_t3_t4 | QQQ | 190 | $22,805 | $13,023 | $9,782 | $51 | 57.4% | 5.9% |
| t1_t2_t3_t4 | SPY | 203 | $20,930 | $6,591 | $14,339 | $71 | 64.5% | 5.7% |
| t1_t3 | QQQ | 148 | $23,145 | $13,929 | $9,215 | $62 | 56.1% | 11.3% |
| t1_t3 | SPY | 166 | $26,403 | $9,068 | $17,335 | $104 | 54.2% | 5.7% |
| t2_t3 | QQQ | 164 | $27,279 | $10,833 | $16,446 | $100 | 47.6% | 8.3% |
| t2_t3 | SPY | 165 | $11,813 | $7,766 | $4,047 | $25 | 51.5% | 10.1% |
| t2_trainwindows | QQQ | 149 | $35,151 | $15,136 | $20,016 | $134 | 48.3% | 9.7% |
| t2_trainwindows | SPY | 153 | $27,470 | $9,314 | $18,155 | $119 | 54.9% | 11.0% |
| t3_kelly | QQQ | 149 | $9,660 | $9,424 | $236 | $2 | 46.3% | 8.0% |
| t3_kelly | SPY | 163 | $12,711 | $8,316 | $4,395 | $27 | 53.4% | 10.8% |
| t4_geopriority | QQQ | 182 | $26,839 | $17,056 | $9,783 | $54 | 54.4% | 7.6% |
| t4_geopriority | SPY | 197 | $23,169 | $11,040 | $12,129 | $62 | 57.4% | 2.5% |
