import os
import openai
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

openai.api_key = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Store chat history per (chat_id, thread_id)
CHAT_HISTORY = {}

def store_message(chat_id, thread_id, user, text):
    key = (chat_id, thread_id)
    if key not in CHAT_HISTORY:
        CHAT_HISTORY[key] = []
    CHAT_HISTORY[key].append({
        "user": user,
        "text": text,
        "time": datetime.utcnow()
    })

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        thread_id = update.message.message_thread_id or 0
        store_message(
            chat_id=update.effective_chat.id,
            thread_id=thread_id,
            user=update.effective_user.first_name,
            text=update.message.text
        )

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id or 0
    key = (update.effective_chat.id, thread_id)

    if key not in CHAT_HISTORY:
        await update.message.reply_text("No conversation history in this thread yet.")
        return

    now = datetime.utcnow()
    recent_msgs = [
        msg for msg in CHAT_HISTORY[key]
        if msg["time"] > now - timedelta(hours=3)
    ]
    if not recent_msgs:
        await update.message.reply_text("No recent messages in this thread to summarize.")
        return

    combined_text = "\n".join([f'{msg["user"]}: {msg["text"]}' for msg in recent_msgs])
    summary_prompt = (
        "Summarize the following group chat transcript into structured sections. "
        "Identify and group major discussion topics yourself — do not rely on any predefined topics. "
        "Use emojis and short titles as section headers (like '🎵 Music', '🥾 Hiking', '🍕 Food'), "
        "and write in a clear, friendly, human-like tone. The summary should feel like a natural recap for someone who missed the chat.

"
        f"Chat transcript:
{combined_text}"
    )

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You summarize group chats with friendly structure and emoji section headers."},
            {"role": "user", "content": summary_prompt}
        ]
    )
    summary = response["choices"][0]["message"]["content"]
    await update.message.reply_text(summary)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
app.add_handler(CommandHandler("tldr", tldr))
app.run_polling()