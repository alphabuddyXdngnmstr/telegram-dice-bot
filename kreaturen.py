"""
Kreaturen- und Zauberbeschreibungen aus dem SRD 5.1 (DE) für den Dice Bot.

- srd_monster_de.json   Wertekästen aller SRD-Kreaturen
- srd_zauber_de.json    Beschreibungen aller SRD-Zauber
- kreaturen_zuordnung.json
      "formen":   Schreibweisen aus den Encountern  -> SRD-Kreatur
      "ersatz":   Kreaturen, die nicht im SRD stehen -> nächstliegende SRD-Kreatur
      "eintraege": Sonderfälle für einzelne Encounter-Texte (Erzähltexte usw.)

Die Erkennung läuft auf dem bereits ausgewürfelten Encountertext:
"7 Kobolde" -> Kobold (7×). Kreaturen ohne Anzahl davor (z. B. "Spuren von Orks")
werden absichtlich nicht gezählt, Texte ohne Kreatur bleiben reiner Text.
"""

import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_BASE = Path(__file__).resolve().parent

MONSTERS: Dict[str, dict] = {}
SPELLS: Dict[str, dict] = {}
_FORMS: Dict[str, Tuple[str, bool]] = {}   # form (lower) -> (SRD-Name, ist_ersatz)
_ENTRY_OVERRIDES: Dict[str, List[dict]] = {}
_SPELL_ALIASES: Dict[str, str] = {}
_MATCH_RE: Optional[re.Pattern] = None
_PAREN_RE: Optional[re.Pattern] = None

TG_LIMIT = 4000  # Telegram erlaubt 4096 Zeichen, etwas Puffer

_COUNT_WORDS = {
    "ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1,
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
}
_COUNT_PATTERN = r"(?P<n>\d+|" + "|".join(sorted(_COUNT_WORDS, key=len, reverse=True)) + r")"
_LETTER = r"A-Za-zÄÖÜäöüß"


def _norm(s: str) -> str:
    s = (s or "").replace("­", "").replace(" ", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def init_kreaturen() -> None:
    global _FORMS, _ENTRY_OVERRIDES, _SPELL_ALIASES, _MATCH_RE, _PAREN_RE

    mon_path = _BASE / "srd_monster_de.json"
    spell_path = _BASE / "srd_zauber_de.json"
    map_path = _BASE / "kreaturen_zuordnung.json"

    # In-place füllen, damit "from kreaturen import MONSTERS" weiter funktioniert
    MONSTERS.clear()
    SPELLS.clear()
    if mon_path.exists():
        for m in json.loads(mon_path.read_text(encoding="utf-8")):
            MONSTERS[m["name"]] = m
    if spell_path.exists():
        for s in json.loads(spell_path.read_text(encoding="utf-8")):
            SPELLS[_norm(s["name"])] = s

    data = {}
    if map_path.exists():
        data = json.loads(map_path.read_text(encoding="utf-8"))

    forms: Dict[str, Tuple[str, bool]] = {}

    # 1) SRD-Namen selbst
    for name in MONSTERS:
        forms[_norm(name)] = (name, False)

    # 2) Schreibweisen (Plural, Dativ, Tippfehler)
    for srd_name, variants in (data.get("formen") or {}).items():
        if srd_name.startswith("_"):
            continue
        for v in variants:
            forms[_norm(v)] = (srd_name, False)

    # 3) Ersatz für Nicht-SRD-Kreaturen
    for srd_name, variants in (data.get("ersatz") or {}).items():
        if srd_name.startswith("_"):
            continue
        for v in variants:
            forms[_norm(v)] = (srd_name, True)

    # Nur Ziele behalten, die es wirklich gibt
    _FORMS = {k: v for k, v in forms.items() if v[0] in MONSTERS}

    _ENTRY_OVERRIDES = {}
    for txt, lst in (data.get("eintraege") or {}).items():
        if txt.startswith("_"):
            continue
        _ENTRY_OVERRIDES[_norm(txt)] = lst

    _SPELL_ALIASES = {_norm(k): v for k, v in (data.get("zauber_aliase") or {}).items()}

    alts = "|".join(re.escape(f) for f in sorted(_FORMS, key=len, reverse=True))
    if alts:
        _MATCH_RE = re.compile(
            rf"(?<![{_LETTER}\d-]){_COUNT_PATTERN}\s+(?P<name>{alts})(?![{_LETTER}-])",
            re.IGNORECASE,
        )
        _PAREN_RE = re.compile(
            rf"(?<![{_LETTER}\d-]){_COUNT_PATTERN}\s+(?P<role>[{_LETTER}][{_LETTER}\- ]{{0,40}}?)\s*\(\s*(?P<name>{alts})\s*\)",
            re.IGNORECASE,
        )
    else:
        _MATCH_RE = None
        _PAREN_RE = None


def _count_value(raw: str) -> int:
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return _COUNT_WORDS.get(raw, 1)


def _override_count(spec, numbers: List[int]) -> int:
    """1, "#2" (zweite Zahl im ausgewürfelten Text) oder "#2+1"."""
    if isinstance(spec, int):
        return spec
    total = 0
    for part in str(spec).split("+"):
        part = part.strip()
        if part.startswith("#"):
            idx = int(part[1:]) - 1
            total += numbers[idx] if 0 <= idx < len(numbers) else 1
        elif part.isdigit():
            total += int(part)
    return max(total, 1)


def _add(found: Dict[str, dict], srd: str, count: int, label: str, ersatz: bool) -> None:
    item = found.setdefault(srd, {"srd": srd, "count": 0, "labels": []})
    item["count"] += count
    item["labels"].append({"label": label, "count": count, "ersatz": ersatz})


def find_creatures(raw_text: str, rolled_text: str) -> List[dict]:
    """Liefert die Kreaturen eines Encounters (zusammengefasst je SRD-Kreatur)."""
    if not MONSTERS or _MATCH_RE is None:
        return []

    found: Dict[str, dict] = {}

    override = _ENTRY_OVERRIDES.get(_norm(raw_text))
    if override is not None:
        # Sonderfall: Anzahl steht entweder fest oder wird über das n-te
        # Würfelergebnis im ausgewürfelten Text bestimmt.
        numbers = [int(x) for x in re.findall(r"\d+", rolled_text)]
        for spec in override:
            srd = spec.get("srd")
            if srd not in MONSTERS:
                continue
            count = _override_count(spec.get("anzahl", 1), numbers)
            _add(found, srd, count, spec.get("als", srd), bool(spec.get("ersatz", False)))
        return list(found.values())

    text = (rolled_text or "").replace("\u00ad", "").replace("\u00a0", " ")
    taken: List[Tuple[int, int]] = []

    # 1) "3 Fallensteller (Gemeiner)"  -> Gemeiner, angezeigt als Fallensteller
    for m in _PAREN_RE.finditer(text):
        role = m.group("role").strip()
        if re.search(r"\b(mit|und|oder|aus|von|auf)\b", role, re.IGNORECASE):
            continue
        srd, ersatz = _FORMS[_norm(m.group("name"))]
        _add(found, srd, _count_value(m.group("n")), role, ersatz)
        taken.append((m.start(), m.end()))

    # 2) "7 Kobolde", "1 Banditenhauptmann mit 4 Banditen"
    for m in _MATCH_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        srd, ersatz = _FORMS[_norm(m.group("name"))]
        _add(found, srd, _count_value(m.group("n")), m.group("name").strip(), ersatz)

    return list(found.values())


# -----------------------------------------------------------------
# Formatierung (Absatzlogik wie in Kreaturen.txt, als Telegram-HTML)
# -----------------------------------------------------------------

PARSE_MODE = "HTML"

TYPE_EMOJI = [
    ("Schwarm", "🐾"), ("Tier", "🐾"), ("Drache", "🐉"), ("Humanoide", "🧑"), ("Monstrosität", "👾"),
    ("Unhold", "👹"), ("Untoter", "💀"), ("Elementar", "🔥"), ("Riese", "🗿"), ("Konstrukt", "⚙️"),
    ("Pflanze", "🌿"), ("celestisches Wesen", "😇"), ("Feenwesen", "🧚"), ("Aberration", "🐙"),
    ("Schlick", "🦠"),
]

_LABEL_BLOCKS = [
    "Rettungswürfe", "Fertigkeiten", "Schadensanfälligkeiten", "Schadensresistenzen",
    "Schadensimmunitäten", "Zustandsimmunitäten",
]

_ATTACK_WORDS = re.compile(
    r"(Nah- oder Fernkampfwaffenangriff:|Nahkampfwaffenangriff:|Fernkampfwaffenangriff:|"
    r"Nah- oder Fernkampfzauberangriff:|Nahkampfzauberangriff:|Fernkampfzauberangriff:|"
    r"Treffer:|Fehlschlag:|Erfolg:)"
)
_TO_HIT = re.compile(r"([+−-]\d+)( auf Treffer)")
_DAMAGE = re.compile(r"(\b\d+ \(\d+W\d+(?:\s*[+−-]\s*\d+)?\))")
_DC = re.compile(r"\b(SG[- ]\d+)")
_SPELL_LINE = re.compile(
    r"^(Zaubertricks \(beliebig oft\)|\d\. Grad \(\d+ Pl[aä]tze?\)|\d\. Grad|Beliebig oft|"
    r"Je \d+-mal täglich|\d+-mal täglich)(\s*:)"
)


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def strip_html(s: str) -> str:
    """Notfall: Text ohne Formatierung (falls Telegram das HTML ablehnt)."""
    return html.unescape(re.sub(r"</?[a-z][^>]*>", "", s or ""))


def type_emoji(mtype: str) -> str:
    head = (mtype or "").split(",")[0]
    for word, emo in TYPE_EMOJI:
        if word in head:
            return emo
    return "🐲"


_BARE_DICE = re.compile(r"(?<![(\w])(\d+W\d+(?:\s*[+−]\s*\d+)?)(?![\w)])")


def _rich(text: str, dice: bool = False) -> str:
    """Hebt Angriffsart, Bonus, Schaden und SG hervor. Erwartet unformatierten Text.
    dice=True: zusätzlich freie Würfelangaben wie 8W6 (für Zauber und Gegenstände)."""
    t = esc(text)
    t = _ATTACK_WORDS.sub(r"<i>\1</i>", t)
    t = _TO_HIT.sub(r"<b>\1</b>\2", t)
    t = _DAMAGE.sub(r"<b>\1</b>", t)
    t = _DC.sub(r"<b>\1</b>", t)
    if dice:
        t = _BARE_DICE.sub(r"<b>\1</b>", t)
    return t


def _entry_html(e: dict) -> str:
    lines = (e.get("text") or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        m = _SPELL_LINE.match(line)
        if m:
            rest = line[m.end():].strip()
            out.append(f"<i>{esc(m.group(1))}:</i> {esc(rest)}")
        else:
            out.append(_rich(line))
    body = "\n".join(out).rstrip()
    if e.get("name"):
        return f"<b><i>{esc(e['name'])}.</i></b> {body}".rstrip()
    return body


def _labels_note(item: dict) -> List[str]:
    lines = []
    srd = item["srd"]
    ersatz = [l for l in item["labels"] if l["ersatz"]]
    other = [l for l in item["labels"] if not l["ersatz"] and _norm(l["label"]) not in _same_forms(srd)]
    multi = len(item["labels"]) > 1
    if ersatz:
        names = ", ".join(_fmt_label(l, multi) for l in ersatz)
        lines.append(f"⚠️ <i>Ersatz für: {esc(names)} – nicht im SRD</i>")
    if other:
        names = ", ".join(_fmt_label(l, multi) for l in other)
        lines.append(f"ℹ️ <i>Im Encounter: {esc(names)}</i>")
    return lines


def _fmt_label(l: dict, multi: bool) -> str:
    return f"{l['label']} ({l['count']}×)" if multi else l["label"]


def _same_forms(srd: str) -> set:
    return {f for f, (n, ers) in _FORMS.items() if n == srd and not ers}


def format_monster(m: dict, count: Optional[int] = None, notes: Optional[List[str]] = None) -> str:
    paras: List[str] = []

    title = f"{type_emoji(m['type'])} <b>{esc(m['name'].upper())}</b>"
    if count is not None:
        title += f" ({count}×)"
    head = [title, f"<i>{esc(m['type'])}</i>"]
    if notes:
        head.extend(notes)
    paras.append("\n".join(head))

    props = dict(m.get("props") or [])

    block = []
    for lab, emo in (("Rüstungsklasse", "🛡️"), ("Trefferpunkte", "❤️"), ("Bewegungsrate", "👣")):
        if lab in props:
            block.append(f"{emo} <b>{lab}</b> {esc(props[lab])}".rstrip())
    if block:
        paras.append("\n".join(block))

    if m.get("abilities"):
        rows = []
        for k_, v in m["abilities"]:
            score, _, mod = v.partition(" ")
            rows.append(f"<code>{k_} {score:>2} {esc(mod)}</code>")
        paras.append("\n".join(rows))

    for lab in _LABEL_BLOCKS:
        if lab in props and props[lab]:
            paras.append(f"<b>{lab}</b>\n{esc(props[lab])}")

    if "Sinne" in props:
        sinne = props["Sinne"]
        mm = re.search(r",?\s*Passive Wahrnehmung (\d+)\s*$", sinne)
        if mm:
            rest = sinne[: mm.start()].strip().rstrip(",")
            first = f"👁️ <b>Sinne</b> {esc(rest)}".rstrip()
            paras.append(f"{first}\n<b>Passive Wahrnehmung</b> {mm.group(1)}")
        else:
            paras.append(f"👁️ <b>Sinne</b> {esc(sinne)}")

    if "Sprachen" in props:
        paras.append(f"🗣️ <b>Sprachen</b>\n{esc(props['Sprachen'] or '–')}")

    if "Herausforderungsgrad" in props:
        paras.append(f"⭐ <b>Herausforderungsgrad</b> {esc(props['Herausforderungsgrad'])}")

    for e in m.get("traits") or []:
        paras.append(_entry_html(e))

    for key, title_, emo in (("actions", "AKTIONEN", "⚔️"), ("reactions", "REAKTIONEN", "↩️"),
                             ("legendary", "LEGENDÄRE AKTIONEN", "👑")):
        entries = m.get(key) or []
        if not entries:
            continue
        first, *rest = entries
        paras.append(f"{emo} <b>{title_}</b>\n{_entry_html(first)}")
        for e in rest:
            paras.append(_entry_html(e))

    return "\n\n".join(p for p in paras if p)


TABLE_MARK = "\u0001"


def text_paragraphs(text: str) -> List[str]:
    """Absätze aus dem SRD-Text (unformatiert). Tabellenzeilen und Aufzählungen werden je zu einem Block."""
    out: List[str] = []
    block: List[str] = []
    kind = None  # "table" oder "list"
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(TABLE_MARK):
            this, line = "table", line[len(TABLE_MARK):].strip()
        elif line.startswith("• "):
            this = "list"
        else:
            this = None
        if block and this != kind:
            out.append("\n".join(block))
            block = []
        if this:
            block.append(line)
            kind = this
        else:
            out.append(line)
            kind = None
    if block:
        out.append("\n".join(block))
    return out


_NAMED_SUB = re.compile(r"^([A-ZÄÖÜ][^:]{1,60}?):\s")


def html_paragraphs(text: str) -> List[str]:
    """Wie text_paragraphs, aber als HTML: benannte Abschnitte fett-kursiv, Werte hervorgehoben."""
    out = []
    for p in text_paragraphs(text):
        if "\n" in p:  # Tabelle oder Liste
            lines = p.split("\n")
            if lines[0].startswith("• "):
                out.append("\n".join(_rich(l, dice=True) for l in lines))
            else:
                out.append("\n".join([f"<b>{esc(lines[0])}</b>"] + [esc(l) for l in lines[1:]]))
            continue
        if p.startswith("Auf höheren Graden:"):
            out.append("<b><i>Auf höheren Graden:</i></b> " + _rich(p[len("Auf höheren Graden:"):].strip(), dice=True))
            continue
        m = _NAMED_SUB.match(p)
        if m and len(m.group(1).split()) <= 6:
            out.append(f"<b><i>{esc(m.group(1))}:</i></b> " + _rich(p[m.end():], dice=True))
            continue
        out.append(_rich(p, dice=True))
    return out


SPELL_PROP_EMOJI = {"Zeitaufwand": "⏱️", "Reichweite": "🎯", "Komponenten": "🧪", "Wirkungsdauer": "⏳"}


def format_spell(s: dict, with_name: bool = True) -> str:
    head = []
    if with_name:
        head.append(f"📜 <b>{esc(s['name'].upper())}</b>")
    head.append(f"<i>{esc(s['school'])}</i>")
    paras = ["\n".join(head)]
    props = "\n".join(f"{SPELL_PROP_EMOJI.get(k_, '•')} <b>{esc(k_)}:</b> {esc(v)}" for k_, v in s.get("props") or [])
    if props:
        paras.append(props)
    # Jeder Absatz aus dem SRD wird ein eigener Absatz in Telegram
    paras.extend(html_paragraphs(s.get("text") or ""))
    return "\n\n".join(paras)


def split_message(text: str, limit: int = TG_LIMIT) -> List[str]:
    """Teilt lange Texte an Absatzgrenzen, damit Telegram sie annimmt."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    cur = ""
    for para in text.split("\n\n"):
        if len(para) > limit:
            # Notfall: sehr langer Absatz -> ohne Formatierung teilen, damit keine Tags zerbrechen
            para = esc(strip_html(para))
        while len(para) > limit:
            # an Satzgrenze teilen
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 0 else limit
            piece, para = para[:cut].strip(), para[cut:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def pack_messages(blocks: List[str], limit: int = TG_LIMIT, sep: str = "\n\n――――――――――\n\n") -> List[str]:
    """Packt mehrere kurze Blöcke (z. B. Zauber) in möglichst wenige Nachrichten."""
    out: List[str] = []
    cur = ""
    for b in blocks:
        for piece in split_message(b, limit):
            candidate = f"{cur}{sep}{piece}" if cur else piece
            if len(candidate) > limit:
                if cur:
                    out.append(cur)
                cur = piece
            else:
                cur = candidate
    if cur:
        out.append(cur)
    return out


def lookup_spell(name: str) -> Optional[dict]:
    key = _norm(name)
    if key in _SPELL_ALIASES:
        key = _norm(_SPELL_ALIASES[key])
    return SPELLS.get(key)


def build_creature_messages(raw_text: str, rolled_text: str) -> List[str]:
    """Nachrichten für alle Kreaturen eines Encounters, danach die Zauber."""
    items = find_creatures(raw_text, rolled_text)
    if not items:
        return []

    messages: List[str] = []
    spell_order: List[Tuple[str, str]] = []   # (Zaubername, Kreatur)
    seen_spells = set()

    for item in items:
        m = MONSTERS[item["srd"]]
        text = format_monster(m, item["count"], _labels_note(item))
        messages.extend(split_message(text))
        for sp in m.get("spells") or []:
            key = _norm(_SPELL_ALIASES.get(_norm(sp), sp))
            if key in seen_spells:
                continue
            seen_spells.add(key)
            spell_order.append((sp, m["name"]))

    if spell_order:
        by_creature: Dict[str, List[str]] = {}
        for sp, owner in spell_order:
            by_creature.setdefault(owner, []).append(sp)
        for owner, names in by_creature.items():
            blocks = []
            missing = []
            for sp in names:
                s = lookup_spell(sp)
                if s:
                    blocks.append(format_spell(s))
                else:
                    missing.append(sp)
            if not blocks and not missing:
                continue
            header = f"📚 <b>Zauber von {esc(owner)}</b>"
            if missing:
                header += "\n<i>Nicht im SRD gefunden: " + esc(", ".join(missing)) + "</i>"
            packed = pack_messages(blocks) if blocks else [""]
            packed[0] = f"{header}\n\n{packed[0]}".strip()
            messages.extend(packed)

    return messages
