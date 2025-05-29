# Summaria Bot - Customized by Sadeja
# Enhanced for thread-based group summarization and AI replies
# Built with love and a little shade 💅🏾
# ===============================


import openai
from telegram import Update
from telegram.ext import ContextTypes

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Ask me something juicy, babe.")
        return

    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Summaria, a helpful but shady assistant in a Telegram group. Respond like you’re part of the group chat, know the vibe, and make it fun."},
                {"role": "user", "content": query}
            ],
            max_tokens=300,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        reply = f"Girl I tried but something went wrong: {e}"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply)
