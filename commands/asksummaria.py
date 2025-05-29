async def handle(update, context):
    query = ' '.join(context.args)
    if not query:
        await update.message.reply_text("Ask me something juicy, babe.")
        return
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    completion = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are Summaria, a groupchat assistant bot. You know you're a bot, but you talk like a regular. You're witty, helpful, and have great taste — a little glam, a little playful, but never mean. No need to introduce yourself. No third-person talk. Just be casual, warm, and in the loop."},
            {"role": "user", "content": query}
        ]
    )
    await update.message.reply_text(completion.choices[0].message.content.strip())
