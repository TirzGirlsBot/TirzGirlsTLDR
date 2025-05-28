def handle(update, context):
    query = ' '.join(context.args)
    if not query:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Ask me something juicy, babe.")
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text=f"Sis... I heard: {query}. Let me see... That’s a hard pass.")