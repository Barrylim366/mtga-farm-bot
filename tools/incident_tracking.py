from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from runtime_paths import runtime_file

REGISTRY_PATH = runtime_file("incident_registry.json")
LEGACY_REGISTRY_PATH = ROOT_DIR / "incident_registry.json"
VALID_STATUSES = {
    "proposed",
    "applied",
    "survived_n_runs",
    "reproduced_and_passed",
    "regressed",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _incident_id_from_dir(incident_dir: Path) -> str:
    return incident_dir.name


def default_tracking_payload(
    *,
    incident_id: str,
    created_at: str,
    trigger: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "incident_id": incident_id,
        "created_at": created_at,
        "trigger": trigger,
        "suggested_signature": "",
        "signature_basis": [],
        "signature": "",
        "status": "",
        "confidence": None,
        "runs_since_applied": 0,
        "reproduced_and_passed_count": 0,
        "regression_count": 0,
        "evidence": [],
        "notes": "",
        "signature_knowledge": default_signature_knowledge(),
        "updated_at": _utc_now_iso(),
    }


def default_signature_knowledge() -> dict[str, Any]:
    return {
        "hypothesis": "",
        "applied_fix": "",
        "next_debug_action": "",
        "source_incident": "",
        "last_reviewed_at": "",
    }


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_")


def _compact_enum(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("Phase_", "").replace("Step_", "").replace("ClientMessageType_", "")
    return _slugify(text)


def _append_component(components: list[str], basis: list[str], component: str, reason: str) -> None:
    token = _slugify(component)
    if not token:
        return
    if token not in components:
        components.append(token)
        basis.append(reason)


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    haystack = text or ""
    return any(pattern in haystack for pattern in patterns)


def suggest_signature_for_incident(
    incident_dir: Path,
    *,
    created_at: str = "",
    trigger: str = "",
) -> tuple[str, list[str]]:
    incident_payload = _read_json(incident_dir / "incident.json", {})
    if not isinstance(incident_payload, dict):
        incident_payload = {}
    status = incident_payload.get("status")
    if not isinstance(status, dict):
        status = {}
    turn_info = status.get("turn_info")
    if not isinstance(turn_info, dict):
        turn_info = {}
    bot_tail = _read_text(incident_dir / "bot_tail.txt")
    player_tail = _read_text(incident_dir / "player_tail.txt")
    combined_tail = "\n".join(part for part in (bot_tail, player_tail) if part)

    reason_value = str(incident_payload.get("reason") or trigger or "").strip()
    components: list[str] = []
    basis: list[str] = []

    reason_map = {
        "own_inactivity_timer_stalled": "own_inactivity",
        "repeated_own_timer_critical": "own_timer_critical",
        "own_timeout_observed": "own_timeout",
        "supervisor_stuck_timeout": "supervisor_stuck",
    }
    _append_component(
        components,
        basis,
        reason_map.get(reason_value, _slugify(reason_value or "incident")),
        f"trigger={reason_value or 'unknown'}",
    )

    wait_reason = str(status.get("intentional_wait_reason") or "").strip()
    wait_map = {
        "decision_delay_wait": "decision_delay",
        "target_selection_wait": "target_selection",
        "stack_resolution_wait": "stack_resolution",
        "mulligan_wait": "mulligan",
        "pending_message_wait": "pending_message",
        "pay_costs_wait": "pay_costs",
        "post_match_delay": "post_match",
        "logout_transition_wait": "logout_transition",
    }
    if wait_reason:
        _append_component(
            components,
            basis,
            wait_map.get(wait_reason, _slugify(wait_reason)),
            f"intentional_wait_reason={wait_reason}",
        )

    phase_token = _compact_enum(turn_info.get("phase"))
    step_token = _compact_enum(turn_info.get("step"))
    if phase_token and step_token:
        _append_component(
            components,
            basis,
            f"{phase_token}_{step_token}",
            f"turn_info={turn_info.get('phase')} / {turn_info.get('step')}",
        )
    elif phase_token:
        _append_component(
            components,
            basis,
            phase_token,
            f"turn_info={turn_info.get('phase')}",
        )

    pattern_hints = [
        (
            "decision_delay_wait_dropped",
            ("Decision delay already armed for current priority window",),
        ),
        (
            "decision_delay_low_rope",
            ("Decision delay override:", "Decision delay bypassed:", "Decision delay clamped:"),
        ),
        (
            "stale_target_wait",
            ("Pausing decision while target selection is pending", "Target selection auto-clear"),
        ),
        (
            "safe_stack_pass",
            ("Stack present but safe to resolve",),
        ),
        (
            "stack_wait",
            ("Deferring decision: stack has",),
        ),
        (
            "select_n_failed",
            ("SelectN failed to select any cards",),
        ),
        (
            "select_n_ids_not_in_hand",
            ("ids not in hand",),
        ),
        (
            "select_n_in_progress",
            ("SelectN in progress: pausing other decisions.",),
        ),
        (
            "premature_mulligan_keep",
            ("Local MulliganReq observed: clearing premature keep state.",),
        ),
        (
            "stale_mulligan_wait",
            ("Mulligan decision already armed; waiting for callback.", "Skipping delayed mulligan"),
        ),
        (
            "logout_home_stall",
            ("logout did not reach the login screen",),
        ),
        (
            "home_state_stale",
            ("MainNav load in",),
        ),
    ]
    dominant_pattern = ""
    for token, patterns in pattern_hints:
        if _match_any(combined_tail, patterns):
            dominant_pattern = token
            _append_component(components, basis, token, f"log_pattern={patterns[0]}")
            break

    prompt_hints = [
        ("select_n", ("SelectNReq", "SelectN ", "SelectN aborted", "SelectN failed")),
        (
            "target_selection",
            (
                "SelectTargetsReq",
                "PlayerSelectingTargets",
                "Pausing decision while target selection is pending",
            ),
        ),
        ("declare_attackers", ("DeclareAttackersReq", "Step_DeclareAttack")),
        ("declare_blockers", ("DeclareBlockersReq", "Step_DeclareBlock", "DeclareBlock")),
        ("mulligan", ("MulliganReq", "MulliganResp", "mulligan")),
        ("pay_costs", ("PayCostsReq", "pay costs")),
        ("logout", ("logout did not reach the login screen",)),
        ("main_nav", ("MainNav load in", "home_ready")),
    ]
    for token, patterns in prompt_hints:
        if token == "target_selection" and dominant_pattern == "decision_delay_wait_dropped":
            continue
        if _match_any(combined_tail, patterns):
            _append_component(components, basis, token, f"log_pattern={' / '.join(patterns[:2])}")
            break

    if not components:
        _append_component(
            components,
            basis,
            _slugify(trigger or created_at or incident_dir.name or "incident"),
            "fallback=incident_id_or_trigger",
        )

    return ("_".join(components), basis)


def ensure_tracking_file(incident_dir: Path, *, created_at: str = "", trigger: str = "") -> dict[str, Any]:
    tracking_path = incident_dir / "tracking.json"
    payload = _read_json(tracking_path, None)
    suggested_signature, signature_basis = suggest_signature_for_incident(
        incident_dir,
        created_at=str(created_at or ""),
        trigger=str(trigger or ""),
    )
    if isinstance(payload, dict):
        payload.setdefault("schema_version", 1)
        payload.setdefault("incident_id", _incident_id_from_dir(incident_dir))
        payload.setdefault("created_at", str(created_at or payload.get("created_at") or ""))
        payload.setdefault("trigger", str(trigger or payload.get("trigger") or ""))
        payload["signature_knowledge"] = _normalize_signature_knowledge(
            payload.get("signature_knowledge"),
            source_incident=str(payload.get("incident_id") or incident_dir.name),
            touch_review_time=False,
        )
        payload["suggested_signature"] = suggested_signature
        payload["signature_basis"] = signature_basis
        payload["updated_at"] = _utc_now_iso()
        _write_json(tracking_path, payload)
        return payload
    incident_id = _incident_id_from_dir(incident_dir)
    payload = default_tracking_payload(
        incident_id=incident_id,
        created_at=str(created_at or incident_id.removeprefix("incident-")),
        trigger=str(trigger or ""),
    )
    payload["suggested_signature"] = suggested_signature
    payload["signature_basis"] = signature_basis
    _write_json(tracking_path, payload)
    return payload


def _load_registry() -> dict[str, Any]:
    runtime_data = _read_json(REGISTRY_PATH, {"schema_version": 1, "signatures": {}})
    legacy_data = _read_json(LEGACY_REGISTRY_PATH, {"schema_version": 1, "signatures": {}})
    data = _merge_registry_data(runtime_data, legacy_data)
    if LEGACY_REGISTRY_PATH.is_file():
        runtime_signatures = data.get("signatures") or {}
        if isinstance(runtime_signatures, dict) and runtime_signatures:
            _save_registry(data)
    return data


def _merge_registry_data(runtime_data: Any, legacy_data: Any) -> dict[str, Any]:
    runtime_registry = _normalize_registry_payload(runtime_data)
    legacy_registry = _normalize_registry_payload(legacy_data)
    merged = {
        "schema_version": max(
            int(runtime_registry.get("schema_version") or 1),
            int(legacy_registry.get("schema_version") or 1),
        ),
        "signatures": dict(legacy_registry.get("signatures") or {}),
    }
    merged_signatures = merged["signatures"]
    for key, value in (runtime_registry.get("signatures") or {}).items():
        merged_signatures[str(key)] = value
    return merged


def _normalize_registry_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {"schema_version": 1, "signatures": {}}
    data.setdefault("schema_version", 1)
    data.setdefault("signatures", {})
    signatures = data.get("signatures") or {}
    normalized_signatures: dict[str, Any] = {}
    if isinstance(signatures, dict):
        for key, value in list(signatures.items()):
            if not isinstance(value, dict):
                normalized_signatures[str(key)] = {
                    "signature": str(key),
                    "times_seen": 0,
                    "runs_since_applied": 0,
                    "reproduced_and_passed_count": 0,
                    "regression_count": 0,
                    "current_status": "",
                    "confidence": None,
                    "latest_incident": "",
                    "evidence": [],
                    "signature_knowledge": default_signature_knowledge(),
                }
                continue
            value["signature_knowledge"] = _normalize_signature_knowledge(
                value.get("signature_knowledge"),
                source_incident=str(value.get("latest_incident") or ""),
                touch_review_time=False,
            )
            normalized_signatures[str(key)] = value
    data["signatures"] = normalized_signatures
    return data


def _save_registry(registry: dict[str, Any]) -> None:
    _write_json(REGISTRY_PATH, registry)


def _compact_evidence(items: list[Any], *, limit: int = 3) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in list(items or [])[-max(0, int(limit)):]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "at": str(item.get("at") or ""),
                "text": str(item.get("text") or ""),
            }
        )
    return compact


def _normalize_signature_knowledge(
    payload: Any,
    *,
    source_incident: str = "",
    touch_review_time: bool = False,
) -> dict[str, Any]:
    knowledge = default_signature_knowledge()
    if isinstance(payload, dict):
        for key in knowledge:
            knowledge[key] = str(payload.get(key) or "").strip()
    if source_incident.strip() and not str(knowledge.get("source_incident") or "").strip():
        knowledge["source_incident"] = source_incident.strip()
    if touch_review_time and any(knowledge.get(key) for key in ("hypothesis", "applied_fix", "next_debug_action")):
        knowledge["last_reviewed_at"] = _utc_now_iso()
    return knowledge


def _merge_signature_knowledge(
    existing: Any,
    *,
    hypothesis: str = "",
    applied_fix: str = "",
    next_debug_action: str = "",
    source_incident: str = "",
    touch_review_time: bool = False,
) -> dict[str, Any]:
    knowledge = _normalize_signature_knowledge(
        existing,
        source_incident=source_incident,
        touch_review_time=False,
    )
    updates = {
        "hypothesis": str(hypothesis or "").strip(),
        "applied_fix": str(applied_fix or "").strip(),
        "next_debug_action": str(next_debug_action or "").strip(),
    }
    changed = False
    for key, value in updates.items():
        if value:
            if knowledge.get(key) != value:
                changed = True
            knowledge[key] = value
    if changed and source_incident.strip() and knowledge.get("source_incident") != source_incident.strip():
        knowledge["source_incident"] = source_incident.strip()
    if touch_review_time and changed and any(knowledge.get(key) for key in ("hypothesis", "applied_fix", "next_debug_action")):
        knowledge["last_reviewed_at"] = _utc_now_iso()
    return knowledge


def build_related_incidents_payload(
    *,
    incident_dir: Path,
    created_at: str = "",
    trigger: str = "",
) -> dict[str, Any]:
    tracking = ensure_tracking_file(
        incident_dir,
        created_at=str(created_at or ""),
        trigger=str(trigger or ""),
    )
    registry = _load_registry()
    signatures = registry.get("signatures") or {}
    suggested_signature = str(tracking.get("suggested_signature") or "").strip()
    current_signature = str(tracking.get("signature") or "").strip()

    payload: dict[str, Any] = {
        "incident_id": str(tracking.get("incident_id") or incident_dir.name),
        "trigger": str(tracking.get("trigger") or trigger or ""),
        "suggested_signature": suggested_signature,
        "signature_basis": list(tracking.get("signature_basis") or []),
        "current_signature": current_signature,
        "has_known_match": False,
        "matching_signature": "",
        "matching_record": {},
        "known_guidance": default_signature_knowledge(),
    }

    match_signature = ""
    if current_signature and isinstance(signatures.get(current_signature), dict):
        match_signature = current_signature
    elif suggested_signature and isinstance(signatures.get(suggested_signature), dict):
        match_signature = suggested_signature

    if not match_signature:
        return payload

    record = signatures.get(match_signature) or {}
    if not isinstance(record, dict):
        return payload

    payload["has_known_match"] = True
    payload["matching_signature"] = match_signature
    payload["matching_record"] = {
        "signature": match_signature,
        "times_seen": int(record.get("times_seen") or 0),
        "current_status": str(record.get("current_status") or ""),
        "confidence": _normalize_confidence(record.get("confidence")),
        "first_seen_incident": str(record.get("first_seen_incident") or ""),
        "latest_incident": str(record.get("latest_incident") or ""),
        "runs_since_applied": int(record.get("runs_since_applied") or 0),
        "reproduced_and_passed_count": int(record.get("reproduced_and_passed_count") or 0),
        "regression_count": int(record.get("regression_count") or 0),
        "last_updated_at": str(record.get("last_updated_at") or ""),
        "recent_evidence": _compact_evidence(list(record.get("evidence") or [])),
    }
    payload["known_guidance"] = _normalize_signature_knowledge(
        record.get("signature_knowledge"),
        source_incident=str(record.get("latest_incident") or ""),
        touch_review_time=False,
    )
    return payload


def build_signature_knowledge_payload(
    *,
    incident_dir: Path,
    created_at: str = "",
    trigger: str = "",
) -> dict[str, Any]:
    related = build_related_incidents_payload(
        incident_dir=incident_dir,
        created_at=created_at,
        trigger=trigger,
    )
    return {
        "incident_id": str(related.get("incident_id") or incident_dir.name),
        "trigger": str(related.get("trigger") or ""),
        "suggested_signature": str(related.get("suggested_signature") or ""),
        "current_signature": str(related.get("current_signature") or ""),
        "matching_signature": str(related.get("matching_signature") or ""),
        "has_known_match": bool(related.get("has_known_match")),
        "known_guidance": _normalize_signature_knowledge(
            related.get("known_guidance"),
            source_incident="",
            touch_review_time=False,
        ),
    }


def _append_evidence(existing: list[Any], items: list[str]) -> list[Any]:
    merged = list(existing or [])
    now = _utc_now_iso()
    for text in items:
        text_value = str(text or "").strip()
        if not text_value:
            continue
        merged.append({"at": now, "text": text_value})
    return merged


def _normalize_confidence(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def update_incident_tracking(
    *,
    incident_dir: Path,
    signature: str,
    status: str,
    confidence: float | None,
    evidence: list[str],
    notes: str = "",
    runs_since_applied: int | None = None,
    hypothesis: str = "",
    applied_fix: str = "",
    next_debug_action: str = "",
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    tracking = ensure_tracking_file(incident_dir)
    tracking["signature"] = str(signature or "").strip()
    tracking["status"] = status
    tracking["confidence"] = _normalize_confidence(confidence)
    tracking["updated_at"] = _utc_now_iso()
    tracking["notes"] = str(notes or tracking.get("notes") or "")
    tracking["evidence"] = _append_evidence(list(tracking.get("evidence") or []), evidence)
    tracking["signature_knowledge"] = _merge_signature_knowledge(
        tracking.get("signature_knowledge"),
        hypothesis=hypothesis,
        applied_fix=applied_fix,
        next_debug_action=next_debug_action,
        source_incident=str(tracking.get("incident_id") or incident_dir.name),
        touch_review_time=bool(hypothesis or applied_fix or next_debug_action),
    )
    if runs_since_applied is not None:
        tracking["runs_since_applied"] = max(0, int(runs_since_applied))
    if status == "reproduced_and_passed":
        tracking["reproduced_and_passed_count"] = int(tracking.get("reproduced_and_passed_count") or 0) + 1
    if status == "regressed":
        tracking["regression_count"] = int(tracking.get("regression_count") or 0) + 1
    _write_json(incident_dir / "tracking.json", tracking)

    registry = _load_registry()
    signatures = registry.setdefault("signatures", {})
    record = signatures.get(signature)
    if not isinstance(record, dict):
        record = {
            "signature": signature,
            "first_seen_incident": tracking["incident_id"],
            "first_seen_at": tracking.get("created_at") or tracking["updated_at"],
            "times_seen": 0,
            "runs_since_applied": 0,
            "reproduced_and_passed_count": 0,
            "regression_count": 0,
            "current_status": "",
            "confidence": None,
            "latest_incident": "",
            "evidence": [],
            "signature_knowledge": default_signature_knowledge(),
        }
    record["times_seen"] = int(record.get("times_seen") or 0) + 1
    record["latest_incident"] = tracking["incident_id"]
    record["current_status"] = status
    record["confidence"] = tracking["confidence"]
    record["last_updated_at"] = tracking["updated_at"]
    record["evidence"] = _append_evidence(list(record.get("evidence") or []), evidence)
    record["signature_knowledge"] = _merge_signature_knowledge(
        record.get("signature_knowledge"),
        hypothesis=hypothesis,
        applied_fix=applied_fix,
        next_debug_action=next_debug_action,
        source_incident=str(tracking.get("incident_id") or incident_dir.name),
        touch_review_time=bool(hypothesis or applied_fix or next_debug_action),
    )
    if runs_since_applied is not None:
        record["runs_since_applied"] = max(0, int(runs_since_applied))
    if status == "reproduced_and_passed":
        record["reproduced_and_passed_count"] = int(record.get("reproduced_and_passed_count") or 0) + 1
    if status == "regressed":
        record["regression_count"] = int(record.get("regression_count") or 0) + 1
    signatures[signature] = record
    _save_registry(registry)
    return tracking


def bump_signature_runs(*, signature: str, runs: int, evidence: list[str]) -> dict[str, Any]:
    if not signature.strip():
        raise ValueError("signature is required")
    registry = _load_registry()
    signatures = registry.setdefault("signatures", {})
    record = signatures.get(signature)
    if not isinstance(record, dict):
        raise ValueError(f"Unknown signature: {signature}")
    new_runs = max(0, int(record.get("runs_since_applied") or 0)) + max(0, int(runs))
    record["runs_since_applied"] = new_runs
    if new_runs > 0:
        record["current_status"] = "survived_n_runs"
    current_confidence = _normalize_confidence(record.get("confidence"))
    if current_confidence is None:
        current_confidence = 0.3
    if new_runs >= 10:
        current_confidence = max(current_confidence, 0.6)
    record["confidence"] = current_confidence
    record["last_updated_at"] = _utc_now_iso()
    record["evidence"] = _append_evidence(list(record.get("evidence") or []), evidence)
    signatures[signature] = record
    _save_registry(registry)
    return record


def update_signature_guidance(
    *,
    signature: str,
    hypothesis: str = "",
    applied_fix: str = "",
    next_debug_action: str = "",
    source_incident: str = "",
) -> dict[str, Any]:
    if not signature.strip():
        raise ValueError("signature is required")
    registry = _load_registry()
    signatures = registry.setdefault("signatures", {})
    record = signatures.get(signature)
    if not isinstance(record, dict):
        raise ValueError(f"Unknown signature: {signature}")
    record["signature_knowledge"] = _merge_signature_knowledge(
        record.get("signature_knowledge"),
        hypothesis=hypothesis,
        applied_fix=applied_fix,
        next_debug_action=next_debug_action,
        source_incident=source_incident,
        touch_review_time=True,
    )
    record["last_updated_at"] = _utc_now_iso()
    signatures[signature] = record
    _save_registry(registry)
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage incident tracking metadata.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create tracking.json for an incident bundle if missing.")
    init_parser.add_argument("--incident-dir", required=True)
    init_parser.add_argument("--created-at", default="")
    init_parser.add_argument("--trigger", default="")

    set_parser = subparsers.add_parser("set-status", help="Set signature/status/confidence for an incident.")
    set_parser.add_argument("--incident-dir", required=True)
    set_parser.add_argument("--signature", required=True)
    set_parser.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    set_parser.add_argument("--confidence", type=float, default=None)
    set_parser.add_argument("--runs-since-applied", type=int, default=None)
    set_parser.add_argument("--notes", default="")
    set_parser.add_argument("--evidence", action="append", default=[])
    set_parser.add_argument("--hypothesis", default="")
    set_parser.add_argument("--applied-fix", default="")
    set_parser.add_argument("--next-debug-action", default="")

    runs_parser = subparsers.add_parser("record-survival", help="Increase survived run count for a signature.")
    runs_parser.add_argument("--signature", required=True)
    runs_parser.add_argument("--runs", type=int, default=1)
    runs_parser.add_argument("--evidence", action="append", default=[])

    guidance_parser = subparsers.add_parser("set-guidance", help="Store reusable hypothesis/fix/debug guidance for a signature.")
    guidance_parser.add_argument("--signature", required=True)
    guidance_parser.add_argument("--hypothesis", default="")
    guidance_parser.add_argument("--applied-fix", default="")
    guidance_parser.add_argument("--next-debug-action", default="")
    guidance_parser.add_argument("--source-incident", default="")

    show_parser = subparsers.add_parser("show", help="Show either one incident tracking file or the whole registry.")
    show_parser.add_argument("--incident-dir", default="")
    show_parser.add_argument("--signature", default="")

    suggest_parser = subparsers.add_parser("suggest", help="Suggest a signature for an incident bundle.")
    suggest_parser.add_argument("--incident-dir", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init":
        payload = ensure_tracking_file(
            Path(args.incident_dir),
            created_at=str(args.created_at or ""),
            trigger=str(args.trigger or ""),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "set-status":
        payload = update_incident_tracking(
            incident_dir=Path(args.incident_dir),
            signature=str(args.signature),
            status=str(args.status),
            confidence=args.confidence,
            evidence=list(args.evidence or []),
            notes=str(args.notes or ""),
            runs_since_applied=args.runs_since_applied,
            hypothesis=str(args.hypothesis or ""),
            applied_fix=str(args.applied_fix or ""),
            next_debug_action=str(args.next_debug_action or ""),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "record-survival":
        payload = bump_signature_runs(
            signature=str(args.signature),
            runs=int(args.runs or 1),
            evidence=list(args.evidence or []),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "set-guidance":
        payload = update_signature_guidance(
            signature=str(args.signature),
            hypothesis=str(args.hypothesis or ""),
            applied_fix=str(args.applied_fix or ""),
            next_debug_action=str(args.next_debug_action or ""),
            source_incident=str(args.source_incident or ""),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "show":
        if args.incident_dir:
            payload = ensure_tracking_file(Path(args.incident_dir))
        else:
            registry = _load_registry()
            if args.signature:
                payload = (registry.get("signatures") or {}).get(str(args.signature), {})
            else:
                payload = registry
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "suggest":
        incident_dir = Path(args.incident_dir)
        incident_payload = _read_json(incident_dir / "incident.json", {})
        trigger = ""
        created_at = ""
        if isinstance(incident_payload, dict):
            trigger = str(incident_payload.get("reason") or "")
            created_at = str(incident_payload.get("created_at") or "")
        suggested_signature, signature_basis = suggest_signature_for_incident(
            incident_dir,
            created_at=created_at,
            trigger=trigger,
        )
        payload = {
            "incident_id": _incident_id_from_dir(incident_dir),
            "suggested_signature": suggested_signature,
            "signature_basis": signature_basis,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
