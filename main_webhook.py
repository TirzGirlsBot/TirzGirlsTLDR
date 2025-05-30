import os
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.ext.webhook import WebhookServer
from openai import OpenAI
from collections import defaultdict

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

chat_history = defaultdict(list)
cooldowns = {}
MEMORY_DB = "memory.sqlite"

def store_in_memory(chat_id, user_id, user_name, message):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO memory (chat_id, user_id, user_name, message, timestamp) VALUES (?, ?, ?, ?, ?)", (
        str(chat_id),
        str(user_id),
        user_name,
        message,
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()

def store_message(update: Update):
    msg = update.message
    if msg and msg.text:
        key = (msg.chat_id, msg.message_thread_id or 0)
        chat_history[key].append({
            "timestamp": datetime.now(timezone.utc),
            "user": msg.from_user.first_name,
            "text": msg.text.strip()
        })
        store_in_memory(msg.chat_id, msg.from_user.id, msg.from_user.first_name, msg.text.strip())

def get_recent_messages(chat_id, thread_id, duration_minutes=180):
    key = (chat_id, thread_id or 0)
    now = datetime.now(timezone.utc)
    return [
        entry for entry in chat_history[key]
        if (now - entry["timestamp"]).total_seconds() <= duration_minutes * 60
    ]

def is_on_cooldown(user_id):
    now = datetime.now(timezone.utc)
    if user_id in cooldowns and (now - cooldowns[user_id]).total_seconds() < 30:
        return True
    cooldowns[user_id] = now
    return False

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_on_cooldown(user_id):
        await update.message.reply_text("Slow down, boo 😘")
        return

    duration = 180
    if context.args:
        arg = context.args[0].lower()
        if arg.endswith("h"):
            try:
                duration = int(arg[:-1]) * 60
            except:
                pass
        elif arg.endswith("m"):
            try:
                duration = int(arg[:-1])
            except:
                pass
        elif arg == "all":
            duration = 999999

    msgs = get_recent_messages(update.effective_chat.id, update.message.message_thread_id, duration)
    if not msgs:
        await update.message.reply_text("Nothing juicy to summarize 💅🏾")
        return

    convo = "\n".join([f"{m['user']}: {m['text']}" for m in msgs])
    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You summarize Telegram group chats like a helpful assistant. No emojis or bullet points. Just plain text in the order things were said."},
                {"role": "user", "content": f"Summarize this:
{convo}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
    except:
        reply = "Babe I tried, but the summary glitched 😵‍💫"
    await update.message.reply_text(reply)

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    bot_username = context.bot.username.lower()
    if f"@{bot_username}" not in text.lower() and not msg.reply_to_message:
        return

    user_name = msg.from_user.first_name or "someone"
    prompt = text.replace(f"@{bot_username}", "").strip()
    if not prompt:
        await msg.reply_text(f"👀 I’m here, {user_name} — say something cute.")
        return

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are Summaria, a smart, shady group chat girlbot. Witty, fun, and helpful."},
                {"role": "user", "content": f"The user is {user_name}. {prompt}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
    except:
        reply = "I tried baby but my brain glitched 🫠"

    await msg.reply_text(reply)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/tldr [3h|1h|6h|all|30m] — Summarize recent convo
"
        "Mention me or reply for AI replies 💅🏾"
    )

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_message(update)

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("tldr", tldr))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_message))
    app.add_handler(MessageHandler(filters.TEXT, ai_reply))
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
