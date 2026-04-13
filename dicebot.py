import os
import random
import re
import math
import html
import asyncio
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from aiohttp import web
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
_ROLL_CMD_PREFIX = re.compile(r"^/roll(?:@\w+)?\s*", re.IGNORECASE)
_ROLL_NOTE_SPLIT = re.compile(r"^(.*?)(?:\s+#notiz:\s*(.+))?$", re.IGNORECASE)

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


def _extract_roll_jobs(message_text: str) -> List[Tuple[str, Optional[str]]]:
    jobs: List[Tuple[str, Optional[str]]] = []

    for raw_line in (message_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        line = _ROLL_CMD_PREFIX.sub("", line, count=1).strip()
        if not line:
            continue

        note_match = _ROLL_NOTE_SPLIT.match(line)
        if not note_match:
            raise ValueError("Ungültiges Format")

        expr = (note_match.group(1) or "").strip()
        note = (note_match.group(2) or "").strip() or None

        if not expr:
            raise ValueError("Bitte gib vor der #Notiz einen Würfelausdruck an")

        jobs.append((expr, note))

    if not jobs:
        raise ValueError("Bitte gib mindestens einen Würfelausdruck an")

    if len(jobs) > 20:
        raise ValueError("Maximal 20 Würfe pro Nachricht")

    return jobs


async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = (update.message.text or "").strip() if update.message else ""

    try:
        jobs = _extract_roll_jobs(raw_text)
    except ValueError as e:
        await update.message.reply_text(
            f"Ungültiges Format.\n{e}\n\n"
            "Beispiele:\n"
            "/roll 1d6\n"
            "/roll 2d20+3\n"
            "/roll 1d20+2d6+3\n"
            "/roll 1d20+6 #Notiz: Flammenklinge\n\n"
            "Mehrere Würfe in einer Nachricht:\n"
            "/roll 1d20+6 #Notiz: Flammenklinge\n"
            "/roll 1d20+6 #Notiz: Heilige Flamme"
        )
        return

    results: List[str] = []

    for idx, (expr, note) in enumerate(jobs, start=1):
        try:
            normalized, total, details = parse_roll_expression(expr)
        except ValueError as e:
            prefix = f"Wurf {idx}: " if len(jobs) > 1 else ""
            await update.message.reply_text(
                f"Ungültiges Format.\n{prefix}{e}\n\n"
                "Beispiele:\n"
                "/roll 1d6\n"
                "/roll 2d20+3\n"
                "/roll 1d20+2d6+3\n"
                "/roll 1d20+6 #Notiz: Flammenklinge"
            )
            return

        block_lines = []
        if len(jobs) > 1:
            block_lines.append(f"{idx}.")
        if note:
            block_lines.append(f"Notiz: {note}")
        block_lines.append(f"🎲 {normalized}")
        block_lines.append("Details:")
        block_lines.extend(details)
        block_lines.append("")
        block_lines.append(f"Summe: {total}")
        results.append("\n".join(block_lines))

    msg = "\n\n".join(results)
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

    roll_ = random.randint(1, 100)
    for s, e, txt in table:
        if s <= roll_ <= e:
            return roll_, txt

    return roll_, "Nichts gefunden. Deine Tabelle hat an der Stelle vermutlich eine Lücke."

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

    biom_ = query.data.split(":", 1)[1]
    context.user_data["enc_biome"] = biom_

    await query.edit_message_text(f"⚔️ Biom: {biom_}\nWelche Stufe?", reply_markup=build_encounter_level_keyboard())
    return ENC_PICK_LEVEL

async def rollencounter_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    level = query.data.split(":", 1)[1]
    biom_ = (context.user_data.get("enc_biome") or "").strip()

    try:
        w100, encounter_raw = pick_encounter(biom_, level)
        encounter_rolled, dice_details = roll_inline_w_dice(encounter_raw)

        msg = (
            f"⚔️ Encounter\n"
            f"Biom: {_canonical_enc_biom(biom_)}\n"
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
            "Check Level Auswahl und die Überschrift in encounters_de.txt.\n"
            "Wenn du auf Render bist, check ob auf dem Server wirklich die aktuelle Datei liegt."
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
    for b in sorted(ENCOUNTERS.keys()):
        lvls = ", ".join(sorted(ENCOUNTERS[b].keys()))
        lines.append(f"{b}: {lvls}")

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
        await update.message.reply_text("🌲 Waldkarte\nErgebnis: Encounter\nWähle die Stufe:", reply_markup=build_waldkarte_level_keyboard())
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
        await update.message.reply_text("🌲 Waldkarte\nErgebnis: Kreaturenhort\nWähle die Stufe:", reply_markup=build_waldkarte_level_keyboard())
        return

    await update.message.reply_text(
        f"🌲 Waldkarte\nW18: {roll18}\nErgebnis: NPC\nEin NPC gibt dir eine Wegbeschreibung zum Portal oder die Info, die du suchst."
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
    3: ("Neutral Neutral", "Du stehst vor einer Tür, was tust du? Schlüssel ins Schloss stecken."),
    4: ("Neutral Prüfend", "Du stehst vor einer Tür, was tust du? An der Tür stehen, Schlüssel betrachten und ins Schloss stecken."),
    5: ("Logisch", "Du stehst vor einer Tür, was tust du? An der Tür lauschen und Entscheidung treffen, ggf die Tür zu öffnen."),
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
        detail = f"{coin}: {count}W{sides} x {_fmt_int(mult)} = {_fmt_int(total)} (Basis {base}, Würfe: {', '.join(map(str, rolls))})"
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

def _pick_range_table(table: List[Tuple[int, int, object]], roll_: int):
    for a, b, payload in table:
        if a <= roll_ <= b:
            return payload
    return None

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

# -----------------------
# MAGIC TABLES A BIS I (aus Datei)
# -----------------------

MAGIC_TABLES: Dict[str, List[Tuple[int, int, str]]] = {}

def _load_magic_raw_text() -> str:
    path = Path(__file__).with_name("Magische Gegenstände Tabelle.txt")
    if not path.exists():
        path = Path(__file__).with_name("magische_gegenstaende_tabelle.txt")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")

def _clean_magic_line(ln: str) -> str:
    if ln is None:
        return ""
    s = ln.replace("\ufeff", "")
    s = s.replace("\u00a0", " ")
    for ch in ["\u2013", "\u2014", "\u2212", "\u2011"]:
        s = s.replace(ch, "-")
    return s.strip()

def _normalize_magic_item_text(txt: str) -> str:
    t = (txt or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t

def _load_magic_tables_from_text(text: str) -> Dict[str, List[Tuple[int, int, str]]]:
    lines = [_clean_magic_line(ln) for ln in text.splitlines()]

    head_re = re.compile(r"^\s*Magische\s+Gegenstände\s+Tabelle\s+([A-I])\s*$", re.IGNORECASE)
    entry_re = re.compile(
        r"^(?P<s>\d{2}|00)\s*(?:-|bis)\s*(?P<e>\d{2}|00)\s*:\s*(?P<item>.+?)\s*$",
        re.IGNORECASE,
    )
    entry_single_re = re.compile(
        r"^(?P<s>\d{2}|00)\s*:\s*(?P<item>.+?)\s*$",
        re.IGNORECASE,
    )

    data: Dict[str, List[Tuple[int, int, str]]] = {}
    cur: Optional[str] = None

    for ln in lines:
        if not ln:
            continue

        m_head = head_re.match(ln)
        if m_head:
            cur = m_head.group(1).upper()
            data.setdefault(cur, [])
            continue

        if cur is None:
            continue

        low = ln.lower()

        if low.startswith("w100"):
            continue
        if "w8 ergebnisse" in low or "w12 ergebnisse" in low:
            continue
        if low.startswith("w8") or low.startswith("w12"):
            continue

        m_ent = entry_re.match(ln)
        if m_ent:
            s = _to_int_w100(m_ent.group("s"))
            e = _to_int_w100(m_ent.group("e"))
            if s > e:
                s, e = e, s
            item = _normalize_magic_item_text(m_ent.group("item"))
            data.setdefault(cur, []).append((s, e, item))
            continue

        m_one = entry_single_re.match(ln)
        if m_one:
            s = _to_int_w100(m_one.group("s"))
            item = _normalize_magic_item_text(m_one.group("item"))
            data.setdefault(cur, []).append((s, s, item))
            continue

    for k in list(data.keys()):
        data[k].sort(key=lambda x: (x[0], x[1]))

    return data

def init_magic_tables():
    global MAGIC_TABLES
    raw = _load_magic_raw_text()
    MAGIC_TABLES = _load_magic_tables_from_text(raw) if raw.strip() else {}

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
    if table_letter == "G" and "Figur der wundersamen Kraft" in item:
        r8 = random.randint(1, 8)
        item = f"Figur der wundersamen Kraft ({FIGURINES_W8[r8]})"
        extra_details.append(f"W8 Figur: {r8} -> {FIGURINES_W8[r8]}")

    if table_letter == "I" and "Magische Rüstung" in item:
        r12 = random.randint(1, 12)
        item = f"Magische Rüstung ({MAGIC_ARMOR_W12[r12]})"
        extra_details.append(f"W12 Rüstung: {r12} -> {MAGIC_ARMOR_W12[r12]}")

    return r, item, extra_details

HOARD_COINS: Dict[str, List[Tuple[str, int, int, int]]] = {
    "0-4": [("KM", 6, 6, 100), ("SM", 3, 6, 100), ("GM", 2, 6, 10)],
    "5-10": [("KM", 2, 6, 100), ("SM", 2, 6, 1000), ("GM", 6, 6, 100), ("PM", 3, 6, 10)],
    "11-16": [("KM", 2, 6, 100), ("SM", 2, 6, 1000), ("GM", 6, 6, 100), ("PM", 3, 6, 10)],
    "17+": [("GM", 12, 6, 1000), ("PM", 8, 6, 1000)],
}

HOARD_LOOT: Dict[str, List[Tuple[int, int, Optional[Tuple[str, Tuple[int, int], int]], List[Tuple[str, str]]]]] = {
    "0-4": [
        (1, 30, ("gems", (2, 6), 10), []),
        (31, 60, ("gems", (2, 6), 50), [("A", "1W6")]),
        (61, 80, ("art", (1, 4), 25), [("A", "1W4")]),
        (81, 95, ("art", (1, 4), 50), [("B", "1W4")]),
        (96, 100, ("gems", (2, 6), 100), [("B", "1W4"), ("C", "1W2")]),
    ],
    "5-10": [
        (1, 20, ("gems", (2, 6), 50), [("A", "1W4")]),
        (21, 45, ("art", (2, 4), 25), [("A", "1W6")]),
        (46, 70, ("gems", (2, 6), 100), [("B", "1W4")]),
        (71, 90, ("art", (2, 4), 50), [("B", "1W6")]),
        (91, 100, ("gems", (2, 6), 250), [("C", "1W4"), ("D", "1W2")]),
    ],
    "11-16": [
        (1, 15, ("art", (2, 4), 250), [("C", "1W4")]),
        (16, 40, ("gems", (2, 6), 250), [("C", "1W6")]),
        (41, 65, ("art", (2, 4), 750), [("D", "1W4")]),
        (66, 85, ("gems", (2, 6), 500), [("D", "1W6")]),
        (86, 100, ("art", (2, 4), 2500), [("E", "1W2"), ("F", "1W4")]),
    ],
    "17+": [
        (1, 10, ("gems", (2, 6), 1000), [("D", "1W4"), ("F", "1W4")]),
        (11, 35, ("art", (2, 4), 2500), [("E", "1W2"), ("F", "1W6")]),
        (36, 60, ("gems", (2, 6), 5000), [("F", "1W4"), ("G", "1W4")]),
        (61, 85, ("art", (2, 4), 7500), [("G", "1W6")]),
        (86, 100, ("gems", (2, 6), 10000), [("H", "1W2"), ("I", "1")]),
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

def _roll_individual_treasure(cr_key: str) -> str:
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
        "Ergebnis:\n" + "\n".join(lines)
    )

    if details:
        msg += "\n\nWürfe:\n" + "\n".join(details)

    return msg

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
    loot_table = HOARD_LOOT.get(cr_key, [])
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
        msg = _roll_individual_treasure(cr_key)

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
# ROLLDUNGEON SYSTEM
# -----------------------

DUNGEON_PICK_LEVEL = 300
DUNGEON_PICK_PLAYERS = 301

DUNGEON_THEMES = [
    "Verlassene Zwergenmine",
    "Vergessener Tempel",
    "Magierlabor",
    "Nekropole unter der Stadt",
    "Räuberhöhle mit Schmugglertunneln",
    "Versunkene Ruine",
    "Unterreich Außenposten",
    "Kristallhöhlen voller Echo",
]

DUNGEON_GOALS = [
    "Berge ein Artefakt",
    "Rette eine gefangene Person",
    "Finde einen geheimen Durchgang",
    "Zerstöre eine Quelle der Verderbnis",
    "Stiehl Beweise oder Dokumente",
    "Töte oder vertreibe den Anführer",
]

ROOM_LAYOUT = [
    "enger Korridor",
    "breite Halle",
    "runde Kammer",
    "verzweigte Kreuzung",
    "eingestürzte Höhle",
    "Treppe in die Tiefe",
    "Brücke über eine Schlucht",
    "kleine Nische hinter Steinplatten",
]

ROOM_MOOD = [
    "flackerndes Licht und lange Schatten",
    "klamme Kälte, Atem wird sichtbar",
    "schwerer Staub, jeder Schritt knirscht",
    "feuchte Wände, irgendwo tropft Wasser",
    "leises Flüstern, als ob die Steine reden",
    "spürbare Magie, Haare stellen sich auf",
    "Brandgeruch, Asche auf dem Boden",
    "unheimliche Stille, kein Echo",
    "dicke Spinnweben, alte Kokons",
    "frische Kratzspuren, hier ist etwas aktiv",
]

ROOM_TYPE = [
    "Kampf",
    "Falle",
    "Rätsel",
    "Entdeckung",
    "Soziales",
    "Schatz",
]

COMPLICATION = [
    "Zeitdruck, etwas nähert sich",
    "Alarmmechanismus, Fehler ruft Verstärkung",
    "Giftige Luft, langes Verweilen kostet Ausdauer",
    "Magischer Nebel, Sicht ist stark eingeschränkt",
    "Der Boden ist instabil, Gefahr einzustürzen",
    "Ein Fluch liegt auf dem Raum, kurze Nebenwirkung",
]

def _tg_spoiler(text: str) -> str:
    return f"<tg-spoiler>{html.escape(text)}</tg-spoiler>"

def _dungeon_dc(level: int, hard: bool = False) -> int:
    base = 10 + (level // 3)
    if hard:
        base += 2
    return clamp(base, 10, 22)

def _room_count(level: int, players: int) -> int:
    base = 2 + math.ceil(level / 4)
    party_adj = round((players - 3) / 2)
    n = base + party_adj + random.randint(0, 3)
    return clamp(n, 3, 12)

def _pick_encounter_style(level: int, players: int) -> str:
    styles = [
        "Schwarm kleiner Gegner",
        "ein Elite Gegner mit Support",
        "Hinterhalt aus Deckung",
        "Patrouille, die Verstärkung rufen kann",
        "Mini Boss mit Terrain Vorteil",
        "zwei Fraktionen, die sich gerade bekämpfen",
    ]
    hint = "leichter" if level <= 4 else "normal"
    if level >= 11:
        hint = "hart"
    if players <= 2:
        hint = "eher kleiner"
    if players >= 5:
        hint = "eher größer"
    return f"{random.choice(styles)} (Skalierung: {hint})"

def _pick_trap(level: int) -> str:
    traps = [
        "Druckplatte löst Pfeilsalve aus",
        "Fallgrube mit Stacheln",
        "Schwingende Klinge aus der Wand",
        "Runensiegel, das Blitzschaden entlädt",
        "Gasfalle, die Sicht und Atmung stört",
        "Einsturz, wenn zu viel Gewicht drauf kommt",
    ]
    dc = _dungeon_dc(level, hard=False)
    return f"{random.choice(traps)} (SG {dc} entdecken oder entschärfen)"

def _pick_puzzle(level: int) -> str:
    puzzles = [
        "Drehbare Statuen, die in die richtige Richtung zeigen müssen",
        "Runenreihenfolge, die den Raum entsperrt",
        "Gewichtsrätsel mit vier Sockeln",
        "Lichtstrahlen über Spiegel umleiten",
        "Zahlenmuster, das eine Tür entriegelt",
        "Rätselspruch auf einer Säule, Antwort öffnet einen Mechanismus",
    ]
    dc = _dungeon_dc(level, hard=True)
    return f"{random.choice(puzzles)} (SG {dc} für Analyse oder Werkzeug)"

def _pick_discovery(level: int) -> str:
    finds = [
        "alte Karte mit Abkürzung",
        "Tagebuch mit Hinweis auf den Boss",
        "geheime Hebelwand, die einen Raum überspringt",
        "Ritualkreis, der kurz einen Buff gibt",
        "Wandrelief mit Lore und Warnung",
        "Spuren, die zeigen, wo die Beute gelagert ist",
    ]
    dc = _dungeon_dc(level, hard=False)
    return f"{random.choice(finds)} (SG {dc} Wahrnehmung oder Investigation)"

def _pick_social(level: int) -> str:
    npcs = [
        "verletzter Kundschafter, der raus will",
        "Gefangener, der einen Deal anbietet",
        "unsicherer Kultist, der zweifelt",
        "geistiger Wächter, der Fragen stellt",
        "kleines Monster, das handeln will",
        "Söldnertrupp, der auch hier ist",
    ]
    dc = _dungeon_dc(level, hard=False)
    return f"{random.choice(npcs)} (SG {dc} Überreden oder Einschüchtern, wenn nötig)"

def _pick_treasure(level: int) -> str:
    loot = [
        "versteckte Truhe hinter losen Steinen",
        "Opfergabe auf einem Altar",
        "Gürteltasche an einem Skelett",
        "Kiste mit Siegel, das erst geknackt werden muss",
        "Schmugglersafe in der Wand",
        "magisch versiegeltes Kästchen",
    ]
    dc = _dungeon_dc(level, hard=False)
    return f"{random.choice(loot)} (Tipp: /rollschatz passend zum HG)"

def _generate_room(i: int, n: int, level: int, players: int) -> str:
    layout = random.choice(ROOM_LAYOUT)
    mood = random.choice(ROOM_MOOD)
    rtype = random.choice(ROOM_TYPE)
    comp = random.choice(COMPLICATION)

    if i == n:
        rtype = "Finale"

    title = f"Raum {i} von {n}"
    header = f"{title}\nLayout: {layout}\nStimmung: {mood}"

    if rtype == "Kampf":
        body = f"Inhalt: Kampf\nBegegnung: {_pick_encounter_style(level, players)}\nKomplikation: {comp}"
    elif rtype == "Falle":
        body = f"Inhalt: Falle\nFalle: {_pick_trap(level)}\nKomplikation: {comp}"
    elif rtype == "Rätsel":
        body = f"Inhalt: Rätsel\nRätsel: {_pick_puzzle(level)}\nKomplikation: {comp}"
    elif rtype == "Entdeckung":
        body = f"Inhalt: Entdeckung\nFund: {_pick_discovery(level)}\nKomplikation: {comp}"
    elif rtype == "Soziales":
        body = f"Inhalt: Soziales\nNSC: {_pick_social(level)}\nKomplikation: {comp}"
    elif rtype == "Schatz":
        body = f"Inhalt: Schatz\nBeute: {_pick_treasure(level)}\nKomplikation: {comp}"
    else:
        boss = _pick_encounter_style(level + 3, players)
        body = (
            "Inhalt: Finale\n"
            f"Boss Szene: {boss}\n"
            "Belohnung: Kreaturenhort oder Schlüssel zum Ziel\n"
            "Tipp: Wenn du Loot willst, nimm /rollschatz als Schatzhort"
        )

    return header + "\n" + body

def build_dungeon_level_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for lvl in range(1, 21):
        row.append(InlineKeyboardButton(str(lvl), callback_data=f"dungeon_lvl:{lvl}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Abbrechen", callback_data="dungeon_cancel")])
    return InlineKeyboardMarkup(rows)

def build_dungeon_players_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(str(n), callback_data=f"dungeon_ply:{n}") for n in range(1, 7)]]
    rows.append([InlineKeyboardButton("Abbrechen", callback_data="dungeon_cancel")])
    return InlineKeyboardMarkup(rows)

async def rolldungeon_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("dungeon_level", None)
    context.user_data.pop("dungeon_players", None)

    if len(context.args) >= 2:
        try:
            lvl = int(context.args[0])
            ply = int(context.args[1])
            if not (1 <= lvl <= 20 and 1 <= ply <= 6):
                raise ValueError
            text = _build_dungeon_output(lvl, ply)
            await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
            return ConversationHandler.END
        except Exception:
            pass

    await update.message.reply_text("🏰 Rolldungeon\nWähle das Spielerlevel:", reply_markup=build_dungeon_level_keyboard())
    return DUNGEON_PICK_LEVEL

def _build_dungeon_output(level: int, players: int) -> str:
    theme = random.choice(DUNGEON_THEMES)
    goal = random.choice(DUNGEON_GOALS)
    n = _room_count(level, players)

    header = (
        "🏰 <b>Rolldungeon</b>\n"
        f"Level: <b>{level}</b> | Spieler: <b>{players}</b> | Räume: <b>{n}</b>\n"
        "Tipp: Wenn du per Hand spoilern willst: STRG+SHIFT+P pro Raum\n"
        f"Thema: <b>{html.escape(theme)}</b>\n"
        f"Ziel: <b>{html.escape(goal)}</b>\n\n"
        "Alle Räume sind Spoiler. Antippen zum Aufdecken.\n"
    )

    rooms = []
    for i in range(1, n + 1):
        room_txt = _generate_room(i, n, level, players)
        rooms.append(_tg_spoiler(room_txt))

    return header + "\n\n".join(rooms) + "\n\nViel Spaß 😊"

async def rolldungeon_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lvl = int(query.data.split(":", 1)[1])
    context.user_data["dungeon_level"] = lvl

    await query.edit_message_text("Wie viele Spieler?", reply_markup=build_dungeon_players_keyboard())
    return DUNGEON_PICK_PLAYERS

async def rolldungeon_pick_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ply = int(query.data.split(":", 1)[1])
    lvl = int(context.user_data.get("dungeon_level", 1))

    text = _build_dungeon_output(lvl, ply)
    await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
    return ConversationHandler.END

async def rolldungeon_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Rolldungeon abgebrochen 🙂")
    return ConversationHandler.END

async def rolldungeon_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Rolldungeon abgebrochen 🙂")
    return ConversationHandler.END


# -----------------------
# MONSTERREAKTION / MORAL
# -----------------------

REAK_NATUR, REAK_DISZIPLIN, REAK_ZIEL, REAK_HP, REAK_LAGE, REAK_AUSLOESER = range(6)

REACTION_NATURE_CHOICES = [
    ("Friedlich", "friedlich"),
    ("Kompromissbereit", "kompromissbereit"),
    ("Neutral", "neutral"),
    ("Territorial", "territorial"),
    ("Räuberisch", "räuberisch"),
    ("Boshaft", "boshaft"),
    ("Fanatisch", "fanatisch"),
]
REACTION_DISCIPLINE_CHOICES = [
    ("Feige", "feige"),
    ("Normal", "normal"),
    ("Brutal", "brutal"),
    ("Fanatisch", "fanatisch"),
    ("Geistlos", "geistlos"),
]
REACTION_GOAL_CHOICES = [
    ("Warnen", "warnen"),
    ("Vertreiben", "vertreiben"),
    ("Beute machen", "beute machen"),
    ("Töten", "töten"),
    ("Bewachen / Nest / Schatz", "bewachen"),
]
REACTION_HP_CHOICES = [
    ("76–100%", "76-100%"),
    ("51–75%", "51-75%"),
    ("26–50%", "26-50%"),
    ("11–25%", "11-25%"),
    ("1–10%", "1-10%"),
]
REACTION_SITUATION_CHOICES = [
    ("Überlegen", "überlegen"),
    ("Ausgeglichen", "ausgeglichen"),
    ("Unterlegen", "unterlegen"),
    ("Eingekesselt", "eingekesselt"),
]
REACTION_TRIGGER_CHOICES = [
    ("Gruppe freundlich", "gruppe freundlich"),
    ("Gruppe vorsichtig", "gruppe vorsichtig"),
    ("Gruppe provoziert", "gruppe provoziert"),
    ("Gruppe hat angegriffen", "gruppe hat schon angegriffen"),
    ("Anführer tot / halbe Gruppe gefallen", "anführer tot / halbe gruppe gefallen"),
]

REACTION_CHOICES_BY_STATE = {
    REAK_NATUR: REACTION_NATURE_CHOICES,
    REAK_DISZIPLIN: REACTION_DISCIPLINE_CHOICES,
    REAK_ZIEL: REACTION_GOAL_CHOICES,
    REAK_HP: REACTION_HP_CHOICES,
    REAK_LAGE: REACTION_SITUATION_CHOICES,
    REAK_AUSLOESER: REACTION_TRIGGER_CHOICES,
}
REACTION_KEY_BY_STATE = {
    REAK_NATUR: "nature",
    REAK_DISZIPLIN: "discipline",
    REAK_ZIEL: "goal",
    REAK_HP: "hp",
    REAK_LAGE: "situation",
    REAK_AUSLOESER: "trigger",
}
REACTION_TITLE_BY_STATE = {
    REAK_NATUR: "1. Grundnatur?",
    REAK_DISZIPLIN: "2. Disziplin?",
    REAK_ZIEL: "3. Aktuelles Ziel?",
    REAK_HP: "4. HP-Status?",
    REAK_LAGE: "5. Lage?",
    REAK_AUSLOESER: "6. Auslöser?",
}

NATURE_MOD = {
    "friedlich": 4,
    "kompromissbereit": 2,
    "neutral": 0,
    "territorial": -1,
    "räuberisch": -2,
    "raeuberisch": -2,
    "boshaft": -4,
    "fanatisch": -6,
}
HP_CONTACT_MOD = {
    "76-100%": 0,
    "51-75%": 0,
    "26-50%": 1,
    "11-25%": 3,
    "1-10%": 5,
}
SITUATION_CONTACT_MOD = {
    "überlegen": -2,
    "ueberlegen": -2,
    "ausgeglichen": 0,
    "unterlegen": 2,
    "eingekesselt": -1,
}
TRIGGER_CONTACT_MOD = {
    "gruppe freundlich": 2,
    "gruppe vorsichtig": 1,
    "gruppe provoziert": -2,
    "gruppe hat schon angegriffen": -4,
    "anführer tot / halbe gruppe gefallen": 0,
    "anfuhrer tot / halbe gruppe gefallen": 0,
}
DISCIPLINE_WILL = {
    "feige": 2,
    "normal": 4,
    "brutal": 6,
    "fanatisch": 8,
    "geistlos": 10,
}
SITUATION_WILL_MOD = {
    "überlegen": 2,
    "ueberlegen": 2,
    "ausgeglichen": 0,
    "unterlegen": -2,
    "eingekesselt": -2,
}
GOAL_WILL_MOD = {
    "warnen": 0,
    "vertreiben": 1,
    "beute machen": 0,
    "töten": 1,
    "bewachen": 2,
}
HP_WILL_MOD = {
    "76-100%": 0,
    "51-75%": 0,
    "26-50%": -1,
    "11-25%": -3,
    "1-10%": -5,
}


def _roll_2d6() -> Tuple[int, List[int]]:
    dice = [random.randint(1, 6), random.randint(1, 6)]
    return sum(dice), dice


def _fmt_2d6(total: int, dice: List[int]) -> str:
    return f"{total} ({dice[0]}+{dice[1]})"


def _normalize_choice(value: str) -> str:
    return (value or "").strip().lower().replace("ö", "o").replace("ü", "u").replace("ä", "a")


def _canon_choice(value: str) -> str:
    raw = (value or "").strip().lower()
    mapping = {
        "raeuberisch": "räuberisch",
        "ueberlegen": "überlegen",
        "anfuhrer tot / halbe gruppe gefallen": "anführer tot / halbe gruppe gefallen",
    }
    return mapping.get(raw, raw)


def _display_choice(value: str) -> str:
    canonical = _canon_choice(value)
    for choices in REACTION_CHOICES_BY_STATE.values():
        for label, stored in choices:
            if stored == canonical:
                return label
    return canonical.capitalize()


def _parse_choice_from_pairs(text_value: str, choices: List[Tuple[str, str]]) -> Optional[str]:
    raw = (text_value or "").strip().lower()
    values = [value for _, value in choices]
    if raw in values:
        return raw
    nr = re.match(r"^(\d+)", raw)
    if nr:
        idx = int(nr.group(1)) - 1
        if 0 <= idx < len(choices):
            return choices[idx][1]
    normalized_map: Dict[str, str] = {}
    for label, value in choices:
        normalized_map[_normalize_choice(label)] = value
        normalized_map[_normalize_choice(value)] = value
    return normalized_map.get(_normalize_choice(raw))


def _build_reaction_keyboard(step: int) -> InlineKeyboardMarkup:
    choices = REACTION_CHOICES_BY_STATE[step]
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for idx, (label, _value) in enumerate(choices):
        row.append(InlineKeyboardButton(label, callback_data=f"reaktion:{step}:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Abbrechen", callback_data="reaktion_cancel")])
    return InlineKeyboardMarkup(rows)


def _reaction_prompt(step: int) -> str:
    return (
        f"👁️ /reaktion\n\n{REACTION_TITLE_BY_STATE[step]}\n"
        "Wähle per Button oder antworte mit Zahl oder Wort.\n"
        "/cancel bricht ab."
    )


async def _send_reaction_prompt(update: Update, step: int, prefix: Optional[str] = None) -> None:
    text = _reaction_prompt(step)
    if prefix:
        text = f"{prefix}\n\n{text}"
    reply_markup = _build_reaction_keyboard(step)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)


def _special_low_hp_result(nature: str, discipline: str) -> Tuple[str, int]:
    if discipline in ("fanatisch", "geistlos"):
        return ("Kämpft weiter (Sonderregel für Fanatiker/Untote/Konstrukte)", 10)
    roll = random.randint(1, 10)
    if nature in ("friedlich", "neutral", "territorial", "räuberisch"):
        if roll <= 8:
            return (f"Flucht (W10={roll})", roll)
        if roll == 9:
            return (f"Defensive Gegenwehr (W10={roll})", roll)
        return (f"Bleibt im Kampf (W10={roll})", roll)
    if nature in ("kompromissbereit",):
        if roll <= 5:
            return (f"Flucht (W10={roll})", roll)
        if roll <= 8:
            return (f"Aufgabe / Verhandlung (W10={roll})", roll)
        return (f"Kämpft weiter (W10={roll})", roll)
    if roll <= 4:
        return (f"Flucht (W10={roll})", roll)
    if roll <= 6:
        return (f"Rückzug mit Gegenwehr (W10={roll})", roll)
    return (f"Kämpft weiter (W10={roll})", roll)


def generate_reaction_report(data: Dict[str, str]) -> str:
    nature = _canon_choice(data["nature"])
    discipline = _canon_choice(data["discipline"])
    goal = _canon_choice(data["goal"])
    hp = data["hp"]
    situation = _canon_choice(data["situation"])
    trigger = _canon_choice(data["trigger"])

    contact_total, contact_dice = _roll_2d6()
    contact_mod = (
        NATURE_MOD.get(nature, 0)
        + HP_CONTACT_MOD.get(hp, 0)
        + SITUATION_CONTACT_MOD.get(situation, 0)
        + TRIGGER_CONTACT_MOD.get(trigger, 0)
    )
    contact_score = contact_total + contact_mod

    if contact_score <= 4:
        contact_result = "Angriff"
    elif contact_score <= 7:
        contact_result = "Drohen / Vertreiben"
    elif contact_score <= 10:
        contact_result = "Beobachten / Abwarten"
    elif contact_score <= 13:
        contact_result = "Reden / Deal möglich"
    else:
        contact_result = "Freundlich / lässt passieren / zieht ab"

    will_base = DISCIPLINE_WILL.get(discipline, 4)
    will_mod = (
        SITUATION_WILL_MOD.get(situation, 0)
        + GOAL_WILL_MOD.get(goal, 0)
        + HP_WILL_MOD.get(hp, 0)
    )
    if trigger == "anführer tot / halbe gruppe gefallen":
        will_mod -= 4

    will_score = will_base + will_mod
    special_low_hp_line = None
    if hp == "1-10%":
        special_low_hp_line, _ = _special_low_hp_result(nature, discipline)

    if special_low_hp_line:
        morale_result = special_low_hp_line
    elif will_score <= 2:
        morale_result = "Panik, Flucht oder Aufgabe"
    elif will_score <= 5:
        morale_result = "Rückzug / Verhandlung"
    elif will_score <= 8:
        morale_result = "Kämpft defensiv weiter"
    else:
        morale_result = "Kämpft voll weiter"

    lines = [
        "👁️ Reaktions- und Moralwurf",
        "",
        "Eingaben:",
        f"• Grundnatur: {_display_choice(nature)}",
        f"• Disziplin: {_display_choice(discipline)}",
        f"• Ziel: {_display_choice(goal)}",
        f"• HP-Status: {_display_choice(hp)}",
        f"• Lage: {_display_choice(situation)}",
        f"• Auslöser: {_display_choice(trigger)}",
        "",
        "Erstkontakt:",
        f"• 2W6: {_fmt_2d6(contact_total, contact_dice)}",
        f"• Modifikator: {contact_mod:+d}",
        f"• Reaktionswert: {contact_score}",
        f"• Ergebnis: {contact_result}",
        "",
        "Kampf-Moral:",
        f"• Kampfwille: {will_base} {will_mod:+d} = {will_score}",
        f"• Ergebnis: {morale_result}",
    ]
    if hp == "1-10%":
        lines.append("")
        lines.append("Sonderregel 1–10% HP aktiv.")
    return "\n".join(lines)


async def _apply_reaction_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, step: int, choice: str):
    context.user_data.setdefault("reaktion", {})
    context.user_data["reaktion"][REACTION_KEY_BY_STATE[step]] = choice

    if step == REAK_AUSLOESER:
        report = generate_reaction_report(context.user_data["reaktion"])
        context.user_data.pop("reaktion", None)
        context.user_data.pop("reaktion_step", None)
        if update.callback_query:
            await update.callback_query.edit_message_text(report)
        elif update.message:
            await update.message.reply_text(report)
        return ConversationHandler.END

    next_step = step + 1
    context.user_data["reaktion_step"] = next_step
    await _send_reaction_prompt(update, next_step)
    return next_step


async def reaktion_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reaktion"] = {}
    context.user_data["reaktion_step"] = REAK_NATUR
    await _send_reaction_prompt(update, REAK_NATUR)
    return REAK_NATUR


async def reaktion_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _prefix, step_txt, idx_txt = (query.data or "").split(":", 2)
        step = int(step_txt)
        idx = int(idx_txt)
    except (ValueError, AttributeError):
        return context.user_data.get("reaktion_step", REAK_NATUR)

    current_step = context.user_data.get("reaktion_step", REAK_NATUR)
    if step != current_step:
        await query.answer("Diese Auswahl ist nicht mehr aktuell.", show_alert=False)
        await _send_reaction_prompt(update, current_step)
        return current_step

    choices = REACTION_CHOICES_BY_STATE.get(step, [])
    if idx < 0 or idx >= len(choices):
        await query.answer("Ungültige Auswahl.", show_alert=False)
        await _send_reaction_prompt(update, current_step, "Bitte wähle eine gültige Option.")
        return current_step

    return await _apply_reaction_choice(update, context, step, choices[idx][1])


async def reaktion_step_natur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = _parse_choice_from_pairs(update.message.text, REACTION_NATURE_CHOICES)
    if not choice:
        await _send_reaction_prompt(update, REAK_NATUR, "Bitte wähle eine gültige Grundnatur.")
        return REAK_NATUR
    return await _apply_reaction_choice(update, context, REAK_NATUR, choice)


async def reaktion_step_disziplin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = _parse_choice_from_pairs(update.message.text, REACTION_DISCIPLINE_CHOICES)
    if not choice:
        await _send_reaction_prompt(update, REAK_DISZIPLIN, "Bitte wähle eine gültige Disziplin.")
        return REAK_DISZIPLIN
    return await _apply_reaction_choice(update, context, REAK_DISZIPLIN, choice)


async def reaktion_step_ziel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = _parse_choice_from_pairs(update.message.text, REACTION_GOAL_CHOICES)
    if not choice:
        await _send_reaction_prompt(update, REAK_ZIEL, "Bitte wähle ein gültiges Ziel.")
        return REAK_ZIEL
    return await _apply_reaction_choice(update, context, REAK_ZIEL, choice)


async def reaktion_step_hp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = _parse_choice_from_pairs(update.message.text, REACTION_HP_CHOICES)
    if not choice:
        await _send_reaction_prompt(update, REAK_HP, "Bitte wähle einen gültigen HP-Status.")
        return REAK_HP
    return await _apply_reaction_choice(update, context, REAK_HP, choice)


async def reaktion_step_lage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = _parse_choice_from_pairs(update.message.text, REACTION_SITUATION_CHOICES)
    if not choice:
        await _send_reaction_prompt(update, REAK_LAGE, "Bitte wähle eine gültige Lage.")
        return REAK_LAGE
    return await _apply_reaction_choice(update, context, REAK_LAGE, choice)


async def reaktion_step_ausloeser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = _parse_choice_from_pairs(update.message.text, REACTION_TRIGGER_CHOICES)
    if not choice:
        await _send_reaction_prompt(update, REAK_AUSLOESER, "Bitte wähle einen gültigen Auslöser.")
        return REAK_AUSLOESER
    return await _apply_reaction_choice(update, context, REAK_AUSLOESER, choice)


async def reaktion_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("reaktion", None)
    context.user_data.pop("reaktion_step", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("/reaktion abgebrochen 🙂")
    elif update.message:
        await update.message.reply_text("/reaktion abgebrochen 🙂")
    return ConversationHandler.END


async def reaktion_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await reaktion_cancel(update, context)

# -----------------------
# HEALTH / PING (für FastCron)
# -----------------------

async def ping(request):
    return web.Response(text="ok")

# -----------------------
# HELP
# -----------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧰 Befehle\n\n"
        "/help  diese Hilfe\n"
        "/roll <Ausdruck>  Würfeln, z.B. /roll 1d6 oder /roll 2d20+3 oder /roll 1d20+2d6+3 (auch 1w6)\n"
        "/rollchance  Skillwurf plus SG und Belohnung\n"
        "/reaktion  fragt 6 Punkte ab und gibt Erstkontakt + Kampf-Moral aus\n"
        "/rollhunt  Jagdwurf mit Mod Auswahl\n"
        "/rollwaldkarte  zieht eine Waldkarte (Skillchance, Ruhe, Entdeckung, Encounter, Hort, NPC)\n"
        "/rolldungeon  Dungeon Generator mit Spoiler Räumen\n"
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
        "/cancel  bricht Orakel, Encounter, Schatz oder Rolldungeon Auswahl ab"
    )
    await update.message.reply_text(msg)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    base_url = os.environ.get("BASE_URL")
    port = int(os.environ.get("PORT", "10000"))

    if not token or not base_url:
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder BASE_URL fehlt")

    base_url = base_url.rstrip("/")

    ptb_app = Application.builder().token(token).updater(None).build()

    init_encounters()
    init_magic_tables()

    ptb_app.add_handler(CommandHandler("help", help_cmd))
    ptb_app.add_handler(CommandHandler("start", help_cmd))

    ptb_app.add_handler(CommandHandler("roll", roll))
    ptb_app.add_handler(CommandHandler("rollchance", rollchance))
    reaction_conv = ConversationHandler(
        entry_points=[CommandHandler("reaktion", reaktion_start)],
        states={
            REAK_NATUR: [
                CallbackQueryHandler(reaktion_cancel_cb, pattern=r"^reaktion_cancel$"),
                CallbackQueryHandler(reaktion_pick, pattern=r"^reaktion:\d+:\d+$"),
            ],
            REAK_DISZIPLIN: [
                CallbackQueryHandler(reaktion_cancel_cb, pattern=r"^reaktion_cancel$"),
                CallbackQueryHandler(reaktion_pick, pattern=r"^reaktion:\d+:\d+$"),
            ],
            REAK_ZIEL: [
                CallbackQueryHandler(reaktion_cancel_cb, pattern=r"^reaktion_cancel$"),
                CallbackQueryHandler(reaktion_pick, pattern=r"^reaktion:\d+:\d+$"),
            ],
            REAK_HP: [
                CallbackQueryHandler(reaktion_cancel_cb, pattern=r"^reaktion_cancel$"),
                CallbackQueryHandler(reaktion_pick, pattern=r"^reaktion:\d+:\d+$"),
            ],
            REAK_LAGE: [
                CallbackQueryHandler(reaktion_cancel_cb, pattern=r"^reaktion_cancel$"),
                CallbackQueryHandler(reaktion_pick, pattern=r"^reaktion:\d+:\d+$"),
            ],
            REAK_AUSLOESER: [
                CallbackQueryHandler(reaktion_cancel_cb, pattern=r"^reaktion_cancel$"),
                CallbackQueryHandler(reaktion_pick, pattern=r"^reaktion:\d+:\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", reaktion_cancel)],
        allow_reentry=True,
    )
    ptb_app.add_handler(reaction_conv)

    ptb_app.add_handler(CommandHandler("rollhunt", rollhunt))
    ptb_app.add_handler(CallbackQueryHandler(rollhunt_pick_mod, pattern=r"^hunt_mod:"))
    ptb_app.add_handler(CallbackQueryHandler(rollhunt_cancel_cb, pattern=r"^hunt_cancel$"))

    ptb_app.add_handler(CommandHandler("rollwaldkarte", rollwaldkarte))
    ptb_app.add_handler(CallbackQueryHandler(rollwaldkarte_pick_level, pattern=r"^waldkarte_level:"))

    ptb_app.add_handler(CommandHandler("rollplayerbehaviour", rollplayerbehaviour))

    ptb_app.add_handler(CommandHandler("setbiom", setbiom))
    ptb_app.add_handler(CommandHandler("biom", biom))
    ptb_app.add_handler(CommandHandler("rollbiom", rollbiom))
    ptb_app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))

    ptb_app.add_handler(CommandHandler("encdebug", encdebug))

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
    ptb_app.add_handler(encounter_conv)

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
    ptb_app.add_handler(oracle_conv)

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
    ptb_app.add_handler(treasure_conv)

    dungeon_conv = ConversationHandler(
        entry_points=[CommandHandler("rolldungeon", rolldungeon_start)],
        states={
            DUNGEON_PICK_LEVEL: [
                CallbackQueryHandler(rolldungeon_pick_level, pattern=r"^dungeon_lvl:"),
                CallbackQueryHandler(rolldungeon_cancel_cb, pattern=r"^dungeon_cancel$"),
            ],
            DUNGEON_PICK_PLAYERS: [
                CallbackQueryHandler(rolldungeon_pick_players, pattern=r"^dungeon_ply:"),
                CallbackQueryHandler(rolldungeon_cancel_cb, pattern=r"^dungeon_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", rolldungeon_cancel_cmd)],
        allow_reentry=True,
    )
    ptb_app.add_handler(dungeon_conv)

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    async def telegram_webhook(request: web.Request) -> web.Response:
        data = await request.json()
        await ptb_app.update_queue.put(Update.de_json(data=data, bot=ptb_app.bot))
        return web.Response(text="ok", content_type="text/plain")

    aio_app = web.Application()
    aio_app.router.add_get("/", health)
    aio_app.router.add_get("/health", health)
    aio_app.router.add_post("/webhook", telegram_webhook)

    async def on_startup(_app: web.Application) -> None:
        await ptb_app.initialize()
        await ptb_app.start()
        await ptb_app.bot.set_webhook(
            url=f"{base_url}/webhook",
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )

    async def on_cleanup(_app: web.Application) -> None:
        # Wichtig für Render-Deploys:
        # Den Webhook hier NICHT löschen.
        # Sonst kann die alte Instanz beim Shutdown den frisch gesetzten
        # Webhook der neuen Instanz wieder entfernen.
        await ptb_app.stop()
        await ptb_app.shutdown()

    aio_app.on_startup.append(on_startup)
    aio_app.on_cleanup.append(on_cleanup)

    web.run_app(aio_app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
