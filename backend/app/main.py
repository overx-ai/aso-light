import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api import api_router
from app.core.config import settings
from app.data.seed import seed_territories
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.mcp import mcp_app

import app.models  # noqa: F401 — register all models with Base.metadata

# App-level loggers default to INFO so logger.info() lines (apply payload,
# intro-offer results, etc.) show up in the dev log without bumping each
# call site to warning. uvicorn keeps its own access logs at INFO too.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:    %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Compose with the FastMCP lifespan (required for MCP session management).
    async with mcp_app.lifespan(app):
        logger.info("Starting up: initialising database")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_session_factory() as session:
            await seed_territories(session)
        yield
        logger.info("Shutting down")
        await engine.dispose()


app = FastAPI(
    title="ASO Light",
    description="App Store Optimization SaaS tool",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.mount("/mcp", mcp_app)


# `redirect_slashes=False` (project convention) means /mcp without a trailing
# slash 404s instead of forwarding to the mounted FastMCP app. 307 keeps the
# method and body intact, so MCP clients configured with /mcp keep working.
@app.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"])
async def _mcp_trailing_slash() -> RedirectResponse:
    return RedirectResponse(url="/mcp/", status_code=307)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
