import json
import time
import urllib.request
import urllib.error
import os
import shutil
import sys
from pathlib import Path

from runtime_paths import ensure_runtime_subdir

# Scryfall now rejects (HTTP 400) any request missing an Accept header in
# addition to User-Agent. Sending only User-Agent made every card/oracle
# lookup fail, and the failures were cached as empty strings -- which is why
# removal detection (and anything else needing oracle text) silently broke.
_SCRYFALL_HEADERS = {"User-Agent": "MTGABot/1.0", "Accept": "application/json"}

# In-memory (never persisted) negative cache for transient Scryfall failures
# (network unreachable, timeout, 429/500...). Genuine 404s are cached to disk
# as "no data" (see _scryfall_cache / _scryfall_oracle_cache below), but a
# transient failure must not poison that persistent cache -- one bad response
# would then look like "confirmed no data" forever. Without any cooldown at
# all, though, every decision-loop call for the same card re-issues a blocking
# request with the same failure, which can burn the in-game priority timer.
# This short in-memory cooldown is the middle ground: fast-fail for a few
# minutes, then try again in case connectivity/rate-limiting recovered.
_TRANSIENT_FAILURE_TTL_SEC = 120
_transient_failure_until: dict[str, float] = {}


def _transient_failure_active(cache_key: str) -> bool:
    until = _transient_failure_until.get(cache_key)
    return until is not None and time.time() < until


def _mark_transient_failure(cache_key: str) -> None:
    _transient_failure_until[cache_key] = time.time() + _TRANSIENT_FAILURE_TTL_SEC


def _app_root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _app_data_path(filename: str) -> str:
    return str(ensure_runtime_subdir("cache") / filename)


def _resource_root_dir() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if isinstance(meipass, str) and meipass and os.path.isdir(meipass):
            return os.path.abspath(meipass)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _resource_data_path(filename: str) -> str:
    data_candidate = os.path.join(_resource_root_dir(), "data", filename)
    if os.path.exists(data_candidate):
        return data_candidate
    return os.path.join(_resource_root_dir(), filename)


def _seed_data_file(filename: str) -> None:
    dst = _app_data_path(filename)
    src = _resource_data_path(filename)
    if os.path.exists(dst):
        return
    if not os.path.exists(src):
        return
    if os.path.abspath(src) == os.path.abspath(dst):
        return
    try:
        shutil.copy2(src, dst)
    except Exception:
        pass


def _load_json_with_fallback(path: str, fallback_path: str, default):
    for candidate in (path, fallback_path):
        if not candidate:
            continue
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return default


CARD_DATA_PATH = _app_data_path("cards.json")
SCRYFALL_CACHE_PATH = _app_data_path("scryfall_cache.json")
SCRYFALL_ORACLE_CACHE_PATH = _app_data_path("scryfall_oracle_cache.json")
MISSING_CARDS_PATH = _app_data_path("missing_cards.json")
for _seed_name in (
    "cards.json",
    "scryfall_cache.json",
    "scryfall_oracle_cache.json",
    "missing_cards.json",
):
    _seed_data_file(_seed_name)
_card_data = []
_scryfall_cache = {}
_scryfall_oracle_cache = {}

_scryfall_cache = _load_json_with_fallback(
    SCRYFALL_CACHE_PATH,
    _resource_data_path("scryfall_cache.json"),
    {},
)
if not isinstance(_scryfall_cache, dict):
    _scryfall_cache = {}

_scryfall_oracle_cache = _load_json_with_fallback(
    SCRYFALL_ORACLE_CACHE_PATH,
    _resource_data_path("scryfall_oracle_cache.json"),
    {},
)
if not isinstance(_scryfall_oracle_cache, dict):
    _scryfall_oracle_cache = {}


def _save_scryfall_oracle_cache():
    """Save the oracle text cache to disk"""
    try:
        with open(SCRYFALL_ORACLE_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(_scryfall_oracle_cache, f)
    except Exception:
        pass


def _save_scryfall_cache():
    """Save the scryfall cache to disk"""
    try:
        with open(SCRYFALL_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(_scryfall_cache, f)
    except Exception:
        pass


def _load_missing_cards() -> list[int]:
    candidates = [MISSING_CARDS_PATH, _resource_data_path("missing_cards.json")]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [int(x) for x in data if isinstance(x, int) or str(x).isdigit()]
        except Exception:
            continue
    return []


def _save_missing_cards(ids: list[int]) -> None:
    try:
        with open(MISSING_CARDS_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(set(ids)), f, indent=2)
    except Exception:
        pass


def _fetch_card_info_from_scryfall(arena_id: int) -> dict | None:
    # Same transient-failure cooldown as get_produced_mana_from_scryfall /
    # get_oracle_text_from_scryfall, and deliberately the same cache key: all
    # three hit /cards/arena/{id}, so a network failure for one is a failure for
    # all. Without it, an unknown grpId on the board with Scryfall unreachable
    # re-issues a blocking 8s request on every get_card_info() call inside the
    # hot removal/fight loop (_creature_keywords, _source_has_deathtouch,
    # killable_by_damage), which can burn the in-game priority timer.
    cache_key = str(arena_id)
    if _transient_failure_active(cache_key):
        return None
    try:
        url = f"https://api.scryfall.com/cards/arena/{arena_id}"
        req = urllib.request.Request(url, headers=_SCRYFALL_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
        # Normalize to the fields the bot uses from cards.json
        card = {
            "grpId": arena_id,
            "titleId": data.get("oracle_id"),
            "manaCost": data.get("mana_cost", ""),
            "colors": data.get("colors", []),
            "types": data.get("type_line", "").replace("—", "-").split(),
            "setCode": data.get("set", "").upper(),
            "rarity": data.get("rarity", ""),
            "name": data.get("name", f"Card#{arena_id}"),
            "oracleText": data.get("oracle_text", ""),
            "keywords": data.get("keywords", []),
        }
        return card
    except urllib.error.HTTPError as e:
        # A genuine 404 means this arena_id isn't on Scryfall at all -- no point
        # in a cooldown, the caller records it in missing_cards.json. Any other
        # status is transient and must trip the cooldown.
        if e.code != 404:
            _mark_transient_failure(cache_key)
        return None
    except Exception:
        _mark_transient_failure(cache_key)
        return None


# There is deliberately no Scryfall bulk-download path here. Merging the
# ~600 MB default_cards bulk file could only ever add Arena IDs that cards.json
# does not already have -- and the MTGA export covers every Arena grpId, so it
# added nothing while blocking the first start of each day (Scryfall rebuilds
# the bulk file daily) on the download plus a full in-memory parse. Missing
# cards and the oracle text/keywords the export never carries are resolved per
# card and cached instead: refresh_missing_cards() below, get_card_info()'s
# _fetch_card_info_from_scryfall() fallback, and get_oracle_text_from_scryfall().


def refresh_missing_cards() -> None:
    """
    Try to resolve any previously missing Arena IDs from Scryfall.
    This keeps cards.json up to date across sessions without a full bulk download.
    """
    ids = _load_missing_cards()
    if not ids:
        return
    updated = False
    remaining = []
    for arena_id in ids:
        card = _fetch_card_info_from_scryfall(arena_id)
        if card:
            _card_data.append(card)
            updated = True
        else:
            remaining.append(arena_id)
    if updated:
        try:
            with open(CARD_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(_card_data, f, indent=2)
        except Exception:
            pass
    _save_missing_cards(remaining)


def get_produced_mana_from_scryfall(arena_id: int):
    """
    Fetch the produced_mana colors for a card from Scryfall API.
    Results are cached to avoid repeated API calls.

    Parameters:
        arena_id: The MTGA arena ID (grpId)
    Returns:
        List of color codes like ['B', 'G'] or None if not found
    """
    cache_key = str(arena_id)

    # Check cache first
    if cache_key in _scryfall_cache:
        return _scryfall_cache[cache_key]
    if _transient_failure_active(cache_key):
        return None

    # Fetch from Scryfall
    try:
        url = f"https://api.scryfall.com/cards/arena/{arena_id}"
        req = urllib.request.Request(url, headers=_SCRYFALL_HEADERS)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            produced_mana = data.get('produced_mana', [])

            # Cache the result
            _scryfall_cache[cache_key] = produced_mana
            _save_scryfall_cache()

            return produced_mana
    except urllib.error.HTTPError as e:
        # Only a genuine 404 is worth caching as "no data"; other statuses are
        # transient and must not poison the cache (see oracle-cache note above).
        if e.code == 404:
            _scryfall_cache[cache_key] = None
            _save_scryfall_cache()
        else:
            _mark_transient_failure(cache_key)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        # A read timeout (as opposed to a connect timeout) raises a bare
        # TimeoutError, not URLError -- urlopen only wraps failures up to
        # establishing the connection. Without this, a slow/stalled Scryfall
        # response escapes uncaught into the caller (get_land_produced_colors
        # -> _score_land -> generate_move's decision loop), discarding the
        # AI's entire move for that decision instead of degrading to "unknown
        # color, treat as wildcard" like every other missing-data case here.
        _mark_transient_failure(cache_key)
        return None


def get_oracle_text_from_scryfall(arena_id: int):
    """
    Fetch the oracle_text for a card from Scryfall API.
    Results are cached to avoid repeated API calls.
    """
    cache_key = str(arena_id)
    if cache_key in _scryfall_oracle_cache:
        return _scryfall_oracle_cache[cache_key]
    if _transient_failure_active(cache_key):
        return ""
    try:
        url = f"https://api.scryfall.com/cards/arena/{arena_id}"
        req = urllib.request.Request(url, headers=_SCRYFALL_HEADERS)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            oracle_text = data.get('oracle_text', '') or ''
            _scryfall_oracle_cache[cache_key] = oracle_text
            _save_scryfall_oracle_cache()
            return oracle_text
    except urllib.error.HTTPError as e:
        # 404 = this arena_id genuinely isn't on Scryfall; cache the miss so we
        # don't hammer it. Any other HTTP status (e.g. transient 429/500, or the
        # old 400 header bug) must NOT be cached, or one bad response poisons the
        # entry forever.
        if e.code == 404:
            _scryfall_oracle_cache[cache_key] = ""
            _save_scryfall_oracle_cache()
        else:
            _mark_transient_failure(cache_key)
        return ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        # Network/parse failure: transient, do not poison the persistent cache,
        # but do avoid hammering it for a couple of minutes. TimeoutError is
        # listed explicitly because a read timeout raises it bare, not wrapped
        # in URLError -- see the identical fix in get_produced_mana_from_scryfall.
        _mark_transient_failure(cache_key)
        return ""


def get_land_produced_colors(arena_id: int):
    """
    Get the mana colors a land can produce.

    Parameters:
        arena_id: The MTGA arena ID (grpId)
    Returns:
        Set of color strings like {'black', 'green'} or empty set if unknown
    """
    color_map = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}

    # First, try to get from local card data using titleId for basic lands
    card_info = get_card_info(arena_id)
    if card_info:
        title_id = card_info.get('titleId')
        if title_id in BASIC_LAND_MANA_MAP:
            return {BASIC_LAND_MANA_MAP[title_id]}
        name = card_info.get('name')
        if name in BASIC_LAND_NAME_MAP:
            return {BASIC_LAND_NAME_MAP[name]}

    # For non-basic lands, try Scryfall
    produced_mana = get_produced_mana_from_scryfall(arena_id)
    if produced_mana:
        return {color_map.get(c, c) for c in produced_mana if c in color_map}

    return set()


# Basic Land titleId to mana color mapping
# All verified from player.log mana activation data
BASIC_LAND_MANA_MAP = {
    647: "green",    # Forest (verified: grpId 95200 -> ManaColor_Green)
    648: "white",    # Plains (verified: grpId 95192 -> ManaColor_White)
    652: "blue",     # Island (verified)
    653: "black",    # Swamp (verified)
    1250: "red",     # Mountain (verified)
}

# Basic land name -> mana color, keyed by name rather than titleId. The curated
# Starter Deck Duel DB (data/starter_deck_cards.json) has no titleId field, so it
# shadows cards.json for every basic-land grpId and BASIC_LAND_MANA_MAP alone
# would miss every basic in that mode and fall back to a live Scryfall call.
BASIC_LAND_NAME_MAP = {
    "Plains": "white",
    "Island": "blue",
    "Swamp": "black",
    "Mountain": "red",
    "Forest": "green",
}

# abilityGrpId to mana color mapping for ActionType_Activate_Mana.
# These are MTGA's standard tap-for-mana ability IDs in WUBRG order.
# Verified from Player.log data:
#   Mountain (titleId 1250) -> abilityGrpId 1004 = RED
#   Forest (titleId 647) -> abilityGrpId 1005 = GREEN
#   Dual land with SubType_Swamp+SubType_Forest carries 1003 + 1005 -> 1003 = BLACK
MANA_ABILITY_MAP = {
    1001: "white",
    1002: "blue",
    1003: "black",
    1004: "red",
    1005: "green",
}


def get_mana_color_from_ability(ability_grp_id: int):
    """
    Returns the mana color for a given abilityGrpId from ActionType_Activate_Mana.

    Parameters:
        ability_grp_id: The abilityGrpId from the action
    Returns:
        The mana color as a string ('white', 'blue', 'black', 'red', 'green')
        or None if not a recognized mana ability
    """
    return MANA_ABILITY_MAP.get(ability_grp_id)

_card_data = _load_json_with_fallback(
    CARD_DATA_PATH,
    _resource_data_path("cards.json"),
    [],
)
if not isinstance(_card_data, list):
    _card_data = []

# Curated Starter Deck Duel card DB (grpId -> full info incl. oracle text) for
# exactly the ten Foundations Starter Decks. Committed and offline: the MTGA
# export (cards.json) carries no oracle text and Starter Deck Duel only ever
# uses these cards, so this is consulted first and removes the live-Scryfall
# dependency during matches. Regenerate with tools/build_starter_deck_cards.py.
_starter_cards = _load_json_with_fallback(
    _resource_data_path("starter_deck_cards.json"),
    "",
    {},
)
if not isinstance(_starter_cards, dict):
    _starter_cards = {}


def reload_cards_from_disk() -> None:
    """Reload cards.json into memory after an export/update."""
    global _card_data
    data = _load_json_with_fallback(
        CARD_DATA_PATH,
        _resource_data_path("cards.json"),
        [],
    )
    if isinstance(data, list):
        _card_data = data


def warm_up_starter_data() -> dict:
    """Eagerly load the curated Starter Deck Duel card DB and pre-resolve the
    removal/counter profiles for every card in it.

    Called once at bot startup. It pays the JSON parse + profile scan cost
    (~0.2s) up front, instead of on the first in-game decision, and validates
    the data is present. Returns a summary dict for logging.
    """
    global _starter_cards
    if not isinstance(_starter_cards, dict) or not _starter_cards:
        _starter_cards = _load_json_with_fallback(
            _resource_data_path("starter_deck_cards.json"), "", {}
        )
        if not isinstance(_starter_cards, dict):
            _starter_cards = {}

    removal = counter = 0
    # Local imports: RemovalLogic/CounterLogic import this module, so importing
    # them at module load time would be circular. At call time both exist.
    try:
        import AI.Utilities.RemovalLogic as RemovalLogic
        import AI.Utilities.CounterLogic as CounterLogic
        for gid in _starter_cards:
            try:
                if RemovalLogic.get_removal_profile(int(gid)):
                    removal += 1
                if CounterLogic.get_counter_profile(int(gid)):
                    counter += 1
            except Exception:
                continue
    except Exception:
        pass
    return {"cards": len(_starter_cards), "removal": removal, "counter": counter}


_card_data_index: dict = {}
_card_data_index_state: tuple | None = None


def _get_card_data_index() -> dict:
    """grpId -> card dict, rebuilt from _card_data whenever it changes.

    _card_data is a plain list scanned linearly by get_card_info, which used to
    be an O(n) walk over the full card DB (~26k entries from the MTGA export) on
    every miss of the small starter dict -- expensive when called per-creature inside the
    hot removal/fight evaluation loop. The (id, len) pair changes on both an
    in-place append and a wholesale reassignment (reload_cards_from_disk), which
    are the only two ways _card_data is ever mutated, so it is a cheap and
    correct staleness check without having to touch every call site.
    """
    global _card_data_index, _card_data_index_state
    state = (id(_card_data), len(_card_data))
    if state != _card_data_index_state:
        _card_data_index = {
            card.get("grpId"): card
            for card in _card_data
            if isinstance(card, dict) and card.get("grpId") is not None
        }
        _card_data_index_state = state
    return _card_data_index


def get_card_info(mtga_id: int):
    """
    Parameters
        mtga_id: Must be a valid mtg arena id
    Returns
        A dictionary object containing full info of the card that has the specified MTGA id
    """
    # Curated Starter Deck Duel data first: it has oracle text (which the MTGA
    # export lacks) and covers every card that can appear in this mode.
    starter = _starter_cards.get(str(mtga_id))
    if starter:
        return starter
    card = _get_card_data_index().get(mtga_id)
    if card is not None:
        return card
    # Not found in local data: try Scryfall once and cache.
    card = _fetch_card_info_from_scryfall(mtga_id)
    if card:
        _card_data.append(card)
        try:
            with open(CARD_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(_card_data, f, indent=2)
        except Exception:
            pass
        return card
    # Track missing IDs for refresh on next start.
    ids = _load_missing_cards()
    if mtga_id not in ids:
        ids.append(mtga_id)
        _save_missing_cards(ids)
    return None # Return None if card not found


def get_card_info_local(mtga_id: int):
    """Offline-only variant of get_card_info.

    Looks the card up in the two local layers (curated starter DB, then the
    on-disk cards.json index) and returns None on a miss. Unlike
    get_card_info() it NEVER falls back to a blocking Scryfall request and
    NEVER rewrites cards.json/missing_cards. Use this on latency-sensitive
    paths (e.g. the debug snapshot recorder) where a network round-trip could
    burn the in-game priority timer. In Starter Deck Duel the starter layer is
    warmed by warm_up_starter_data() at startup, so the hit rate is ~100%.
    """
    if mtga_id is None:
        return None
    try:
        starter = _starter_cards.get(str(mtga_id))
    except Exception:
        starter = None
    if starter:
        return starter
    try:
        return _get_card_data_index().get(mtga_id)
    except Exception:
        return None


def get_oracle_text(mtga_id: int) -> str:
    card_info = get_card_info(mtga_id)
    if card_info and card_info.get("oracleText"):
        return str(card_info.get("oracleText") or "")
    return str(get_oracle_text_from_scryfall(mtga_id) or "")


def card_has_convoke(mtga_id: int) -> bool:
    card_info = get_card_info(mtga_id) or {}
    keywords = card_info.get("keywords", []) or []
    if any(str(k).lower() == "convoke" for k in keywords):
        return True
    oracle_text = card_info.get("oracleText") or get_oracle_text(mtga_id)
    return "convoke" in str(oracle_text).lower()


def calculate_cmc(mana_cost: str) -> int:
    """
    Convert manaCost like "{2}{W}{W}" to a simple integer mana value.
    """
    if not mana_cost:
        return 0
    symbols = mana_cost.replace("}{", " ").replace("{", "").replace("}", "").split()
    total = 0
    for sym in symbols:
        if not sym:
            continue
        if sym.lower() == "x":
            continue
        if sym.isdigit():
            total += int(sym)
        else:
            total += 1
    return total


def get_land_mana_color(mtga_id: int):
    """
    Returns the mana color produced by a land card.

    Parameters:
        mtga_id: The MTGA grpId of the land card
    Returns:
        The mana color as a string ('white', 'blue', 'black', 'red', 'green')
        or None if not a recognized basic land
    """
    card_info = get_card_info(mtga_id)
    if card_info is None:
        return None

    # Check if it's a land
    if 'Land' not in card_info.get('types', []):
        return None

    # Get mana color from titleId mapping for basic lands
    title_id = card_info.get('titleId')
    if title_id in BASIC_LAND_MANA_MAP:
        return BASIC_LAND_MANA_MAP[title_id]
    name = card_info.get('name')
    if name in BASIC_LAND_NAME_MAP:
        return BASIC_LAND_NAME_MAP[name]

    # For non-basic lands with colors field, use that
    colors = card_info.get('colors', [])
    if colors:
        color_map = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}
        return color_map.get(colors[0])

    return None
