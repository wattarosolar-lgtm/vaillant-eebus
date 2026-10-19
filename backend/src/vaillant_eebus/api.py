"""HTTP API and web UI, reached through Home Assistant ingress.

All routes are relative to the ingress path prefix; the UI uses relative URLs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import grpc
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vaillant_eebus.pairing import NotPairedError, PairingService, PairingStatus

_LOGGER = logging.getLogger(__name__)

# The Supervisor's address on the hassio network; ingress requests come from it.
INGRESS_CLIENT = "172.30.32.2"

_BRIDGE_ERRORS: dict[grpc.StatusCode, tuple[int, str]] = {
    grpc.StatusCode.UNAVAILABLE: (503, "Die EEBUS-Bridge oder das Gateway ist gerade nicht erreichbar."),
    grpc.StatusCode.DEADLINE_EXCEEDED: (504, "Die EEBUS-Bridge hat nicht rechtzeitig geantwortet."),
    grpc.StatusCode.NOT_FOUND: (404, "Die EEBUS-Bridge kennt das gekoppelte Gateway nicht."),
}
_BRIDGE_ERROR_DEFAULT = (502, "Die EEBUS-Bridge hat einen Fehler gemeldet.")


class RegisterRequest(BaseModel):
    ski: str = Field(pattern=r"^[0-9A-Fa-f]{40}$")


def create_app(pairing: PairingService, *, static_dir: Path | None, trusted_clients: frozenset[str]) -> FastAPI:
    app = FastAPI(title="Vaillant EEBUS", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def only_ingress(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client = request.client.host if request.client else None
        if client not in trusted_clients:
            _LOGGER.warning("Rejected request from %s: not the ingress proxy", client)
            return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)

    @app.get("/api/pairing")
    async def get_pairing() -> PairingStatus:
        return await pairing.status()

    @app.post("/api/pairing/register")
    async def register_gateway(request: RegisterRequest) -> PairingStatus:
        return await pairing.pair(request.ski)

    @app.get("/api/diagnostics/dump")
    async def download_dump() -> Response:
        dump = await pairing.diagnostic_dump()
        return Response(
            content=dump.content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{dump.filename}"'},
        )

    @app.exception_handler(grpc.aio.AioRpcError)
    async def bridge_error(_request: Request, exc: Exception) -> JSONResponse:
        code = exc.code() if isinstance(exc, grpc.aio.AioRpcError) else grpc.StatusCode.UNKNOWN
        status, message = _BRIDGE_ERRORS.get(code, _BRIDGE_ERROR_DEFAULT)
        _LOGGER.warning("Bridge call failed: %s", code.name)
        return JSONResponse({"detail": message}, status_code=status)

    @app.exception_handler(NotPairedError)
    async def not_paired(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Es ist noch kein Gateway gekoppelt."}, status_code=409)

    if static_dir is not None:
        if static_dir.is_dir():
            app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
        else:
            _LOGGER.warning("UI directory %s not found; serving the API only", static_dir)
    return app
