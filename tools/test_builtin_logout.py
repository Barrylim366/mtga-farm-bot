import json
import argparse
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Controller.MTGAController.Controller import Controller


def _load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_screen_bounds(value) -> tuple[tuple[int, int], tuple[int, int]]:
    try:
        p1 = value[0]
        p2 = value[1]
        return (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1]))
    except Exception:
        return (0, 0), (2560, 1440)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test built-in logout/account-switch flows.")
    parser.add_argument(
        "--full-switch",
        action="store_true",
        help="Run complete built-in account switch flow (logout + login + post-login).",
    )
    args = parser.parse_args()

    root = ROOT
    cfg_path = root / "calibration_config.json"
    if not cfg_path.exists():
        print(f"calibration_config.json nicht gefunden: {cfg_path}")
        return 1

    cfg = _load_config(cfg_path)
    log_path = str(cfg.get("log_path", "")).strip()
    if not log_path:
        print("log_path fehlt in calibration_config.json")
        return 1

    screen_bounds = _parse_screen_bounds(cfg.get("screen_bounds"))
    click_targets = cfg.get("click_targets", {}) or {}
    input_backend = str(cfg.get("input_backend", "auto") or "auto")

    print("Initialisiere Controller...")
    controller = Controller(
        log_path=log_path,
        screen_bounds=screen_bounds,
        click_targets=click_targets,
        input_backend=input_backend,
        account_switch_minutes=0,
    )

    if args.full_switch:
        print("Starte kompletten eingebauten Account-Switch-Flow in 2 Sekunden...")
        time.sleep(2.0)
        controller._perform_account_switch()  # Intentional test hook for real runtime path.
        print("Full Account-Switch-Flow abgeschlossen (Details siehe bot.log).")
        return 0

    print("Starte eingebaute Logout-Sequenz in 2 Sekunden...")
    time.sleep(2.0)
    ok = controller.run_mapped_logout_sequence_for_test()
    print("Fertig." if ok else "Fehler in Logout-Sequenz.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
