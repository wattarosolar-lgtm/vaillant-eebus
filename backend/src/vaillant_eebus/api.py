"""HTTP API and web UI, reached through Home Assistant ingress.

All routes are relative to the ingress path prefix; the UI uses relative URLs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path, PurePath
from typing import Annotated, Any, Literal, Protocol

import grpc
from fastapi import FastAPI, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, Field
from starlette.types import Scope

from vaillant_eebus.energy import EnergyMeter, EnergyToday
from vaillant_eebus.ha_client import WEATHER_ENTITY, HomeAssistantUnavailable
from vaillant_eebus.ha_import import HomeAssistantState
from vaillant_eebus.hems.config import (
    BAND_K,
    BLOCK_ABOVE_C,
    CLOUD_COMMANDS,
    DESIGN_OUTDOOR_C,
    FLOW_C,
    HEAT_LOAD_W,
    HEATING_LIMIT_C,
    INTERVAL_MIN,
    LATITUDE,
    LONGITUDE,
    LOSS_K,
    STEP_K,
    TARGET_STARTS,
    ZONE1_THRESHOLD_W,
    ZONES_MAX,
    PlannerMode,
    WeatherSource,
    ZoneKind,
    hems_from_json,
    on_step,
)
from vaillant_eebus.hems.service import HemsService, HemsStatus, ModeNotAvailable, PlanView, plan_csv
from vaillant_eebus.history import PowerHistory
from vaillant_eebus.home_assistant import EntityChoice, HomeAssistant, performance_today
from vaillant_eebus.ids import CLIMATE_ENTITY, ENTITY_ID, SENSOR_ENTITY, WATER_HEATER_ENTITY, ZONE_ID, is_own_entity
from vaillant_eebus.log_store import PAGE_SIZE, LogCategory, LogPage, LogStore, csv_document
from vaillant_eebus.pairing import ConnectionState, ForeignBridgeError, NotPairedError, PairingService, PairingStatus
from vaillant_eebus.room_data import (
    COMFORT_K,
    CORRECTION_LIMIT_K,
    DEADBAND_K,
    DESIGN_C,
    EMERGENCY_C,
    FLOW_LPH,
    MAX_COMMANDS,
    MIN_INTERVAL_MIN,
    OFFSET_K,
    PRESETTING_MAX,
    RATED_W,
    ROOM_HEAT_LOAD_W,
    ROOM_LOSS_K,
    ROOM_SETPOINT_C,
    SCHEDULE_MAX,
    SURFACES_MAX,
    ProbePair,
    RatedBasis,
    RoomControlMode,
    RoomStage,
    SurfaceKind,
    details_from_json,
)
from vaillant_eebus.rooms.service import RoomControl, RoomControlStatus
from vaillant_eebus.settings import (
    HEAT_SENSORS_MAX,
    MYVAILLANT_ZONES_MAX,
    ROOM_NAME_MAX,
    Connection,
    ExportGroup,
    MyVaillantEntities,
    OperatingMode,
    Room,
)
from vaillant_eebus.state import HeatPumpState, StateHub
from vaillant_eebus.write_gate import (
    ControlCompressor,
    EndZoneOverride,
    PendingWrite,
    SetConsumptionLimit,
    SetFailsafeLimit,
    SetHotWaterBoost,
    SetHotWaterMode,
    SetHotWaterSetpoint,
    SetZoneMode,
    SetZoneSetpoint,
    WriteGate,
    WriteNotExecuted,
    WriteOutcomeUnknown,
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
NO_PAIRING_MESSAGE = "Mit der Anbindung myVAILLANT gibt es keine Kopplung und keinen Diagnose-Dump."
FOREIGN_BRIDGE_MESSAGE = (
    "Unter der Adresse dieser App antwortet eine fremde EEBUS-Bridge. Vermutlich läuft eine andere EEBUS-App; "
    "bitte diese stoppen und diese App neu starten."
)


# Seconds between keep-alive events on the event stream. The UI treats a stream
# without any message for three times as long as hung and opens a new one.
SSE_KEEPALIVE = 15.0
# A named event, unlike an SSE comment, reaches the page's script.
SSE_PING = "event: ping\ndata: {}\n\n"


class RegisterRequest(BaseModel):
    ski: str = Field(pattern=r"^[0-9A-Fa-f]{40}$")


class ModeRequest(BaseModel):
    mode: OperatingMode
    # Switching to live needs the user's explicit confirmation.
    confirm: bool = False


class WriteRequest(BaseModel):
    # The mode the caller showed the user when the change was made. If it
    # changed since, nothing is sent: a change decided in demo never goes live.
    operating_mode: OperatingMode


class SetpointRequest(WriteRequest):
    value: float = Field(allow_inf_nan=False)


class OperationModeRequest(WriteRequest):
    # Checked against the modes the device offers by the write gate.
    mode: str = Field(pattern=r"^[a-z]{1,32}$")


class BoostRequest(WriteRequest):
    active: bool


class ConsumptionLimitRequest(WriteRequest):
    value_watts: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: int = Field(ge=0)
    active: bool


class FailsafeLimitRequest(WriteRequest):
    value_watts: float = Field(ge=0, allow_inf_nan=False)
    duration_minimum_seconds: int = Field(ge=0)


class CompressorRequest(WriteRequest):
    action: Literal["schedule", "pause", "resume", "abort"]
    # Only for schedule; without it the compressor starts at once.
    start_time: AwareDatetime | None = None


# A zone's ID: the SPINE address of the zone's room entity (EEBUS, e.g. "5.1.1")
# or its climate entity (myVAILLANT).
ZoneId = Annotated[str, PathParam(pattern=ZONE_ID.pattern)]
# A room: the entity of its temperature.
RoomId = Annotated[str, PathParam(pattern=ENTITY_ID.pattern)]


class MyVaillantModel(BaseModel):
    zones: list[Annotated[str, Field(pattern=CLIMATE_ENTITY.pattern)]] = Field(
        default=[], max_length=MYVAILLANT_ZONES_MAX
    )
    hot_water: str | None = Field(default=None, pattern=WATER_HEATER_ENTITY.pattern)
    outdoor_temperature: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    power: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)

    def entities(self) -> MyVaillantEntities:
        return MyVaillantEntities(
            zones=tuple(self.zones),
            hot_water=self.hot_water,
            outdoor_temperature=self.outdoor_temperature,
            power=self.power,
        )


class ConnectionRequest(BaseModel):
    connection: Connection
    myvaillant: MyVaillantModel = MyVaillantModel()


class RoomModel(BaseModel):
    entity_id: str = Field(pattern=ENTITY_ID.pattern)
    name: str = Field(min_length=1, max_length=ROOM_NAME_MAX)
    zone_id: str | None = Field(default=None, pattern=ZONE_ID.pattern)


class SettingsRequest(BaseModel):
    rooms: list[RoomModel] = Field(max_length=50)
    heat_energy_entities: list[Annotated[str, Field(pattern=ENTITY_ID.pattern)]] = Field(
        default=[], max_length=HEAT_SENSORS_MAX
    )
    export: list[ExportGroup]


HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


def _range(limits: tuple[float, float], default: float | None = None) -> Any:
    return Field(default=default, allow_inf_nan=False, ge=limits[0], le=limits[1])


class ZoneConfigModel(BaseModel):
    zone_id: str = Field(pattern=ZONE_ID.pattern)
    kind: ZoneKind | None = None
    flow_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    flow_setpoint_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    design_flow_c: float | None = _range(FLOW_C)
    design_return_c: float | None = _range(FLOW_C)
    band_down_k: float | None = _range(BAND_K)
    band_up_k: float | None = _range(BAND_K)


class HemsSettingsRequest(BaseModel):
    weather_source: WeatherSource
    weather_entity: str | None = Field(default=None, pattern=WEATHER_ENTITY.pattern)
    latitude: float | None = _range(LATITUDE)
    longitude: float | None = _range(LONGITUDE)
    heat_heating_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    heat_dhw_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    buffer_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    design_outdoor_c: float | None = _range(DESIGN_OUTDOOR_C)
    heat_load_w: float | None = _range(HEAT_LOAD_W)
    zones: list[ZoneConfigModel] = Field(default=[], max_length=ZONES_MAX)
    step_k: float = _range(STEP_K, 0.5)
    min_interval_min: int = Field(default=30, ge=INTERVAL_MIN[0], le=INTERVAL_MIN[1])
    night_start: str = Field(default="00:00", pattern=HHMM)
    night_end: str = Field(default="10:00", pattern=HHMM)
    night_loss_k: float = _range(LOSS_K, 0.5)
    defrost_loss_k: float = _range(LOSS_K, 1.0)
    target_starts: int = Field(default=10, ge=TARGET_STARTS[0], le=TARGET_STARTS[1])
    heating_limit_c: float = _range(HEATING_LIMIT_C, 15.0)
    block_above_c: float = _range(BLOCK_ABOVE_C, 5.0)
    cloud_commands_max: int = Field(default=100, ge=CLOUD_COMMANDS[0], le=CLOUD_COMMANDS[1])
    zone1_threshold_w: float = _range(ZONE1_THRESHOLD_W, 30.0)


class SurfaceModel(BaseModel):
    kind: SurfaceKind = SurfaceKind.RADIATOR
    rated_w: float | None = _range(RATED_W)
    rated_basis: RatedBasis = RatedBasis.T75_65
    flow_lph: float | None = _range(FLOW_LPH)
    presetting: str = Field(default="", max_length=PRESETTING_MAX)


class ScheduleModel(BaseModel):
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)
    setpoint_c: float = _range(ROOM_SETPOINT_C, 20.0)


class RoomDetailsRequest(BaseModel):
    heat_load_w: float | None = _range(ROOM_HEAT_LOAD_W)
    design_c: float | None = _range(DESIGN_C)
    surfaces: list[SurfaceModel] = Field(default=[], max_length=SURFACES_MAX)
    trv_entity: str | None = Field(default=None, pattern=CLIMATE_ENTITY.pattern)
    valve_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    humidity_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    setpoint_c: float = _range(ROOM_SETPOINT_C, 20.0)
    schedule: list[ScheduleModel] = Field(default=[], max_length=SCHEDULE_MAX)
    comfort_down_k: float = _range(COMFORT_K, 0.5)
    comfort_up_k: float = _range(COMFORT_K, 0.5)
    night_loss_k: float | None = _range(ROOM_LOSS_K)
    defrost_loss_k: float | None = _range(ROOM_LOSS_K)
    throttle: bool = True
    emergency_c: float | None = _range(EMERGENCY_C)
    correction_limit_k: float = _range(CORRECTION_LIMIT_K, 5.0)
    offset_k: float = _range(OFFSET_K, 0.0)
    deadband_k: float = _range(DEADBAND_K, 0.5)
    min_interval_min: int = Field(default=15, ge=MIN_INTERVAL_MIN[0], le=MIN_INTERVAL_MIN[1])
    max_commands: int = Field(default=24, ge=MAX_COMMANDS[0], le=MAX_COMMANDS[1])


class RoomControlRequest(BaseModel):
    mode: RoomControlMode
    # "Steuern" needs the user's explicit confirmation.
    confirm: bool = False


class RoomStageRequest(BaseModel):
    stage: RoomStage
    confirm: bool = False


class TestRunRequest(BaseModel):
    action: Literal["arm", "cancel"]
    confirm: bool = False


class ProbeRequest(BaseModel):
    flow_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    return_entity: str | None = Field(default=None, pattern=SENSOR_ENTITY.pattern)
    room_id: str | None = Field(default=None, pattern=ENTITY_ID.pattern)


def _check_steps(values: dict[str, float | None], step: float, unit: str) -> None:
    for name, value in values.items():
        if value is not None and not on_step(value, step):
            raise WriteRejected(422, f"{name} geht nur in Schritten von {f'{step:g}'.replace('.', ',')} {unit}.")


class HemsModeRequest(BaseModel):
    mode: PlannerMode


@dataclass(frozen=True, slots=True)
class Location:
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    # None until chosen in the setup wizard (the app then works like EEBUS).
    connection: Connection | None
    # The connection the app works with right now; another one takes effect after the restart.
    active: Connection
    state: ConnectionState
    myvaillant: MyVaillantEntities
    # False outside Home Assistant: myVAILLANT cannot be used.
    home_assistant_available: bool
    app_version: str
    # True when the app restarts to change the connection.
    restarting: bool = False


@dataclass(frozen=True, slots=True)
class SettingsStatus:
    # Each room's zone for the current connection (the other one's stays in the backend).
    rooms: list[RoomModel]
    heat_energy_entities: list[str]
    export: list[ExportGroup]
    # Entity IDs the app currently reports to Home Assistant.
    exported: list[str]
    # False outside Home Assistant: nothing can be chosen or exported.
    home_assistant_available: bool


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
    # EEBUS or myVAILLANT: where the values come from.
    link: Connection
    mode: OperatingMode
    # Sent values the device has not reported yet.
    pending: list[PendingWrite]
    # None until a gateway is paired and this app's own bridge answers.
    heat_pump: HeatPumpState | None
    # Summed up by the app from the power; None before the first power reading of the day.
    energy_today: EnergyToday | None
    home_assistant: HomeAssistantState
    # Heat from Home Assistant divided by energy_today; None unless both are reliable.
    performance_today: float | None


@dataclass(frozen=True, slots=True)
class ModeStatus:
    mode: OperatingMode


@dataclass(frozen=True, slots=True)
class RoomsPage:
    """The page "Räume": what the energy management knows of the rooms and the room control."""

    rooms: list[Any]
    zones: list[Any]
    control: RoomControlStatus | None


@dataclass(frozen=True, slots=True)
class Automation:
    yaml: str


class _Client(Protocol):
    async def is_disconnected(self) -> bool: ...


async def state_events(
    client: _Client,
    state: StateHub,
    api_state: Callable[[HeatPumpState | None], Awaitable[ApiState]],
    *,
    keepalive: float = SSE_KEEPALIVE,
) -> AsyncGenerator[str]:
    """Server-sent events: the full state on connect and after every change, and
    a ping after keepalive seconds without a change.

    A failing bridge call skips one message instead of ending the stream; the
    next change or ping follows as usual.
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
                    yield SSE_PING


def create_app(
    pairing: PairingService,
    state: StateHub,
    gate: WriteGate,
    log: LogStore,
    energy: EnergyMeter,
    home_assistant: HomeAssistant,
    *,
    static_dir: Path | None,
    trusted_clients: frozenset[str],
    zone: tzinfo = UTC,
    hems: HemsService | None = None,
    rooms: RoomControl | None = None,
    restart: Callable[[], None] | None = None,
) -> FastAPI:
    """zone is Home Assistant's time zone: protocol days and downloaded times are local.
    restart stops the backend so that s6 starts it again (to change the connection)."""
    app = FastAPI(title="Vaillant EEBUS", docs_url=None, redoc_url=None, openapi_url=None)
    device = home_assistant.device
    restarting = False

    async def connection_state() -> ConnectionState:
        if device is not None:
            return device.connection_state()
        return (await pairing.status()).state

    def require_eebus() -> None:
        if gate.connection is not Connection.EEBUS:
            raise WriteRejected(409, NO_PAIRING_MESSAGE)

    async def connection_status() -> ConnectionStatus:
        current = home_assistant.settings
        return ConnectionStatus(
            connection=current.connection,
            active=gate.connection,
            state=await connection_state(),
            myvaillant=current.myvaillant,
            home_assistant_available=home_assistant.available,
            app_version=pairing.app_version,
            restarting=restarting,
        )

    def energy_management() -> HemsService:
        if hems is None:
            raise WriteRejected(503, "Das Energiemanagement läuft nicht.")
        return hems

    def room_control() -> RoomControl:
        if rooms is None:
            raise WriteRejected(503, "Die Raumregelung läuft nicht.")
        return rooms

    def rooms_page() -> RoomsPage:
        view = energy_management().rooms_view()
        return RoomsPage(
            rooms=list(view.rooms), zones=list(view.zones), control=rooms.status() if rooms is not None else None
        )

    async def to_demo() -> None:
        """Before demo mode: the room control hands the thermostats back while still live."""
        if rooms is not None and gate.mode is OperatingMode.LIVE:
            await rooms.before_demo()

    async def api_state(heat_pump: HeatPumpState | None) -> ApiState:
        electricity = energy.today()
        values = home_assistant.snapshot()
        return ApiState(
            connection=await connection_state(),
            link=gate.connection,
            mode=gate.mode,
            pending=gate.pending,
            heat_pump=heat_pump,
            energy_today=electricity,
            home_assistant=values,
            performance_today=performance_today(values.heat_today, electricity),
        )

    def settings_status() -> SettingsStatus:
        current = home_assistant.settings
        return SettingsStatus(
            rooms=[RoomModel(entity_id=room.entity_id, name=room.name, zone_id=room.zone_id) for room in current.rooms],
            heat_energy_entities=list(current.heat_energy_entities),
            export=sorted(current.export),
            exported=home_assistant.exported(),
            home_assistant_available=home_assistant.available,
        )

    @app.middleware("http")
    async def only_ingress(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client = request.client.host if request.client else None
        if client not in trusted_clients:
            _LOGGER.warning("Rejected request from %s: not the ingress proxy", client)
            return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)

    @app.get("/api/connection")
    async def get_connection() -> ConnectionStatus:
        return await connection_status()

    @app.put("/api/connection")
    async def put_connection(request: ConnectionRequest) -> ConnectionStatus:
        """Choose EEBUS or myVAILLANT and the myVAILLANT entities; a new connection restarts the app."""
        nonlocal restarting
        entities = request.myvaillant.entities()
        if len(set(entities.zones)) != len(entities.zones):
            raise WriteRejected(422, "Jede Zone kann nur einmal gewählt werden.")
        thermostats = {room.details.trv_entity for room in home_assistant.settings.rooms if room.details.trv_entity}
        if thermostats & set(entities.zones):
            raise WriteRejected(
                422,
                "Eine gewählte Zone ist das Heizkörperthermostat eines Raums; bitte dort ein anderes wählen.",
            )
        if any(entity and is_own_entity(entity) for entity in (entities.outdoor_temperature, entities.power)):
            raise WriteRejected(
                422,
                "Die eigenen Sensoren der App (sensor.vaillant_eebus_…) können keine Quelle sein; ihr Wert käme nur "
                "von der App selbst zurück. Bitte den Sensor der myVAILLANT-Integration wählen.",
            )
        if request.connection is Connection.MYVAILLANT:
            if not home_assistant.available:
                raise WriteRejected(409, "myVAILLANT geht nur, wenn die App in Home Assistant läuft.")
            if not entities.configured:
                raise WriteRejected(422, "Bitte mindestens eine Zone oder das Warmwasser von myVAILLANT wählen.")
        home_assistant.change_myvaillant(entities)
        before = home_assistant.settings.connection
        if request.connection is not gate.connection:
            if gate.mode is OperatingMode.LIVE:
                # The new way is checked in demo mode first; live needs a new confirmation.
                await to_demo()
                gate.set_mode(OperatingMode.DEMO, confirm=False)
            # The zones have other IDs on the new way: the rooms take theirs along.
            current = state.current
            old_labels = {zone.id: zone.label for zone in current.zones} if current is not None else {}
            new_labels = (
                await home_assistant.myvaillant_zone_labels(entities.zones)
                if request.connection is Connection.MYVAILLANT
                else {}
            )
            home_assistant.switch_rooms(old_labels, new_labels)
            home_assistant.change_connection(request.connection)
            restarting = True
            if restart is not None:
                restart()
        elif before is not request.connection:
            home_assistant.change_connection(request.connection)
        return await connection_status()

    @app.get("/api/myvaillant/discover")
    async def discover_myvaillant() -> MyVaillantEntities:
        return await home_assistant.discover_myvaillant()

    @app.get("/api/pairing")
    async def get_pairing() -> PairingStatus:
        require_eebus()
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
        require_eebus()
        return await pairing.pair(request.ski)

    @app.get("/api/mode")
    async def get_mode() -> ModeStatus:
        return ModeStatus(mode=gate.mode)

    @app.post("/api/mode")
    async def set_mode(request: ModeRequest) -> ModeStatus:
        if request.mode is OperatingMode.DEMO:
            await to_demo()
        return ModeStatus(mode=gate.set_mode(request.mode, confirm=request.confirm))

    @app.post("/api/zones/{zone_id}/setpoint")
    async def set_zone_setpoint(zone_id: ZoneId, request: SetpointRequest) -> WriteResult:
        command = SetZoneSetpoint(zone_id=zone_id, celsius=request.value)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/zones/{zone_id}/mode")
    async def set_zone_mode(zone_id: ZoneId, request: OperationModeRequest) -> WriteResult:
        command = SetZoneMode(zone_id=zone_id, mode=request.mode)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/zones/{zone_id}/override/end")
    async def end_zone_override(zone_id: ZoneId, request: WriteRequest) -> WriteResult:
        """myVAILLANT: end the zone's temporary override (quick veto); it follows its time program again."""
        command = EndZoneOverride(zone_id=zone_id)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/dhw/setpoint")
    async def set_dhw_setpoint(request: SetpointRequest) -> WriteResult:
        command = SetHotWaterSetpoint(celsius=request.value)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/dhw/mode")
    async def set_dhw_mode(request: OperationModeRequest) -> WriteResult:
        command = SetHotWaterMode(mode=request.mode)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/dhw/boost")
    async def set_dhw_boost(request: BoostRequest) -> WriteResult:
        command = SetHotWaterBoost(active=request.active)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/lpc")
    async def write_consumption_limit(request: ConsumptionLimitRequest) -> WriteResult:
        command = SetConsumptionLimit(
            watts=request.value_watts, duration_s=request.duration_seconds, active=request.active
        )
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/lpc/failsafe")
    async def write_failsafe_limit(request: FailsafeLimitRequest) -> WriteResult:
        command = SetFailsafeLimit(watts=request.value_watts, duration_s=request.duration_minimum_seconds)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.post("/api/ohpcf")
    async def control_compressor(request: CompressorRequest) -> WriteResult:
        command = ControlCompressor(action=request.action, start_time=request.start_time)
        return await gate.submit(command, expected_mode=request.operating_mode)

    @app.get("/api/settings")
    async def get_settings() -> SettingsStatus:
        return settings_status()

    @app.put("/api/settings")
    async def put_settings(request: SettingsRequest) -> SettingsStatus:
        entity_ids = [room.entity_id for room in request.rooms]
        if len(set(entity_ids)) != len(entity_ids):
            raise WriteRejected(422, "Jeder Sensor kann nur einem Raum zugeordnet werden.")
        if len(set(request.heat_energy_entities)) != len(request.heat_energy_entities):
            raise WriteRejected(422, "Jeder Wärmesensor kann nur einmal gewählt werden.")
        if rooms is not None:
            # A room that goes away (or whose sensor changes) first hands its thermostat back.
            known = {room.entity_id: room.details.trv_entity for room in home_assistant.settings.rooms}
            await rooms.before_rooms_change(
                ((room.entity_id, known.get(room.entity_id)) for room in request.rooms), "Räume geändert"
            )
        home_assistant.change(
            rooms=tuple(
                Room(entity_id=room.entity_id, name=room.name.strip(), zone_id=room.zone_id) for room in request.rooms
            ),
            heat_energy_entities=tuple(request.heat_energy_entities),
            export=frozenset(request.export),
        )
        return settings_status()

    @app.get("/api/ha/entities")
    async def get_ha_entities(
        kind: Literal[
            "temperature",
            "energy",
            "power",
            "percent",
            "humidity",
            "room",
            "thermostat",
            "weather",
            "climate",
            "water_heater",
        ],
        q: Annotated[str, Query(max_length=100)] = "",
    ) -> list[EntityChoice]:
        return await home_assistant.entities(kind, q)

    @app.get("/api/ha/location")
    async def get_ha_location() -> Location:
        location = await home_assistant.location()
        if location is None:
            raise WriteRejected(404, "In Home Assistant ist kein Standort eingetragen.")
        return Location(latitude=round(location[0], 4), longitude=round(location[1], 4))

    @app.get("/api/hems")
    async def get_hems() -> HemsStatus:
        return energy_management().status()

    @app.put("/api/hems/settings")
    async def put_hems_settings(request: HemsSettingsRequest) -> HemsStatus:
        service = energy_management()
        _check_steps({"Die Schrittweite": request.step_k}, 0.5, "K")
        for zone_config in request.zones:
            _check_steps({"Das Band einer Zone": zone_config.band_down_k}, 0.5, "K")
            _check_steps({"Das Band einer Zone": zone_config.band_up_k}, 0.5, "K")
        _check_steps(
            {"Der Verlust": request.night_loss_k, "Der Verlust bei Abtaurisiko": request.defrost_loss_k}, 0.1, "K"
        )
        if len({zone.zone_id for zone in request.zones}) != len(request.zones):
            raise WriteRejected(422, "Jede Zone kann nur einmal eingestellt werden.")
        if request.night_start == request.night_end:
            raise WriteRejected(422, "Das Nachtfenster braucht verschiedene Zeiten für Beginn und Ende.")
        if request.weather_source is WeatherSource.HOME_ASSISTANT and request.weather_entity is None:
            raise WriteRejected(422, "Für die Wetter-Entität von Home Assistant bitte eine Entität wählen.")
        service.change_settings(hems_from_json(request.model_dump(mode="json")))
        await service.plan_now()
        return service.status()

    @app.post("/api/hems/mode")
    async def set_hems_mode(request: HemsModeRequest) -> HemsStatus:
        service = energy_management()
        try:
            await service.set_mode(request.mode)
        except ModeNotAvailable:
            raise WriteRejected(
                409, "„Steuern“ der Zonen kommt mit Version 1.5.0; diese Version plant die Zonen nur."
            ) from None
        await service.plan_now()
        return service.status()

    @app.get("/api/hems/plan")
    async def get_plan() -> PlanView:
        return energy_management().plan_view()

    @app.get("/api/hems/plan/export")
    async def download_plan() -> Response:
        filename = f"vaillant-eebus-plan-{datetime.now(zone):%Y%m%d-%H%M}.csv"
        return Response(
            content=plan_csv(energy_management().plan_view(), zone),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/rooms")
    async def get_rooms() -> RoomsPage:
        return rooms_page()

    @app.put("/api/rooms/control")
    async def put_room_control(request: RoomControlRequest) -> RoomsPage:
        await room_control().set_mode(request.mode, confirm=request.confirm)
        return rooms_page()

    @app.put("/api/rooms/probe")
    async def put_probe(request: ProbeRequest) -> RoomsPage:
        await room_control().set_probe(
            ProbePair(flow_entity=request.flow_entity, return_entity=request.return_entity, room_id=request.room_id)
        )
        return rooms_page()

    @app.get("/api/rooms/automation")
    async def get_automation() -> Automation:
        return Automation(yaml=room_control().automation())

    @app.put("/api/rooms/{entity_id}/stage")
    async def put_room_stage(entity_id: RoomId, request: RoomStageRequest) -> RoomsPage:
        await room_control().set_stage(entity_id, request.stage, confirm=request.confirm)
        return rooms_page()

    @app.post("/api/rooms/{entity_id}/test")
    async def room_test(entity_id: RoomId, request: TestRunRequest) -> RoomsPage:
        control = room_control()
        if request.action == "arm":
            await control.arm_test(entity_id, confirm=request.confirm)
        else:
            await control.cancel_test(entity_id)
        return rooms_page()

    @app.put("/api/rooms/{entity_id}")
    async def put_room(entity_id: RoomId, request: RoomDetailsRequest) -> RoomsPage:
        service = energy_management()
        _check_steps(
            {
                "Das Raum-Soll": request.setpoint_c,
                "Das Auslegungs-Soll": request.design_c,
                "Der Notfallwert": request.emergency_c,
                **{
                    f"Das Soll im Zeitfenster {index}": window.setpoint_c
                    for index, window in enumerate(request.schedule, 1)
                },
            },
            0.5,
            "°C",
        )
        _check_steps(
            {
                "Das Komfortband": request.comfort_down_k,
                "Das Komfortband nach oben": request.comfort_up_k,
                "Der Verlust nachts": request.night_loss_k,
                "Der Verlust bei Abtaurisiko": request.defrost_loss_k,
            },
            0.1,
            "K",
        )
        _check_steps(
            {
                "Die Grenze der Korrektur": request.correction_limit_k,
                "Der feste Ausgleich": request.offset_k,
                "Die Totzone": request.deadband_k,
            },
            0.5,
            "K",
        )
        if any(window.start == window.end for window in request.schedule):
            raise WriteRejected(422, "Ein Zeitfenster braucht verschiedene Zeiten für Beginn und Ende.")
        current = home_assistant.settings
        known = next((room for room in current.rooms if room.entity_id == entity_id), None)
        if known is None:
            raise WriteRejected(404, "Diesen Raum gibt es nicht (mehr). Räume legen Sie in den Einstellungen an.")
        trv = request.trv_entity
        if trv is not None and trv in current.myvaillant.zones:
            raise WriteRejected(422, "Diese Klima-Entität ist eine Zone von myVAILLANT, kein Heizkörper-Thermostat.")
        if trv is not None and any(
            room.details.trv_entity == trv for room in current.rooms if room.entity_id != entity_id
        ):
            raise WriteRejected(422, "Dieses Thermostat gehört schon zu einem anderen Raum.")
        if known.details.stage is RoomStage.CONTROL and trv != known.details.trv_entity:
            raise WriteRejected(409, "Der Raum steht auf „Steuern“; das Thermostat erst nach „Vorschau“ wechseln.")
        if rooms is not None and trv != known.details.trv_entity:
            # The old thermostat is handed back before the room gets another one.
            await rooms.before_rooms_change(
                (
                    (room.entity_id, trv if room.entity_id == entity_id else room.details.trv_entity)
                    for room in current.rooms
                ),
                f"Thermostat von {known.name} gewechselt",
            )
        details = details_from_json(request.model_dump(mode="json"))
        # The stage is changed on its own (with confirmation); the editor keeps it.
        home_assistant.change_room(entity_id, replace(details, stage=known.details.stage))
        service.wake()
        return rooms_page()

    @app.get("/api/history/power")
    async def get_power_history() -> PowerHistory:
        return await home_assistant.power_history()

    @app.get("/api/log")
    async def get_log(
        category: LogCategory | None = None,
        problems: bool = False,
        before: Annotated[int | None, Query(ge=1)] = None,
        after: Annotated[int | None, Query(ge=0)] = None,
        until: date | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = PAGE_SIZE,
    ) -> LogPage:
        """before/after page by entry ID; until is the last local day to show (jump to a date)."""
        end = datetime.combine(until + timedelta(days=1), time(0), tzinfo=zone) if until is not None else None
        return await log.page(
            category=category, problems=problems, before=before, after=after, before_time=end, limit=limit
        )

    @app.get("/api/log/export")
    async def download_log(category: LogCategory | None = None, problems: bool = False) -> Response:
        entries = await log.entries(category=category, problems=problems)
        filename = f"vaillant-eebus-protokoll-{datetime.now(zone):%Y%m%d-%H%M%S}.csv"
        return Response(
            content=csv_document(entries, zone),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/diagnostics/dump")
    async def download_dump() -> Response:
        require_eebus()
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

    @app.exception_handler(WriteOutcomeUnknown)
    async def write_outcome_unknown(_request: Request, exc: Exception) -> JSONResponse:
        code = exc.code_name if isinstance(exc, WriteOutcomeUnknown) else "UNKNOWN"
        return JSONResponse(
            {
                "detail": "Ob die Änderung bei der Wärmepumpe angekommen ist, ist unklar "
                f"(Fehlercode {code}). Die App prüft, ob die Wärmepumpe den Wert meldet."
            },
            status_code=504,
        )

    @app.exception_handler(WriteNotExecuted)
    async def write_not_executed(_request: Request, exc: Exception) -> JSONResponse:
        code = exc.code if isinstance(exc, WriteNotExecuted) else "UNKNOWN"
        return JSONResponse(
            {"detail": f"Home Assistant hat den Befehl nicht ausgeführt ({code}); an der Wärmepumpe bleibt alles."},
            status_code=502,
        )

    @app.exception_handler(WriteRejected)
    async def write_rejected(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, WriteRejected)
        headers = {"Retry-After": str(math.ceil(exc.retry_after))} if exc.retry_after is not None else None
        return JSONResponse({"detail": exc.message}, status_code=exc.status, headers=headers)

    @app.exception_handler(HomeAssistantUnavailable)
    async def home_assistant_unavailable(_request: Request, exc: Exception) -> JSONResponse:
        _LOGGER.warning("Home Assistant request failed: %s", exc)
        return JSONResponse(
            {"detail": "Home Assistant ist gerade nicht erreichbar. Bitte später noch einmal versuchen."},
            status_code=503,
        )

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
