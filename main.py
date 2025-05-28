
"""
Telegram Bot: TirzGirlsBot
Summarizes tagged group chats using OpenAI
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory message history
chat_history = {}

def clean_message(msg):
    if msg.text and not msg.text.startswith(("/", "@")):
        return msg.text
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message and not update.message.is_automatic_forward:
        if chat_id not in chat_history:
            chat_history[chat_id] = []
        chat_history[chat_id].append((update.message.date, update.message.text, update.effective_user.first_name))

        # Trim to last 3 hours only
        cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
        chat_history[chat_id] = [msg for msg in chat_history[chat_id] if msg[0] > cutoff]

async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    duration = 180  # default to 3 hours

    if args:
        try:
            if "m" in args[0]:
                duration = min(int(args[0].replace("m", "")), 180)
            elif "h" in args[0]:
                duration = min(int(args[0].replace("h", "")) * 60, 180)
        except:
            pass

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=duration)
    messages = [f"{user}: {text}" for ts, text, user in chat_history.get(chat_id, []) if ts > cutoff]

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    prompt = "Summarize the following messages in a natural, human, plain-text way without emojis or topics:\n" + "\n".join(messages)

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("Failed to get summary.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/tldr [time] — Summarize recent messages (max 3h)\n"
        "/clearhistory — Clear history (admin only)\n"
        "/help — Show this message"
    )

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_history.pop(chat_id, None)
    await update.message.reply_text("Message history cleared.")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CommandHandler("tldr", summarize))
    app.add_handler(CommandHandler("clearhistory", clear_history))
    app.add_handler(CommandHandler("help", help_command))

    app.run_polling()

if __name__ == '__main__':
    main()
