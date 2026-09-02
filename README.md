# Telegram Ban-All Bot

## Files

- `bot.py` — main bot
- `info.py` — BOT_TOKEN, OWNER_ID and optional initial admins
- `requirements.txt` — dependency
- `bot.db` — created automatically

## Setup

1. Open `info.py`.
2. Put your BotFather token in `BOT_TOKEN`.
3. Put your numeric Telegram ID in `OWNER_ID`.
4. Optionally add initial admin IDs to `ADMINS`.
5. Install:
   `pip install -r requirements.txt`
6. Run:
   `python bot.py`

## Features

- Owner button panel.
- Add/remove admins from buttons.
- Automatic chat registration when the bot becomes an administrator.
- Connected chat list.
- `/banall USER_ID`
- Reply to a user's message with `/banall`.
- `/unbanall USER_ID`
- Per-chat permission checking.
- Per-chat ban verification.
- Owner DM report.

## Telegram limitation

The bot can only ban users in chats where it is present and has the required administrator permission. It cannot globally ban someone from arbitrary Telegram chats it does not control.
