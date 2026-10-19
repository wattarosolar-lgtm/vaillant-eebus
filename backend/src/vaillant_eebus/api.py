"""HTTP API and web UI, reached through Home Assistant ingress.

All routes are relative to the ingress path prefix; the UI uses relative URLs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePath

import grpc
from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import Scope

from vaillant_eebus.pairing import ConnectionState, ForeignBridgeError, NotPairedError, PairingService, PairingStatus
from vaillant_eebus.state import HeatPumpState, StateService

_LOGGER = logging.getLogger(__name__)

# The Supervisor's address on the hassio network; ingress requests come from it.
INGRESS_CLIENT = "172.30.32.2"

_BRIDGE_ERRORS: dict[grpc.StatusCode, tuple[int, str]] = {
    grpc.StatusCode.UNAVAILABLE: (503, "Die EEBUS-Bridge oder das Gateway ist gerade nicht erreichbar."),
    grpc.StatusCode.DEADLINE_EXCEEDED: (504, "Die EEBUS-Bridge hat nicht rechtzeitig geantwortet."),
    grpc.StatusCode.NOT_FOUND: (404, "Die EEBUS-Bridge kennt das gekoppelte Gateway nicht."),
}
_BRIDGE_ERROR_DEFAULT = (502, "Die EEBUS-Bridge hat einen Fehler gemeldet.")
FOREIGN_BRIDGE_MESSAGE = (
    "Unter der Adresse dieser App antwortet eine fremde EEBUS-Bridge. Vermutlich läuft eine andere EEBUS-App; "
    "bitte diese stoppen und diese App neu starten."
)


# Seconds between keep-alive comments on the event stream.
SSE_KEEPALIVE = 15.0


class RegisterRequest(BaseModel):
    ski: str = Field(pattern=r"^[0-9A-Fa-f]{40}$")


class UiFiles(StaticFiles):
    """Serves the built UI with cache rules that survive app updates.

    Bundled assets carry a content hash in their name and never change; the page
    itself must be revalidated, or a browser keeps referencing assets of an older
    version that no longer exist.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if PurePath(path).parts[:1] == ("assets",):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


@dataclass(frozen=True, slots=True)
class ApiState:
    connection: ConnectionState
    # None until a gateway is paired and this app's own bridge answers.
    heat_pump: HeatPumpState | None


def create_app(
    pairing: PairingService,
    state: StateService,
    *,
    static_dir: Path | None,
    trusted_clients: frozenset[str],
) -> FastAPI:
    app = FastAPI(title="Vaillant EEBUS", docs_url=None, redoc_url=None, openapi_url=None)

    async def api_state(heat_pump: HeatPumpState | None) -> ApiState:
        return ApiState(connection=(await pairing.status()).state, heat_pump=heat_pump)

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

    @app.get("/api/state")
    async def get_state() -> ApiState:
        return await api_state(state.current)

    @app.get("/api/events")
    async def get_events(request: Request) -> StreamingResponse:
        """Server-sent events: the full state on connect and after every change."""

        async def stream() -> AsyncIterator[str]:
            async with state.listen() as updates:
                current = state.current
                while True:
                    payload = json.dumps(jsonable_encoder(await api_state(current)), ensure_ascii=False)
                    yield f"event: state\ndata: {payload}\n\n"
                    while True:
                        if await request.is_disconnected():
                            return
                        try:
                            async with asyncio.timeout(SSE_KEEPALIVE):
                                current = await updates.get()
                            break
                        except TimeoutError:
                            yield ": keep-alive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
        return JSONResponse({"detail": f"{message} (Fehlercode {code.name})"}, status_code=status)

    @app.exception_handler(NotPairedError)
    async def not_paired(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Es ist noch kein Gateway gekoppelt."}, status_code=409)

    @app.exception_handler(ForeignBridgeError)
    async def foreign_bridge(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": FOREIGN_BRIDGE_MESSAGE}, status_code=409)

    if static_dir is not None:
        if static_dir.is_dir():
            app.mount("/", UiFiles(directory=static_dir, html=True), name="ui")
        else:
            _LOGGER.warning("UI directory %s not found; serving the API only", static_dir)
    return app
