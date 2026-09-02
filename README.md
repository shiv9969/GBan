# Koyeb-ready Telegram Ban-All Bot

## Koyeb

Start command can be left empty because `Procfile` contains:

`web: python bot.py`

The app listens on `0.0.0.0:8000` for Koyeb health checks while the Telegram bot uses polling.

If configuring Koyeb manually, use:
- Port: `8000`
- Protocol: HTTP/TCP as appropriate for your service
- Health path: `/health`

## Setup

Edit `info.py`:
- `BOT_TOKEN`
- `OWNER_ID`
- optional `ADMINS`

Then deploy. `bot.db` is created automatically.

## Security

Do NOT publish `info.py` containing your real bot token in a public GitHub repository.

If a bot token has been exposed publicly, revoke it in BotFather and put the newly generated token in `info.py`.
