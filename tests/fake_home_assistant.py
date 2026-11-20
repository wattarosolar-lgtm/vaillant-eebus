"""A small Home Assistant Core for tests and the local UI preview.

Serves the parts of the REST and WebSocket API the app uses: states, the
app's own sensors, the history of an entity, the home location, the service
weather.get_forecasts, authentication,
subscribe_trigger for state changes, subscribe_events for
homeassistant_started, the recorder's long-term statistics
(recorder/statistics_during_period), and a restart that drops every connection
and every state the app set (the history stays, as in the recorder). It also
stands in for Open-Meteo (/v1/dwd-icon) with the hours in open_meteo.

add_myvaillant() adds the entities of the myVAILLANT integration like the
real installation (two zones, hot water, outdoor temperature, power); the
services on them change the states as the integration does.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import WSMsgType, web

TOKEN = "fake-supervisor-token"

MV_ZONE_1 = "climate.zuhause_zone_1_climate"
MV_ZONE_2 = "climate.zuhause_zone_2_climate"
MV_HOT_WATER = "water_heater.zuhause_domestic_hot_water_0"
MV_OUTDOOR = "sensor.zuhause_outdoor_temperature"
MV_POWER = "sensor.zuhause_device_0_arotherm_split_plus_current_power"
# The integration's default length of a quick veto.
QUICK_VETO_HOURS = 3.0
_DHW_OPERATIONS = ["Manual", "Time Controlled", "Off", "Cylinder Boost"]


@dataclass
class _Socket:
    ws: web.WebSocketResponse
    # Subscription ID -> watched entity IDs (state trigger) or None (homeassistant_started).
    subscriptions: dict[int, frozenset[str] | None] = field(default_factory=dict)


class FakeHomeAssistant:
    def __init__(self, *, trigger_key: str = "trigger") -> None:
        # Which key names the trigger type: "trigger" (HA 2024.10+) or "platform" (older).
        self.trigger_key = trigger_key
        self.states: dict[str, dict[str, Any]] = {}
        # Every request that changed a state: (method, entity ID, body).
        self.writes: list[tuple[str, str, Any]] = []
        # Recorded states per entity: (last_changed, state); set() adds to it.
        self.history: dict[str, list[tuple[datetime, str]]] = {}
        # Path start and query of every history request.
        self.history_requests: list[dict[str, str]] = []
        # Home location for /api/config; None: not set.
        self.location: tuple[float, float] | None = (52.52, 13.405)
        # Hourly forecast per weather entity for weather.get_forecasts.
        self.forecasts: dict[str, list[dict[str, Any]]] = {}
        # Every service call: (domain.service, body, query).
        self.service_calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        # domain.service -> HTTP status to answer instead of carrying the call out.
        self.service_status: dict[str, int] = {}
        # Long-term statistics per entity: rows with "start" (datetime) and mean/min/max/change.
        self.statistics: dict[str, list[dict[str, Any]]] = {}
        # Every statistics request: (statistic IDs, start, end).
        self.statistics_requests: list[tuple[list[str], datetime, datetime]] = []
        # Open-Meteo stand-in: hourly values {"time": [unix seconds], "temperature_2m": [...], ...}.
        self.open_meteo: dict[str, list[Any]] | None = None
        self.open_meteo_requests: list[dict[str, str]] = []
        # Climate entity -> the setpoint of its time program (shown again after a quick veto).
        self.programs: dict[str, float] = {}
        # Seconds the Vaillant cloud lags behind: set, a device service shows the new value at
        # once, falls back to the old one before it returns (the integration's refresh still
        # gets the cloud's old data) and shows the new one again after this long.
        self.cloud_lag: float | None = None
        self.connections = 0
        self.refuse_auth = False
        self._sockets: list[_Socket] = []
        self._runner: web.AppRunner | None = None
        self.url = ""

    async def start(self, port: int = 0) -> str:
        app = web.Application()
        app.router.add_get("/api/states", self._get_states)
        app.router.add_get("/api/states/{entity_id}", self._get_state)
        app.router.add_post("/api/states/{entity_id}", self._post_state)
        app.router.add_delete("/api/states/{entity_id}", self._delete_state)
        app.router.add_get("/api/history/period/{start}", self._get_history)
        app.router.add_get("/api/config", self._get_config)
        app.router.add_post("/api/services/{domain}/{service}", self._call_service)
        app.router.add_get("/websocket", self._websocket)
        app.router.add_get("/v1/dwd-icon", self._open_meteo)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", port).start()
        self.url = f"http://127.0.0.1:{self._runner.addresses[0][1]}"
        return self.url

    async def stop(self) -> None:
        await self._close_sockets()
        if self._runner is not None:
            await self._runner.cleanup()

    # --- test controls -----------------------------------------------------------

    def set(
        self,
        entity_id: str,
        state: str,
        *,
        unit: str | None = None,
        name: str | None = None,
        reported: datetime | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Set an entity's state as an integration would, and notify subscribers."""
        old = self.states.get(entity_id)
        attributes = dict(attributes or {})
        if unit is not None:
            attributes["unit_of_measurement"] = unit
        if name is not None:
            attributes["friendly_name"] = name
        at = reported or datetime.now(UTC)
        moment = at.isoformat()
        new = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
            "last_changed": moment,
            "last_updated": moment,
            "last_reported": moment,
        }
        self.states[entity_id] = new
        if old is None or (old["state"], old["attributes"]) != (state, attributes):
            self._notify(entity_id, old, new)
        if old is None or old["state"] != state:
            self.history.setdefault(entity_id, []).append((at, state))

    def add_myvaillant(self) -> None:
        """The myVAILLANT integration's entities: zone 1 in auto, zone 2 in manual at 23 °C."""
        zone = {
            "hvac_modes": ["off", "heat_cool", "auto"],
            "preset_modes": ["boost", "none", "away", "system_off", "ventilation_boost"],
            "preset_mode": "none",
            "min_temp": 7,
            "max_temp": 35,
            "target_temp_step": 0.5,
            "time_program_heating": {"monday": []},
            "quick_veto_start_date_time": None,
            "quick_veto_end_date_time": None,
            "holiday_start_date_time": None,
            "holiday_end_date_time": None,
        }
        self.programs[MV_ZONE_1] = 21.5
        self.set(
            MV_ZONE_1,
            "auto",
            name="Zuhause Zone 1 (Circuit 0) Climate",
            attributes={**zone, "temperature": 21.5, "current_temperature": 21.0},
        )
        self.set(
            MV_ZONE_2,
            "heat_cool",
            name="Zuhause Zone 2 (Circuit 1) Climate",
            attributes={**zone, "temperature": 23.0, "current_temperature": 22.4},
        )
        self.set(
            MV_HOT_WATER,
            "Time Controlled",
            name="Zuhause Domestic Hot Water 0",
            attributes={
                "operation_list": list(_DHW_OPERATIONS),
                "operation_mode": "Time Controlled",
                "temperature": 50,
                "current_temperature": 47.5,
                "min_temp": 35,
                "max_temp": 70,
                "time_program_dhw": {"monday": []},
            },
        )
        self.set(MV_OUTDOOR, "4.5", unit="°C", name="Zuhause Outdoor Temperature")
        self.set(MV_POWER, "850", unit="W", name="Zuhause Device 0 aroTHERM Split plus Current Power")

    def change(self, entity_id: str, state: str | None = None, **attributes: Any) -> None:
        """Change an entity's state or some of its attributes, keeping the rest."""
        old = self.states[entity_id]
        self.set(entity_id, old["state"] if state is None else state, attributes={**old["attributes"], **attributes})

    def remove(self, entity_id: str) -> None:
        old = self.states.pop(entity_id, None)
        if old is not None:
            self._notify(entity_id, old, None)

    async def restart(self, *, keep: Iterable[str] = ()) -> None:
        """Drop every connection and every state except keep, as a restart of HA does."""
        await self._close_sockets()
        kept = frozenset(keep)
        self.states = {entity_id: state for entity_id, state in self.states.items() if entity_id in kept}

    async def started(self) -> None:
        """Fire homeassistant_started to the subscribers."""
        for socket in list(self._sockets):
            for subscription, watched in socket.subscriptions.items():
                if watched is None:
                    await socket.ws.send_json(
                        {"id": subscription, "type": "event", "event": {"event_type": "homeassistant_started"}}
                    )

    def own_states(self) -> dict[str, dict[str, Any]]:
        return {entity_id: state for entity_id, state in self.states.items() if "vaillant_eebus_" in entity_id}

    # --- REST ------------------------------------------------------------------

    def _authorized(self, request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def _get_states(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        return web.json_response(list(self.states.values()))

    async def _get_state(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        state = self.states.get(request.match_info["entity_id"])
        if state is None:
            return web.json_response({"message": "Entity not found."}, status=404)
        return web.json_response(state)

    async def _post_state(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        entity_id = request.match_info["entity_id"]
        body = await request.json()
        self.writes.append(("POST", entity_id, body))
        existed = entity_id in self.states
        self.set(entity_id, str(body["state"]))
        self.states[entity_id]["attributes"] = dict(body.get("attributes") or {})
        return web.json_response(self.states[entity_id], status=200 if existed else 201)

    async def _delete_state(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        entity_id = request.match_info["entity_id"]
        self.writes.append(("DELETE", entity_id, None))
        if self.states.pop(entity_id, None) is None:
            return web.json_response({"message": "Entity not found."}, status=404)
        return web.json_response({"message": "Entity removed."})

    async def _get_history(self, request: web.Request) -> web.Response:
        """The state at start and every change until end_time, as with minimal_response."""
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        self.history_requests.append({"start": request.match_info["start"], **request.query})
        start = datetime.fromisoformat(request.match_info["start"])
        end = datetime.fromisoformat(request.query["end_time"]) if "end_time" in request.query else start + timedelta(1)
        entity_id = request.query.get("filter_entity_id", "")
        changes = sorted(self.history.get(entity_id, []))
        earlier = [state for moment, state in changes if moment <= start]
        items = ([(start, earlier[-1])] if earlier else []) + [change for change in changes if start < change[0] <= end]
        if not items:
            return web.json_response([])
        first_moment, first_state = items[0]
        rows: list[dict[str, Any]] = [
            {"entity_id": entity_id, "state": first_state, "last_changed": first_moment.isoformat()}
        ]
        rows += [{"state": state, "last_changed": moment.isoformat()} for moment, state in items[1:]]
        return web.json_response([rows])

    async def _open_meteo(self, request: web.Request) -> web.Response:
        self.open_meteo_requests.append(dict(request.query))
        if self.open_meteo is None:
            return web.json_response({"error": True, "reason": "not available"}, status=400)
        return web.json_response({"latitude": 52.52, "longitude": 13.4, "hourly": self.open_meteo})

    async def _get_config(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        location = self.location
        return web.json_response(
            {
                "latitude": location[0] if location else 0,
                "longitude": location[1] if location else 0,
                "time_zone": "Europe/Berlin",
            }
            if location
            else {"time_zone": "Europe/Berlin"}
        )

    async def _call_service(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"message": "Unauthorized"}, status=401)
        service = f"{request.match_info['domain']}.{request.match_info['service']}"
        body = await request.json()
        self.service_calls.append((service, body, dict(request.query)))
        if service in self.service_status:
            return web.json_response({"message": "Failed"}, status=self.service_status[service])
        if service in _DEVICE_SERVICES:
            entity_id = body.get("entity_id")
            if entity_id not in self.states:
                return web.json_response({"message": "Entity not found"}, status=400)
            before = copy.deepcopy(self.states[entity_id])
            self._device_service(service, body)
            if self.cloud_lag is not None:
                after = copy.deepcopy(self.states[entity_id])
                await asyncio.sleep(0.05)
                self._restore(entity_id, before)
                asyncio.get_running_loop().call_later(self.cloud_lag, self._restore, entity_id, after)
            return web.json_response([])
        if service != "weather.get_forecasts" or "return_response" not in request.query:
            return web.json_response({"message": "Service does not support responses"}, status=400)
        entity_id = body.get("entity_id")
        if entity_id not in self.forecasts:
            return web.json_response({"message": "Entity not found"}, status=400)
        return web.json_response(
            {"changed_states": [], "service_response": {entity_id: {"forecast": self.forecasts[entity_id]}}}
        )

    def _device_service(self, service: str, body: dict[str, Any]) -> None:
        """What the myVAILLANT integration does with the call (its local data, refreshed later)."""
        entity_id = body["entity_id"]
        state = self.states[entity_id]["state"]
        now = datetime.now(UTC)
        match service:
            case "climate.set_temperature" if state == "heat_cool":
                self.change(entity_id, temperature=body["temperature"])
            case "climate.set_temperature":
                end = now + timedelta(hours=QUICK_VETO_HOURS)
                self._quick_veto(entity_id, float(body["temperature"]), end)
            case "mypyllant.set_quick_veto":
                end = now + timedelta(hours=float(body.get("duration_hours") or QUICK_VETO_HOURS))
                self._quick_veto(entity_id, float(body["temperature"]), end)
            case "mypyllant.cancel_quick_veto":
                self.change(
                    entity_id,
                    preset_mode="none",
                    temperature=self.programs.get(entity_id, 20.0),
                    quick_veto_start_date_time=None,
                    quick_veto_end_date_time=None,
                )
            case "climate.set_hvac_mode":
                self.change(entity_id, body["hvac_mode"], preset_mode="none", quick_veto_end_date_time=None)
            case "water_heater.set_temperature":
                self.change(entity_id, temperature=int(body["temperature"]))
            case "water_heater.set_operation_mode":
                operation = body["operation_mode"]
                attributes = {} if operation == "Cylinder Boost" else {"operation_mode": operation}
                self.change(entity_id, operation, **attributes)

    def _restore(self, entity_id: str, saved: dict[str, Any]) -> None:
        self.set(entity_id, saved["state"], attributes=saved["attributes"])

    def _quick_veto(self, entity_id: str, celsius: float, end: datetime) -> None:
        self.change(
            entity_id,
            preset_mode="boost",
            temperature=celsius,
            quick_veto_start_date_time=datetime.now(UTC).isoformat(),
            quick_veto_end_date_time=end.isoformat(),
        )

    # --- WebSocket ---------------------------------------------------------------

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.10.0"})
        auth = await ws.receive_json()
        if self.refuse_auth or auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.10.0"})
        socket = _Socket(ws)
        self._sockets.append(socket)
        self.connections += 1
        try:
            async for message in ws:
                if message.type is WSMsgType.TEXT:
                    await self._command(socket, message.json())
        finally:
            with contextlib.suppress(ValueError):
                self._sockets.remove(socket)
        return ws

    async def _command(self, socket: _Socket, command: dict[str, Any]) -> None:
        command_id = command["id"]
        kind = command["type"]
        if kind == "subscribe_events" and command.get("event_type") == "homeassistant_started":
            socket.subscriptions[command_id] = None
        elif kind == "subscribe_trigger":
            trigger = command["trigger"]
            if trigger.get(self.trigger_key) != "state":
                await socket.ws.send_json(
                    {
                        "id": command_id,
                        "type": "result",
                        "success": False,
                        "error": {"code": "invalid_format", "message": "Invalid trigger"},
                    }
                )
                return
            entity_ids = trigger["entity_id"]
            socket.subscriptions[command_id] = frozenset([entity_ids] if isinstance(entity_ids, str) else entity_ids)
        elif kind == "unsubscribe_events":
            socket.subscriptions.pop(command["subscription"], None)
        elif kind == "recorder/statistics_during_period":
            start = datetime.fromisoformat(command["start_time"])
            end = datetime.fromisoformat(command["end_time"])
            ids = list(command["statistic_ids"])
            self.statistics_requests.append((ids, start, end))
            result = {
                entity_id: [
                    {
                        **{key: value for key, value in row.items() if key != "start"},
                        "start": row["start"].timestamp() * 1000,
                        "end": (row["start"] + timedelta(hours=1)).timestamp() * 1000,
                    }
                    for row in self.statistics[entity_id]
                    if start <= row["start"] < end
                ]
                for entity_id in ids
                if entity_id in self.statistics
            }
            await socket.ws.send_json({"id": command_id, "type": "result", "success": True, "result": result})
            return
        else:
            await socket.ws.send_json(
                {
                    "id": command_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown command."},
                }
            )
            return
        await socket.ws.send_json({"id": command_id, "type": "result", "success": True, "result": None})

    def _notify(self, entity_id: str, old: dict[str, Any] | None, new: dict[str, Any] | None) -> None:
        for socket in list(self._sockets):
            for subscription, watched in socket.subscriptions.items():
                if watched is not None and entity_id in watched:
                    event = {
                        "id": subscription,
                        "type": "event",
                        "event": {
                            "variables": {
                                "trigger": {
                                    "platform": "state",
                                    "entity_id": entity_id,
                                    "from_state": old,
                                    "to_state": new,
                                }
                            },
                            "context": None,
                        },
                    }
                    asyncio.get_running_loop().create_task(socket.ws.send_json(event))

    async def _close_sockets(self) -> None:
        for socket in list(self._sockets):
            await socket.ws.close()
        self._sockets.clear()


_DEVICE_SERVICES = frozenset(
    {
        "climate.set_temperature",
        "climate.set_hvac_mode",
        "water_heater.set_temperature",
        "water_heater.set_operation_mode",
        "mypyllant.set_quick_veto",
        "mypyllant.cancel_quick_veto",
    }
)


async def start_fake_home_assistant(**kwargs: Any) -> FakeHomeAssistant:
    fake = FakeHomeAssistant(**kwargs)
    await fake.start()
    return fake
