# Daily Discord Macro Regime Bot

This repo runs a daily macro regime classifier with market data from `yfinance`.
GitHub Actions runs the schedule, `regime_classifier.py` fetches SPY, GLD, VDE,
and `^TNX`, and Discord receives only the concise summary block.

## Discord Webhook Secret

1. Create a Discord webhook for the target channel.
2. In GitHub, open the repo and go to `Settings` -> `Secrets and variables` -> `Actions`.
3. Select `New repository secret`.
4. Set the secret name to `DISCORD_WEBHOOK`.
5. Paste the Discord webhook URL as the secret value and save it.

The workflow reads this value from GitHub Actions secrets. The webhook URL is
not hardcoded in the repo and is not printed in the Actions logs.

## Manual Workflow Run

1. In GitHub, open the `Actions` tab.
2. Select `Daily Regime Discord`.
3. Choose `Run workflow`.
4. Run it from the default branch.

## Daily Schedule

The workflow runs on weekdays at `14:00 UTC`, which is early morning in Los
Angeles. It can also be triggered manually with `workflow_dispatch`.

## Data Sources

All market and macro inputs come from `yfinance`:

- `SPY`
- `GLD`
- `VDE`
- `^TNX` for the 10-year rate

There are no FRED, `pandas_datareader`, or local CSV dependencies.
