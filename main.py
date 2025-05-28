
"""
Telegram Bot: TirzGirlsBot (Final Debug)
Summarizes group messages using OpenAI (GPT-3.5) and logs everything
"""

import os
import logging
import traceback
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

chat_history = {}

def clean_message(msg):
    if msg.text and not msg.text.startswith(("/", "@")):
        return msg.text
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    msg = update.message.text

    logger.info(f"Message from {user} in chat {chat_id}: {msg}")

    if update.message and not update.message.is_automatic_forward:
        if chat_id not in chat_history:
            chat_history[chat_id] = []
        chat_history[chat_id].append((update.message.date, msg, user))

        cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
        chat_history[chat_id] = [m for m in chat_history[chat_id] if m[0] > cutoff]

        logger.info(f"Stored message. Total stored for chat {chat_id}: {len(chat_history[chat_id])}")

async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
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
    messages = [f"{user}: {text}" for ts, text, user in chat_history.get(chat_id, []) if ts > cutoff]

    logger.info(f"Messages found for summarization: {len(messages)}")

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    prompt = (
        "You're a helpful assistant tasked with summarizing a group chat conversation.
"
        "Do not use bullet points, emojis, or topic categories. Just give a flowing, plain-text recap.
"
        "Keep it natural, like someone casually explaining what happened earlier.

"
        "Here is the recent chat:

"
        + "\n".join(messages) +
        "

Summarize what was said."
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.choices[0].message.content.strip()
        logger.info("Summary generated successfully.")
        await update.message.reply_text(summary)
    except Exception as e:
        traceback.print_exc()
        logger.error(f"OpenAI API error: {e}")
        await update.message.reply_text("Failed to get summary. Error was logged.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/tldr [time] — Summarize recent messages (max 3h)\n"
        "/clearhistory — Clear history (admin only)\n"
        "/help — Show this message"
    )

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_history.pop(chat_id, None)
    logger.info(f"Cleared history for chat {chat_id}")
    await update.message.reply_text("Message history cleared.")

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
