import os
from datetime import datetime, timedelta, UTC
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

CHAT_HISTORY = {}

def store_message(chat_id, thread_id, user, text):
    key = (chat_id, thread_id)
    if key not in CHAT_HISTORY:
        CHAT_HISTORY[key] = []
    CHAT_HISTORY[key].append({
        "user": user,
        "text": text,
        "time": datetime.now(UTC)
    })

def parse_duration(arg: str) -> timedelta:
    arg = arg.strip().lower()
    if arg.endswith("h"):
        return timedelta(hours=int(arg[:-1]))
    elif arg.endswith("m"):
        return timedelta(minutes=int(arg[:-1]))
    return timedelta(hours=3)  # default fallback

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

    # Parse time argument
    try:
        duration = parse_duration(context.args[0]) if context.args else timedelta(hours=3)
    except Exception:
        await update.message.reply_text("Invalid format. Use /tldr 30m or /tldr 2h")
        return

    now = datetime.now(UTC)
    recent_msgs = [
        msg for msg in CHAT_HISTORY[key]
        if msg["time"] > now - duration
    ]
    if not recent_msgs:
        await update.message.reply_text("No recent messages in this thread to summarize.")
        return

    combined_text = "\n".join([f'{msg["user"]}: {msg["text"]}' for msg in recent_msgs])
    summary_prompt = (
        "Summarize the following group chat transcript into structured sections. "
        "Identify and group major discussion topics yourself — do not rely on any predefined topics. "
        "Use emojis and short titles as section headers (like '🎵 Music', '🥾 Hiking', '🍕 Food'), "
        "and write in a clear, friendly, human-like tone. The summary should feel like a natural recap for someone who missed the chat.\n\n"
        f"Chat transcript:\n{combined_text}"
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You summarize group chats with friendly structure and emoji section headers."},
            {"role": "user", "content": summary_prompt}
        ]
    )
    summary = response.choices[0].message.content
    await update.message.reply_text(summary)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
app.add_handler(CommandHandler("tldr", tldr))

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id or 0
    key = (update.effective_chat.id, thread_id)
    if key in CHAT_HISTORY:
        CHAT_HISTORY[key] = []
        await update.message.reply_text("🧹 Cleared this thread's chat history.")
    else:
        await update.message.reply_text("There's nothing to clear yet in this thread.")

app.add_handler(CommandHandler("clear", clear))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "/tldr — summarize the last 3 hours\n"
        "/tldr 1h — summarize the last 1 hour\n"
        "/tldr 30m — summarize the last 30 minutes\n"
        "/clear — clear this thread's chat history\n"
        "/help — show this message"
    )
    await update.message.reply_text(help_text)
app.add_handler(CommandHandler("help", help_command))
app.run_polling()

