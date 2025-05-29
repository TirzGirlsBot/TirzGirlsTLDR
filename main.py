
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
    chat_history.setdefault(key, []).append((datetime.now(timezone.utc), user, msg.text))
    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You're Summaria, a smart and slightly shady group chat assistant."},
                {"role": "user", "content": f"The user speaking is named {user}. {prompt}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
    messages = chat_history.get(key, [])
    if key not in warned_threads:
        await update.message.reply_text("Note: I can only summarize messages since my last fix.")
        warned_threads.add(key)
    messages = chat_history.get(key, [])

    if key not in warned_threads:
        await update.message.reply_text("Note: I can only summarize messages since my last fix.")
        warned_threads.add(key)

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    arg = context.args[0] if context.args else '3h'
        try:
            reply = completion.choices[0].message.content.strip()
            reply = "Girl I glitched for a sec, try again 💅🏾"
        await msg.reply_text(reply)
            minutes = min(int(arg[:-1]) * 60, 180)
        elif arg == 'all':
            minutes = 180
        pass

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    filtered = [(u, t) for ts, u, t in messages if ts > cutoff]
    if not filtered:
        await update.message.reply_text("No messages in that timeframe.")
        return

    chat_text = "\n".join([f"{u}: {t}" for u, t in filtered])
        prompt = f"""You are Summaria, a Telegram group assistant. Summarize the conversation below, keeping the tone natural and chronological. Only include what was said in this group topic, and avoid emojis or formatting:

{chat_text}"""
