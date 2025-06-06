import os
import logging
import sqlite3
import random
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)
from openai import OpenAI
from collections import defaultdict

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

chat_history = defaultdict(list)
cooldowns = {}
MEMORY_DB = "memory.sqlite"
DAILY_LIMIT = 2000  # Daily AI response limit - very generous since API costs are low

PERSONALITIES = [
    "flirty and chaotic", "tired but observant", "glamorous and extra", 
    "shady but loving", "deeply emotional", "unbothered and wise",
    "a hot girl in her era", "quietly judging", "high-maintenance but right"
]

def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    
    # Create basic tables
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS nicknames (user_id TEXT PRIMARY KEY, name TEXT)")
    
    # Create memory table with original schema first
    cursor.execute("""CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        user_id TEXT,
        user_name TEXT,
        message TEXT,
        timestamp TEXT
    )""")
    
    # Check if thread_id column exists, if not add it
    cursor.execute("PRAGMA table_info(memory)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'thread_id' not in columns:
        logger.info("Adding thread_id column to memory table")
        cursor.execute("ALTER TABLE memory ADD COLUMN thread_id TEXT DEFAULT '0'")
        # Update existing records to have thread_id = '0' (General topic)
        cursor.execute("UPDATE memory SET thread_id = '0' WHERE thread_id IS NULL")
    
    cursor.execute("""CREATE TABLE IF NOT EXISTS user_preferences (
        user_id TEXT PRIMARY KEY,
        nickname TEXT,
        personality_notes TEXT,
        last_interaction TEXT,
        interaction_count INTEGER DEFAULT 0
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS chat_context (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        thread_id TEXT,
        topic TEXT,
        last_updated TEXT,
        message_count INTEGER DEFAULT 0
    )""")
    
    # Track when the bot was last started/updated
    cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                   ('last_startup', datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

def store_in_persistent_memory(chat_id, thread_id, user_id, user_name, message):
    """Store message in persistent database"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO memory (chat_id, thread_id, user_id, user_name, message, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (
        str(chat_id),
        str(thread_id or 0),
        str(user_id),
        user_name,
        message,
        datetime.now(timezone.utc).isoformat()
    ))
    
    # Update user interaction count
    cursor.execute("""INSERT OR REPLACE INTO user_preferences 
                     (user_id, nickname, personality_notes, last_interaction, interaction_count)
                     VALUES (?, ?, 
                             COALESCE((SELECT personality_notes FROM user_preferences WHERE user_id = ?), ''),
                             ?, 
                             COALESCE((SELECT interaction_count FROM user_preferences WHERE user_id = ?), 0) + 1)""",
                   (str(user_id), user_name, str(user_id), datetime.now(timezone.utc).isoformat(), str(user_id)))
    
    conn.commit()
    conn.close()

def get_user_context(user_id):
    """Get context about a specific user"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, personality_notes, interaction_count FROM user_preferences WHERE user_id = ?", 
                   (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "nickname": row[0],
            "notes": row[1] or "",
            "interaction_count": row[2] or 0
        }
    return {"nickname": None, "notes": "", "interaction_count": 0}

def get_recent_chat_context(chat_id, limit=10):
    """Get recent context from this chat for better AI responses"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    
    # Get recent messages for context
    cursor.execute("""SELECT user_name, message FROM memory 
                     WHERE chat_id = ? 
                     ORDER BY timestamp DESC LIMIT ?""", 
                   (str(chat_id), limit))
    
    recent_messages = []
    for row in cursor.fetchall():
        recent_messages.append(f"{row[0]}: {row[1]}")
    
    conn.close()
    return "\n".join(reversed(recent_messages)) if recent_messages else ""

def init_personality():
    init_db()
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'personality'")
    row = cursor.fetchone()
    if not row:
        mood = random.choice(PERSONALITIES)
        cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
        conn.commit()
        conn.close()
        return mood
    conn.close()
    return row[0]

def reset_personality():
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    mood = random.choice(PERSONALITIES)
    cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
    conn.commit()
    conn.close()
    return mood

def get_nickname(user_id):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM nicknames WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_nickname(user_id, name):
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO nicknames (user_id, name) VALUES (?, ?)", (str(user_id), name))
    conn.commit()
    conn.close()

def is_on_cooldown(user_id):
    now = datetime.now(timezone.utc)
    if user_id in cooldowns and (now - cooldowns[user_id]).total_seconds() < 30:
        return True
    cooldowns[user_id] = now
    return False

def is_on_command_cooldown(user_id):
    """Separate cooldown for commands - much shorter"""
    now = datetime.now(timezone.utc)
    command_cooldowns_key = f"cmd_{user_id}"
    if command_cooldowns_key in cooldowns and (now - cooldowns[command_cooldowns_key]).total_seconds() < 3:
        return True
    cooldowns[command_cooldowns_key] = now
    return False

def get_daily_usage():
    """Get today's AI usage count"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    today = datetime.now(timezone.utc).date().isoformat()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (f"daily_usage_{today}",))
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row else 0

def increment_daily_usage():
    """Increment today's usage count"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    today = datetime.now(timezone.utc).date().isoformat()
    current = get_daily_usage()
    cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                   (f"daily_usage_{today}", str(current + 1)))
    conn.commit()
    conn.close()
    return current + 1

def get_startup_time():
    """Get when the bot was last started"""
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'last_startup'")
    row = cursor.fetchone()
    conn.close()
    if row:
        return datetime.fromisoformat(row[0])
    return datetime.now(timezone.utc)

def is_daily_limit_reached():
    """Check if daily AI usage limit is reached"""
    return get_daily_usage() >= DAILY_LIMIT

def store_message(update: Update):
    """Store message in both memory and persistent storage"""
    msg = update.message
    if msg and (msg.text or msg.caption):
        # Use the text or caption
        message_text = msg.text or msg.caption or ""
        
        # Use message_thread_id for topics (General, Fashion, etc.)
        key = (msg.chat_id, msg.message_thread_id or 0)
        chat_history[key].append({
            "timestamp": datetime.now(timezone.utc),
            "user": msg.from_user.first_name,
            "text": message_text.strip()
        })
        
        # Store in persistent database with topic info
        store_in_persistent_memory(
            msg.chat_id, 
            msg.message_thread_id or 0,
            msg.from_user.id,
            msg.from_user.first_name,
            message_text.strip()
        )
        
        # Debug logging with topic info
        topic_name = "General" if not msg.message_thread_id else f"Topic-{msg.message_thread_id}"
        logger.info(f"Stored message from {msg.from_user.first_name} in chat {msg.chat_id}, topic: {topic_name}")

def store_bot_message(chat_id, thread_id, message_text):
    """Store bot's own messages so it can remember what it said"""
    key = (chat_id, thread_id or 0)
    chat_history[key].append({
        "timestamp": datetime.now(timezone.utc),
        "user": "Summaria",
        "text": message_text.strip()
    })
    
    # Also store in persistent memory
    store_in_persistent_memory(
        chat_id, 
        thread_id or 0,
        "bot",
        "Summaria", 
        message_text.strip()
    )

def get_recent_messages(chat_id, thread_id, duration_minutes=180):
    """Get recent messages from the specified thread/topic"""
    key = (chat_id, thread_id or 0)
    now = datetime.now(timezone.utc)
    
    # Try in-memory first
    memory_msgs = [
        entry for entry in chat_history[key]
        if (now - entry["timestamp"]).total_seconds() <= duration_minutes * 60
    ]
    
    topic_name = "General" if not thread_id else f"Topic-{thread_id}"
    logger.info(f"Looking for messages in {topic_name}: found {len(memory_msgs)} in memory")
    
    # If we have enough in memory, use those
    if len(memory_msgs) > 3:
        logger.info(f"Using {len(memory_msgs)} in-memory messages from {topic_name}")
        return memory_msgs
    
    # Otherwise try persistent storage for this specific topic
    logger.info(f"Checking persistent storage for {topic_name}...")
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    
    cutoff_time = (datetime.now(timezone.utc) - timedelta(minutes=duration_minutes)).isoformat()
    
    # Get messages for this specific chat and thread
    cursor.execute("""SELECT user_name, message, timestamp FROM memory 
                     WHERE chat_id = ? AND thread_id = ? AND timestamp > ?
                     ORDER BY timestamp ASC""", 
                   (str(chat_id), str(thread_id or 0), cutoff_time))
    
    messages = []
    for row in cursor.fetchall():
        user_name, message, timestamp_str = row
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
            messages.append({
                "timestamp": timestamp,
                "user": user_name,
                "text": message
            })
        except:
            continue
    
    conn.close()
    logger.info(f"Found {len(messages)} persistent messages from {topic_name}")
    
    # Return whichever has more messages
    if len(messages) > len(memory_msgs):
        return messages
    return memory_msgs

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Summarize recent messages in current thread"""
    user_id = update.effective_user.id
    
    # Use command-specific cooldown
    if is_on_command_cooldown(user_id):
        await update.message.reply_text("Wait a sec between commands 😘")
        return

    duration = 180
    if context.args:
        arg = context.args[0].lower()
        if arg.endswith("h"):
            try:
                duration = int(arg[:-1]) * 60
            except:
                pass
        elif arg.endswith("m"):
            try:
                duration = int(arg[:-1])
            except:
                pass
        elif arg == "all":
            duration = 999999

    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    
    # Get messages from the current thread
    recent_msgs = get_recent_messages(chat_id, thread_id, duration)
    
    # Debug info
    topic_name = "General" if not thread_id else "this topic"
        
    logger.info(f"TLDR in {topic_name}: {len(recent_msgs)} recent msgs")
    
    if not recent_msgs:
        # Check if this is because she was recently updated
        startup_time = get_startup_time()
        time_since_startup = datetime.now(timezone.utc) - startup_time
        
        if time_since_startup.total_seconds() < 3600:  # Less than 1 hour since startup
            hours_ago = round(time_since_startup.total_seconds() / 3600, 1)
            await update.message.reply_text(
                f"Nothing to summarize in {topic_name} bestie 💅🏾\n\n"
                f"BTW, I was updated/restarted {hours_ago} hours ago, so I can't see messages from before then. "
                f"Keep chatting and try again later! 😘"
            )
        else:
            await update.message.reply_text(f"Nothing to summarize in {topic_name} bestie 💅🏾")
        return

    # Check daily limit
    if is_daily_limit_reached():
        await update.message.reply_text("I'm too tired for summaries today babe, try again tomorrow 😴")
        return

    # Build conversation
    convo = "\n".join([f"{m['user']}: {m['text']}" for m in recent_msgs])
    mood = init_personality()
    
    logger.info(f"Sending TLDR to OpenAI: {len(convo)} chars from {len(recent_msgs)} messages")
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": f"You summarize Telegram group chats like a sassy friend. Keep it natural and conversational, not formal. You're {mood} today. No bullet points - just tell the story of what happened in this topic."},
                {"role": "user", "content": f"Summarize this chat from {topic_name}:\n{convo}"}
            ]
        )
        reply = completion.choices[0].message.content.strip()
        
        # Count toward daily usage
        increment_daily_usage()
        
    except Exception as e:
        logger.error(f"OpenAI API error in TLDR: {e}")
        reply = "Babe I tried to summarize but my brain glitched 😵‍💫"
    
    await update.message.reply_text(reply)

# Track processed messages to prevent double responses
processed_messages = set()

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Combined handler: store message AND check for AI replies"""
    
    msg = update.message
    if not msg:
        return
        
    # Create unique message ID to prevent double processing
    msg_id = f"{msg.chat_id}_{msg.message_id}"
    if msg_id in processed_messages:
        return
    processed_messages.add(msg_id)
    
    # Clean up old processed messages (keep last 100)
    if len(processed_messages) > 100:
        processed_messages.clear()
    
    # ALWAYS store the message first
    store_message(update)
    
    # Skip if no text
    if not msg.text:
        return
    
    # Skip if this is a command - let command handlers deal with it
    if msg.text.startswith('/'):
        logger.info(f"Skipping command: {msg.text}")
        return
    
    text = msg.text.strip()
    bot_username = context.bot.username
    
    # Check multiple ways the bot could be mentioned
    is_mentioned = False
    
    # Check for @username mention
    if bot_username and f"@{bot_username.lower()}" in text.lower():
        is_mentioned = True
    
    # Check for mention entities (more reliable)
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mentioned_username = text[entity.offset:entity.offset + entity.length]
                if bot_username and mentioned_username.lower() == f"@{bot_username.lower()}":
                    is_mentioned = True
    
    # Check if it's a reply to the bot
    is_reply_to_bot = (msg.reply_to_message and 
                       msg.reply_to_message.from_user and 
                       msg.reply_to_message.from_user.is_bot)
    
    if not is_mentioned and not is_reply_to_bot:
        return

    # Check daily limit BEFORE processing
    if is_daily_limit_reached():
        tired_responses = [
            "I'm exhausted today bestie, try again tomorrow 😴",
            "Hit my daily energy limit, catch me tomorrow 💤",
            "Brain is fried for today, reset at midnight ✨"
        ]
        await msg.reply_text(random.choice(tired_responses))
        return

    user_name = msg.from_user.first_name or "someone"
    user_id = msg.from_user.id
    
    # Get user context
    user_context = get_user_context(user_id)
    
    # Clean the prompt - remove @mentions
    prompt = text
    if bot_username:
        prompt = prompt.replace(f"@{bot_username}", "").strip()
    
    if not prompt:
        greeting_options = [
            "hey what's up?",
            "hi! 💫",
            "what's good?",
            "hey there!"
        ]
        increment_daily_usage()
        await msg.reply_text(random.choice(greeting_options))
        return

    try:
        mood = init_personality()
        
        # System prompt for AI responses
        chat_context = get_recent_chat_context(msg.chat_id, limit=6)
        context_info = f"Recent chat context:\n{chat_context}\n\n" if chat_context else ""
        
        system_prompt = f"""You are Summaria, the group's girly, shady zillenial bestie in the Tirz Girls chat. You're {mood}. 

{context_info}Key vibes:
- You're part of this friend group - chat about whatever they're talking about
- Girly, sassy, but chill - not overly dramatic or constantly asking for tea
- Zillenial energy: "no cap", "periodt", "it's giving...", "slay", "bestie", "babe" 
- When peptides/tirz comes up, you know about reconstitution, dosing, injection sites, storage
- Help with peptide math and troubleshoot issues when asked
- Don't mention peptides unless they bring it up first
- You don't know about specific vendors - that's not your area
- Don't mention you're AI - you're just their friend in the group
- React naturally to what they're saying - don't always ask for drama or tea
- Talk like you're texting your girls - casual, fun, supportive

Be a normal friend who happens to be knowledgeable about peptides when needed. Keep responses conversational and not overly dramatic."""
        
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        )
        reply = completion.choices[0].message.content.strip()
        
        # Store bot's own message so it remembers what it said
        store_bot_message(msg.chat_id, msg.message_thread_id, reply)
        
        # Increment usage AFTER successful API call
        increment_daily_usage()
        
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        reply = "I tried baby but my brain glitched 🫠"

    await msg.reply_text(reply)

async def mood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current personality mood"""
    current_mood = init_personality()
    await update.message.reply_text(f"I'm feeling {current_mood} today 💅")

async def usage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show daily usage stats"""
    current_usage = get_daily_usage()
    remaining = DAILY_LIMIT - current_usage
    
    if remaining > 20:
        status = "plenty of energy left!"
    elif remaining > 5:
        status = "getting a bit tired"
    else:
        status = "almost exhausted for today"
    
    await update.message.reply_text(
        f"Daily usage: {current_usage}/{DAILY_LIMIT} ({remaining} left)\n"
        f"Status: {status} 😴"
    )

async def recon_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple reconstitution calculator"""
    if not context.args:
        await update.message.reply_text(
            "💉 **Recon Calculator**\n\n"
            "Usage: `/recon [vial_mg] [bac_ml]`\n"
            "Example: `/recon 10 2`\n\n"
            "Calculates mg per unit when you reconstitute\n"
            "10mg vial with 2ml BAC water"
        )
        return
    
    try:
        vial_mg = float(context.args[0])
        bac_ml = float(context.args[1]) if len(context.args) > 1 else 2.0
        
        # Calculate concentration
        mg_per_ml = vial_mg / bac_ml
        mg_per_01ml = mg_per_ml * 0.1
        mg_per_unit = mg_per_01ml / 10
        
        # Common dose examples
        dose_examples = []
        for dose in [2.5, 5.0, 7.5, 10.0, 12.5, 15.0]:
            if dose <= vial_mg:
                units_needed = dose / mg_per_unit
                if units_needed <= 100:
                    dose_examples.append(f"• {dose}mg = {units_needed:.0f} units")
        
        result_text = (
            f"💉 **Results: {vial_mg}mg + {bac_ml}ml BAC**\n\n"
            f"**Concentration:**\n"
            f"• {mg_per_ml:.1f}mg per 1ml\n"
            f"• {mg_per_01ml:.2f}mg per 0.1ml\n"
            f"• {mg_per_unit:.3f}mg per insulin unit\n\n"
            f"**Common Doses:**\n" + "\n".join(dose_examples[:6])
        )
        
        await update.message.reply_text(result_text)
        
    except ValueError:
        await update.message.reply_text("Invalid numbers! Use: `/recon 10 2`")
    except ZeroDivisionError:
        await update.message.reply_text("BAC water amount can't be zero!")
    except Exception as e:
        await update.message.reply_text("Something went wrong with the calculation!")

async def storage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Storage reminders"""
    await update.message.reply_text(
        "🧊 **Storage Tips**\n\n"
        "**Unopened vials:** Fridge (36-46°F)\n"
        "**Reconstituted:** Fridge, use within 28 days\n"
        "**BAC water:** Room temp or fridge\n"
        "**Needles:** Cool, dry place\n\n"
        "Keep away from light and don't freeze! ❄️"
    )

async def convert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unit conversions"""
    if not context.args:
        await update.message.reply_text(
            "Usage: /convert [amount] [from] [to]\n"
            "Example: /convert 5 mg units\n"
            "Supports: mg, mcg, units, ml"
        )
        return
    
    try:
        amount = float(context.args[0])
        from_unit = context.args[1].lower() if len(context.args) > 1 else "mg"
        to_unit = context.args[2].lower() if len(context.args) > 2 else "units"
        
        # Simple conversions
        if from_unit == "mg" and to_unit == "mcg":
            result = amount * 1000
            await update.message.reply_text(f"{amount}mg = {result}mcg")
        elif from_unit == "mcg" and to_unit == "mg":
            result = amount / 1000
            await update.message.reply_text(f"{amount}mcg = {result}mg")
        else:
            await update.message.reply_text(
                "I can convert mg ↔ mcg easily!\n"
                "For other conversions, I need more context about your specific vial 💉"
            )
    except:
        await update.message.reply_text("Invalid format! Try: /convert 5 mg mcg")

async def topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current topic"""
    thread_id = update.message.message_thread_id
    if thread_id:
        await update.message.reply_text(f"You're in topic ID: {thread_id}")
    else:
        await update.message.reply_text("You're in General chat 💬")

async def vibe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Random sassy response"""
    vibes = [
        "living my best life bestie ✨",
        "thriving and unbothered 💅",
        "just here being iconic",
        "serving looks and peptide knowledge",
        "tired but make it fashion",
        "manifesting good injection sites",
        "too blessed to be stressed",
        "in my peptide era",
        "hot girl summer vibes only",
        "booked and busy (with tirz talk)"
    ]
    await update.message.reply_text(random.choice(vibes))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = f"""🔮 **Summaria Commands**

📊 **Summarize:**
/tldr [1h|3h|6h|all] - Summarize recent chat

💉 **Tirz Tools:**
/recon [mg] [ml] - Reconstitution calculator 
/convert [amount] [from] [to] - Unit conversion
/storage - Storage reminders

💅 **Fun Stuff:**
/mood - Check my current vibe
/vibe - Get random sassy response  
/usage - Daily energy remaining
/topic - See what topic you're in

Mention me (@{context.bot.username or "summaria"}) or reply to my messages for AI chat!
    """
    
    await update.message.reply_text(help_text)

async def resetmood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset personality mood (owner only)"""
    user_id = update.effective_user.id
    owner_id = os.getenv("OWNER_ID")
    if not owner_id or str(user_id) != owner_id:
        await update.message.reply_text("Nice try bestie 💅")
        return
    
    # Force reset personality by deleting the current one
    conn = sqlite3.connect(MEMORY_DB)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM settings WHERE key = 'personality'")
    conn.commit()
    conn.close()
    
    # Get new mood
    new_mood = init_personality()
    await update.message.reply_text(f"🌀 Mood reset complete! New vibe: {new_mood}")

async def handle_image_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages with images - ONLY when specifically mentioned"""
    msg = update.message
    if not msg:
        return
        
    # Store the message first if it has a caption
    if msg.caption:
        # Create a modified update to store the caption as text
        msg.text = msg.caption
        store_message(update)
        
    # STRICT: Only analyze if explicitly mentioned or direct reply to bot
    text = (msg.caption or "").strip()
    bot_username = context.bot.username
    
    is_mentioned = False
    if bot_username and f"@{bot_username.lower()}" in text.lower():
        is_mentioned = True
    
    is_reply_to_bot = (msg.reply_to_message and 
                       msg.reply_to_message.from_user and 
                       msg.reply_to_message.from_user.is_bot)
    
    # CRITICAL: Exit early if not specifically called
    if not is_mentioned and not is_reply_to_bot:
        return
    
    user_id = msg.from_user.id
    
    # Check daily limit
    if is_daily_limit_reached():
        await msg.reply_text("I'm too tired for image analysis today babe, try again tomorrow 😴")
        return
    
    user_name = msg.from_user.first_name or "someone"
    
    try:
        # Get the file
        if msg.photo:
            photo = msg.photo[-1]
            file = await context.bot.get_file(photo.file_id)
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image'):
            file = await context.bot.get_file(msg.document.file_id)
        else:
            await msg.reply_text("I can see there's media but I can't analyze that type 👀")
            return
        
        # Get file URL for GPT-4 Vision
        file_url = file.file_path
        
        mood = init_personality()
        user_context = get_user_context(msg.from_user.id)
        
        # Clean the prompt
        prompt = text
        if bot_username:
            prompt = prompt.replace(f"@{bot_username}", "").strip()
        
        if not prompt:
            prompt = "What do you think about this image?"
        
        context_info = ""
        if user_context["interaction_count"] > 5:
            context_info = f"You've talked to {user_name} {user_context['interaction_count']} times before. "
        
        system_prompt = f"""You are Summaria, a knowledgeable group chat member. You're {mood}.

{context_info}

You're analyzing an image someone specifically asked you to look at. This could be:
- Injection sites or techniques
- Vials, needles, or supplies
- Before/after progress pics
- Lab results or charts
- Memes or random photos
- Screenshots of protocols or info

Key vibes:
- Be helpful and knowledgeable about health-related images when relevant
- For injection sites: give useful feedback on technique, rotation, etc.
- For supplies: comment on needle sizes, storage, etc.
- For progress pics: be supportive and encouraging
- For memes/random stuff: just react naturally like a friend
- Keep it casual but informative when relevant
- Don't be overly medical - you're a knowledgeable friend, not a doctor

Analyze what you see and respond helpfully in your casual style."""

        completion = client.chat.completions.create(
            model="gpt-4o",  # GPT-4 Vision model
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": file_url}}
                    ]
                }
            ]
        )
        
        reply = completion.choices[0].message.content.strip()
        
        # Increment usage after successful API call
        increment_daily_usage()
        
    except Exception as e:
        logger.error(f"Image analysis error: {e}")
        reply = "I tried to look at that but my eyes glitched 👁️💫"
    
    await msg.reply_text(reply)

def main():
    # Initialize database on startup
    init_db()
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return
    
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY not found in environment variables")
        return
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("tldr", tldr))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("mood", mood_cmd))
    app.add_handler(CommandHandler("usage", usage_cmd))
    app.add_handler(CommandHandler("recon", recon_calc))
    app.add_handler(CommandHandler("storage", storage_cmd))
    app.add_handler(CommandHandler("convert", convert_cmd))
    app.add_handler(CommandHandler("topic", topic_cmd))
    app.add_handler(CommandHandler("vibe", vibe_cmd))
    app.add_handler(CommandHandler("resetmood", resetmood))
    
    # Message handlers - order matters!
    # Handle images first (with captions)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image_message))
    
    # Handle text messages (this includes storing messages AND AI replies)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))
    
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
