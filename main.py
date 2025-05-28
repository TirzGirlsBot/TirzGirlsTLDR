
import os
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, ChatMember
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler, ChatMemberHandler
from openai import OpenAI

BOT_NAME = "Summaria"
BOT_HANDLE = "@tirzgirlstdlrbot"

openai_api_key = os.getenv("OPENAI_API_KEY")
bot_token = os.getenv("BOT_TOKEN")

client = OpenAI(api_key=openai_api_key)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory storage
message_log = {}

def format_time(ts):
    return ts.strftime('%Y-%m-%d %H:%M:%S')

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return

    chat_id = update.effective_chat.id
    if chat_id not in message_log:
        message_log[chat_id] = []

    message_log[chat_id].append({
        "user": update.message.from_user.full_name,
        "text": update.message.text or "",
        "time": datetime.now(timezone.utc)
    })

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if BOT_HANDLE not in update.message.text:
        return

    chat_id = update.effective_chat.id
    now = datetime.now(timezone.utc)
    time_limit = timedelta(hours=3)
    messages = message_log.get(chat_id, [])

    recent_messages = [
        f"{msg['user']}: {msg['text']}"
        for msg in messages
        if now - msg['time'] <= time_limit
    ]

    if not recent_messages:
        await update.message.reply_text("There's nothing to summarize from the past few hours.")
        return

    prompt = "Summarize the following group chat in plain English. Keep it chronological and clear, without emojis or headers:

"
    prompt += "
".join(recent_messages)

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You summarize group chats clearly and naturally."},
                {"role": "user", "content": prompt}
            ]
        )
        await update.message.reply_text(response.choices[0].message.content.strip())
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await update.message.reply_text("Sorry, I couldn't generate a summary.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if BOT_HANDLE not in update.message.text:
        return

    help_text = (
        "Hi! I'm the group summary bot.

"
        "Here’s what you can do:
"
        "- `@tirzgirlstdlrbot /tldr` — get a summary of the last 3 hours
"
        "- `@tirzgirlstdlrbot /clear` — clear the chat log
"
        "- `@tirzgirlstdlrbot /help` — show this message"
    )
    await update.message.reply_text(help_text)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if BOT_HANDLE not in update.message.text:
        return

    chat_id = update.effective_chat.id
    message_log[chat_id] = []
    await update.message.reply_text("Chat history cleared.")

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.chat_member.new_chat_members:
        if member.username == BOT_HANDLE.replace("@", ""):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Hi! I'm Summaria, the group summary bot. Mention me with /tldr to get a recap of the last few hours."
            )

app = ApplicationBuilder().token(bot_token).build()

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_message))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex(rf"{BOT_HANDLE}.*\/tldr"), tldr))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex(rf"{BOT_HANDLE}.*\/help"), help_cmd))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex(rf"{BOT_HANDLE}.*\/clear"), clear_cmd))
app.add_handler(ChatMemberHandler(welcome, ChatMemberHandler.CHAT_MEMBER))

app.run_polling()
