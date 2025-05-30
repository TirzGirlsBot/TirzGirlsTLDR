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
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

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

def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS nicknames (user_id TEXT PRIMARY KEY, name TEXT)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        user_id TEXT,
        user_name TEXT,
        message TEXT,
        timestamp TEXT
    )""")
    conn.commit()
    conn.close()

def init_personality():
    init_db()
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'personality'")
    row = cursor.fetchone()
    if not row:
        mood = random.choice(PERSONALITIES)
        cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
        conn.commit()
        conn.close()
        return mood
    conn.close()
    return row[0]

def reset_personality():
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    mood = random.choice(PERSONALITIES)
    cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
    conn.commit()
    conn.close()
    return mood

def get_nickname(user_id):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM nicknames WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_nickname(user_id, name):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO nicknames (user_id, name) VALUES (?, ?)", (str(user_id), name))
    conn.commit()
    conn.close()

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
                {"role": "user", "content": f"Summarize this:\n{convo}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        reply = "Babe I tried, but the summary glitched 😵‍💫"
    
    await update.message.reply_text(reply)

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle AI replies when bot is mentioned"""
    msg = update.message
    if not msg or not msg.text:
        return
    
    text = msg.text.strip()
    bot_username = context.bot.username
    
    # Debug logging
    logger.info(f"Bot username: {bot_username}")
    logger.info(f"Message text: {text}")
    logger.info(f"Message entities: {msg.entities}")
    
    # Check multiple ways the bot could be mentioned
    is_mentioned = False
    
    # Check for @username mention
    if bot_username and f"@{bot_username.lower()}" in text.lower():
        is_mentioned = True
        logger.info("Found @username mention")
    
    # Check for mention entities (more reliable)
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mentioned_username = text[entity.offset:entity.offset + entity.length]
                if bot_username and mentioned_username.lower() == f"@{bot_username.lower()}":
                    is_mentioned = True
                    logger.info(f"Found entity mention: {mentioned_username}")
    
    # Check if it's a reply to the bot
    is_reply_to_bot = (msg.reply_to_message and 
                       msg.reply_to_message.from_user and 
                       msg.reply_to_message.from_user.is_bot)
    
    if is_reply_to_bot:
        logger.info("Message is reply to bot")
    
    if not is_mentioned and not is_reply_to_bot:
        return

    user_name = msg.from_user.first_name or "someone"
    
    # Clean the prompt - remove @mentions
    prompt = text
    if bot_username:
        prompt = prompt.replace(f"@{bot_username}", "").strip()
    
    if not prompt:
        await msg.reply_text(f"👀 I'm here, {user_name} — say something cute.")
        return

    try:
        mood = init_personality()
        system_prompt = f"You are Summaria, a smart, shady group chat girlbot. Witty, fun, and helpful. Today your mood is: {mood}. Keep responses conversational and sassy but not too long. Use your personality!"
        
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"The user is {user_name}. {prompt}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        reply = "I tried baby but my brain glitched 🫠"

    await msg.reply_text(reply)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🔮 **Summaria Commands**

/tldr - Summarize last 3 hours
/tldr 1h, /tldr 6h, /tldr all - Custom time ranges
/help - Show this help

Mention me (@{}) or reply to my messages for AI chat! 💅🏾""".format(context.bot.username or "summaria")
    
    await update.message.reply_text(help_text)

async def resetmood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    owner_id = os.getenv("OWNER_ID")
    if not owner_id or str(user_id) != owner_id:
        return
    
    new_mood = reset_personality()
    await update.message.reply_text(f"🌀 Reset complete. New personality: {new_mood}")

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_message(update)

def main():
    # Initialize database on startup
    init_db()
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return
    
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY not found in environment variables")
        return
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Add handlers - AI reply BEFORE log_message
    app.add_handler(CommandHandler("tldr", tldr))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("resetmood", resetmood))
    
    # AI reply handler should come before general message logging
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), ai_reply))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_message))
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
