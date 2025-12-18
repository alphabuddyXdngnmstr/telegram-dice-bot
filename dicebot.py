import os
import random
import re
import math
from pathlib import Path
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

# Erlaubt z.B. 1d6, 2d20, 3d8+4, 1d10-1, auch 1w6
DICE_PATTERN = re.compile(r"^(\d+)[dw](\d+)([+-]\d+)?$", re.IGNORECASE)

# Oracle Conversation States
ORACLE_QUESTION, ORACLE_ODDS, ORACLE_CHAOS = range(3)

# Encounter Conversation States
ENC_CONFIRM, ENC_PICK_BIOM, ENC_PICK_LEVEL = range(3)

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

# -----------------------
# BIOM SYSTEM
# -----------------------
SURFACE_BIOMES = ["Arktis", "Küste", "Wüste", "Wald", "Grasland", "Hügel", "Berg", "Sumpf"]
SPECIAL_BIOMES = ["Unterreich", "Wasser", "Stadt/Dorf"]
ALL_BIOMES = SURFACE_BIOMES + SPECIAL_BIOMES

def normalize_biom(text: str) -> str | None:
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

def roll_biom(current_biom: str) -> tuple[str, str, str | None]:
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
        await update.message.reply_text(
            "🌍 Wähle dein aktuelles Biom aus",
            reply_markup=build_biom_keyboard()
        )
        return

    biom_raw = " ".join(context.args).strip()
    biom = normalize_biom(biom_raw)

    if not biom:
        await update.message.reply_text("Unbekanntes Biom. Erlaubt: " + ", ".join(ALL_BIOMES))
        return

    if biom == "Stadt/Dorf":
        await update.message.reply_text(
            "Stadt/Dorf liegt immer auf einem Biom. Setze bitte das Biom darunter, z.B. /setbiom Wald 🙂"
        )
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
            await update.message.reply_text(
                "Stadt/Dorf liegt immer auf einem Biom. Nutze bitte z.B. /rollbiom Wald 🙂"
            )
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
# ENCOUNTER SYSTEM
# -----------------------

ENCOUNTERS: dict[str, dict[str, list[tuple[int, int, str]]]] = {}

def _to_int_w100(token: str) -> int:
    token = token.strip()
    if token == "00":
        return 100
    return int(token)

def _canonical_level(a: int, b: int) -> str:
    if a == 1 and b in (4, 5):
        return "1-4"
    if a in (5, 6) and b == 10:
        return "5-10"
    if a == 11 and b == 16:
        return "11-16"
    if a == 17 and b == 20:
        return "17-20"
    if a == 11 and b == 20:
        return "11-20"
    return f"{a}-{b}"

def _canonical_enc_biom(raw: str) -> str:
    t = (raw or "").strip().lower()

    if "arktis" in t:
        return "Arktis"
    if "grasland" in t:
        return "Grasland"
    if "hügel" in t or "huegel" in t:
        return "Hügel"
    if "küste" in t or "kueste" in t or "küsten" in t or "kuesten" in t:
        return "Küste"
    if "sumpf" in t:
        return "Sumpf"
    if "wald" in t:
        return "Wald"
    if "wüste" in t or "wueste" in t:
        return "Wüste"
    if "underdark" in t or "unterreich" in t:
        return "Unterreich"
    if "unterwasser" in t:
        return "Unterwasser"
    if "stadt" in t or "dorf" in t:
        return "Stadt/Dorf"
    if "berg" in t:
        return "Berg"

    return raw.strip()

def _biom_for_encounter_from_current(current: str) -> str:
    # Biom System hat "Wasser", Encounter System nutzt "Unterwasser"
    if current == "Wasser":
        return "Unterwasser"
    return current

def _load_encounter_raw_text() -> str:
    path = Path(__file__).with_name("encounters_de.txt")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")

def _load_encounters_from_text(text: str) -> dict[str, dict[str, list[tuple[int, int, str]]]]:
    lines = [ln.strip() for ln in text.splitlines()]

    heading_re = re.compile(
        r"^(?P<biome>.+?)\s*\(\s*Stufe\s*(?P<a>\d+)\s*(?:-|–|bis)\s*(?P<b>\d+)\s*\)",
        re.IGNORECASE,
    )
    range_re = re.compile(
        r"^(?P<s>\d{2})(?:\s*(?:-|–|bis)\s*(?P<e>\d{2}))?\s*(?P<rest>.*)$",
        re.IGNORECASE,
    )

    data: dict[str, dict[str, list[tuple[int, int, str]]]] = {}
    cur_biome: str | None = None
    cur_level: str | None = None
    pending_range: tuple[int, int] | None = None
    pending_text_parts: list[str] = []

    def flush_pending():
        nonlocal pending_range, pending_text_parts
        if cur_biome and cur_level and pending_range and pending_text_parts:
            s, e = pending_range
            entry = " ".join(pending_text_parts).strip()
            if entry:
                data.setdefault(cur_biome, {}).setdefault(cur_level, []).append((s, e, entry))
        pending_range = None
        pending_text_parts = []

    for ln in lines:
        if not ln:
            continue

        m_head = heading_re.match(ln)
        if m_head:
            flush_pending()
            cur_biome = _canonical_enc_biom(m_head.group("biome").strip())
            a = int(m_head.group("a"))
            b = int(m_head.group("b"))
            cur_level = _canonical_level(a, b)
            continue

        low = ln.lower()
        if low.startswith("w100"):
            continue
        if "w100" in low and "begegn" in low:
            continue

        m_rng = range_re.match(ln)
        if m_rng and cur_biome and cur_level:
            s = _to_int_w100(m_rng.group("s"))
            e_raw = m_rng.group("e")
            e = _to_int_w100(e_raw) if e_raw else s
            if s > e:
                s, e = e, s

            rest = (m_rng.group("rest") or "").strip()

            flush_pending()
            pending_range = (s, e)
            pending_text_parts = [rest] if rest else []
            continue

        if pending_range:
            pending_text_parts.append(ln)

    flush_pending()
    return data

def init_encounters():
    global ENCOUNTERS
    raw = _load_encounter_raw_text()
    ENCOUNTERS = _load_encounters_from_text(raw) if raw.strip() else {}

def build_encounter_confirm_keyboard(current_biom: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"✅ {current_biom}", callback_data="enc_confirm:yes"),
            InlineKeyboardButton("🌍 Anderes Biom", callback_data="enc_confirm:no"),
        ]
    ]
    return InlineKeyboardMarkup(rows)

def build_encounter_biom_keyboard() -> InlineKeyboardMarkup:
    choices = [
        "Arktis", "Berg", "Grasland", "Hügel",
        "Küste", "Sumpf", "Wald", "Wüste",
        "Unterreich", "Unterwasser", "Stadt/Dorf",
    ]
    rows = []
    row = []
    for label in choices:
        row.append(InlineKeyboardButton(label, callback_data=f"enc_biom:{label}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def build_encounter_level_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("1-4", callback_data="enc_lvl:1-4"),
            InlineKeyboardButton("5-10", callback_data="enc_lvl:5-10"),
        ],
        [
            InlineKeyboardButton("11-16", callback_data="enc_lvl:11-16"),
            InlineKeyboardButton("17-20", callback_data="enc_lvl:17-20"),
        ],
    ]
    return InlineKeyboardMarkup(rows)

def pick_encounter(biom: str, level: str) -> tuple[int, str]:
    biom = _canonical_enc_biom(biom)
    tables_for_biom = ENCOUNTERS.get(biom, {})
    table = tables_for_biom.get(level)

    if table is None and level in ("11-16", "17-20"):
        table = tables_for_biom.get("11-20")

    if not table:
        raise KeyError(f"Keine Tabelle für {biom} {level}")

    roll = random.randint(1, 100)
    for s, e, txt in table:
        if s <= roll <= e:
            return roll, txt

    return roll, "Nichts gefunden. Deine Tabelle hat an der Stelle vermutlich eine Lücke."

_W_DICE_EXPR = re.compile(r"(\d+)\s*[Ww]\s*(\d+)(\s*[+-]\s*\d+)?")

def roll_inline_w_dice(text: str) -> tuple[str, list[str]]:
    """
    Ersetzt Ausdrücke wie 1W6, 2W10 + 5 usw direkt im Text durch das Ergebnis.
    Gibt zusätzlich eine Liste mit Details der Würfe zurück.
    """
    details: list[str] = []

    def repl(m: re.Match) -> str:
        count = int(m.group(1))
        sides = int(m.group(2))
        mod_raw = m.group(3)
        mod = 0
        if mod_raw:
            mod = int(mod_raw.replace(" ", ""))

        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + mod

        mod_txt = f"{mod:+d}" if mod else ""
        details.append(f"{count}W{sides}{mod_txt} = {total} (Würfe: {', '.join(map(str, rolls))})")
        return str(total)

    rolled_text = _W_DICE_EXPR.sub(repl, text)
    return rolled_text, details

async def rollencounter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ENCOUNTERS:
        await update.message.reply_text(
            "Ich habe noch keine Encounter Tabellen geladen.\n"
            "Lege eine encounters_de.txt neben dein Script und starte den Bot neu 🙂"
        )
        return ConversationHandler.END

    # Optional: /rollencounter Wald
    if context.args:
        raw = " ".join(context.args).strip()
        biom_norm = normalize_biom(raw) or _canonical_enc_biom(raw)
        biom_norm = _biom_for_encounter_from_current(biom_norm)
        context.user_data["enc_biom"] = biom_norm
        await update.message.reply_text(
            f"⚔️ Biom: {biom_norm}\nWelche Stufe?",
            reply_markup=build_encounter_level_keyboard()
        )
        return ENC_PICK_LEVEL

    current = context.user_data.get("current_biom")
    if not current:
        await update.message.reply_text(
            "Ich kenne dein aktuelles Biom noch nicht.\n"
            "Setze es bitte erst mit /setbiom 🙂",
            reply_markup=build_biom_keyboard()
        )
        return ConversationHandler.END

    enc_biom = _biom_for_encounter_from_current(current)
    context.user_data["enc_biom"] = enc_biom

    await update.message.reply_text(
        f"⚔️ Nutze aktuelles Biom?\nAktuell: {current}\nEncounter Tabelle: {enc_biom}",
        reply_markup=build_encounter_confirm_keyboard(enc_biom)
    )
    return ENC_CONFIRM

async def rollencounter_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.split(":", 1)[1]
    if choice == "yes":
        biom = context.user_data.get("enc_biom", "Unbekannt")
        await query.edit_message_text(
            f"⚔️ Biom: {biom}\nWelche Stufe?",
            reply_markup=build_encounter_level_keyboard()
        )
        return ENC_PICK_LEVEL

    await query.edit_message_text(
        "⚔️ Welches Biom?",
        reply_markup=build_encounter_biom_keyboard()
    )
    return ENC_PICK_BIOM

async def rollencounter_pick_biom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    biom = query.data.split(":", 1)[1]
    context.user_data["enc_biom"] = biom

    await query.edit_message_text(
        f"⚔️ Biom: {biom}\nWelche Stufe?",
        reply_markup=build_encounter_level_keyboard()
    )
    return ENC_PICK_LEVEL

async def rollencounter_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    level = query.data.split(":", 1)[1]
    biom = (context.user_data.get("enc_biom") or "").strip()

    try:
        w100, encounter_raw = pick_encounter(biom, level)
        encounter_rolled, dice_details = roll_inline_w_dice(encounter_raw)

        msg = (
            f"⚔️ Encounter\n"
            f"Biom: {_canonical_enc_biom(biom)}\n"
            f"Stufe: {level}\n"
            f"W100: {w100:02d}\n\n"
            f"Begegnung (Tabelle):\n{encounter_raw}\n\n"
            f"Begegnung (ausgewürfelt):\n{encounter_rolled}"
        )

        if dice_details:
            msg += "\n\nWürfe:\n" + "\n".join(dice_details)

    except KeyError:
        msg = (
            f"Für Biom {biom} und Stufe {level} habe ich keine passende Tabelle gefunden.\n"
            f"Check die Überschrift in encounters_de.txt."
        )

    await query.edit_message_text(msg)
    return ConversationHandler.END

async def rollencounter_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Encounter abgebrochen 🙂")
    return ConversationHandler.END

# -----------------------
# ORACLE + DICE SYSTEM
# -----------------------
def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def oracle_outcome(odds_key: str, chaos_rank: int) -> dict:
    base = BASE_CHANCE[odds_key]
    adjust = (chaos_rank - 5) * 5
    chance = clamp(base + adjust, 0, 100)

    roll = random.randint(1, 100)

    ex_yes = 0 if chance == 0 else max(1, chance // 5)

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
        await update.message.reply_text("Beispiel:\n/roll 1d6\n/roll 2d20+3\n/roll 1w6")
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

    # Encounter Tabellen laden
    init_encounters()

    # Standard Würfel
    app.add_handler(CommandHandler("roll", roll))

    # Biom System
    app.add_handler(CommandHandler("setbiom", setbiom))
    app.add_handler(CommandHandler("biom", biom))
    app.add_handler(CommandHandler("rollbiom", rollbiom))
    app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))

    # Encounter Conversation
    encounter_conv = ConversationHandler(
        entry_points=[CommandHandler("rollencounter", rollencounter_start)],
        states={
            ENC_CONFIRM: [CallbackQueryHandler(rollencounter_confirm, pattern=r"^enc_confirm:")],
            ENC_PICK_BIOM: [CallbackQueryHandler(rollencounter_pick_biom, pattern=r"^enc_biom:")],
            ENC_PICK_LEVEL: [CallbackQueryHandler(rollencounter_pick_level, pattern=r"^enc_lvl:")],
        },
        fallbacks=[CommandHandler("cancel", rollencounter_cancel)],
        allow_reentry=True,
    )
    app.add_handler(encounter_conv)

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
