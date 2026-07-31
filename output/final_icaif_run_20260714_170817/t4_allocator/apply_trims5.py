from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / "paper_final_t4_test.tex"
s = p.read_text(encoding="utf-8")

pairs = [
(r"[width=0.88\textwidth]{fig_cem_contribution.pdf}",
 r"[width=0.80\textwidth]{fig_cem_contribution.pdf}"),
("The random-selection test bundles gate timing with selection and does not equalize executed trade counts. The fixed reference policy is pre-specified but informally informed by earlier fitted policies, and the exploratory RL evidence is unverifiable because its artifacts were not retained.",
 "The random-selection test bundles gate timing with selection and does not equalize executed trade counts; the fixed reference is informally informed by earlier fitted policies; the RL evidence is unverifiable because its artifacts were not retained; and the allocator experiment evaluates the FIFO and random arms slightly off-policy (the schedules were fitted with T4 in the loop)."),
("--- and even the fixed reference rule set converted the screened candidate stream into portfolios that historically outperformed both buy-and-hold benchmarks during the test period.",
 "--- and even the fixed reference rule set historically outperformed both buy-and-hold benchmarks."),
]
for old, new in pairs:
    assert old in s, "MISSING: " + old[:60]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(pairs))
