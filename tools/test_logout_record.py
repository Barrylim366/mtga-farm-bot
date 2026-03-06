import json
import time
from pathlib import Path

from pynput import keyboard, mouse


def _load_logout_record(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    records = data.get("records", [])
    for rec in reversed(records):
        if rec.get("name") in {"Logout", "logout", "Account Switch"}:
            return rec
    return None


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    record_path = root / "recorded_actions_records.json"
    if not record_path.exists():
        print(f"recorded_actions_records.json nicht gefunden: {record_path}")
        return 1

    rec = _load_logout_record(record_path)
    if rec is None:
        print("Kein passender Record gefunden (erwartet: Logout/logout/Account Switch).")
        return 1

    actions = rec.get("actions", [])
    if not actions:
        print("Record hat keine Actions.")
        return 1

    m = mouse.Controller()
    k = keyboard.Controller()

    print(f"Nutze Record: {rec.get('name')} ({len(actions)} actions)")
    print("Starte in 2 Sekunden...")
    time.sleep(2.0)

    for ev in actions:
        delay = float(ev.get("delay", 0.0))
        if delay > 0:
            time.sleep(delay)
        ev_type = ev.get("type")
        if ev_type == "click":
            x = int(float(ev.get("x", 0)))
            y = int(float(ev.get("y", 0)))
            print(f"click {x},{y}")
            m.position = (x, y)
            time.sleep(0.05)
            m.press(mouse.Button.left)
            time.sleep(0.05)
            m.release(mouse.Button.left)
        elif ev_type == "key":
            key_name = str(ev.get("key", ""))
            print(f"key {key_name}")
            if key_name == "esc":
                k.press(keyboard.Key.esc)
                k.release(keyboard.Key.esc)
            elif len(key_name) == 1:
                k.press(key_name)
                k.release(key_name)

    print("Fertig.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
