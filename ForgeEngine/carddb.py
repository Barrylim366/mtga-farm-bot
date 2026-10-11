"""Arena's own card database: grpId -> English name and printed characteristics.

Read straight from the SQLite file the client ships (``Raw_CardDatabase_*.mtga``)
and cached as JSON beside the engine, so a lookup during a match never touches
SQLite or the network. Forge looks cards up by English name, and Arena's
counter types are numbers, so both come from here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from ForgeEngine import paths

_TAG = re.compile(r"<[^>]+>")
_MONO_PATH = re.compile(r"Mono path\[0\] = '(.+?)/MTGA_Data/Managed'")
_COLOR_LETTER = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}

_DEFAULT_DATA_DIRS = (
    r"C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw",
    r"C:\Program Files (x86)\Steam\steamapps\common\MTGA\MTGA_Data\Downloads\Raw",
    r"C:\Program Files\Steam\steamapps\common\MTGA\MTGA_Data\Downloads\Raw",
    "~/Library/Application Support/com.wizards.mtga/Downloads/Raw",
    "~/Library/Application Support/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw",
    "~/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw",
    "~/.steam/steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw",
)


def default_log_path() -> str:
    from Controller.MTGAController.LogReader import LogReader

    return LogReader._default_player_log_path()


def find_data_dir(hint: str | None = None, log_path: str | None = None) -> Path | None:
    """Where the client keeps Raw_CardDatabase.

    ``$MTGA_DATA_DIR``, then the folder the user gave the bot, then the install
    path the client logs at start, then the usual install places.
    """
    for named in (os.environ.get("MTGA_DATA_DIR", ""), hint or ""):
        if named and Path(named).is_dir():
            return Path(named)
    log = Path(log_path or default_log_path())
    for candidate in (log, log.with_name("Player-prev.log")):
        try:
            with candidate.open("rb") as handle:
                for _ in range(200):
                    raw = handle.readline()
                    if not raw:
                        break
                    found = _MONO_PATH.search(raw.decode("utf-8", errors="replace"))
                    if found:
                        folder = Path(found.group(1)) / "MTGA_Data" / "Downloads" / "Raw"
                        if folder.is_dir():
                            return folder
        except OSError:
            continue
    for guess in _DEFAULT_DATA_DIRS:
        folder = Path(os.path.expanduser(guess))
        if folder.is_dir():
            return folder
    return None


def _ints(text) -> list[int]:
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


def _stat(text) -> int | None:
    text = str(text or "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def export(db_file: Path) -> dict:
    """The parts of the client database the engine needs."""
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        def enumeration(kind: str) -> dict[int, str]:
            rows = conn.execute(
                "SELECT e.Value, l.Loc FROM Enums e JOIN Localizations_enUS l ON l.LocId = e.LocId "
                "WHERE e.Type = ? ORDER BY l.Formatted DESC",
                (kind,),
            )
            names: dict[int, str] = {}
            # Ordered so the least-formatted variant is written last and wins.
            for value, text in rows:
                if isinstance(value, int) or str(value).lstrip("-").isdigit():
                    names[int(value)] = _TAG.sub("", str(text or ""))
            return names

        card_types = enumeration("CardType")
        sub_types = enumeration("SubType")
        super_types = enumeration("SuperType")
        colors = enumeration("Color")
        counters = enumeration("CounterType")

        titles: dict[int, str] = {}
        for loc_id, text in conn.execute(
            "SELECT l.LocId, l.Loc FROM Localizations_enUS l "
            "WHERE l.LocId IN (SELECT TitleId FROM Cards) ORDER BY l.Formatted DESC"
        ):
            titles[int(loc_id)] = _TAG.sub("", str(text or ""))

        cards: dict[str, dict] = {}
        for row in conn.execute(
            "SELECT GrpId, TitleId, IsToken, Power, Toughness, Colors, Types, Subtypes, Supertypes FROM Cards"
        ):
            grp_id, title_id, is_token, power, toughness, color_ids, types, subtypes, supertypes = row
            name = titles.get(title_id) if isinstance(title_id, int) else None
            if not name:
                continue
            entry: dict = {"name": name}
            if is_token:
                entry["token"] = True
            if _stat(power) is not None:
                entry["power"] = _stat(power)
            if _stat(toughness) is not None:
                entry["toughness"] = _stat(toughness)
            letters = "".join(_COLOR_LETTER.get(colors.get(c, ""), "") for c in _ints(color_ids))
            if letters:
                entry["colors"] = letters
            for key, column, table in (
                ("types", types, card_types),
                ("subtypes", subtypes, sub_types),
                ("supertypes", supertypes, super_types),
            ):
                names = [table[i] for i in _ints(column) if i in table]
                if names:
                    entry[key] = names
            cards[str(grp_id)] = entry
    finally:
        conn.close()
    return {"cards": cards, "counters": {str(k): v for k, v in counters.items()}}


class CardDb:
    """The cards of the Arena client, by grpId."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.cards: dict = data.get("cards") or {}
        self.counters: dict = data.get("counters") or {}

    def __len__(self) -> int:
        return len(self.cards)

    def get(self, grp_id) -> dict | None:
        if grp_id is None:
            return None
        return self.cards.get(str(grp_id))

    def name(self, grp_id) -> str | None:
        info = self.get(grp_id)
        return info.get("name") if info else None

    def counter_name(self, counter_type) -> str | None:
        return self.counters.get(str(counter_type))

    @classmethod
    def load(cls, data_dir: Path | None, cache_path: Path | None = None) -> "CardDb":
        """The cached export if it still matches the client's file, else a fresh one.

        With no client installation to read, the last cache is used as it is:
        an older card list is far more useful than none.
        """
        cache_path = cache_path or paths.card_cache()
        source = None
        if data_dir is not None and Path(data_dir).is_dir():
            candidates = [p for p in Path(data_dir).glob("Raw_CardDatabase_*.mtga") if p.is_file()]
            if candidates:
                source = max(candidates, key=lambda p: p.stat().st_mtime)
        cached = None
        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                cached = json.load(handle)
        except (OSError, ValueError):
            cached = None
        if source is None:
            if cached:
                return cls(cached)
            raise RuntimeError(
                "Arena's card database was not found. Set MTGA_DATA_DIR to <MTGA>/MTGA_Data/Downloads/Raw."
            )
        stamp = {"file": source.name, "mtime": int(source.stat().st_mtime)}
        if cached and cached.get("source") == stamp:
            return cls(cached)
        data = export(source)
        data["source"] = stamp
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
        except OSError:
            pass
        return cls(data)
