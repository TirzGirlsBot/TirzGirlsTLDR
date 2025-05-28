
"""
Telegram Bot: TirzGirlsBot (OpenAI v1 Fix + Topic-Aware)
Summarizes messages only from the same Telegram topic using OpenAI 1.x SDK.
"""

import os
import logging
import traceback
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Format: {chat_id: {thread_id: [ (timestamp, text, user) ]}}
chat_history = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id or 0
    user = update.effective_user.first_name
    msg = update.message.text

    ts = update.message.date
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    logger.info(f"Message from {user} in chat {chat_id}, thread {thread_id}: {msg}")

    if chat_id not in chat_history:
        chat_history[chat_id] = {}
    if thread_id not in chat_history[chat_id]:
        chat_history[chat_id][thread_id] = []

    chat_history[chat_id][thread_id].append((ts, msg, user))

    # Trim older messages
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    chat_history[chat_id][thread_id] = [
        m for m in chat_history[chat_id][thread_id] if m[0] > cutoff
    ]

async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id or 0
    args = context.args
    duration = 180

    if args:
        try:
            if "m" in args[0]:
                duration = min(int(args[0].replace("m", "")), 180)
            elif "h" in args[0]:
                duration = min(int(args[0].replace("h", "")) * 60, 180)
        except Exception as e:
            logger.warning(f"Invalid duration argument: {args[0]}")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=duration)
    thread_messages = chat_history.get(chat_id, {}).get(thread_id, [])
    messages = [f"{user}: {text}" for ts, text, user in thread_messages if ts > cutoff]

    logger.info(f"Messages found for summarization in chat {chat_id}, thread {thread_id}: {len(messages)}")

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    prompt = """You're a helpful assistant summarizing a Telegram topic thread (conversation within a group).
Only summarize messages from this topic (thread). Do not include unrelated topics from other threads.
Keep it natural, chronological, no emojis or bullet points.

Here is the conversation:

""" + "\n".join(messages) + """

Summarize what was said."""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(summary)
    except Exception as e:
        traceback.print_exc()
        logger.error(f"OpenAI API error: {e}")
        await update.message.reply_text("Failed to get summary. Error was logged.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/tldr [time] — Summarize recent messages (same topic only)\n"
        "/clearhistory — Clear this topic’s message history\n"
        "/help — Show this message"
    )

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id or 0
    if chat_id in chat_history and thread_id in chat_history[chat_id]:
        chat_history[chat_id].pop(thread_id)
    await update.message.reply_text("Topic history cleared.")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CommandHandler("tldr", summarize))
    app.add_handler(CommandHandler("clearhistory", clear_history))
    app.add_handler(CommandHandler("help", help_command))

    logger.info("Bot is starting.")
    app.run_polling()

if __name__ == '__main__':
    main()
