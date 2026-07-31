from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / "paper_final_t4_test.tex"
s = p.read_text(encoding="utf-8")

subs = [
("Its values were fixed in code before these experiments, but they were informally chosen by the authors with knowledge of earlier fitted policies, and they resemble typical CEM optima. The reference is therefore fixed but not hindsight-free: it is a \\emph{conservative} comparator that biases the study against finding a CEM improvement, while its own absolute performance overstates what an uninformed operator would have achieved.",
 "Its values were fixed in code before these experiments but were informally chosen with knowledge of earlier fitted policies (they resemble typical CEM optima). The reference is therefore fixed but not hindsight-free: a \\emph{conservative} comparator biased against finding a CEM improvement, whose own absolute performance overstates an uninformed operator."),
("Exploratory PPO reinforcement-learning experiments conducted during development did not learn a stable replacement for the low-dimensional CEM policy: across the runs attempted, the agent failed to produce consistently reliable entry, exit, stop-loss, sizing, and portfolio decisions. The limited number of high-quality, label-complete event candidates (roughly five hundred for training) appears insufficient for estimating a high-capacity sequential policy, although those experiments cannot prove that sample size is the sole cause.",
 "Exploratory PPO reinforcement-learning experiments conducted during development did not learn a stable replacement for the low-dimensional CEM policy: the agent failed to produce consistently reliable entry, exit, stop-loss, sizing, and portfolio decisions. The roughly five hundred label-complete training candidates appear insufficient for estimating a high-capacity sequential policy, although those experiments cannot prove that sample size is the sole cause."),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs))
