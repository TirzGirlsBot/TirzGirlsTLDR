
# Summaria Bot - Minimal & Clean
# ===============================

import os
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

chat_history = {}
warned_threads = set()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = update.effective_user.first_name or "someone"
    key = (update.effective_chat.id, msg.message_thread_id or 0)
    chat_history.setdefault(key, []).append((datetime.now(timezone.utc), user, msg.text))

    if context.bot.username.lower() in msg.text.lower():
        prompt = msg.text.replace(f"@{context.bot.username}", "").strip()
        if not prompt:
            await msg.reply_text(f"👀 I’m here, {user} — say something cute.")
            return
        try:
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You're Summaria, a smart and slightly shady group chat assistant. You track group dynamics, remember users, and sound like one of the crew — casual, insightful, and in the know."},
                    {"role": "user", "content": f"The user speaking is named {user}. {prompt}"}
                ]
            )
            reply = completion.choices[0].message.content.strip()
        except:
            reply = "Girl I glitched for a sec, try again 💅🏾"
        await msg.reply_text(reply)

async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    messages = chat_history.get(key, [])

    if key not in warned_threads:
        await update.message.reply_text("Note: I can only summarize messages since my last fix.")
        warned_threads.add(key)

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    arg = context.args[0] if context.args else '3h'
    try:
        minutes = 180
        if arg.endswith('m'):
            minutes = min(int(arg[:-1]), 180)
        elif arg.endswith('h'):
            minutes = min(int(arg[:-1]) * 60, 180)
        elif arg == 'all':
            minutes = 180
    except:
        pass

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    filtered = [(u, t) for ts, u, t in messages if ts > cutoff]
    if not filtered:
        await update.message.reply_text("No messages in that timeframe.")
        return

    chat_text = "\n".join([f"{u}: {t}" for u, t in filtered])
    prompt = (
        "You are Summaria, a Telegram group assistant. Summarize the conversation below, keeping the tone natural and chronological. "
        "Only include what was said in this group topic, and avoid emojis or formatting:

" + chat_text
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        summary = completion.choices[0].message.content.strip()
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await update.message.reply_text("Failed to get summary.")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    chat_history.pop(key, None)
    warned_threads.discard(key)
    await update.message.reply_text("Topic history cleared.")

async def start_or_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey babe, I'm Summaria 💅🏾 — your group chat assistant.

"
        "Here's what I do:
"
        "• /tldr [time] — I’ll summarize the last 3h (or use 1h, 30m, all)
"
        "• /clearhistory — Forget this convo’s thread
"
        "• @Summaria — Tag me and I’ll reply with AI 🧠

"
        "I only summarize convos in the topic I’m tagged in. I can't look back before I was fixed. I’m not omniscient… yet 😉"
    )

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler(['start', 'help'], start_or_help))
    app.add_handler(CommandHandler("tldr", summarize))
    app.add_handler(CommandHandler("clearhistory", clear_history))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
