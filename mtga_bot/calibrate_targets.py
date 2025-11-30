from __future__ import annotations

"""
Small helper to capture screen-relative coordinates for MTGA buttons/hand slots.

Usage:
    source .venv/bin/activate
    python -m mtga_bot.calibrate_targets

It will prompt you to hover the mouse over targets and press Enter.
Results are saved to calibration.json in the repo root and printed as a
config snippet (click_targets, hand_x/y ratios).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pyautogui


REPO_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION_FILE = REPO_ROOT / "calibration.json"


@dataclass
class TargetSpec:
    name: str
    prompt: str


BUTTONS: List[TargetSpec] = [
    TargetSpec("queue_button", "Hover Queue/Play button"),
    TargetSpec("keep_hand", "Hover Keep 7 button"),
    TargetSpec("mulligan", "Hover Mulligan button (optional, Enter to skip)"),
    TargetSpec("concede", "Hover Concede/Leave match confirmation"),
    TargetSpec("next_button", "Hover Next/End step button"),
    TargetSpec("submit_button", "Hover Submit/Resolve button (optional)"),
]


def _capture_point(prompt: str, allow_skip: bool = False) -> Tuple[float, float] | None:
    print(f"{prompt} and press Enter...", flush=True)
    input()
    pos = pyautogui.position()
    width, height = pyautogui.size()
    rel_x = pos.x / width
    rel_y = pos.y / height
    print(f"  Captured at abs=({pos.x}, {pos.y}) rel=({rel_x:.4f}, {rel_y:.4f})")
    return (rel_x, rel_y)


def main() -> None:
    pyautogui.FAILSAFE = False
    # Prefer scrot if available and backend not set.
    if not os.environ.get("PYAUTOGUI_SCREENSHOT"):
        os.environ["PYAUTOGUI_SCREENSHOT"] = "scrot"
    width, height = pyautogui.size()
    print(f"Screen detected: {width}x{height}")

    targets: Dict[str, Tuple[float, float]] = {}
    for spec in BUTTONS:
        allow_skip = "optional" in spec.prompt.lower()
        res = _capture_point(spec.prompt, allow_skip=allow_skip)
        if res:
            targets[spec.name] = res

    print("\nHand calibration: hover three hand cards (left-ish, middle, right-ish) and press Enter for each.")
    hand_points: List[Tuple[float, float]] = []
    for idx in range(3):
        res = _capture_point(f"Hand slot {idx+1}")
        if res:
            hand_points.append(res)

    hand_y = sum(pt[1] for pt in hand_points) / len(hand_points) if hand_points else 0.9
    hand_x = [pt[0] for pt in hand_points] if hand_points else [0.46, 0.54, 0.62]

    data = {
        "click_targets": targets,
        "hand_y_ratio": hand_y,
        "hand_x_ratios": hand_x,
        "land_y_ratio": hand_y,  # often similar height
        "land_x_ratios": hand_x,
    }

    CALIBRATION_FILE.write_text(json.dumps(data, indent=2))
    print(f"\nSaved calibration to {CALIBRATION_FILE}")
    print("\nConfig snippet to copy into config.json:\n")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
