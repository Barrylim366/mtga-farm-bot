"""Arena's own card database: grpId -> English name and printed characteristics.

Read straight from the SQLite file the game client ships (Raw_CardDatabase_*.mtga) and cached as
JSON, so a lookup during a match never touches SQLite or the network.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "forge_bridge" / "cache" / "arena_cards.json"

_TAG = re.compile(r"<[^>]+>")
_COLOR_LETTER = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}


def default_log_path() -> Path:
    return Path(os.path.expanduser("~")) / "AppData" / "LocalLow" / "Wizards Of The Coast" / "MTGA" / "Player.log"


def find_data_dir(log_path: Optional[Path] = None) -> Optional[Path]:
    """Where the client keeps Raw_CardDatabase: $MTGA_DATA_DIR, else the install path the client logs at start."""
    env = os.environ.get("MTGA_DATA_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    log_path = log_path or default_log_path()
    candidates = [log_path, log_path.with_name("Player-prev.log")]
    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                for _ in range(200):
                    line = handle.readline()
                    if not line:
                        break
                    match = re.search(r"Mono path\[0\] = '(.+?)/MTGA_Data/Managed'", line)
                    if match:
                        raw = Path(match.group(1)) / "MTGA_Data" / "Downloads" / "Raw"
                        if raw.is_dir():
                            return raw
        except OSError:
            continue
    for guess in (
        r"C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw",
        r"C:\Program Files (x86)\Steam\steamapps\common\MTGA\MTGA_Data\Downloads\Raw",
    ):
        if Path(guess).is_dir():
            return Path(guess)
    return None


def _ints(text: Optional[str]) -> list[int]:
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


def _stat(text: Optional[str]) -> Optional[int]:
    text = (text or "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _export(db_file: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        def enum(kind: str) -> dict[int, str]:
            rows = conn.execute(
                "SELECT e.Value, l.Loc FROM Enums e JOIN Localizations_enUS l ON l.LocId = e.LocId "
                "WHERE e.Type = ? ORDER BY l.Formatted DESC",
                (kind,),
            ).fetchall()
            # ordered so the least-formatted variant is written last and wins
            return {int(value): _TAG.sub("", text or "") for value, text in rows}

        card_types = enum("CardType")
        sub_types = enum("SubType")
        super_types = enum("SuperType")
        colors = enum("Color")
        counters = enum("CounterType")

        titles: dict[int, str] = {}
        for loc_id, text in conn.execute(
            "SELECT l.LocId, l.Loc FROM Localizations_enUS l "
            "WHERE l.LocId IN (SELECT TitleId FROM Cards) ORDER BY l.Formatted DESC"
        ):
            titles[int(loc_id)] = _TAG.sub("", text or "")

        cards: dict[str, dict] = {}
        for grp, title_id, is_token, power, toughness, color_ids, type_ids, sub_ids, super_ids in conn.execute(
            "SELECT GrpId, TitleId, IsToken, Power, Toughness, Colors, Types, Subtypes, Supertypes FROM Cards"
        ):
            name = titles.get(int(title_id))
            if not name:
                continue
            entry: dict = {"name": name}
            if is_token:
                entry["token"] = True
            p, t = _stat(power), _stat(toughness)
            if p is not None:
                entry["power"] = p
            if t is not None:
                entry["toughness"] = t
            letters = "".join(_COLOR_LETTER.get(colors.get(c, ""), "") for c in _ints(color_ids))
            if letters:
                entry["colors"] = letters
            for key, ids, table in (
                ("types", type_ids, card_types),
                ("subtypes", sub_ids, sub_types),
                ("supertypes", super_ids, super_types),
            ):
                names = [table[i] for i in _ints(ids) if i in table]
                if names:
                    entry[key] = names
            cards[str(grp)] = entry
    finally:
        conn.close()
    return {"cards": cards, "counters": {str(k): v for k, v in counters.items()}}


class CardDb:
    def __init__(self, data: dict):
        self._cards: dict[str, dict] = data.get("cards", {})
        self._counters: dict[str, str] = data.get("counters", {})

    def __len__(self) -> int:
        return len(self._cards)

    def get(self, grp_id: Optional[int]) -> Optional[dict]:
        return None if grp_id is None else self._cards.get(str(grp_id))

    def name(self, grp_id: Optional[int]) -> Optional[str]:
        card = self.get(grp_id)
        return card["name"] if card else None

    def counter_name(self, counter_type: int) -> Optional[str]:
        return self._counters.get(str(counter_type))

    @classmethod
    def load(cls, data_dir: Optional[Path] = None, cache_path: Path = CACHE_PATH) -> "CardDb":
        """The cached export if it still matches the client's database file, otherwise a fresh one.

        With no client installation to read, the last cache is used as it is: an older card list is far
        more useful than none.
        """
        data_dir = data_dir or find_data_dir()
        source = None
        if data_dir is not None:
            files = glob.glob(str(Path(data_dir) / "Raw_CardDatabase_*.mtga"))
            if files:
                source = Path(max(files, key=os.path.getmtime))
        cached = None
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        if source is None:
            if cached is None:
                raise FileNotFoundError(
                    "Arena's card database was not found. Set MTGA_DATA_DIR to <MTGA>/MTGA_Data/Downloads/Raw."
                )
            return cls(cached)
        stamp = {"file": source.name, "mtime": int(source.stat().st_mtime)}
        if cached is not None and cached.get("source") == stamp:
            return cls(cached)
        data = _export(source)
        data["source"] = stamp
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return cls(data)
