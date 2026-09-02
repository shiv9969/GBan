import sqlite3
import logging
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

from info import BOT_TOKEN, OWNER_ID, ADMINS


# ============================================================
# CONFIG
# ============================================================

DB_PATH = "bot.db"
PORT = 8000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger(__name__)


# ============================================================
# KOYEB HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            body = b"OK"

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def start_health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    log.info(
        "Koyeb health server running on port %s",
        PORT
    )

    server.serve_forever()


# ============================================================
# DATABASE
# ============================================================

def db():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with closing(db()) as con:

        con.executescript(
            """
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
                can_post INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        for uid in ADMINS:

            con.execute(
                """
                INSERT OR IGNORE INTO admins
                (user_id, added_by)
                VALUES (?, ?)
                """,
                (
                    int(uid),
                    OWNER_ID,
                ),
            )

        con.commit()


# ============================================================
# ADMIN DATABASE
# ============================================================

def is_admin(user_id):

    if user_id == OWNER_ID:
        return True

    with closing(db()) as con:

        row = con.execute(
            """
            SELECT 1
            FROM admins
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

        return row is not None


def add_admin(user_id):

    with closing(db()) as con:

        con.execute(
            """
            INSERT OR REPLACE INTO admins
            (user_id, added_by)
            VALUES (?, ?)
            """,
            (
                user_id,
                OWNER_ID,
            ),
        )

        con.commit()


def remove_admin(user_id):

    if user_id == OWNER_ID:
        return False

    with closing(db()) as con:

        cur = con.execute(
            """
            DELETE FROM admins
            WHERE user_id=?
            """,
            (user_id,),
        )

        con.commit()

        return cur.rowcount > 0


def get_admins():

    with closing(db()) as con:

        return con.execute(
            """
            SELECT user_id
            FROM admins
            ORDER BY added_at
            """
        ).fetchall()


# ============================================================
# CHAT DATABASE
# ============================================================

def get_chats():

    with closing(db()) as con:

        return con.execute(
            """
            SELECT *
            FROM chats
            ORDER BY title COLLATE NOCASE
            """
        ).fetchall()


def save_chat(
    chat_id,
    title,
    chat_type,
    can_ban,
    can_post,
    active=1,
):

    with closing(db()) as con:

        con.execute(
            """
            INSERT INTO chats
            (
                chat_id,
                title,
                chat_type,
                can_ban,
                can_post,
                active,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)

            ON CONFLICT(chat_id)
            DO UPDATE SET

                title=excluded.title,
                chat_type=excluded.chat_type,
                can_ban=excluded.can_ban,
                can_post=excluded.can_post,
                active=excluded.active,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                chat_id,
                title,
                chat_type,
                int(can_ban),
                int(can_post),
                int(active),
            ),
        )

        con.commit()


# ============================================================
# OWNER DM
# ============================================================

async def owner_dm(context, text):

    try:

        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=text,
        )

    except TelegramError:

        log.exception(
            "Could not send owner DM"
        )


# ============================================================
# CHAT PERMISSION CHECK
# ============================================================

async def get_bot_permissions(
    chat_id,
    context,
):

    me = await context.bot.get_me()

    member = await context.bot.get_chat_member(
        chat_id=chat_id,
        user_id=me.id,
    )

    can_ban = bool(
        getattr(
            member,
            "can_restrict_members",
            False,
        )
    )

    can_post = bool(
        getattr(
            member,
            "can_post_messages",
            False,
        )
    )

    return member, can_ban, can_post


# ============================================================
# AUTO REGISTER CHAT
# ============================================================

async def register_chat(
    chat,
    context,
):

    try:

        member, can_ban, can_post = await get_bot_permissions(
            chat.id,
            context,
        )

        save_chat(
            chat_id=chat.id,
            title=chat.title or str(chat.id),
            chat_type=chat.type,
            can_ban=can_ban,
            can_post=can_post,
            active=1,
        )

        log.info(
            "Registered chat %s | ban=%s post=%s",
            chat.id,
            can_ban,
            can_post,
        )

        return can_ban, can_post

    except TelegramError as e:

        log.error(
            "Chat registration failed: %s",
            e,
        )

        return False, False


# ============================================================
# OWNER PANEL
# ============================================================

def panel():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👤 Add Admin",
                    callback_data="admin_add",
                ),
                InlineKeyboardButton(
                    "❌ Remove Admin",
                    callback_data="admin_remove",
                ),
            ],

            [
                InlineKeyboardButton(
                    "👥 Admin List",
                    callback_data="admin_list",
                ),
            ],

            [
                InlineKeyboardButton(
                    "📡 Connected Chats",
                    callback_data="chat_list",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="panel",
                ),
            ],
        ]
    )


# ============================================================
# COMMAND TARGET PARSER
# ============================================================

def get_command_target(update):

    message = update.effective_message

    if not message:
        return None

    # Example:
    # /banall 123456789
    # /unbanall 123456789
    # /unban 123456789

    if message.text:

        parts = message.text.strip().split()

        if len(parts) >= 2:

            try:

                user_id = int(parts[1])

                if user_id > 0:
                    return user_id

            except ValueError:

                pass

    # Reply method
    #
    # Reply to user's message:
    #
    # /banall
    # /unban
    # /unbanall

    if (
        message.reply_to_message
        and message.reply_to_message.from_user
    ):

        return message.reply_to_message.from_user.id

    return None


# ============================================================
# /START
# ============================================================

async def start(update, context):

    user_id = update.effective_user.id

    if user_id == OWNER_ID:

        await update.message.reply_text(
            "🛡️ OWNER CONTROL PANEL",
            reply_markup=panel(),
        )

        return

    if is_admin(user_id):

        await update.message.reply_text(
            "✅ You are an authorized admin.\n\n"

            "🚫 Ban:\n"
            "/banall <user_id>\n\n"

            "🔓 Unban:\n"
            "/unbanall <user_id>\n"
            "/unban <user_id>\n\n"

            "Reply to a user's message and use "
            "/banall or /unban."
        )

        return

    await update.message.reply_text(
        "⛔ You are not authorized."
    )


# ============================================================
# /PANEL
# ============================================================

async def panel_command(update, context):

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "⛔ Owner only."
        )

        return

    await update.message.reply_text(
        "🛡️ OWNER CONTROL PANEL",
        reply_markup=panel(),
    )


# ============================================================
# /BANALL
# ============================================================

async def banall(update, context):

    if not is_admin(update.effective_user.id):

        await update.message.reply_text(
            "⛔ You are not authorized."
        )

        return

    user_id = get_command_target(update)

    if not user_id:

        await update.message.reply_text(
            "Usage:\n\n"
            "/banall <user_id>\n\n"
            "OR reply to user's message with:\n"
            "/banall"
        )

        return

    rows = get_chats()

    if not rows:

        await update.message.reply_text(
            "⚠️ No connected chats."
        )

        return

    progress = await update.message.reply_text(
        f"🚫 BAN ALL STARTED\n\n"
        f"User ID: {user_id}\n"
        f"Chats: {len(rows)}"
    )

    success = []
    failed = []

    for row in rows:

        if not row["active"]:

            failed.append(
                (
                    row["title"],
                    "Chat is inactive",
                )
            )

            continue

        try:

            # IMPORTANT:
            # Always check fresh Telegram permissions.
            member, can_ban, can_post = await get_bot_permissions(
                row["chat_id"],
                context,
            )

            # Same permission check for:
            # group
            # supergroup
            # channel
            #
            # Telegram Bot API uses can_restrict_members
            # for ban/unban rights.

            if not can_ban:

                save_chat(
                    row["chat_id"],
                    row["title"],
                    row["chat_type"],
                    False,
                    can_post,
                    1,
                )

                failed.append(
                    (
                        row["title"],
                        "Bot does not have can_restrict_members",
                    )
                )

                continue

            # ACTUAL BAN
            #
            # This works for group,
            # supergroup and channel.

            result = await context.bot.ban_chat_member(
                chat_id=row["chat_id"],
                user_id=user_id,
                revoke_messages=True,
            )

            if not result:

                failed.append(
                    (
                        row["title"],
                        "Telegram returned False",
                    )
                )

                continue

            # VERIFY
            check = await context.bot.get_chat_member(
                row["chat_id"],
                user_id,
            )

            if check.status == ChatMemberStatus.KICKED:

                success.append(
                    row["title"]
                )

                save_chat(
                    row["chat_id"],
                    row["title"],
                    row["chat_type"],
                    True,
                    can_post,
                    1,
                )

            else:

                failed.append(
                    (
                        row["title"],
                        f"Verification status: {check.status}",
                    )
                )

        except TelegramError as e:

            failed.append(
                (
                    row["title"],
                    str(e),
                )
            )

    # ========================================================
    # RESULT
    # ========================================================

    report = (
        "🚫 BAN ALL COMPLETED\n\n"

        f"👤 User ID: `{user_id}`\n\n"

        f"✅ Verified banned: "
        f"{len(success)}/{len(rows)}\n"

        f"❌ Failed: "
        f"{len(failed)}"
    )

    if success:

        report += (
            "\n\n"
            "✅ SUCCESS:\n"
            +
            "\n".join(
                f"• {name}"
                for name in success
            )
        )

    if failed:

        report += (
            "\n\n"
            "❌ FAILED:\n"
            +
            "\n".join(
                f"• {name}\n  └ {reason}"
                for name, reason in failed
            )
        )

    await progress.edit_text(
        report,
        parse_mode="Markdown",
    )

    # Owner always gets report
    await owner_dm(
        context,
        report,
    )


# ============================================================
# /UNBANALL
# ============================================================

async def unbanall(update, context):

    if not is_admin(update.effective_user.id):

        await update.message.reply_text(
            "⛔ You are not authorized."
        )

        return

    user_id = get_command_target(update)

    if not user_id:

        await update.message.reply_text(
            "Usage:\n\n"
            "/unbanall <user_id>\n\n"
            "OR reply to user's message with:\n"
            "/unbanall"
        )

        return

    rows = get_chats()

    if not rows:

        await update.message.reply_text(
            "⚠️ No connected chats."
        )

        return

    progress = await update.message.reply_text(
        f"🔓 UNBAN ALL STARTED\n\n"
        f"User ID: {user_id}\n"
        f"Chats: {len(rows)}"
    )

    success = []
    failed = []

    for row in rows:

        if not row["active"]:

            failed.append(
                (
                    row["title"],
                    "Chat is inactive",
                )
            )

            continue

        try:

            # Fresh permission check
            member, can_ban, can_post = await get_bot_permissions(
                row["chat_id"],
                context,
            )

            if not can_ban:

                failed.append(
                    (
                        row["title"],
                        "Bot does not have can_restrict_members",
                    )
                )

                continue

            # ACTUAL UNBAN

            result = await context.bot.unban_chat_member(
                chat_id=row["chat_id"],
                user_id=user_id,
                only_if_banned=True,
            )

            if not result:

                failed.append(
                    (
                        row["title"],
                        "Telegram returned False",
                    )
                )

                continue

            # VERIFY
            #
            # After unban the user may be:
            #
            # LEFT
            # MEMBER
            #
            # Both mean the ban has been removed.
            #
            # KICKED means still banned.

            check = await context.bot.get_chat_member(
                row["chat_id"],
                user_id,
            )

            if check.status != ChatMemberStatus.KICKED:

                success.append(
                    (
                        row["title"],
                        row["chat_id"],
                        can_post,
                        check.status,
                    )
                )

            else:

                failed.append(
                    (
                        row["title"],
                        "Still KICKED after unban",
                    )
                )

        except TelegramError as e:

            failed.append(
                (
                    row["title"],
                    str(e),
                )
            )

    # ========================================================
    # SEND CHAT NOTIFICATION
    # ========================================================

    notified = []
    notification_failed = []

    for (
        title,
        chat_id,
        can_post,
        status,
    ) in success:

        # In a channel the bot may be able to ban/unban
        # but may NOT have permission to post messages.
        #
        # Therefore unban itself must not depend on
        # notification permission.

        if not can_post:

            notification_failed.append(
                (
                    title,
                    "Unbanned, but bot cannot post notification"
                )
            )

            continue

        try:

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🔓 USER UNBANNED\n\n"

                    f"👤 User ID: `{user_id}`\n\n"

                    "✅ The ban has been removed.\n"
                    "↩️ This user can join/rejoin this chat again."
                ),
                parse_mode="Markdown",
            )

            notified.append(title)

        except TelegramError as e:

            notification_failed.append(
                (
                    title,
                    f"Unbanned, notification failed: {e}"
                )
            )

    # ========================================================
    # RESULT
    # ========================================================

    report = (
        "🔓 UNBAN ALL COMPLETED\n\n"

        f"👤 User ID: `{user_id}`\n\n"

        f"✅ Verified unbanned: "
        f"{len(success)}/{len(rows)}\n"

        f"📢 Notifications sent: "
        f"{len(notified)}\n"

        f"❌ Failed: "
        f"{len(failed)}"
    )

    if success:

        report += (
            "\n\n"
            "✅ UNBANNED:\n"
            +
            "\n".join(
                f"• {title} ({status})"
                for title, chat_id, can_post, status
                in success
            )
        )

    if notified:

        report += (
            "\n\n"
            "📢 NOTIFICATION SENT:\n"
            +
            "\n".join(
                f"• {title}"
                for title in notified
            )
        )

    if notification_failed:

        report += (
            "\n\n"
            "⚠️ NOTIFICATION ISSUES:\n"
            +
            "\n".join(
                f"• {title}\n  └ {reason}"
                for title, reason in notification_failed
            )
        )

    if failed:

        report += (
            "\n\n"
            "❌ FAILED:\n"
            +
            "\n".join(
                f"• {title}\n  └ {reason}"
                for title, reason in failed
            )
        )

    await progress.edit_text(
        report,
        parse_mode="Markdown",
    )

    # Owner ALWAYS gets report.
    await owner_dm(
        context,
        report,
    )


# ============================================================
# /UNBAN
# ============================================================

async def unban(update, context):

    # Same working logic as /unbanall
    await unbanall(
        update,
        context,
    )


# ============================================================
# OWNER BUTTONS
# ============================================================

async def buttons(update, context):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != OWNER_ID:

        await query.edit_message_text(
            "⛔ Owner only."
        )

        return

    data = query.data

    # --------------------------------------------------------
    # PANEL
    # --------------------------------------------------------

    if data == "panel":

        await query.edit_message_text(
            "🛡️ OWNER CONTROL PANEL",
            reply_markup=panel(),
        )

        return

    # --------------------------------------------------------
    # ADD ADMIN
    # --------------------------------------------------------

    if data == "admin_add":

        context.user_data["adding_admin"] = True

        await query.edit_message_text(
            "👤 ADD ADMIN\n\n"

            "Send Telegram User ID.\n\n"

            "Example:\n"
            "`123456789`\n\n"

            "Send /cancel to cancel.",
            parse_mode="Markdown",
        )

        return

    # --------------------------------------------------------
    # REMOVE ADMIN
    # --------------------------------------------------------

    if data == "admin_remove":

        rows = get_admins()

        buttons = []

        for row in rows:

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"❌ {row['user_id']}",
                        callback_data=f"remove:{row['user_id']}",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "◀️ Back",
                    callback_data="panel",
                )
            ]
        )

        await query.edit_message_text(
            "❌ SELECT ADMIN TO REMOVE:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
        )

        return

    # --------------------------------------------------------
    # REMOVE ADMIN
    # --------------------------------------------------------

    if data.startswith("remove:"):

        user_id = int(
            data.split(":")[1]
        )

        if remove_admin(user_id):

            text = (
                "✅ ADMIN REMOVED\n\n"
                f"User ID: `{user_id}`"
            )

        else:

            text = (
                "⚠️ Admin was not found."
            )

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=panel(),
        )

        return

    # --------------------------------------------------------
    # ADMIN LIST
    # --------------------------------------------------------

    if data == "admin_list":

        rows = get_admins()

        text = (
            "👥 ADMIN LIST\n\n"
            f"👑 Owner: `{OWNER_ID}`\n"
        )

        if rows:

            text += "\n".join(
                f"• `{row['user_id']}`"
                for row in rows
            )

        else:

            text += (
                "• No additional admins"
            )

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=panel(),
        )

        return

    # --------------------------------------------------------
    # CHAT LIST
    # --------------------------------------------------------

    if data == "chat_list":

        rows = get_chats()

        if not rows:

            text = (
                "📡 CONNECTED CHATS\n\n"
                "No chats registered."
            )

        else:

            text = (
                "📡 CONNECTED CHATS\n\n"
            )

            for row in rows:

                ban_icon = (
                    "✅"
                    if row["can_ban"]
                    else "❌"
                )

                post_icon = (
                    "📢"
                    if row["can_post"]
                    else "🔇"
                )

                active_icon = (
                    "🟢"
                    if row["active"]
                    else "🔴"
                )

                text += (
                    f"{active_icon} {row['title']}\n"
                    f"  Type: {row['chat_type']}\n"
                    f"  Ban: {ban_icon}\n"
                    f"  Notify: {post_icon}\n"
                    f"  ID: `{row['chat_id']}`\n\n"
                )

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=panel(),
        )

        return


# ============================================================
# OWNER TEXT INPUT
# ============================================================

async def owner_text(update, context):

    if update.effective_user.id != OWNER_ID:
        return

    if not context.user_data.get(
        "adding_admin"
    ):
        return

    raw = update.message.text.strip()

    if raw.lower() == "/cancel":

        context.user_data.pop(
            "adding_admin",
            None,
        )

        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=panel(),
        )

        return

    try:

        user_id = int(raw)

        if user_id <= 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ Invalid Telegram User ID.\n"
            "Only numbers are allowed."
        )

        return

    add_admin(user_id)

    context.user_data.pop(
        "adding_admin",
        None,
    )

    await update.message.reply_text(
        "✅ ADMIN ADDED\n\n"
        f"User ID: `{user_id}`",
        parse_mode="Markdown",
        reply_markup=panel(),
    )


# ============================================================
# AUTO CHAT REGISTRATION
# ============================================================

async def my_chat_member(update, context):

    change = update.my_chat_member

    if not change:
        return

    chat = change.chat
    status = change.new_chat_member.status

    # --------------------------------------------------------
    # BOT BECAME ADMIN
    # --------------------------------------------------------

    if status == ChatMemberStatus.ADMINISTRATOR:

        can_ban, can_post = await register_chat(
            chat,
            context,
        )

        await owner_dm(
            context,
            (
                "📡 NEW CHAT CONNECTED\n\n"

                f"Name: {chat.title or chat.id}\n"
                f"Type: {chat.type}\n"
                f"ID: `{chat.id}`\n\n"

                f"🚫 Ban permission: "
                f"{'✅' if can_ban else '❌'}\n"

                f"📢 Notification permission: "
                f"{'✅' if can_post else '❌'}"
            ),
        )

        return

    # --------------------------------------------------------
    # BOT REMOVED
    # --------------------------------------------------------

    if status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
    ):

        save_chat(
            chat.id,
            chat.title or str(chat.id),
            chat.type,
            False,
            False,
            0,
        )

        await owner_dm(
            context,
            (
                "⚠️ BOT REMOVED FROM CHAT\n\n"
                f"Name: {chat.title or chat.id}\n"
                f"ID: `{chat.id}`"
            ),
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context,
):

    log.exception(
        "Unhandled error",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE"
    ):

        raise RuntimeError(
            "Please set BOT_TOKEN in info.py"
        )

    init_db()

    # Koyeb health server
    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "panel",
            panel_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "banall",
            banall,
        )
    )

    app.add_handler(
        CommandHandler(
            "unbanall",
            unbanall,
        )
    )

    app.add_handler(
        CommandHandler(
            "unban",
            unban,
        )
    )

    # Buttons
    app.add_handler(
        CallbackQueryHandler(
            buttons
        )
    )

    # Auto chat registration
    app.add_handler(
        ChatMemberHandler(
            my_chat_member,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )

    # Owner input
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            owner_text,
        )
    )

    app.add_error_handler(
        error_handler
    )

    log.info(
        "Telegram bot starting..."
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
