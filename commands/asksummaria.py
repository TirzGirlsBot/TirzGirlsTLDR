async def handle(update, context):
    query = ' '.join(context.args)
    if not query:
        await update.message.reply_text("Ask me something juicy, babe.")
    else:
        await update.message.reply_text(f"Sis... I heard: {query}. Let me see... That’s a hard pass.")
