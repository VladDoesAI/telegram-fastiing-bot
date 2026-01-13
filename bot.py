import os
import re
import sqlite3
import random
from datetime import datetime, timedelta, time
import pytz

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DB = "tracker.db"
TOKEN = os.environ["BOT_TOKEN"]

# ---------- Attitude message pools ----------

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

DAILY_SUMMARY_MESSAGES = [
    "📊 Day complete. You handled your business.",
    "📊 Solid discipline today. Keep that standard.",
    "📊 You showed up today. Respect."
]

# ---------- DB helpers ----------

def db():
    return sqlite3.connect(DB, check_same_thread=False)

def utcnow():
    return datetime.utcnow()

def ensure_tables():
    con = db()
    cur = con.cursor()
    cur.executescript(open("schema.sql").read())
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

# ---------- Helpers ----------

def parse_hhmm(val):
    h, m = val.split(":")
    return time(int(h), int(m))

# ---------- Core actions ----------

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
        "UPDATE state SET last_water_time=? WHERE telegram_user_id=?",
        (utcnow().isoformat(), user_id)
    )
    con.commit()
    con.close()

# ---------- Message handler ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.lower()
    ensure_user(user_id)

    if "water" in text:
        amount = int(re.findall(r"\d+", text)[0]) if re.findall(r"\d+", text) else 250
        log_water(user_id, amount)
        await update.message.reply_text(f"💧 Logged {amount} ml.")
        return
