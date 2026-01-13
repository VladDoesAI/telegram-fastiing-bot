import os
import re
import sqlite3
import random
import asyncio
import requests
from datetime import datetime, timedelta, time
import pytz

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DB = "tracker.db"
TOKEN = os.environ["BOT_TOKEN"]

# =========================
# Attitude message pools
# =========================

WATER_REMINDERS = [
    "💧 Drink some water. I pity the fool who ignores hydration.",
    "💧 Hydrate. Your body ain’t a cactus.",
    "💧 Water. Now. Don’t make me ask twice.",
    "💧 Dry body = weak body. Drink up.",
    "💧 You thirsty or just lazy? Drink water.",
    "💧 Hydration check. Handle it."
]

FASTING_REMINDERS = [
    "⏳ You’re still fasting. Stay sharp.",
    "⏳ Fasting continues. Discipline beats cravings.",
    "⏳ Clock’s still running. Don’t fold now.",
    "⏳ You chose this fast. Own it.",
    "⏳ Hunger is loud. Discipline is louder."
]

EATING_OPEN_REMINDERS = [
    "🍽️ Eating window is open. Eat with purpose.",
    "🍽️ You’re clear to eat. Don’t waste it.",
    "🍽️ Window’s open. Fuel up—no nonsense.",
    "🍽️ You earned this meal. Keep it clean."
]

EATING_CLOSE_SOON_REMINDERS = [
    "⚠️ 30 minutes left. Finish strong.",
    "⚠️ Clock’s ticking. Wrap it up.",
    "⚠️ Last call. Make it count.",
    "⚠️ You’ve got 30 minutes. No excuses."
]

EATING_CLOSED_REMINDERS = [
    "⛔ Window closed. Fasting starts now.",
    "⛔ That’s it. Kitchen’s closed.",
    "⛔ Eating time’s over. Discipline time.",
    "⛔ No more food. Stay sharp."
]

STORY_OK_MESSAGES = [
    "📸 Story’s up. You showed your face. Respect.",
    "📸 You posted a story. Accountability handled.",
    "📸 Story detected. You did the work."
]

STORY_MISS_MESSAGES = [
    "❌ No story today. You had the window. Don’t waste tomorrow.",
    "❌ No story up. Discipline slipped. Fix it.",
    "❌ You stayed silent today. That’s on you."
]

STORY_FAIL_MESSAGES = [
    "⚠️ Couldn’t verify a story today. Handle it manually.",
    "⚠️ Instagram didn’t cooperate. No excuses tomorrow."
]

# =========================
# DB helpers
# =========================

def db():
    return sqlite3.connect(DB, check_same_thread=False)

def utcnow():
    return datetime.utcnow()

def ensure_tables_and_columns():
    con = db()
    cur = con.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_user_id INTEGER PRIMARY KEY,
        timezone TEXT DEFAULT 'UTC',
        eating_start TEXT DEFAULT '12:00',
        eating_end TEXT DEFAULT '20:00',
        water_goal_ml INTEGER DEFAULT 3000,
        ig_username TEXT,
        ig_enabled INTEGER DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER,
        type TEXT,
        amount_ml INTEGER,
        timestamp TEXT
    );

    CREATE TABLE IF NOT EXISTS state (
        telegram_user_id INTEGER PRIMARY KEY,
        is_eating INTEGER DEFAULT 0,
        last_meal_time TEXT,
        last_water_time TEXT,
        last_water_reminder_time TEXT
    );
    """)

    # Defensive migrations
    for col in ["ig_username", "ig_enabled"]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except Exception:
            pass

    try:
        cur.execute("ALTER TABLE state ADD COLUMN last_water_reminder_time TEXT")
    except Exception:
        pass

    con.commit()
    con.close()

def ensure_user(user_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM users WHERE telegram_user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (telegram_user_id, created_at) VALUES (?, ?)",
            (user_id, utcnow().isoformat())
        )
        cur.execute("INSERT INTO state (telegram_user_id) VALUES (?)", (user_id,))
        con.commit()
    con.close()

# =========================
# Instagram logic
# =========================

def has_active_story(username):
    try:
        url = f"https://www.instagram.com/{username}/"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None

        text = r.text
        if '"has_public_story":true' in text:
            return True
        if '"has_public_story":false' in text:
            return False
        if '"reel_ids":[' in text:
            return True

        return False
    except Exception:
        return None

async def has_active_story_with_retry(username):
    first = has_active_story(username)
    if first is not None:
        return first
    await asyncio.sleep(5)
    return has_active_story(username)

# =========================
# Helpers
# =========================

def parse_hhmm(val):
    h, m = val.split(":")
    return time(int(h), int(m))

# =========================
# Core actions
# =========================

def log_event(user_id, type_, amount=None):
    con = db()
    con.execute(
        "INSERT INTO events (telegram_user_id, type, amount_ml, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, type_, amount, utcnow().isoformat())
    )
    con.commit()
    con.close()

def start_eating(user_id):
    log_event(user_id, "EAT_START")
    con = db()
    con.execute(
        "UPDATE state SET is_eating=1, last_meal_time=? WHERE telegram_user_id=?",
        (utcnow().isoformat(), user_id)
    )
    con.commit()
    con.close()

def stop_eating(user_id):
    log_event(user_id, "EAT_STOP")
    con = db()
    con.execute(
        "UPDATE state SET is_eating=0 WHERE telegram_user_id=?",
        (user_id,)
    )
    con.commit()
    con.close()

def log_water(user_id, amount):
    log_event(user_id, "WATER", amount)
    con = db()
    con.execute(
        "UPDATE state SET last_water_time=?, last_water_reminder_time=NULL WHERE telegram_user_id=?",
        (utcnow().isoformat(), user_id)
    )
    con.commit()
    con.close()

# =========================
# Message handler
# =========================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.lower()
    ensure_user(user_id)

    if text.startswith("set instagram"):
        username = text.split()[-1].lstrip("@")
        db().execute(
            "UPDATE users SET ig_username=? WHERE telegram_user_id=?",
            (username, user_id)
        ).connection.commit()
        await update.message.reply_text(f"📸 Instagram set to @{username}.")
        return

    if text == "instagram on":
        db().execute(
            "UPDATE users SET ig_enabled=1 WHERE telegram_user_id=?",
            (user_id,)
        ).connection.commit()
        await update.message.reply_text("📸 Instagram checks ON.")
        return

    if text == "instagram off":
        db().execute(
            "UPDATE users SET ig_enabled=0 WHERE telegram_user_id=?",
            (user_id,)
        ).connection.commit()
        await update.message.reply_text("📸 Instagram checks OFF.")
        return

    if text == "instagram status":
        cur = db().cursor()
        cur.execute(
            "SELECT ig_enabled, ig_username FROM users WHERE telegram_user_id=?",
            (user_id,)
        )
        enabled, username = cur.fetchone()
        await update.message.reply_text(
            f"📸 Instagram: {'ON' if enabled else 'OFF'}\n"
            f"Username: @{username if username else 'not set'}"
        )
        return

    if "water" in text:
        amount = int(r
