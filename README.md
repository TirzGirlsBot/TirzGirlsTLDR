# Tirz TLDR Bot 📝

A group-friendly Telegram bot that silently logs messages and summarizes the last few hours of conversation when someone types `/tldr`.

## Features

- 🕵️ Logs messages in the background
- ⏱️ Summarizes the last 3 hours
- 🧠 Powered by GPT-3.5
- 👥 Great for busy group chats

## Setup

1. Set these environment variables:
   - `TELEGRAM_TOKEN`
   - `OPENAI_API_KEY`

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run:
```bash
python main.py
```

## Usage

- Add the bot to your Telegram group.
- Say `/tldr` anytime for a natural summary of the latest convo.

MIT License.