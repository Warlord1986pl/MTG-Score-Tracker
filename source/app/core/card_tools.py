from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+?)\s*$")
WORD_RE = re.compile(r"[A-Za-z0-9']+")
LAND_HINTS = {
    "plains",
    "island",
    "swamp",
    "mountain",
    "forest",
    "mesa",
    "strand",
    "foothills",
    "vents",
    "shrine",
    "garden",
    "triome",
    "headquarters",
    "falls",
    "crypt",
    "arena",
    "flat",
    "marsh",
}
CARD_SHORTCUT_OVERRIDES: dict[str, list[str]] = {
    "Orcish Bowmasters": ["OBM"],
    "Leyline of the Guildpact": ["LOTG"],
    "Consign to Memory": ["CTM"],
    "Teferi, Time Raveler": ["T3F"],
    "Stubborn Denial": ["SD"],
    "Strix Serenade": ["STS"],
    "Territorial Kavu": ["TK"],
    "Quantum Riddler": ["QR"],
    "Phlage, Titan of Fire's Fury": ["PH"],
    "Ragavan, Nimble Pilferer": ["RAG"],
    "Leyline Binding": ["LB"],
    "Scion of Draco": ["SOD", "SCION"],
    "Wear / Tear": ["WT", "Wear/Tear"],
    "Wrath of the Skies": ["WOTS"],
    "Mystical Dispute": ["MD"],
    "Damping Sphere": ["DS"],
    "Obsidian Charmaw": ["OC"],
}


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def extract_card_entries_from_decklist(decklist_text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    in_sideboard = False

    for raw_line in str(decklist_text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("- Source:") or line.startswith("_No decklist"):
            continue

        lowered = line.lower().rstrip(":")
        if lowered in {"mainboard", "decklist", "deck list"}:
            in_sideboard = False
            continue
        if lowered in {"sideboard", "// sideboard", "sb"}:
            in_sideboard = True
            continue

        normalized_line = line.lstrip("- ").strip()
        match = CARD_LINE_RE.match(normalized_line)
        if not match:
            continue

        quantity = int(match.group(1))
        card_name = match.group(2).strip()
        entries.append({"name": card_name, "quantity": quantity, "section": "side" if in_sideboard else "main"})

    return entries


def extract_card_names_from_decklist(decklist_text: str) -> list[str]:
    return _unique_preserve_order([str(entry.get("name", "")) for entry in extract_card_entries_from_decklist(decklist_text)])


def _build_generic_aliases(card_name: str) -> list[str]:
    tokens = [token for token in WORD_RE.findall(card_name) if token]
    aliases: list[str] = []
    if not tokens:
        return aliases

    initials = "".join(token[0].upper() for token in tokens)
    if 2 <= len(initials) <= 5:
        aliases.append(initials)

    if len(tokens) == 2:
        alias = (tokens[0][0] + tokens[1][:2]).upper()
        if 2 <= len(alias) <= 5:
            aliases.append(alias)

    return _unique_preserve_order(aliases)


def build_card_shortcuts(card_names: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_name in card_names:
        card_name = str(raw_name).strip()
        if not card_name:
            continue

        aliases = list(CARD_SHORTCUT_OVERRIDES.get(card_name, []))
        lowered = card_name.lower()
        if not aliases and not any(hint in lowered for hint in LAND_HINTS):
            aliases.extend(_build_generic_aliases(card_name))
        for alias in aliases:
            cleaned_alias = str(alias).strip().upper()
            if len(cleaned_alias) < 2:
                continue
            mapping.setdefault(cleaned_alias, card_name)
    return dict(sorted(mapping.items(), key=lambda item: (len(item[0]), item[0], item[1])))


def build_decklist_autocomplete_terms(decklist_text: str) -> list[str]:
    entries = extract_card_entries_from_decklist(decklist_text)
    shortcuts = build_card_shortcuts([str(entry.get("name", "")) for entry in entries])
    terms: list[str] = []

    for entry in entries:
        card_name = str(entry.get("name", "")).strip()
        quantity = int(entry.get("quantity", 0) or 0)
        section = str(entry.get("section", "main"))
        if not card_name:
            continue

        terms.append(card_name)
        first_word = WORD_RE.findall(card_name)
        if first_word:
            terms.append(first_word[0])
        matching_aliases = [alias for alias, target in shortcuts.items() if target == card_name]
        terms.extend(matching_aliases)
        terms.append(f"{card_name} ({', '.join(matching_aliases)})" if matching_aliases else card_name)

        max_suggested = max(1, min(quantity, 4))
        if section == "side":
            for amount in range(1, max_suggested + 1):
                terms.append(f"+{amount} {card_name}")
            terms.append(f"bring in {card_name}")
        else:
            for amount in range(1, max_suggested + 1):
                terms.append(f"-{amount} {card_name}")
            terms.append(f"cut {card_name}")

    return _unique_preserve_order(terms)


def choose_reference_card_names(card_names: list[str], max_cards: int = 8) -> list[str]:
    nonlands: list[str] = []
    lands: list[str] = []
    for card_name in _unique_preserve_order(card_names):
        lowered = card_name.lower()
        if any(hint in lowered for hint in LAND_HINTS):
            lands.append(card_name)
        else:
            nonlands.append(card_name)
    return (nonlands + lands)[:max_cards]


def _read_scryfall_cache(cache_path: Path) -> dict[str, dict[str, str]]:
    if not cache_path.exists():
        return {}
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_scryfall_cache(cache_path: Path, payload: dict[str, dict[str, str]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch_scryfall_card_reference(
    card_names: list[str],
    cache_dir: Path | None = None,
    *,
    max_cards: int = 8,
    timeout: float = 6.0,
) -> list[dict[str, str]]:
    selected = choose_reference_card_names(card_names, max_cards=max_cards)
    if not selected:
        return []

    cache_path = (cache_dir or Path.cwd() / ".cache") / "scryfall_cards.json"
    cache = _read_scryfall_cache(cache_path)
    results: list[dict[str, str]] = []
    cache_changed = False

    for card_name in selected:
        cache_key = card_name.lower()
        if cache_key in cache:
            results.append(cache[cache_key])
            continue

        url = f"https://api.scryfall.com/cards/named?exact={urllib.parse.quote(card_name)}"
        request = urllib.request.Request(url, headers={"User-Agent": "MTGScoreTracker/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            continue

        if not isinstance(payload, dict) or payload.get("object") == "error":
            continue

        oracle_text = str(payload.get("oracle_text", "")).replace("\n", " ").strip()
        card_summary = {
            "name": str(payload.get("name", card_name)).strip(),
            "mana_cost": str(payload.get("mana_cost", "")).strip(),
            "type_line": str(payload.get("type_line", "")).strip(),
            "oracle_text": oracle_text,
            "scryfall_uri": str(payload.get("scryfall_uri", "")).strip(),
        }
        cache[cache_key] = card_summary
        results.append(card_summary)
        cache_changed = True

    if cache_changed:
        _write_scryfall_cache(cache_path, cache)

    return results
