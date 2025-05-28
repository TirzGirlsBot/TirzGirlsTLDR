import os
import logging
from datetime import datetime, timedelta, UTC
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Store recent messages per topic
recent_messages = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! I'm the group summary bot. Use /tldr to get a recap of this topic's recent convo, or /help to see all commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/tldr – Summarize this topic’s last 3 hours of messages\n"
        "/help – Show this help message\n"
        "Just type /tldr in the same thread you want a summary for."
    )

async def store_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    topic_id = update.message.message_thread_id or 0
    now = datetime.now(UTC)
    chat_id = update.message.chat_id
    key = f"{chat_id}:{topic_id}"

    if key not in recent_messages:
        recent_messages[key] = []

    recent_messages[key].append({
        "text": update.message.text,
        "user": update.message.from_user.first_name,
        "time": now
    })

    # Keep only last 3 hours of messages
    cutoff = now - timedelta(hours=3)
    recent_messages[key] = [m for m in recent_messages[key] if m["time"] > cutoff]

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic_id = update.message.message_thread_id or 0
    chat_id = update.message.chat_id
    key = f"{chat_id}:{topic_id}"
    messages = recent_messages.get(key, [])

    if not messages:
        await update.message.reply_text("No recent messages to summarize for this topic.")
        return

    combined_text = "\n".join(f"{m['user']}: {m['text']}" for m in messages)

    prompt = (
        "Summarize the following group chat in plain English. "
        "Keep it chronological and clear, without emojis or headers. "
        "Don't make things up. Here is the conversation:\n\n"
        f"{combined_text}"
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(summary)
    except Exception as e:
        logging.error(str(e))
        await update.message.reply_text("Something went wrong while summarizing.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("tldr", tldr))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, store_message))
    app.run_polling()
