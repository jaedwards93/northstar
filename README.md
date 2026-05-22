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
└── console/
    ├── index.html
    ├── app.js
    └── style.css
```

## Pieces

1. **middleware** — main FastAPI app
2. **mock_northstar** — fake carrier outbound API
3. **console** — agent UI (static files at `/console`)
4. **harness** — scripted test scenarios (TBD)

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
| `GET /sessions/{id}` | Conversation detail + `previous_sessions` for same phone |
| `POST /sessions/{id}/reply` | Agent reply (`delivery_attempts` on response) |

Session expiry rules live in `shared/session_policy.py` (used by middleware; mirrored in the UI via `/config`).
