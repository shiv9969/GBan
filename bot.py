import sqlite3, logging
from contextlib import closing

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ChatMemberHandler, ContextTypes, filters
)
from telegram.error import TelegramError, Forbidden, BadRequest

from info import BOT_TOKEN, OWNER_ID, ADMINS

DB_PATH = "bot.db"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with closing(db()) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            chat_type TEXT NOT NULL,
            can_ban INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        for uid in ADMINS:
            con.execute("INSERT OR IGNORE INTO admins(user_id, added_by) VALUES (?, ?)",
                        (int(uid), OWNER_ID))
        con.commit()

def is_admin(uid):
    if uid == OWNER_ID:
        return True
    with closing(db()) as con:
        return con.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone() is not None

def add_admin(uid):
    with closing(db()) as con:
        con.execute("INSERT OR REPLACE INTO admins(user_id, added_by) VALUES (?, ?)",
                    (uid, OWNER_ID))
        con.commit()

def remove_admin(uid):
    if uid == OWNER_ID:
        return False
    with closing(db()) as con:
        cur = con.execute("DELETE FROM admins WHERE user_id=?", (uid,))
        con.commit()
        return cur.rowcount > 0

def admins():
    with closing(db()) as con:
        return con.execute("SELECT user_id FROM admins ORDER BY added_at").fetchall()

def chats():
    with closing(db()) as con:
        return con.execute("SELECT * FROM chats ORDER BY title COLLATE NOCASE").fetchall()

def save_chat(chat_id, title, chat_type, can_ban, active=1):
    with closing(db()) as con:
        con.execute("""
        INSERT INTO chats(chat_id,title,chat_type,can_ban,active,updated_at)
        VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
          title=excluded.title, chat_type=excluded.chat_type,
          can_ban=excluded.can_ban, active=excluded.active,
          updated_at=CURRENT_TIMESTAMP
        """, (chat_id, title, chat_type, int(can_ban), int(active)))
        con.commit()

async def owner_dm(context, text):
    try:
        await context.bot.send_message(OWNER_ID, text)
    except TelegramError:
        log.exception("Owner DM failed")

async def register_chat(chat, context):
    try:
        me = await context.bot.get_me()
        m = await context.bot.get_chat_member(chat.id, me.id)
        can_ban = bool(getattr(m, "can_restrict_members", False))
        if chat.type == "channel":
            can_ban = m.status == ChatMemberStatus.ADMINISTRATOR
        save_chat(chat.id, chat.title or str(chat.id), chat.type, can_ban)
        return can_ban
    except TelegramError as e:
        log.error("Chat registration failed: %s", e)
        return False

def panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Add Admin", callback_data="add"),
         InlineKeyboardButton("❌ Remove Admin", callback_data="remove")],
        [InlineKeyboardButton("👥 Admin List", callback_data="listadmins")],
        [InlineKeyboardButton("📡 Connected Chats", callback_data="chats"),
         InlineKeyboardButton("🔄 Refresh", callback_data="panel")]
    ])

async def start(update, context):
    uid = update.effective_user.id
    if uid == OWNER_ID:
        await update.message.reply_text("🛡️ Owner Control Panel", reply_markup=panel())
    elif is_admin(uid):
        await update.message.reply_text(
            "✅ Authorized admin.\n\nUse /banall <user_id> or reply to a user's message with /banall."
        )
    else:
        await update.message.reply_text("⛔ You are not authorized.")

async def panel_cmd(update, context):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    await update.message.reply_text("🛡️ Owner Control Panel", reply_markup=panel())

def target(update, context):
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        return update.message.reply_to_message.from_user.id
    return None

async def banall(update, context):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    uid = target(update, context)
    if not uid:
        return await update.message.reply_text(
            "Usage: /banall <user_id>\nOr reply to the user's message with /banall"
        )

    rows = chats()
    if not rows:
        return await update.message.reply_text("⚠️ No connected chats.")

    msg = await update.message.reply_text(f"🚫 Banning `{uid}` from {len(rows)} chats...",
                                          parse_mode="Markdown")
    ok, fail = [], []

    for r in rows:
        try:
            me = await context.bot.get_me()
            bot_member = await context.bot.get_chat_member(r["chat_id"], me.id)
            can_ban = bool(getattr(bot_member, "can_restrict_members", False))
            if r["chat_type"] == "channel":
                can_ban = bot_member.status == ChatMemberStatus.ADMINISTRATOR

            if not can_ban:
                save_chat(r["chat_id"], r["title"], r["chat_type"], False)
                fail.append((r["title"], "No ban permission"))
                continue

            await context.bot.ban_chat_member(r["chat_id"], uid, revoke_messages=True)
            check = await context.bot.get_chat_member(r["chat_id"], uid)

            if check.status == ChatMemberStatus.KICKED:
                ok.append(r["title"])
                save_chat(r["chat_id"], r["title"], r["chat_type"], True)
            else:
                fail.append((r["title"], f"Verification: {check.status}"))
        except (Forbidden, BadRequest, TelegramError) as e:
            fail.append((r["title"], str(e)))

    out = f"🚫 BAN ALL COMPLETED\n\nUser ID: `{uid}`\n\n✅ Verified: {len(ok)}/{len(rows)}\n❌ Failed: {len(fail)}"
    if ok:
        out += "\n\n✅ Banned:\n" + "\n".join("• " + x for x in ok)
    if fail:
        out += "\n\n❌ Failed:\n" + "\n".join(f"• {n} — {e}" for n,e in fail)

    await msg.edit_text(out, parse_mode="Markdown")
    await owner_dm(context, out)

async def unbanall(update, context):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    uid = target(update, context)
    if not uid:
        return await update.message.reply_text(
            "Usage: /unbanall <user_id>\nOr reply to the user's message with /unbanall"
        )

    rows = chats()
    ok, fail = [], []
    for r in rows:
        try:
            await context.bot.unban_chat_member(r["chat_id"], uid, only_if_banned=True)
            check = await context.bot.get_chat_member(r["chat_id"], uid)
            if check.status != ChatMemberStatus.KICKED:
                ok.append(r["title"])
            else:
                fail.append((r["title"], "Still banned"))
        except TelegramError as e:
            fail.append((r["title"], str(e)))

    out = f"✅ UNBAN ALL COMPLETED\n\nUser ID: `{uid}`\n\n✅ Success: {len(ok)}/{len(rows)}\n❌ Failed: {len(fail)}"
    if fail:
        out += "\n\n❌ Failed:\n" + "\n".join(f"• {n} — {e}" for n,e in fail)
    await update.message.reply_text(out, parse_mode="Markdown")
    await owner_dm(context, out)

async def buttons(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        return await q.edit_message_text("⛔ Owner only.")

    if q.data == "panel":
        return await q.edit_message_text("🛡️ Owner Control Panel", reply_markup=panel())

    if q.data == "add":
        context.user_data["adding"] = True
        return await q.edit_message_text(
            "👤 Send Telegram User ID to add.\n\nExample: `123456789`\nSend /cancel to abort.",
            parse_mode="Markdown"
        )

    if q.data == "remove":
        rows = admins()
        kb = [[InlineKeyboardButton(f"❌ {r['user_id']}", callback_data=f"rm:{r['user_id']}")]
              for r in rows]
        kb.append([InlineKeyboardButton("◀️ Back", callback_data="panel")])
        return await q.edit_message_text("Select admin to remove:", reply_markup=InlineKeyboardMarkup(kb))

    if q.data.startswith("rm:"):
        uid = int(q.data.split(":")[1])
        remove_admin(uid)
        return await q.edit_message_text("✅ Admin removed.", reply_markup=panel())

    if q.data == "listadmins":
        rows = admins()
        text = f"👥 ADMINS\n\n👑 Owner: `{OWNER_ID}`\n"
        text += "\n".join(f"• `{r['user_id']}`" for r in rows) or "• No additional admins"
        return await q.edit_message_text(text, parse_mode="Markdown", reply_markup=panel())

    if q.data == "chats":
        rows = chats()
        if not rows:
            text = "📡 No connected chats."
        else:
            text = "📡 CONNECTED CHATS\n\n"
            for r in rows:
                text += f"{'✅' if r['can_ban'] else '⚠️'} {r['title']}\n  {r['chat_type']} | ID: {r['chat_id']}\n"
        return await q.edit_message_text(text, reply_markup=panel())

async def owner_text(update, context):
    if update.effective_user.id != OWNER_ID or not context.user_data.get("adding"):
        return
    raw = update.message.text.strip()
    if raw.lower() == "/cancel":
        context.user_data.pop("adding", None)
        return await update.message.reply_text("Cancelled.", reply_markup=panel())
    try:
        uid = int(raw)
        if uid <= 0: raise ValueError
    except ValueError:
        return await update.message.reply_text("❌ Invalid numeric User ID.")
    add_admin(uid)
    context.user_data.pop("adding", None)
    await update.message.reply_text(f"✅ Admin added\n\nUser ID: `{uid}`",
                                    parse_mode="Markdown", reply_markup=panel())

async def my_chat_member(update, context):
    cm = update.my_chat_member
    if not cm:
        return
    chat = cm.chat
    if cm.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
        can_ban = await register_chat(chat, context)
        await owner_dm(context,
            f"📡 NEW CHAT CONNECTED\n\nName: {chat.title or chat.id}\n"
            f"Type: {chat.type}\nID: `{chat.id}`\nBan permission: {'✅' if can_ban else '⚠️'}")
    elif cm.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        save_chat(chat.id, chat.title or str(chat.id), chat.type, False, 0)
        await owner_dm(context, f"⚠️ BOT REMOVED\n\n{chat.title or chat.id}\nID: `{chat.id}`")

async def error_handler(update, context):
    log.exception("Unhandled error", exc_info=context.error)

def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise RuntimeError("Set BOT_TOKEN in info.py")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel_cmd))
    app.add_handler(CommandHandler("banall", banall))
    app.add_handler(CommandHandler("unbanall", unbanall))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, owner_text))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
