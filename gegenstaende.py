"""
Magische Gegenstände und Zauberschriftrollen aus dem SRD 5.1 (DE) für den Dice Bot.

- srd_gegenstaende_de.json  Beschreibungen aller magischen Gegenstände aus dem SRD
- Zauber kommen aus kreaturen.SPELLS (srd_zauber_de.json)

Die Tabellen A bis I im Bot stammen aus dem Spielleiterhandbuch und benutzen dort
teils andere Namen ("Hewards praktischer Rucksack", "Zauberstecken des Feuers").
Die Zuordnung zu den SRD-Namen steht unten in ALIASES und RULES.
"""

import json
import random
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import kreaturen as K

_BASE = Path(__file__).resolve().parent

ITEMS: Dict[str, dict] = {}          # Name (klein) -> Gegenstand
SPELLS_BY_LEVEL: Dict[int, List[dict]] = {}

# Tabellenname -> SRD-Name (ohne Klammerzusatz verglichen, Groß/Klein egal)
ALIASES = {
    "drachenschuppen panzer": "Drachenschuppenpanzer",
    "pfeil fangender schild": "Pfeilfangender Schild",
    "irit flasche": "Ifriti-Flasche",
    "daerns flotte festung": "Flotte Festung",
    "der apparat von kwalish": "Apparat der Krabbe",
    "eisenbänder von bilarro": "Eisenbänder des Bindens",
    "hewards praktischer rucksack": "Praktischer Rucksack",
    "horn von valhalla": "Horn von Walhalla",
    "keoghtoms salbe": "Restaurative Salbe",
    "mithrilrüstung": "Mithralrüstung",
    "nolzurs wunderfarben": "Wunderfarben",
    "quaals feder": "Federfigur",
    "robe der erzmagier": "Robe des Erzmagiers",
    "ritterrüstung des widerstands": "Rüstung des Widerstands",
    "stein des glücks": "Stein des Glücks (Glücksstein)",
    "umhang des mantarochen": "Umhang des Mantarochens",
    "ring der djinni beschwörung": "Ring der Dschinni-Beschwörung",
    "zauberstecken der magi": "Stab des Magus",
    "zauberstecken des frostes": "Stab des Frosts",
    "zauberstecken des donners und blitzes": "Stab des Blitzes und Donners",
    "magische rüstung": "Rüstung, +1, +2 oder +3",
    "ehlonnas köcher": "Effizienter Köcher",
}

# (Muster, SRD-Name)
RULES = [
    (r"^waffe, \+[123]$", "Waffe, +1, +2 oder +3"),
    (r"^schild, \+[123]$", "Schild, +1, +2 oder +3"),
    (r"^geschosse, \+[123]$", "Geschosse, +1, +2 oder +3"),
    (r"^rüstung,? \+[123]\b.*$", "Rüstung, +1, +2 oder +3"),
    (r"^zauberstab des kriegsmagiers, \+[123]$", "Zauberstab des Kriegsmagiers, +1, +2 oder +3"),
    (r"^trank der \w*riesenstärke$", "Trank der Riesenstärke"),
    (r"^gürtel der .*riesenstärke$", "Gürtel der Riesenstärke"),
    (r"^trank der (mächtigen|überlegenen|höchsten) heilung$", "Heiltrank"),
    (r"^ring der (erd|feuer|luft|wasser)elementar[ -]herrschaft$", "Ring der Elementar-Herrschaft"),
]

SCROLL_RE = re.compile(r"^Zauberschriftrolle \((Zaubertrick|Zaubergrad (\d))\)", re.IGNORECASE)

# Rettungswurf-SG und Angriffsbonus nach Grad (Tabelle "Zauberschriftrolle" im SRD)
SCROLL_DC = {0: (13, 5), 1: (13, 5), 2: (13, 5), 3: (15, 7), 4: (15, 7),
             5: (17, 9), 6: (17, 9), 7: (18, 10), 8: (18, 10), 9: (19, 11)}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("­", "")).strip().lower()


def spell_level(spell: dict) -> Optional[int]:
    m = re.search(r"(\d)\. Grades", spell.get("school", ""))
    if m:
        return int(m.group(1))
    if "zaubertrick" in spell.get("school", "").lower():
        return 0
    return None


def init_gegenstaende() -> None:
    ITEMS.clear()
    SPELLS_BY_LEVEL.clear()
    path = _BASE / "srd_gegenstaende_de.json"
    if path.exists():
        for it in json.loads(path.read_text(encoding="utf-8")):
            ITEMS[_norm(it["name"])] = it
    for s in K.SPELLS.values():
        lv = spell_level(s)
        if lv is not None:
            SPELLS_BY_LEVEL.setdefault(lv, []).append(s)
    for lst in SPELLS_BY_LEVEL.values():
        lst.sort(key=lambda s: s["name"])


def resolve_item(table_name: str) -> Optional[dict]:
    """Findet den SRD-Eintrag zu einem Namen aus den Tabellen A bis I."""
    name = _norm(table_name)
    base = re.sub(r"\s*\(.*\)\s*$", "", name).strip()
    for cand in (name, base):
        if cand in ITEMS:
            return ITEMS[cand]
        if cand in ALIASES:
            return ITEMS.get(_norm(ALIASES[cand]))
        hy = cand.replace(" herrschaft", "-herrschaft")
        if hy in ITEMS:
            return ITEMS[hy]
        if cand.startswith("zauberstecken "):
            st = "stab " + cand[len("zauberstecken "):]
            if st in ITEMS:
                return ITEMS[st]
        for pat, target in RULES:
            if re.match(pat, cand):
                return ITEMS.get(_norm(target))
    # "Magische Rüstung (Rüstung +2 Plattenpanzer)"
    inner = re.search(r"\((.*)\)", name)
    if inner and base == "magische rüstung":
        return ITEMS.get(_norm("Rüstung, +1, +2 oder +3"))
    return None


def roll_scroll(table_name: str) -> Optional[Tuple[str, dict, int]]:
    """'Zauberschriftrolle (Zaubergrad 3)' -> (neuer Name, Zauber, Grad)."""
    m = SCROLL_RE.match(table_name or "")
    if not m:
        return None
    level = 0 if m.group(1).lower() == "zaubertrick" else int(m.group(2))
    pool = SPELLS_BY_LEVEL.get(level) or []
    if not pool:
        return None
    spell = random.choice(pool)
    return f"{table_name}: {spell['name']}", spell, level


# -----------------------------------------------------------------
# Formatierung
# -----------------------------------------------------------------

_FIGUR_KEYS = {
    "bronze": "Bronze-Greif", "ebenholz": "Ebenholz-Fliege", "golden": "Goldene Löwen",
    "elfenbein": "Elfenbein-Ziegen", "marmor": "Marmor-Elefant", "obsidian": "Obsidianpferd",
    "onyx": "Onyx-Hund", "serpentin": "Serpentin-Eule", "silber": "Silber-Rabe",
}


def _item_paragraphs(item: dict, table_name: str) -> List[str]:
    paras = K.text_paragraphs(item.get("text") or "")
    if item["name"] == "Figur der wundersamen Kraft":
        # nur die erwürfelte Figur zeigen (plus allgemeine Regeln am Anfang)
        low = table_name.lower()
        key = next((v for k, v in _FIGUR_KEYS.items() if k in low), None)
        named = re.compile(r"^[A-ZÄÖÜ][\wäöüß-]+(?: [\wäöüß-]+)? \((?:selten|sehr selten|ungewöhnlich)\):")
        first_named = next((i for i, p in enumerate(paras) if named.match(p)), None)
        if key and first_named is not None:
            picked, on = [], False
            for p in paras[first_named:]:
                if named.match(p):
                    on = p.startswith(key)
                if on:
                    picked.append(p)
            if picked:
                return paras[:first_named] + picked
    return paras


def format_item(item: dict, count: int, labels: List[str]) -> str:
    title = item["name"] if count <= 1 else f"{item['name']} ({count}×)"
    paras = [title]
    differs = [l for l in labels if _norm(re.sub(r"\s*\(.*\)$", "", l)) != _norm(item["name"])]
    if differs:
        counts: Dict[str, int] = {}
        for l in labels:
            counts[l] = counts.get(l, 0) + 1
        if len(counts) == 1 and count <= 1:
            paras.append("Im Schatz: " + labels[0])
        else:
            paras.append("Im Schatz: " + ", ".join(f"{l} ({c}×)" for l, c in counts.items()))
    if item.get("type"):
        paras.append(item["type"])
    paras.extend(_item_paragraphs(item, labels[0] if labels else ""))
    return "\n\n".join(paras)


def format_scroll(spell: dict, level: int) -> str:
    dc, atk = SCROLL_DC.get(level, (13, 5))
    grad = "Zaubertrick" if level == 0 else f"Zaubergrad {level}"
    head = f"📜 Zauberschriftrolle: {spell['name']}\n\n{grad} · Rettungswurf-SG {dc} · Angriffsbonus +{atk}"
    return head + "\n\n" + K.format_spell(spell, with_name=False)


def build_treasure_messages(picked: List[dict]) -> List[str]:
    """picked: [{"name": Tabellenname, "spell": dict|None, "level": int|None}, ...]"""
    if not picked:
        return []

    groups: Dict[str, dict] = {}
    scrolls: List[Tuple[dict, int]] = []
    missing: List[str] = []

    for p in picked:
        if p.get("spell"):
            scrolls.append((p["spell"], p["level"]))
            continue
        item = resolve_item(p["name"])
        if not item:
            missing.append(p["name"])
            continue
        g = groups.setdefault(item["name"], {"item": item, "count": 0, "labels": []})
        g["count"] += 1
        g["labels"].append(p["name"])

    messages: List[str] = []
    for g in groups.values():
        messages.extend(K.split_message(format_item(g["item"], g["count"], g["labels"])))

    if scrolls:
        rules = ITEMS.get(_norm("Zauberschriftrolle"))
        blocks = [format_scroll(s, lv) for s, lv in scrolls]
        if rules:
            # Regeltext ohne die Tabelle, SG und Angriffsbonus stehen schon bei jeder Rolle
            rule_paras = [p for p in K.text_paragraphs(rules["text"]) if "\n" not in p]
            blocks.append("So funktionieren Zauberschriftrollen\n\n" + "\n\n".join(rule_paras))
        messages.extend(K.pack_messages(blocks))

    if missing:
        uniq = list(dict.fromkeys(missing))
        messages.append(
            "Nicht im SRD enthalten, bitte im Spielleiterhandbuch nachschlagen:\n"
            + "\n".join(f"• {m}" for m in uniq)
        )

    return messages
