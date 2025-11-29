# bot_sofascore.py
import requests
import time
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIG ---
BOT_TOKEN = "8573437607:AAHoJis3QwZlbNgi5ttPIpFAWS5E-hYVaRw"
POLL_INTERVAL = 10  # segundos entre consultas (ajusta si quieres menos llamado)

# Estructura:
# user_matches[chat_id] = {
#   match_id: {
#       last_score: (h,a),
#       last_cards: {...},
#       last_possession: {"home":None,"away":None},
#       lineups_sent: False,
#       last_events: set(),
#       ht_stats_sent: False,
#       ft_stats_sent: False,
#       last_status: None
#   }, ...
# }
user_matches = {}

# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola! Envíame uno o varios IDs de partido de Sofascore separados por espacios.\n\n"
        "Ejemplo:\n`1234567 7654321`\n\n"
        "Monitorearé:\n"
        "⚽ Goles\n"
        "🟨🟥 Tarjetas\n"
        "📊 Posesión\n"
        "🧩 Alineaciones iniciales\n"
        "⏱️ Eventos minuto a minuto\n"
        "📊 Estadísticas al descanso (HT) y al final (FT)\n",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envíame IDs (números) separados por espacios. Para dejar de monitorear un chat, escribe /stop"
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in user_matches:
        user_matches.pop(chat_id)
        await update.message.reply_text("✔ Dejé de monitorear tus partidos.")
    else:
        await update.message.reply_text("No estaba monitoreando nada para este chat.")

async def set_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    raw = update.message.text.strip().split()

    # si el usuario escribe /start o /help, ya están manejados
    if not raw:
        await update.message.reply_text("Envíame uno o varios IDs (números) separados por espacios.")
        return

    # validar que todos sean numéricos
    if not all(x.isdigit() for x in raw):
        await update.message.reply_text("❌ Por favor envía solo IDs numéricos separados por espacios.")
        return

    # registrar
    user_matches[chat_id] = {}
    for match_id in raw:
        user_matches[chat_id][match_id] = {
            "last_score": None,
            "last_cards": {"home_red": 0, "home_yellow": 0, "away_red": 0, "away_yellow": 0},
            "last_possession": {"home": None, "away": None},
            "lineups_sent": False,
            "last_events": set(),
            "ht_stats_sent": False,
            "ft_stats_sent": False,
            "last_status": None
        }

    await update.message.reply_text("✔ Monitoreando estos partidos:\n" + " ".join(raw))

# --------- Funciones para obtener datos de Sofascore ----------
HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch_event_data(match_id):
    """
    Devuelve un dict con keys:
      event, lineups, incidents
    O None en caso de error.
    """
    try:
        event_resp = requests.get(f"https://api.sofascore.com/api/v1/event/{match_id}", headers=HEADERS, timeout=10)
        event_resp.raise_for_status()
        event = event_resp.json().get("event", None)
    except Exception:
        return None

    # lineups
    try:
        lineups_resp = requests.get(f"https://api.sofascore.com/api/v1/event/{match_id}/lineups", headers=HEADERS, timeout=10)
        lineups = lineups_resp.json()
    except Exception:
        lineups = None

    # incidents / minute-to-minute
    try:
        incidents_resp = requests.get(f"https://api.sofascore.com/api/v1/event/{match_id}/incidents", headers=HEADERS, timeout=10)
        incidents = incidents_resp.json()
    except Exception:
        incidents = None

    return {"event": event, "lineups": lineups, "incidents": incidents}

def format_lineups(lineups):
    if not lineups or "home" not in lineups or "away" not in lineups:
        return "Alineaciones no disponibles."

    try:
        home_team = lineups["home"]["team"]["name"]
        away_team = lineups["away"]["team"]["name"]
        # players list puede variar — intentamos coger nombre si existe
        def players_text(side):
            players = []
            for p in lineups[side].get("players", []):
                name = p.get("player", {}).get("name") or p.get("name") or "Sin nombre"
                position = p.get("position", "") or ""
                players.append(f"- {name} {position}".strip())
            return "\n".join(players) if players else "No disponible"
        return f"🧩 *Alineaciones Iniciales*\n\n*{home_team}*\n{players_text('home')}\n\n*{away_team}*\n{players_text('away')}"
    except Exception:
        return "Alineaciones no disponibles."

def format_full_stats(event):
    stats = event.get("statistics", [])
    if not stats:
        return "Estadísticas no disponibles."

    txt = "📊 *Estadísticas completas*\n\n"
    for group in stats:
        group_name = group.get("period", "General")
        txt += f"*{group_name.title()}*\n"
        for item in group.get("statisticsItems", []):
            name = item.get("name", "")
            home = item.get("home", "-")
            away = item.get("away", "-")
            txt += f"{name}: {home} - {away}\n"
        txt += "\n"
    return txt

def parse_incidents(incidents):
    """
    Retorna lista de tuples (unique_id, text)
    incidents puede ser { "incidents": [...] } o lista directa; manejamos ambos.
    """
    parsed = []
    if not incidents:
        return parsed

    items = incidents.get("incidents") if isinstance(incidents, dict) and "incidents" in incidents else incidents
    if not items:
        return parsed

    for item in items:
        try:
            iid = str(item.get("id", item.get("uniqueId", "")) or "")
            minute = item.get("time", {}).get("minute", "?")
            itype = item.get("incidentType", item.get("type", ""))
            player = item.get("player", {}).get("name") or item.get("name") or ""
            txt = None

            if itype == "goal":
                txt = f"⚽ {minute}' - Gol de {player}"
            elif itype == "yellowCard":
                txt = f"🟨 {minute}' - Amarilla para {player}"
            elif itype == "redCard":
                txt = f"🟥 {minute}' - ROJA para {player}"
            elif itype == "substitution":
                off = item.get("playerOut", {}).get("name", "")
                on = item.get("playerIn", {}).get("name", "")
                txt = f"🔄 {minute}' - Cambio: {off} ➜ {on}"
            elif itype == "penalty":
                txt = f"⚠️ {minute}' - Penal"
            elif itype == "varDecision":
                txt = f"📺 {minute}' - VAR: {item.get('description','')}"
            elif itype == "injury":
                txt = f"🤕 {minute}' - Lesión de {player}"
            else:
                # otros tipos: corner, offside, shot... los incluimos genéricos si tienen player o description
                desc = item.get("description") or item.get("detail") or ""
                if player or desc:
                    txt = f"{minute}' - {itype} {player} {desc}".strip()

            if txt:
                parsed.append((iid, txt))
        except Exception:
            continue

    return parsed

# ---------------- Monitor principal ----------------
async def monitor(app):
    await asyncio.sleep(1)  # pequeña espera inicial
    while True:
        try:
            # recorrer copia para evitar runtime changes
            for chat_id, matches in list(user_matches.items()):
                for match_id, info in list(matches.items()):
                    data = fetch_event_data(match_id)
                    if not data or not data.get("event"):
                        continue

                    event = data["event"]
                    home = event.get("homeTeam", {}).get("shortName") or event.get("homeTeam", {}).get("name", "Local")
                    away = event.get("awayTeam", {}).get("shortName") or event.get("awayTeam", {}).get("name", "Visitante")

                    # SCORE
                    hs = event.get("homeScore", {}).get("current")
                    as_ = event.get("awayScore", {}).get("current")
                    score = (hs, as_)

                    # CARDS
                    cards = {
                        "home_yellow": event.get("homeYellowCards", 0),
                        "home_red": event.get("homeRedCards", 0),
                        "away_yellow": event.get("awayYellowCards", 0),
                        "away_red": event.get("awayRedCards", 0),
                    }

                    # POSSESSION
                    possession_home = possession_away = None
                    for group in event.get("statistics", []):
                        for item in group.get("statisticsItems", []):
                            if item.get("name") == "Ball possession":
                                try:
                                    possession_home = int(item.get("home") or 0)
                                    possession_away = int(item.get("away") or 0)
                                except Exception:
                                    possession_home = None
                                    possession_away = None

                    possession = {"home": possession_home, "away": possession_away}

                    # STATUS
                    status_type = event.get("status", {}).get("type", "")  # e.g., "inprogress", "break", "ended"

                    # ---- ALINEACIONES ----
                    if not info["lineups_sent"] and data.get("lineups"):
                        txt = format_lineups(data["lineups"])
                        try:
                            await app.bot.send_message(chat_id, txt, parse_mode="Markdown")
                        except Exception:
                            await app.bot.send_message(chat_id, "🧩 Alineaciones disponibles (no formateadas).")
                        info["lineups_sent"] = True

                    # ---- GOLES ----
                    if info["last_score"] and score != info["last_score"]:
                        hs_display = hs if hs is not None else "-"
                        as_display = as_ if as_ is not None else "-"
                        await app.bot.send_message(chat_id, f"⚽ ¡GOL!\n{home} {hs_display} - {as_display} {away}")

                    info["last_score"] = score

                    # ---- TARJETAS ----
                    last_cards = info["last_cards"]
                    alerts = []
                    if cards["home_yellow"] > last_cards["home_yellow"]:
                        alerts.append(f"🟨 Amarilla para *{home}*")
                    if cards["away_yellow"] > last_cards["away_yellow"]:
                        alerts.append(f"🟨 Amarilla para *{away}*")
                    if cards["home_red"] > last_cards["home_red"]:
                        alerts.append(f"🟥 ROJA para *{home}*")
                    if cards["away_red"] > last_cards["away_red"]:
                        alerts.append(f"🟥 ROJA para *{away}*")
                    if alerts:
                        await app.bot.send_message(chat_id, f"{home} vs {away}\n" + "\n".join(alerts), parse_mode="Markdown")
                    info["last_cards"] = cards

                    # ---- POSESIÓN ----
                    lp = info["last_possession"]
                    if lp["home"] is not None and possession["home"] is not None:
                        if abs(possession["home"] - lp["home"]) >= 3:
                            await app.bot.send_message(chat_id,
                                f"📊 Cambio en posesión:\n{home}: {possession['home']}%\n{away}: {possession['away']}%")
                    info["last_possession"] = possession

                    # ---- EVENTOS MINUTO A MINUTO ----
                    incidents = data.get("incidents")
                    parsed = parse_incidents(incidents)
                    for event_id, event_text in parsed:
                        if event_id and event_id not in info["last_events"]:
                            await app.bot.send_message(chat_id, f"⏱ {event_text}")
                            info["last_events"].add(event_id)

                    # ---- ESTADÍSTICAS AL DESCANSO Y FINAL ----
                    # Usamos 'event' para obtener status y statistics
                    # status_type: "break" para halftime, "ended"/"finished" para final (según Sofa)
                    if status_type == "break" and not info["ht_stats_sent"]:
                        stats_text = format_full_stats(event)
                        await app.bot.send_message(chat_id, f"⏱️ *Descanso* — {home} vs {away}\n\n" + stats_text, parse_mode="Markdown")
                        info["ht_stats_sent"] = True

                    if status_type in ("ended", "finished") and not info["ft_stats_sent"]:
                        stats_text = format_full_stats(event)
                        await app.bot.send_message(chat_id, f"🏁 *Final del Partido* — {home} vs {away}\n\n" + stats_text, parse_mode="Markdown")
                        info["ft_stats_sent"] = True

                    info["last_status"] = status_type

            # esperar antes de la siguiente ronda
            await asyncio.sleep(POLL_INTERVAL)
        except Exception as e:
            # en caso de error global, imprimimos y esperamos un poco antes de continuar
            print("Error en monitor:", e)
            await asyncio.sleep(5)

# ---------------- Setup y ejecución ----------------
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop))
    # cualquier mensaje de texto se interpreta como IDs de partidos
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), set_matches))

    # lanzar monitor en background
    asyncio.create_task(monitor(app))

    # iniciar polling (se queda escuchando mensajes)
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

