"""
Northstar demo harness — scripted scenarios for the agent console.

Services must already be running (separate terminal):

  python run.py

Run scenarios (staggered over the first 5 minutes):

  python harness/simulate.py

Step through messages manually (scenarios still staggered; long TTL waits move to Phase 2):

  python harness/simulate.py --step
"""

from __future__ import annotations

import argparse
import random
import secrets
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx

from shared.session_policy import SESSION_TTL_SECONDS

MIDDLEWARE_URL = "http://127.0.0.1:8000"
MOCK_URL = "http://127.0.0.1:8001"

HTTP_RETRY_ATTEMPTS = 5
HTTP_RETRY_BACKOFF_SEC = 0.5
_RETRYABLE_ERRORS = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.PoolTimeout,
)

MIN_STEP_PAUSE_SEC = 15
TWO_MIN_PAUSE_SEC = 120
ONE_MIN_PAUSE_SEC = 60
STAGGER_WINDOW_SEC = 300  # first 5 minutes
SESSION_EXPIRE_WAIT_SEC = SESSION_TTL_SECONDS + 10  # past session TTL

_STREET_NAMES = (
    "Oak St",
    "Maple Ave",
    "Cedar Ln",
    "Pine Dr",
    "Elm St",
    "Birch Rd",
    "Walnut Way",
    "Cherry Ct",
    "Park Blvd",
    "Lakeview Ter",
    "Highland Ave",
    "Mill St",
    "River Rd",
    "Sunset Dr",
    "Meadow Ln",
)
_STREET_SUFFIXES = ("", " Apt 2", " Apt 4B", " Unit 12", " #3")

_shutdown = threading.Event()
_step_mode = False
_program_start = 0.0
_stdin_lock = threading.Lock()
_step_ctx = threading.local()

# Step-mode presentation estimates (Enter before each inbound/outbound in Phase 1).
STEP_MODE_MESSAGES_PHASE1 = 58
STEP_MODE_SEC_PER_ENTER_EST = 12.0

ScenarioFn = Callable[[], None]


@dataclass(frozen=True)
class DeferredStep:
    scenario_id: str
    title: str
    label: str
    run: Callable[[], None]
    not_before: float
    order: int


_deferred_lock = threading.Lock()
_deferred_steps: list[DeferredStep] = []
_deferred_order = 0


@dataclass(frozen=True)
class ScheduledScenario:
    id: str
    title: str
    start_offset_sec: float
    run: ScenarioFn


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    )


def _request_with_retry(request: Callable[[], httpx.Response]) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
        try:
            return request()
        except _RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt < HTTP_RETRY_ATTEMPTS:
                time.sleep(HTTP_RETRY_BACKOFF_SEC * attempt)
    assert last_exc is not None
    raise last_exc


def check_services() -> str | None:
    with http_client() as client:
        for name, url in (
            ("middleware", f"{MIDDLEWARE_URL}/health"),
            ("mock Northstar", f"{MOCK_URL}/health"),
        ):
            try:
                res = client.get(url)
                if res.status_code != 200:
                    return f"{name} unhealthy at {url} (HTTP {res.status_code})"
            except httpx.RequestError as exc:
                return f"{name} not reachable at {url}: {exc}"
    return None


def scenario_phone(scenario_id: str) -> str:
    """+1 + 6 digits + 4-digit suffix (scenario 1 → …1111, 10 → …1010, 11 → …1011)."""
    n = int(scenario_id)
    if n < 10:
        suffix = int(str(n) * 4)
    elif n == 10:
        suffix = 1010
    elif n == 11:
        suffix = 1011
    else:
        suffix = int(f"{n:04d}")
    middle = random.randint(200000, 999999)
    return f"+1{middle}{suffix:04d}"


def random_address(rng: random.Random | None = None) -> str:
    r = rng or random.Random()
    number = r.randint(100, 9999)
    street = r.choice(_STREET_NAMES)
    suffix = r.choice(_STREET_SUFFIXES)
    return f"{number} {street}{suffix}"


def log(scenario_id: str, title: str, message: str) -> None:
    print(f"[Scenario {scenario_id} — {title}] {message}")


def _step_prefix() -> str:
    sid = getattr(_step_ctx, "scenario_id", None)
    name = getattr(_step_ctx, "title", None)
    if sid and name:
        return f"[Scenario {sid} — {name}] "
    return ""


def _wait_enter(prompt: str) -> None:
    if _shutdown.is_set():
        raise KeyboardInterrupt
    with _stdin_lock:
        print(f"\n>>> {_step_prefix()}{prompt}", flush=True)
        try:
            input()
        except EOFError:
            raise KeyboardInterrupt from None


def _schedule_deferred(
    scenario_id: str,
    title: str,
    label: str,
    run: Callable[[], None],
    *,
    delay_sec: float,
) -> None:
    global _deferred_order
    not_before = time.monotonic() + delay_sec
    with _deferred_lock:
        _deferred_order += 1
        order = _deferred_order
        _deferred_steps.append(
            DeferredStep(
                scenario_id=scenario_id,
                title=title,
                label=label,
                run=run,
                not_before=not_before,
                order=order,
            )
        )
    rel_min = (not_before - _program_start) / 60.0
    log(
        scenario_id,
        title,
        f"Deferred to Phase 2 (~{rel_min:.0f} min from start): {label}",
    )


def pause_ttl_deferred(
    scenario_id: str,
    title: str,
    label: str,
    delay_sec: float,
    continue_fn: Callable[[], None],
) -> None:
    """Auto mode: sleep then continue. Step mode: run continue_fn later in Phase 2."""
    if _step_mode:
        _schedule_deferred(
            scenario_id, title, label, continue_fn, delay_sec=delay_sec
        )
        return
    pause(scenario_id, title, delay_sec, label)
    continue_fn()


def pause(scenario_id: str, title: str, seconds: float, label: str) -> None:
    if _step_mode:
        return
    log(scenario_id, title, f"... waiting {seconds:g}s — {label}")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _shutdown.is_set():
            raise KeyboardInterrupt
        time.sleep(min(0.5, deadline - time.monotonic()))


def _step_before_message(kind: str, preview: str) -> None:
    if not _step_mode:
        return
    snippet = preview.replace("\n", " ").strip()
    if len(snippet) > 72:
        snippet = snippet[:69] + "…"
    _wait_enter(f"Press Enter to send {kind}: {snippet}")


def wait_until(started_at: float, offset_sec: float) -> None:
    deadline = started_at + offset_sec
    while time.monotonic() < deadline:
        if _shutdown.is_set():
            raise KeyboardInterrupt
        time.sleep(min(0.5, deadline - time.monotonic()))


def post_inbound(client: httpx.Client, phone: str, text: str) -> dict[str, Any]:
    _step_before_message("inbound", text)
    res = _request_with_retry(
        lambda: client.post(
            f"{MIDDLEWARE_URL}/inbound",
            json={"from": phone, "text": text, "timestamp": iso(utc_now())},
        )
    )
    res.raise_for_status()
    return res.json()


def post_reply(client: httpx.Client, session_id: str, text: str) -> None:
    res = post_reply_raw(client, session_id, text)
    res.raise_for_status()
    body = res.json()
    if not body.get("success", True):
        raise RuntimeError(f"Reply delivery failed: {body.get('error')}")


def post_reply_raw(
    client: httpx.Client,
    session_id: str,
    text: str,
    *,
    timestamp: datetime | None = None,
) -> httpx.Response:
    _step_before_message("outbound", text)
    at = timestamp or utc_now()
    body = {"text": text, "timestamp": iso(at)}
    return _request_with_retry(
        lambda: client.post(
            f"{MIDDLEWARE_URL}/sessions/{session_id}/reply",
            json=body,
        )
    )


def expect_session_expired_reply(res: httpx.Response) -> None:
    if res.status_code != 409:
        raise RuntimeError(f"expected HTTP 409 for expired session reply, got {res.status_code}")
    detail = res.json().get("detail")
    if not isinstance(detail, dict) or detail.get("code") != "SESSION_EXPIRED":
        raise RuntimeError(f"expected SESSION_EXPIRED, got {detail}")


def expect_idempotent_outbound(res: httpx.Response) -> None:
    res.raise_for_status()
    body = res.json()
    if not body.get("duplicate"):
        raise RuntimeError(f"expected duplicate=true idempotent replay, got {body}")


def patch_tags(client: httpx.Client, session_id: str, tags: list[str]) -> None:
    res = _request_with_retry(
        lambda: client.patch(
            f"{MIDDLEWARE_URL}/sessions/{session_id}/tags",
            json={"tags": tags},
        )
    )
    res.raise_for_status()


_DISPATCH_PREFIX = "911 Dispatch: "

# Citizen text containing any of these is treated as already describing the emergency.
_SITUATION_HINTS = (
    "fire",
    "smoke",
    "seizure",
    "break",
    "intruder",
    "chest pain",
    "ambulance",
    "burn",
    "burned",
    "fight",
    "weapon",
    "medical",
    "stove",
    "violent",
    "smell smoke",
    "caught fire",
)


def citizen_described_situation(citizen_text: str) -> bool:
    """True when the caller already gave a concrete emergency (not just 'help' / '911')."""
    t = citizen_text.lower().strip()
    if t in ("help", "911", "help!"):
        return False
    return any(hint in t for hint in _SITUATION_HINTS)


def dispatcher_initial_response(
    client: httpx.Client,
    session_id: str,
    *,
    citizen_text: str,
    has_address: bool = False,
) -> None:
    """Ask only for what is still missing: address, situation, or both."""
    situation = citizen_described_situation(citizen_text)
    if situation and not has_address:
        text = (
            _DISPATCH_PREFIX
            + "Please share your location so we can dispatch the appropriate "
            "emergency services."
        )
    elif not situation and has_address:
        text = (
            _DISPATCH_PREFIX
            + "Message Received. Please describe what is happening so we can "
            "dispatch the appropriate emergency services."
        )
    elif not situation:
        text = (
            _DISPATCH_PREFIX
            + "Please share your location and a brief description of what is "
            "happening so we can dispatch the appropriate emergency services."
        )
    else:
        text = (
            _DISPATCH_PREFIX
            + "Please share your location so we can dispatch the appropriate "
            "emergency services."
        )
    post_reply(client, session_id, text)


def dispatcher_needs_address(client: httpx.Client, session_id: str) -> None:
    post_reply(
        client,
        session_id,
        _DISPATCH_PREFIX
        + "I'm still on the line. Please send your address when you can so we "
        "can dispatch the appropriate emergency services.",
    )


# ---------------------------------------------------------------------------
# SCENARIO 1 — Happy path (fire → new session after TTL)
# ---------------------------------------------------------------------------


def run_scenario_1_happy_path() -> None:
    scenario_id = "1"
    title = "Happy path"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}, address {address}")

        log(scenario_id, title, "[1] Citizen: initial help request")
        first = post_inbound(client, phone, "911 please help, I need assistance!")
        sid = first["session_id"]

        log(scenario_id, title, "[2] Citizen: shares address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher reviews")
        log(scenario_id, title, "[3] Dispatcher: asks what is happening")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="911 please help, I need assistance!",
            has_address=True,
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "citizen reports fire")
        log(scenario_id, title, "[4] Citizen: house on fire")
        post_inbound(client, phone, "My house is on fire!")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "FIRE dispatched")
        log(scenario_id, title, "[5] Dispatcher: FIRE en route")
        post_reply(
            client,
            sid,
            "FIRE units are on the way. Stay outside and keep clear of the structure.",
        )
        patch_tags(client, sid, ["fire"])

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "FIRE on scene check-in")
        log(scenario_id, title, "[6] Dispatcher: situation resolved?")
        post_reply(
            client,
            sid,
            "911 Dispatch: FIRE units should be on scene. Is everyone out safely, "
            "and is the situation under control?",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "citizen update")
        log(scenario_id, title, "[7] Citizen: trucks arrived, everyone outside")
        post_inbound(
            client,
            phone,
            "The fire trucks just got here. We're all outside the house.",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher follow-up")
        log(scenario_id, title, "[8] Dispatcher: stay clear")
        post_reply(
            client,
            sid,
            "911 Dispatch: Good. Stay clear of the structure and let us know if "
            "anything changes.",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "citizen thanks")
        log(scenario_id, title, "[9] Citizen: yes, thank you")
        post_inbound(
            client,
            phone,
            "Yes — thank you so much for your help.",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher closing")
        log(scenario_id, title, "[10] Dispatcher: you're welcome")
        post_reply(
            client,
            sid,
            "911 Dispatch: You're welcome. We're glad you're safe. Text us again "
            "anytime you need emergency assistance.",
        )

        def after_ttl() -> None:
            with http_client() as ttl_client:
                log(scenario_id, title, "[11] Citizen: texts after session expired")
                follow_up = post_inbound(
                    ttl_client, phone, "Sorry — are you still there?"
                )
                new_sid = follow_up["session_id"]
                if new_sid != sid:
                    log(scenario_id, title, "New session created after expiry")
                pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "agent on new session")
                log(scenario_id, title, "[12] Dispatcher: reply on new session")
                post_reply(
                    ttl_client,
                    new_sid,
                    "911 Dispatch: We're still here. How can we help you now?",
                )
            log(scenario_id, title, f"Complete — {phone}")

        pause_ttl_deferred(
            scenario_id, title, "past session TTL", SESSION_EXPIRE_WAIT_SEC, after_ttl
        )
        if _step_mode:
            log(
                scenario_id,
                title,
                "Phase 1 complete (post-expiry follow-up deferred to Phase 2)",
            )


# ---------------------------------------------------------------------------
# SCENARIO 2 — Expired session (no citizen reply; reply disabled)
# ---------------------------------------------------------------------------


def run_scenario_2_expired_session() -> None:
    scenario_id = "2"
    title = "Expired session"
    phone = scenario_phone(scenario_id)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: HELP")
        first = post_inbound(client, phone, "HELP")
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: location and emergency")
        dispatcher_initial_response(client, sid, citizen_text="HELP")

        pause(scenario_id, title, TWO_MIN_PAUSE_SEC, "2 min after HELP")
        log(scenario_id, title, "[3] Dispatcher: still on the line")
        dispatcher_needs_address(client, sid)

        def after_ttl() -> None:
            with http_client() as ttl_client:
                log(scenario_id, title, "[4] Verify reply rejected")
                expect_session_expired_reply(
                    post_reply_raw(
                        ttl_client, sid, "Reply after expiry (harness check)."
                    )
                )
            log(scenario_id, title, "Reply disabled in console for this caller")
            log(scenario_id, title, f"Complete — {phone}")

        pause_ttl_deferred(
            scenario_id,
            title,
            "TTL elapsed",
            SESSION_EXPIRE_WAIT_SEC + 20,
            after_ttl,
        )
        if _step_mode:
            log(scenario_id, title, "Phase 1 complete (expiry check deferred to Phase 2)")


# ---------------------------------------------------------------------------
# SCENARIO 3 — Duplicate outbound (medical)
# ---------------------------------------------------------------------------

_ADDRESS_PROMPT = (
    "911 Dispatch: Please share your location so we can dispatch medical assistance."
)


def run_scenario_3_duplicate_outbound() -> None:
    scenario_id = "3"
    title = "Duplicate outbound"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: seizure, needs medical")
        first = post_inbound(client, phone, "I need medical help — someone is having a seizure!")
        sid = first["session_id"]

        log(scenario_id, title, "[2] Dispatcher: request address")
        sent_at = utc_now()
        post_reply_raw(client, sid, _ADDRESS_PROMPT, timestamp=sent_at).raise_for_status()

        log(scenario_id, title, "[3] Duplicate outbound replay (same timestamp, idempotent)")
        expect_idempotent_outbound(
            post_reply_raw(client, sid, _ADDRESS_PROMPT, timestamp=sent_at)
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "citizen sends address")
        log(scenario_id, title, "[4] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "MEDICAL dispatched")
        log(scenario_id, title, "[5] Dispatcher: MEDICAL en route")
        post_reply(
            client,
            sid,
            "MEDICAL has been dispatched and is on the way. Stay with the patient if it's safe.",
        )
        patch_tags(client, sid, ["medical"])

        pause(scenario_id, title, TWO_MIN_PAUSE_SEC, "citizen asks ETA")
        log(scenario_id, title, "[6] Citizen: how long until medical arrives")
        post_inbound(
            client,
            phone,
            "How much longer until medical gets here?",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher ETA")
        log(scenario_id, title, "[7] Dispatcher: estimated arrival")
        post_reply(
            client,
            sid,
            "911 Dispatch: MEDICAL is approximately 6–8 minutes out. Stay with the "
            "patient and keep the line open if anything changes.",
        )
        log(scenario_id, title, f"Complete — {phone}")


# ---------------------------------------------------------------------------
# SCENARIO 4 — Outbound delivery failure (schedule slot 4; id 8)
# ---------------------------------------------------------------------------


def run_scenario_8_outbound_failure() -> None:
    scenario_id = "8"
    title = "Outbound failure"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: needs help")
        first = post_inbound(client, phone, "911 I need help right away!")
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher asks")
        log(scenario_id, title, "[2] Dispatcher: location and emergency")
        dispatcher_initial_response(
            client, sid, citizen_text="911 I need help right away!"
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "failed outbound")
        log(scenario_id, title, "[4] Dispatcher: FORCE_FAIL test")
        res = post_reply_raw(
            client,
            sid,
            "POLICE are en route — FORCE_FAIL delivery test for harness.",
        )
        res.raise_for_status()
        body = res.json()
        if not body.get("success", True):
            log(scenario_id, title, f"Delivery failed as expected: {body.get('error')}")
        else:
            log(scenario_id, title, "WARNING: expected delivery failure")

        pause(scenario_id, title, ONE_MIN_PAUSE_SEC, "retry without FORCE_FAIL")
        log(scenario_id, title, "[5] Dispatcher: retry delivery")
        post_reply(
            client,
            sid,
            "911 Dispatch: POLICE are en route. Stay on the line if you need anything else.",
        )
        patch_tags(client, sid, ["police"])
        log(scenario_id, title, f"Complete — {phone}")


# ---------------------------------------------------------------------------
# SCENARIO 5 — Multi-agency dispatch
# ---------------------------------------------------------------------------


def run_scenario_5_multi_agency() -> None:
    scenario_id = "5"
    title = "Multi-agency"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: crash needs police and medical")
        first = post_inbound(
            client,
            phone,
            "There's been a bad car accident — we need police and an ambulance!",
        )
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: request location")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="There's been a bad car accident — we need police and an ambulance!",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "injuries detail")
        log(scenario_id, title, "[4] Citizen: injuries")
        post_inbound(
            client,
            phone,
            "One driver is hurt and trapped — the other car ran a red light.",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "POLICE dispatched")
        log(scenario_id, title, "[5] Dispatcher: POLICE en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: POLICE are en route to secure the scene and manage traffic.",
        )
        patch_tags(client, sid, ["police"])

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "MEDICAL dispatched")
        log(scenario_id, title, "[6] Dispatcher: MEDICAL en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: MEDICAL is also en route for injuries. Do not move "
            "anyone who is seriously hurt unless they are in immediate danger.",
        )
        patch_tags(client, sid, ["police", "medical"])
        log(scenario_id, title, f"Complete — {phone}")


# ---------------------------------------------------------------------------
# SCENARIO 6 — Medical chest pain
# ---------------------------------------------------------------------------


def run_scenario_6_medical_chest_pain() -> None:
    scenario_id = "6"
    title = "Medical chest pain"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: chest pain")
        first = post_inbound(
            client, phone, "I need an ambulance — my husband is having chest pain!"
        )
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: request location")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="I need an ambulance — my husband is having chest pain!",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "symptoms worsen")
        log(scenario_id, title, "[4] Citizen: trouble breathing")
        post_inbound(client, phone, "He's having trouble breathing now.")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "MEDICAL dispatched")
        log(scenario_id, title, "[5] Dispatcher: MEDICAL en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: MEDICAL is en route. If he's conscious, keep him calm and "
            "avoid giving food or drink.",
        )
        patch_tags(client, sid, ["medical"])
        log(scenario_id, title, f"Complete — {phone}")


# ---------------------------------------------------------------------------
# SCENARIO 7 — Fire smoke alarm
# ---------------------------------------------------------------------------


def run_scenario_7_fire_smoke() -> None:
    scenario_id = "7"
    title = "Fire smoke alarm"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: smoke alarm")
        first = post_inbound(
            client, phone, "My smoke alarm won't stop — I smell smoke in the house!"
        )
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: request location")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="My smoke alarm won't stop — I smell smoke in the house!",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "kitchen fire detail")
        log(scenario_id, title, "[4] Citizen: kitchen smoke")
        post_inbound(client, phone, "Smoke is coming from the kitchen — stove may be on fire.")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "FIRE dispatched")
        log(scenario_id, title, "[5] Dispatcher: FIRE en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: FIRE units are responding. Get everyone out and wait "
            "outside well away from the home.",
        )
        patch_tags(client, sid, ["fire"])

        def after_ttl() -> None:
            with http_client() as ttl_client:
                log(scenario_id, title, "[6] Citizen: message after session expired")
                follow_up = post_inbound(
                    ttl_client,
                    phone,
                    "The smoke cleared — is it okay to go back inside?",
                )
                new_sid = follow_up["session_id"]
                if new_sid != sid:
                    log(scenario_id, title, "New session created after expiry")
                pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "agent on new session")
                log(scenario_id, title, "[7] Dispatcher: reply on new session")
                post_reply(
                    ttl_client,
                    new_sid,
                    "911 Dispatch: Do not re-enter until FIRE has cleared the structure. "
                    "We'll help coordinate next steps.",
                )
            log(scenario_id, title, f"Complete — {phone}")

        pause_ttl_deferred(
            scenario_id, title, "past session TTL", SESSION_EXPIRE_WAIT_SEC, after_ttl
        )
        if _step_mode:
            log(
                scenario_id,
                title,
                "Phase 1 complete (post-expiry follow-up deferred to Phase 2)",
            )


# ---------------------------------------------------------------------------
# SCENARIO 8 — Police break-in (schedule slot 8; id 4)
# ---------------------------------------------------------------------------


def run_scenario_4_police_break_in() -> None:
    scenario_id = "4"
    title = "Police break-in"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: intruder")
        first = post_inbound(client, phone, "Someone is trying to break into my home!")
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: request location")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="Someone is trying to break into my home!",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "citizen gives address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "back door detail")
        log(scenario_id, title, "[4] Citizen: back door")
        post_inbound(client, phone, "They're at the back door — I'm hiding in the bedroom.")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "POLICE dispatched")
        log(scenario_id, title, "[5] Dispatcher: POLICE en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: POLICE have been dispatched. Stay somewhere safe and quiet.",
        )
        patch_tags(client, sid, ["police"])
        log(scenario_id, title, f"Complete — {phone}")


# ---------------------------------------------------------------------------
# SCENARIO 9 — Fire and medical (burn injury)
# ---------------------------------------------------------------------------


def run_scenario_9_fire_and_medical() -> None:
    scenario_id = "9"
    title = "Fire and medical"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: kitchen fire and burn")
        first = post_inbound(
            client,
            phone,
            "A towel caught fire on the stove — I burned my hand trying to put it out!",
        )
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: request location")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="A towel caught fire on the stove — I burned my hand trying to put it out!",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "still burning")
        log(scenario_id, title, "[4] Citizen: fire not out")
        post_inbound(client, phone, "Flames are out but smoke is heavy — my hand is blistering.")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "FIRE and MEDICAL dispatched")
        log(scenario_id, title, "[5] Dispatcher: both agencies en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: FIRE and MEDICAL are both en route. Move to fresh air and "
            "run cool water over the burn.",
        )
        patch_tags(client, sid, ["fire", "medical"])
        log(scenario_id, title, f"Complete — {phone}")


# ---------------------------------------------------------------------------
# SCENARIO 10 — Expiring soon (long pause, citizen returns)
# ---------------------------------------------------------------------------


def run_scenario_10_expiring_soon() -> None:
    scenario_id = "10"
    title = "Expiring soon"
    phone = scenario_phone(scenario_id)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: HELP")
        first = post_inbound(client, phone, "HELP")
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: location and emergency")
        dispatcher_initial_response(client, sid, citizen_text="HELP")

        def after_expiring_window() -> None:
            with http_client() as ttl_client:
                log(scenario_id, title, "[3] Citizen: still on line")
                post_inbound(
                    ttl_client,
                    phone,
                    "I'm still here — sorry, I was finding my address.",
                )
                pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "citizen gives address")
                log(scenario_id, title, "[4] Citizen: address")
                post_inbound(ttl_client, phone, "My address is 892 Pine Dr.")
                pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "POLICE dispatched")
                log(scenario_id, title, "[5] Dispatcher: POLICE en route")
                post_reply(
                    ttl_client,
                    sid,
                    "911 Dispatch: POLICE have been dispatched. Stay on the line if "
                    "you need anything else.",
                )
                patch_tags(ttl_client, sid, ["police"])
            log(scenario_id, title, f"Complete — {phone}")

        pause_ttl_deferred(
            scenario_id,
            title,
            "approaching expiring-soon window",
            240,
            after_expiring_window,
        )
        if _step_mode:
            log(
                scenario_id,
                title,
                "Phase 1 complete (expiring-soon follow-up deferred to Phase 2)",
            )


# ---------------------------------------------------------------------------
# SCENARIO 11 — Session rollover (police → new session after TTL)
# ---------------------------------------------------------------------------


def run_scenario_11_session_rollover() -> None:
    scenario_id = "11"
    title = "Session rollover"
    rng = random.Random(secrets.randbits(64))
    phone = scenario_phone(scenario_id)
    address = random_address(rng)

    with http_client() as client:
        log(scenario_id, title, f"Starting — caller {phone}")

        log(scenario_id, title, "[1] Citizen: noise complaint escalated")
        first = post_inbound(client, phone, "There's a fight next door — it's getting violent!")
        sid = first["session_id"]

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "dispatcher responds")
        log(scenario_id, title, "[2] Dispatcher: request location")
        dispatcher_initial_response(
            client,
            sid,
            citizen_text="There's a fight next door — it's getting violent!",
        )

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "address")
        log(scenario_id, title, "[3] Citizen: address")
        post_inbound(client, phone, f"My address is {address}")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "weapons mentioned")
        log(scenario_id, title, "[4] Citizen: someone has a weapon")
        post_inbound(client, phone, "I think someone has a weapon — people are yelling.")

        pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "POLICE dispatched")
        log(scenario_id, title, "[5] Dispatcher: POLICE en route")
        post_reply(
            client,
            sid,
            "911 Dispatch: POLICE are on the way. Stay away from the conflict and "
            "keep the line open.",
        )
        patch_tags(client, sid, ["police"])

        def after_ttl() -> None:
            with http_client() as ttl_client:
                log(scenario_id, title, "[6] Citizen: thank you (new session)")
                thanks = post_inbound(ttl_client, phone, "Thank you")
                new_sid = thanks["session_id"]
                if new_sid != sid:
                    log(scenario_id, title, "New session created after expiry")
                pause(scenario_id, title, MIN_STEP_PAUSE_SEC, "agent on new session")
                log(scenario_id, title, "[7] Dispatcher: you're welcome")
                post_reply(
                    ttl_client,
                    new_sid,
                    "911 Dispatch: You're welcome. We're glad we could help. "
                    "Text us again anytime you need emergency assistance.",
                )
            log(scenario_id, title, f"Complete — {phone}")

        pause_ttl_deferred(
            scenario_id, title, "past session TTL", SESSION_EXPIRE_WAIT_SEC, after_ttl
        )
        if _step_mode:
            log(
                scenario_id,
                title,
                "Phase 1 complete (post-expiry rollover deferred to Phase 2)",
            )


def _stagger_offsets(count: int, window_sec: float) -> list[float]:
    if count <= 1:
        return [0.0]
    step = window_sec / count
    return [round(i * step, 1) for i in range(count)]


_scenarios = [
    ("1", "Happy path", run_scenario_1_happy_path),
    ("2", "Expired session", run_scenario_2_expired_session),
    ("3", "Duplicate outbound", run_scenario_3_duplicate_outbound),
    ("8", "Outbound failure", run_scenario_8_outbound_failure),
    ("5", "Multi-agency", run_scenario_5_multi_agency),
    ("6", "Medical chest pain", run_scenario_6_medical_chest_pain),
    ("7", "Fire smoke alarm", run_scenario_7_fire_smoke),
    ("4", "Police break-in", run_scenario_4_police_break_in),
    ("9", "Fire and medical", run_scenario_9_fire_and_medical),
    ("10", "Expiring soon", run_scenario_10_expiring_soon),
    ("11", "Session rollover", run_scenario_11_session_rollover),
]
_offsets = _stagger_offsets(len(_scenarios), STAGGER_WINDOW_SEC)
SCENARIO_SCHEDULE: list[ScheduledScenario] = [
    ScheduledScenario(sid, title, offset, fn)
    for (sid, title, fn), offset in zip(_scenarios, _offsets, strict=True)
]


def _print_step_mode_schedule() -> None:
    phase1_lo = STEP_MODE_MESSAGES_PHASE1 * STEP_MODE_SEC_PER_ENTER_EST / 60.0
    phase1_hi = phase1_lo * 1.35
    stagger_min = STAGGER_WINDOW_SEC / 60.0
    ttl_min = SESSION_EXPIRE_WAIT_SEC / 60.0
    print("Step mode presentation guide:")
    print(
        f"  Phase 1 (~{phase1_lo:.0f}–{phase1_hi:.0f} min): "
        f"All 11 scenarios staggered over ~{stagger_min:.0f} min; "
        f"Press Enter before each message (~{STEP_MODE_MESSAGES_PHASE1} sends)."
    )
    print(
        f"  Phase 2 (~{ttl_min:.0f} min TTL waits, then Enter per message): "
        "session expiry, expiring-soon, and post-expiry agent replies"
    )
    print("    • Scenario 1 — Citizen texts after TTL → agent on new session")
    print("    • Scenario 2 — Reply rejected on expired session")
    print("    • Scenario 7 — Citizen after TTL → agent on new session")
    print("    • Scenario 10 — Citizen returns in expiring-soon window")
    print("    • Scenario 11 — Thank you after TTL → agent you're welcome")
    print(
        f"  Tip: Phase 2 starts after Phase 1 threads finish; "
        f"TTL timers run in the background (~{ttl_min:.0f} min from deferral).\n"
    )


def _wait_until_monotonic(deadline: float, scenario_id: str, title: str, label: str) -> None:
    while True:
        if _shutdown.is_set():
            raise KeyboardInterrupt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _step_mode and remaining > 5:
            log(
                scenario_id,
                title,
                f"... TTL wait {remaining:.0f}s remaining — {label}",
            )
            time.sleep(min(15.0, remaining))
        else:
            time.sleep(min(0.5, remaining))


def run_deferred_phase() -> None:
    with _deferred_lock:
        steps = sorted(_deferred_steps, key=lambda s: (s.not_before, s.order))

    if not steps:
        return

    print("\n" + "=" * 60)
    print("PHASE 2 — Deferred steps (expiry / expiring-soon / rollover)")
    print("=" * 60)
    for step in steps:
        rel = (step.not_before - _program_start) / 60.0
        print(
            f"  Scenario {step.scenario_id} — {step.title}: "
            f"{step.label} (scheduled ~{rel:.0f} min from start)"
        )
    print()

    for step in steps:
        if _shutdown.is_set():
            break
        _step_ctx.scenario_id = step.scenario_id
        _step_ctx.title = step.title
        log(step.scenario_id, step.title, f"Phase 2 — {step.label}")
        _wait_until_monotonic(step.not_before, step.scenario_id, step.title, step.label)
        try:
            step.run()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[Scenario {step.scenario_id} — {step.title}] FAILED: {exc}")
            traceback.print_exc()


def _run_wrapped(scenario_id: str, title: str, fn: ScenarioFn) -> None:
    _step_ctx.scenario_id = scenario_id
    _step_ctx.title = title
    try:
        fn()
    except KeyboardInterrupt:
        log(scenario_id, title, "Stopped")
    except Exception as exc:
        print(f"[Scenario {scenario_id} — {title}] FAILED: {exc}")
        traceback.print_exc()


def run_staggered() -> None:
    """Start each scenario at a different time over the first five minutes."""
    global _program_start
    _program_start = time.monotonic()
    program_start = _program_start
    threads: list[threading.Thread] = []

    for item in SCENARIO_SCHEDULE:
        if _shutdown.is_set():
            break
        wait = item.start_offset_sec - (time.monotonic() - program_start)
        if wait > 0:
            print(
                f"  (next in {wait:.0f}s: Scenario {item.id} — {item.title})",
                flush=True,
            )
            wait_until(program_start, item.start_offset_sec)

        print(f"Launching Scenario {item.id} — {item.title}", flush=True)
        thread = threading.Thread(
            target=_run_wrapped,
            args=(item.id, item.title, item.run),
            name=f"scenario-{item.id}",
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    try:
        while True:
            if not any(t.is_alive() for t in threads):
                break
            for thread in threads:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        _shutdown.set()
        print("\nStopping scenarios (Ctrl+C again to force quit)...")
        for thread in threads:
            thread.join(timeout=3)
        raise

    run_deferred_phase()


def main() -> None:
    global _step_mode

    parser = argparse.ArgumentParser(description="Northstar demo harness")
    parser.add_argument(
        "--step",
        action="store_true",
        help="Press Enter before each message; defer long TTL waits to Phase 2",
    )
    args = parser.parse_args()
    _step_mode = args.step

    print("Northstar demo harness\n")
    err = check_services()
    if err:
        print(f"Services not ready — {err}")
        print("Start both with: python run.py")
        sys.exit(1)

    if _step_mode:
        print("Step mode: scenarios run in order (1 → 10).")
        print("Press Enter before each inbound/outbound message.")
        print("Long TTL waits still require Enter but do not sleep.\n")
    else:
        print("Scenarios (staggered over first 5 minutes):")
        for item in SCENARIO_SCHEDULE:
            print(f"  {item.start_offset_sec:5.0f}s — Scenario {item.id}: {item.title}")
    print("\nPress Ctrl+C to stop.\n")

    try:
        run_staggered()
        print("\nHarness complete.")
    except KeyboardInterrupt:
        print("\nHarness stopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
