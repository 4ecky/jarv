import asyncio
import os
import time
from datetime import datetime, timezone, timedelta

import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= НАСТРОЙКИ =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

API_URL = "https://api.football-data.org/v4/matches"
HEADERS = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}

ADMIN_ID = 7024518865

# ================= СОСТОЯНИЯ =================

STARTED_CHATS = set()
LIVE_CHATS = set()
DM_CHATS = set()

BOT_MESSAGES = {}
NOTIFIED_MATCHES = set()

CACHE = {
    "live": {},
    "scheduled": [],
    "last_live_update": 0,
    "last_scheduled_update": 0,
}

# ================= ВСПОМОГАТЕЛЬНОЕ =================

async def send_and_store(bot, chat_id, text, reply_markup=None):
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    BOT_MESSAGES.setdefault(chat_id, []).append(msg.message_id)

# ================= API =================

def fetch_live():
    try:
        r = requests.get(API_URL, headers=HEADERS, params={"status": "LIVE"}, timeout=5)
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        print("LIVE API ERROR:", e)
        return []

def fetch_scheduled():
    try:
        r = requests.get(API_URL, headers=HEADERS, params={"status": "SCHEDULED"}, timeout=10)
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        print("SCHEDULED API ERROR:", e)
        return []

# ================= КЛАВИАТУРА =================

def main_menu(chat_id):
    keyboard = [
        ["📩 DM"],
        ["🔴 Сейчас"],
        ["📅 Ближайшие матчи"],
    ]
    if chat_id == ADMIN_ID:
        keyboard.append(["🧪 Test goal"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ================= GOALS =================

async def process_goals(context, live_matches):
    current_ids = set()

    for m in live_matches:
        match_id = m["id"]
        current_ids.add(match_id)

        score = m["score"]["fullTime"]
        hg, ag = score["home"], score["away"]
        minute = m.get("minute")

        last = CACHE["live"].get(match_id, {"hg": hg, "ag": ag})
        for _ in range(hg - last["hg"]):
            await notify_goal(context, m, minute)
        for _ in range(ag - last["ag"]):
            await notify_goal(context, m, minute)

        CACHE["live"][match_id] = {"hg": hg, "ag": ag}

    for old in set(CACHE["live"]) - current_ids:
        del CACHE["live"][old]

async def notify_goal(context, match, minute):
    text = (
        f"⚽ ГООООЛ!\n"
        f"{match['homeTeam']['name']} "
        f"{match['score']['fullTime']['home']} : "
        f"{match['score']['fullTime']['away']} "
        f"{match['awayTeam']['name']}\n"
        f"⏱ {minute} мин"
    )

    for chat in LIVE_CHATS:
        await send_and_store(context.bot, chat, text)

    if minute and (2 <= minute <= 11 or 69 <= minute <= 72):
        for chat in DM_CHATS:
            await send_and_store(context.bot, chat, text)

# ================= BACKGROUND LOOP =================

async def background_loop(app):
    await asyncio.sleep(5)

    while True:
        now = time.time()

        if now - CACHE["last_live_update"] >= 30:
            live = fetch_live()
            await process_goals(app.bot, live)
            CACHE["last_live_update"] = now

        if now - CACHE["last_scheduled_update"] >= 600:
            CACHE["scheduled"] = fetch_scheduled()
            CACHE["last_scheduled_update"] = now

        await asyncio.sleep(5)

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    STARTED_CHATS.add(chat)
    await send_and_store(context.bot, chat, "👋 Бот запущен", main_menu(chat))

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    STARTED_CHATS.discard(chat)
    LIVE_CHATS.discard(chat)
    DM_CHATS.discard(chat)
    await update.message.reply_text("⛔ Остановлен", reply_markup=ReplyKeyboardRemove())

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    text = update.message.text

    if text == "📩 DM":
        DM_CHATS.add(chat)
        LIVE_CHATS.discard(chat)
        await update.message.reply_text("📩 DM включён")

    elif text == "🔴 Сейчас":
        LIVE_CHATS.add(chat)
        matches = fetch_live()
        if not matches:
            await update.message.reply_text("Нет LIVE матчей")
            return

        blocks = [
            f"{m['homeTeam']['name']} — {m['awayTeam']['name']} "
            f"{m['score']['fullTime']['home']}:{m['score']['fullTime']['away']}"
            for m in matches
        ]
        await update.message.reply_text("\n".join(blocks))

# ================= ЗАПУСК =================

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    asyncio.create_task(background_loop(app))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )

if __name__ == "__main__":
    main()


