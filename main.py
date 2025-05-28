"""
Summaria: TirzGirlsTLDRBot main application
"""

import os
import logging
import traceback
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from commands import vibecheck, asksummaria, summariadvice, praise, shade

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# { (chat_id, thread_id): [(timestamp, user, text)] }
chat_history = {}
warned_threads = set()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    thread_id = msg.message_thread_id or 0
    user = msg.from_user.first_name
    text = msg.text

    if not text or text.startswith('/'):
        return

    ts = msg.date
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    key = (chat_id, thread_id)
    chat_history.setdefault(key, []).append((ts, user, text))

    # trim history older than 3 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    chat_history[key] = [m for m in chat_history[key] if m[0] > cutoff]

async def start_or_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey babe, I'm Summaria 💅🏾, your thread girl.
"
        "I ONLY summarize convos in the topic I'm tagged in.

"
        "• /tldr [time] - Summarize the last 3h (or specify 1h, 30m, all)
"
        "• /clearhistory - Clear this topic's memory
"
        "• /vibecheck - Check the room's vibe
"
        "• /asksummaria - Ask me anything
"
        "• /summariadvice - Get unsolicited advice
"
        "• /praise - Get hyped up
"
        "• /shade - Get playfully dragged

"
        "I CAN'T:
"
        "🚫 Read messages from before my last fix
"
        "🚫 Summarize across topics or groups
"
        "🚫 Answer random AI prompts (yet 😉)
"
    )

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    chat_history.pop(key, None)
    warned_threads.discard(key)
    await update.message.reply_text("Topic history cleared.")

async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    messages = chat_history.get(key, [])

    if key not in warned_threads:
        await update.message.reply_text("Note: I can only summarize messages since my last fix.")
        warned_threads.add(key)

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    # parse duration
    arg = context.args[0] if context.args else '3h'
    try:
        if arg.endswith('m'):
            minutes = min(int(arg[:-1]), 180)
        elif arg.endswith('h'):
            minutes = min(int(arg[:-1]) * 60, 180)
        elif arg == 'all':
            minutes = 180
        else:
            minutes = 180
    except:
        minutes = 180

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    filtered = [(u, t) for ts, u, t in messages if ts > cutoff]

    if not filtered:
        await update.message.reply_text("No messages in that timeframe.")
        return

    chat_text = "\n".join([f"{u}: {t}" for u, t in filtered])
    prompt = (
        "You're a helpful assistant summarizing a Telegram topic thread.
"
        "Only summarize this thread; no emojis or bullets.
"
        "Here are the messages:\n"
        + chat_text +
        "\n\nGive a concise, chronological summary."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":prompt}],
        )
        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await update.message.reply_text("Failed to get summary.")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler(['start', 'help'], start_or_help))
    app.add_handler(CommandHandler("clearhistory", clear_history))
    app.add_handler(CommandHandler("tldr", summarize))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    # fun commands
    app.add_handler(CommandHandler("vibecheck", vibecheck.handle))
    app.add_handler(CommandHandler("asksummaria", asksummaria.handle))
    app.add_handler(CommandHandler("summariadvice", summariadvice.handle))
    app.add_handler(CommandHandler("praise", praise.handle))
    app.add_handler(CommandHandler("shade", shade.handle))
    app.run_polling()

if __name__ == "__main__":
    main()