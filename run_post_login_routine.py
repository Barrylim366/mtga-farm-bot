import argparse
import json
import os
import time

import pyautogui


def _default_player_log_path() -> str:
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(
            home,
            "AppData",
            "LocalLow",
            "Wizards Of The Coast",
            "MTGA",
            "Player.log",
        )
    return os.path.join(
        home,
        ".local",
        "share",
        "Steam",
        "steamapps",
        "compatdata",
        "2141910",
        "pfx",
        "drive_c",
        "users",
        "steamuser",
        "AppData",
        "LocalLow",
        "Wizards Of The Coast",
        "MTGA",
        "Player.log",
    )


GUILD_COLOR_MAP = {
    "azorius": "WU",
    "dimir": "UB",
    "rakdos": "RB",
    "gruul": "RG",
    "selesnya": "GW",
    "orzhov": "WB",
    "izzet": "UR",
    "golgari": "BG",
    "boros": "RW",
    "simic": "UG",
}
COLOR_LETTERS = set("WUBRG")


def read_log_tail(path, max_bytes=600000):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    return data.decode("utf-8", errors="ignore")


def extract_latest_quests(log_path):
    text = read_log_tail(log_path)
    idx = text.rfind('"quests"')
    if idx == -1:
        return []
    start = text.rfind("{", 0, idx)
    if start == -1:
        return []
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text[start:])
    except Exception:
        return []
    quests = payload.get("quests", [])
    return quests if isinstance(quests, list) else []


def parse_guild_quests(log_path):
    quests = extract_latest_quests(log_path)
    parsed = []
    for quest in quests:
        loc_key = str(quest.get("locKey", "")).lower()
        guild = None
        for name in GUILD_COLOR_MAP:
            if name in loc_key:
                guild = name
                break
        if not guild:
            continue
        gold = 0
        chest = quest.get("chestDescription") or {}
        loc_params = chest.get("locParams") or {}
        if isinstance(loc_params, dict):
            try:
                gold = int(loc_params.get("number1") or 0)
            except (TypeError, ValueError):
                gold = 0
        parsed.append({"guild": guild, "gold": gold})
    return parsed


def select_best_quest(log_path):
    quests = parse_guild_quests(log_path)
    if not quests:
        return None
    quests.sort(key=lambda q: q.get("gold", 0), reverse=True)
    return quests[0]


def choose_deck_image(account_dir, target_letters):
    images = [n for n in os.listdir(account_dir) if n.lower().endswith((".png", ".jpg", ".jpeg"))]
    if not images:
        return None
    if not target_letters:
        return os.path.join(account_dir, images[0])
    target_set = set(target_letters.upper())
    best = None
    best_score = (-1, -999, 0, "")
    for name in images:
        name_letters = {ch for ch in name.upper() if ch in COLOR_LETTERS}
        score = len(name_letters & target_set)
        extra = len(name_letters - target_set)
        tie = (score, -extra, -len(name), name.lower())
        if tie > best_score:
            best_score = tie
            best = name
    if best is None or best_score[0] <= 0:
        return os.path.join(account_dir, images[0])
    return os.path.join(account_dir, best)


def hard_click(pos):
    pyautogui.moveTo(pos.x, pos.y, duration=0.1)
    time.sleep(0.05)
    pyautogui.mouseDown()
    time.sleep(0.06)
    pyautogui.mouseUp()
    time.sleep(0.05)


LOG_PATH = os.path.join(os.path.abspath(os.path.dirname(__file__)), "post_login_test.log")


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def click_image(image_path, label, confidence=0.82, timeout=20.0):
    start = time.time()
    log(f"searching {label}: confidence={confidence:.2f}, timeout={timeout:.1f}s")
    while (time.time() - start) < timeout:
        pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
        if pos:
            hard_click(pos)
            log(f"clicked {label} at {pos}")
            return True
        time.sleep(0.5)
        elapsed = time.time() - start
        if int(elapsed) % 2 == 0:
            log(f"still searching {label}... {elapsed:.1f}s")
    log(f"not found: {label}")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="Account_1", help="Account folder name")
    parser.add_argument(
        "--log",
        default=_default_player_log_path(),
    )
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.dirname(__file__))
    account_dir = os.path.join(repo_root, args.account)
    buttons_dir = os.path.join(repo_root, "Buttons")

    if not os.path.isdir(account_dir):
        print(f"account folder not found: {account_dir}")
        return

    quest = select_best_quest(args.log)
    if quest:
        colors = GUILD_COLOR_MAP.get(quest.get("guild", ""), "")
        print(f"quest: {quest['guild']} colors={colors} gold={quest['gold']}")
    else:
        colors = ""
        print("no guild quest found")

    deck_image = choose_deck_image(account_dir, colors)
    if not deck_image:
        print("no deck images found")
        return
    print(f"selected deck image: {deck_image}")

    log("focus MTGA (2s)...")
    time.sleep(2)

    if not click_image(os.path.join(buttons_dir, "play_btn.png"), "play"):
        return
    log("clicked play, waiting 1s")
    time.sleep(1)
    if not click_image(os.path.join(buttons_dir, "find_match_btn.png"), "find_match"):
        return
    log("clicked find_match, waiting 2s")
    # Historic Play often appears after a short transition; give it more time and a slightly lower confidence.
    time.sleep(2)
    if not click_image(os.path.join(buttons_dir, "hist_play_btn.png"), "hist_play", confidence=0.75, timeout=40.0):
        return
    log("clicked hist_play, waiting 1s")
    time.sleep(1)
    if not click_image(os.path.join(buttons_dir, "my_decks.png"), "my_decks"):
        return
    log("clicked my_decks, waiting 1s")
    time.sleep(1)
    if not click_image(deck_image, "deck"):
        return
    log("clicked deck, waiting 1s")
    time.sleep(1)
    click_image(os.path.join(buttons_dir, "play_btn.png"), "play_confirm")


if __name__ == "__main__":
    main()
