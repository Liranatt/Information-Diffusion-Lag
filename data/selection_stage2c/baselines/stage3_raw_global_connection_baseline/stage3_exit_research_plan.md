# Stage 3 Exit Research — Development Folds Only

The Stage 2B research-frozen selector is:

- connection strength descending;
- `expected_slot_days` tie-breaker;
- minimum connection strength threshold `1.00`.

This selector is immutable during exit research. The sample contains only
Stage 2B outer development-fold replay trades. The later lockbox was not
opened and is reserved for one final evaluation of the complete frozen
modular pipeline.

The first exit-research diagnostic compares the frozen reference exit path
with the legal terminal `T_e - 1` horizon. Where a terminal return label is
available it is joined; the terminal date itself is derived from price
timestamps strictly before `T_e`. A future
exit model may learn earlier exits, safety exits, and terminal holding, but
every action must satisfy `exit_date < T_e`; `T_e` itself is never an exit.

No selector, ranking rule, tie-breaker, or admission threshold may be changed
after this point.
