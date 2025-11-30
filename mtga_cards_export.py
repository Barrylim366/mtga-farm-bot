"""Export MTG Arena card data to JSON without touching the game client."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default locations and file names
MTGA_DATA_DIR = (
    r"/home/barrylim/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"
)
CARDS_JSON_PATH = "cards.json"
METADATA_PATH = "cards_metadata.json"


CARD_PATTERNS = ("data_cards*.mtga", "Raw_CardDatabase*.mtga")


def find_latest_cards_file(data_dir: Path) -> Optional[Path]:
    """Return the newest matching card data file or None if absent."""
    candidates = []
    for pattern in CARD_PATTERNS:
        candidates.extend([p for p in data_dir.glob(pattern) if p.is_file()])
    if not candidates:
        return None

    def sort_key(path: Path) -> tuple[float, str]:
        stat = path.stat()
        return stat.st_mtime, path.name

    return max(candidates, key=sort_key)


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
    except OSError as exc:
        print(f"Error reading file for hashing: {path}: {exc}")
        sys.exit(1)
    return digest.hexdigest()


def load_metadata(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read metadata {path}: {exc}. Re-exporting.")
        return None


def save_metadata(path: Path, metadata: Dict[str, Any]) -> None:
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)
    except OSError as exc:
        print(f"Failed to write metadata {path}: {exc}")


def simplify_card(raw: Dict[str, Any]) -> Dict[str, Any]:
    card: Dict[str, Any] = {}
    for key in (
        "grpId",
        "titleId",
        "manaCost",
        "colors",
        "types",
        "setCode",
        "rarity",
    ):
        if key in raw:
            card[key] = raw.get(key)
    return card


def parse_cards_file(path: Path) -> List[Dict[str, Any]]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        print(f"Failed to read cards file {path}: {exc}")
        sys.exit(1)

    if raw_bytes.startswith(b"SQLite format 3"):
        return parse_cards_from_sqlite(path)

    if raw_bytes.startswith(b"\x1f\x8b"):  # gzip header
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except OSError as exc:
            print(f"Failed to decompress gzip data in {path}: {exc}")
            sys.exit(1)

    text = raw_bytes.decode("utf-8", errors="ignore")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Failed to parse JSON in {path}: {exc}")
        sys.exit(1)

    cards: Optional[List[Any]] = None
    if isinstance(data, list):
        cards = data
    elif isinstance(data, dict) and isinstance(data.get("cards"), list):
        cards = data.get("cards")

    if cards is None:
        print(f"Unrecognized card data format in {path}.")
        sys.exit(1)

    simplified: List[Dict[str, Any]] = []
    for entry in cards:
        if isinstance(entry, dict):
            simplified.append(simplify_card(entry))
    return simplified


def read_enum_text_map(conn: sqlite3.Connection, enum_type: str) -> Dict[int, str]:
    try:
        cur = conn.execute(
            """
            SELECT e.Value, l.Loc
            FROM Enums e
            JOIN Localizations_enUS l ON l.LocId = e.LocId
            WHERE e.Type = ?
            """,
            (enum_type,),
        )
        return {int(value): text for value, text in cur.fetchall()}
    except sqlite3.Error as exc:
        print(f"Failed to read enum map for {enum_type}: {exc}")
        return {}


def normalize_mana_cost(mana: Optional[str]) -> Optional[str]:
    if not mana:
        return None
    parts = [part for part in mana.split("o") if part]
    if not parts:
        return None
    return "".join(f"{{{p}}}" for p in parts)


def parse_cards_from_sqlite(path: Path) -> List[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"Failed to open SQLite database {path}: {exc}")
        sys.exit(1)

    color_map = read_enum_text_map(conn, "Color")
    type_map = read_enum_text_map(conn, "CardType")
    color_symbol_map = {
        "White": "W",
        "Blue": "U",
        "Black": "B",
        "Red": "R",
        "Green": "G",
    }

    cards: List[Dict[str, Any]] = []
    try:
        cur = conn.execute(
            """
            SELECT GrpId, TitleId, OldSchoolManaText, Colors, Types, ExpansionCode, Rarity
            FROM Cards
            """
        )
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        print(f"Failed to read Cards table from {path}: {exc}")
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    rarity_map = {
        1: "Common",
        2: "Uncommon",
        3: "Rare",
        4: "Mythic",
        5: "Special",
    }

    for grp_id, title_id, mana_raw, colors_raw, types_raw, set_code, rarity_raw in rows:
        card: Dict[str, Any] = {}
        if grp_id is not None:
            card["grpId"] = grp_id
        if title_id is not None:
            card["titleId"] = title_id

        mana_cost = normalize_mana_cost(mana_raw)
        if mana_cost:
            card["manaCost"] = mana_cost

        colors_list: List[str] = []
        if isinstance(colors_raw, str) and colors_raw.strip():
            for part in colors_raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    color_id = int(part)
                except ValueError:
                    colors_list.append(part)
                    continue
                name = color_map.get(color_id)
                symbol = color_symbol_map.get(name or "", name or str(color_id))
                colors_list.append(symbol)
        if colors_list:
            card["colors"] = colors_list

        types_list: List[str] = []
        if isinstance(types_raw, str) and types_raw.strip():
            for part in types_raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    type_id = int(part)
                except ValueError:
                    types_list.append(part)
                    continue
                types_list.append(type_map.get(type_id, str(type_id)))
        if types_list:
            card["types"] = types_list

        if set_code:
            card["setCode"] = set_code

        if rarity_raw is not None:
            card["rarity"] = rarity_map.get(rarity_raw, str(rarity_raw))

        cards.append(card)

    return cards


def export_cards(cards: List[Dict[str, Any]], output_path: Path) -> None:
    try:
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(cards, handle, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"Failed to write cards JSON {output_path}: {exc}")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export MTGA card data to JSON for analysis."
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="Path to MTGA data directory containing data_cards*.mtga files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir or MTGA_DATA_DIR)
    print(f"Using data dir: {data_dir}")
    output_path = Path(CARDS_JSON_PATH)

    if not data_dir.exists() or not data_dir.is_dir():
        print(f"Data directory does not exist or is not a directory: {data_dir}")
        sys.exit(1)

    current_cards_file = find_latest_cards_file(data_dir)
    if current_cards_file is None:
        patterns = ", ".join(CARD_PATTERNS)
        print(
            f"No card data files found in {data_dir} matching: {patterns}"
        )
        sys.exit(1)
    print(f"Found latest card file: {current_cards_file.name}")

    current_mtime = current_cards_file.stat().st_mtime
    current_hash = compute_sha256(current_cards_file)

    metadata_path = Path(METADATA_PATH)
    metadata = load_metadata(metadata_path)
    if metadata:
        if (
            metadata.get("source_filename") == current_cards_file.name
            and metadata.get("source_mtime") == current_mtime
            and metadata.get("source_sha256") == current_hash
            and output_path.exists()
        ):
            print("No changes detected in card data. cards.json is up to date.")
            sys.exit(0)

    cards = parse_cards_file(current_cards_file)
    export_cards(cards, Path(CARDS_JSON_PATH))
    print(f"Parsed {len(cards)} cards, wrote {CARDS_JSON_PATH}.")

    new_metadata = {
        "source_filename": current_cards_file.name,
        "source_mtime": current_mtime,
        "source_sha256": current_hash,
    }
    save_metadata(metadata_path, new_metadata)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # Catch-all to avoid uncaught crashes
        print(f"Unexpected error: {exc}")
        sys.exit(1)
