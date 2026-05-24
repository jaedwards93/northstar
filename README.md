# Northstar Text-to-911 Middleware (POC)

## Layout

```
northstar/
├── shared/schemas.py
├── middleware/app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── store.py
│   ├── routes/
│   │   ├── webhooks.py
│   │   └── agent.py
│   └── services/
│       ├── inbound.py
│       ├── sessions.py
│       └── outbound.py
├── mock_northstar/app/main.py
├── run.py
├── harness/simulate.py
└── console/
    ├── index.html
    ├── app.js
    └── style.css
```

## Pieces

1. **middleware** — main FastAPI app
2. **mock_northstar** — fake carrier outbound API
3. **console** — agent UI (static files at `/console`)
4. **harness** — scripted demo scenarios (`harness/simulate.py`)

## Agent console

Run middleware from repo root (so `shared` imports resolve):

```bash
uvicorn middleware.app.main:app --reload
```

Open **http://127.0.0.1:8000/console/** — polls `GET /sessions?group_by_phone=true` every 2s (one row per caller).

| Endpoint | Purpose |
|----------|---------|
| `GET /config` | `session_ttl_seconds`, `session_expiring_soon_seconds` |
| `GET /sessions?group_by_phone=true` | One row per phone (current session) |
| `GET /sessions?include_expired=true` | Per-session list (tests/tools) |
| `GET /sessions/{id}` | Conversation detail + `previous_sessions`; `latest_outbound_delivery_status`, `outbound_delivery_failure` |
| `POST /sessions/{id}/reply` | Agent reply (`text`, optional `timestamp` for idempotency; `duplicate` on replay) |

Session expiry rules live in `shared/session_policy.py` (used by middleware; mirrored in the UI via `/config`).

## Harness and local demo

Use two terminals from repo root (venv active). **`run.py`** only starts servers; **`harness/simulate.py`** runs timed scenarios in parallel (one thread per scenario) so long waits do not block the API process.

### Setup (once)

```bash
python -m venv venv
pip install -r requirements.txt
```

**PowerShell:** `.\venv\Scripts\Activate.ps1`  
**Linux / macOS:** `source venv/bin/activate`

### Terminal 1 — services

```bash
python run.py
```

Starts mock Northstar (:8001) and middleware (:8000). Session TTL and expiring-soon thresholds are defined in `shared/session_policy.py` (defaults: 5 min TTL, 2 min expiring-soon window). Leave this running; **Ctrl+C** stops only the processes this script started.

### Terminal 2 — harness

```bash
python harness/simulate.py
```

Runs **11 scenarios** staggered over the **first 5 minutes** (not all at once). You can use the console as new callers appear.

**Step mode** (present one message at a time):

```bash
python harness/simulate.py --step
```

Same **staggered** launch as auto mode (all 11 callers on screen). Press **Enter** before each inbound/outbound (~58 messages in Phase 1, ~12–16 min at a comfortable pace). Long waits (session TTL, expiring-soon) are **deferred to Phase 2** at the end so you do not sit on one scenario for 5 minutes — the harness prints a timing guide at startup.

Typical presentation: Terminal 1 → `python run.py`; Terminal 2 → `python harness/simulate.py` (or `--step`); watch **http://127.0.0.1:8000/console/** as messages arrive.

**Mock Northstar:** outbound text containing `FORCE_FAIL` returns HTTP 500 so the middleware retry path is deterministic.

Session timing defaults live in `shared/session_policy.py` (wired into middleware via `config.py`).
