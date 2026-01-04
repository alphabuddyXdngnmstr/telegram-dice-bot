import os
import random
import re
import math
import html
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
# ROLLPLAYERBEHAVIOUR SYSTEM
# -----------------------

PLAYER_BEHAVIOUR_TABLE = {
    1: (
        "Chaotisch Dumm",
        "Du stehst vor einer Tür, was tust du? Schlüssel gegen Tür werfen!"
    ),
    2: (
        "Chaotisch",
        "Du stehst vor einer Tür, was tust du? Schlüssel gegen Tür werfen und auf das Schloss zielen!"
    ),
    3: (
        "Neutral, Neutral",
        "Du stehst vor einer Tür, was tust du? Schlüssel ins Schloss stecken."
    ),
    4: (
        "Neutral Prüfend",
        "Du stehst vor einer Tür, was tust du? An der Tür stehen, Schlüssel betrachten und ins Schloss stecken."
    ),
    5: (
        "Logisch",
        "Du stehst vor einer Tür, was tust du? An der Tür lauschen und Entscheidung treffen, ggf die Tür zu öffnen."
    ),
    6: (
        "Logisch Intelligent",
        "Du stehst vor einer Tür, was tust du? Lauschen, durch das Schlüsselloch schauen und vorbereiten."
    ),
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
# ROLLSCHATZ SYSTEM
# -----------------------

TREASURE_PICK_TYPE, TREASURE_PICK_CR = range(2)

TREASURE_TYPES = [
    ("Einzeln", "single"),
    ("Hort", "hoard"),
]

TREASURE_CR_BANDS = ["0-4", "5-10", "11-16", "17+"]

COIN_KEYS = ["KM", "SM", "EM", "GM", "PM"]

def _fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", ".")

def _fmt_w100(n: int) -> str:
    return "00" if n == 100 else f"{n:02d}"

def _roll_scaled(count: int, sides: int, mult: int = 1) -> Tuple[int, List[int]]:
    rolls = [random.randint(1, sides) for _ in range(count)]
    return sum(rolls) * mult, rolls

def _roll_count(count: int, sides: int) -> Tuple[int, List[int]]:
    rolls = [random.randint(1, sides) for _ in range(count)]
    return sum(rolls), rolls

def _coin_line(cur: str, count: int, sides: int, mult: int) -> Tuple[int, str]:
    val, rolls = _roll_scaled(count, sides, mult)
    mult_txt = f" x {_fmt_num(mult)}" if mult != 1 else ""
    if count == 1:
        detail = f"{count}W{sides}{mult_txt} = {_fmt_num(val)} (Wurf: {rolls[0]})"
    else:
        detail = f"{count}W{sides}{mult_txt} = {_fmt_num(val)} (Würfe: {', '.join(map(str, rolls))})"
    return val, f"{cur}: {detail}"

def build_treasure_type_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("💰 Einzeln", callback_data="treasure_type:single"),
            InlineKeyboardButton("🏺 Hort", callback_data="treasure_type:hoard"),
        ],
        [InlineKeyboardButton("Abbrechen", callback_data="treasure_cancel")],
    ]
    return InlineKeyboardMarkup(rows)

def build_treasure_cr_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("HG 0-4", callback_data="treasure_cr:0-4"), InlineKeyboardButton("HG 5-10", callback_data="treasure_cr:5-10")],
        [InlineKeyboardButton("HG 11-16", callback_data="treasure_cr:11-16"), InlineKeyboardButton("HG 17+", callback_data="treasure_cr:17+")],
        [InlineKeyboardButton("Abbrechen", callback_data="treasure_cancel")],
    ]
    return InlineKeyboardMarkup(rows)

SINGLE_TREASURE_TABLE: Dict[str, List[Tuple[int, int, List[Tuple[str, int, int, int]]]]] = {
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

HOARD_COIN_BASE: Dict[str, List[Tuple[str, int, int, int]]] = {
    "0-4": [("KM", 6, 6, 100), ("SM", 3, 6, 100), ("GM", 2, 6, 10)],
    "5-10": [("KM", 2, 6, 100), ("SM", 2, 6, 1000), ("GM", 6, 6, 100), ("PM", 3, 6, 10)],
    "11-16": [("KM", 2, 6, 100), ("SM", 2, 6, 1000), ("GM", 6, 6, 100), ("PM", 3, 6, 10)],
    "17+": [("GM", 12, 6, 1000), ("PM", 8, 6, 1000)],
}

# gem_art: (kind, count_dice_count, count_dice_sides, count_mult, value_each_gp)
# magic: list of (table_letter, count_dice_count, count_dice_sides, count_mult)
HOARD_EXTRA_TABLE: Dict[str, List[Tuple[int, int, Optional[Tuple[str, int, int, int, int]], Optional[List[Tuple[str, int, int, int]]]]]] = {
    "0-4": [
        (1, 6, None, None),
        (7, 16, ("Edelsteine", 2, 6, 1, 10), None),
        (17, 26, ("Kunstgegenstände", 2, 4, 1, 25), None),
        (27, 36, ("Edelsteine", 2, 6, 1, 50), None),
        (37, 44, ("Edelsteine", 2, 6, 1, 10), [("A", 1, 6, 1)]),
        (45, 52, ("Kunstgegenstände", 2, 4, 1, 25), [("A", 1, 6, 1)]),
        (53, 60, ("Edelsteine", 2, 6, 1, 50), [("A", 1, 6, 1)]),
        (61, 65, ("Edelsteine", 2, 6, 1, 10), [("B", 1, 4, 1)]),
        (66, 70, ("Kunstgegenstände", 2, 4, 1, 25), [("B", 1, 4, 1)]),
        (71, 75, ("Edelsteine", 2, 6, 1, 50), [("B", 1, 4, 1)]),
        (76, 78, ("Edelsteine", 2, 6, 1, 10), [("C", 1, 4, 1)]),
        (79, 80, ("Kunstgegenstände", 2, 4, 1, 25), [("C", 1, 4, 1)]),
        (81, 85, ("Edelsteine", 2, 6, 1, 50), [("C", 1, 4, 1)]),
        (86, 92, ("Kunstgegenstände", 2, 4, 1, 25), [("F", 1, 4, 1)]),
        (93, 97, ("Edelsteine", 2, 6, 1, 50), [("F", 1, 4, 1)]),
        (98, 99, ("Kunstgegenstände", 2, 4, 1, 25), [("G", 1, 1, 1)]),
        (100, 100, ("Edelsteine", 2, 6, 1, 50), [("G", 1, 1, 1)]),
    ],
    "5-10": [
        (1, 4, None, None),
        (5, 10, ("Kunstgegenstände", 2, 4, 1, 25), None),
        (11, 16, ("Edelsteine", 3, 6, 1, 50), None),
        (17, 22, ("Edelsteine", 3, 6, 1, 100), None),
        (23, 28, ("Kunstgegenstände", 2, 4, 1, 250), None),
        (29, 32, ("Kunstgegenstände", 2, 4, 1, 25), [("A", 1, 6, 1)]),
        (33, 36, ("Edelsteine", 3, 6, 1, 50), [("A", 1, 6, 1)]),
        (37, 40, ("Edelsteine", 3, 6, 1, 100), [("A", 1, 6, 1)]),
        (41, 44, ("Kunstgegenstände", 2, 4, 1, 250), [("A", 1, 6, 1)]),
        (45, 49, ("Kunstgegenstände", 2, 4, 1, 25), [("B", 1, 4, 1)]),
        (50, 54, ("Edelsteine", 3, 6, 1, 50), [("B", 1, 4, 1)]),
        (55, 59, ("Edelsteine", 3, 6, 1, 100), [("B", 1, 4, 1)]),
        (60, 63, ("Kunstgegenstände", 2, 4, 1, 250), [("B", 1, 4, 1)]),
        (64, 66, ("Kunstgegenstände", 2, 4, 1, 25), [("C", 1, 4, 1)]),
        (67, 69, ("Edelsteine", 3, 6, 1, 50), [("C", 1, 4, 1)]),
        (70, 72, ("Edelsteine", 3, 6, 1, 100), [("C", 1, 4, 1)]),
        (73, 74, ("Kunstgegenstände", 2, 4, 1, 250), [("C", 1, 4, 1)]),
        (75, 76, ("Kunstgegenstände", 2, 4, 1, 25), [("D", 1, 1, 1)]),
        (77, 78, ("Edelsteine", 3, 6, 1, 50), [("D", 1, 1, 1)]),
        (79, 79, ("Edelsteine", 3, 6, 1, 100), [("D", 1, 1, 1)]),
        (80, 80, ("Kunstgegenstände", 2, 4, 1, 250), [("D", 1, 1, 1)]),
        (81, 84, ("Kunstgegenstände", 2, 4, 1, 25), [("F", 1, 4, 1)]),
        (85, 88, ("Edelsteine", 3, 6, 1, 50), [("F", 1, 4, 1)]),
        (89, 91, ("Edelsteine", 3, 6, 1, 100), [("F", 1, 4, 1)]),
        (92, 94, ("Kunstgegenstände", 2, 4, 1, 250), [("F", 1, 4, 1)]),
        (95, 96, ("Edelsteine", 3, 6, 1, 100), [("G", 1, 4, 1)]),
        (97, 98, ("Kunstgegenstände", 2, 4, 1, 250), [("G", 1, 4, 1)]),
        (99, 99, ("Edelsteine", 3, 6, 1, 100), [("H", 1, 4, 1)]),
        (100, 100, ("Kunstgegenstände", 2, 4, 1, 250), [("H", 1, 4, 1)]),
    ],
    "11-16": [
        (1, 3, None, None),
        (4, 6, ("Kunstgegenstände", 2, 4, 1, 250), None),
        (7, 9, ("Kunstgegenstände", 2, 4, 1, 750), None),
        (10, 12, ("Edelsteine", 3, 6, 1, 500), None),
        (13, 15, ("Edelsteine", 3, 6, 1, 1000), None),
        (16, 19, ("Kunstgegenstände", 2, 4, 1, 250), [("A", 1, 4, 1), ("B", 1, 6, 1)]),
        (20, 23, ("Kunstgegenstände", 2, 4, 1, 750), [("A", 1, 4, 1), ("B", 1, 6, 1)]),
        (24, 26, ("Edelsteine", 3, 6, 1, 500), [("A", 1, 4, 1), ("B", 1, 6, 1)]),
        (27, 29, ("Edelsteine", 3, 6, 1, 1000), [("A", 1, 4, 1), ("B", 1, 6, 1)]),
        (30, 35, ("Kunstgegenstände", 2, 4, 1, 250), [("C", 1, 6, 1)]),
        (36, 40, ("Kunstgegenstände", 2, 4, 1, 750), [("C", 1, 6, 1)]),
        (41, 45, ("Edelsteine", 3, 6, 1, 500), [("C", 1, 6, 1)]),
        (46, 50, ("Edelsteine", 3, 6, 1, 1000), [("C", 1, 6, 1)]),
        (51, 54, ("Kunstgegenstände", 2, 4, 1, 250), [("D", 1, 4, 1)]),
        (55, 58, ("Kunstgegenstände", 2, 4, 1, 750), [("D", 1, 4, 1)]),
        (59, 62, ("Edelsteine", 3, 6, 1, 500), [("D", 1, 4, 1)]),
        (63, 66, ("Edelsteine", 3, 6, 1, 1000), [("D", 1, 4, 1)]),
        (67, 68, ("Kunstgegenstände", 2, 4, 1, 250), [("E", 1, 1, 1)]),
        (69, 70, ("Kunstgegenstände", 2, 4, 1, 750), [("E", 1, 1, 1)]),
        (71, 72, ("Edelsteine", 3, 6, 1, 500), [("E", 1, 1, 1)]),
        (73, 74, ("Edelsteine", 3, 6, 1, 1000), [("E", 1, 1, 1)]),
        (75, 76, ("Kunstgegenstände", 2, 4, 1, 250), [("F", 1, 1, 1), ("G", 1, 4, 1)]),
        (77, 78, ("Kunstgegenstände", 2, 4, 1, 750), [("F", 1, 1, 1), ("G", 1, 4, 1)]),
        (79, 80, ("Edelsteine", 3, 6, 1, 500), [("F", 1, 1, 1), ("G", 1, 4, 1)]),
        (81, 82, ("Edelsteine", 3, 6, 1, 1000), [("F", 1, 1, 1), ("G", 1, 4, 1)]),
        (83, 85, ("Kunstgegenstände", 2, 4, 1, 250), [("H", 1, 4, 1)]),
        (86, 88, ("Kunstgegenstände", 2, 4, 1, 750), [("H", 1, 4, 1)]),
        (89, 90, ("Edelsteine", 3, 6, 1, 500), [("H", 1, 4, 1)]),
        (91, 92, ("Edelsteine", 3, 6, 1, 1000), [("H", 1, 4, 1)]),
        (93, 94, ("Kunstgegenstände", 2, 4, 1, 250), [("I", 1, 1, 1)]),
        (95, 96, ("Kunstgegenstände", 2, 4, 1, 750), [("I", 1, 1, 1)]),
        (97, 98, ("Edelsteine", 3, 6, 1, 500), [("I", 1, 1, 1)]),
        (99, 100, ("Edelsteine", 3, 6, 1, 1000), [("I", 1, 1, 1)]),
    ],
    "17+": [
        (1, 2, None, None),
        (3, 5, ("Edelsteine", 3, 6, 1, 1000), [("C", 1, 8, 1)]),
        (6, 8, ("Kunstgegenstände", 1, 10, 1, 2500), [("C", 1, 8, 1)]),
        (9, 11, ("Kunstgegenstände", 1, 4, 1, 7500), [("C", 1, 8, 1)]),
        (12, 14, ("Edelsteine", 1, 8, 1, 5000), [("C", 1, 8, 1)]),
        (15, 22, ("Edelsteine", 3, 6, 1, 1000), [("D", 1, 6, 1)]),
        (23, 30, ("Kunstgegenstände", 1, 10, 1, 2500), [("D", 1, 6, 1)]),
        (31, 38, ("Kunstgegenstände", 1, 4, 1, 7500), [("D", 1, 6, 1)]),
        (39, 46, ("Edelsteine", 1, 8, 1, 5000), [("D", 1, 6, 1)]),
        (47, 52, ("Edelsteine", 3, 6, 1, 1000), [("E", 1, 6, 1)]),
        (53, 58, ("Kunstgegenstände", 1, 10, 1, 2500), [("E", 1, 6, 1)]),
        (59, 63, ("Kunstgegenstände", 1, 4, 1, 7500), [("E", 1, 6, 1)]),
        (64, 68, ("Edelsteine", 1, 8, 1, 5000), [("E", 1, 6, 1)]),
        (69, 69, ("Edelsteine", 3, 6, 1, 1000), [("G", 1, 4, 1)]),
        (70, 70, ("Kunstgegenstände", 1, 10, 1, 2500), [("G", 1, 4, 1)]),
        (71, 71, ("Kunstgegenstände", 1, 4, 1, 7500), [("G", 1, 4, 1)]),
        (72, 72, ("Edelsteine", 1, 8, 1, 5000), [("G", 1, 4, 1)]),
        (73, 74, ("Edelsteine", 3, 6, 1, 1000), [("H", 1, 4, 1)]),
        (75, 76, ("Kunstgegenstände", 1, 10, 1, 2500), [("H", 1, 4, 1)]),
        (77, 78, ("Kunstgegenstände", 1, 4, 1, 7500), [("H", 1, 4, 1)]),
        (79, 80, ("Edelsteine", 1, 8, 1, 5000), [("H", 1, 4, 1)]),
        (81, 85, ("Edelsteine", 3, 6, 1, 1000), [("I", 1, 4, 1)]),
        (86, 90, ("Kunstgegenstände", 1, 10, 1, 2500), [("I", 1, 4, 1)]),
        (91, 95, ("Kunstgegenstände", 1, 4, 1, 7500), [("I", 1, 4, 1)]),
        (96, 100, ("Edelsteine", 1, 8, 1, 5000), [("I", 1, 4, 1)]),
    ],
}

def _pick_range(table: List[Tuple[int, int, object]], roll: int):
    for a, b, v in table:
        if a <= roll <= b:
            return v
    return None

def _roll_from_range_table(table: List[Tuple[int, int, object]]) -> Tuple[int, object]:
    r = random.randint(1, 100)
    v = _pick_range(table, r)
    return r, v

def _roll_magic_table_value(table_ranges: List[Tuple[int, int, str]]) -> Tuple[int, str]:
    r = random.randint(1, 100)
    item = None
    for a, b, txt in table_ranges:
        if a <= r <= b:
            item = txt
            break
    if item is None:
        item = "Unbekannter Gegenstand"
    return r, item

MAGIC_TABLE_W8_FIGUR_G = {
    1: "Bronze Greif",
    2: "Ebenholz Fliege",
    3: "Goldene Löwen",
    4: "Elfenbein Ziegen",
    5: "Marmor Elefant",
    6: "Onyx Hund",
    7: "Onyx Hund",
    8: "Serpentin Eule",
}

MAGIC_TABLE_W12_ARMOR_I = {
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
        (12, 14, "Figur der wundersamen Kraft (wirf einen W8)"),
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
        (76, 76, "Magische Rüstung (wirf 1W12)"),
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

def roll_magic_item(table_letter: str) -> Tuple[str, str]:
    table_letter = (table_letter or "").strip().upper()
    ranges = MAGIC_TABLES.get(table_letter)
    if not ranges:
        r = random.randint(1, 100)
        return f"{table_letter}: Unbekannte Tabelle", f"W100: {_fmt_w100(r)}"

    r, item = _roll_magic_table_value(ranges)
    extra = ""

    if table_letter == "G" and "W8" in item:
        w8 = random.randint(1, 8)
        extra = f" | W8: {w8} -> {MAGIC_TABLE_W8_FIGUR_G.get(w8, 'Unbekannt')}"
        item = "Figur der wundersamen Kraft"
    if table_letter == "I" and "W12" in item:
        w12 = random.randint(1, 12)
        extra = f" | W12: {w12} -> {MAGIC_TABLE_W12_ARMOR_I.get(w12, 'Unbekannt')}"
        item = "Magische Rüstung"

    return item, f"Tabelle {table_letter} | W100: {_fmt_w100(r)}{extra}"

def _coins_to_lines(coins: Dict[str, int]) -> List[str]:
    lines = []
    for k in COIN_KEYS:
        v = coins.get(k, 0)
        if v:
            lines.append(f"{k}: {_fmt_num(v)}")
    if not lines:
        return ["Keine Münzen"]
    return lines

def generate_single_treasure(cr_band: str) -> str:
    cr_band = cr_band if cr_band in SINGLE_TREASURE_TABLE else "0-4"
    w100, entry = _roll_from_range_table(SINGLE_TREASURE_TABLE[cr_band])

    coins = {k: 0 for k in COIN_KEYS}
    details = []
    for cur, c, s, m in entry:
        val, line = _coin_line(cur, c, s, m)
        coins[cur] += val
        details.append(line)

    msg = (
        f"💰 Einzelschatz\n"
        f"HG: {cr_band}\n"
        f"W100: {_fmt_w100(w100)}\n\n"
        f"Münzen:\n" + "\n".join(_coins_to_lines(coins)) + "\n\n"
        f"Würfe:\n" + "\n".join(details)
    )
    return msg

def generate_hoard_treasure(cr_band: str) -> str:
    cr_band = cr_band if cr_band in HOARD_COIN_BASE else "0-4"

    coins = {k: 0 for k in COIN_KEYS}
    coin_details = []
    for cur, c, s, m in HOARD_COIN_BASE[cr_band]:
        val, line = _coin_line(cur, c, s, m)
        coins[cur] += val
        coin_details.append(line)

    w100 = random.randint(1, 100)
    extra_entry = _pick_range(HOARD_EXTRA_TABLE[cr_band], w100)

    gem_lines: List[str] = []
    magic_lines: List[str] = []
    magic_details: List[str] = []

    if extra_entry is None:
        extra_entry = (None, None)

    gem_art, magic_specs = extra_entry

    if gem_art:
        kind, dc, ds, dm, value_each = gem_art
        count_val, count_rolls = _roll_scaled(dc, ds, dm)
        total_val = count_val * value_each
        if dc == 1:
            gem_lines.append(f"{kind}: {count_val} x {value_each} GM (gesamt {_fmt_num(total_val)} GM) | Wurf: {count_rolls[0]}")
        else:
            gem_lines.append(f"{kind}: {count_val} x {value_each} GM (gesamt {_fmt_num(total_val)} GM) | Würfe: {', '.join(map(str, count_rolls))}")

    if magic_specs:
        for table_letter, dc, ds, dm in magic_specs:
            if dc == 1 and ds == 1:
                how_many = 1
                how_detail = "einmal"
            else:
                how_many, rolls = _roll_scaled(dc, ds, dm)
                how_detail = f"{dc}W{ds}" + (f" x {_fmt_num(dm)}" if dm != 1 else "") + f" -> {how_many} | Würfe: {', '.join(map(str, rolls))}"

            magic_lines.append(f"Tabelle {table_letter}: {how_detail}")
            for _ in range(int(how_many)):
                item, det = roll_magic_item(table_letter)
                magic_details.append(f"{item} ({det})")

    msg = (
        f"🏺 Schatzhort\n"
        f"HG: {cr_band}\n\n"
        f"Münzen:\n" + "\n".join(_coins_to_lines(coins)) + "\n\n"
        f"Würfe Münzen:\n" + "\n".join(coin_details) + "\n\n"
        f"Extra W100: {_fmt_w100(w100)}\n"
    )

    if gem_lines:
        msg += "\nEdelsteine oder Kunstgegenstände:\n" + "\n".join(gem_lines) + "\n"
    else:
        msg += "\nEdelsteine oder Kunstgegenstände:\nKeine\n"

    if magic_lines:
        msg += "\nMagische Gegenstände:\n" + "\n".join(magic_lines) + "\n"
        if magic_details:
            msg += "\nAusgewürfelt:\n" + "\n".join(magic_details)
    else:
        msg += "\nMagische Gegenstände:\nKeine"

    return msg

async def rollschatz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("treasure_type", None)
    context.user_data.pop("treasure_cr", None)

    await update.message.reply_text(
        "💰 /rollschatz\nWillst du Einzelschatz oder Schatzhort?",
        reply_markup=build_treasure_type_keyboard()
    )
    return TREASURE_PICK_TYPE

async def rollschatz_pick_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.split(":", 1)[1]
    context.user_data["treasure_type"] = choice

    label = "Einzelschatz" if choice == "single" else "Schatzhort"
    await query.edit_message_text(
        f"💰 {label}\nWelcher Herausforderungsgrad?",
        reply_markup=build_treasure_cr_keyboard()
    )
    return TREASURE_PICK_CR

async def rollschatz_pick_cr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cr = query.data.split(":", 1)[1]
    context.user_data["treasure_cr"] = cr

    ttype = context.user_data.get("treasure_type", "single")

    if ttype == "hoard":
        msg = generate_hoard_treasure(cr)
    else:
        msg = generate_single_treasure(cr)

    await query.edit_message_text(msg)
    return ConversationHandler.END

async def rollschatz_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Schatzwurf abgebrochen 🙂")
    else:
        await update.message.reply_text("Schatzwurf abgebrochen 🙂")
    return ConversationHandler.END


# -----------------------
# ROLLDUNGEON SYSTEM
# -----------------------

SUITS = {
    1: "♣",
    2: "♦",
    3: "♥",
    4: "♠",
}

JOKER_NPCS = [
    ("Marie", "Stanley", "Class"),
    ("Marve", "Stanley", "Fighter"),
    ("Kavia", "Corwin", "Rogue"),
    ("Joselyn", "Leon", "Bard"),
    ("Tellava", "Hadar", "Wizard"),
    ("Bess", "Baelin", "Cleric"),
    ("Nura", "Torgil", "Druid"),
    ("Rala", "Vasar", "Paladin"),
    ("Elera", "Zelmio", "Ranger"),
    ("Sylthir", "Tcham", "Warlock"),
]

JOKER_ITEMS = [
    ("Weapon", "Unknown", "100gp"),
    ("Armor", "Tiny", "150gp"),
    ("Book", "Fragile", "200gp"),
    ("Scroll", "Glowing", "250gp"),
    ("Map", "Ancient", "500gp"),
    ("Ring", "Magical", "750gp"),
]

QUEST_TABLES = [
    [
        (2, 4, "Convince an NPC to return to town"),
        (5, 6, "Kill a monster who holds an ITEM"),
        (10, 13, "Rescue an imprisoned NPC"),
        (14, 17, "Stop an evil NPC from killing locals"),
        (18, 99, "Find and destroy a dangerous ITEM"),
    ],
    [
        (2, 5, "Map dungeon lvl 1 for an NPC"),
        (6, 9, "Rescue a potentially lost NPC"),
        (10, 13, "Kill a small group of evil monsters"),
        (14, 17, "Retrieve an ITEM for a wealthy NPC"),
        (18, 99, "Clear the dungeon of all monsters"),
    ],
    [
        (2, 4, "Find an ITEM for a local innkeeper"),
        (5, 7, "Rumors of 200gp on dungeon lvl 2"),
        (8, 10, "Legends tell of a rare ITEM inside"),
        (11, 13, "Save the family member of a PC"),
        (14, 16, "Slay a small group of evil NPCs"),
        (17, 19, "Kill or delay a powerful monster"),
        (20, 99, "Stop cultists summoning demons"),
    ],
    [
        (2, 4, "Discover what is hiding in the ruins"),
        (5, 7, "PCs find a map leading to dungeon"),
        (8, 10, "A strange ITEM is said to be on lvl 2"),
        (11, 13, "Map dungeon lvl 2 (1d4) for an NPC"),
        (14, 16, "Bring back the head of a monster"),
        (17, 19, "Find a rare ITEM before an evil NPC"),
        (20, 99, "Slay a dragon to save the locals"),
    ],
]

HALLWAY_TABLES = [
    [
        (1, 3, "Nothing"),
        (4, 6, "Backpack (empty)"),
        (7, 8, "Small patches of dried blood"),
        (9, 9, "Rusty axe + 1d8 torches"),
        (10, 10, "Backpack (healing potion)"),
    ],
    [
        (1, 1, "Nothing"),
        (2, 2, "Dead rat"),
        (3, 3, "Rubble"),
        (4, 4, "Moldy cheese"),
        (5, 5, "Iron ingots"),
        (6, 6, "Dagger"),
        (7, 7, "Dried herbs"),
        (8, 8, "Bones"),
        (9, 9, "4d8 gp"),
        (10, 10, "Tools"),
    ],
    [
        (1, 2, "Nothing"),
        (3, 4, "Spider webs + tattered clothes"),
        (5, 6, "Broken glass bottles"),
        (7, 8, "Scrapes and cracks in walls"),
        (9, 9, "Skeletal remains of a horse"),
        (10, 10, "Backpack (1d4 days of rations)"),
    ],
]

ROOM_CONTENT_TIER = {
    1: [
        (1, 3, "Backpack (3d6 gp + 50' rope)"),
        (4, 6, "Broken chairs + thick layer of dust"),
        (7, 9, "Kobold corpse (3d10 cp)"),
        (10, 10, "Desk + chair + high quality bed"),
    ],
    2: [
        (1, 1, "Nothing"),
        (2, 4, "1d6 stone statues"),
        (5, 6, "Halfling corpse (4d8 gp)"),
        (7, 8, "Broken glass bottles"),
        (9, 9, "Lizardfolk corpse"),
        (10, 10, "Scorch marks on walls"),
    ],
    3: [
        (1, 2, "Nothing"),
        (3, 4, "2d8 sacks of wheat"),
        (5, 6, "Broken chest (empty)"),
        (7, 8, "Human corpse"),
        (9, 9, "Chains + cages"),
        (10, 10, "Sack (1d10 x 100 cp)"),
    ],
}

SOUND_TABLES = [
    {1: "None", 2: "Wind", 3: "Hissing", 4: "Dripping", 5: "Moans", 6: "Faint music"},
    {1: "None", 2: "Footsteps", 3: "Rumbling", 4: "Clanking", 5: "Thumping", 6: "Screams"},
    {1: "None", 2: "Faint whispering voices", 3: "Splintering of wood", 4: "Rattling of chains", 5: "Clinking of falling coins", 6: "Distant gutteral laughter"},
    {1: "None", 2: "Groans", 3: "Splashing", 4: "Footsteps", 5: "Sobbing", 6: "Roaring"},
]

SMELL_TABLES = [
    {1: "None", 2: "Metallic", 3: "Dried sweat", 4: "Acidic", 5: "Incense", 6: "Rotten meat"},
    {1: "None", 2: "Burnt wood", 3: "Dirt/Soil", 4: "Excrement", 5: "Lamp oil", 6: "Sulfur"},
    {1: "None", 2: "Burnt meat", 3: "Urine", 4: "Rotting flesh", 5: "Straw", 6: "Mold"},
]

MAGIC_POOLS = {
    1: [
        (1, 4, "No noticeable effect (GM decides)"),
        (5, 7, "drink 1/day: restore 1d8 HP"),
        (8, 9, "touch 1/day: deafened for 1 hour"),
        (10, 11, "drink 1/week: for 1d12 hours deal 1d6 bonus damage each hit"),
        (12, 12, "touch 1/day: suffer 1d12 damage"),
    ],
    2: [
        (1, 4, "No noticeable effect (GM decides)"),
        (5, 7, "drink 1/week: restore 1d20 HP"),
        (8, 9, "touch 1/day: blinded for 1 hour"),
        (10, 11, "touch 1/week: for 3d6 x 10 mins advantage on initiative rolls"),
        (12, 12, "drink 1/day: suffer 2d12 damage"),
    ],
    3: [
        (1, 6, "No noticeable effect"),
        (7, 9, "drink 1/day: restore 2d10 HP"),
        (10, 11, "touch 1/day: poisoned 1d3 hours"),
        (12, 12, "drink 1/day: for 3d6 x 10 mins move twice as fast on land"),
    ],
    4: [
        (1, 6, "No noticeable effect"),
        (7, 9, "drink 1/day: restore 2d10 HP"),
        (10, 11, "touch 1/day: poisoned 1d3 hours"),
        (12, 12, "drink 1/day: for 3d6 x 10 mins move twice as fast on land"),
    ],
}

def _tier_from_level(lvl: int) -> int:
    if lvl <= 4:
        return 1
    if lvl <= 10:
        return 2
    if lvl <= 16:
        return 3
    return 4

def _roll_table_ranges(ranges: List[Tuple[int, int, str]], die_sides: int) -> Tuple[int, str]:
    r = random.randint(1, die_sides)
    for a, b, txt in ranges:
        if a <= r <= b:
            return r, txt
    return r, "Nothing"

def _pick_range_from_total(ranges: List[Tuple[int, int, str]], total: int) -> str:
    for a, b, txt in ranges:
        if a <= total <= b:
            return txt
    return "Nothing"

def _roll_sound() -> str:
    table = random.choice(SOUND_TABLES)
    r = random.randint(1, 6)
    return f"W6: {r} -> {table[r]}"

def _roll_smell() -> str:
    table = random.choice(SMELL_TABLES)
    r = random.randint(1, 6)
    return f"W6: {r} -> {table[r]}"

def _roll_hallway() -> str:
    ranges = []
    for t in random.choice(HALLWAY_TABLES):
        ranges.append(t)
    r = random.randint(1, 10)
    txt = _pick_range(ranges, r)
    extra = ""
    if "1d8 torches" in txt:
        x, rolls = _roll_scaled(1, 8, 1)
        extra = f" | 1W8: {rolls[0]} -> {x} Fackeln"
    if "4d8 gp" in txt:
        x, rolls = _roll_scaled(4, 8, 1)
        extra = f" | 4W8: {', '.join(map(str, rolls))} -> {_fmt_num(x)} GM"
    if "1d4 days of rations" in txt:
        x, rolls = _roll_scaled(1, 4, 1)
        extra = f" | 1W4: {rolls[0]} -> {x} Tage Rationen"
    return f"W10: {r} -> {txt}{extra}"

def _roll_room_contents(tier: int) -> str:
    if tier <= 1:
        ranges = ROOM_CONTENT_TIER[1]
    elif tier == 2:
        ranges = ROOM_CONTENT_TIER[2]
    else:
        ranges = ROOM_CONTENT_TIER[3]

    r = random.randint(1, 10)
    txt = _pick_range(ranges, r)
    extra = ""

    if "3d6 gp" in txt:
        x, rolls = _roll_scaled(3, 6, 1)
        extra = f" | 3W6: {', '.join(map(str, rolls))} -> {_fmt_num(x)} GM"
    if "3d10 cp" in txt:
        x, rolls = _roll_scaled(3, 10, 1)
        extra = f" | 3W10: {', '.join(map(str, rolls))} -> {_fmt_num(x)} KM"
    if "4d8 gp" in txt:
        x, rolls = _roll_scaled(4, 8, 1)
        extra = f" | 4W8: {', '.join(map(str, rolls))} -> {_fmt_num(x)} GM"
    if "2d8 sacks" in txt:
        x, rolls = _roll_scaled(2, 8, 1)
        extra = f" | 2W8: {', '.join(map(str, rolls))} -> {x} Säcke"
    if "1d6 stone statues" in txt:
        x, rolls = _roll_scaled(1, 6, 1)
        extra = f" | 1W6: {rolls[0]} -> {x} Statuen"
    if "1d10 x 100 cp" in txt:
        base = random.randint(1, 10)
        extra = f" | 1W10: {base} -> {_fmt_num(base * 100)} KM"

    return f"W10: {r} -> {txt}{extra}"

def _roll_magic_pool(tier: int) -> str:
    ranges = MAGIC_POOLS.get(tier, MAGIC_POOLS[1])
    r = random.randint(1, 12)
    txt = _pick_range_from_total(ranges, r)
    extra = ""

    if "restore 1d8 HP" in txt:
        x, rolls = _roll_scaled(1, 8, 1)
        extra = f" | 1W8: {rolls[0]} -> {x} HP"
    if "for 1d12 hours" in txt:
        x, rolls = _roll_scaled(1, 12, 1)
        extra = f" | 1W12: {rolls[0]} -> {x} Stunden"
        y, yrolls = _roll_scaled(1, 6, 1)
        extra += f" | 1W6: {yrolls[0]} -> +{y} Schaden pro Treffer"
    if "suffer 1d12 damage" in txt:
        x, rolls = _roll_scaled(1, 12, 1)
        extra = f" | 1W12: {rolls[0]} -> {x} Schaden"
    if "restore 1d20 HP" in txt:
        x, rolls = _roll_scaled(1, 20, 1)
        extra = f" | 1W20: {rolls[0]} -> {x} HP"
    if "for 3d6 x 10 mins" in txt:
        base, rolls = _roll_scaled(3, 6, 1)
        extra = f" | 3W6: {', '.join(map(str, rolls))} -> {base * 10} Minuten"
    if "suffer 2d12 damage" in txt:
        x, rolls = _roll_scaled(2, 12, 1)
        extra = f" | 2W12: {', '.join(map(str, rolls))} -> {x} Schaden"
    if "restore 2d10 HP" in txt:
        x, rolls = _roll_scaled(2, 10, 1)
        extra = f" | 2W10: {', '.join(map(str, rolls))} -> {x} HP"
    if "poisoned 1d3 hours" in txt:
        x, rolls = _roll_scaled(1, 3, 1)
        extra = f" | 1W3: {rolls[0]} -> {x} Stunden"
    if "move twice as fast" in txt:
        base, rolls = _roll_scaled(3, 6, 1)
        extra = f" | 3W6: {', '.join(map(str, rolls))} -> {base * 10} Minuten"

    return f"W12: {r} -> {txt}{extra}"

def _roll_trap(pc_level: int, tier: int) -> str:
    d4 = random.randint(1, 4)
    total = d4 + pc_level

    if tier <= 1:
        if total <= 4:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> None"
        if 5 <= total <= 7:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Simple pit trap"
        if 8 <= total <= 9:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Hidden pit trap"
        if total == 10:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Falling net trap"
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> Poison darts trap"

    if tier == 2:
        if total <= 4:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> None"
        if 5 <= total <= 7:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Hidden pit trap"
        if 8 <= total <= 9:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Spiked pit trap"
        if total == 10:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Collapsing roof trap"
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> Poison darts trap"

    if tier == 3:
        if total <= 4:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> None"
        if 5 <= total <= 7:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Locking pit trap"
        if 8 <= total <= 9:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Spiked pit trap"
        if total == 10:
            return f"1W4+Lvl: {d4}+{pc_level}={total} -> Collapsing roof trap"
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> Fire-breathing statue"

    if total <= 4:
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> None"
    if 5 <= total <= 7:
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> Locking pit trap"
    if 8 <= total <= 9:
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> Spiked pit trap"
    if total == 10:
        return f"1W4+Lvl: {d4}+{pc_level}={total} -> Collapsing roof trap"
    return f"1W4+Lvl: {d4}+{pc_level}={total} -> Fire-breathing statue"

MONSTER_POOLS = {
    1: {
        "9-10": ["goblin", "skeleton", "zombie", "kobold"],
        "11-12": ["orc", "bandit", "giant spider", "harpy"],
        "13-14": ["bugbear", "ghoul", "lizardfolk", "wererat"],
        "15-16": ["ogre", "wight", "gargoyle", "berserker"],
        "17+": ["troll", "umber hulk", "basilisk", "wraith"],
    },
    2: {
        "9-10": ["giant spider", "orc", "ghoul", "bugbear"],
        "11-12": ["mimic", "wereboar", "orc war chief", "druid"],
        "13-14": ["minotaur", "black pudding", "drow", "griffon"],
        "15-16": ["drow elite warrior", "troll", "yuan-ti", "wight"],
        "17+": ["shadow demon", "vampire spawn", "gorgon", "hydra"],
    },
    3: {
        "9-10": ["gargoyle", "ogre", "wight", "giant hyena"],
        "11-12": ["giant turtle", "mummy", "black dragon wyrmling", "displacer beast"],
        "13-14": ["basilisk", "orc war chief", "drow", "werebear"],
        "15-16": ["gorgon", "cambion", "quasit + cult fanatics", "chimera"],
        "17+": ["revenant", "vampire spawn", "stone golem", "shadow demon"],
    },
    4: {
        "9-10": ["mummy", "vampire spawn", "gorgon", "wyvern"],
        "11-12": ["giant", "young dragon", "stone golem", "cambion"],
        "13-14": ["adult dragon", "demon", "devil", "lich cult leader"],
        "15-16": ["ancient dragon", "balor", "pit fiend", "empowered lich"],
        "17+": ["campaign boss", "something unfair on purpose", "two bosses arguing", "the dungeon itself awakens"],
    },
}

def _roll_monsters(pc_level: int, pc_count: int, dungeon_level: int, hard: bool) -> str:
    tier = _tier_from_level(dungeon_level)
    d6 = random.randint(1, 6)
    total = d6 + pc_level + (2 if hard else 0)

    if total <= 8:
        return f"1W6+Lvl{'(+2)' if hard else ''}: {d6}+{pc_level}{'+2' if hard else ''}={total} -> None"

    pool = MONSTER_POOLS.get(tier, MONSTER_POOLS[1])

    if 9 <= total <= 10:
        monster = random.choice(pool["9-10"])
        return f"1W6+Lvl{'(+2)' if hard else ''}: {d6}+{pc_level}{'+2' if hard else ''}={total} -> {pc_count} x {monster} (pro PC)"
    if 11 <= total <= 12:
        monster = random.choice(pool["11-12"])
        return f"1W6+Lvl{'(+2)' if hard else ''}: {d6}+{pc_level}{'+2' if hard else ''}={total} -> {pc_count} x {monster} (pro PC)"
    if 13 <= total <= 14:
        monster = random.choice(pool["13-14"])
        return f"1W6+Lvl{'(+2)' if hard else ''}: {d6}+{pc_level}{'+2' if hard else ''}={total} -> {pc_count} x {monster} (pro PC)"
    if 15 <= total <= 16:
        monster = random.choice(pool["15-16"])
        return f"1W6+Lvl{'(+2)' if hard else ''}: {d6}+{pc_level}{'+2' if hard else ''}={total} -> {pc_count} x {monster} (pro PC)"

    monster = random.choice(pool["17+"])
    return f"1W6+Lvl{'(+2)' if hard else ''}: {d6}+{pc_level}{'+2' if hard else ''}={total} -> {pc_count} x {monster} (pro PC)"

def _cr_band_from_level(lvl: int) -> str:
    if lvl <= 4:
        return "0-4"
    if lvl <= 10:
        return "5-10"
    if lvl <= 16:
        return "11-16"
    return "17+"

def _roll_quest(pc_level: int) -> str:
    table = random.choice(QUEST_TABLES)
    d8 = random.randint(1, 8)
    total = d8 + pc_level
    for a, b, txt in table:
        if a <= total <= b:
            return f"1W8+Lvl: {d8}+{pc_level}={total} -> {txt}"
    return f"1W8+Lvl: {d8}+{pc_level}={total} -> Quest (improv)"

def _roll_joker() -> str:
    n = random.randint(1, 10)
    i = random.randint(1, 6)
    npc = JOKER_NPCS[n - 1]
    item = JOKER_ITEMS[i - 1]
    return (
        f"NPC W10: {n} -> {npc[0]} {npc[1]} ({npc[2]})\n"
        f"Quest ITEM W6: {i} -> {item[0]} ({item[1]}) Wert: {item[2]}"
    )

async def rolldungeon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pc_level = 1
    pc_count = 1
    dungeon_level = 1
    rooms = None

    try:
        if len(context.args) >= 1:
            pc_level = int(context.args[0])
        if len(context.args) >= 2:
            pc_count = int(context.args[1])
        if len(context.args) >= 3:
            dungeon_level = int(context.args[2])
        else:
            dungeon_level = pc_level
        if len(context.args) >= 4:
            rooms = int(context.args[3])
    except Exception:
        pc_level = 1
        pc_count = 1
        dungeon_level = 1
        rooms = None

    pc_level = max(1, min(20, pc_level))
    pc_count = max(1, min(8, pc_count))
    dungeon_level = max(1, min(20, dungeon_level))

    if rooms is None:
        rooms = random.randint(1, 6) + 5
    rooms = max(3, min(20, rooms))

    tier = _tier_from_level(dungeon_level)
    cr_band = _cr_band_from_level(dungeon_level)

    header = (
        "🗺️ Rolldungeon\n"
        f"PCs: {pc_count} | PC Stufe: {pc_level} | Dungeon Stufe: {dungeon_level} | Räume: {rooms}\n"
        "Tipp: Spoiler in Telegram per Strg+Shift+P (Desktop) oder Text markieren und Spoiler wählen.\n\n"
        "Räume sind als Spoiler vorbereitet. Klick drauf zum Aufdecken.\n"
    )

    sections: List[str] = []
    sections.append(html.escape(header))

    entrance = (
        "Einstieg\n"
        f"Quest Hook: {_roll_quest(pc_level)}\n"
        f"Geräusch: {_roll_sound()}\n"
        f"Geruch: {_roll_smell()}\n"
        f"Flur Inhalt: {_roll_hallway()}"
    )
    sections.append(
        f"<b>Raum 1: Eingang</b>\n"
        f"<span class=\"tg-spoiler\">{html.escape(entrance)}</span>\n"
    )

    for idx in range(2, rooms):
        rank_roll = random.randint(1, 12)
        suit_roll = random.randint(1, 4)
        suit = SUITS[suit_roll]

        if rank_roll <= 9:
            rank = str(rank_roll + 1)
        elif rank_roll == 10:
            rank = "J"
        elif rank_roll == 11:
            rank = "Q"
        else:
            rank = "🃏"

        is_hard = (suit == "♠")
        room_lines: List[str] = []
        room_lines.append(f"Generator: Rang W12={rank_roll} -> {rank} | Symbol W4={suit_roll} -> {suit}")

        if rank == "🃏":
            room_lines.append("Special: Joker Raum")
            room_lines.append(_roll_joker())
            if random.random() < 0.5:
                room_lines.append(f"Geräusch: {_roll_sound()}")
            if random.random() < 0.5:
                room_lines.append(f"Geruch: {_roll_smell()}")
        else:
            if suit == "♦":
                if rank in {"7", "8", "9", "10", "Q"}:
                    room_lines.append("Typ: Schatzraum")
                    if random.random() < 0.35:
                        room_lines.append("Falle: Poison needle trap (kleiner Klassiker)")
                    room_lines.append(generate_single_treasure(cr_band))
                else:
                    room_lines.append("Typ: Falle")
                    room_lines.append(_roll_trap(pc_level, tier))
                    if random.random() < 0.6:
                        room_lines.append("Danach: Monster im Lärm angezogen")
                        room_lines.append(_roll_monsters(pc_level, pc_count, dungeon_level, is_hard))
            elif suit == "♥":
                if rank in {"6", "9", "10"} and random.random() < 0.7:
                    room_lines.append("Typ: Magischer Pool")
                    room_lines.append(_roll_magic_pool(tier))
                else:
                    room_lines.append("Typ: Raum Inhalt")
                    room_lines.append(_roll_room_contents(tier))
                if random.random() < 0.6:
                    room_lines.append(f"Geräusch: {_roll_sound()}")
                if random.random() < 0.6:
                    room_lines.append(f"Geruch: {_roll_smell()}")
            else:
                room_lines.append("Typ: Monsterraum")
                room_lines.append(_roll_monsters(pc_level, pc_count, dungeon_level, is_hard))
                if random.random() < 0.4:
                    room_lines.append("Beute: kleiner Bonus Schatz")
                    room_lines.append(generate_single_treasure(cr_band))
                if random.random() < 0.5:
                    room_lines.append(f"Geräusch: {_roll_sound()}")
                if random.random() < 0.5:
                    room_lines.append(f"Geruch: {_roll_smell()}")

            if random.random() < 0.35:
                room_lines.append("Nebenflur: Inhalt")
                room_lines.append(_roll_hallway())

        room_title = f"{rank}{suit}"
        room_txt = "\n".join(room_lines)

        sections.append(
            f"<b>Raum {idx}: {html.escape(room_title)}</b>\n"
            f"<span class=\"tg-spoiler\">{html.escape(room_txt)}</span>\n"
        )

    end_choice = "Questziel" if random.random() < 0.6 else "Treppe zum nächsten Level"
    end_lines = [
        f"Finale: {end_choice}",
        f"Geräusch: {_roll_sound()}",
        f"Geruch: {_roll_smell()}",
    ]

    if end_choice == "Questziel":
        end_lines.append("Boss Encounter")
        end_lines.append(_roll_monsters(pc_level, pc_count, dungeon_level, True))
        end_lines.append("Schatzhort als Belohnung")
        end_lines.append(generate_hoard_treasure(cr_band))
    else:
        end_lines.append("Abstieg gesichert, kleiner Abschiedsschatz")
        end_lines.append(generate_single_treasure(cr_band))

    end_txt = "\n".join(end_lines)
    sections.append(
        f"<b>Raum {rooms}: Finale</b>\n"
        f"<span class=\"tg-spoiler\">{html.escape(end_txt)}</span>\n"
    )

    chunks: List[str] = []
    current = ""
    for part in sections:
        if len(current) + len(part) > 3500:
            chunks.append(current)
            current = ""
        current += part
    if current:
        chunks.append(current)

    for ch in chunks:
        await update.message.reply_text(ch, parse_mode="HTML")


# -----------------------
# HELP
# -----------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧰 Befehle\n\n"
        "/help  diese Hilfe\n"
        "/roll <Ausdruck>  Würfeln, z.B. /roll 1d6 oder /roll 2d20+3 oder /roll 1d20+2d6+3 (auch 1w6)\n"
        "/rollplayerbehaviour  1W6 Rollplay Verhalten Tabelle\n"
        "/rollchance  Skillwurf plus SG und Belohnung\n"
        "/rollhunt  Jagdwurf mit Mod Auswahl\n"
        "/rollwaldkarte  zieht eine Waldkarte (Skillchance, Ruhe, Entdeckung, Encounter, Hort, NPC)\n"
        "/rollschatz  Schatz würfeln (Einzeln oder Hort, dann HG Auswahl)\n"
        "/rolldungeon [pc_lvl] [pc_count] [dungeon_lvl] [rooms]  Dungeon als Würfelsystem, alle Räume auf einmal als Spoiler\n\n"
        "🌍 Biom\n"
        "/setbiom <Biom>  setzt dein aktuelles Biom (oder ohne Parameter per Buttons)\n"
        "/biom  zeigt dein aktuelles Biom\n"
        "/rollbiom [Biom]  würfelt das nächste Biom (optional vorher setzen)\n\n"
        "⚔️ Encounters\n"
        "/rollencounter [Biom]  würfelt einen Encounter (nutzt sonst dein aktuelles Biom)\n"
        "/encdebug  zeigt welche Encounter Tabellen wirklich geladen wurden\n\n"
        "🔮 Orakel\n"
        "/rolloracle [Frage]  Ja Nein Orakel\n"
        "/cancel  bricht Orakel oder Encounter oder Schatz Auswahl ab"
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
    app.add_handler(CommandHandler("rollplayerbehaviour", rollplayerbehaviour))
    app.add_handler(CommandHandler("rollchance", rollchance))

    app.add_handler(CommandHandler("rollhunt", rollhunt))
    app.add_handler(CallbackQueryHandler(rollhunt_pick_mod, pattern=r"^hunt_mod:"))
    app.add_handler(CallbackQueryHandler(rollhunt_cancel_cb, pattern=r"^hunt_cancel$"))

    app.add_handler(CommandHandler("rollwaldkarte", rollwaldkarte))
    app.add_handler(CallbackQueryHandler(rollwaldkarte_pick_level, pattern=r"^waldkarte_level:"))

    app.add_handler(CommandHandler("setbiom", setbiom))
    app.add_handler(CommandHandler("biom", biom))
    app.add_handler(CommandHandler("rollbiom", rollbiom))
    app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))

    app.add_handler(CommandHandler("encdebug", encdebug))

    treasure_conv = ConversationHandler(
        entry_points=[CommandHandler("rollschatz", rollschatz_start)],
        states={
            TREASURE_PICK_TYPE: [
                CallbackQueryHandler(rollschatz_pick_type, pattern=r"^treasure_type:"),
                CallbackQueryHandler(rollschatz_cancel_cb, pattern=r"^treasure_cancel$"),
            ],
            TREASURE_PICK_CR: [
                CallbackQueryHandler(rollschatz_pick_cr, pattern=r"^treasure_cr:"),
                CallbackQueryHandler(rollschatz_cancel_cb, pattern=r"^treasure_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", rollschatz_cancel_cb)],
        allow_reentry=True,
    )
    app.add_handler(treasure_conv)

    app.add_handler(CommandHandler("rolldungeon", rolldungeon))

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

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=f"{base_url}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
