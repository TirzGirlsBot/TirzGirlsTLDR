import os
import openai
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

openai.api_key = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# In-memory storage of messages by chat ID
CHAT_HISTORY = {}

def store_message(chat_id, user, text):
    if chat_id not in CHAT_HISTORY:
        CHAT_HISTORY[chat_id] = []
    CHAT_HISTORY[chat_id].append({
        "user": user,
        "text": text,
        "time": datetime.utcnow()
    })

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        store_message(
            chat_id=update.effective_chat.id,
            user=update.effective_user.first_name,
            text=update.message.text
        )

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in CHAT_HISTORY:
        await update.message.reply_text("No conversation history available yet.")
        return

    now = datetime.utcnow()
    recent_msgs = [
        msg for msg in CHAT_HISTORY[chat_id]
        if msg["time"] > now - timedelta(hours=3)
    ]
    if not recent_msgs:
        await update.message.reply_text("No recent messages to summarize.")
        return

    combined_text = "\n".join([f'{msg["user"]}: {msg["text"]}' for msg in recent_msgs])
    summary_prompt = (
        "Summarize the following conversation in a natural and helpful way. "
        "Keep it under 5 sentences:

" + combined_text
    )

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You summarize group chats."},
            {"role": "user", "content": summary_prompt}
        ]
    )
    summary = response["choices"][0]["message"]["content"]
    await update.message.reply_text("📝 TLDR:
" + summary)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
app.add_handler(CommandHandler("tldr", tldr))
app.run_polling()