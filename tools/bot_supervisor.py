from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Controller.Utilities.input_controller import create_input_controller
from bot_logger import ensure_debug_dir
from runtime_paths import runtime_file
from runtime_status import get_status_path, read_status
from state.state_machine import BotState, get_state_from_playerlog
from tools.incident_tracking import build_related_incidents_payload, build_signature_knowledge_payload, ensure_tracking_file
from vision.vision import VisionEngine
from vision.window_locator import ArenaRegionProvider, focus_mtga_window


def load_default_concede_rel() -> tuple[int, int]:
    default_rel = (962, 631)
    config_path = runtime_file("config", "calibration_config.json")
    try:
        if not config_path.is_file():
            return default_rel
        with config_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        click_targets = data.get("click_targets", {})
        concede = click_targets.get("concede", {})
        x = int(concede.get("x"))
        y = int(concede.get("y"))
        if not (0 <= x <= 1920 and 0 <= y <= 1080):
            return default_rel
        return (x, y)
    except Exception:
        return default_rel


DEFAULT_CONCEDE_REL = load_default_concede_rel()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MTGA bot under a stuck-recovery supervisor.")
    parser.add_argument("--poll-sec", type=float, default=2.0, help="Supervisor polling interval.")
    parser.add_argument("--stuck-seconds", type=float, default=300.0, help="No-activity threshold before recovery.")
    parser.add_argument(
        "--startup-grace-sec",
        type=float,
        default=45.0,
        help="Ignore stale/old runtime status for this many seconds after starting a new child.",
    )
    parser.add_argument(
        "--my-timer-critical-threshold",
        type=int,
        default=1,
        help="Treat own sand-clock critical events as stuck after this many hits in one match.",
    )
    parser.add_argument(
        "--my-timer-stall-sec",
        type=float,
        default=20.0,
        help="Treat a running own inactivity timer with no bot action for this many seconds as stuck.",
    )
    parser.add_argument("--restart-delay-sec", type=float, default=3.0, help="Delay before restarting the bot.")
    parser.add_argument(
        "--stop-after-incident",
        action="store_true",
        help="Stop the supervisor after one handled incident instead of restarting the bot automatically.",
    )
    parser.add_argument("--input-backend", default=os.environ.get("MTGA_BOT_INPUT_BACKEND", "auto"))
    parser.add_argument("--codex-template", default=str(ROOT_DIR / "supervisor" / "codex_window.png"))
    parser.add_argument("--mtga-launch-cmd", default=os.environ.get("MTGA_SUPERVISOR_MTGA_LAUNCH_CMD", ""))
    parser.add_argument(
        "--mtga-process-names",
        default=os.environ.get("MTGA_SUPERVISOR_MTGA_PROCESS_NAMES", "MTGA.exe,MTGALauncher.exe"),
        help="Comma-separated process names to kill before relaunch.",
    )
    parser.add_argument(
        "--concede-rel-x",
        type=int,
        default=int(DEFAULT_CONCEDE_REL[0]),
        help="1920-relative X coordinate for the in-game Concede button after ESC opens options.",
    )
    parser.add_argument(
        "--concede-rel-y",
        type=int,
        default=int(DEFAULT_CONCEDE_REL[1]),
        help="1920-relative Y coordinate for the in-game Concede button after ESC opens options.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Bot command after `--`. Defaults to `python run_bot.py`.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = normalize_command(args.command)
    vision = VisionEngine()
    provider = ArenaRegionProvider(
        vision=vision,
        assets_dir=str(ROOT_DIR / "assets" / "assert"),
    )
    input_controller = create_input_controller(args.input_backend)
    last_recovery_epoch = 0.0

    while True:
        child = start_child(command)
        child_started_at = time.time()
        try:
            while True:
                if child.poll() is not None:
                    break
                status = read_status()
                if not status:
                    time.sleep(max(0.5, args.poll_sec))
                    continue
                if should_skip_due_to_startup(status, child_pid=child.pid, child_started_at=child_started_at, startup_grace_sec=args.startup_grace_sec):
                    time.sleep(max(0.5, args.poll_sec))
                    continue
                trigger_reason = detect_stuck_reason(status, args)
                if trigger_reason is None and should_skip_due_to_wait(status):
                    time.sleep(max(0.5, args.poll_sec))
                    continue
                if trigger_reason is None:
                    mode = str(status.get("mode") or "")
                    if mode not in {"in_game", "stuck_suspected"}:
                        time.sleep(max(0.5, args.poll_sec))
                        continue
                stale_for = compute_stale_seconds(status)
                if trigger_reason is None and stale_for < float(args.stuck_seconds):
                    time.sleep(max(0.5, args.poll_sec))
                    continue
                now = time.time()
                if (now - last_recovery_epoch) < 30.0:
                    time.sleep(max(0.5, args.poll_sec))
                    continue
                last_recovery_epoch = now
                if trigger_reason is None:
                    trigger_reason = "supervisor_stuck_timeout"
                incident_dir = ""
                recovery = {
                    "ok": False,
                    "trigger_reason": trigger_reason,
                    "actions": [],
                }
                codex_payload = {
                    "ok": False,
                    "reason": "skipped_due_to_recovery_failure",
                }
                try:
                    incident_dir = write_incident_bundle(
                        status=status,
                        stale_for=stale_for,
                        vision=vision,
                        provider=provider,
                        reason=trigger_reason,
                    )
                    write_recovery_result(incident_dir, recovery)
                    write_codex_result(incident_dir, codex_payload)
                    terminate_child(child)
                    recovery = attempt_recovery(
                        status=status,
                        incident_dir=incident_dir,
                        trigger_reason=trigger_reason,
                        input_controller=input_controller,
                        vision=vision,
                        provider=provider,
                        mtga_launch_cmd=str(args.mtga_launch_cmd or "").strip(),
                        mtga_process_names=parse_process_names(args.mtga_process_names),
                        concede_rel=(int(args.concede_rel_x), int(args.concede_rel_y)),
                    )
                    codex_payload = {
                        "ok": False,
                        "reason": "skipped_not_on_home",
                    }
                    if recovery.get("ok") and recovery.get("final_state") == str(BotState.HOME):
                        codex_payload = notify_codex(
                            input_controller=input_controller,
                            vision=vision,
                            template_path=str(args.codex_template),
                            debug_dir=incident_dir,
                        )
                except Exception as exc:
                    crash_text = traceback.format_exc()
                    recovery = {
                        "ok": False,
                        "trigger_reason": trigger_reason,
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                        "actions": list(recovery.get("actions") or []),
                    }
                    if incident_dir:
                        write_supervisor_crash(
                            incident_dir,
                            phase="incident_recovery",
                            crash_text=crash_text,
                        )
                    try:
                        print(crash_text, file=sys.stderr, flush=True)
                    except Exception:
                        pass
                finally:
                    if incident_dir:
                        write_recovery_result(incident_dir, recovery)
                        write_codex_result(incident_dir, codex_payload)
                        capture_post_recovery_bundle(
                            incident_dir=incident_dir,
                            vision=vision,
                            provider=provider,
                            playerlog_path=resolve_playerlog_path(status),
                        )
                if args.stop_after_incident:
                    return 0
                break
        finally:
            terminate_child(child)
        time.sleep(max(0.5, float(args.restart_delay_sec)))


def normalize_command(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        raw = raw[1:]
    if raw:
        return raw
    return [sys.executable, str(ROOT_DIR / "tools" / "run_bot_ui_path.py")]


def start_child(command: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["MTGA_SUPERVISOR_ACTIVE"] = "1"
    return subprocess.Popen(command, cwd=str(ROOT_DIR), env=env)


def terminate_child(child: subprocess.Popen | None) -> None:
    if child is None or child.poll() is not None:
        return
    try:
        child.terminate()
        child.wait(timeout=8)
        return
    except Exception:
        pass
    try:
        child.kill()
        child.wait(timeout=5)
    except Exception:
        pass


def should_skip_due_to_wait(status: dict) -> bool:
    until = float(status.get("intentional_wait_until_epoch") or 0.0)
    return until > time.time()


def should_skip_due_to_startup(
    status: dict,
    *,
    child_pid: int,
    child_started_at: float,
    startup_grace_sec: float,
) -> bool:
    now = time.time()
    if (now - float(child_started_at)) < max(1.0, float(startup_grace_sec)):
        try:
            status_pid = int(status.get("pid") or 0)
        except Exception:
            status_pid = 0
        if status_pid != int(child_pid):
            return True
        mode = str(status.get("mode") or "")
        if mode in {"starting", "ready"}:
            return True
    return False


def compute_stale_seconds(status: dict) -> float:
    refs = [
        float(status.get("last_playerlog_event_at_epoch") or 0.0),
        float(status.get("last_decision_at_epoch") or 0.0),
        float(status.get("last_input_at_epoch") or 0.0),
        float(status.get("started_at_epoch") or 0.0),
    ]
    latest = max(refs) if refs else 0.0
    if latest <= 0.0:
        return 0.0
    return max(0.0, time.time() - latest)


def has_local_priority(status: dict) -> bool:
    turn_info = status.get("turn_info")
    if not isinstance(turn_info, dict) or not turn_info:
        return False
    try:
        local_seat = int(status.get("local_system_seat_id") or 0)
    except Exception:
        local_seat = 0
    if local_seat <= 0:
        return False
    try:
        decision_player = int(turn_info.get("decisionPlayer") or 0)
    except Exception:
        decision_player = 0
    try:
        priority_player = int(turn_info.get("priorityPlayer") or 0)
    except Exception:
        priority_player = 0
    return decision_player == local_seat or priority_player == local_seat


def detect_stuck_reason(status: dict, args: argparse.Namespace) -> str | None:
    bot_state = str(status.get("bot_state") or "")
    mode = str(status.get("mode") or "")
    timer_type = str(status.get("my_timer_type") or "")
    try:
        critical_count = int(status.get("my_timer_critical_count") or 0)
    except Exception:
        critical_count = 0
    if (
        bot_state != str(BotState.HOME)
        and timer_type == "TimerType_Inactivity"
        and critical_count >= max(1, int(args.my_timer_critical_threshold))
    ):
        return "repeated_own_timer_critical"
    if bot_state != str(BotState.HOME) and bool(status.get("my_timer_timeout_seen")):
        return "own_timeout_observed"
    wait_active = should_skip_due_to_wait(status)
    if (
        bot_state == str(BotState.IN_GAME)
        and mode == "in_game"
        and bool(status.get("my_timer_running"))
        and str(status.get("my_timer_type") or "") == "TimerType_Inactivity"
    ):
        try:
            stall_threshold = max(5.0, float(args.my_timer_stall_sec))
        except Exception:
            stall_threshold = 20.0
        try:
            timer_elapsed = float(status.get("my_timer_elapsed_sec") or 0.0)
        except Exception:
            timer_elapsed = 0.0
        try:
            timer_remaining = float(status.get("my_timer_remaining_sec") or 0.0)
        except Exception:
            timer_remaining = 0.0
        try:
            last_input = float(status.get("last_input_at_epoch") or 0.0)
        except Exception:
            last_input = 0.0
        try:
            last_decision = float(status.get("last_decision_at_epoch") or 0.0)
        except Exception:
            last_decision = 0.0
        action_latest = max(last_input, last_decision)
        action_idle = max(0.0, time.time() - action_latest) if action_latest > 0.0 else 0.0
        if wait_active and timer_remaining > 8.0:
            return None
        if not has_local_priority(status):
            return None
        if timer_elapsed >= stall_threshold and action_idle >= stall_threshold:
            return "own_inactivity_timer_stalled"
    return None


def write_incident_bundle(
    *,
    status: dict,
    stale_for: float,
    vision: VisionEngine,
    provider: ArenaRegionProvider,
    reason: str,
) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    incident_dir = Path(ensure_debug_dir(f"incident-{stamp}"))
    playerlog_path = resolve_playerlog_path(status)
    state = get_state_from_playerlog(read_tail(playerlog_path, max_bytes=160000))
    detection = provider.detect(write_debug_on_fail=False)
    payload = {
        "reason": reason,
        "created_at": stamp,
        "stale_seconds": stale_for,
        "derived_playerlog_state": str(state),
        "status": status,
        "arena_detection": {
            "ok": detection.ok,
            "region": list(detection.region) if detection.region is not None else None,
            "code": detection.code,
            "message": detection.message,
            "matched_anchor": detection.matched_anchor,
            "diagnostics": detection.diagnostics or {},
        },
    }
    try:
        with (incident_dir / "incident.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass
    write_text(incident_dir / "bot_tail.txt", read_tail(resolve_bot_log_path(), max_bytes=160000))
    write_text(incident_dir / "player_tail.txt", read_tail(playerlog_path, max_bytes=160000))
    write_text(incident_dir / "status_path.txt", get_status_path())
    try:
        tracking = ensure_tracking_file(
            incident_dir,
            created_at=stamp,
            trigger=reason,
        )
        with (incident_dir / "related_incidents.json").open("w", encoding="utf-8") as handle:
            json.dump(
                build_related_incidents_payload(
                    incident_dir=incident_dir,
                    created_at=stamp,
                    trigger=reason,
                ),
                handle,
                indent=2,
            )
        with (incident_dir / "signature_knowledge.json").open("w", encoding="utf-8") as handle:
            json.dump(
                build_signature_knowledge_payload(
                    incident_dir=incident_dir,
                    created_at=stamp,
                    trigger=reason,
                ),
                handle,
                indent=2,
            )
    except Exception:
        pass

    try:
        vision.begin_tick()
        full = vision.capture(None)
        vision.save_image(full, str(incident_dir / "full_screen.png"))
        if detection.region is not None:
            arena = vision.capture(detection.region)
            vision.save_image(arena, str(incident_dir / "arena_region.png"))
    except Exception:
        pass
    return str(incident_dir)


def capture_post_recovery_bundle(
    *,
    incident_dir: str,
    vision: VisionEngine,
    provider: ArenaRegionProvider,
    playerlog_path: str,
) -> None:
    try:
        payload = {
            "derived_playerlog_state": str(get_state_from_playerlog(read_tail(playerlog_path, max_bytes=160000))),
        }
        detection = provider.detect(write_debug_on_fail=False)
        payload["arena_detection"] = {
            "ok": detection.ok,
            "region": list(detection.region) if detection.region is not None else None,
            "code": detection.code,
            "message": detection.message,
            "matched_anchor": detection.matched_anchor,
        }
        with (Path(incident_dir) / "post_recovery_state.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass
    try:
        vision.begin_tick()
        full = vision.capture(None)
        vision.save_image(full, str(Path(incident_dir) / "post_recovery_full_screen.png"))
        arena = resolve_mtga_region(provider)
        if arena is not None:
            arena_img = vision.capture(arena)
            vision.save_image(arena_img, str(Path(incident_dir) / "post_recovery_arena_region.png"))
    except Exception:
        pass


def attempt_recovery(
    *,
    status: dict,
    incident_dir: str,
    trigger_reason: str,
    input_controller,
    vision: VisionEngine,
    provider: ArenaRegionProvider,
    mtga_launch_cmd: str,
    mtga_process_names: list[str],
    concede_rel: tuple[int, int],
) -> dict:
    playerlog_path = resolve_playerlog_path(status)
    state = get_state_from_playerlog(read_tail(playerlog_path, max_bytes=160000))
    result = {
        "ok": False,
        "initial_state": str(state),
        "trigger_reason": trigger_reason,
        "actions": [],
    }

    if trigger_reason in {"repeated_own_timer_critical", "own_inactivity_timer_stalled", "own_timeout_observed"} and state == BotState.IN_GAME:
        if concede_to_home(
            input_controller=input_controller,
            vision=vision,
            provider=provider,
            playerlog_path=playerlog_path,
            incident_dir=incident_dir,
            concede_rel=concede_rel,
            result=result,
        ):
            result["ok"] = True
            result["final_state"] = str(get_state_from_playerlog(read_tail(playerlog_path, max_bytes=160000)))
            return result

    if recover_to_home(
        input_controller=input_controller,
        vision=vision,
        provider=provider,
        playerlog_path=playerlog_path,
        result=result,
    ):
        result["ok"] = True
        result["final_state"] = str(get_state_from_playerlog(read_tail(playerlog_path, max_bytes=160000)))
        return result

    if mtga_launch_cmd:
        result["actions"].append("client_restart")
        restart_mtga(mtga_launch_cmd=mtga_launch_cmd, mtga_process_names=mtga_process_names)
        time.sleep(20.0)
        if recover_to_home(
            input_controller=input_controller,
            vision=vision,
            provider=provider,
            playerlog_path=playerlog_path,
            result=result,
        ):
            result["ok"] = True
    result["final_state"] = str(get_state_from_playerlog(read_tail(playerlog_path, max_bytes=160000)))
    result["incident_dir"] = incident_dir
    return result


def recover_to_home(*, input_controller, vision: VisionEngine, provider: ArenaRegionProvider, playerlog_path: str, result: dict) -> bool:
    if is_home_visible(vision=vision, provider=provider):
        result["actions"].append("already_home")
        return True

    for _ in range(8):
        if focus_mtga_window():
            result["actions"].append("focus_mtga_window")
            time.sleep(0.3)
        tail = read_tail(playerlog_path, max_bytes=160000)
        state = get_state_from_playerlog(tail)
        result["actions"].append(f"state:{state}")
        if state == BotState.HOME:
            return True
        if looks_like_match_end(tail):
            result["actions"].append("match_end_detected")
            if dismiss_match_end_screen(input_controller=input_controller, provider=provider, result=result):
                time.sleep(2.0)
                if is_home_visible(vision=vision, provider=provider):
                    result["actions"].append("home_after_match_end_dismiss")
                    return True
                continue
        input_controller.tap_escape()
        result["actions"].append("tap_escape")
        time.sleep(1.2)
        if is_home_visible(vision=vision, provider=provider):
            result["actions"].append("home_anchor_visible")
            return True
    return False


def concede_to_home(
    *,
    input_controller,
    vision: VisionEngine,
    provider: ArenaRegionProvider,
    playerlog_path: str,
    incident_dir: str,
    concede_rel: tuple[int, int],
    result: dict,
) -> bool:
    if focus_mtga_window():
        result["actions"].append("focus_mtga_window")
        time.sleep(0.3)
    arena = resolve_mtga_region(provider)
    if arena is None:
        result["actions"].append("concede_skipped_no_arena")
        return False

    concede_abs = (int(arena[0] + concede_rel[0]), int(arena[1] + concede_rel[1]))
    concede_region = build_focus_region(
        center=concede_abs,
        bounds=arena,
        width=760,
        height=360,
    )
    concede_template = str(ROOT_DIR / "Buttons" / "concede.png")
    concede_match = None
    for attempt in range(1, 4):
        if attempt == 1:
            result["actions"].append("concede_tap_escape")
        else:
            if focus_mtga_window():
                result["actions"].append(f"focus_mtga_window_retry_{attempt}")
                time.sleep(0.2)
            result["actions"].append(f"concede_tap_escape_retry_{attempt}")
        input_controller.tap_escape()
        time.sleep(0.9)
        capture_concede_debug(
            incident_dir=incident_dir,
            vision=vision,
            arena=arena,
            focus_region=concede_region,
            stage=f"after_escape_attempt_{attempt}",
            extra={
                "concede_abs": [int(concede_abs[0]), int(concede_abs[1])],
                "concede_region": [int(v) for v in concede_region],
            },
        )
        concede_match = find_template_match_in_region(
            vision=vision,
            template_path=concede_template,
            region=arena,
            threshold=0.72,
        )
        if concede_match is None:
            concede_match = find_template_match_in_region(
                vision=vision,
                template_path=concede_template,
                region=concede_region,
                threshold=0.60,
            )
        if concede_match is not None:
            break
        result["actions"].append(f"concede_template_not_found_attempt_{attempt}")

    if concede_match is not None:
        click_low_level(input_controller, concede_match["point"])
        result["actions"].append(
            f"concede_template_click:{concede_match['point'][0]},{concede_match['point'][1]} score={concede_match['score']:.3f}"
        )
        time.sleep(0.3)
        click_low_level(input_controller, concede_match["point"])
        result["actions"].append("concede_template_click_retry")
    else:
        result["actions"].append("concede_menu_not_visible_or_template_missing")
        capture_concede_debug(
            incident_dir=incident_dir,
            vision=vision,
            arena=arena,
            focus_region=concede_region,
            stage="template_not_found",
            extra={
                "concede_abs": [int(concede_abs[0]), int(concede_abs[1])],
                "concede_region": [int(v) for v in concede_region],
            },
        )
        return recover_to_home(
            input_controller=input_controller,
            vision=vision,
            provider=provider,
            playerlog_path=playerlog_path,
            result=result,
        )
    time.sleep(1.2)

    if click_template_in_region(
        input_controller=input_controller,
        vision=vision,
        template_path=str(ROOT_DIR / "Buttons" / "okay_btn.png"),
        region=(int(arena[0] + 700), int(arena[1] + 360), 520, 360),
        threshold=0.84,
    ):
        result["actions"].append("concede_confirm_okay")
        time.sleep(1.0)

    deadline = time.time() + 18.0
    while time.time() < deadline:
        tail = read_tail(playerlog_path, max_bytes=160000)
        state = get_state_from_playerlog(tail)
        result["actions"].append(f"concede_state:{state}")
        if looks_like_match_end(tail):
            result["actions"].append("concede_match_end_detected")
            break
        if state != BotState.IN_GAME:
            break
        time.sleep(0.6)

    capture_concede_debug(
        incident_dir=incident_dir,
        vision=vision,
        arena=arena,
        focus_region=concede_region,
        stage="post_concede_wait",
        extra={
            "concede_abs": [int(concede_abs[0]), int(concede_abs[1])],
            "concede_region": [int(v) for v in concede_region],
        },
    )
    return recover_to_home(
        input_controller=input_controller,
        vision=vision,
        provider=provider,
        playerlog_path=playerlog_path,
        result=result,
    )


def is_home_visible(*, vision: VisionEngine, provider: ArenaRegionProvider) -> bool:
    arena = provider.reacquire()
    if arena is None:
        return False
    template = str(ROOT_DIR / "assets" / "assert" / "home_anchor.png")
    roi = (int(arena[0] + 20), int(arena[1] + 20), 380, 160)
    vision.begin_tick()
    return vision.assert_template(roi, template, threshold=0.78)


def restart_mtga(*, mtga_launch_cmd: str, mtga_process_names: list[str]) -> None:
    if os.name == "nt":
        for name in mtga_process_names:
            try:
                subprocess.run(
                    ["taskkill", "/IM", name, "/F"],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=15,
                )
            except Exception:
                pass
    if mtga_launch_cmd:
        subprocess.Popen(mtga_launch_cmd, cwd=str(ROOT_DIR), shell=True)


def click_low_level(input_controller, point: tuple[int, int]) -> None:
    input_controller.move_abs(int(point[0]), int(point[1]))
    time.sleep(0.08)
    input_controller.left_down()
    time.sleep(0.06)
    input_controller.left_up()


def click_template_in_region(
    *,
    input_controller,
    vision: VisionEngine,
    template_path: str,
    region: tuple[int, int, int, int],
    threshold: float,
) -> bool:
    if not os.path.isfile(template_path):
        return False
    try:
        vision.begin_tick()
        image = vision.capture(region)
        if image is None:
            return False
        match = vision.find_template(image, template_path, threshold=threshold)
        if match is None:
            return False
        point = (int(region[0] + match.x), int(region[1] + match.y))
        click_low_level(input_controller, point)
        return True
    except Exception:
        return False


def build_focus_region(
    *,
    center: tuple[int, int],
    bounds: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    bx, by, bw, bh = (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
    cx, cy = (int(center[0]), int(center[1]))
    x = max(bx, min(cx - (width // 2), bx + bw - width))
    y = max(by, min(cy - (height // 2), by + bh - height))
    return (int(x), int(y), int(min(width, bw)), int(min(height, bh)))


def capture_concede_debug(
    *,
    incident_dir: str,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    focus_region: tuple[int, int, int, int],
    stage: str,
    extra: dict | None = None,
) -> None:
    payload = {
        "stage": str(stage),
        "arena": [int(v) for v in arena],
        "focus_region": [int(v) for v in focus_region],
    }
    if extra:
        payload.update(extra)
    try:
        with (Path(incident_dir) / f"concede_debug_{stage}.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass
    try:
        vision.begin_tick()
        full = vision.capture(None)
        if full is not None:
            vision.save_image(full, str(Path(incident_dir) / f"concede_debug_{stage}_full.png"))
        arena_img = vision.capture(arena)
        if arena_img is not None:
            vision.save_image(arena_img, str(Path(incident_dir) / f"concede_debug_{stage}_arena.png"))
        focus_img = vision.capture(focus_region)
        if focus_img is not None:
            vision.save_image(focus_img, str(Path(incident_dir) / f"concede_debug_{stage}_focus.png"))
    except Exception:
        pass


def resolve_mtga_region(provider: ArenaRegionProvider) -> tuple[int, int, int, int] | None:
    region = provider.reacquire()
    if region is not None:
        return region
    try:
        detection = provider.detect(write_debug_on_fail=False)
        if detection.region is not None:
            return tuple(int(v) for v in detection.region)
    except Exception:
        return None
    return None


def dismiss_match_end_screen(*, input_controller, provider: ArenaRegionProvider, result: dict) -> bool:
    if focus_mtga_window():
        result["actions"].append("focus_mtga_window")
        time.sleep(0.3)
    arena = resolve_mtga_region(provider)
    if arena is None:
        result["actions"].append("dismiss_match_end_no_arena")
        return False
    center = (int(arena[0] + (arena[2] // 2)), int(arena[1] + (arena[3] // 2)))
    click_low_level(input_controller, center)
    time.sleep(0.25)
    click_low_level(input_controller, center)
    result["actions"].append(f"dismiss_match_end:{center[0]},{center[1]}")
    return True


def looks_like_match_end(log_tail: str) -> bool:
    text = str(log_tail or "").lower()
    if not text:
        return False
    markers = (
        "onsceneloaded for matchendscene",
        "matchgameroomstatetype_matchcompleted",
        "matchstate_gamecomplete",
        "matchstate_matchcomplete",
        "resultreason_concede",
        "resultreason_timeout",
        "gremessagetype_intermissionreq",
    )
    return any(marker in text for marker in markers)


def find_template_match_in_region(
    *,
    vision: VisionEngine,
    template_path: str,
    region: tuple[int, int, int, int],
    threshold: float,
) -> dict | None:
    if not os.path.isfile(template_path):
        return None
    try:
        vision.begin_tick()
        image = vision.capture(region)
        if image is None:
            return None
        match = vision.find_template(image, template_path, threshold=threshold)
        if match is None:
            return None
        return {
            "point": (int(region[0] + match.x), int(region[1] + match.y)),
            "score": float(match.score),
        }
    except Exception:
        return None


def notify_codex(*, input_controller, vision: VisionEngine, template_path: str, debug_dir: str | None = None) -> dict:
    result = {"ok": False, "template_path": template_path}
    if not template_path or not os.path.isfile(template_path):
        result["reason"] = "missing_template"
        return result
    try:
        vision.begin_tick()
        full = vision.capture(None)
        if debug_dir and full is not None:
            vision.save_image(full, str(Path(debug_dir) / "codex_notify_before.png"))
        match = vision.find_template(full, template_path, threshold=0.80) if full is not None else None
        if match is None:
            result["reason"] = "template_not_found"
            return result
        target_x = int(match.x)
        target_y = int(match.y)
        input_controller.move_abs(target_x, target_y)
        time.sleep(0.10)
        input_controller.left_down()
        time.sleep(0.07)
        input_controller.left_up()
        time.sleep(0.18)
        # A second click is more reliable for the Codex desktop chat field, which
        # sometimes highlights visually before the caret is actually ready for Enter.
        input_controller.left_down()
        time.sleep(0.06)
        input_controller.left_up()
        time.sleep(0.25)
        input_controller.type_text("stuck")
        time.sleep(0.25)
        input_controller.tap_enter()
        time.sleep(0.35)
        # Best-effort retry: the first Enter can be swallowed if focus settles late.
        input_controller.tap_enter()
        time.sleep(0.15)
        vision.begin_tick()
        after = vision.capture(None)
        if debug_dir and after is not None:
            vision.save_image(after, str(Path(debug_dir) / "codex_notify_after.png"))
        result.update(
            {
                "ok": True,
                "x": target_x,
                "y": target_y,
                "score": float(match.score),
                "enter_attempts": 2,
                "verification": "best_effort_no_visual_confirmation",
            }
        )
        return result
    except Exception as exc:
        result["reason"] = str(exc)
        return result


def resolve_playerlog_path(status: dict) -> str:
    candidate = str(status.get("log_path") or "").strip()
    if candidate and os.path.isfile(candidate):
        return candidate
    env_path = str(os.environ.get("MTGA_BOT_LOG_PATH", "")).strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    home = Path.home()
    return str(home / "AppData" / "LocalLow" / "Wizards Of The Coast" / "MTGA" / "Player.log")


def resolve_bot_log_path() -> str:
    return str(runtime_file("logs", "bot.log"))


def read_tail(path: str, *, max_bytes: int) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def write_text(path: Path, content: str) -> None:
    try:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content or "")
    except Exception:
        pass


def write_recovery_result(incident_dir: str, recovery: dict) -> None:
    path = Path(incident_dir) / "recovery.json"
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(recovery, handle, indent=2)
    except Exception:
        pass


def write_codex_result(incident_dir: str, payload: dict) -> None:
    path = Path(incident_dir) / "codex_notify.json"
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass


def write_supervisor_crash(incident_dir: str, *, phase: str, crash_text: str) -> None:
    payload = {
        "phase": phase,
        "created_at": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "traceback": str(crash_text or ""),
    }
    path = Path(incident_dir) / "supervisor_crash.json"
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass


def parse_process_names(raw: str) -> list[str]:
    values = []
    for item in str(raw or "").split(","):
        name = item.strip()
        if name:
            values.append(name)
    return values


if __name__ == "__main__":
    raise SystemExit(main())
