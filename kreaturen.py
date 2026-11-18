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
# Formatierung (Absatzlogik wie in Kreaturen.txt)
# -----------------------------------------------------------------

_LABEL_BLOCKS = [
    "Rettungswürfe", "Fertigkeiten", "Schadensanfälligkeiten", "Schadensresistenzen",
    "Schadensimmunitäten", "Zustandsimmunitäten",
]


def _entry_text(e: dict) -> str:
    if e.get("name"):
        return f"{e['name']}: {e['text']}".rstrip()
    return e["text"].rstrip()


def _labels_note(item: dict) -> List[str]:
    lines = []
    srd = item["srd"]
    ersatz = [l for l in item["labels"] if l["ersatz"]]
    other = [l for l in item["labels"] if not l["ersatz"] and _norm(l["label"]) not in _same_forms(srd)]
    multi = len(item["labels"]) > 1
    if ersatz:
        names = ", ".join(_fmt_label(l, multi) for l in ersatz)
        lines.append(f"Ersatz für: {names} – nicht im SRD")
    if other:
        names = ", ".join(_fmt_label(l, multi) for l in other)
        lines.append(f"Im Encounter: {names}")
    return lines


def _fmt_label(l: dict, multi: bool) -> str:
    return f"{l['label']} ({l['count']}×)" if multi else l["label"]


def _same_forms(srd: str) -> set:
    return {f for f, (n, ers) in _FORMS.items() if n == srd and not ers}


def format_monster(m: dict, count: Optional[int] = None, notes: Optional[List[str]] = None) -> str:
    paras: List[str] = []

    title = m["name"] if count is None else f"{m['name']} ({count}×)"
    paras.append(title)
    if notes:
        paras.append("\n".join(notes))

    paras.append(m["type"])

    props = dict(m.get("props") or [])

    block = []
    for lab in ("Rüstungsklasse", "Trefferpunkte", "Bewegungsrate"):
        if lab in props:
            block.append(f"{lab} {props[lab]}".strip())
    if block:
        paras.append("\n".join(block))

    if m.get("abilities"):
        paras.append("\n".join(f"{k} {v}" for k, v in m["abilities"]))

    for lab in _LABEL_BLOCKS:
        if lab in props and props[lab]:
            paras.append(f"{lab}\n{props[lab]}")

    if "Sinne" in props:
        sinne = props["Sinne"]
        mm = re.search(r",?\s*(Passive Wahrnehmung \d+)\s*$", sinne)
        if mm:
            rest = sinne[: mm.start()].strip().rstrip(",")
            first = f"Sinne {rest}" if rest else "Sinne"
            paras.append(f"{first}\n{mm.group(1)}")
        else:
            paras.append(f"Sinne {sinne}")

    if "Sprachen" in props:
        paras.append(f"Sprachen\n{props['Sprachen'] or '–'}")

    if "Herausforderungsgrad" in props:
        paras.append(f"Herausforderungsgrad {props['Herausforderungsgrad']}")

    for e in m.get("traits") or []:
        paras.append(_entry_text(e))

    for key, title_ in (("actions", "Aktionen"), ("reactions", "Reaktionen"), ("legendary", "Legendäre Aktionen")):
        entries = m.get(key) or []
        if not entries:
            continue
        first, *rest = entries
        paras.append(f"{title_}\n{_entry_text(first)}")
        for e in rest:
            paras.append(_entry_text(e))

    return "\n\n".join(p for p in paras if p)


def format_spell(s: dict) -> str:
    paras = [s["name"], s["school"]]
    props = "\n".join(f"{k}: {v}" for k, v in s.get("props") or [])
    if props:
        paras.append(props)
    text = (s.get("text") or "").strip()
    if text:
        # Jeder Absatz aus dem SRD wird ein eigener Absatz in Telegram
        paras.extend(p.strip() for p in text.split("\n") if p.strip())
    return "\n\n".join(paras)


def split_message(text: str, limit: int = TG_LIMIT) -> List[str]:
    """Teilt lange Texte an Absatzgrenzen, damit Telegram sie annimmt."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    cur = ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            # Notfall: sehr langer Absatz, an Satzgrenze teilen
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
            header = f"📜 Zauber: {owner}"
            if missing:
                header += "\nNicht im SRD gefunden: " + ", ".join(missing)
            packed = pack_messages(blocks) if blocks else [""]
            packed[0] = f"{header}\n\n{packed[0]}".strip()
            messages.extend(packed)

    return messages
