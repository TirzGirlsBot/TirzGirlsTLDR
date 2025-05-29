# Summaria Bot - Customized by 
# Enhanced for thread-based group summarization and AI replies
# Built with love and a little shade 💅🏾
# ===============================

"""
Summaria: TirzGirlsTLDRBot main application
"""

import os
import logging
import traceback
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from commands import vibecheck, asksummaria, summariadvice, praise, shade

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# { (chat_id, thread_id): [(timestamp, user, text)] }
chat_history = {}
warned_threads = set()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    chat_history.setdefault(key, []).append(update.message)
    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text
    bot_username = context.bot.username.lower()
    if f"@{bot_username}" in text.lower():
        prompt = text.replace(f"@{bot_username}", "")
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    recent_msgs = chat_history.get(key, [])[-5:]
    thread_summary = "Recent convo: " + " | ".join(m.text for m in recent_msgs if m.text)
.strip()
        user_name = update.effective_user.first_name or "someone"
        if not prompt:
            await msg.reply_text(f"👀 I’m here, {user_name} — say something cute.")
            return
        try:
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are Summaria, a group chat bot who talks like a sharp but sweet regular. You're opinionated, supportive, and always paying attention. You remember what people said earlier in the thread and let it influence your responses. You don't repeat facts, you give takes. You're friendly, playful, and clever — not mean or robotic. No third-person talk. Just real replies like you're part of the conversation."},
                    {"role": "user", "content": f"{thread_summary}. The user speaking is named {user_name}. {prompt}}"}
                ]
            )
            reply = completion.choices[0].message.content.strip()
        except Exception as e:
            reply = "Girl I glitched for a sec, try again 💅🏾"
        await msg.reply_text(reply)
async def ai_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.chat.type != "group" and msg.chat.type != "supergroup":
        return

    if "summaria" in msg.text.lower():
        query = msg.text
        try:
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are Summaria, a group chat bot who talks like a sharp but sweet regular. You're opinionated, supportive, and always paying attention. You remember what people said earlier in the thread and let it influence your responses. You don't repeat facts, you give takes. You're friendly, playful, and clever — not mean or robotic. No third-person talk. Just real replies like you're part of the conversation."},
                    {"role": "user", "content": query}
                ]
            )
            reply = completion.choices[0].message.content
        except Exception as e:
            reply = "Ugh. I tried, but something went wrong with my brain (AI service). Try again later, boo."
        await msg.reply_text(reply)

async def start_or_help(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
"""Hey babe, I'm Summaria 💅🏾, your thread girl. I keep up with drama, shade, glow-ups, and meltdowns — but only from this convo thread. If you just added me or fixed me, I can't see what happened before. Let’s keep it cute 💖\n\nHere’s what I do, babe:\n• /tldr [time] — I’ll spill the tea on the last 3h (or use 1h, 30m, all)\n• /clearhistory — Forget this convo’s thread\n• @Summaria — Tag me with any tea, drama, or random thought and I’ll respond (with AI 💅🏾)\n\nBut don’t get wild — I only know what I’ve seen since I got here. I can’t look back before I was fixed, and I won’t summarize other threads or groups.\n\nI’m not a genie… *yet*. Stay tuned.
"
        "I ONLY summarize convos in the topic I'm tagged in.

"
        "• /tldr [time] - Summarize the last 3h (or specify 1h, 30m, all)
"
        "• /clearhistory - Clear this topic's memory
"
        "• /vibecheck - Check the room's vibe
"
        "• /asksummaria - Ask me anything
"
        "• /summariadvice - Get unsolicited advice
"
        "• /praise - Get hyped up
"
        "• /shade - Get playfully dragged

"
        "I CAN'T:
"
        "🚫 Read messages from before my last fix
"
        "🚫 Summarize across topics or groups
"
        "🚫 Answer random AI prompts (yet 😉)
"
    """)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    chat_history.pop(key, None)
    warned_threads.discard(key)
    await update.message.reply_text("Topic history cleared.")

async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.message.message_thread_id or 0)
    messages = chat_history.get(key, [])

    if key not in warned_threads:
        await update.message.reply_text("Note: I can only summarize messages since my last fix.")
        warned_threads.add(key)

    if not messages:
        await update.message.reply_text("No recent messages to summarize.")
        return

    # parse duration
    arg = context.args[0] if context.args else '3h'
    try:
        if arg.endswith('m'):
            minutes = min(int(arg[:-1]), 180)
        elif arg.endswith('h'):
            minutes = min(int(arg[:-1]) * 60, 180)
        elif arg == 'all':
            minutes = 180
        else:
            minutes = 180
    except:
        minutes = 180

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    filtered = [(u, t) for ts, u, t in messages if ts > cutoff]

    if not filtered:
        await update.message.reply_text("No messages in that timeframe.")
        return

    chat_text = "\n".join([f"{u}: {t}" for u, t in filtered])
    prompt = (
        "You're a helpful assistant summarizing a Telegram topic thread."
        "Only summarize this thread; no emojis or bullets."
        "Here are the messages:\n"
        + chat_text +
        "\n\nGive a concise, chronological summary."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":prompt}],
        )
        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await update.message.reply_text("Failed to get summary.")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler(['start', 'help'], start_or_help))
    app.add_handler(CommandHandler("clearhistory", clear_history))
    app.add_handler(CommandHandler("tldr", summarize))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    # fun commands
    app.add_handler(CommandHandler("vibecheck", vibecheck.handle))
    app.add_handler(CommandHandler("asksummaria", asksummaria.handle))
    app.add_handler(CommandHandler("summariadvice", summariadvice.handle))
    app.add_handler(CommandHandler("praise", praise.handle))
    app.add_handler(CommandHandler("shade", shade.handle))
    app.run_polling()

if __name__ == "__main__":
    main()

from telegram.constants import ParseMode
from telegram.ext import MessageHandler, filters

# Handler to respond to AI mentions in group
async def ai_mention_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.bot.username.lower() in update.message.text.lower():
        prompt = update.message.text
        try:
            completion = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are Summaria, a group chat bot who talks like a sharp but sweet regular. You're opinionated, supportive, and always paying attention. You remember what people said earlier in the thread and let it influence your responses. You don't repeat facts, you give takes. You're friendly, playful, and clever — not mean or robotic. No third-person talk. Just real replies like you're part of the conversation."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )
            reply = completion.choices[0].message.content
            await update.message.reply_text(reply)
        except Exception as e:
            await update.message.reply_text("Girl I glitched for a sec, try again 💅🏾")

# Register the AI mention handler
application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, ai_mention_response))

def help_command(update, context):
    help_text = (
        "Hey babe, I’m Summaria 💅🏾 — your thread girl. I keep up with drama, tea, glow-ups, and meltdowns… but only from the convo I’m in.\n\n"
        "Here’s what I can do:\n"
        "• /tldr [time] — I’ll spill the tea on the last 3h (or try 1h, 30m, all)\n"
        "• /clearhistory — Forget this convo’s thread\n\n"
        "You can also tag me in any thread and just talk to me directly — I’ll respond like I’m in the group too 💄\n\n"
        "⚠️ I only summarize what I’ve seen. If you added me late or changed threads, I can’t go back. Each thread is its own little world."
    )
    update.message.reply_text(help_text)