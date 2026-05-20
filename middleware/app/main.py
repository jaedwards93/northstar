"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from middleware.app.routes import agent, webhooks

app = FastAPI(
    title="Northstar Text-to-911 Middleware",
    description="POC middleware between Northstar and an agent console.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks.router)
app.include_router(agent.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
