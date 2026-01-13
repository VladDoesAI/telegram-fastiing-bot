import os
import re
import sqlite3
import random
import requests
from datetime import datetime, timedelta, time
import pytz

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DB = "tracker.db"
TOKEN = os.environ["BOT_TOKEN"]
IG_USERNAME = os.environ.get("IG_USERNAME")

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

# ---------- Instagram story check ----------

def has_active_story(username):
    """
    Best-effort check for whether a public IG profile has an active story.
    Returns:
        True  -> story exists
        False -> no story
        None  -> could not determine
    """
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

        # Known indicators of active story ring
        if '"has_public_story":true' in text:
            return True

        if '"has_public_story":false' in text:
            return False

        # Fallback heuristic (sometimes used)
        if '"reel_ids":[' in text:
            return True

        return False

    except Exception:
        return None

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

    if "start" in text and "eat" in text:
        start_eating(user_id)
        await update.message.reply_text("🍽️ Eating window started.")
        return

    if "stop" in text or "done" in text:
        stop_eating(user_id)
        await update.message.reply_text("⏳ Fasting started.")
        return

    if "status" in text:
        con = db()
        cur = con.cursor()
        cur.execute(
            "SELECT is_eating, last_meal_time FROM state WHERE telegram_user_id=?",
            (user_id,)
        )
        is_eating, last_meal = cur.fetchone()
        con.close()

        if is_eating:
            await update.message.reply_text("🍽️ You are currently eating.")
        elif last_meal:
            delta = utcnow() - datetime.fromisoformat(last_meal)
            await update.message.reply_text(
                f"⏳ Fasting for {delta.seconds//3600}h {(delta.seconds%3600)//60}m."
            )
        return

# ---------- Reminder engine ----------

async def reminder_tick(context):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT u.telegram_user_id, u.timezone, u.eating_start, u.eating_end,
               s.is_eating, s.last_water_time, s.last_meal_time
        FROM users u JOIN state s ON u.telegram_user_id = s.telegram_user_id
    """)
    rows = cur.fetchall()
    con.close()

    for uid, tz, es, ee, is_eating, last_water, last_meal in rows:
        tzinfo = pytz.timezone(tz)
        now = utcnow().replace(tzinfo=pytz.utc).astimezone(tzinfo)

        # 💧 Water
        if not last_water or utcnow() - datetime.fromisoformat(last_water) > timedelta(minutes=90):
            await context.bot.send_message(uid, random.choice(WATER_REMINDERS))

        # ⏳ Fasting
        if not is_eating and last_meal:
            delta = utcnow() - datetime.fromisoformat(last_meal)
            if delta.seconds % (6 * 3600) < 60:
                await context.bot.send_message(uid, random.choice(FASTING_REMINDERS))

        # 🍽️ Eating window
        start = datetime.combine(now.date(), parse_hhmm(es), tzinfo)
        end = datetime.combine(now.date(), parse_hhmm(ee), tzinfo)

        if abs((now - start).total_seconds()) < 60:
            await context.bot.send_message(uid, random.choice(EATING_OPEN_REMINDERS))

        if abs((now - (end - timedelta(minutes=30))).total_seconds()) < 60:
            await context.bot.send_message(uid, random.choice(EATING_CLOSE_SOON_REMINDERS))

        if abs((now - end).total_seconds()) < 60:
            await context.bot.send_message(uid, random.choice(EATING_CLOSED_REMINDERS))

            # 🔍 Instagram story accountability
            if IG_USERNAME:
                result = has_active_story(IG_USERNAME)
                if result is True:
                    await context.bot.send_message(uid, random.choice(STORY_OK_MESSAGES))
                elif result is False:
                    await context.bot.send_message(uid, random.choice(STORY_MISS_MESSAGES))
                else:
                    await context.bot.send_message(uid, random.choice(STORY_FAIL_MESSAGES))

# ---------- Boot ----------

if __name__ == "__main__":
    ensure_tables()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminder_tick, "interval", minutes=1, args=[app.bot])
    scheduler.start()

    app.run_polling()
