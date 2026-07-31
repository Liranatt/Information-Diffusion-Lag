# CEM matrix verification (equity-log recomputation)

Rows: 10; mismatches beyond tolerance (0.02): 0

    experiment benchmark  test_return_pct  recomputed_return_pct  test_sharpe  recomputed_sharpe  test_max_dd_pct  recomputed_max_dd_pct  return_match  sharpe_match  dd_match
      Baseline       SPY          30.2742               30.27424       3.2975           3.297473          -6.6651              -6.665065          True          True      True
      Baseline       QQQ          27.3641               27.36411       2.9321           2.932055          -6.3585              -6.358454          True          True      True
         T1+T2       SPY          30.8712               30.87124       3.4107           3.410672          -9.5242              -9.524171          True          True      True
         T1+T2       QQQ          24.6260               24.62597       2.5162           2.516193          -7.5066              -7.506578          True          True      True
      T1+T2+T3       SPY          25.5048               25.50480       2.9878           2.987830          -7.7556              -7.755563          True          True      True
      T1+T2+T3       QQQ          22.5750               22.57502       2.1001           2.100118         -11.4338             -11.433836          True          True      True
T4 GeoPriority       SPY          27.2668               27.26684       2.8862           2.886212          -8.3279              -8.327923          True          True      True
T4 GeoPriority       QQQ          23.5796               23.57957       2.5431           2.543147          -6.7405              -6.740521          True          True      True
   T1+T2+T3+T4       SPY          28.4881               28.48806       3.2171           3.217128          -8.7223              -8.722264          True          True      True
   T1+T2+T3+T4       QQQ          24.6097               24.60967       2.1990           2.198999         -10.3829             -10.382916          True          True      True
