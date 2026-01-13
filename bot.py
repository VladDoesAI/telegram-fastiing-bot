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
# Message pools
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
    "📸 Story’s up. You showed your face. Respect."
]

STORY_MISS_MESSAGES = [
    "❌ No story today. You had the window. Don’t waste tomorrow."
]

STORY_FAIL_MESSAGES = [
    "⚠️ Couldn’t verify a story today. Handle it manually."
]

# =========================
# DB helpers
# =========================

def db():
    return sqlite3.connect(DB, check_same_thread=False)

def utcnow():
    return datetime.utcnow()

def ensure_tables():
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

    CREATE TABLE IF NOT EXISTS state (
        telegram_user_id INTEGER PRIMARY KEY,
        is_eating INTEGER DEFAULT 0,
        last_meal_time TEXT,
        last_water_time TEXT,
        last_water_reminder_time TEXT
    );
    """)

    for col in ["ig_username", "ig_enabled"]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col}")
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
# Instagram helpers
# =========================

def instagram_username_valid(username):
    try:
        url = f"https://www.instagram.com/{username}/"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def has_active_story(username):
    try:
        url = f"https://www.instagram.com/{username}/"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        text = r.text
        if '"has_public_story":true' in text or '"reel_ids":[' in text:
            return True
        if '"has_public_story":false' in text:
            return False
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

def start_eating(user_id):
    db().execute(
        "UPDATE state SET is_eating=1, last_meal_time=? WHERE telegram_user_id=?",
        (utcnow().isoformat(), user_id)
    ).connection.commit()

def stop_eating(user_id):
    db().execute(
        "UPDATE state SET is_eating=0 WHERE telegram_user_id=?",
        (user_id,)
    ).connection.commit()

def log_water(user_id, amount):
    db().execute(
        "UPDATE state SET last_water_time=?, last_water_reminder_time=NULL WHERE telegram_user_id=?",
        (utcnow().isoformat(), user_id)
    ).connection.commit()

# =========================
# Message handler
# =========================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    text_l = text.lower()

    ensure_user(user_id)

    # ----- Instagram commands -----

    if text_l.startswith("set instagram"):
        username = text.split()[-1].lstrip("@")
        if instagram_username_valid(username):
            db().execute(
                "UPDATE users SET ig_username=? WHERE telegram_user_id=?",
                (username, user_id)
            ).connection.commit()
            await update.message.reply_text(f"📸 Instagram verified: @{username}")
        else:
            await update.message.reply_text("❌ Instagram account not found or not public.")
        return

    if text_l == "instagram on":
        cur = db().cursor()
        cur.execute("SELECT ig_username FROM users WHERE telegram_user_id=?", (user_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            await update.message.reply_text("❌ Set a public Instagram username first.")
        else:
            db().execute(
                "UPDATE users SET ig_enabled=1 WHERE telegram_user_id=?",
                (user_id,)
            ).connection.commit()
            await update.message.reply_text(f"📸 Instagram checks ON for @{row[0]}")
        return

    if text_l == "instagram off":
        db().execute(
            "UPDATE users SET ig_enabled=0 WHERE telegram_user_id=?",
            (user_id,)
        ).connection.commit()
        await update.message.reply_text("📸 Instagram checks OFF.")
        return

    # ----- Status -----

    if text_l == "status":
        cur = db().cursor()
        cur.execute("""
            SELECT s.is_eating, s.last_meal_time, u.ig_enabled, u.ig_username
            FROM state s JOIN users u ON s.telegram_user_id = u.telegram_user_id
            WHERE s.telegram_user_id=?
        """, (user_id,))
        is_eating, last_meal, ig_enabled, ig_username = cur.fetchone()

        msg = []
        msg.append("🍽️ Eating" if is_eating else "⏳ Fasting")

        if last_meal:
            delta = utcnow() - datetime.fromisoformat(last_meal)
            msg.append(f"⏳ Fasted: {delta.seconds//3600}h {(delta.seconds%3600)//60}m")

        msg.append(
            f"📸 Instagram: {'ON' if ig_enabled else 'OFF'}"
            + (f" (@{ig_username})" if ig_username else "")
        )

        await update.message.reply_text("\n".join(msg))
        return

    # ----- Core actions -----

    if "water" in text_l:
        amount = int(re.findall(r"\d+", text)[0]) if re.findall(r"\d+", text) else 250
        log_water(user_id, amount)
        await update.message.reply_text(f"💧 Logged {amount} ml.")
        return

    if "start" in text_l and "eat" in text_l:
        start_eating(user_id)
        await update.message.reply_text("🍽️ Eating window started.")
        return

    if "stop" in text_l or "done" in text_l:
        stop_eating(user_id)
        await update.message.reply_text("⏳ Fasting started.")
        return

# =========================
# Reminder engine
# =========================

async def reminder_tick(context):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT u.telegram_user_id, u.timezone, u.eating_start, u.eating_end,
               u.ig_enabled, u.ig_username,
               s.is_eating, s.last_water_time, s.last_water_reminder_time, s.last_meal_time
        FROM users u JOIN state s ON u.telegram_user_id = s.telegram_user_id
    """)
    rows = cur.fetchall()
    con.close()

    for uid, tz, es, ee, ig_enabled, ig_username, is_eating, last_water, last_reminder, last_meal in rows:
        tzinfo = pytz.timezone(tz)
        now = utcnow().replace(tzinfo=pytz.utc).astimezone(tzinfo)

        # Water reminders (stateful)
        if last_water:
            since = utcnow() - datetime.fromisoformat(last_water)
            if since > timedelta(minutes=90):
                if not last_reminder or utcnow() - datetime.fromisoformat(last_reminder) > timedelta(minutes=90):
                    await context.bot.send_message(uid, random.choice(WATER_REMINDERS))
                    db().execute(
                        "UPDATE state SET last_water_reminder_time=? WHERE telegram_user_id=?",
                        (utcnow().isoformat(), uid)
                    ).connection.commit()

        # Eating window close + Instagram
        end = datetime.combine(now.date(), parse_hhmm(ee), tzinfo)
        if abs((now - end).total_seconds()) < 60:
            await context.bot.send_message(uid, random.choice(EATING_CLOSED_REMINDERS))
            if ig_enabled and ig_username:
                result = await has_active_story_with_retry(ig_username)
                if result is True:
                    await context.bot.send_message(uid, random.choice(STORY_OK_MESSAGES))
                elif result is False:
                    await context.bot.send_message(uid, random.choice(STORY_MISS_MESSAGES))
                else:
                    await context.bot.send_message(uid, random.choice(STORY_FAIL_MESSAGES))

# =========================
# Boot
# =========================

if __name__ == "__main__":
    ensure_tables()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminder_tick, "interval", minutes=1)
    scheduler.start()

    app.run_polling()
