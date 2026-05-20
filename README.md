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
3. **console** — agent UI
4. **harness** — scripted test scenarios (TBD)
