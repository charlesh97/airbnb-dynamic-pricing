"""FastAPI app — iGMS Dynamic Pricing Dashboard."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .routes import calendar, config, day_detail, pricing, push
from .engine_proxy import get_properties
from pricing_engine.config import EngineConfig


def _setup_logging() -> None:
    """Configure app logging from LOG_LEVEL/.env."""
    repo_root = Path(__file__).parent.parent
    cfg = EngineConfig.from_env(repo_root / ".env")
    level_name = (cfg.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Keep uvicorn and app loggers aligned with requested level.
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("dashboard").setLevel(level)
    logging.getLogger("pricing_engine").setLevel(level)


_setup_logging()

app = FastAPI(title="iGMS Dynamic Pricing Dashboard", version="1.0.0")

# CORS — allow browser dev tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(calendar.router)
app.include_router(day_detail.router)
app.include_router(config.router)
app.include_router(pricing.router)
app.include_router(push.router)


# ── Static files & templates ─────────────────────────────────────────────────

template_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(template_dir))

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root(request: Request):
    """Redirect to calendar view."""
    return RedirectResponse(url="/calendar", status_code=302)


def _get_current_uid(request: Request) -> str:
    """Extract current_uid from URL params or return default."""
    uid = request.query_params.get("property_uid", "")
    if not uid:
        props = get_properties()
        uid = props[0]["property_uid"] if props else "731418607849470882"
    return uid


@app.get("/calendar")
async def calendar_page(request: Request):
    """Calendar view page."""
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {"properties": get_properties(), "current_uid": _get_current_uid(request)},
    )


@app.get("/config-editor")
async def config_page(request: Request):
    """Config editor page."""
    return templates.TemplateResponse(
        request,
        "config_editor.html",
        {"properties": get_properties(), "current_uid": _get_current_uid(request)},
    )
