def handle(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id, text="I would agree with you but then we’d both be wrong.")