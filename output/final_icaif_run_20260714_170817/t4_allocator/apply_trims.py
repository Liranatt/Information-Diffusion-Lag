from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / "paper_final_t4_test.tex"
s = p.read_text(encoding="utf-8")

subs = [
(r"Across the four CEM budgets of the pre-existing 40-run grid, the all-treatment arm's median excess over ten seeds is positive throughout (Table~\ref{tab:budget}): $+6.69$ to $+8.26$ points on SPY (10/10 seeds positive per budget) and $+3.89$ to $+6.44$ on QQQ (8--9/10), with heavily overlapping interquartile ranges; the budgets are robustness diagnostics and none is selected by test performance.",
 r"Across the four CEM budgets of the pre-existing 40-run grid, the all-treatment arm's median excess over ten seeds is positive throughout: on SPY $+8.26$ ($6{\times}20$), $+6.69$ ($6{\times}30$), $+7.76$ ($10{\times}20$), and $+6.97$ ($10{\times}30$) points with 10/10 seeds positive in every budget; on QQQ $+3.89$, $+5.65$, $+4.65$, and $+6.44$ with 8--9/10 positive. Interquartile ranges overlap heavily; the budgets are robustness diagnostics and none is selected by test performance."),
("\\input{table_budget.tex}\n\n", ""),
(r"Two documented caveats: random draws execute slightly fewer trades after capacity skips (an asymmetry flattering the observed arm), and even arbitrary selections come from the cleaned, screened pool.",
 r"Two documented caveats: random draws execute slightly fewer trades after capacity skips (an asymmetry flattering the observed arm), and even arbitrary selections come from the cleaned, screened pool."),
]
# The caveat sentence may still be in its original long form; handle both.
long_caveat = ("Two caveats are documented: random draws execute fewer trades after capacity skips "
               "(median 159 versus 213 on SPY), an asymmetry that flatters the observed arm in a "
               "positive-mean stream; and even arbitrary selections are drawn from the cleaned, screened candidate pool.")
short_caveat = ("Two documented caveats: random draws execute slightly fewer trades after capacity skips "
                "(an asymmetry flattering the observed arm), and even arbitrary selections come from the "
                "cleaned, screened pool.")
if long_caveat in s:
    s = s.replace(long_caveat, short_caveat)

more = [
("The treatments' value in this sample is operational realism --- label-complete chronology, bounded sizing, explainable allocation --- not terminal return. That is a valid result, and we report it as such.",
 "The treatments' value in this sample is operational realism --- label-complete chronology, bounded sizing, explainable allocation --- not terminal return."),
(r"The allocator has real scope: a median of ${\sim}43$ contested dates and ${\sim}90$ positions per run whose identity differs between T4 and FIFO (roughly half of all trades), driven by priority ranking and same-symbol collapse --- the preemption rule never fired in any of the 20 runs.",
 r"The allocator has real scope: a median of ${\sim}43$ contested dates and ${\sim}90$ positions per run whose identity differs between T4 and FIFO, via priority ranking and same-symbol collapse; the preemption rule never fired in any of the 20 runs."),
]

for old, new in subs[:2] + more:
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("trims applied")
