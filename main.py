import os
import logging
import sqlite3
import random
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from openai import OpenAI
from collections import defaultdict

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

chat_history = defaultdict(list)
cooldowns = {}
MEMORY_DB = "memory.sqlite"

PERSONALITIES = [
    "flirty and chaotic", "tired but observant", "glamorous and extra", 
    "shady but loving", "deeply emotional", "unbothered and wise",
    "a hot girl in her era", "quietly judging", "high-maintenance but right"
]

def init_personality():
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("SELECT value FROM settings WHERE key = 'personality'")
    row = cursor.fetchone()
    if not row:
        mood = random.choice(PERSONALITIES)
        cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
        conn.commit()
        return mood
    return row[0]

def reset_personality():
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    mood = random.choice(PERSONALITIES)
    cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
    conn.commit()
    return mood

def get_nickname(user_id):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS nicknames (user_id TEXT PRIMARY KEY, name TEXT)")
    cursor.execute("SELECT name FROM nicknames WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    return row[0] if row else None

def set_nickname(user_id, name):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO nicknames (user_id, name) VALUES (?, ?)", (str(user_id), name))
    conn.commit()

def is_on_cooldown(user_id):
    now = datetime.now(timezone.utc)
    if user_id in cooldowns and (now - cooldowns[user_id]).total_seconds() < 30:
        return True
    cooldowns[user_id] = now
    return False

def store_message(update: Update):
    msg = update.message
    if msg and msg.text:
        key = (msg.chat_id, msg.message_thread_id or 0)
        chat_history[key].append({
            "timestamp": datetime.now(timezone.utc),
            "user": msg.from_user.first_name,
            "text": msg.text.strip()
        })

def get_recent_messages(chat_id, thread_id, duration_minutes=180):
    key = (chat_id, thread_id or 0)
    now = datetime.now(timezone.utc)
    return [
        entry for entry in chat_history[key]
        if (now - entry["timestamp"]).total_seconds() <= duration_minutes * 60
    ]

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
    mood = init_personality()
    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": f"You summarize Telegram group chats. Use plain text in order. No emojis or formatting. Today, your mood is: {mood}."},
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
    user_id = msg.from_user.id
    name = get_nickname(user_id) or msg.from_user.first_name or "babe"
    text = msg.text.strip()
    bot_username = context.bot.username.lower()
    if f"@{bot_username}" not in text.lower() and not msg.reply_to_message:
        return

    prompt = text.replace(f"@{bot_username}", "").strip()
    mood = init_personality()

    # Nickname setting if detected
    lowered = prompt.lower()
    if lowered.startswith("call me ") or lowered.startswith("my name is "):
        nickname = prompt.split(" ", 2)[-1].strip()
        set_nickname(user_id, nickname)
        await msg.reply_text(f"Okay {nickname} 💖 Got it.")
        return

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": f"You are Summaria, the shady-smart Telegram groupchat girlbot. You're currently feeling {mood}. Speak like you're one of the crew. Use {name}'s name whenever possible."},
                {"role": "user", "content": f"The user is {name}. {prompt}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
    except:
        reply = "I tried baby but my brain glitched 🫠"

    await msg.reply_text(reply)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/tldr [3h|1h|6h|all|30m] — Summarize convo
"
        "Mention or reply to talk to me 💅🏾"
    )

async def resetmood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != os.getenv("OWNER_ID"):
        return
    new_mood = reset_personality()
    await update.message.reply_text(f"🌀 Reset complete. New personality: {new_mood}")

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_message(update)

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("tldr", tldr))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("resetmood", resetmood))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_message))
    app.add_handler(MessageHandler(filters.TEXT, ai_reply))
    app.run_polling()

if __name__ == "__main__":
    main()
