# CEM Loss Attribution Report

## baseline / QQQ

**OOS trades: 197** | Total stock PnL: $22,447 | Index component: $15,374 | Selection component: $7,073

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-11,932 |
| Loss from bad selection | $-43,320 |
| Opportunity cost (positive stock, underperformed) | $-2,560 |
|  |  |
| Absolute losing trades | 85 / 197 (43.1%) |
| Losers that beat the index | 6 / 85 (7.1%) |
| Losers that underperformed index | 79 |
| Positive trades that underperformed index | 17 |
| Positive trades that beat index | 95 |
| Trades that added value vs index | 51.3% |
|  |  |
| Avg selection component PnL | $36 |
| Median selection component PnL | $9 |
| Avg index component PnL | $78 |
| Median index component PnL | $67 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-43,320 from bad selection vs $-11,932 from index-down moves).
- Only 7% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (95) than underperformed it (17) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($7,073), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 99 | $-5,086 | $-4,741 | $-345 | 47.5% |
| Late (Mar 22+) | 98 | $27,533 | $20,115 | $7,418 | 55.1% |

## t1_t2 / QQQ

**OOS trades: 208** | Total stock PnL: $24,521 | Index component: $15,275 | Selection component: $9,247

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-12,097 |
| Loss from bad selection | $-40,452 |
| Opportunity cost (positive stock, underperformed) | $-2,301 |
|  |  |
| Absolute losing trades | 71 / 208 (34.1%) |
| Losers that beat the index | 4 / 71 (5.6%) |
| Losers that underperformed index | 67 |
| Positive trades that underperformed index | 22 |
| Positive trades that beat index | 115 |
| Trades that added value vs index | 57.2% |
|  |  |
| Avg selection component PnL | $44 |
| Median selection component PnL | $78 |
| Avg index component PnL | $73 |
| Median index component PnL | $77 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-40,452 from bad selection vs $-12,097 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (115) than underperformed it (22) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($9,247), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 111 | $57 | $-4,062 | $4,119 | 58.6% |
| Late (Mar 22+) | 97 | $24,464 | $19,337 | $5,127 | 55.7% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 36 | $172 | $1,184 | $-1,012 | 50.0% |
| 4 | 129 | $10,256 | $8,145 | $2,111 | 56.6% |
| 5 | 43 | $14,093 | $5,945 | $8,148 | 65.1% |

## t1_t2_t3 / QQQ

**OOS trades: 195** | Total stock PnL: $18,559 | Index component: $12,225 | Selection component: $6,334

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,727 |
| Loss from bad selection | $-34,887 |
| Opportunity cost (positive stock, underperformed) | $-991 |
|  |  |
| Absolute losing trades | 71 / 195 (36.4%) |
| Losers that beat the index | 4 / 71 (5.6%) |
| Losers that underperformed index | 67 |
| Positive trades that underperformed index | 17 |
| Positive trades that beat index | 107 |
| Trades that added value vs index | 56.9% |
|  |  |
| Avg selection component PnL | $32 |
| Median selection component PnL | $18 |
| Avg index component PnL | $63 |
| Median index component PnL | $52 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-34,887 from bad selection vs $-7,727 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (107) than underperformed it (17) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($6,334), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 113 | $-5,905 | $-1,538 | $-4,367 | 55.8% |
| Late (Mar 22+) | 82 | $24,464 | $13,762 | $10,701 | 58.5% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 33 | $-1,854 | $439 | $-2,292 | 51.5% |
| 4 | 130 | $934 | $4,713 | $-3,780 | 56.1% |
| 5 | 32 | $19,479 | $7,073 | $12,406 | 65.6% |

## t1_t2_t3_t4 / QQQ

**OOS trades: 192** | Total stock PnL: $21,644 | Index component: $12,010 | Selection component: $9,635

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,701 |
| Loss from bad selection | $-31,307 |
| Opportunity cost (positive stock, underperformed) | $-1,323 |
|  |  |
| Absolute losing trades | 70 / 192 (36.5%) |
| Losers that beat the index | 4 / 70 (5.7%) |
| Losers that underperformed index | 66 |
| Positive trades that underperformed index | 19 |
| Positive trades that beat index | 103 |
| Trades that added value vs index | 55.7% |
|  |  |
| Avg selection component PnL | $50 |
| Median selection component PnL | $4 |
| Avg index component PnL | $63 |
| Median index component PnL | $19 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-31,307 from bad selection vs $-9,701 from index-down moves).
- Only 6% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (103) than underperformed it (19) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($9,635), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 104 | $-2,822 | $-3,274 | $451 | 54.8% |
| Late (Mar 22+) | 88 | $24,466 | $15,283 | $9,183 | 56.8% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 28 | $962 | $190 | $771 | 57.1% |
| 4 | 123 | $2,342 | $6,110 | $-3,768 | 53.7% |
| 5 | 41 | $18,341 | $5,709 | $12,632 | 61.0% |

## t4_geopriority / QQQ

**OOS trades: 172** | Total stock PnL: $21,165 | Index component: $17,274 | Selection component: $3,891

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,569 |
| Loss from bad selection | $-39,898 |
| Opportunity cost (positive stock, underperformed) | $-937 |
|  |  |
| Absolute losing trades | 75 / 172 (43.6%) |
| Losers that beat the index | 5 / 75 (6.7%) |
| Losers that underperformed index | 70 |
| Positive trades that underperformed index | 8 |
| Positive trades that beat index | 89 |
| Trades that added value vs index | 54.6% |
|  |  |
| Avg selection component PnL | $23 |
| Median selection component PnL | $53 |
| Avg index component PnL | $100 |
| Median index component PnL | $73 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-39,898 from bad selection vs $-7,569 from index-down moves).
- Only 7% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (89) than underperformed it (8) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($3,891), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 86 | $-2,249 | $-3,118 | $869 | 48.8% |
| Late (Mar 22+) | 86 | $23,414 | $20,391 | $3,022 | 60.5% |

## baseline / SPY

**OOS trades: 200** | Total stock PnL: $16,622 | Index component: $9,186 | Selection component: $7,436

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,606 |
| Loss from bad selection | $-42,223 |
| Opportunity cost (positive stock, underperformed) | $-708 |
|  |  |
| Absolute losing trades | 92 / 200 (46.0%) |
| Losers that beat the index | 5 / 92 (5.4%) |
| Losers that underperformed index | 87 |
| Positive trades that underperformed index | 10 |
| Positive trades that beat index | 98 |
| Trades that added value vs index | 51.5% |
|  |  |
| Avg selection component PnL | $37 |
| Median selection component PnL | $7 |
| Avg index component PnL | $46 |
| Median index component PnL | $48 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-42,223 from bad selection vs $-9,606 from index-down moves).
- Only 5% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (98) than underperformed it (10) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($7,436), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 95 | $-7,779 | $-4,287 | $-3,492 | 44.2% |
| Late (Mar 22+) | 105 | $24,401 | $13,473 | $10,928 | 58.1% |

## t1_t2 / SPY

**OOS trades: 224** | Total stock PnL: $25,995 | Index component: $11,063 | Selection component: $14,932

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,418 |
| Loss from bad selection | $-39,958 |
| Opportunity cost (positive stock, underperformed) | $-984 |
|  |  |
| Absolute losing trades | 78 / 224 (34.8%) |
| Losers that beat the index | 2 / 78 (2.6%) |
| Losers that underperformed index | 76 |
| Positive trades that underperformed index | 11 |
| Positive trades that beat index | 135 |
| Trades that added value vs index | 61.2% |
|  |  |
| Avg selection component PnL | $67 |
| Median selection component PnL | $129 |
| Avg index component PnL | $49 |
| Median index component PnL | $49 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-39,958 from bad selection vs $-9,418 from index-down moves).
- Only 3% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (135) than underperformed it (11) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($14,932), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 115 | $2,733 | $-3,048 | $5,781 | 62.6% |
| Late (Mar 22+) | 109 | $23,262 | $14,111 | $9,151 | 59.6% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 31 | $-554 | $1,162 | $-1,715 | 51.6% |
| 4 | 148 | $12,653 | $6,653 | $6,000 | 60.8% |
| 5 | 45 | $13,896 | $3,249 | $10,647 | 68.9% |

## t1_t2_t3 / SPY

**OOS trades: 215** | Total stock PnL: $7,333 | Index component: $7,531 | Selection component: $-199

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,091 |
| Loss from bad selection | $-33,373 |
| Opportunity cost (positive stock, underperformed) | $-1,041 |
|  |  |
| Absolute losing trades | 78 / 215 (36.3%) |
| Losers that beat the index | 4 / 78 (5.1%) |
| Losers that underperformed index | 74 |
| Positive trades that underperformed index | 7 |
| Positive trades that beat index | 130 |
| Trades that added value vs index | 62.3% |
|  |  |
| Avg selection component PnL | $-1 |
| Median selection component PnL | $48 |
| Avg index component PnL | $35 |
| Median index component PnL | $21 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-33,373 from bad selection vs $-7,091 from index-down moves).
- Only 5% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (130) than underperformed it (7) — the stock selection adds value among winning trades.
- Overall selection component is **negative** ($-199), meaning the strategy would have been better off holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 116 | $-4,163 | $-1,279 | $-2,884 | 60.3% |
| Late (Mar 22+) | 99 | $11,495 | $8,810 | $2,685 | 64.7% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 31 | $-1,961 | $818 | $-2,778 | 51.6% |
| 4 | 141 | $3,148 | $5,405 | $-2,256 | 63.1% |
| 5 | 43 | $6,145 | $1,309 | $4,836 | 67.4% |

## t1_t2_t3_t4 / SPY

**OOS trades: 216** | Total stock PnL: $4,016 | Index component: $7,218 | Selection component: $-3,202

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-7,528 |
| Loss from bad selection | $-35,623 |
| Opportunity cost (positive stock, underperformed) | $-393 |
|  |  |
| Absolute losing trades | 82 / 216 (38.0%) |
| Losers that beat the index | 3 / 82 (3.7%) |
| Losers that underperformed index | 79 |
| Positive trades that underperformed index | 7 |
| Positive trades that beat index | 127 |
| Trades that added value vs index | 60.2% |
|  |  |
| Avg selection component PnL | $-15 |
| Median selection component PnL | $52 |
| Avg index component PnL | $33 |
| Median index component PnL | $20 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-35,623 from bad selection vs $-7,528 from index-down moves).
- Only 4% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (127) than underperformed it (7) — the stock selection adds value among winning trades.
- Overall selection component is **negative** ($-3,202), meaning the strategy would have been better off holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 120 | $240 | $-2,418 | $2,657 | 61.7% |
| Late (Mar 22+) | 96 | $3,776 | $9,635 | $-5,859 | 58.3% |

**Per-fold OOS:**

| Fold | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| 3 | 34 | $-1,170 | $595 | $-1,765 | 55.9% |
| 4 | 147 | $-1,217 | $5,679 | $-6,896 | 58.5% |
| 5 | 35 | $6,403 | $944 | $5,460 | 71.4% |

## t4_geopriority / SPY

**OOS trades: 197** | Total stock PnL: $23,593 | Index component: $10,874 | Selection component: $12,720

| Metric | Value |
| --- | --- |
| Loss from index-down moves | $-9,380 |
| Loss from bad selection | $-42,490 |
| Opportunity cost (positive stock, underperformed) | $-396 |
|  |  |
| Absolute losing trades | 81 / 197 (41.1%) |
| Losers that beat the index | 2 / 81 (2.5%) |
| Losers that underperformed index | 79 |
| Positive trades that underperformed index | 5 |
| Positive trades that beat index | 111 |
| Trades that added value vs index | 57.4% |
|  |  |
| Avg selection component PnL | $65 |
| Median selection component PnL | $113 |
| Avg index component PnL | $55 |
| Median index component PnL | $46 |

**Diagnosis:**
- The larger source of absolute losses is **bad stock selection** ($-42,490 from bad selection vs $-9,380 from index-down moves).
- Only 2% of losing trades beat the index — most losses compound market exposure with poor selection.
- More winners beat the index (111) than underperformed it (5) — the stock selection adds value among winning trades.
- Overall selection component is **positive** ($12,720), meaning the strategy adds value relative to holding the index.

| Period | N | Stock PnL | Index Comp | Selection Comp | % Added Value |
| --- | --- | --- | --- | --- | --- |
| Early (pre-Mar 22) | 99 | $-3,405 | $-3,519 | $114 | 53.5% |
| Late (Mar 22+) | 98 | $26,999 | $14,392 | $12,606 | 61.2% |

## Cross-Config OOS Comparison

| Config | Bench | N | Stock PnL | Index Comp | Select Comp | Sel/Trade | % Beat Index | % Losers Beat |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | QQQ | 197 | $22,447 | $15,374 | $7,073 | $36 | 51.3% | 7.1% |
| baseline | SPY | 200 | $16,622 | $9,186 | $7,436 | $37 | 51.5% | 5.4% |
| t1_t2 | QQQ | 208 | $24,521 | $15,275 | $9,247 | $44 | 57.2% | 5.6% |
| t1_t2 | SPY | 224 | $25,995 | $11,063 | $14,932 | $67 | 61.2% | 2.6% |
| t1_t2_t3 | QQQ | 195 | $18,559 | $12,225 | $6,334 | $32 | 56.9% | 5.6% |
| t1_t2_t3 | SPY | 215 | $7,333 | $7,531 | $-199 | $-1 | 62.3% | 5.1% |
| t1_t2_t3_t4 | QQQ | 192 | $21,644 | $12,010 | $9,635 | $50 | 55.7% | 5.7% |
| t1_t2_t3_t4 | SPY | 216 | $4,016 | $7,218 | $-3,202 | $-15 | 60.2% | 3.7% |
| t4_geopriority | QQQ | 172 | $21,165 | $17,274 | $3,891 | $23 | 54.6% | 6.7% |
| t4_geopriority | SPY | 197 | $23,593 | $10,874 | $12,720 | $65 | 57.4% | 2.5% |
