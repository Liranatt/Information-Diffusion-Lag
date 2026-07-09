"""Live paper-trading layer: IB execution + Polymarket tracking + control loop.

Modules:
  config            -- environment-driven settings
  connection        -- async IB (TWS/Gateway) connection
  database          -- live state tables + shared historical tables (asyncpg)
  data_fetcher      -- hourly/daily IB bars for tracked symbols only
  policy            -- latest walk-forward fold policy + half-Kelly sizing
  strategy_engine   -- backtest-kernel-parity entry/exit rules
  order_manager     -- benchmark-rotation orders + fully-invested cash sweep
  position_manager  -- IB <-> DB reconciliation
  control_pipeline  -- the 24/7 hourly orchestrator
  run_live          -- CLI entry point (python -m interactive_brokers.run_live)
"""
