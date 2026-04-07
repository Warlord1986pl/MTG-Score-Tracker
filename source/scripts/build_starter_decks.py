from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FORMATS = [
    "Standard",
    "Modern",
    "Pioneer",
    "Vintage",
    "Legacy",
    "Pauper",
    "Premodern",
]


def _infer_bucket(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["burn", "prowess", "zoo", "humans", "infect", "energy", "aggro"]):
        return "Aggro"
    if any(k in n for k in ["control", "lantern", "prison"]):
        return "Control"
    if any(k in n for k in ["combo", "storm", "belcher", "ritual", "creativity", "titan", "yawgmoth"]):
        return "Combo"
    if any(k in n for k in ["reanimator", "living end", "goryo", "graveyard", "dredge"]):
        return "Graveyard"
    if any(k in n for k in ["tron", "ramp", "eldrazi"]):
        return "Ramp"
    if any(k in n for k in ["blink"]):
        return "Blink"
    if any(k in n for k in ["midrange", "omnath"]):
        return "Midrange"
    if any(k in n for k in ["tempo"]):
        return "Tempo"
    return "Other"


def _fetch_metagame(fmt: str, min_date: date, max_date: date) -> list[dict]:
    q = urlencode(
        {
            "format": fmt,
            "min_date": min_date.isoformat(),
            "max_date": max_date.isoformat(),
            "limit": 32,
        }
    )
    url = f"https://api.videreproject.com/metagame?{q}"
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urlopen(req, timeout=30) as res:
        payload = json.loads(res.read().decode("utf-8"))
    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    max_date = date.today()
    min_date = max_date - timedelta(days=14)

    by_format: dict[str, list[str]] = {}
    merged: dict[str, str] = {}

    existing_path = config_dir / "decks.json"
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                for k, v in existing.items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        merged[k.strip()] = v.strip()
        except Exception:
            pass

    for fmt in FORMATS:
        rows = _fetch_metagame(fmt, min_date, max_date)
        names = [str(r.get("archetype", "")).strip() for r in rows]
        names = sorted({n for n in names if n})
        by_format[fmt] = names
        for name in names:
            merged.setdefault(name, _infer_bucket(name))

    (config_dir / "starter_decks_by_format.json").write_text(
        json.dumps(by_format, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    (config_dir / "starter_decks_meta.json").write_text(
        json.dumps(
            {
                "source": "https://api.videreproject.com/metagame",
                "window_days": 14,
                "min_date": min_date.isoformat(),
                "max_date": max_date.isoformat(),
                "formats": FORMATS,
                "counts": {k: len(v) for k, v in by_format.items()},
                "unique_decks": len(merged),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    existing_path.write_text(json.dumps(dict(sorted(merged.items())), indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"Starter pack updated. Unique decks: {len(merged)}")
    for fmt in FORMATS:
        print(f"  {fmt}: {len(by_format.get(fmt, []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
