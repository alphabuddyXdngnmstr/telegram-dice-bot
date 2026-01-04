import os
import random
import re
import math
from pathlib import Path
from typing import Optional, Tuple, List, Dict
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

# Neue Roll Syntax: Ausdruck aus Würfeltermen und Zahlen, z.B.
# 1d20+2d6+3
# 2w6-1d4+5
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

        # Zahlenterm
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
# ENCOUNTER SYSTEM (liest encounters_de.txt)
# -----------------------

ENC_CONFIRM, ENC_PICK_BIOM, ENC_PICK_LEVEL = range(3)

ENCOUNTERS: Dict[str, Dict[str, List[Tuple[int, int, str]]]] = {}

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
    if current == "Wasser":
        return "Unterwasser"
    return current

def _load_encounter_raw_text() -> str:
    path = Path(__file__).with_name("encounters_de.txt")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")

def _clean_enc_line(ln: str) -> str:
    if ln is None:
        return ""
    s = ln.replace("\ufeff", "")
    s = s.replace("\u00a0", " ")
    # normalize Dash Varianten zu "-"
    for ch in ["\u2013", "\u2014", "\u2212", "\u2011"]:
        s = s.replace(ch, "-")
    return s.strip()

def _load_encounters_from_text(text: str) -> Dict[str, Dict[str, List[Tuple[int, int, str]]]]:
    lines = [_clean_enc_line(ln) for ln in text.splitlines()]

    heading_re = re.compile(
        r"^(?P<biome>.+?)\s*\(\s*Stufe\s*(?P<a>\d+)\s*(?:-|bis)\s*(?P<b>\d+)\s*\)",
        re.IGNORECASE,
    )
    range_re = re.compile(
        r"^(?P<s>\d{2})(?:\s*(?:-|bis)\s*(?P<e>\d{2}))?\s*(?P<rest>.*)$",
        re.IGNORECASE,
    )

    data: Dict[str, Dict[str, List[Tuple[int, int, str]]]] = {}
    cur_biome: Optional[str] = None
    cur_level: Optional[str] = None
    pending_range: Optional[Tuple[int, int]] = None
    pending_text_parts: List[str] = []

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
    rows = [[
        InlineKeyboardButton(f"✅ {current_biom}", callback_data="enc_confirm:yes"),
        InlineKeyboardButton("🌍 Anderes Biom", callback_data="enc_confirm:no"),
    ]]
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
        [InlineKeyboardButton("1-4", callback_data="enc_lvl:1-4"), InlineKeyboardButton("5-10", callback_data="enc_lvl:5-10")],
        [InlineKeyboardButton("11-16", callback_data="enc_lvl:11-16"), InlineKeyboardButton("17-20", callback_data="enc_lvl:17-20")],
    ]
    return InlineKeyboardMarkup(rows)

def pick_encounter(biom: str, level: str) -> Tuple[int, str]:
    biom = _canonical_enc_biom(biom)
    tables_for_biom = ENCOUNTERS.get(biom, {})
    table = tables_for_biom.get(level)

    if table is None and level in ("11-16", "17-20"):
        table = tables_for_biom.get("11-20")

    if not table:
        available = ", ".join(sorted(tables_for_biom.keys()))
        if not available:
            available = "keine"
        raise KeyError(f"Keine Tabelle für {biom} {level}. Verfügbar: {available}")

    roll = random.randint(1, 100)
    for s, e, txt in table:
        if s <= roll <= e:
            return roll, txt

    return roll, "Nichts gefunden. Deine Tabelle hat an der Stelle vermutlich eine Lücke."

_W_DICE_EXPR = re.compile(r"(\d+)\s*[Ww]\s*(\d+)(\s*[+-]\s*\d+)?")

def roll_inline_w_dice(text: str) -> Tuple[str, List[str]]:
    details: List[str] = []

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

    if context.args:
        raw = " ".join(context.args).strip()
        biom_norm = normalize_biom(raw) or _canonical_enc_biom(raw)
        biom_norm = _biom_for_encounter_from_current(biom_norm)
        context.user_data["enc_biome"] = biom_norm

        await update.message.reply_text(f"⚔️ Biom: {biom_norm}\nWelche Stufe?", reply_markup=build_encounter_level_keyboard())
        return ENC_PICK_LEVEL

    current = context.user_data.get("current_biom")
    if not current:
        await update.message.reply_text(
            "Ich kenne dein aktuelles Biom noch nicht.\nSetze es bitte erst mit /setbiom 🙂",
            reply_markup=build_biom_keyboard()
        )
        return ConversationHandler.END

    enc_biom = _biom_for_encounter_from_current(current)
    context.user_data["enc_biome"] = enc_biom

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
        biom = context.user_data.get("enc_biome", "Unbekannt")
        await query.edit_message_text(f"⚔️ Biom: {biom}\nWelche Stufe?", reply_markup=build_encounter_level_keyboard())
        return ENC_PICK_LEVEL

    await query.edit_message_text("⚔️ Welches Biom?", reply_markup=build_encounter_biom_keyboard())
    return ENC_PICK_BIOM

async def rollencounter_pick_biom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    biom = query.data.split(":", 1)[1]
    context.user_data["enc_biome"] = biom

    await query.edit_message_text(f"⚔️ Biom: {biom}\nWelche Stufe?", reply_markup=build_encounter_level_keyboard())
    return ENC_PICK_LEVEL

async def rollencounter_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    level = query.data.split(":", 1)[1]
    biom = (context.user_data.get("enc_biome") or "").strip()

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

    except KeyError as e:
        msg = (
            f"{e}\n\n"
            f"Check Level Auswahl und die Überschrift in encounters_de.txt.\n"
            f"Wenn du auf Render bist, check ob auf dem Server wirklich die aktuelle Datei liegt."
        )

    await query.edit_message_text(msg)
    return ConversationHandler.END

async def rollencounter_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Encounter abgebrochen 🙂")
    return ConversationHandler.END

async def encdebug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ENCOUNTERS:
        await update.message.reply_text("Keine Encounter geladen.")
        return

    lines = []
    for biom in sorted(ENCOUNTERS.keys()):
        lvls = ", ".join(sorted(ENCOUNTERS[biom].keys()))
        lines.append(f"{biom}: {lvls}")

    await update.message.reply_text(
        "📚 Encounter Debug\n"
        "Geladene Tabellen:\n" + "\n".join(lines)
    )


# -----------------------
# ROLLCHANCE SYSTEM
# -----------------------

ATTR_TABLE = {
    1: ("STÄ", "💪"),
    2: ("GES", "🏃‍♂️"),
    3: ("KON", "🛡️"),
    4: ("INT", "🧠"),
    5: ("WEI", "🦉"),
    6: ("CHA", "✨"),
}

def _roll_sum(count: int, sides: int) -> Tuple[int, List[int]]:
    rolls = [random.randint(1, sides) for _ in range(count)]
    return sum(rolls), rolls

def _apply_next_reward_bonus_if_any(context: ContextTypes.DEFAULT_TYPE) -> Tuple[int, Optional[str]]:
    if context.user_data.get("next_reward_bonus_d10x10"):
        context.user_data["next_reward_bonus_d10x10"] = False
        bonus = random.randint(1, 10) * 10
        return bonus, f"Bonus (Merker): 1W10x10 = {bonus} GM"
    return 0, None

async def rollchance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    skill_roll = random.randint(1, 6)
    attr, emoji = ATTR_TABLE[skill_roll]

    w100 = random.randint(1, 100)

    sg = 10
    reward = 0
    reward_text = ""
    magic_item = False

    if 1 <= w100 <= 40:
        sg = 10
        base, _r = _roll_sum(1, 10)
        reward = base * 10
        reward_text = f"{reward} GM"
    elif 41 <= w100 <= 75:
        sg = 15
        base, _r = _roll_sum(2, 10)
        reward = base * 10
        reward_text = f"{reward} GM"
    elif 76 <= w100 <= 90:
        sg = 18
        base, _r = _roll_sum(4, 10)
        reward = base * 10
        reward_text = f"{reward} GM"
    elif 91 <= w100 <= 98:
        sg = 22
        base, _r = _roll_sum(6, 10)
        reward = base * 10
        reward_text = f"{reward} GM"
    else:
        sg = 30
        base, _r = _roll_sum(1, 4)
        reward = base * 1000
        reward_text = f"{reward} GM + 1x Magic Item"
        magic_item = True

    bonus, bonus_line = _apply_next_reward_bonus_if_any(context)
    if bonus:
        reward += bonus
        if magic_item:
            reward_text = f"{reward} GM + 1x Magic Item"
        else:
            reward_text = f"{reward} GM"

    msg = (
        f"🎯 Rollchance\n"
        f"Skillwurf 1W6: {skill_roll}\n"
        f"Attribut: {attr} {emoji}\n"
        f"W100: {w100:02d}\n"
    )

    if bonus_line:
        msg += f"{bonus_line}\n"

    msg += (
        f"\nDein Skill SG ist {sg} für {attr} {emoji}. "
        f"Deine Belohnung ist {reward_text} (W100: {w100:02d}). "
        f"Viel Erfolg 😊"
    )

    await update.message.reply_text(msg)


# -----------------------
# ROLLHUNT SYSTEM
# -----------------------

HUNT_MOD_CHOICES = list(range(-4, 7))

def build_hunt_mod_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for m in HUNT_MOD_CHOICES:
        label = f"{m:+d}" if m != 0 else "0"
        row.append(InlineKeyboardButton(label, callback_data=f"hunt_mod:{m}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Abbrechen", callback_data="hunt_cancel")])
    return InlineKeyboardMarkup(rows)

def hunt_outcome_text(total: int) -> str:
    if total <= 5:
        return "Kein Erfolg"
    if 6 <= total <= 10:
        return "Tierspuren gefunden"
    if 11 <= total <= 15:
        return "Beeren oder Muscheln (1x Ration) + 10 XP"
    if 16 <= total <= 19:
        return "Jagderfolg, normale Beute (2x Ration)"
    return "Jagderfolg, sehr gute Beute + Tierfell (10 GM Wert)"

async def rollhunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        try:
            mod = int(context.args[0])
            if mod not in HUNT_MOD_CHOICES:
                raise ValueError
            context.user_data["hunt_mod"] = mod
            roll1 = random.randint(1, 20)
            total1 = roll1 + mod
            first_txt = hunt_outcome_text(total1)

            msg = (
                f"🏹 Rollhunt\n"
                f"Wurf: 1W20 ({roll1}) + Mod ({mod:+d}) = {total1}\n"
                f"Ergebnis: {first_txt}"
            )

            if 6 <= total1 <= 10:
                roll2 = random.randint(1, 20)
                total2 = roll2 + mod
                second_txt = hunt_outcome_text(total2)
                msg += (
                    f"\n\nSpuren gefunden, du würfelst nochmal\n"
                    f"Neuer Wurf: 1W20 ({roll2}) + Mod ({mod:+d}) = {total2}\n"
                    f"Neues Ergebnis: {second_txt}"
                )

            await update.message.reply_text(msg)
            return
        except Exception:
            pass

    await update.message.reply_text(
        "🏹 Rollhunt\nWie hoch ist deine Mod von WEI oder Überlebenskunst oder Naturkunde? Wähle den passenden Wert 🙂",
        reply_markup=build_hunt_mod_keyboard()
    )

async def rollhunt_pick_mod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    raw = query.data.split(":", 1)[1]
    try:
        mod = int(raw)
    except ValueError:
        await query.edit_message_text("Ungültiger Mod. Nutze /rollhunt erneut 🙂")
        return

    if mod not in HUNT_MOD_CHOICES:
        await query.edit_message_text("Mod muss zwischen -4 und 6 liegen. Nutze /rollhunt erneut 🙂")
        return

    context.user_data["hunt_mod"] = mod

    roll1 = random.randint(1, 20)
    total1 = roll1 + mod
    first_txt = hunt_outcome_text(total1)

    msg = (
        f"🏹 Rollhunt\n"
        f"Mod: {mod:+d}\n"
        f"Wurf: 1W20 ({roll1}) + Mod ({mod:+d}) = {total1}\n"
        f"Ergebnis: {first_txt}"
    )

    if 6 <= total1 <= 10:
        roll2 = random.randint(1, 20)
        total2 = roll2 + mod
        second_txt = hunt_outcome_text(total2)
        msg += (
            f"\n\nSpuren gefunden, du würfelst nochmal\n"
            f"Neuer Wurf: 1W20 ({roll2}) + Mod ({mod:+d}) = {total2}\n"
            f"Neues Ergebnis: {second_txt}"
        )

    await query.edit_message_text(msg)

async def rollhunt_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Rollhunt abgebrochen 🙂")


# -----------------------
# WALDKARTE SYSTEM
# -----------------------

WALDKARTE_LEVELS = ["1-4", "5-10", "11-16", "17-20"]

def build_waldkarte_level_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("1-4", callback_data="waldkarte_level:1-4"), InlineKeyboardButton("5-10", callback_data="waldkarte_level:5-10")],
        [InlineKeyboardButton("11-16", callback_data="waldkarte_level:11-16"), InlineKeyboardButton("17-20", callback_data="waldkarte_level:17-20")],
    ]
    return InlineKeyboardMarkup(rows)

async def rollwaldkarte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    roll18 = random.randint(1, 18)

    if 1 <= roll18 <= 7:
        await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Skillchance")
        await rollchance(update, context)
        return

    if 8 <= roll18 <= 11:
        await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Ruhe\nDu kannst jagen, chillen oder trainieren 🙂")
        return

    if roll18 == 12:
        d4 = random.randint(1, 4)
        mapping = {1: "Ruine", 2: "Händler", 3: "Dorf", 4: "Gasthaus"}
        await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Ortschaft außerhalb der Karte\nW4: {d4} -> {mapping[d4]}")
        return

    if roll18 in (13, 14):
        context.user_data["waldkarte_pending"] = {"type": "encounter", "card_roll": roll18}
        await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Encounter\n\nWähle die Stufe:", reply_markup=build_waldkarte_level_keyboard())
        return

    if roll18 in (15, 16):
        d6 = random.randint(1, 6)

        if d6 == 1:
            a = random.randint(1, 10)
            b = random.randint(1, 10)
            gold = (a + b) * 10
            await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Entdeckung\nW6: {d6} -> Truhe\n2W10: {a} + {b} = {a + b}\nBelohnung: {gold} GM")
            return

        if d6 == 2:
            await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Entdeckung\nW6: {d6} -> 50% Rabatt Händler")
            return

        if d6 == 3:
            await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Entdeckung\nW6: {d6} -> Zauberschriften Händler")
            return

        if d6 == 4:
            context.user_data["next_reward_bonus_d10x10"] = True
            await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Entdeckung\nW6: {d6} -> Merker\nBei deiner nächsten Belohnung bekommst du zusätzlich 1W10x10 GM 🙂")
            return

        if d6 == 5:
            await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Entdeckung\nW6: {d6} -> 1x Inspiration")
            return

        context.user_data["omen_bonus_d6"] = True
        await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Entdeckung\nW6: {d6} -> Omen\nMerker: Du kannst 1W6 zu jedem Wurf dazunehmen 🙂")
        return

    if roll18 == 17:
        context.user_data["waldkarte_pending"] = {"type": "hort", "card_roll": roll18}
        await update.message.reply_text(f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: Kreaturenhort\n\nWähle die Stufe:", reply_markup=build_waldkarte_level_keyboard())
        return

    await update.message.reply_text(
        f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: NPC\nEin NPC gibt dir eine Wegbeschreibung zum Portal oder die Info, die du suchst (Joker)."
    )

async def rollwaldkarte_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    level = query.data.split(":", 1)[1].strip()
    pending = context.user_data.get("waldkarte_pending")

    if not pending:
        await query.edit_message_text("Ich habe keine offene Waldkarte Auswahl mehr. Nutze /rollwaldkarte 🙂")
        return

    if level not in WALDKARTE_LEVELS:
        context.user_data.pop("waldkarte_pending", None)
        await query.edit_message_text("Ungültige Stufe. Nutze /rollwaldkarte erneut 🙂")
        return

    if not ENCOUNTERS:
        context.user_data.pop("waldkarte_pending", None)
        await query.edit_message_text("Ich habe keine Encounter Tabellen geladen.\nLege eine encounters_de.txt neben dein Script und starte den Bot neu 🙂")
        return

    card_roll = pending.get("card_roll", "?")
    kind = pending.get("type", "encounter")
    context.user_data.pop("waldkarte_pending", None)

    biome = "Wald"

    try:
        w100, encounter_raw = pick_encounter(biome, level)
        encounter_rolled, dice_details = roll_inline_w_dice(encounter_raw)

        title = "Kreaturenhort" if kind == "hort" else "Encounter"
        extra = "Das ist die Kreatur, die den Hort hält oder bewacht." if kind == "hort" else "Viel Spaß 🙂"

        msg = (
            f"🌲 Waldkarte\n"
            f"W18: {card_roll}\n"
            f"Ergebnis: {title}\n"
            f"Biom: {biome}\n"
            f"Stufe: {level}\n"
            f"W100: {w100:02d}\n\n"
            f"Begegnung (Tabelle):\n{encounter_raw}\n\n"
            f"Begegnung (ausgewürfelt):\n{encounter_rolled}\n\n"
            f"{extra}"
        )

        if dice_details:
            msg += "\n\nWürfe:\n" + "\n".join(dice_details)

        await query.edit_message_text(msg)
    except KeyError as e:
        await query.edit_message_text(str(e))


# -----------------------
# ROLLPLAYERBEHAVIOUR SYSTEM
# -----------------------

PLAYER_BEHAVIOUR_TABLE = {
    1: ("Chaotisch Dumm", "Du stehst vor einer Tür, was tust du? Schlüssel gegen Tür werfen!"),
    2: ("Chaotisch", "Du stehst vor einer Tür, was tust du? Schlüssel gegen Tür werfen und auf das Schloss zielen!"),
    3: ("Neutral, Neutral", "Du stehst vor einer Tür, was tust du? Schlüssel ins Schloss stecken."),
    4: ("Neutral Prüfend", "Du stehst vor einer Tür, was tust du? An der Tür stehen Schlüssel betrachten und ins Schloss stecken."),
    5: ("Logisch", "Du stehst vor einer Tür, was tust du? An der Tür lauschen und Entscheidung treffen ggf die Tür zu öffnen"),
    6: ("Logisch Intelligent", "Du stehst vor einer Tür, was tust du? Lauschen, durch das Schlüsselloch schauen und vorbereiten."),
}

async def rollplayerbehaviour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = random.randint(1, 6)
    title, example = PLAYER_BEHAVIOUR_TABLE[r]

    msg = (
        f"🎭 Rollplayer Behaviour\n"
        f"1W6: {r}\n"
        f"Ergebnis: {title}\n"
        f"Bsp: {example}"
    )
    await update.message.reply_text(msg)


# -----------------------
# ROLLSCHATZ SYSTEM
# -----------------------

TREASURE_KIND_STATE = 200
TREASURE_CR_STATE = 201

COIN_ORDER = ["KM", "SM", "EM", "GM", "PM"]

def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")

def _fmt_w100(n: int) -> str:
    return "00" if n == 100 else f"{n:02d}"

def _roll_nds(count: int, sides: int) -> Tuple[int, List[int]]:
    rolls = [random.randint(1, sides) for _ in range(count)]
    return sum(rolls), rolls

def _roll_coin_spec(coin: str, count: int, sides: int, mult: int) -> Tuple[int, str]:
    base, rolls = _roll_nds(count, sides)
    total = base * mult
    if mult == 1:
        detail = f"{coin}: {count}W{sides} = {base} (Würfe: {', '.join(map(str, rolls))})"
    else:
        detail = f"{coin}: {count}W{sides} x { _fmt_int(mult) } = { _fmt_int(total) } (Basis {base}, Würfe: {', '.join(map(str, rolls))})"
    return total, detail

def _roll_count_expr(expr: str) -> Tuple[int, str]:
    e = (expr or "").strip()
    if e == "1":
        return 1, "1"
    m = re.match(r"^(\d+)[Ww](\d+)$", e)
    if not m:
        return 1, "1"
    c = int(m.group(1))
    s = int(m.group(2))
    total, rolls = _roll_nds(c, s)
    return total, f"{c}W{s} = {total} (Würfe: {', '.join(map(str, rolls))})"

def _pick_range_table(table: List[Tuple[int, int, object]], roll: int):
    for a, b, payload in table:
        if a <= roll <= b:
            return payload
    return None

# Einzelschatz Tabellen
INDIVIDUAL_TREASURE: Dict[str, List[Tuple[int, int, List[Tuple[str, int, int, int]]]]] = {
    "0-4": [
        (1, 30, [("KM", 5, 6, 1)]),
        (31, 60, [("SM", 4, 6, 1)]),
        (61, 70, [("EM", 3, 6, 1)]),
        (71, 95, [("GM", 3, 6, 1)]),
        (96, 100, [("PM", 1, 6, 1)]),
    ],
    "5-10": [
        (1, 30, [("KM", 4, 6, 100), ("EM", 1, 6, 10)]),
        (31, 60, [("SM", 6, 6, 10), ("GM", 2, 6, 10)]),
        (61, 70, [("EM", 3, 6, 10), ("GM", 2, 6, 10)]),
        (71, 95, [("GM", 4, 6, 10)]),
        (96, 100, [("GM", 2, 6, 10), ("PM", 3, 6, 1)]),
    ],
    "11-16": [
        (1, 20, [("SM", 4, 6, 100), ("GM", 1, 6, 100)]),
        (21, 35, [("EM", 1, 6, 100), ("GM", 1, 6, 100)]),
        (36, 75, [("GM", 2, 6, 100), ("PM", 1, 6, 10)]),
        (76, 100, [("GM", 2, 6, 100), ("PM", 2, 6, 10)]),
    ],
    "17+": [
        (1, 15, [("EM", 2, 6, 1000), ("GM", 8, 6, 100)]),
        (16, 55, [("GM", 1, 6, 1000), ("PM", 1, 6, 100)]),
        (56, 100, [("GM", 1, 6, 1000), ("PM", 2, 6, 100)]),
    ],
}

# Magische Gegenstände Tabellen A bis I
MAGIC_TABLES: Dict[str, List[Tuple[int, int, str]]] = {
    "A": [
        (1, 50, "Heiltrank"),
        (51, 60, "Zauberschriftrolle (Zaubertrick)"),
        (61, 70, "Trank des Kletterns"),
        (71, 90, "Zauberschriftrolle (Zaubergrad 1)"),
        (91, 94, "Zauberschriftrolle (Zaubergrad 2)"),
        (95, 98, "Trank der mächtigen Heilung"),
        (99, 99, "Nimmervoller Beutel"),
        (100, 100, "Schwebekugel"),
    ],
    "B": [
        (1, 15, "Trank der mächtigen Heilung"),
        (16, 22, "Trank des Feueratems"),
        (23, 29, "Trank des Widerstands"),
        (30, 34, "Geschosse, +1"),
        (35, 39, "Trank der Tierfreundschaft"),
        (40, 44, "Trank der Hügelriesenstärke"),
        (45, 49, "Trank des Wachstums"),
        (50, 54, "Trank der Wasseratmung"),
        (55, 59, "Zauberschriftrolle (Zaubergrad 2)"),
        (60, 64, "Zauberschriftrolle (Zaubergrad 3)"),
        (65, 67, "Nimmervoller Beutel"),
        (68, 70, "Keoghtoms Salbe"),
        (71, 73, "Öl der Glätte"),
        (74, 75, "Staub des Verschwindens"),
        (76, 77, "Staub der Trockenheit"),
        (78, 79, "Staub des Niesens und Erstickens"),
        (80, 81, "Elementarer Edelstein"),
        (82, 83, "Liebestrank"),
        (84, 84, "Alchemiekrug"),
        (85, 85, "Mütze der Wasseratmung"),
        (86, 86, "Umhang des Mantarochen"),
        (87, 87, "Schwebekugel"),
        (88, 88, "Nachtbrille"),
        (89, 89, "Helm des Sprachenverstehens"),
        (90, 90, "Unbewegliches Zepter"),
        (91, 91, "Laterne der Enttarnung"),
        (92, 92, "Rüstung des Wassermanns"),
        (93, 93, "Mithrilrüstung"),
        (94, 94, "Trank des Gifts"),
        (95, 95, "Ring des Schwimmens"),
        (96, 96, "Robe der nützlichen Dinge"),
        (97, 97, "Seil des Kletterns"),
        (98, 98, "Sattel des Kavaliers"),
        (99, 99, "Zauberstab der Magieerkennung"),
        (100, 100, "Zauberstab der Geheimnisse"),
    ],
    "C": [
        (1, 15, "Trank der überlegenen Heilung"),
        (16, 22, "Zauberschriftrolle (Zaubergrad 4)"),
        (23, 27, "Geschosse, +2"),
        (28, 32, "Trank des Hellsehens"),
        (33, 37, "Trank der Verkleinerung"),
        (38, 42, "Trank der gasförmigen Gestalt"),
        (43, 47, "Trank der Frostriesenstärke"),
        (48, 52, "Trank der Steinriesenstärke"),
        (53, 57, "Trank des Heldenmuts"),
        (58, 62, "Trank der Unverwundbarkeit"),
        (63, 67, "Trank des Gedankenlesens"),
        (68, 72, "Zauberschriftrolle (Zaubergrad 5)"),
        (73, 75, "Elixir der Gesundheit"),
        (76, 78, "Öl der Körperlosigkeit"),
        (79, 81, "Trank der Feuerriesenstärke"),
        (82, 84, "Quaals Feder"),
        (85, 87, "Schriftrolle des Schutzes"),
        (88, 89, "Bohnenbeutel"),
        (90, 91, "Perle der Kraft"),
        (92, 92, "Glocke des Öffnens"),
        (93, 93, "Karaffe des endlosen Wassers"),
        (94, 94, "Augen des präzisen Sehens"),
        (95, 95, "Faltboot"),
        (96, 96, "Hewards praktischer Rucksack"),
        (97, 97, "Hufeisen der Geschwindigkeit"),
        (98, 98, "Halskette der Feuerbälle"),
        (99, 99, "Anhänger der Gesundheit"),
        (100, 100, "Steine der Verständigung"),
    ],
    "D": [
        (1, 20, "Trank der höchsten Heilung"),
        (21, 30, "Trank der Unsichtbarkeit"),
        (31, 40, "Trank der Geschwindigkeit"),
        (41, 50, "Zauberschriftrolle (Zaubergrad 6)"),
        (51, 57, "Zauberschriftrolle (Zaubergrad 7)"),
        (58, 62, "Geschosse, +3"),
        (63, 67, "Öl der Schärfe"),
        (68, 72, "Trank des Fliegens"),
        (73, 77, "Trank der Wolkenriesenstärke"),
        (78, 82, "Trank der Langlebigkeit"),
        (83, 87, "Trank der Vitalität"),
        (88, 92, "Zauberschriftrolle (Zaubergrad 8)"),
        (93, 95, "Hufeisen des Zephyrs"),
        (96, 98, "Nolzurs Wunderfarben"),
        (99, 99, "Fraßbeutel"),
        (100, 100, "Tragbares Loch"),
    ],
    "E": [
        (1, 30, "Zauberschriftrolle (Zaubergrad 8)"),
        (31, 55, "Trank der Sturmriesenstärke"),
        (56, 70, "Trank der höchsten Heilung"),
        (71, 85, "Zauberschriftrolle (Zaubergrad 9)"),
        (86, 93, "Universelles Lösungsmittel"),
        (94, 98, "Pfeil des Tötens"),
        (99, 100, "Ewiger Leim"),
    ],
    "F": [
        (1, 15, "Waffe, +1"),
        (16, 18, "Schild, +1"),
        (19, 21, "Wächterschild"),
        (22, 23, "Amulett des Schutzes gegen Ortung und Ausspähung"),
        (24, 25, "Stiefel der Elfen"),
        (26, 27, "Stiefel des Schreitens und Springens"),
        (28, 29, "Armschienen des Bogenschützen"),
        (30, 31, "Brosche des Abschirmens"),
        (32, 33, "Flugbesen"),
        (34, 35, "Elfenumhang"),
        (36, 37, "Umhang des Schutzes"),
        (38, 39, "Panzerhandschuhe der Ogerkraft"),
        (40, 41, "Hut der Verkleidung"),
        (42, 43, "Wurfspeer des Blitzes"),
        (44, 45, "Perle der Macht"),
        (46, 47, "Zepter des Paktbewahrers, +1"),
        (48, 49, "Schuhe des Spinnenkletterns"),
        (50, 51, "Zauberstecken der Kreuzotter"),
        (52, 53, "Zauberstecken der Python"),
        (54, 55, "Schwert der Vergeltung"),
        (56, 57, "Dreizack der Fischherrschaft"),
        (58, 59, "Zauberstab der magischen Geschosse"),
        (60, 61, "Zauberstab des Kriegsmagiers, +1"),
        (62, 63, "Zauberstab des Netzes"),
        (64, 65, "Waffe der Warnung"),
        (66, 66, "Adamantrüstung (Kettenpanzer)"),
        (67, 67, "Adamantrüstung (Kettenhemd)"),
        (68, 68, "Adamantrüstung (Schuppenpanzer)"),
        (69, 69, "Trickbeutel (grau)"),
        (70, 70, "Trickbeutel (rostbraun)"),
        (71, 71, "Trickbeutel (hellbraun)"),
        (72, 72, "Stiefel der Winterlande"),
        (73, 73, "Diadem des Versengens"),
        (74, 74, "Karten der Illusionen"),
        (75, 75, "Rauchflasche"),
        (76, 76, "Augen der Bezauberung"),
        (77, 77, "Augen des Adlers"),
        (78, 78, "Figur der wundersamen Kraft (silberner Rabe)"),
        (79, 79, "Edelstein der Helligkeit"),
        (80, 80, "Handschuhe des Geschossfangens"),
        (81, 81, "Handschuhe des Schwimmens und Kletterns"),
        (82, 82, "Handschuhe des Diebstahls"),
        (83, 83, "Stirnband der Intelligenz"),
        (84, 84, "Helm der Telepathie"),
        (85, 85, "Bardeninstrument (Doss Laute)"),
        (86, 86, "Bardeninstrument (Fochlucan Pandora)"),
        (87, 87, "Bardeninstrument (Mac Fuimidh Cister)"),
        (88, 88, "Medaillon der Gedanken"),
        (89, 89, "Halskette der Anpassung"),
        (90, 90, "Anhänger der Wundheilung"),
        (91, 91, "Flöte des Unheimlichen"),
        (92, 92, "Flöte des Rattenfängers"),
        (93, 93, "Ring des Springens"),
        (94, 94, "Ring der Gedankenabschirmung"),
        (95, 95, "Ring der Wärme"),
        (96, 96, "Ring des Wasserlaufens"),
        (97, 97, "Ehlonnas Köcher"),
        (98, 98, "Stein des Glücks"),
        (99, 99, "Windfächer"),
        (100, 100, "Geflügelte Stiefel"),
    ],
    "G": [
        (1, 11, "Waffe, +2"),
        (12, 14, "Figur der wundersamen Kraft (W8)"),
        (15, 15, "Adamantrüstung (Brustplatte)"),
        (16, 16, "Adamantrüstung (Schienenpanzer)"),
        (17, 17, "Anhänger der Gesundheit"),
        (18, 18, "Rüstung der Verwundbarkeit"),
        (19, 19, "Pfeil fangender Schild"),
        (20, 20, "Zwergengürtel"),
        (21, 21, "Gürtel der Hügelriesenstärke"),
        (22, 22, "Berserkeraxt"),
        (23, 23, "Stiefel des Schwebens"),
        (24, 24, "Stiefel der Geschwindigkeit"),
        (25, 25, "Schale der Wasserelementar Herrschaft"),
        (26, 26, "Armschienen der Verteidigung"),
        (27, 27, "Feuerschale der Feuerelementar Herrschaft"),
        (28, 28, "Umhang des Scharlatans"),
        (29, 29, "Rauchfass der Luftelementar Herrschaft"),
        (30, 30, "Rüstung, +1 Kettenpanzer"),
        (31, 31, "Rüstung des Widerstands (Kettenpanzer)"),
        (32, 32, "Rüstung, +1 Kettenhemd"),
        (33, 33, "Rüstung des Widerstands (Kettenhemd)"),
        (34, 34, "Umhang der Verlagerung"),
        (35, 35, "Umhang der Fledermaus"),
        (36, 36, "Würfel der Kraft"),
        (37, 37, "Daerns flotte Festung"),
        (38, 38, "Dolch des Gifts"),
        (39, 39, "Dimensionsfesseln"),
        (40, 40, "Drachentöter"),
        (41, 41, "Elfenrüstung"),
        (42, 42, "Flammenzunge"),
        (43, 43, "Edelstein des Sehens"),
        (44, 44, "Riesentöter"),
        (45, 45, "Verzaubertes beschlagenes Leder"),
        (46, 46, "Helm der Teleportation"),
        (47, 47, "Horn der Sprengung"),
        (48, 48, "Horn von Valhalla (Silber oder Messing)"),
        (49, 49, "Bardeninstrument (Canaith Mandoline)"),
        (50, 50, "Bardeninstrument (Cli Leier)"),
        (51, 51, "Ionenstein (Wahrnehmung)"),
        (52, 52, "Ionenstein (Schutz)"),
        (53, 53, "Ionenstein (Reserve)"),
        (54, 54, "Ionenstein (Ernährung)"),
        (55, 55, "Eisenbänder von Bilarro"),
        (56, 56, "Rüstung, +1 Leder"),
        (57, 57, "Rüstung des Widerstands (Leder)"),
        (58, 58, "Streitkolben des Zusammenbruchs"),
        (59, 59, "Streitkolben des Niederstreckens"),
        (60, 60, "Streitkolben des Terrors"),
        (61, 61, "Mantel des Zauberwiderstands"),
        (62, 62, "Halskette der Gebetsperlen"),
        (63, 63, "Anhänger des Giftschutzes"),
        (64, 64, "Ring des Tierumgangs"),
        (65, 65, "Ring des Ausweichens"),
        (66, 66, "Ring des Federfalls"),
        (67, 67, "Ring der Bewegungsfreiheit"),
        (68, 68, "Ring des Schutzes"),
        (69, 69, "Ring des Widerstands"),
        (70, 70, "Ring des Zauberspeichers"),
        (71, 71, "Ring des Widders"),
        (72, 72, "Ring des Röntgenblicks"),
        (73, 73, "Robe der Augen"),
        (74, 74, "Zepter der Herrschaft"),
        (75, 75, "Zepter des Paktbewahrers, +2"),
        (76, 76, "Fesselseil"),
        (77, 77, "Rüstung, +1 Schuppenpanzer"),
        (78, 78, "Rüstung des Widerstands (Schuppenpanzer)"),
        (79, 79, "Schild, +2"),
        (80, 80, "Schild der Geschossanziehung"),
        (81, 81, "Zauberstecken der Bezauberung"),
        (82, 82, "Zauberstecken der Heilung"),
        (83, 83, "Zauberstecken der Insektenschwärme"),
        (84, 84, "Zauberstecken der Waldlande"),
        (85, 85, "Zauberstecken der Verkümmerung"),
        (86, 86, "Stein der Erdelementar Herrschaft"),
        (87, 87, "Sonnenklinge"),
        (88, 88, "Schwert des Lebensentzugs"),
        (89, 89, "Schwert der Verwundung"),
        (90, 90, "Tentakelzepter"),
        (91, 91, "Bösartige Waffe"),
        (92, 92, "Zauberstab der Bindung"),
        (93, 93, "Zauberstab der Feindeslokalisierung"),
        (94, 94, "Zauberstab der Angst"),
        (95, 95, "Zauberstab der Feuerbälle"),
        (96, 96, "Zauberstab der Blitzschläge"),
        (97, 97, "Zauberstab der Paralyse"),
        (98, 98, "Zauberstab des Kriegsmagiers, +2"),
        (99, 99, "Zauberstab des Wunders"),
        (100, 100, "Zauberflügel"),
    ],
    "H": [
        (1, 10, "Waffe, +3"),
        (11, 12, "Amulett der Ebenen"),
        (13, 14, "Fliegender Teppich"),
        (15, 16, "Kristallkugel (sehr seltene Version)"),
        (17, 18, "Ring der Regeneration"),
        (19, 20, "Ring der Sternschnuppen"),
        (21, 22, "Ring der Telekinese"),
        (23, 24, "Robe der schillernden Farben"),
        (25, 26, "Robe der Sterne"),
        (27, 28, "Zepter der Absorption"),
        (29, 30, "Zepter der Wachsamkeit"),
        (31, 32, "Zepter der Sicherheit"),
        (33, 34, "Zepter des Paktbewahrers, +3"),
        (35, 36, "Krummsäbel der Geschwindigkeit"),
        (37, 38, "Schild, +3"),
        (39, 40, "Zauberstecken des Feuers"),
        (41, 42, "Zauberstecken des Frostes"),
        (43, 44, "Zauberstecken der Macht"),
        (45, 46, "Zauberstecken des Schlagens"),
        (47, 48, "Zauberstecken des Donners und Blitzes"),
        (49, 50, "Schwert der Schärfe"),
        (51, 52, "Zauberstab der Verwandlung"),
        (53, 54, "Zauberstab des Kriegsmagiers, +3"),
        (55, 55, "Adamantrüstung (Plattenpanzer)"),
        (56, 56, "Adamantrüstung (Ritterrüstung)"),
        (57, 57, "Belebter Schild"),
        (58, 58, "Gürtel der Feuerriesenstärke"),
        (59, 59, "Gürtel der Frost oder Steinriesenstärke"),
        (60, 60, "Rüstung, +1 Brustplatte"),
        (61, 61, "Rüstung des Widerstands (Brustplatte)"),
        (62, 62, "Kerze der Anrufung"),
        (63, 63, "Rüstung, +2 Kettenpanzer"),
        (64, 64, "Rüstung, +2 Kettenhemd"),
        (65, 65, "Umhang der Spinnentiere"),
        (66, 66, "Tanzendes Schwert"),
        (67, 67, "Dämonenrüstung"),
        (68, 68, "Drachenschuppen Panzer"),
        (69, 69, "Zwergische Ritterrüstung"),
        (70, 70, "Zwergischer Wurfhammer"),
        (71, 71, "Irit Flasche"),
        (72, 72, "Figur der wundersamen Kraft (Obsidianpferd)"),
        (73, 73, "Frostbrand"),
        (74, 74, "Helm der Pracht"),
        (75, 75, "Horn von Valhalla (Bronze)"),
        (76, 76, "Bardeninstrument (Anstruth Harfe)"),
        (77, 77, "Ionenstein (Absorption)"),
        (78, 78, "Ionenstein (Agilität)"),
        (79, 79, "Ionenstein (Standhaftigkeit)"),
        (80, 80, "Ionenstein (Erkenntnis)"),
        (81, 81, "Ionenstein (Verstand)"),
        (82, 82, "Ionenstein (Führungskraft)"),
        (83, 83, "Ionenstein (Stärke)"),
        (84, 84, "Rüstung, +2 Leder"),
        (85, 85, "Handbuch der körperlichen Gesundheit"),
        (86, 86, "Handbuch der körperlichen Ertüchtigung"),
        (87, 87, "Handbuch der Golems"),
        (88, 88, "Handbuch des schnellen Handelns"),
        (89, 89, "Seelenspiegel"),
        (90, 90, "Dieb der neun Leben"),
        (91, 91, "Schwurbogen"),
        (92, 92, "Rüstung, +2 Schuppenpanzer"),
        (93, 93, "Zauberabwehrschild"),
        (94, 94, "Rüstung, +2 Schienenpanzer"),
        (95, 95, "Rüstung des Widerstands (Schienenpanzer)"),
        (96, 96, "Rüstung, +1 beschlagenes Leder"),
        (97, 97, "Rüstung des Widerstands (beschlagenes Leder)"),
        (98, 98, "Leitfaden des klaren Denkens"),
        (99, 99, "Leitfaden der Führungskraft und der Einflussnahme"),
        (100, 100, "Leitfaden des Verständnisses"),
    ],
    "I": [
        (1, 5, "Verteidiger"),
        (6, 10, "Hammer des Blitzschlags"),
        (11, 15, "Glücksklinge"),
        (16, 20, "Schwert der Antwort"),
        (21, 23, "Heiliger Rächer"),
        (24, 26, "Ring der Djinni Beschwörung"),
        (27, 29, "Ring der Unsichtbarkeit"),
        (30, 32, "Ring des Zauberwendens"),
        (33, 35, "Zepter der herrschaftlichen Macht"),
        (36, 38, "Zauberstecken der Magi"),
        (39, 41, "Herrscherschwert"),
        (42, 43, "Gürtel der Wolkenriesenstärke"),
        (44, 45, "Rüstung, +2 Brustplatte"),
        (46, 47, "Rüstung, +3 Kettenpanzer"),
        (48, 49, "Rüstung, +3 Kettenhemd"),
        (50, 51, "Umhang der Unsichtbarkeit"),
        (52, 53, "Kristallkugel (legendär)"),
        (54, 55, "Rüstung, +1 Plattenpanzer"),
        (56, 57, "Eiserne Flasche"),
        (58, 59, "Rüstung, +3 Leder"),
        (60, 61, "Rüstung, +1 Ritterrüstung"),
        (62, 63, "Robe der Erzmagier"),
        (64, 65, "Zepter der Auferstehung"),
        (66, 67, "Rüstung, +1 Schuppenpanzer"),
        (68, 69, "Skarabäus des Schutzes"),
        (70, 71, "Rüstung, +2 Schienenpanzer"),
        (72, 73, "Rüstung, +2 beschlagenes Leder"),
        (74, 75, "Brunnen der vielen Welten"),
        (76, 76, "Magische Rüstung (W12)"),
        (77, 77, "Der Apparat von Kwalish"),
        (78, 78, "Rüstung der Unverwundbarkeit"),
        (79, 79, "Gürtel der Sturmriesenstärke"),
        (80, 80, "Würfel der Ebenen"),
        (81, 81, "Schicksalskarten"),
        (82, 82, "Irit Rüstung"),
        (83, 83, "Rüstung des Widerstands (Plattenpanzer)"),
        (84, 84, "Horn von Valhalla (Eisen)"),
        (85, 85, "Bardeninstrument (Ollamh Harfe)"),
        (86, 86, "Ionenstein (Höhere Absorption)"),
        (87, 87, "Ionenstein (Meisterschaft)"),
        (88, 88, "Ionenstein (Regeneration)"),
        (89, 89, "Ritterrüstung der Körperlosigkeit"),
        (90, 90, "Ritterrüstung des Widerstands"),
        (91, 91, "Ring der Luftelementar Herrschaft"),
        (92, 92, "Ring der Erdelementar Herrschaft"),
        (93, 93, "Ring der Feuerelementar Herrschaft"),
        (94, 94, "Ring der drei Wünsche"),
        (95, 95, "Ring der Wasserelementar Herrschaft"),
        (96, 96, "Sphäre des Nichts"),
        (97, 97, "Talisman des reinen Guten"),
        (98, 98, "Talisman des Nichts"),
        (99, 99, "Talisman des absolut Bösen"),
        (100, 100, "Leitfaden der verstummten Sprache 🙂"),
    ],
}

FIGURINES_W8 = {
    1: "Bronze Greif",
    2: "Ebenholz Fliege",
    3: "Goldene Löwen",
    4: "Elfenbein Ziegen",
    5: "Marmor Elefant",
    6: "Onyx Hund",
    7: "Onyx Hund",
    8: "Serpentin Eule",
}

MAGIC_ARMOR_W12 = {
    1: "Rüstung +2 Plattenpanzer",
    2: "Rüstung +2 Plattenpanzer",
    3: "Rüstung +2 Ritterrüstung",
    4: "Rüstung +2 Ritterrüstung",
    5: "Rüstung +3 beschlagenes Leder",
    6: "Rüstung +3 beschlagenes Leder",
    7: "Rüstung +3 Brustplatte",
    8: "Rüstung +3 Brustplatte",
    9: "Rüstung +3 Schienenpanzer",
    10: "Rüstung +3 Schienenpanzer",
    11: "Rüstung +3 Plattenpanzer",
    12: "Rüstung +3 Ritterrüstung",
}

def _pick_magic_item(table_letter: str) -> Tuple[int, str, List[str]]:
    r = random.randint(1, 100)
    entries = MAGIC_TABLES.get(table_letter)
    if not entries:
        return r, f"Unbekannte Tabelle {table_letter}", []

    item = "Unbekannt"
    for a, b, txt in entries:
        if a <= r <= b:
            item = txt
            break

    extra_details: List[str] = []
    if table_letter == "G" and "Figur der wundersamen Kraft (W8)" in item:
        r8 = random.randint(1, 8)
        item = f"Figur der wundersamen Kraft ({FIGURINES_W8[r8]})"
        extra_details.append(f"W8 Figur: {r8} -> {FIGURINES_W8[r8]}")

    if table_letter == "I" and "Magische Rüstung (W12)" in item:
        r12 = random.randint(1, 12)
        item = f"Magische Rüstung ({MAGIC_ARMOR_W12[r12]})"
        extra_details.append(f"W12 Rüstung: {r12} -> {MAGIC_ARMOR_W12[r12]}")

    return r, item, extra_details

# Hort Münzen je HG
HOARD_COINS: Dict[str, List[Tuple[str, int, int, int]]] = {
    "0-4": [("KM", 6, 6, 100), ("SM", 3, 6, 100), ("GM", 2, 6, 10)],
    "5-10": [("KM", 2, 6, 100), ("SM", 2, 6, 1000), ("GM", 6, 6, 100), ("PM", 3, 6, 10)],
    "11-16": [("KM", 2, 6, 100), ("SM", 2, 6, 1000), ("GM", 6, 6, 100), ("PM", 3, 6, 10)],
    "17+": [("GM", 12, 6, 1000), ("PM", 8, 6, 1000)],
}

# Hort Zusatztabelle je HG
# GemArt: ("gems"|"art", (dice_count, dice_sides), value_each_gm)
# MagicRolls: List[ (table_letter, count_expr) ] count_expr "1" oder "1W4" etc
HOARD_LOOT: Dict[str, List[Tuple[int, int, Optional[Tuple[str, Tuple[int, int], int]], List[Tuple[str, str]]]]] = {
    "0-4": [
        (1, 6, None, []),
        (7, 16, ("gems", (2, 6), 10), []),
        (17, 26, ("art", (2, 4), 25), []),
        (27, 36, ("gems", (2, 6), 50), []),
        (37, 44, ("gems", (2, 6), 10), [("A", "1W6")]),
        (45, 52, ("art", (2, 4), 25), [("A", "1W6")]),
        (53, 60, ("gems", (2, 6), 50), [("A", "1W6")]),
        (61, 65, ("gems", (2, 6), 10), [("B", "1W4")]),
        (66, 70, ("art", (2, 4), 25), [("B", "1W4")]),
        (71, 75, ("gems", (2, 6), 50), [("B", "1W4")]),
        (76, 78, ("gems", (2, 6), 10), [("C", "1W4")]),
        (79, 80, ("art", (2, 4), 25), [("C", "1W4")]),
        (81, 85, ("gems", (2, 6), 50), [("C", "1W4")]),
        (86, 92, ("art", (2, 4), 25), [("F", "1W4")]),
        (93, 97, ("gems", (2, 6), 50), [("F", "1W4")]),
        (98, 99, ("art", (2, 4), 25), [("G", "1")]),
        (100, 100, ("gems", (2, 6), 50), [("G", "1")]),
    ],
    "5-10": [
        (1, 4, None, []),
        (5, 10, ("art", (2, 4), 25), []),
        (11, 16, ("gems", (3, 6), 50), []),
        (17, 22, ("gems", (3, 6), 100), []),
        (23, 28, ("art", (2, 4), 250), []),
        (29, 32, ("art", (2, 4), 25), [("A", "1W6")]),
        (33, 36, ("gems", (3, 6), 50), [("A", "1W6")]),
        (37, 40, ("gems", (3, 6), 100), [("A", "1W6")]),
        (41, 44, ("art", (2, 4), 250), [("A", "1W6")]),
        (45, 49, ("art", (2, 4), 25), [("B", "1W4")]),
        (50, 54, ("gems", (3, 6), 50), [("B", "1W4")]),
        (55, 59, ("gems", (3, 6), 100), [("B", "1W4")]),
        (60, 63, ("art", (2, 4), 250), [("B", "1W4")]),
        (64, 66, ("art", (2, 4), 25), [("C", "1W4")]),
        (67, 69, ("gems", (3, 6), 50), [("C", "1W4")]),
        (70, 72, ("gems", (3, 6), 100), [("C", "1W4")]),
        (73, 74, ("art", (2, 4), 250), [("C", "1W4")]),
        (75, 76, ("art", (2, 4), 25), [("D", "1")]),
        (77, 78, ("gems", (3, 6), 50), [("D", "1")]),
        (79, 79, ("gems", (3, 6), 100), [("D", "1")]),
        (80, 80, ("art", (2, 4), 250), [("D", "1")]),
        (81, 84, ("art", (2, 4), 25), [("F", "1W4")]),
        (85, 88, ("gems", (3, 6), 50), [("F", "1W4")]),
        (89, 91, ("gems", (3, 6), 100), [("F", "1W4")]),
        (92, 94, ("art", (2, 4), 250), [("F", "1W4")]),
        (95, 96, ("gems", (3, 6), 100), [("G", "1W4")]),
        (97, 98, ("art", (2, 4), 250), [("G", "1W4")]),
        (99, 99, ("gems", (3, 6), 100), [("H", "1W4")]),
        (100, 100, ("art", (2, 4), 250), [("H", "1W4")]),
    ],
    "11-16": [
        (1, 3, None, []),
        (4, 6, ("art", (2, 4), 250), []),
        (7, 9, ("art", (2, 4), 750), []),
        (10, 12, ("gems", (3, 6), 500), []),
        (13, 15, ("gems", (3, 6), 1000), []),
        (16, 19, ("art", (2, 4), 250), [("A", "1W4"), ("B", "1W6")]),
        (20, 23, ("art", (2, 4), 750), [("A", "1W4"), ("B", "1W6")]),
        (24, 26, ("gems", (3, 6), 500), [("A", "1W4"), ("B", "1W6")]),
        (27, 29, ("gems", (3, 6), 1000), [("A", "1W4"), ("B", "1W6")]),
        (30, 35, ("art", (2, 4), 250), [("C", "1W6")]),
        (36, 40, ("art", (2, 4), 750), [("C", "1W6")]),
        (41, 45, ("gems", (3, 6), 500), [("C", "1W6")]),
        (46, 50, ("gems", (3, 6), 1000), [("C", "1W6")]),
        (51, 54, ("art", (2, 4), 250), [("D", "1W4")]),
        (55, 58, ("art", (2, 4), 750), [("D", "1W4")]),
        (59, 62, ("gems", (3, 6), 500), [("D", "1W4")]),
        (63, 66, ("gems", (3, 6), 1000), [("D", "1W4")]),
        (67, 68, ("art", (2, 4), 250), [("E", "1")]),
        (69, 70, ("art", (2, 4), 750), [("E", "1")]),
        (71, 72, ("gems", (3, 6), 500), [("E", "1")]),
        (73, 74, ("gems", (3, 6), 1000), [("E", "1")]),
        (75, 76, ("art", (2, 4), 250), [("F", "1"), ("G", "1W4")]),
        (77, 78, ("art", (2, 4), 750), [("F", "1"), ("G", "1W4")]),
        (79, 80, ("gems", (3, 6), 500), [("F", "1"), ("G", "1W4")]),
        (81, 82, ("gems", (3, 6), 1000), [("F", "1"), ("G", "1W4")]),
        (83, 85, ("art", (2, 4), 250), [("H", "1W4")]),
        (86, 88, ("art", (2, 4), 750), [("H", "1W4")]),
        (89, 90, ("gems", (3, 6), 500), [("H", "1W4")]),
        (91, 92, ("gems", (3, 6), 1000), [("H", "1W4")]),
        (93, 94, ("art", (2, 4), 250), [("I", "1")]),
        (95, 96, ("art", (2, 4), 750), [("I", "1")]),
        (97, 98, ("gems", (3, 6), 500), [("I", "1")]),
        (99, 100, ("gems", (3, 6), 1000), [("I", "1")]),
    ],
    "17+": [
        (1, 2, None, []),
        (3, 5, ("gems", (3, 6), 1000), [("C", "1W8")]),
        (6, 8, ("art", (1, 10), 2500), [("C", "1W8")]),
        (9, 11, ("art", (1, 4), 7500), [("C", "1W8")]),
        (12, 14, ("gems", (1, 8), 5000), [("C", "1W8")]),
        (15, 22, ("gems", (3, 6), 1000), [("D", "1W6")]),
        (23, 30, ("art", (1, 10), 2500), [("D", "1W6")]),
        (31, 38, ("art", (1, 4), 7500), [("D", "1W6")]),
        (39, 46, ("gems", (1, 8), 5000), [("D", "1W6")]),
        (47, 52, ("gems", (3, 6), 1000), [("E", "1W6")]),
        (53, 58, ("art", (1, 10), 2500), [("E", "1W6")]),
        (59, 63, ("art", (1, 4), 7500), [("E", "1W6")]),
        (64, 68, ("gems", (1, 8), 5000), [("E", "1W6")]),
        (69, 69, ("gems", (3, 6), 1000), [("G", "1W4")]),
        (70, 70, ("art", (1, 10), 2500), [("G", "1W4")]),
        (71, 71, ("art", (1, 4), 7500), [("G", "1W4")]),
        (72, 72, ("gems", (1, 8), 5000), [("G", "1W4")]),
        (73, 74, ("gems", (3, 6), 1000), [("H", "1W4")]),
        (75, 76, ("art", (1, 10), 2500), [("H", "1W4")]),
        (77, 78, ("art", (1, 4), 7500), [("H", "1W4")]),
        (79, 80, ("gems", (1, 8), 5000), [("H", "1W4")]),
        (81, 85, ("gems", (3, 6), 1000), [("I", "1W4")]),
        (86, 90, ("art", (1, 10), 2500), [("I", "1W4")]),
        (91, 95, ("art", (1, 4), 7500), [("I", "1W4")]),
        (96, 100, ("gems", (1, 8), 5000), [("I", "1W4")]),
    ],
}

def _cr_label(cr_key: str) -> str:
    if cr_key == "0-4":
        return "0 bis 4"
    if cr_key == "5-10":
        return "5 bis 10"
    if cr_key == "11-16":
        return "11 bis 16"
    return "17+"

def build_treasure_kind_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Schatzhort", callback_data="treasure_kind:hoard"),
         InlineKeyboardButton("Einzelschatz", callback_data="treasure_kind:individual")],
        [InlineKeyboardButton("Abbrechen", callback_data="treasure_cancel")],
    ]
    return InlineKeyboardMarkup(rows)

def build_treasure_cr_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("HG 0 bis 4", callback_data="treasure_cr:0-4"),
         InlineKeyboardButton("HG 5 bis 10", callback_data="treasure_cr:5-10")],
        [InlineKeyboardButton("HG 11 bis 16", callback_data="treasure_cr:11-16"),
         InlineKeyboardButton("HG 17+", callback_data="treasure_cr:17+")],
        [InlineKeyboardButton("Abbrechen", callback_data="treasure_cancel")],
    ]
    return InlineKeyboardMarkup(rows)

def _roll_individual_treasure(cr_key: str) -> Tuple[str, List[str]]:
    w100 = random.randint(1, 100)
    table = INDIVIDUAL_TREASURE[cr_key]
    specs = _pick_range_table([(a, b, payload) for a, b, payload in table], w100) or []
    totals: Dict[str, int] = {k: 0 for k in COIN_ORDER}
    details: List[str] = []

    for coin, c, s, m in specs:
        amount, det = _roll_coin_spec(coin, c, s, m)
        totals[coin] += amount
        details.append(det)

    lines = []
    for coin in COIN_ORDER:
        if totals.get(coin, 0) > 0:
            lines.append(f"{coin}: {_fmt_int(totals[coin])}")
    if not lines:
        lines.append("Keine Münzen")

    msg = (
        f"💰 Rollschatz\n"
        f"Art: Einzelschatz\n"
        f"Herausforderungsgrad: {_cr_label(cr_key)}\n"
        f"W100: {_fmt_w100(w100)}\n\n"
        f"Ergebnis:\n" + "\n".join(lines)
    )

    if details:
        msg += "\n\nWürfe:\n" + "\n".join(details)

    return msg, details

def _roll_gem_or_art(spec: Tuple[str, Tuple[int, int], int]) -> Tuple[str, List[str]]:
    kind, (dc, ds), value_each = spec
    count, rolls = _roll_nds(dc, ds)
    total_value = count * value_each
    kind_label = "Edelsteine" if kind == "gems" else "Kunstgegenstände"
    detail = f"{dc}W{ds} = {count} (Würfe: {', '.join(map(str, rolls))})"
    line = f"{kind_label}: {count} Stück á {_fmt_int(value_each)} GM = {_fmt_int(total_value)} GM"
    return line, [detail]

def _roll_hoard_treasure(cr_key: str) -> str:
    coin_specs = HOARD_COINS[cr_key]
    coin_totals: Dict[str, int] = {k: 0 for k in COIN_ORDER}
    coin_details: List[str] = []

    for coin, c, s, m in coin_specs:
        amount, det = _roll_coin_spec(coin, c, s, m)
        coin_totals[coin] += amount
        coin_details.append(det)

    w100 = random.randint(1, 100)
    loot_table = HOARD_LOOT[cr_key]
    payload = _pick_range_table([(a, b, (gem_art, magic)) for a, b, gem_art, magic in loot_table], w100)
    if payload is None:
        gem_art = None
        magic_rolls: List[Tuple[str, str]] = []
    else:
        gem_art, magic_rolls = payload

    lines = [f"💰 Rollschatz", f"Art: Schatzhort", f"Herausforderungsgrad: {_cr_label(cr_key)}", ""]
    coin_lines = []
    for coin in COIN_ORDER:
        if coin_totals.get(coin, 0) > 0:
            coin_lines.append(f"{coin}: {_fmt_int(coin_totals[coin])}")
    if not coin_lines:
        coin_lines.append("Keine Münzen")

    lines.append("Münzen:")
    lines.extend(coin_lines)
    lines.append("")
    lines.append(f"W100: {_fmt_w100(w100)}")

    extra_details: List[str] = []

    if gem_art is None:
        lines.append("Edelsteine oder Kunstgegenstände: keine")
    else:
        gem_line, gem_details = _roll_gem_or_art(gem_art)
        lines.append("Edelsteine oder Kunstgegenstände:")
        lines.append(gem_line)
        extra_details.extend(gem_details)

    magic_items: List[str] = []
    magic_details: List[str] = []

    if not magic_rolls:
        lines.append("Magische Gegenstände: keine")
    else:
        lines.append("Magische Gegenstände:")
        for table_letter, count_expr in magic_rolls:
            n, n_detail = _roll_count_expr(count_expr)
            magic_details.append(f"Tabelle {table_letter}: {n_detail}")
            for _ in range(max(0, n)):
                r_item, item, extra = _pick_magic_item(table_letter)
                magic_items.append(f"Tabelle {table_letter} W100 {_fmt_w100(r_item)}: {item}")
                magic_details.extend(extra)

        if magic_items:
            lines.extend(magic_items)
        else:
            lines.append("Keine magischen Gegenstände")

    out = "\n".join(lines)

    all_details: List[str] = []
    all_details.extend(coin_details)
    if extra_details:
        all_details.append("Zusatzzahlen:")
        all_details.extend(extra_details)
    if magic_details:
        all_details.append("Magie Würfe:")
        all_details.extend(magic_details)

    if all_details:
        out += "\n\nWürfe:\n" + "\n".join(all_details)

    return out

async def rollschatz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("treasure_kind", None)
    context.user_data.pop("treasure_cr", None)
    await update.message.reply_text("💰 Rollschatz\nWas willst du würfeln?", reply_markup=build_treasure_kind_keyboard())
    return TREASURE_KIND_STATE

async def rollschatz_pick_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind = query.data.split(":", 1)[1].strip()
    if kind not in {"hoard", "individual"}:
        await query.edit_message_text("Ungültige Auswahl. Nutze /rollschatz erneut 🙂")
        return ConversationHandler.END

    context.user_data["treasure_kind"] = kind
    await query.edit_message_text("Welcher Herausforderungsgrad?", reply_markup=build_treasure_cr_keyboard())
    return TREASURE_CR_STATE

async def rollschatz_pick_cr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cr_key = query.data.split(":", 1)[1].strip()
    if cr_key not in {"0-4", "5-10", "11-16", "17+"}:
        await query.edit_message_text("Ungültiger Herausforderungsgrad. Nutze /rollschatz erneut 🙂")
        return ConversationHandler.END

    kind = context.user_data.get("treasure_kind", "individual")
    context.user_data["treasure_cr"] = cr_key

    if kind == "hoard":
        msg = _roll_hoard_treasure(cr_key)
    else:
        msg, _ = _roll_individual_treasure(cr_key)

    await query.edit_message_text(msg)
    return ConversationHandler.END

async def rollschatz_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Schatz abgebrochen 🙂")
    return ConversationHandler.END

async def rollschatz_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Schatz abgebrochen 🙂")
    return ConversationHandler.END


# -----------------------
# HELP
# -----------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧰 Befehle\n\n"
        "/help  diese Hilfe\n"
        "/roll <Ausdruck>  Würfeln, z.B. /roll 1d6 oder /roll 2d20+3 oder /roll 1d20+2d6+3 (auch 1w6)\n"
        "/rollchance  Skillwurf plus SG und Belohnung\n"
        "/rollhunt  Jagdwurf mit Mod Auswahl\n"
        "/rollwaldkarte  zieht eine Waldkarte (Skillchance, Ruhe, Entdeckung, Encounter, Hort, NPC)\n"
        "/rollplayerbehaviour  würfelt Rollplayer Behaviour (1W6)\n"
        "/rollschatz  würfelt Schatz (Schatzhort oder Einzelschatz) nach Herausforderungsgrad\n\n"
        "🌍 Biom\n"
        "/setbiom <Biom>  setzt dein aktuelles Biom (oder ohne Parameter per Buttons)\n"
        "/biom  zeigt dein aktuelles Biom\n"
        "/rollbiom [Biom]  würfelt das nächste Biom (optional vorher setzen)\n\n"
        "⚔️ Encounters\n"
        "/rollencounter [Biom]  würfelt einen Encounter (nutzt sonst dein aktuelles Biom)\n"
        "/encdebug  zeigt welche Encounter Tabellen wirklich geladen wurden\n\n"
        "🔮 Orakel\n"
        "/rolloracle [Frage]  Ja Nein Orakel\n"
        "/cancel  bricht Orakel, Encounter oder Schatz Auswahl ab"
    )
    await update.message.reply_text(msg)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    base_url = os.environ.get("BASE_URL")
    port = int(os.environ.get("PORT", "10000"))

    if not token or not base_url:
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder BASE_URL fehlt")

    base_url = base_url.rstrip("/")
    app = Application.builder().token(token).build()

    init_encounters()

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start", help_cmd))

    app.add_handler(CommandHandler("roll", roll))
    app.add_handler(CommandHandler("rollchance", rollchance))

    app.add_handler(CommandHandler("rollhunt", rollhunt))
    app.add_handler(CallbackQueryHandler(rollhunt_pick_mod, pattern=r"^hunt_mod:"))
    app.add_handler(CallbackQueryHandler(rollhunt_cancel_cb, pattern=r"^hunt_cancel$"))

    app.add_handler(CommandHandler("rollwaldkarte", rollwaldkarte))
    app.add_handler(CallbackQueryHandler(rollwaldkarte_pick_level, pattern=r"^waldkarte_level:"))

    app.add_handler(CommandHandler("rollplayerbehaviour", rollplayerbehaviour))

    app.add_handler(CommandHandler("setbiom", setbiom))
    app.add_handler(CommandHandler("biom", biom))
    app.add_handler(CommandHandler("rollbiom", rollbiom))
    app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))

    app.add_handler(CommandHandler("encdebug", encdebug))

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

    treasure_conv = ConversationHandler(
        entry_points=[CommandHandler("rollschatz", rollschatz_start)],
        states={
            TREASURE_KIND_STATE: [
                CallbackQueryHandler(rollschatz_pick_kind, pattern=r"^treasure_kind:"),
                CallbackQueryHandler(rollschatz_cancel_cb, pattern=r"^treasure_cancel$"),
            ],
            TREASURE_CR_STATE: [
                CallbackQueryHandler(rollschatz_pick_cr, pattern=r"^treasure_cr:"),
                CallbackQueryHandler(rollschatz_cancel_cb, pattern=r"^treasure_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", rollschatz_cancel)],
        allow_reentry=True,
    )
    app.add_handler(treasure_conv)

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=f"{base_url}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
