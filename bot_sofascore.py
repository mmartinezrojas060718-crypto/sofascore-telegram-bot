
import os
import requests
import time
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("7877215240:AAHJaRYZDLWaneRniFEvVDpNgLmglthSewQ")
POLL_INTERVAL = 10

user_matches = {}

HEADERS = {"User-Agent": "Mozilla/5.0"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot activo en Render. Envíame IDs de partidos separados por espacios."
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in user_matches:
        user_matches.pop(chat_id)
        await update.message.reply_text("Monitoreo detenido.")
    else:
        await update.message.reply_text("No había monitoreo activo.")

async def set_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    ids = update.message.text.strip().split()
    if not all(x.isdigit() for x in ids):
        await update.message.reply_text("Envíe solo IDs numéricos.")
        return
    user_matches[chat_id] = {mid: {"last_score": None} for mid in ids}
    await update.message.reply_text(f"Monitoreando: {' '.join(ids)}")

def fetch_event(match_id):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/event/{match_id}", headers=HEADERS, timeout=10)
        return r.json().get("event")
    except:
        return None

async def monitor(app):
    await asyncio.sleep(2)
    while True:
        try:
            for chat_id, matches in list(user_matches.items()):
                for match_id, info in list(matches.items()):
                    event = fetch_event(match_id)
                    if not event:
                        continue
                    hs = event.get("homeScore", {}).get("current")
                    as_ = event.get("awayScore", {}).get("current")
                    score = (hs, as_)
                    if info["last_score"] and score != info["last_score"]:
                        h = event.get("homeTeam", {}).get("name")
                        a = event.get("awayTeam", {}).get("name")
                        await app.bot.send_message(chat_id, f"GOL: {h} {hs} - {as_} {a}")
                    info["last_score"] = score
            await asyncio.sleep(POLL_INTERVAL)
        except Exception as e:
            print("Monitor error:", e)
            await asyncio.sleep(5)

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, set_matches))
    asyncio.create_task(monitor(app))
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
