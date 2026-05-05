# Daily Telegram Macro Regime Bot

This repo runs a daily macro regime classifier with market data from `yfinance`.
GitHub Actions runs the schedule, `regime_classifier.py` fetches SPY, GLD, VDE,
and `^TNX`, and Telegram receives only the concise summary block.

## Telegram Setup

1. In Telegram, open a chat with `@BotFather`.
2. Send `/newbot` and follow the prompts to create a bot.
3. Copy the bot token from BotFather. It looks like `123456:ABC...`.
4. Start a chat with your new bot and send it any message, such as `hi`.
5. In a browser, open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`.
6. Find the `chat.id` value in the JSON response. That is your Telegram chat ID.
7. If you want to send messages to a group, add the bot to the group, send a message in the group, then call `getUpdates` and use that group `chat.id`.

## GitHub Secrets

In GitHub, open the repo and go to `Settings` -> `Secrets and variables` -> `Actions`.
Select `New repository secret` and add:

- `TELEGRAM_BOT_TOKEN`: the token from BotFather
- `TELEGRAM_CHAT_ID`: your Telegram user or group chat ID

The workflow reads these values from GitHub Actions secrets. The bot token and
chat ID are not hardcoded in the repo.

When adding the secrets, paste only the raw value. Do not include quotes, spaces,
or extra line breaks. A token with a trailing newline can make Telegram requests
fail before they are sent.

## Manual Workflow Run

1. In GitHub, open the `Actions` tab.
2. Select `Daily Regime Telegram`.
3. Choose `Run workflow`.
4. Run it from the default branch.

## Daily Schedule

The workflow runs Monday through Friday at `22:00 UTC`, safely after the regular
U.S. equity market close. That is 2:00 PM Los Angeles time during standard time
and 3:00 PM Los Angeles time during daylight time. It can also be triggered
manually with `workflow_dispatch`.

## Data Sources

All market and macro inputs come from `yfinance`:

- `SPY`
- `GLD`
- `VDE`
- `^TNX` for the 10-year rate

There are no FRED, `pandas_datareader`, or local CSV dependencies.
