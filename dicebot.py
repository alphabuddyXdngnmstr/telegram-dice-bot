import os
import random
import re
import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# Erlaubt z.B. 1d6, 2d20, 3d8+4, 1d10-1
DICE_PATTERN = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$", re.IGNORECASE)

# Oracle Conversation States
ORACLE_QUESTION, ORACLE_ODDS, ORACLE_CHAOS = range(3)

# Deutsche Odds Auswahl
ODDS_OPTIONS = [
    ("Unmöglich", "impossible"),
    ("Keine Chance", "no_way"),
    ("Sehr unwahrscheinlich", "very_unlikely"),
    ("Unwahrscheinlich", "unlikely"),
    ("50/50", "fifty_fifty"),
    ("Eher wahrscheinlich", "somewhat_likely"),
    ("Wahrscheinlich", "likely"),
    ("Sehr wahrscheinlich", "very_likely"),
    ("Fast sicher", "near_sure"),
    ("Sicher", "a_sure_thing"),
    ("Muss so sein", "has_to_be"),
]

# Basis Wahrscheinlichkeiten in Prozent
# Das ist eine saubere, formelbasierte Variante, die sich wie Mythic anfühlt
# Chaos Rang verschiebt das Ganze danach nach oben oder unten
BASE_CHANCE = {
    "impossible": 1,
    "no_way": 5,
    "very_unlikely": 15,
    "unlikely": 25,
    "fifty_fifty": 50,
    "somewhat_likely": 65,
    "likely": 75,
    "very_likely": 85,
    "near_sure": 90,
    "a_sure_thing": 95,
    "has_to_be": 99,
}

# Kleine eigene Random Event Tabellen, frei formuliert
EVENT_FOCUS = [
    "Fernere Begegnung",
    "Umgebungsereignis",
    "NSC Aktion",
    "NSC negativ",
    "NSC positiv",
    "Faden bewegt sich",
    "Neuer Faden",
    "Hinweis oder Zeichen",
]

ACTION_WORDS = [
    "Enthüllen", "Verbergen", "Warnen", "Vereinen", "Zerbrechen", "Locken",
    "Verfolgen", "Täuschen", "Retten", "Opfern", "Entkommen", "Erinnern",
    "Wachsen", "Verhandeln", "Entfachen", "Erstarren",
]

SUBJECT_WORDS = [
    "Schlüssel", "Tor", "Pfad", "Schatten", "Spiegel", "Schwur", "Krone",
    "Echo", "Nebel", "Feuer", "Fluss", "Ruine", "Fremder", "Tier", "Grenze",
    "Blut",
]

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def oracle_outcome(odds_key: str, chaos_rank: int) -> dict:
    """
    Gibt Ergebnis, Roll, Chance und ob Random Event getriggert wurde zurück.
    Chaos Rank 5 ist neutral, darüber wird es ja lastiger, darunter nein lastiger.
    """
    base = BASE_CHANCE[odds_key]
    adjust = (chaos_rank - 5) * 5
    chance = clamp(base + adjust, 0, 100)

    roll = random.randint(1, 100)

    # Exceptional Yes ist unteres Fünftel des Erfolgsbereichs
    ex_yes = 0 if chance == 0 else max(1, chance // 5)

    # Exceptional No ist oberes Fünftel des Fehlschlagbereichs
    fail_size = 100 - chance
    ex_no_size = 0 if fail_size == 0 else int(math.ceil(fail_size / 5))
    ex_no_start = 101 if ex_no_size == 0 else 101 - ex_no_size

    if chance > 0 and roll <= ex_yes:
        result = "Außergewöhnlich Ja"
    elif roll <= chance:
        result = "Ja"
    elif chance < 100 and roll >= ex_no_start:
        result = "Außergewöhnlich Nein"
    else:
        result = "Nein"

    # Random Event Trigger
    # Doppelzahl und Roll ist kleiner gleich Chaos Rang
    doubles = (roll % 11 == 0)
    random_event = bool(doubles and roll <= chaos_rank)

    return {
        "roll": roll,
        "chance": chance,
        "ex_yes": ex_yes,
        "ex_no_start": ex_no_start,
        "result": result,
        "random_event": random_event,
    }

def build_odds_keyboard():
    rows = []
    row = []
    for label, key in ODDS_OPTIONS:
        row.append(InlineKeyboardButton(label, callback_data=f"oracle_odds:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def build_chaos_keyboard():
    rows = []
    row = []
    for n in range(1, 10):
        row.append(InlineKeyboardButton(str(n), callback_data=f"oracle_chaos:{n}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Beispiel:\n/roll 1d6\n/roll 2d20+3")
        return

    expr = context.args[0].lower().strip()
    match = DICE_PATTERN.match(expr)

    if not match:
        await update.message.reply_text("Ungültiges Format.\nNutze z.B. /roll 1d6 oder /roll 2d6")
        return

    dice_count = int(match.group(1))
    sides = int(match.group(2))
    modifier = int(match.group(3)) if match.group(3) else 0

    if dice_count < 1 or dice_count > 100:
        await update.message.reply_text("Maximal 100 Würfel auf einmal.")
        return

    if sides < 2 or sides > 100000:
        await update.message.reply_text("Seitenzahl bitte zwischen 2 und 100000.")
        return

    rolls = [random.randint(1, sides) for _ in range(dice_count)]
    total = sum(rolls) + modifier

    rolls_text = ", ".join(map(str, rolls))
    mod_text = f"{modifier:+}" if modifier else ""

    await update.message.reply_text(
        f"🎲 {dice_count}d{sides}{mod_text}\n"
        f"Würfe: {rolls_text}\n"
        f"Summe: {total}"
    )

async def rolloracle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("oracle_question", None)
    context.user_data.pop("oracle_odds", None)
    context.user_data.pop("oracle_chaos", None)

    if context.args:
        context.user_data["oracle_question"] = " ".join(context.args).strip()
        await update.message.reply_text(
            "🔮 Wie sind die Chancen?",
            reply_markup=build_odds_keyboard()
        )
        return ORACLE_ODDS

    await update.message.reply_text("🔮 Was ist deine Ja Nein Frage? Schreib sie als Antwort 🙂")
    return ORACLE_QUESTION

async def rolloracle_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    context.user_data["oracle_question"] = text if text else "Ohne konkrete Frage"
    await update.message.reply_text(
        "Wie sind die Chancen?",
        reply_markup=build_odds_keyboard()
    )
    return ORACLE_ODDS

async def rolloracle_pick_odds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":", 1)[1]
    context.user_data["oracle_odds"] = data

    await query.edit_message_text(
        "Chaos Rang auswählen, 1 bis 9",
        reply_markup=build_chaos_keyboard()
    )
    return ORACLE_CHAOS

async def rolloracle_pick_chaos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chaos = int(query.data.split(":", 1)[1])
    context.user_data["oracle_chaos"] = chaos

    odds_key = context.user_data.get("oracle_odds", "fifty_fifty")
    question = context.user_data.get("oracle_question", "Ohne konkrete Frage")

    result = oracle_outcome(odds_key, chaos)

    # Labels für Ausgabe
    odds_label = next((lbl for lbl, key in ODDS_OPTIONS if key == odds_key), odds_key)

    msg = (
        f"🔮 Orakelwurf\n"
        f"Frage: {question}\n"
        f"Chancen: {odds_label}\n"
        f"Chaos Rang: {chaos}\n\n"
        f"d100: {result['roll']}\n"
        f"Ergebnis: {result['result']}"
    )

    if result["random_event"]:
        focus = random.choice(EVENT_FOCUS)
        w1 = random.choice(ACTION_WORDS)
        w2 = random.choice(SUBJECT_WORDS)
        msg += (
            f"\n\n✨ Zufallsereignis ausgelöst\n"
            f"Fokus: {focus}\n"
            f"Bedeutung: {w1}, {w2}"
        )

    await query.edit_message_text(msg)
    return ConversationHandler.END

async def rolloracle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Orakel abgebrochen 🙂")
    return ConversationHandler.END

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    base_url = os.environ.get("BASE_URL")
    port = int(os.environ.get("PORT", "10000"))

    if not token or not base_url:
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder BASE_URL fehlt")

    base_url = base_url.rstrip("/")

    app = Application.builder().token(token).build()

    # Standard Würfel
    app.add_handler(CommandHandler("roll", roll))

    # Orakel Conversation
    oracle_conv = ConversationHandler(
        entry_points=[CommandHandler("rolloracle", rolloracle_start)],
        states={
            ORACLE_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, rolloracle_question)],
            ORACLE_ODDS: [CallbackQueryHandler(rolloracle_pick_odds, pattern=r"^oracle_odds:")],
            ORACLE_CHAOS: [CallbackQueryHandler(rolloracle_pick_chaos, pattern=r"^oracle_chaos:")],
        },
        fallbacks=[CommandHandler("cancel", rolloracle_cancel)],
        allow_reentry=True,
    )
    app.add_handler(oracle_conv)

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=f"{base_url}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
