"""FastAPI application factory. Serves the API under /api/v1 and the static frontend at /."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fixmytech import __version__
from fixmytech.domain.knowledge import default_knowledge_base
from fixmytech.domain.repair import RepairPricingService, load_demo_rows
from fixmytech.integrations.flipvalue.client import FlipValueClient, FlipValueSettings
from fixmytech.pricing.estimate import PricingPolicy
from fixmytech.pricing.store import PricingStore
from fixmytech.server.api import router
from fixmytech.server.config import Settings, get_settings
from fixmytech.server.db import Database
from fixmytech.server.errors import install_error_handlers
from fixmytech.server.partner import PartnerStore
from fixmytech.server.security import ProtectionMiddleware, SlidingWindowLimiter
from fixmytech.server.services import AppState

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line. Messages are already free of secrets and bodies (see security.py)."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(out)


def configure_logging(fmt: str) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if fmt == "json":
        for h in root.handlers:
            h.setFormatter(JsonLogFormatter())


def flipvalue_settings(s: Settings) -> FlipValueSettings:
    return FlipValueSettings(
        mode=s.flipvalue_mode, base_url=s.flipvalue_base_url,
        api_key=s.flipvalue_api_key.get_secret_value() if s.flipvalue_api_key else None,
        timeout_s=s.flipvalue_timeout_s, max_retries=s.flipvalue_max_retries, api_version=s.flipvalue_api_version,
    )


def create_app(settings: Settings | None = None, flipvalue: FlipValueClient | None = None,
               serve_frontend: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_format)
    frontend = Path(settings.frontend_dir) if settings.frontend_dir else FRONTEND_DIR

    db = Database(settings.database_url)
    db.migrate()
    db.purge_expired()
    pricing = RepairPricingService(
        PricingStore(Path(settings.pricing_dir)),
        PricingPolicy(allowed_sources=settings.allowed_pricing_sources, demo_enabled=settings.pricing_demo,
                      fresh_days=settings.pricing_fresh_days, stale_days=settings.pricing_stale_days,
                      show_policy_notes=settings.env != "production"),
        demo_rows=load_demo_rows() if settings.pricing_demo else None,
        benchmark_rows=load_demo_rows() if settings.benchmark_fallback_enabled else None,
    )

    app = FastAPI(title="FixMyTech API", version=__version__,
                  docs_url="/api/docs" if settings.env != "production" else None,
                  redoc_url=None, openapi_url="/api/openapi.json")
    app.state.fmt = AppState(
        settings=settings, db=db, kb=default_knowledge_base(), pricing=pricing,
        flipvalue=flipvalue or FlipValueClient(flipvalue_settings(settings)),
        valuation_limiter=SlidingWindowLimiter(settings.valuation_limit_per_hour, 3600),
        partner_store=PartnerStore(Path(settings.partner_config_path)),
        partner_limiter=SlidingWindowLimiter(20, 3600),
    )
    install_error_handlers(app)
    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"], allow_credentials=False)
    app.add_middleware(ProtectionMiddleware, max_body_bytes=settings.max_body_bytes,
                       limiter=SlidingWindowLimiter(settings.rate_limit_per_minute, 60),
                       trust_proxy=settings.trust_proxy_headers)
    app.include_router(router)

    @app.get("/p/{token}", include_in_schema=False)
    def phone_tests_page(token: str) -> FileResponse:
        # The token is checked by the phone API; this only serves the static page.
        return FileResponse(frontend / "phone.html", headers={"Cache-Control": "no-store"})

    if serve_frontend and frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
