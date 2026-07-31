# ניתוח בחירת הטריידים והזדמנויות שהוחמצו

## היקף העבודה

הניתוח מבוסס **רק על הקבצים המקומיים שהועלו לשיחה** ועל פלטי הריצה המקומיים שכבר נוצרו. לא נעשה שימוש ב־GitHub או בקוד מהריפו.

בוצעו ארבעה רבדים נפרדים:

1. ריצה רעיונית של כל מועמד כשלעצמו, ללא מגבלת קיבולת, הן לפי מנגנון ה־hard cap הנוכחי והן ביציאה כפויה ב־\(T_e-1\).
2. השוואה בין טריידים שנבחרו בפועל לבין טריידים זכאים שלא קיבלו הון.
3. ניתוח תחרות בין מועמדים בעלי אותו תאריך כניסה בפועל.
4. ניתוח נפרד של earnings, גיאופוליטיקה, קשר לסקטור/מדדים, וכללי דירוג ויציאה חלופיים.

כדי לא לנפח את התוצאה הגיאופוליטית, המסקנות המרכזיות משתמשות גם ברמת **symbol-day**: מניה אחת ביום אחד נחשבת חשיפה אחת, גם אם מספר שווקי Polymarket יצרו את אותו טרייד.

---

# 1. האם “לסחור בכולם ולצאת ב־\(T_e-1\)” באמת חיובי?

| aggregation       |   n |   train mean % |   test mean % |   overall mean % |
|:------------------|----:|---------------:|--------------:|-----------------:|
| Candidate rows    | 887 |         -0.013 |         2.038 |            1.208 |
| Unique symbol-day | 786 |          0.279 |         0.861 |            0.619 |

ברמת כל שורת מועמד מתקבל ממוצע של **+1.21%**, אך לאחר איחוד אותות כפולים לאותה מניה ולאותו יום הממוצע יורד ל־**+0.62%**. החציון ברמת symbol-day כמעט אפס ושלילי מעט. לכן קיימת תוחלת חיובית, אבל היא אינה אחידה, וחלק משמעותי מהכותרת הגולמית נובע מכפילויות ומאשכולות חזקים.

| split   | event_family   |   n |   mean_net_return_pct |   median_net_return_pct |   win_rate |
|:--------|:---------------|----:|----------------------:|------------------------:|-----------:|
| test    | earnings       | 411 |                 0.348 |                   0.053 |     50.608 |
| test    | geo            |  42 |                 6.276 |                   2.397 |     54.762 |
| train   | earnings       | 281 |                -0.459 |                  -0.321 |     47.687 |
| train   | geo            |  29 |                -0.804 |                  -1.223 |     44.828 |

הפירוש:

- **Earnings:** במצטבר התוחלת כמעט אפס. היא שלילית ב־train וחיובית מעט ב־test.
- **Geo:** שלילי ב־train וחיובי מאוד ב־test. זהו שינוי משטר, לא אפקט יציב שהוכח לאורך שתי התקופות.
- לכן עצם קיומה של תוחלת גולמית אינו אומר שצריך לקנות כל מועמד. בעיית הבחירה והקיבולת אמיתית.

---

# 2. מה קורה בטריידים שהפורטפוליו כן מבצע?

| benchmark   | split   | event_family   |   n_trades |   mean_net_return_pct |   mean_active_return_pct |   nominal_win_rate |   total_net_pnl |
|:------------|:--------|:---------------|-----------:|----------------------:|-------------------------:|-------------------:|----------------:|
| QQQ         | test    | earnings       |        164 |                 0.308 |                   -0.411 |             52.439 |        5213.180 |
| QQQ         | test    | geo            |         10 |                 1.114 |                    1.190 |             50.000 |         949.720 |
| QQQ         | train   | earnings       |        102 |                 0.440 |                    0.331 |             62.745 |        5673.720 |
| QQQ         | train   | geo            |         18 |                 0.600 |                   -0.119 |             66.667 |        1011.600 |
| SPY         | test    | earnings       |        207 |                 0.335 |                   -0.062 |             55.072 |        7298.320 |
| SPY         | test    | geo            |          8 |                 3.748 |                    4.022 |             62.500 |        2941.900 |
| SPY         | train   | earnings       |        104 |                 0.560 |                    0.364 |             62.500 |        7234.720 |
| SPY         | train   | geo            |         20 |                 0.004 |                   -0.441 |             55.000 |        -117.640 |

הנקודה המרכזית היא ההבדל בין **רווח נומינלי** לבין **רווח אקטיבי מול המדד**:

- ב־SPY test, טריידי earnings מרוויחים בממוצע כ־**0.33%**, אבל כמעט לא מוסיפים ערך מול SPY: כ־**−0.06% אקטיבי**.
- ב־QQQ test, earnings מרוויחים נומינלית כ־**0.31%**, אך מפגרים אחרי QQQ בכ־**−0.41%** בממוצע.
- טריידי geo שנבחרו ב־SPY test הם בעלי הערך האקטיבי הגבוה ביותר: כ־**+4.02%** בממוצע, אך המדגם קטן.
- כלומר הבעיה אינה שהמניות תמיד מפסידות. לעיתים קרובות הן עולות, אבל פחות מהנכס שממנו הוצא ההון.

## הטריידים הנבחרים הגרועים ביותר ב־test

| benchmark   | entry_date   | symbol   | event_family   |   connection_strength | exit_reason      |   pnl_pct |   approx_active_net_pct | question                                                              |
|:------------|:-------------|:---------|:---------------|----------------------:|:-----------------|----------:|------------------------:|:----------------------------------------------------------------------|
| SPY         | 2026-03-30   | XLE      | geo            |                  0.60 | hard_loss_6.00%  |     -8.74 |                  -15.77 | Will Iran take military action against a Gulf State on April 3, 2026? |
| QQQ         | 2026-03-31   | XLE      | geo            |                  0.60 | trailing_3.53ATR |     -8.08 |                  -13.09 | Will Iran take military action against a Gulf State on April 4, 2026? |
| QQQ         | 2026-05-06   | AS       | earnings       |                  0.95 | hard_loss_8.00%  |     -8.22 |                  -10.74 | Will Amer Sports (AS) beat quarterly earnings?                        |
| QQQ         | 2026-01-22   | ARM      | earnings       |                  0.95 | hard_loss_8.00%  |     -8.21 |                  -10.22 | Will Arm Holdings (ARM) beat quarterly earnings?                      |
| QQQ         | 2026-03-31   | JNJ      | earnings       |                  1.00 | resolution-1d    |     -2.87 |                   -9.83 | Will Johnson & Johnson (JNJ) beat quarterly earnings?                 |
| QQQ         | 2026-04-21   | RBLX     | earnings       |                  0.95 | hard_loss_8.00%  |     -8.22 |                   -9.32 | Will Roblox (RBLX) beat quarterly earnings?                           |
| QQQ         | 2026-04-20   | RDDT     | earnings       |                  0.95 | hard_loss_8.00%  |     -8.21 |                   -8.93 | Will Reddit (RDDT) beat quarterly earnings?                           |
| SPY         | 2026-03-31   | JNJ      | earnings       |                  1.00 | resolution-1d    |     -2.87 |                   -8.37 | Will Johnson & Johnson (JNJ) beat quarterly earnings?                 |
| SPY         | 2026-01-21   | CMG      | earnings       |                  1.00 | hard_loss_6.00%  |     -6.22 |                   -7.68 | Will Chipotle Mexican Grill Inc (CMG) beat quarterly earnings?        |
| SPY         | 2026-05-07   | HD       | earnings       |                  0.98 | hard_loss_6.00%  |     -6.21 |                   -7.68 | Will Home Depot (HD) beat quarterly earnings?                         |
| SPY         | 2026-04-10   | CME      | earnings       |                  0.95 | resolution-1d    |     -3.91 |                   -7.53 | Will CME Group (CME) beat quarterly earnings?                         |
| SPY         | 2026-01-09   | COF      | earnings       |                  0.96 | hard_loss_6.00%  |     -7.94 |                   -7.45 | Will Capital One (COF) beat quarterly earnings?                       |

ההפסדים הגדולים מתרכזים בשתי קבוצות:

- earnings שקיבלו מקום בקיבולת אך פיגרו מהותית אחרי המדד;
- XLE גיאופוליטי, שהיה חלש גם ברמת הקבוצה ולא רק בטרייד בודד.

---

# 3. אילו טריידים שלא בוצעו היו רווחיים?

| benchmark   | split   | event_family   |   unique_stock_days |   selected_stock_days |   missed_stock_days |   missed_profitable_hardcap_n |   missed_profitable_te1_n |   missed_active_positive_hardcap_n |   selected_hardcap_mean_pct |   missed_hardcap_mean_pct |
|:------------|:--------|:---------------|--------------------:|----------------------:|--------------------:|------------------------------:|--------------------------:|-----------------------------------:|----------------------------:|--------------------------:|
| QQQ         | test    | earnings       |                 352 |                   164 |                 188 |                           104 |                        96 |                                 88 |                       0.544 |                     0.248 |
| QQQ         | test    | geo            |                  26 |                    10 |                  16 |                             8 |                        10 |                                  7 |                       1.339 |                    -0.774 |
| QQQ         | train   | earnings       |                 221 |                   102 |                 119 |                            55 |                        48 |                                 58 |                       0.671 |                    -0.820 |
| QQQ         | train   | geo            |                  25 |                    18 |                   7 |                             4 |                         4 |                                  1 |                       0.161 |                    -0.287 |
| SPY         | test    | earnings       |                 450 |                   207 |                 243 |                           119 |                       117 |                                106 |                       0.577 |                    -0.278 |
| SPY         | test    | geo            |                  20 |                     8 |                  12 |                             7 |                         8 |                                  6 |                       3.975 |                     1.656 |
| SPY         | train   | earnings       |                 271 |                   104 |                 167 |                            93 |                        75 |                                 87 |                       0.791 |                    -0.145 |
| SPY         | train   | geo            |                  26 |                    20 |                   6 |                             2 |                         2 |                                  0 |                       0.545 |                    -2.568 |

דוגמאות מהותיות לטריידים שלא נבחרו ב־test:

| benchmark   | entry_date   | symbol   | event_family   |   feat_connection_strength |   hardcap_return_pct |   hardcap_active_vs_benchmark_gross_pct |   stock_te1_net_return_pct | question                                                             |
|:------------|:-------------|:---------|:---------------|---------------------------:|---------------------:|----------------------------------------:|---------------------------:|:---------------------------------------------------------------------|
| SPY         | 2026-04-30   | CRCL     | earnings       |                       1.00 |                32.00 |                                   31.29 |                      24.95 | Will Circle Internet (CRCL) beat quarterly earnings?                 |
| QQQ         | 2026-05-01   | CRCL     | earnings       |                       1.00 |                20.00 |                                   18.89 |                      13.89 | Will Circle Internet (CRCL) beat quarterly earnings?                 |
| SPY         | 2026-03-25   | USO      | geo            |                       0.68 |                14.00 |                                   14.99 |                      22.99 | Will Iran strike UAE by April 30, 2026?                              |
| QQQ         | 2026-03-24   | USO      | geo            |                       0.56 |                12.93 |                                   14.09 |                      21.76 | Will Iran strike Israel by April 30, 2026?                           |
| SPY         | 2026-04-27   | NET      | earnings       |                       1.00 |                14.16 |                                   11.56 |                      16.94 | Will Cloudflare (NET) beat quarterly earnings?                       |
| SPY         | 2026-01-29   | MGM      | earnings       |                       1.00 |                 7.92 |                                   10.28 |                      11.93 | Will MGM Resorts (MGM) beat quarterly earnings?                      |
| QQQ         | 2026-04-27   | NET      | earnings       |                       1.00 |                14.16 |                                    9.42 |                      16.94 | Will Cloudflare (NET) beat quarterly earnings?                       |
| QQQ         | 2026-03-26   | USO      | geo            |                       0.72 |                10.00 |                                    9.41 |                      18.93 | Will Iran strike Saudi Arabia by April 30, 2026?                     |
| SPY         | 2026-03-26   | USO      | geo            |                       0.52 |                10.00 |                                    9.19 |                      18.93 | Will Iran conduct a military action against Israel on April 2, 2026? |
| SPY         | 2026-04-20   | QCOM     | earnings       |                       0.95 |                10.00 |                                    9.09 |                       8.96 | Will Qualcomm (QCOM) beat quarterly earnings?                        |
| QQQ         | 2026-05-01   | HNGE     | earnings       |                       1.00 |                 7.62 |                                    7.80 |                       7.49 | Will Hinge Health (HNGE) beat quarterly earnings?                    |
| QQQ         | 2026-04-20   | QCOM     | earnings       |                       0.95 |                10.00 |                                    7.30 |                       8.96 | Will Qualcomm (QCOM) beat quarterly earnings?                        |

יש הרבה winners שהוחמצו בשתי התקופות. אך עצם ספירתם אינה מספיקה: בכל יום צפופים יהיו תמיד winners ו־losers בדיעבד. השאלה הנכונה היא האם מאפיין שהיה ידוע לפני הכניסה מאפשר להעדיף אותם באופן יציב.

## האם הבחירה הנוכחית אקראית?

לא. ברוב התאים, המועמדים שנבחרו מציגים תוצאה ממוצעת טובה מהמועמדים שלא נבחרו. לדוגמה:

- SPY earnings train: selected hard-cap **+0.79%**, missed **−0.15%**.
- SPY earnings test: selected **+0.58%**, missed **−0.28%**.
- SPY geo test: selected **+3.97%**, missed **+1.66%**.
- QQQ earnings test: selected **+0.54%**, missed **+0.25%**.

לכן אין הצדקה למחוק את מנגנון הבחירה ולהחליפו ב־“trade everything”. הוא כבר מסנן חלק מהזבל. הבעיה היא שהדירוג בתוך הימים הצפופים עדיין רחוק מאופטימלי.

---

# 4. עד כמה סדר המועמדים באותו יום חשוב?

בכל יום שבו היו גם selected וגם missed, חישבתי החלפה אורקלית אחת: ה־selected הגרוע ביותר מול ה־missed הטוב ביותר, לפי active hard-cap return.

| benchmark   | split   |   choice_days |   days_positive_swap |   mean_oracle_swap_pct |   median_oracle_swap_pct |   p75_oracle_swap_pct |
|:------------|:--------|--------------:|---------------------:|-----------------------:|-------------------------:|----------------------:|
| QQQ         | test    |            36 |                   31 |                  5.534 |                    4.950 |                 9.677 |
| QQQ         | train   |            17 |                   16 |                  4.224 |                    2.554 |                 5.486 |
| SPY         | test    |            37 |                   30 |                  6.953 |                    6.506 |                10.470 |
| SPY         | train   |            19 |                   15 |                  6.176 |                    6.192 |                 9.056 |

זה **אינו כלל מסחר**, מפני שהוא משתמש בתוצאה העתידית. הוא רק מוכיח שקיבולת וסדר תוך־יומי הם מנגנון כלכלי מרכזי:

- ב־SPY test הייתה החלפה משפרת ב־30 מתוך 37 ימי בחירה.
- ב־QQQ test ב־31 מתוך 36 ימים.
- השיפור האורקלי הממוצע ל־swap אחד הוא גדול, ולכן גם דירוג לא מושלם יכול להוסיף ערך.

## דירוגים שנבדקו בפורטפוליו המלא

| benchmark   | ranker              | split   |   excess_return |   overall_ir |   active_max_dd_pct |   n_trades |
|:------------|:--------------------|:--------|----------------:|-------------:|--------------------:|-----------:|
| QQQ         | connection          | test    |          -3.110 |       -0.459 |             -10.818 |        181 |
| QQQ         | connection          | train   |           1.330 |        0.122 |              -6.339 |        131 |
| QQQ         | current             | test    |          -6.398 |       -0.958 |             -13.540 |        179 |
| QQQ         | current             | train   |           4.510 |        0.415 |              -5.222 |        132 |
| QQQ         | family_sector_train | test    |          -9.438 |       -1.390 |             -14.595 |        181 |
| QQQ         | family_sector_train | train   |           6.124 |        0.479 |              -5.796 |        131 |
| QQQ         | geo_first           | test    |          -9.095 |       -1.360 |             -15.634 |        182 |
| QQQ         | geo_first           | train   |           4.510 |        0.415 |              -5.222 |        132 |
| SPY         | connection          | test    |           5.757 |        0.869 |              -4.471 |        213 |
| SPY         | connection          | train   |           4.928 |        0.490 |              -4.779 |        138 |
| SPY         | current             | test    |           2.141 |        0.331 |              -6.362 |        216 |
| SPY         | current             | train   |           3.814 |        0.370 |              -4.779 |        137 |
| SPY         | family_sector_train | test    |           1.490 |        0.223 |              -6.329 |        210 |
| SPY         | family_sector_train | train   |           6.942 |        0.664 |              -4.779 |        134 |
| SPY         | geo_first           | test    |           0.945 |        0.149 |              -6.132 |        215 |
| SPY         | geo_first           | train   |           3.814 |        0.370 |              -4.779 |        137 |

הממצא החשוב:

- **Connection strength יורד** הוא הדירוג הפשוט היחיד שנתן שיפור משמעותי מחוץ למדגם ב־SPY:
  - train excess: מ־**+3.81%** ל־**+4.93%**.
  - test excess: מ־**+2.14%** ל־**+5.76%**.
  - active drawdown השתפר מ־**−6.36%** ל־**−4.47%** ב־test.
- `geo_first` פגע בשני המדדים.
- דירוג לפי family/sector שנלמד ב־train נראה חזק ב־train אך נכשל ב־test — דוגמה קלאסית ל־overfitting.
- ב־QQQ connection משפר את test, אך פוגע משמעותית ב־train. לכן הוא אינו כלל QQQ מוכח.

## למה connection עובד?

| benchmark   | split   | connection_bin   |   n |   te1_mean_pct |   hardcap_mean_pct |   active_hardcap_mean_pct |   selected_rate |
|:------------|:--------|:-----------------|----:|---------------:|-------------------:|--------------------------:|----------------:|
| QQQ         | test    | 0.90-<1.00       | 112 |         -0.289 |             -0.085 |                    -1.142 |          38.393 |
| QQQ         | test    | 1.00             | 238 |          1.369 |              0.624 |                    -0.055 |          50.840 |
| QQQ         | train   | 0.90-<1.00       |  82 |         -1.626 |             -0.728 |                     0.360 |          46.341 |
| QQQ         | train   | 1.00             | 139 |          0.504 |              0.220 |                     0.047 |          46.043 |
| SPY         | test    | 0.90-<1.00       | 138 |         -0.284 |             -0.132 |                    -0.679 |          40.580 |
| SPY         | test    | 1.00             | 308 |          0.782 |              0.238 |                    -0.064 |          48.377 |
| SPY         | train   | 0.90-<1.00       |  93 |         -2.061 |             -0.792 |                    -0.200 |          43.011 |
| SPY         | train   | 1.00             | 178 |          0.691 |              0.739 |                     0.438 |          35.955 |

ב־earnings קיימת הפרדה יציבה:

- connection=1 חיובי יותר גם ב־train וגם ב־test.
- connection נמוך מ־1 שלילי או חלש ברוב המדדים.
- בריצת הפורטפוליו, connection ranking הוסיף ב־SPY test 15 טריידי earnings עם ממוצע **+1.93%** ושיעור הצלחה **86.7%**, והוציא 20 earnings עם ממוצע קרוב לאפס.
- ב־QQQ test נוספו 20 earnings עם ממוצע **+0.64%**, והוצאו 18 עם ממוצע **−0.73%**.

עם זאת, connection צריך להיות **דירוג**, לא gate קשיח. בדקתי סינון שמבטל כל earnings עם connection<1:

| benchmark   | variant    | split   |   excess_return |   overall_ir |   active_max_dd_pct |   n_trades |
|:------------|:-----------|:--------|----------------:|-------------:|--------------------:|-----------:|
| SPY         | connection | train   |           4.928 |        0.490 |              -4.779 |        138 |
| SPY         | connection | test    |           5.757 |        0.869 |              -4.471 |        213 |
| SPY         | conn1_earn | train   |           5.924 |        0.605 |              -4.779 |        121 |
| SPY         | conn1_earn | test    |           1.792 |        0.309 |              -5.275 |        187 |
| SPY         | no_xle     | train   |           5.704 |        0.591 |              -4.444 |        133 |
| SPY         | no_xle     | test    |           5.711 |        0.868 |              -4.051 |        212 |
| QQQ         | connection | train   |           1.330 |        0.122 |              -6.339 |        131 |
| QQQ         | connection | test    |          -3.110 |       -0.459 |             -10.818 |        181 |
| QQQ         | conn1_earn | train   |           0.010 |        0.001 |              -6.427 |        113 |
| QQQ         | conn1_earn | test    |          -6.542 |       -1.103 |             -11.870 |        161 |
| QQQ         | no_xle     | train   |           1.683 |        0.160 |              -5.771 |        127 |
| QQQ         | no_xle     | test    |          -2.686 |       -0.403 |             -10.480 |        179 |

ה־hard filter שיפר את SPY train אך הוריד את SPY test מ־**+5.76%** ל־**+1.79%**, והחמיר את QQQ. הסיבה היא שמועמד חלש יחסית עדיין עשוי להיות שימושי כאשר אין מועמד טוב יותר, וגם שינוי מספר הפוזיציות משנה את מחזור ההון.

**מסקנת הסדר:** באותו תאריך כניסה יש לאסוף את כל המועמדים, לאחד כפילויות symbol-day, ולדרג בראש ובראשונה לפי connection strength. אין לבטל אוטומטית כל מועמד שאינו 1.

---

# 5. כיצד ראוי להתייחס ל־earnings?

## קשר למדדים ולסקטור

| split   | reference   |   n |   correlation |   beta |     r2 |
|:--------|:------------|----:|--------------:|-------:|-------:|
| test    | SPY         | 411 |         0.387 |  1.714 | 15.000 |
| test    | QQQ         | 411 |         0.388 |  1.093 | 15.034 |
| test    | sector      | 398 |         0.402 |  1.095 | 16.152 |
| train   | SPY         | 281 |         0.331 |  1.147 | 10.950 |
| train   | QQQ         | 281 |         0.273 |  0.660 |  7.463 |
| train   | sector      | 279 |         0.496 |  1.123 | 24.620 |

הסקטור הוא המשתנה המסביר החזק ביותר:

- ב־train הוא מסביר כ־**24.6%** מהשונות בתשואת earnings.
- ב־test כ־**16.2%**.
- SPY/QQQ מסבירים בערך 7%–15%.

| split   | reference   | reference_state   |   n |   stock_mean_net_pct |   stock_win_rate |
|:--------|:------------|:------------------|----:|---------------------:|-----------------:|
| test    | SPY         | up                | 254 |                2.029 |           58.268 |
| test    | SPY         | flat_or_down      | 157 |               -2.374 |           38.217 |
| test    | sector      | up                | 194 |                2.596 |           66.495 |
| test    | sector      | flat_or_down      | 204 |               -1.830 |           34.804 |
| train   | SPY         | up                | 134 |                0.640 |           58.955 |
| train   | SPY         | flat_or_down      | 147 |               -1.461 |           37.415 |
| train   | sector      | up                | 121 |                1.650 |           69.421 |
| train   | sector      | flat_or_down      | 158 |               -2.086 |           31.013 |

כאשר הסקטור עצמו עלה בתקופת ההחזקה:

- earnings הניבו בממוצע **+1.65%** ב־train ו־**+2.60%** ב־test.
- כאשר הסקטור היה שטוח או ירד: **−2.09%** ו־**−1.83%**.

זה מוכיח ש־earnings כאן הם במידה רבה **חשיפת בטא לסקטור ולשוק**, עם שארית alpha קטנה. אבל תשואת הסקטור במהלך ההחזקה אינה ידועה בכניסה. ניסיון להשתמש ב־sector 1-month trend שהיה ידוע מראש לא היה יציב: הסימן התהפך בין train ל־test, ודירוג family-sector נכשל מחוץ למדגם.

## סקטורים

- Technology חיובי בשתי התקופות: כ־+0.53% ב־train ו־+3.46% ב־test.
- Consumer Defensive חיובי בשתיהן.
- Healthcare ו־Communication Services חלשים יחסית בשתיהן.
- Financial Services ו־Consumer Cyclical אינם יציבים.

אין מספיק יציבות כדי להפוך את הסקטור למסנן בינארי. השימוש הראוי כרגע הוא:

1. מגבלת ריכוז סקטוריאלית;
2. מדידת alpha מול sector ETF;
3. tie-breaker חלש לאחר connection — לא ציון ראשי.

## האם להחזיק את כל earnings עד \(T_e-1\)?

לא באופן גלובלי.

- ב־SPY, forcing של earnings ל־\(T_e-1\) שיפר train אך הפך את test משלילי קטן/חיובי לתוצאה גרועה: תחת connection ranking ה־test excess ירד מ־**+5.76%** ל־**−2.94%**.
- ב־QQQ, connection + earnings \(T_e-1\) נתן:
  - train excess **+1.34%**;
  - test excess **+3.56%**.

זוהי תוצאה מעניינת ל־QQQ, אך השיפור ב־test מרוכז בעיקר במרץ ובמאי, והוא פוגע בינואר ובפברואר. bootstrap בלוקים להשיפור מול connection בלבד כלל אפס. לכן מדובר ב־**research candidate**, לא כלל שהוכח.

## מסקנת earnings

- להשאיר earnings ביקום.
- לדרג בעיקר לפי connection.
- לא להשתמש ב־geo-first ולא בסקטור כמנבא ראשי.
- לא לכפות יציאת \(T_e-1\) על כל earnings ב־SPY.
- לחקור QQQ earnings-\(T_e-1\) בנפרד על תקופה חדשה.
- למדוד כל טרייד גם מול sector ETF ולא רק נומינלית.

---

# 6. מה למדנו מהטריידים הגיאופוליטיים?

## לפי נכס

| split   | symbol   |   n |   mean_te1_net_pct |   median_te1_net_pct |   win_rate |
|:--------|:---------|----:|-------------------:|---------------------:|-----------:|
| test    | BNO      |   6 |             11.265 |               10.879 |      1.000 |
| train   | BNO      |   5 |              1.980 |                4.473 |      0.800 |
| test    | USO      |  71 |             12.775 |               10.510 |      0.859 |
| train   | USO      |  33 |             -2.449 |                0.271 |      0.545 |
| test    | XLE      |  33 |             -0.943 |               -1.377 |      0.303 |
| train   | XLE      |  17 |             -1.967 |               -3.071 |      0.353 |

- **USO:** חלש ב־train וחזק מאוד ב־test. זהו regime dependence ברור.
- **XLE:** שלילי בשתי התקופות. הוא אינו תחליף טוב אוטומטי ל־USO/BNO עבור shock גיאופוליטי.
- **BNO:** חיובי בשתי התקופות, אך עם 5 ו־6 תצפיות בלבד — מבטיח, לא מוכח.

התוצאה הגולמית של geo מנופחת גם על ידי מספר שאלות שמייצרות אותו USO/BNO באותו יום. לאחר collapse, train geo נשאר שלילי ו־test חיובי מאוד.

## latency בין \(T_\theta\) לכניסה בפועל

| benchmark   | split   | latency_bucket   |   n |   te1_mean_pct |   hardcap_mean_pct |   active_hardcap_mean_pct |
|:------------|:--------|:-----------------|----:|---------------:|-------------------:|--------------------------:|
| QQQ         | test    | 0-1              |  32 |          8.466 |              0.406 |                     0.764 |
| QQQ         | test    | 2-3              |  36 |          9.011 |              0.636 |                     0.298 |
| QQQ         | test    | 4+               |  15 |          3.570 |             -3.426 |                    -6.004 |
| QQQ         | train   | 0-1              |  40 |         -3.416 |             -1.044 |                    -2.038 |
| QQQ         | train   | 2-3              |  17 |          1.853 |              2.051 |                     2.152 |
| QQQ         | train   | 4+               |   6 |         -2.271 |             -2.314 |                    -5.537 |
| SPY         | test    | 0-1              |  51 |          7.248 |              0.443 |                     0.908 |
| SPY         | test    | 2-3              |  33 |         10.682 |              1.695 |                     2.011 |
| SPY         | test    | 4+               |   4 |         -5.815 |             -7.630 |                   -13.449 |
| SPY         | train   | 0-1              |  46 |         -3.391 |             -1.185 |                    -2.257 |
| SPY         | train   | 2-3              |  11 |          2.973 |              3.308 |                     3.776 |
| SPY         | train   | 4+               |   3 |         -2.762 |             -3.648 |                    -5.439 |

זהו המשתנה הגיאופוליטי היציב ביותר שנמצא:

- latency של **2–3 ימים** חיובי ב־train וב־test.
- latency של **4+ ימים** שלילי באופן חד בשתי התקופות.
- latency של 0–1 ימים חזק ב־test אך שלילי ב־train, ולכן אינו כלל יציב.

## יציאה משפחתית ל־geo

הגרסה הטובה ביותר שנמצאה ל־SPY היא:

1. דירוג same-day לפי connection;
2. hard-cap/profit-lock הנוכחי ל־earnings ול־other;
3. יציאת \(T_e-1\) רק ל־geo שהכניסה בפועל שלו התרחשה 2–3 ימים לאחר \(T_\theta\).

| benchmark   | ranker     | variant             | split   |   excess_return |   overall_ir |   active_max_dd_pct |   n_trades |
|:------------|:-----------|:--------------------|:--------|----------------:|-------------:|--------------------:|-----------:|
| SPY         | connection | current             | train   |           4.928 |        0.490 |              -4.779 |        138 |
| SPY         | connection | current             | test    |           5.757 |        0.869 |              -4.471 |        213 |
| SPY         | connection | geo_latency_2_3_te1 | train   |           4.881 |        0.485 |              -4.819 |        138 |
| SPY         | connection | geo_latency_2_3_te1 | test    |           8.373 |        1.203 |              -4.048 |        211 |
| QQQ         | connection | current             | train   |           1.330 |        0.122 |              -6.339 |        131 |
| QQQ         | connection | current             | test    |          -3.110 |       -0.459 |             -10.818 |        181 |
| QQQ         | connection | earnings_te1        | train   |           1.336 |        0.120 |              -7.727 |        109 |
| QQQ         | connection | earnings_te1        | test    |           3.560 |        0.442 |              -8.692 |        134 |

ב־SPY:

- train excess: **+4.88%**.
- test excess: **+8.37%**.
- test Information Ratio: **1.20**.
- active drawdown: **−4.05%**.

לעומת הסדר הנוכחי והיציאות הנוכחיות, השיפור היחסי ב־test היה **+5.63%**. bootstrap בלוקים של חמישה ימים נתן 95% CI של **+0.56% עד +12.32%**, עם 98.6% מהדגימות מעל אפס. ב־train השיפור ביחס לגרסה הנוכחית היה קטן וה־CI כלל אפס.

זהו המפרט המחקרי הטוב ביותר כרגע, אך הוא התגלה תוך הסתכלות ב־test ולכן נדרש חלון עתידי חדש.

ב־QQQ אותו כלל לא נתן שיפור עקבי. אין להעביר כלל SPY ל־QQQ אוטומטית.

---

# 7. האם אפשר ללמוד מודל מורכב יותר לבחירת המניות?

נבדקו:

- Ridge ו־ExtraTrees לחיזוי absolute return ו־active return;
- מודל pairwise שמנסה לנבא מי מבין שני מועמדים באותו יום ינצח.

| benchmark   | split   |   n_pairs |   accuracy |    auc |
|:------------|:--------|----------:|-----------:|-------:|
| SPY         | train   |      1556 |     65.938 | 71.906 |
| SPY         | test    |      2102 |     49.572 | 49.708 |
| QQQ         | train   |      1057 |     63.671 | 69.397 |
| QQQ         | test    |      1480 |     45.068 | 43.810 |

המודלים לומדים את train, אך מאבדים לחלוטין את היכולת ב־test. גם מודלי ה־absolute-return קיבלו \(R^2\) שלילי וקורלציה כמעט אפס ב־test.

לכן אין כרגע הצדקה ל־Random Forest/ExtraTrees או ranker מורכב. המסקנות היציבות הן פשוטות ושקופות יותר:

- connection strength;
- latency גיאופוליטי;
- family-specific exit;
- מגבלות ריכוז.

---

# 8. המפרט שאני ממליץ לבחון בריצה הבאה

## SPY

### בחירה באותו יום

1. לקבץ לפי actual entry date.
2. לאחד כל symbol-day לחשיפה אחת; שאלות נוספות נשמרות כ־supporting signals ולא כטריידים נוספים.
3. לדרג לפי:
   - connection strength יורד;
   - לאחר מכן entry probability;
   - לאחר מכן run-up נמוך יותר או הסדר הקיים כ־tie-breaker.
4. לא לתת עדיפות גלובלית ל־geo.
5. לא לבצע hard filter של connection<1.
6. להגביל מספר מניות מאותו סקטור באותו יום/בפורטפוליו.

### יציאה

- Earnings/other: מנגנון hard-cap + profit-lock המתוקן.
- Geo latency 2–3 ימים: \(T_e-1\).
- Geo latency 4+ ימים: כרגע עדיף לדחות או להשאיר תחת יציאה הגנתית, לא להחזיק אוטומטית עד הסוף.
- XLE: לאסור כ־proxy אוטומטי; לבחון אותו כזרוע נפרדת מול USO/BNO.

## QQQ

אין כרגע מפרט כללי שעומד באותה רמת יציבות.

המועמד היחיד שמצדיק ניסוי נוסף הוא:

- connection same-day ranking;
- earnings מוחזקים ל־\(T_e-1\);
- validation על תקופה חדשה בלבד.

אין להשתמש בתוצאת +3.56% ב־test כהוכחה סופית, משום שהכלל התגלה לאחר שימוש חוזר ב־test והשיפור חודשי מרוכז.

---

# 9. מסקנה ישירה

1. **יש תוחלת ברמת היקום**, אך היא קטנה בהרבה לאחר הסרת כפילויות ואינה יציבה בין משפחות ומשטרים.
2. **הפורטפוליו כבר בוחר טוב יותר מהמועמד הממוצע**; אין סיבה לעבור ל־trade everything.
3. **הבעיה המרכזית היא ranking תחת capacity**, לא רק תנאי הכניסה או הסטופ.
4. **Connection strength הוא האות היציב ביותר ל־earnings**, אך יש להשתמש בו לדירוג ולא לסינון קשיח.
5. **Earnings קשורים מהותית לסקטור ולמדד**. יש למדוד active return ולנהל concentration; אין להשתמש בתשואת הסקטור העתידית כאילו היא signal.
6. **Geo אינו קבוצה אחידה**:
   - USO תלוי משטר;
   - XLE חלש;
   - BNO מבטיח אך קטן;
   - latency 2–3 ימים הוא התנאי היציב ביותר.
7. המפרט הטוב ביותר ל־SPY כרגע הוא **connection ranking + \(T_e-1\) רק ל־geo latency 2–3**, עם hard-cap ליתר המשפחות.
8. אין כרגע ranker ML מורכב שמכליל מחוץ למדגם.
9. ה־test הנוכחי שימש למחקר השוואתי ולכן אינו עוד holdout נקי. ההחלטה הבאה חייבת להיבחן על נתונים חדשים שלא שימשו לבחירת הכללים.

---

# קבצי ביקורת מרכזיים

- `symbol_day_current_priority.csv` — יקום מועמדים מאוחד לפי מניה ויום.
- `symbol_day_selected_vs_missed_summary.csv` — selected לעומת missed.
- `top_missed_winners_hardcap_active.csv` — winners שהוחמצו.
- `top_selected_losers_active.csv` — הטריידים הנבחרים הגרועים.
- `same_day_one_swap_opportunities.csv` — תחרות same-day והחלפה אורקלית.
- `earnings_market_sector_regression.csv` — קשר ל־SPY, QQQ והסקטור.
- `earnings_connection_strength_bins.csv` — הפרדת connection.
- `geo_results_by_entry_latency.csv` — latency גיאופוליטי.
- `exact_same_day_ranker_results.csv` — בדיקות ranking בפורטפוליו המלא.
- `event_family_exit_results.csv` — בדיקות יציאה לפי משפחה.
- `focused_selection_rule_results.csv` — hard filters ו־XLE.
- `pairwise_rank_model_metrics.csv` — כישלון מודל pairwise מחוץ למדגם.
