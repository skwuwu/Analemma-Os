"""
VSM Lambda Handler — Mangum adapter for VirtualSegmentManager FastAPI app.

Exposes the same FastAPI app as a Lambda Function URL endpoint.
SegmentRunnerFunction calls this endpoint for REACT segment governance.

Security:
    X-Analemma-Key header validation — requests without a valid key are rejected
    with 403. The key is stored in the ANALEMMA_VSM_API_KEY environment variable
    (shared between SegmentRunnerFunction and VSMFunction via SAM template).
"""

import os
import logging

from mangum import Mangum
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.bridge.virtual_segment_manager import app

logger = logging.getLogger(__name__)

# ── API Key Middleware ──────────────────────────────────────────────────────

_VSM_API_KEY = os.environ.get("ANALEMMA_VSM_API_KEY", "")


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests missing a valid X-Analemma-Key header.

    Skips validation for /v1/health (used by load balancers / readiness probes)
    and when ANALEMMA_VSM_API_KEY is not configured (local dev fallback).
    """

    async def dispatch(self, request: Request, call_next):
        if not _VSM_API_KEY:
            return await call_next(request)

        if request.url.path == "/v1/health":
            return await call_next(request)

        provided_key = request.headers.get("X-Analemma-Key", "")
        if provided_key != _VSM_API_KEY:
            logger.warning(
                "[VSM Auth] Rejected request to %s — invalid or missing X-Analemma-Key",
                request.url.path,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden: invalid X-Analemma-Key"},
            )

        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

# ── Mangum Lambda Adapter ──────────────────────────────────────────────────

handler = Mangum(app, lifespan="off")
