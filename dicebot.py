import os
import random
import re
import math
import html
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

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

# -----------------------
# DICE ROLL SYSTEM
# -----------------------

_ROLL_ALLOWED = re.compile(r"^[0-9dDwW+\-\s]+$")
_ROLL_TERM = re.compile(r"([+\-]?)(\d+[dw]\d+|\d+)", re.IGNORECASE)
_ROLL_DICE = re.compile(r"^(\d+)[dw](\d+)$", re.IGNORECASE)

def parse_roll_expression(expr: str) -> Tuple[str, int, List[str]]:
    raw = (expr or "").strip()
    if not raw:
        raise ValueError("Leerer Ausdruck")

    if not _ROLL_ALLOWED.match(raw):
        raise ValueError("Ungültige Zeichen im Ausdruck")

    compact = re.sub(r"\s+", "", raw)
    terms = list(_ROLL_TERM.finditer(compact))
    if not terms:
        raise ValueError("Kein gültiger Ausdruck gefunden")

    rebuilt = "".join(t.group(0) for t in terms)
    if rebuilt != compact:
        raise ValueError("Ungültiges Format. Nutze z.B. 1d20+2d6+3")

    total = 0
    details: List[str] = []
    total_dice_rolled = 0

    for t in terms:
        sign_txt = t.group(1)
        sign = -1 if sign_txt == "-" else 1
        token = t.group(2)

        m_dice = _ROLL_DICE.match(token)
        if m_dice:
            count = int(m_dice.group(1))
            sides = int(m_dice.group(2))

            if count < 1 or count > 100:
                raise ValueError("Maximal 100 Würfel pro Term")
            if sides < 2 or sides > 100000:
                raise ValueError("Seitenzahl bitte zwischen 2 und 100000")

            total_dice_rolled += count
            if total_dice_rolled > 200:
                raise ValueError("Zu viele Würfel insgesamt. Maximal 200 pro Ausdruck")

            rolls = [random.randint(1, sides) for _ in range(count)]
            part_sum = sum(rolls) * sign
            total += part_sum

            sgn = "-" if sign < 0 else "+"
            dice_name = f"{count}d{sides}"
            if count == 1:
                details.append(f"{sgn}{dice_name}: {rolls[0]}")
            else:
                details.append(f"{sgn}{dice_name}: {', '.join(map(str, rolls))} (Summe {sum(rolls)})")
            continue

        val = int(token) * sign
        total += val
        sgn = "-" if val < 0 else "+"
        details.append(f"{sgn}{abs(val)} Mod")

    normalized_expr = compact.lower().replace("w", "d")
    if normalized_expr.startswith("+"):
        normalized_expr = normalized_expr[1:]

    return normalized_expr, total, details

async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Beispiele:\n"
            "/roll 1d6\n"
            "/roll 2d20+3\n"
            "/roll 1d20+2d6+3\n"
            "/roll 2w6-1"
        )
        return

    expr = " ".join(context.args).strip()

    try:
        normalized, total, details = parse_roll_expression(expr)
    except ValueError as e:
        await update.message.reply_text(
            f"Ungültiges Format.\n{e}\n\n"
            "Beispiele:\n"
            "/roll 1d6\n"
            "/roll 2d20+3\n"
            "/roll 1d20+2d6+3"
        )
        return

    msg = (
        f"🎲 {normalized}\n"
        f"Details:\n" + "\n".join(details) + "\n\n"
        f"Summe: {total}"
    )
    await update.message.reply_text(msg)

# -----------------------
# ORACLE SYSTEM
# -----------------------

ORACLE_QUESTION, ORACLE_ODDS, ORACLE_CHAOS = range(3)

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
    base = BASE_CHANCE[odds_key]
    adjust = (chaos_rank - 5) * 5
    chance = clamp(base + adjust, 0, 100)

    roll_ = random.randint(1, 100)

    ex_yes = 0 if chance == 0 else max(1, chance // 5)

    fail_size = 100 - chance
    ex_no_size = 0 if fail_size == 0 else int(math.ceil(fail_size / 5))
    ex_no_start = 101 if ex_no_size == 0 else 101 - ex_no_size

    if chance > 0 and roll_ <= ex_yes:
        result = "Außergewöhnlich Ja"
    elif roll_ <= chance:
        result = "Ja"
    elif chance < 100 and roll_ >= ex_no_start:
        result = "Außergewöhnlich Nein"
    else:
        result = "Nein"

    doubles = (roll_ % 11 == 0)
    random_event = bool(doubles and roll_ <= chaos_rank)

    return {
        "roll": roll_,
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

async def rolloracle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("oracle_question", None)
    context.user_data.pop("oracle_odds", None)
    context.user_data.pop("oracle_chaos", None)

    if context.args:
        context.user_data["oracle_question"] = " ".join(context.args).strip()
        await update.message.reply_text("🔮 Wie sind die Chancen?", reply_markup=build_odds_keyboard())
        return ORACLE_ODDS

    await update.message.reply_text("🔮 Was ist deine Ja Nein Frage? Schreib sie als Antwort 🙂")
    return ORACLE_QUESTION

async def rolloracle_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    context.user_data["oracle_question"] = text if text else "Ohne konkrete Frage"
    await update.message.reply_text("Wie sind die Chancen?", reply_markup=build_odds_keyboard())
    return ORACLE_ODDS

async def rolloracle_pick_odds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":", 1)[1]
    context.user_data["oracle_odds"] = data

    await query.edit_message_text("Chaos Rang auswählen, 1 bis 9", reply_markup=build_chaos_keyboard())
    return ORACLE_CHAOS

async def rolloracle_pick_chaos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chaos = int(query.data.split(":", 1)[1])
    context.user_data["oracle_chaos"] = chaos

    odds_key = context.user_data.get("oracle_odds", "fifty_fifty")
    question = context.user_data.get("oracle_question", "Ohne konkrete Frage")

    result = oracle_outcome(odds_key, chaos)
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

# -----------------------
# BIOM SYSTEM
# -----------------------

SURFACE_BIOMES = ["Arktis", "Küste", "Wüste", "Wald", "Grasland", "Hügel", "Berg", "Sumpf"]
SPECIAL_BIOMES = ["Unterreich", "Wasser", "Stadt/Dorf"]
ALL_BIOMES = SURFACE_BIOMES + SPECIAL_BIOMES

def normalize_biom(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if not t:
        return None

    if t in {"stadt", "dorf", "stadt dorf", "stadt/dorf", "stadt\\dorf"}:
        return "Stadt/Dorf"

    for b in ALL_BIOMES:
        if t == b.lower():
            return b
    return None

def build_biom_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row = []
    choices = SURFACE_BIOMES + ["Wasser", "Unterreich"]
    for label in choices:
        row.append(InlineKeyboardButton(label, callback_data=f"biom_set:{label}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def roll_biom(current_biom: str) -> Tuple[str, str, Optional[str]]:
    if current_biom not in ALL_BIOMES:
        raise ValueError(f"Unbekanntes Biom: {current_biom}")

    fixed = {
        "Wasser": 5.0,
        "Unterreich": 5.0,
        "Stadt/Dorf": 10.0,
    }
    current_weight = 66.0

    fixed_for_roll = dict(fixed)
    if current_biom in fixed_for_roll:
        fixed_for_roll.pop(current_biom)

    total_fixed = current_weight + sum(fixed_for_roll.values())
    remaining = 100.0 - total_fixed

    others = [b for b in ALL_BIOMES if b != current_biom and b not in fixed_for_roll]
    per_other = remaining / len(others) if others else 0.0

    choices = [current_biom] + list(fixed_for_roll.keys()) + others
    weights = [current_weight] + list(fixed_for_roll.values()) + [per_other] * len(others)

    rolled = random.choices(choices, weights=weights, k=1)[0]

    if rolled == "Stadt/Dorf":
        return rolled, f"Stadt/Dorf (auf {current_biom})", None

    return rolled, rolled, rolled

async def setbiom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🌍 Wähle dein aktuelles Biom aus", reply_markup=build_biom_keyboard())
        return

    biom_raw = " ".join(context.args).strip()
    biom = normalize_biom(biom_raw)

    if not biom:
        await update.message.reply_text("Unbekanntes Biom. Erlaubt: " + ", ".join(ALL_BIOMES))
        return

    if biom == "Stadt/Dorf":
        await update.message.reply_text("Stadt/Dorf liegt immer auf einem Biom. Setze bitte das Biom darunter, z.B. /setbiom Wald 🙂")
        return

    context.user_data["current_biom"] = biom
    await update.message.reply_text(f"🌍 Aktuelles Biom gesetzt: {biom}")

async def setbiom_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    biom = query.data.split(":", 1)[1]
    biom = normalize_biom(biom)

    if not biom or biom == "Stadt/Dorf":
        await query.edit_message_text("Bitte wähle ein gültiges Biom.")
        return

    context.user_data["current_biom"] = biom
    await query.edit_message_text(f"🌍 Aktuelles Biom gesetzt: {biom}")

async def biom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current_biom")
    if not current:
        await update.message.reply_text("Ich kenne dein aktuelles Biom noch nicht. Setze es mit /setbiom")
        return
    await update.message.reply_text(f"🌍 Aktuelles Biom: {current}")

async def rollbiom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        biom_raw = " ".join(context.args).strip()
        biom_norm = normalize_biom(biom_raw)
        if not biom_norm:
            await update.message.reply_text("Unbekanntes Biom. Erlaubt: " + ", ".join(ALL_BIOMES))
            return
        if biom_norm == "Stadt/Dorf":
            await update.message.reply_text("Stadt/Dorf liegt immer auf einem Biom. Nutze bitte z.B. /rollbiom Wald 🙂")
            return
        context.user_data["current_biom"] = biom_norm

    current = context.user_data.get("current_biom")
    if not current:
        await update.message.reply_text("Setze erst dein aktuelles Biom mit /setbiom, dann /rollbiom")
        return

    rolled_base, display, new_current = roll_biom(current)

    msg = (
        f"🧭 Biom Wurf\n"
        f"Aktuell: {current}\n"
        f"Gerollt: {display}"
    )

    if new_current is not None:
        context.user_data["current_biom"] = new_current
        msg += f"\nNeu: {new_current}"
    else:
        msg += f"\nBiom bleibt: {current}"

    await update.message.reply_text(msg)

# -----------------------
# HELP
# -----------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧰 Befehle\n\n"
        "/help  diese Hilfe\n"
        "/roll <Ausdruck>  Würfeln, z.B. /roll 1d6 oder /roll 2d20+3 oder /roll 1d20+2d6+3 (auch 1w6)\n\n"
        "🌍 Biom\n"
        "/setbiom <Biom>  setzt dein aktuelles Biom (oder ohne Parameter per Buttons)\n"
        "/biom  zeigt dein aktuelles Biom\n"
        "/rollbiom [Biom]  würfelt das nächste Biom (optional vorher setzen)\n\n"
        "🔮 Orakel\n"
        "/rolloracle [Frage]  Ja Nein Orakel\n"
        "/cancel  bricht Orakel ab"
    )
    await update.message.reply_text(msg)

# -----------------------
# PTB Application erstellen
# -----------------------

def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start", help_cmd))

    app.add_handler(CommandHandler("roll", roll))

    app.add_handler(CommandHandler("setbiom", setbiom))
    app.add_handler(CommandHandler("biom", biom))
    app.add_handler(CommandHandler("rollbiom", rollbiom))
    app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))

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

    return app

# -----------------------
# FASTAPI Server
# -----------------------

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

if not TOKEN or not BASE_URL:
    raise RuntimeError("TELEGRAM_BOT_TOKEN oder BASE_URL fehlt")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

ptb_app = build_application(TOKEN)
api = FastAPI()

@api.get("/", response_class=PlainTextResponse)
async def root():
    return "ok"

@api.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"

@api.on_event("startup")
async def on_startup():
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)

@api.on_event("shutdown")
async def on_shutdown():
    await ptb_app.stop()
    await ptb_app.shutdown()

@api.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.update_queue.put(update)
    return {"status": "ok"}
