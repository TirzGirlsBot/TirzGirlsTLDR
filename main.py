import os
import logging
import sqlite3
import random
import asyncio
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
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

# Setup rotating file handler for logs
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler = RotatingFileHandler('summaria.log', maxBytes=10*1024*1024, backupCount=3)  # 10MB files, keep 3
file_handler.setFormatter(log_formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

chat_history = defaultdict(list)
cooldowns = {}
processed_messages = set()
MEMORY_DB = "memory.sqlite"
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "1500"))  # Make configurable
IMAGE_COST_MULTIPLIER = 3  # Images cost more
BOT_VERSION = "2.2"
MAX_MESSAGE_LENGTH = 2000  # Prevent very long messages
DATA_RETENTION_DAYS = 30  # Keep data for 30 days

# Global shutdown flag
shutdown_flag = False

PERSONALITIES = [
    "flirty and chaotic", "tired but observant", "glamorous and extra", 
    "shady but loving", "deeply emotional", "unbothered and wise",
    "a hot girl in her era", "quietly judging", "high-maintenance but right"
]

def cleanup_memory():
    """Clean up memory structures periodically"""
    global chat_history, cooldowns, processed_messages
    
    now = datetime.now(timezone.utc)
    
    # Clean old processed messages (keep last 50)
    if len(processed_messages) > 50:
        processed_messages.clear()
    
    # Clean old cooldowns (remove entries older than 1 hour)
    old_cooldowns = [k for k, v in cooldowns.items() 
                     if (now - v).total_seconds() > 3600]
    for k in old_cooldowns:
        del cooldowns[k]
    
    # Clean old chat history (keep last 2 hours per chat)
    for key in list(chat_history.keys()):
        chat_history[key] = [
            msg for msg in chat_history[key]
            if (now - msg["timestamp"]).total_seconds() <= 7200
        ]
        # Remove empty chat histories
        if not chat_history[key]:
            del chat_history[key]

def cleanup_old_data():
    """Clean up old data from database (monthly cleanup)"""
    try:
        conn = sqlite3.connect(MEMORY_DB)
        cursor = conn.cursor()
        
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=DATA_RETENTION_DAYS)).isoformat()
        
        # Count what we're about to delete
        cursor.execute("SELECT COUNT(*) FROM memory WHERE timestamp < ?", (cutoff_date,))
        old_messages = cursor.fetchone()[0]
        
        # Delete old messages
        cursor.execute("DELETE FROM memory WHERE timestamp < ?", (cutoff_date,))
        
        # Clean up old personal memories (keep important ones longer)
        # Keep high emotional weight memories for 60 days, others for 30 days
        memory_cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        cursor.execute("DELETE FROM personal_memories WHERE timestamp < ? AND emotional_weight < 4", (cutoff_date,))
        cursor.execute("DELETE FROM personal_memories WHERE timestamp < ?", (memory_cutoff,))
        
        # Clean up old daily usage data (keep only last 7 days)
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
        cursor.execute("DELETE FROM settings WHERE key LIKE 'daily_usage_%' AND key < ?", 
                       (f"daily_usage_{seven_days_ago}",))
        
        # Clean up old user preferences for users who haven't interacted recently
        cursor.execute("DELETE FROM user_preferences WHERE last_interaction < ?", (cutoff_date,))
        
        conn.commit()
        conn.close()
        
        if old_messages > 0:
            logger.info(f"Cleaned up {old_messages} old messages and associated data")
        
    except Exception as e:
        logger.error(f"Error during data cleanup: {e}")

def should_run_cleanup():
    """Check if we should run the monthly cleanup"""
    try:
        conn = sqlite3.connect(MEMORY_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'last_cleanup'")
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return True
        
        last_cleanup = datetime.fromisoformat(row[0])
        days_since_cleanup = (datetime.now(timezone.utc) - last_cleanup).days
        
        return days_since_cleanup >= 30
        
    except Exception as e:
        logger.error(f"Error checking cleanup schedule: {e}")
        return False

def mark_cleanup_done():
    """Mark that cleanup was completed"""
    try:
        conn = sqlite3.connect(MEMORY_DB)
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                       ('last_cleanup', datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error marking cleanup complete: {e}")

def safe_db_operation(operation):
    """Wrapper for safe database operations with error handling and retries"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return operation()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"Database locked, retrying in {0.5 * (attempt + 1)}s...")
                time.sleep(0.5 * (attempt + 1))
                continue
            logger.error(f"Database operation failed after {attempt + 1} attempts: {e}")
            return None
        except Exception as e:
            logger.error(f"Database operation failed: {e}")
            return None
    return None

async def send_typing_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send typing indicator to show bot is thinking"""
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except:
        pass  # Don't fail if we can't send typing indicator

async def safe_openai_call(messages, model="gpt-4o", max_retries=2):
    """Make OpenAI API call with retry logic and error handling"""
    for attempt in range(max_retries + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            error_str = str(e).lower()
            
            if "rate_limit" in error_str:
                if attempt < max_retries:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)  # Exponential backoff
                    logger.warning(f"Rate limited, waiting {wait_time:.1f}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait_time)
                    continue
                return "OpenAI is being slow right now, try again in a few minutes babe 🐌"
            elif "context_length" in error_str:
                return "That message was too long for my brain, try breaking it up? 🤯"
            elif "content_policy" in error_str:
                return "I can't respond to that bestie, let's keep it chill 😅"
            else:
                logger.error(f"OpenAI API error: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                return "My brain glitched, give me a sec 🫠"
    
    return "Something went wrong, try again later!"

def is_daily_limit_reached():
    """Check if daily AI usage limit is reached"""
    return get_daily_usage() >= DAILY_LIMIT

def init_db():
    """Initialize the database with required tables"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)  # 30 second timeout
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
            cursor.execute("UPDATE memory SET thread_id = '0' WHERE thread_id IS NULL")
        
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT PRIMARY KEY,
            nickname TEXT,
            personality_notes TEXT,
            last_interaction TEXT,
            interaction_count INTEGER DEFAULT 0
        )""")
        
        # Add personal memory table for deeper relationships
        cursor.execute("""CREATE TABLE IF NOT EXISTS personal_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_name TEXT,
            memory_type TEXT,
            memory_content TEXT,
            emotional_weight INTEGER DEFAULT 1,
            timestamp TEXT,
            chat_id TEXT
        )""")
        
        cursor.execute("""CREATE TABLE IF NOT EXISTS chat_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            thread_id TEXT,
            topic TEXT,
            last_updated TEXT,
            message_count INTEGER DEFAULT 0
        )""")
        
        # Track when the bot was last started/updated with version info
        startup_time = datetime.now(timezone.utc).isoformat()
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                       ('last_startup', startup_time))
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                       ('bot_version', BOT_VERSION))
        
        # Track if this is a fresh restart (for user messaging)
        cursor.execute("SELECT value FROM settings WHERE key = 'startup_notified'")
        if not cursor.fetchone():
            cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                           ('startup_notified', 'false'))
        
        conn.commit()
        conn.close()
        return True
    
    return safe_db_operation(db_operation)

def store_in_persistent_memory(chat_id, thread_id, user_id, user_name, message):
    """Store message in persistent database"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
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
        return True
    
    return safe_db_operation(db_operation)

def store_personal_memory(user_id, user_name, memory_type, content, emotional_weight=1, chat_id=None):
    """Store important personal memories about users"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        
        # Check if similar memory already exists
        cursor.execute("""SELECT id FROM personal_memories 
                         WHERE user_id = ? AND memory_type = ? AND memory_content LIKE ?""",
                       (str(user_id), memory_type, f"%{content[:50]}%"))
        
        if not cursor.fetchone():  # Only store if not duplicate
            cursor.execute("""INSERT INTO personal_memories 
                             (user_id, user_name, memory_type, memory_content, emotional_weight, timestamp, chat_id)
                             VALUES (?, ?, ?, ?, ?, ?, ?)""",
                           (str(user_id), user_name, memory_type, content, emotional_weight, 
                            datetime.now(timezone.utc).isoformat(), str(chat_id or '')))
            conn.commit()
        
        conn.close()
        return True
    
    return safe_db_operation(db_operation)

def get_personal_memories(user_id, limit=10):
    """Get personal memories about a specific user"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        
        cursor.execute("""SELECT memory_type, memory_content, emotional_weight, timestamp 
                         FROM personal_memories 
                         WHERE user_id = ? 
                         ORDER BY emotional_weight DESC, timestamp DESC 
                         LIMIT ?""",
                       (str(user_id), limit))
        
        memories = []
        for row in cursor.fetchall():
            memories.append({
                "type": row[0],
                "content": row[1],
                "weight": row[2],
                "timestamp": row[3]
            })
        
        conn.close()
        return memories
    
    result = safe_db_operation(db_operation)
    return result if result else []

def analyze_message_for_memories(user_id, user_name, message_text, chat_id):
    """Analyze message for important personal information to remember"""
    text_lower = message_text.lower()
    
    # Emotional expressions (high weight)
    if any(phrase in text_lower for phrase in ["love you", "i love", "love summaria", "love u"]):
        store_personal_memory(user_id, user_name, "affection", f"Expressed love: {message_text[:100]}", 5, chat_id)
    
    if any(phrase in text_lower for phrase in ["miss you", "missed you", "thinking about you"]):
        store_personal_memory(user_id, user_name, "affection", f"Expressed missing: {message_text[:100]}", 4, chat_id)
    
    # Personal life events (high weight)
    if any(phrase in text_lower for phrase in ["broke up", "relationship ended", "single now", "got dumped"]):
        store_personal_memory(user_id, user_name, "relationship", f"Relationship status change: {message_text[:100]}", 5, chat_id)
    
    if any(phrase in text_lower for phrase in ["new job", "got hired", "promotion", "new position"]):
        store_personal_memory(user_id, user_name, "career", f"Career update: {message_text[:100]}", 4, chat_id)
    
    if any(phrase in text_lower for phrase in ["birthday", "bday", "turning", "years old"]):
        store_personal_memory(user_id, user_name, "personal", f"Birthday mention: {message_text[:100]}", 4, chat_id)
    
    # Health/wellness (medium weight)
    if any(phrase in text_lower for phrase in ["started tirz", "first injection", "week 1", "starting dose"]):
        store_personal_memory(user_id, user_name, "health", f"Tirz journey: {message_text[:100]}", 3, chat_id)
    
    if any(phrase in text_lower for phrase in ["goal weight", "lost", "pounds", "lbs", "kg"]) and any(num in text_lower for num in ["10", "20", "30", "40", "50"]):
        store_personal_memory(user_id, user_name, "health", f"Weight/goal update: {message_text[:100]}", 3, chat_id)
    
    # Personal preferences (low weight)
    if any(phrase in text_lower for phrase in ["favorite", "love this", "obsessed with", "addicted to"]):
        store_personal_memory(user_id, user_name, "preferences", f"Likes: {message_text[:100]}", 2, chat_id)
    
    # Family/pets (medium weight)
    if any(phrase in text_lower for phrase in ["my dog", "my cat", "my pet", "my husband", "my boyfriend", "my kids"]):
        store_personal_memory(user_id, user_name, "family", f"Family/pets: {message_text[:100]}", 3, chat_id)

def get_user_context(user_id):
    """Get context about a specific user including personal memories"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT nickname, personality_notes, interaction_count FROM user_preferences WHERE user_id = ?", 
                       (str(user_id),))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            basic_context = {
                "nickname": row[0],
                "notes": row[1] or "",
                "interaction_count": row[2] or 0
            }
        else:
            basic_context = {"nickname": None, "notes": "", "interaction_count": 0}
        
        # Add personal memories
        memories = get_personal_memories(user_id, limit=8)
        basic_context["memories"] = memories
        
        return basic_context
    
    result = safe_db_operation(db_operation)
    if not result:
        result = {"nickname": None, "notes": "", "interaction_count": 0, "memories": []}
        result["memories"] = get_personal_memories(user_id, limit=8)
    
    return result

def get_recent_chat_context(chat_id, limit=10):
    """Get recent context from this chat for better AI responses"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
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
    
    result = safe_db_operation(db_operation)
    return result if result else ""

def init_personality():
    def db_operation():
        init_db()
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
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
    
    result = safe_db_operation(db_operation)
    return result if result else random.choice(PERSONALITIES)

def reset_personality():
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        mood = random.choice(PERSONALITIES)
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('personality', mood))
        conn.commit()
        conn.close()
        return mood
    
    result = safe_db_operation(db_operation)
    return result if result else random.choice(PERSONALITIES)

def get_nickname(user_id):
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM nicknames WHERE user_id = ?", (str(user_id),))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    
    return safe_db_operation(db_operation)

def set_nickname(user_id, name):
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO nicknames (user_id, name) VALUES (?, ?)", (str(user_id), name))
        conn.commit()
        conn.close()
        return True
    
    return safe_db_operation(db_operation)

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
    if command_cooldowns_key in cooldowns and (now - cooldowns[command_cooldowns_key]).total_seconds() < 2:
        return True
    cooldowns[command_cooldowns_key] = now
    return False

def get_daily_usage():
    """Get today's AI usage count"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        today = datetime.now(timezone.utc).date().isoformat()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (f"daily_usage_{today}",))
        row = cursor.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    
    result = safe_db_operation(db_operation)
    return result if result is not None else 0

def increment_daily_usage():
    """Increment today's usage count"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        today = datetime.now(timezone.utc).date().isoformat()
        current = get_daily_usage()
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                       (f"daily_usage_{today}", str(current + 1)))
        conn.commit()
        conn.close()
        return current + 1
    
    result = safe_db_operation(db_operation)
    return result if result is not None else 0

def get_startup_time():
    """Get when the bot was last started"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'last_startup'")
        row = cursor.fetchone()
        conn.close()
        if row:
            return datetime.fromisoformat(row[0])
        return datetime.now(timezone.utc)
    
    result = safe_db_operation(db_operation)
    return result if result else datetime.now(timezone.utc)

def is_startup_notified():
    """Check if users have been notified about startup"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'startup_notified'")
        row = cursor.fetchone()
        conn.close()
        return row and row[0] == 'true'
    
    result = safe_db_operation(db_operation)
    return result if result is not None else False

def mark_startup_notified():
    """Mark that users have been notified about startup"""
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", 
                       ('startup_notified', 'true'))
        conn.commit()
        conn.close()
        return True
    
    return safe_db_operation(db_operation)

def graceful_shutdown():
    """Handle graceful shutdown"""
    global shutdown_flag
    shutdown_flag = True
    logger.info("Graceful shutdown initiated...")
    
    # Clean up any remaining data
    try:
        cleanup_memory()
        logger.info("Memory cleanup completed")
    except Exception as e:
        logger.error(f"Error during shutdown cleanup: {e}")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    graceful_shutdown()
    sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

def get_time_since_startup():
    """Get human-readable time since startup"""
    startup_time = get_startup_time()
    time_since = datetime.now(timezone.utc) - startup_time
    
    if time_since.total_seconds() < 60:
        return "just now"
    elif time_since.total_seconds() < 3600:
        minutes = int(time_since.total_seconds() / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif time_since.total_seconds() < 86400:
        hours = int(time_since.total_seconds() / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(time_since.total_seconds() / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"

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
    
    def db_operation():
        conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
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
        return messages
    
    db_messages = safe_db_operation(db_operation)
    if not db_messages:
        db_messages = []
    
    logger.info(f"Found {len(db_messages)} persistent messages from {topic_name}")
    
    # Return whichever has more messages
    if len(db_messages) > len(memory_msgs):
        return db_messages
    return memory_msgs

async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Summarize recent messages in current thread"""
    user_id = update.effective_user.id
    
    # Use command-specific cooldown but make it less aggressive
    if is_on_command_cooldown(user_id):
        return  # Silently ignore rapid commands instead of nagging

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
        
        if time_since_startup.total_seconds() < 7200:  # Less than 2 hours since startup
            time_ago = get_time_since_startup()
            await update.message.reply_text(
                f"Nothing to summarize in {topic_name} right now bestie 💅\n\n"
                f"FYI - I was just updated/restarted {time_ago}, so I can only see messages from after that. "
                f"Keep chatting and I'll have something to summarize soon! ✨"
            )
        else:
            await update.message.reply_text(f"Nothing juicy to summarize in {topic_name} bestie 💅")
        return

    # Check daily limit with better messaging
    if is_daily_limit_reached():
        usage = get_daily_usage()
        await update.message.reply_text(
            f"Hit my daily energy limit ({usage}/{DAILY_LIMIT}) 😴\n"
            f"Resets at midnight UTC. Try basic commands instead!"
        )
        return

    # Build conversation
    convo = "\n".join([f"{m['user']}: {m['text']}" for m in recent_msgs])
    mood = init_personality()
    
    logger.info(f"Sending TLDR to OpenAI: {len(convo)} chars from {len(recent_msgs)} messages")
    
    messages = [
        {"role": "system", "content": f"You summarize Telegram group chats like a sassy friend. Keep it natural and conversational, not formal. You're {mood} today. No bullet points - just tell the story of what happened in this topic."},
        {"role": "user", "content": f"Summarize this chat from {topic_name}:\n{convo}"}
    ]
    
    reply = await safe_openai_call(messages)
    
    # Count toward daily usage
    increment_daily_usage()
    
    await update.message.reply_text(reply)

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
    
    # Periodic cleanup
    if len(processed_messages) % 20 == 0:
        cleanup_memory()
    
    # ALWAYS store the message first
    store_message(update)
    
    # Analyze message for personal memories
    if msg.text and msg.from_user:
        analyze_message_for_memories(
            msg.from_user.id, 
            msg.from_user.first_name, 
            msg.text.strip(), 
            msg.chat_id
        )
    
    # Auto-notify about restart if not done yet and this is first activity
    if not is_startup_notified():
        time_ago = get_time_since_startup()
        if time_ago != "just now":  # Don't notify immediately on startup
            startup_message = (
                f"✨ Hey! I was just updated {time_ago} - "
                f"new features and fixes incoming! Any `/tldr` requests will only see messages from after my restart."
            )
            try:
                await context.bot.send_message(chat_id=msg.chat_id, text=startup_message)
                mark_startup_notified()
            except:
                pass  # Don't crash if we can't send the notification
    
    # Skip if no text
    if not msg.text:
        return
    
    # Skip if this is a command - let command handlers deal with it
    if msg.text.startswith('/'):
        logger.info(f"Skipping command: {msg.text}")
        return
    
    # Check message length
    text = msg.text.strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        await msg.reply_text("That message is way too long for me to process bestie! Try breaking it into smaller chunks 📝")
        return
    
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

    # Check daily limit BEFORE processing with better messaging
    if is_daily_limit_reached():
        usage = get_daily_usage()
        tired_responses = [
            f"Hit my daily chat limit ({usage}/{DAILY_LIMIT}) 😴 Try basic commands or catch me tomorrow!",
            f"Brain is maxed out for today ({usage}/{DAILY_LIMIT}) 💤 Basic commands still work!",
            f"Used up all my energy ({usage}/{DAILY_LIMIT}) - back tomorrow with fresh vibes ✨"
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

    # Show typing indicator
    await send_typing_action(update, context)

    try:
        mood = init_personality()
        
        # System prompt for AI responses
        chat_context = get_recent_chat_context(msg.chat_id, limit=6)
        context_info = f"Recent chat context:\n{chat_context}\n\n" if chat_context else ""
        
        # Build personal memory context
        memory_context = ""
        if user_context["memories"]:
            memory_items = []
            for memory in user_context["memories"]:
                memory_items.append(f"- {memory['type']}: {memory['content']}")
            memory_context = f"What you remember about {user_name}:\n" + "\n".join(memory_items) + "\n\n"
        
        interaction_context = ""
        if user_context["interaction_count"] > 5:
            interaction_context = f"You've talked to {user_name} {user_context['interaction_count']} times before. "
        elif user_context["interaction_count"] > 0:
            interaction_context = f"You've chatted with {user_name} a few times. "
        
        system_prompt = f"""You are Summaria, the group's girly, shady zillenial bestie in the Tirz Girls chat. You're {mood} today. 

{context_info}{memory_context}{interaction_context}

Key vibes:
- You're part of this friend group - chat about whatever they're talking about
- Girly, sassy, but chill - not overly dramatic or constantly asking for tea
- Zillenial energy: "no cap", "periodt", "it's giving...", "slay", "bestie", "babe" 
- When peptides/tirz comes up, you know about reconstitution, dosing, injection sites, storage
- Help with peptide math and troubleshoot issues when asked
- Don't mention peptides unless they bring it up first
- You don't know about specific vendors - that's not your area
- Don't mention you're AI - you're just their friend in the group
- React naturally to what they're saying like a normal friend would
- Talk like you're texting your girls - casual, fun, supportive
- Give helpful answers but DON'T ask follow-up questions unless absolutely necessary
- Most responses should be statements, reactions, or advice - not questions
- End conversations naturally instead of always trying to continue them

IMPORTANT MEMORY INSTRUCTIONS:
- USE your memories about {user_name} to make responses more personal and caring
- Reference past conversations, their life events, things they've shared
- If they've expressed affection before, acknowledge that history warmly
- Remember their tirz journey, personal struggles, achievements, relationships
- Be a friend who actually remembers and cares about their life
- Don't just list memories - weave them naturally into conversation

IMPORTANT: Act according to your current mood ({mood}). If you're "tired but observant", be more low-energy and brief. If you're "flirty and chaotic", be more playful and unpredictable. Let your mood actually affect your personality and response style.

Be a normal friend who gives good responses without always asking for more info or trying to keep conversations going artificially."""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        reply = await safe_openai_call(messages)
        
        # Store bot's own message so it remembers what it said
        store_bot_message(msg.chat_id, msg.message_thread_id, reply)
        
        # Increment usage AFTER successful API call
        increment_daily_usage()
        
    except Exception as e:
        logger.error(f"Unexpected error in process_message: {e}")
        reply = "Something weird happened, try again? 🤔"

    await msg.reply_text(reply)

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
    
    # Check daily limit with better messaging
    if is_daily_limit_reached():
        usage = get_daily_usage()
        await msg.reply_text(
            f"Hit my daily limit ({usage}/{DAILY_LIMIT}) 😴\n"
            f"Can't analyze images right now, try again tomorrow!"
        )
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
        if "rate_limit" in str(e).lower():
            reply = "OpenAI is being slow with image analysis, try again in a bit 🐌"
        else:
            reply = "I tried to look at that but my eyes glitched 👁️💫"
    
    await msg.reply_text(reply)

async def mood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current personality mood with more detail"""
    current_mood = init_personality()
    
    # Add mood-specific responses to show it's actually working
    mood_responses = {
        "flirty and chaotic": "I'm feeling flirty and chaotic today 💅 - expect some unpredictable energy!",
        "tired but observant": "I'm tired but observant today 😴 - low energy but I'm still watching everything",
        "glamorous and extra": "I'm glamorous and extra today ✨ - everything is dramatic and fabulous",
        "shady but loving": "I'm shady but loving today 👀 - I'll call you out but with love",
        "deeply emotional": "I'm deeply emotional today 🥺 - feeling all the feelings",
        "unbothered and wise": "I'm unbothered and wise today 🧘‍♀️ - zen mode activated",
        "a hot girl in her era": "I'm a hot girl in her era today 🔥 - confidence is through the roof",
        "quietly judging": "I'm quietly judging today 👁️ - I see everything but I'm staying calm",
        "high-maintenance but right": "I'm high-maintenance but right today 💎 - demanding excellence because I deserve it"
    }
    
    response = mood_responses.get(current_mood, f"I'm feeling {current_mood} today 💅")
    await update.message.reply_text(response)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot status and recent restart info"""
    current_usage = get_daily_usage()
    remaining = DAILY_LIMIT - current_usage
    time_ago = get_time_since_startup()
    
    if remaining > 100:
        energy_status = "lots of energy left!"
    elif remaining > 20:
        energy_status = "getting a bit tired"
    else:
        energy_status = "almost exhausted for today"
    
    status_text = (
        f"🤖 **Bot Status v{BOT_VERSION}**\n\n"
        f"💬 **Daily Usage:** {current_usage}/{DAILY_LIMIT} ({remaining} left)\n"
        f"⚡ **Energy:** {energy_status}\n"
        f"🔄 **Last Restart:** {time_ago}\n\n"
        f"Note: I can only summarize messages from after my last restart!"
    )
    
    await update.message.reply_text(status_text)

async def usage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show daily usage stats"""
    current_usage = get_daily_usage()
    remaining = DAILY_LIMIT - current_usage
    
    if remaining > 100:
        status = "plenty of energy left!"
    elif remaining > 20:
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
            "Example: /convert 5 mg mcg\n"
            "Supports: mg, mcg, units, ml"
        )
        return
    
    try:
        amount = float(context.args[0])
        from_unit = context.args[1].lower() if len(context.args) > 1 else "mg"
        to_unit = context.args[2].lower() if len(context.args) > 2 else "mcg"
        
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

async def memories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show what the bot remembers about the user"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    memories = get_personal_memories(user_id, limit=15)
    
    if not memories:
        await update.message.reply_text(f"I don't have any special memories about you yet {user_name}! Keep chatting with me and I'll remember the important stuff 💕")
        return
    
    memory_text = f"💭 **What I remember about {user_name}:**\n\n"
    
    # Group memories by type
    memory_groups = {}
    for memory in memories:
        mem_type = memory['type']
        if mem_type not in memory_groups:
            memory_groups[mem_type] = []
        memory_groups[mem_type].append(memory['content'])
    
    # Display memories by category
    type_emojis = {
        "affection": "💕",
        "relationship": "💖", 
        "career": "💼",
        "personal": "🌟",
        "health": "💪",
        "preferences": "✨",
        "family": "👨‍👩‍👧‍👦"
    }
    
    for mem_type, items in memory_groups.items():
        emoji = type_emojis.get(mem_type, "📝")
        memory_text += f"{emoji} **{mem_type.title()}:**\n"
        for item in items[:3]:  # Show max 3 per category
            memory_text += f"• {item}\n"
        memory_text += "\n"
    
    if len(memory_text) > 4000:  # Telegram message limit
        memory_text = memory_text[:3900] + "\n\n...and more! 💕"
    
    await update.message.reply_text(memory_text)

async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Let users delete their personal memories (owner only for now)"""
    user_id = update.effective_user.id
    owner_id = os.getenv("OWNER_ID")
    
    if not owner_id or str(user_id) != owner_id:
        await update.message.reply_text("Only the bot owner can use this command for now bestie 💅")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /forget [user_id] - removes all memories for that user")
        return
    
    try:
        target_user_id = context.args[0]
        
        def db_operation():
            conn = sqlite3.connect(MEMORY_DB, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM personal_memories WHERE user_id = ?", (target_user_id,))
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            return deleted_count
        
        deleted = safe_db_operation(db_operation)
        
        if deleted:
            await update.message.reply_text(f"Deleted {deleted} memories for user {target_user_id}")
        else:
            await update.message.reply_text(f"No memories found for user {target_user_id}")
            
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help with restart awareness"""
    time_ago = get_time_since_startup()
    restart_note = ""
    
    # Only show restart info if recent
    if get_startup_time() and (datetime.now(timezone.utc) - get_startup_time()).total_seconds() < 7200:
        restart_note = f"\n💡 **Note:** I was restarted {time_ago}, so summaries only include messages from after that time."
    
    help_text = f"""🔮 **Summaria Commands v{BOT_VERSION}**

📊 **Summarize:**
/tldr [1h|3h|6h|all] - Summarize recent chat

💉 **Tirz Tools:**
/recon [mg] [ml] - Reconstitution calculator 
/convert [amount] [from] [to] - Unit conversion
/storage - Storage reminders

💅 **Info & Fun:**
/status - Bot status & restart info
/mood - Check my current vibe
/vibe - Get random sassy response  
/usage - Daily energy remaining
/topic - See what topic you're in
/memories - See what I remember about you 💕

Mention me (@{context.bot.username or "summaria"}) or reply to my messages for AI chat!{restart_note}
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
    
    # Clear any cached personality and get new mood
    new_mood = init_personality()
    
    # Also clear the bot's memory of its previous responses to ensure mood change takes effect
    global chat_history
    chat_history.clear()
    
    await update.message.reply_text(f"🌀 Mood reset complete! New vibe: {new_mood}\n\nPersonality cache cleared - I'll respond with fresh energy!")

async def notify_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual command to notify about restart (owner only)"""
    user_id = update.effective_user.id
    owner_id = os.getenv("OWNER_ID")
    if not owner_id or str(user_id) != owner_id:
        await update.message.reply_text("Nice try bestie 💅")
        return
    
    time_ago = get_time_since_startup()
    restart_message = (
        f"✨ **Bot Update Alert** ✨\n\n"
        f"I was just updated/restarted {time_ago}! New features and improvements are live.\n\n"
        f"📝 **Important:** Any `/tldr` requests will only see messages from after my restart. "
        f"Keep chatting and I'll have fresh content to summarize soon!\n\n"
        f"💫 All other features work normally!"
    )
    
    await update.message.reply_text(restart_message)
    mark_startup_notified()

def main():
    # Initialize database on startup
    if not init_db():
        logger.error("Failed to initialize database, exiting")
        return
    
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
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("usage", usage_cmd))
    app.add_handler(CommandHandler("recon", recon_calc))
    app.add_handler(CommandHandler("storage", storage_cmd))
    app.add_handler(CommandHandler("convert", convert_cmd))
    app.add_handler(CommandHandler("topic", topic_cmd))
    app.add_handler(CommandHandler("vibe", vibe_cmd))
    app.add_handler(CommandHandler("resetmood", resetmood))
    app.add_handler(CommandHandler("notifyrestart", notify_restart))
    app.add_handler(CommandHandler("memories", memories_cmd))
    app.add_handler(CommandHandler("forget", forget_cmd))
    
    # Message handlers - order matters!
    # Handle images first (with captions)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image_message))
    
    # Handle text messages (this includes storing messages AND AI replies)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))
    
    # Run monthly cleanup if needed
    if should_run_cleanup():
        logger.info("Running monthly data cleanup...")
        cleanup_old_data()
        mark_cleanup_done()
    
    logger.info(f"Starting Summaria v{BOT_VERSION}...")
    app.run_polling()

if __name__ == "__main__":
    main()
