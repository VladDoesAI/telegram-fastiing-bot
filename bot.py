DeFi Vlad, [Jan 13, 2026 at 1:55:56 PM]:
import os
import re
import sqlite3
from datetime import datetime, timedelta, time
import pytz

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DB = "tracker.db"
TOKEN = os.environ["BOT_TOKEN"]

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
        cur.execute(
            "INSERT INTO state (telegram_user_id) VALUES (?)",
            (user_id,)
        )
        con.commit()
    con.close()

# ---------- Time helpers ----------

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

    if "set timezone" in text:
        tz = text.split()[-1]
        pytz.timezone(tz)  # validate
        db().execute(
            "UPDATE users SET timezone=? WHERE telegram_user_id=?",
            (tz, user_id)
        ).connection.commit()
        await update.message.reply_text(f"🕒 Timezone set to {tz}.")
        return

    if "set eating window" in text:
        window = re.findall(r"\d{2}:\d{2}", text)
        if len(window) == 2:
            db().execute(
                "UPDATE users SET eating_start=?, eating_end=? WHERE telegram_user_id=?",
                (*window, user_id)
            ).connection.commit()
            await update.message.reply_text(
                f"🍽️ Eating window set to {window[0]}–{window[1]}."
            )
        return

    if "set water goal" in text:
        goal = int(re.findall(r"\d+", text)[0])
        db().execute(
            "UPDATE users SET water_goal_ml=? WHERE telegram_user_id=?",
            (goal, user_id)
        ).connection.commit()
        await update.message.reply_text(f"💧 Water goal set to {goal} ml.")
        return

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


DeFi Vlad, [Jan 13, 2026 at 1:55:56 PM]:
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

    for r in rows:
        uid, tz, es, ee, is_eating, last_water, last_meal = r
        tzinfo = pytz.timezone(tz)
        now = utcnow().replace(tzinfo=pytz.utc).astimezone(tzinfo)

        # 💧 Water reminder
        if not last_water or utcnow() - datetime.fromisoformat(last_water) > timedelta(minutes=90):
            await context.bot.send_message(uid, "💧 Time to drink some water.")

        # ⏳ Fasting reminder
        if not is_eating and last_meal:
            delta = utcnow() - datetime.fromisoformat(last_meal)
            if delta.seconds % (6 * 3600) < 60:
                await context.bot.send_message(
                    uid,
                    f"⏳ You’ve been fasting for {delta.seconds//3600} hours."
                )

        # 🍽️ Eating window reminders
        start = datetime.combine(now.date(), parse_hhmm(es), tzinfo)
        end = datetime.combine(now.date(), parse_hhmm(ee), tzinfo)

        if abs((now - start).total_seconds()) < 60:
            await context.bot.send_message(uid, "🍽️ Eating window is now open.")

        if abs((now - (end - timedelta(minutes=30))).total_seconds()) < 60:
            await context.bot.send_message(uid, "⚠️ Eating window closes in 30 minutes.")

        if abs((now - end).total_seconds()) < 60:
            await context.bot.send_message(uid, "⏳ Eating window closed. Fasting begins.")

# ---------- Daily summary ----------

async def daily_summary(context):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT telegram_user_id, timezone, water_goal_ml FROM users")
    users = cur.fetchall()

    for uid, tz, goal in users:
        tzinfo = pytz.timezone(tz)
        today = utcnow().replace(tzinfo=pytz.utc).astimezone(tzinfo).date()
        start = datetime.combine(today, time.min, tzinfo).astimezone(pytz.utc)
        end = datetime.combine(today, time.max, tzinfo).astimezone(pytz.utc)

        cur.execute("""
            SELECT type, amount_ml FROM events
            WHERE telegram_user_id=? AND timestamp BETWEEN ? AND ?
        """, (uid, start.isoformat(), end.isoformat()))

        rows = cur.fetchall()
        water = sum(r[1] or 0 for r in rows if r[0] == "WATER")

        await context.bot.send_message(
            uid,
            f"📊 Daily Summary\n\n"
            f"💧 Water: {water}/{goal} ml\n"
            f"✅ Keep it up!"
        )

    con.close()

# ---------- Boot ----------

if name == "main":
    ensure_tables()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminder_tick, "interval", minutes=1, args=[app.bot])
    scheduler.add_job(daily_summary, "cron", hour=21, args=[app.bot])
    scheduler.start()

    app.run_polling()
