"""HTTP API and web UI, reached through Home Assistant ingress.

All routes are relative to the ingress path prefix; the UI uses relative URLs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Annotated, Literal, Protocol

import grpc
from fastapi import FastAPI, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, Field
from starlette.types import Scope

from vaillant_eebus.log_store import PAGE_SIZE, LogCategory, LogPage, LogStore
from vaillant_eebus.pairing import ConnectionState, ForeignBridgeError, NotPairedError, PairingService, PairingStatus
from vaillant_eebus.settings import OperatingMode
from vaillant_eebus.state import HeatPumpState, StateService
from vaillant_eebus.write_gate import (
    ControlCompressor,
    PendingWrite,
    SetConsumptionLimit,
    SetFailsafeLimit,
    SetHotWaterBoost,
    SetHotWaterMode,
    SetHotWaterSetpoint,
    SetZoneMode,
    SetZoneSetpoint,
    WriteGate,
    WriteRejected,
    WriteResult,
)

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


class ModeRequest(BaseModel):
    mode: OperatingMode
    # Switching to live needs the user's explicit confirmation.
    confirm: bool = False


class SetpointRequest(BaseModel):
    value: float = Field(allow_inf_nan=False)


class OperationModeRequest(BaseModel):
    # Checked against the modes the device offers by the write gate.
    mode: str = Field(pattern=r"^[a-z]{1,32}$")


class BoostRequest(BaseModel):
    active: bool


class ConsumptionLimitRequest(BaseModel):
    value_watts: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: int = Field(ge=0)
    active: bool


class FailsafeLimitRequest(BaseModel):
    value_watts: float = Field(ge=0, allow_inf_nan=False)
    duration_minimum_seconds: int = Field(ge=0)


class CompressorRequest(BaseModel):
    action: Literal["schedule", "pause", "resume", "abort"]
    # Only for schedule; without it the compressor starts at once.
    start_time: AwareDatetime | None = None


# The bridge's zone ID: the SPINE address of the zone's room entity, e.g. "5.1.1".
ZoneId = Annotated[str, PathParam(pattern=r"^[0-9]{1,5}(\.[0-9]{1,5}){0,7}$")]


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
    mode: OperatingMode
    # Sent values the device has not reported yet.
    pending: list[PendingWrite]
    # None until a gateway is paired and this app's own bridge answers.
    heat_pump: HeatPumpState | None


@dataclass(frozen=True, slots=True)
class ModeStatus:
    mode: OperatingMode


class _Client(Protocol):
    async def is_disconnected(self) -> bool: ...


async def state_events(
    client: _Client,
    state: StateService,
    api_state: Callable[[HeatPumpState | None], Awaitable[ApiState]],
    *,
    keepalive: float = SSE_KEEPALIVE,
) -> AsyncGenerator[str]:
    """Server-sent events: the full state on connect and after every change.

    A failing bridge call skips one message instead of ending the stream; the
    next change or keep-alive follows as usual.
    """
    async with state.listen() as updates:
        current = state.current
        while True:
            try:
                payload = json.dumps(jsonable_encoder(await api_state(current)), ensure_ascii=False)
            except grpc.aio.AioRpcError as err:
                _LOGGER.warning("Event stream: bridge call failed (%s), sending the next update", err.code().name)
                yield ": bridge error\n\n"
            else:
                yield f"event: state\ndata: {payload}\n\n"
            while True:
                if await client.is_disconnected():
                    return
                try:
                    async with asyncio.timeout(keepalive):
                        current = await updates.get()
                    break
                except TimeoutError:
                    yield ": keep-alive\n\n"


def create_app(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log: LogStore,
    *,
    static_dir: Path | None,
    trusted_clients: frozenset[str],
) -> FastAPI:
    app = FastAPI(title="Vaillant EEBUS", docs_url=None, redoc_url=None, openapi_url=None)

    async def api_state(heat_pump: HeatPumpState | None) -> ApiState:
        return ApiState(
            connection=(await pairing.status()).state, mode=gate.mode, pending=gate.pending, heat_pump=heat_pump
        )

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
        return StreamingResponse(
            state_events(request, state, api_state),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/pairing/register")
    async def register_gateway(request: RegisterRequest) -> PairingStatus:
        return await pairing.pair(request.ski)

    @app.get("/api/mode")
    async def get_mode() -> ModeStatus:
        return ModeStatus(mode=gate.mode)

    @app.post("/api/mode")
    async def set_mode(request: ModeRequest) -> ModeStatus:
        return ModeStatus(mode=gate.set_mode(request.mode, confirm=request.confirm))

    @app.post("/api/zones/{zone_id}/setpoint")
    async def set_zone_setpoint(zone_id: ZoneId, request: SetpointRequest) -> WriteResult:
        return await gate.submit(SetZoneSetpoint(zone_id=zone_id, celsius=request.value))

    @app.post("/api/zones/{zone_id}/mode")
    async def set_zone_mode(zone_id: ZoneId, request: OperationModeRequest) -> WriteResult:
        return await gate.submit(SetZoneMode(zone_id=zone_id, mode=request.mode))

    @app.post("/api/dhw/setpoint")
    async def set_dhw_setpoint(request: SetpointRequest) -> WriteResult:
        return await gate.submit(SetHotWaterSetpoint(celsius=request.value))

    @app.post("/api/dhw/mode")
    async def set_dhw_mode(request: OperationModeRequest) -> WriteResult:
        return await gate.submit(SetHotWaterMode(mode=request.mode))

    @app.post("/api/dhw/boost")
    async def set_dhw_boost(request: BoostRequest) -> WriteResult:
        return await gate.submit(SetHotWaterBoost(active=request.active))

    @app.post("/api/lpc")
    async def write_consumption_limit(request: ConsumptionLimitRequest) -> WriteResult:
        return await gate.submit(
            SetConsumptionLimit(watts=request.value_watts, duration_s=request.duration_seconds, active=request.active)
        )

    @app.post("/api/lpc/failsafe")
    async def write_failsafe_limit(request: FailsafeLimitRequest) -> WriteResult:
        return await gate.submit(
            SetFailsafeLimit(watts=request.value_watts, duration_s=request.duration_minimum_seconds)
        )

    @app.post("/api/ohpcf")
    async def control_compressor(request: CompressorRequest) -> WriteResult:
        return await gate.submit(ControlCompressor(action=request.action, start_time=request.start_time))

    @app.get("/api/log")
    async def get_log(
        category: LogCategory | None = None,
        before: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = PAGE_SIZE,
    ) -> LogPage:
        return await log.page(category=category, before=before, limit=limit)

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

    @app.exception_handler(WriteRejected)
    async def write_rejected(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, WriteRejected)
        headers = {"Retry-After": str(math.ceil(exc.retry_after))} if exc.retry_after is not None else None
        return JSONResponse({"detail": exc.message}, status_code=exc.status, headers=headers)

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
