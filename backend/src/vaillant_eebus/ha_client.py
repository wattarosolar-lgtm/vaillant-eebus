"""Access to Home Assistant Core through the Supervisor (spec 6.3).

The app reads the states of the entities the user chose and reports its own
sensors: set_state() and remove_state() refuse every entity outside this
app's prefix. It calls four kinds of services: weather.get_forecasts, which
reads a weather forecast for the energy management; with the connection
myVAILLANT the few services in DEVICE_SERVICES that control the heat pump
through the myVAILLANT integration's entities; ROOM_SERVICES, the one
service that sets a radiator thermostat's setpoint; and mqtt.publish with one
fixed message that asks Zigbee2MQTT to read an Aqara thermostat's valve
position (1.4.2). All but the forecast are called only by the write gate
(write_gate.py, for the heat pump through myvaillant.py; tests check it). A
template renders a device's name (read only).

One WebSocket connection carries the state changes of the watched entities
and the "homeassistant_started" event, and answers the reading of the
recorder's long-term statistics (hourly means and changes, read only); the
REST API serves single states, the history of an entity and the app's own
sensors. Watched entities are also read again every few minutes, because a
value reported unchanged moves only last_reported, which triggers no event.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from vaillant_eebus.bridge_client import Backoff
from vaillant_eebus.ids import (
    CLIMATE_ENTITY,
    MQTT_BASE_TOPIC,
    OWN_ENTITY,
    WATER_HEATER_ENTITY,
    device_name_problem,
)
from vaillant_eebus.log_store import LogCategory, LogStore

_LOGGER = logging.getLogger(__name__)

# Home Assistant Core behind the Supervisor's proxy; the token comes from SUPERVISOR_TOKEN.
SUPERVISOR_CORE_URL = "http://supervisor/core"
# The only entities this app ever writes are OWN_ENTITY (ids.py).
# Entities whose forecast the app may ask for.
WEATHER_ENTITY = re.compile(r"^weather\.[a-z0-9_]+$")
# The services that control the heat pump through the myVAILLANT integration,
# and the entities each one may target. No other service changes anything.
DEVICE_SERVICES: dict[tuple[str, str], re.Pattern[str]] = {
    ("climate", "set_temperature"): CLIMATE_ENTITY,
    ("climate", "set_hvac_mode"): CLIMATE_ENTITY,
    ("water_heater", "set_temperature"): WATER_HEATER_ENTITY,
    ("water_heater", "set_operation_mode"): WATER_HEATER_ENTITY,
    ("mypyllant", "set_quick_veto"): CLIMATE_ENTITY,
    ("mypyllant", "cancel_quick_veto"): CLIMATE_ENTITY,
}
# The one service that controls a room: a radiator thermostat's setpoint (Zigbee2MQTT climate entity,
# only the field "temperature"). Which thermostats may be targeted is the write gate's business.
ROOM_SERVICES: dict[tuple[str, str], re.Pattern[str]] = {
    ("climate", "set_temperature"): CLIMATE_ENTITY,
}
ROOM_SERVICE_FIELDS = frozenset({"temperature"})
# The one MQTT message this app sends (1.4.2): Zigbee2MQTT reads an Aqara W600's valve position (attribute
# 0x0360, exposed as running_state with a "get"). Only to "<base topic>/<device name>/get", only this payload,
# never retained; the room gate sends it.
POSITION_READ_SERVICE = ("mqtt", "publish")
POSITION_READ_PAYLOAD = '{"running_state": ""}'
# Seconds between reading the watched entities again.
REFRESH_INTERVAL = 300.0
# Seconds to wait for an answer of Home Assistant.
REQUEST_TIMEOUT = 10.0
# Reading a month of hourly statistics can take a while on a small machine.
STATISTICS_TIMEOUT = 60.0
# Statistics are read a month at a time; the answers can be large.
WS_MAX_MESSAGE = 32 * 1024 * 1024
# A device service waits for the Vaillant cloud and the integration's refresh after it.
DEVICE_SERVICE_TIMEOUT = 30.0
# A thermostat's setpoint only goes to the MQTT broker.
ROOM_SERVICE_TIMEOUT = 15.0
# Answers that mean Home Assistant refused the call before the integration acted.
_REFUSED = frozenset({400, 401, 403, 404, 405})


class HomeAssistantUnavailable(Exception):
    """Home Assistant cannot be reached or refused the request."""


class ServiceCallFailed(Exception):
    """A device or room service failed.

    executed is False when the command certainly did not reach the device
    (Home Assistant refused it or was not reachable), None when that is unknown
    (an error of the integration or the cloud, or no answer in time).
    """

    def __init__(self, message: str, *, executed: bool | None, code: str) -> None:
        super().__init__(message)
        self.executed = executed
        # Short reason for the protocol, e.g. "HTTP 500" or "TIMEOUT".
        self.code = code


@dataclass(frozen=True, slots=True)
class StatisticRow:
    """One hour (or day) of the recorder's long-term statistics."""

    start: datetime
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    # Increase of a counter within the period (energy sensors).
    change: float | None = None


@dataclass(frozen=True, slots=True)
class EntityState:
    entity_id: str
    state: str
    attributes: Mapping[str, Any]
    # Last time the integration reported the value, also when it did not change (HA 2024.3+).
    last_reported: datetime | None

    @classmethod
    def from_json(cls, data: Any) -> EntityState | None:
        if not isinstance(data, dict) or not isinstance(data.get("entity_id"), str):
            return None
        attributes = data.get("attributes")
        return cls(
            entity_id=data["entity_id"],
            state=str(data.get("state", "")),
            attributes=attributes if isinstance(attributes, dict) else {},
            last_reported=_time(data.get("last_reported")) or _time(data.get("last_updated")),
        )


class HomeAssistantListener:
    """Receives what the client learns; every method is optional."""

    def ha_connected(self) -> None:
        """Connected (again): Home Assistant may have restarted in between."""

    def ha_disconnected(self) -> None:
        """The connection is lost; known states may be outdated."""

    def ha_started(self) -> None:
        """Home Assistant finished starting; states set before may be gone."""

    def ha_state(self, entity_id: str, state: EntityState | None) -> None:
        """A watched entity changed; None if it no longer exists."""


class HomeAssistantClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        log: LogStore | None = None,
        backoff: Backoff | None = None,
        refresh_interval: float = REFRESH_INTERVAL,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._log = log
        self._backoff = backoff or Backoff(initial=2.0, maximum=60.0)
        self._refresh_interval = refresh_interval
        self._session: aiohttp.ClientSession | None = None
        self._listeners: list[HomeAssistantListener] = []
        self._watched: frozenset[str] = frozenset()
        self._resubscribe = asyncio.Event()
        self._results: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._connected = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def __aenter__(self) -> HomeAssistantClient:
        # No session-wide timeout: it would also cut the long-lived WebSocket.
        self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self._token}"})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._session is not None:
            await self._session.close()

    @property
    def connected(self) -> bool:
        return self._connected

    def add_listener(self, listener: HomeAssistantListener) -> None:
        self._listeners.append(listener)

    def watch(self, entity_ids: Iterable[str]) -> None:
        """Follow these entities from now on (replaces the earlier set)."""
        watched = frozenset(entity_ids)
        if watched != self._watched:
            self._watched = watched
            self._resubscribe.set()

    # --- REST ------------------------------------------------------------------

    async def states(self) -> list[EntityState]:
        """All states, e.g. for choosing entities."""
        data = await self._rest("GET", "/api/states")
        if not isinstance(data, list):
            raise HomeAssistantUnavailable("unexpected answer to /api/states")
        return [state for item in data if (state := EntityState.from_json(item)) is not None]

    async def state(self, entity_id: str) -> EntityState | None:
        """The entity's state; None if Home Assistant does not know it."""
        return EntityState.from_json(await self._rest("GET", f"/api/states/{entity_id}", missing_ok=True))

    async def set_state(self, entity_id: str, state: str, attributes: Mapping[str, Any]) -> None:
        """Create or update one of this app's own sensors."""
        _check_own(entity_id)
        await self._rest("POST", f"/api/states/{entity_id}", body={"state": state, "attributes": dict(attributes)})

    async def remove_state(self, entity_id: str) -> None:
        """Remove one of this app's own sensors (the user stopped exporting it)."""
        _check_own(entity_id)
        await self._rest("DELETE", f"/api/states/{entity_id}", missing_ok=True)

    async def location(self) -> tuple[float, float] | None:
        """Home Assistant's home location (latitude, longitude); None if it is not set."""
        data = await self._rest("GET", "/api/config")
        if not isinstance(data, dict):
            raise HomeAssistantUnavailable("unexpected answer to /api/config")
        latitude, longitude = data.get("latitude"), data.get("longitude")
        if isinstance(latitude, int | float) and isinstance(longitude, int | float):
            return float(latitude), float(longitude)
        return None

    async def hourly_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        """The hourly forecast of a weather entity.

        weather.get_forecasts reads and changes nothing; apart from it the app
        calls only DEVICE_SERVICES and ROOM_SERVICES (a test checks that no other
        service is called).
        """
        if not WEATHER_ENTITY.match(entity_id):
            raise ValueError(f"{entity_id!r} is not a weather entity")
        data = await self._rest(
            "POST",
            "/api/services/weather/get_forecasts",
            body={"entity_id": entity_id, "type": "hourly"},
            # Flag: Home Assistant only checks that it is present.
            params={"return_response": ""},
        )
        response = data.get("service_response") if isinstance(data, dict) else None
        entry = response.get(entity_id) if isinstance(response, dict) else None
        forecast = entry.get("forecast") if isinstance(entry, dict) else None
        if not isinstance(forecast, list):
            raise HomeAssistantUnavailable(f"no hourly forecast from {entity_id}")
        return [item for item in forecast if isinstance(item, dict)]

    async def call_device_service(self, domain: str, service: str, entity_id: str, data: Mapping[str, Any]) -> None:
        """Call one of DEVICE_SERVICES on a myVAILLANT entity; only the write gate does this.

        Raises ValueError for any other service or entity, ServiceCallFailed when the call failed.
        """
        pattern = DEVICE_SERVICES.get((domain, service))
        if pattern is None:
            raise ValueError(f"{domain}.{service} is not a service this app may call")
        if not pattern.match(entity_id):
            raise ValueError(f"{domain}.{service} may not target {entity_id!r}")
        await self._call_service(domain, service, entity_id, data, limit=DEVICE_SERVICE_TIMEOUT)

    async def call_room_service(self, domain: str, service: str, entity_id: str, data: Mapping[str, Any]) -> None:
        """Call one of ROOM_SERVICES on a radiator thermostat; only the write gate does this.

        Only the fields in ROOM_SERVICE_FIELDS with a finite number. Raises ValueError
        for anything else, ServiceCallFailed when the call failed.
        """
        pattern = ROOM_SERVICES.get((domain, service))
        if pattern is None:
            raise ValueError(f"{domain}.{service} is not a room service")
        if not pattern.match(entity_id):
            raise ValueError(f"{domain}.{service} may not target {entity_id!r}")
        if set(data) != ROOM_SERVICE_FIELDS or not all(
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
            for value in data.values()
        ):
            raise ValueError(f"{domain}.{service}: only a temperature may be set, not {dict(data)!r}")
        await self._call_service(domain, service, entity_id, data, limit=ROOM_SERVICE_TIMEOUT)

    async def publish_position_read(self, base_topic: str, device: str) -> None:
        """Ask Zigbee2MQTT to read a thermostat's valve position; only the room gate does this.

        Publishes POSITION_READ_PAYLOAD to position_read_topic(). Raises ValueError for an
        invalid base topic or device name, ServiceCallFailed when the call failed.
        """
        topic = position_read_topic(base_topic, device)
        domain, service = POSITION_READ_SERVICE
        body = {"topic": topic, "payload": POSITION_READ_PAYLOAD, "qos": 0, "retain": False}
        await self._post_service(domain, service, body, limit=ROOM_SERVICE_TIMEOUT)

    async def device_name(self, entity_id: str) -> str | None:
        """The name of the device a thermostat belongs to, as its integration named it (Zigbee2MQTT: the
        device's friendly name, not a name given in Home Assistant); None without a device.

        Renders a template, which reads only. Raises HomeAssistantUnavailable.
        """
        if not CLIMATE_ENTITY.match(entity_id):
            raise ValueError(f"{entity_id!r} is not a climate entity")
        template = "{{ device_attr(device_id('" + entity_id + "'), 'name') }}"
        text = await self._rest("POST", "/api/template", body={"template": template}, text=True)
        name = str(text).strip()
        return None if name in ("", "None") else name

    async def _call_service(
        self, domain: str, service: str, entity_id: str, data: Mapping[str, Any], *, limit: float
    ) -> None:
        await self._post_service(domain, service, {"entity_id": entity_id, **data}, limit=limit)

    async def _post_service(self, domain: str, service: str, body: Mapping[str, Any], *, limit: float) -> None:
        if self._session is None:
            raise ServiceCallFailed("client not started", executed=False, code="NOT_STARTED")
        try:
            async with self._session.post(
                f"{self._url}/api/services/{domain}/{service}",
                json=dict(body),
                timeout=aiohttp.ClientTimeout(total=limit),
            ) as response:
                if response.status in _REFUSED:
                    raise ServiceCallFailed(
                        f"{domain}.{service}: HTTP {response.status}", executed=False, code=f"HTTP {response.status}"
                    )
                if response.status >= 400:
                    raise ServiceCallFailed(
                        f"{domain}.{service}: HTTP {response.status}", executed=None, code=f"HTTP {response.status}"
                    )
        except aiohttp.ClientConnectorError as err:
            raise ServiceCallFailed(f"{domain}.{service}: {err!r}", executed=False, code="NOT_REACHABLE") from err
        except TimeoutError as err:
            raise ServiceCallFailed(f"{domain}.{service}: no answer in time", executed=None, code="TIMEOUT") from err
        except aiohttp.ClientError as err:
            raise ServiceCallFailed(f"{domain}.{service}: {err!r}", executed=None, code="CONNECTION_LOST") from err

    async def history(self, entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
        """The entity's state at start and every change until end, oldest first (spec 6.3)."""
        data = await self._rest(
            "GET",
            f"/api/history/period/{_api_time(start)}",
            params={
                "filter_entity_id": entity_id,
                "end_time": _api_time(end),
                # Flags: Home Assistant only checks that they are present.
                "minimal_response": "",
                "no_attributes": "",
                "significant_changes_only": "0",
            },
        )
        if not isinstance(data, list):
            raise HomeAssistantUnavailable("unexpected answer to /api/history/period")
        changes = []
        for item in data[0] if data and isinstance(data[0], list) else []:
            moment = _time(item.get("last_changed")) if isinstance(item, dict) else None
            if moment is not None:
                changes.append((moment, str(item.get("state", ""))))
        return changes

    async def _rest(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, str] | None = None,
        missing_ok: bool = False,
        text: bool = False,
    ) -> Any:
        if self._session is None:
            raise HomeAssistantUnavailable("client not started")
        try:
            async with self._session.request(
                method,
                self._url + path,
                json=body,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status == 404 and missing_ok:
                    return None
                if response.status >= 400:
                    raise HomeAssistantUnavailable(f"{method} {path}: HTTP {response.status}")
                if text:
                    return await response.text()
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise HomeAssistantUnavailable(f"{method} {path}: {err!r}") from err

    # --- WebSocket -------------------------------------------------------------

    async def run(self) -> None:
        """Keep the WebSocket connection up and hand changes to the listeners."""
        delays = self._backoff.delays()
        while True:
            try:
                await self._connection()
            except (aiohttp.ClientError, HomeAssistantUnavailable, TimeoutError, ConnectionError) as err:
                _LOGGER.debug("Home Assistant connection ended: %r", err)
                if not self._connected:
                    _LOGGER.warning("Home Assistant not reachable (%s), trying again", err)
            if self._connected:
                self._connected = False
                _LOGGER.warning("Connection to Home Assistant lost")
                self._record("Verbindung zu Home Assistant verloren; die App versucht es weiter.", problem=True)
                for listener in self._listeners:
                    listener.ha_disconnected()
                delays = self._backoff.delays()
            await asyncio.sleep(next(delays))

    async def _connection(self) -> None:
        if self._session is None:
            raise HomeAssistantUnavailable("client not started")
        ws_url = re.sub(r"^http", "ws", self._url) + "/websocket"
        async with asyncio.timeout(REQUEST_TIMEOUT):
            ws = await self._session.ws_connect(ws_url, heartbeat=30, max_msg_size=WS_MAX_MESSAGE)
        async with ws:
            await self._authenticate(ws)
            reader = asyncio.create_task(self._read(ws))
            self._ws = ws
            try:
                await self._command(ws, {"type": "subscribe_events", "event_type": "homeassistant_started"})
                await self._follow(ws, reader)
            finally:
                self._ws = None
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
                for future in self._results.values():
                    future.cancel()
                self._results.clear()

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        hello = await ws.receive_json(timeout=REQUEST_TIMEOUT)
        if hello.get("type") != "auth_required":
            raise HomeAssistantUnavailable(f"unexpected greeting {hello.get('type')!r}")
        await ws.send_json({"type": "auth", "access_token": self._token})
        answer = await ws.receive_json(timeout=REQUEST_TIMEOUT)
        if answer.get("type") != "auth_ok":
            raise HomeAssistantUnavailable(f"authentication refused: {answer.get('message', answer.get('type'))}")

    async def _follow(self, ws: aiohttp.ClientWebSocketResponse, reader: asyncio.Task[None]) -> None:
        """Subscribe to the watched entities; again whenever the set changes."""
        subscription: int | None = None
        while True:
            self._resubscribe.clear()
            watched = self._watched
            if subscription is not None:
                await self._command(ws, {"type": "unsubscribe_events", "subscription": subscription})
                subscription = None
            if watched:
                subscription = await self._subscribe_states(ws, watched)
            await self._refresh(watched)
            if not self._connected:
                # Only now, with the watched values read: listeners see a complete picture.
                self._connected = True
                _LOGGER.info("Connected to Home Assistant")
                self._record("Mit Home Assistant verbunden.")
                for listener in self._listeners:
                    listener.ha_connected()
            resubscribe = asyncio.create_task(self._resubscribe.wait())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {reader, resubscribe}, timeout=self._refresh_interval, return_when=asyncio.FIRST_COMPLETED
                    )
                    if reader in done:
                        reader.result()
                        raise HomeAssistantUnavailable("connection closed")
                    if resubscribe in done:
                        break
                    await self._refresh(self._watched)
            finally:
                resubscribe.cancel()

    async def _subscribe_states(self, ws: aiohttp.ClientWebSocketResponse, entity_ids: frozenset[str]) -> int:
        # "trigger" names the trigger type since HA 2024.10; older versions only know "platform".
        last: HomeAssistantUnavailable | None = None
        for key in ("trigger", "platform"):
            trigger = {key: "state", "entity_id": sorted(entity_ids)}
            try:
                return await self._command(ws, {"type": "subscribe_trigger", "trigger": trigger})
            except HomeAssistantUnavailable as err:
                last = err
        assert last is not None
        raise last

    async def _command(self, ws: aiohttp.ClientWebSocketResponse, payload: dict[str, Any]) -> int:
        """Send a command and wait for its result; returns the command's ID."""
        command_id, _ = await self._exchange(ws, payload, REQUEST_TIMEOUT)
        return command_id

    async def _exchange(
        self, ws: aiohttp.ClientWebSocketResponse, payload: dict[str, Any], seconds: float
    ) -> tuple[int, Any]:
        """Send a command and wait for its result; returns the command's ID and the result."""
        command_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._results[command_id] = future
        try:
            await ws.send_json({"id": command_id, **payload})
            async with asyncio.timeout(seconds):
                result = await future
        finally:
            self._results.pop(command_id, None)
        if not result.get("success"):
            error = result.get("error") or {}
            raise HomeAssistantUnavailable(f"{payload['type']} refused: {error.get('message', error)}")
        return command_id, result.get("result")

    async def statistics(
        self, statistic_ids: Iterable[str], start: datetime, end: datetime, *, period: str = "hour"
    ) -> dict[str, list[StatisticRow]]:
        """The recorder's long-term statistics of these entities between start and end.

        Reads only (recorder/statistics_during_period). Entities without
        long-term statistics are missing from the answer.
        """
        ws = self._ws
        if ws is None or not self._connected:
            raise HomeAssistantUnavailable("not connected")
        ids = sorted(set(statistic_ids))
        if not ids:
            return {}
        try:
            _, result = await self._exchange(
                ws,
                {
                    "type": "recorder/statistics_during_period",
                    "start_time": start.astimezone(UTC).isoformat(),
                    "end_time": end.astimezone(UTC).isoformat(),
                    "statistic_ids": ids,
                    "period": period,
                    "types": ["mean", "min", "max", "change"],
                },
                STATISTICS_TIMEOUT,
            )
        except (TimeoutError, aiohttp.ClientError, ConnectionError) as err:
            raise HomeAssistantUnavailable(f"statistics: {err!r}") from err
        if not isinstance(result, dict):
            raise HomeAssistantUnavailable("unexpected answer to recorder/statistics_during_period")
        rows: dict[str, list[StatisticRow]] = {}
        for statistic_id, items in result.items():
            if statistic_id not in ids or not isinstance(items, list):
                continue
            parsed = [row for item in items if (row := _statistic_row(item)) is not None]
            rows[statistic_id] = sorted(parsed, key=lambda row: row.start)
        return rows

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for message in ws:
            if message.type is not aiohttp.WSMsgType.TEXT:
                continue
            data = message.json()
            if not isinstance(data, dict):
                continue
            if data.get("type") == "result":
                future = self._results.pop(data.get("id", -1), None)
                if future is not None and not future.done():
                    future.set_result(data)
            elif data.get("type") == "event":
                self._dispatch(data.get("event") or {})

    def _dispatch(self, event: dict[str, Any]) -> None:
        if event.get("event_type") == "homeassistant_started":
            _LOGGER.info("Home Assistant started")
            for listener in self._listeners:
                listener.ha_started()
            return
        trigger = (event.get("variables") or {}).get("trigger") or {}
        entity_id = trigger.get("entity_id")
        if isinstance(entity_id, str) and entity_id in self._watched:
            state = EntityState.from_json(trigger.get("to_state"))
            for listener in self._listeners:
                listener.ha_state(entity_id, state)

    async def _refresh(self, entity_ids: frozenset[str]) -> None:
        async def one(entity_id: str) -> None:
            try:
                state = await self.state(entity_id)
            except HomeAssistantUnavailable as err:
                _LOGGER.debug("Reading %s failed: %s", entity_id, err)
                return
            for listener in self._listeners:
                listener.ha_state(entity_id, state)

        await asyncio.gather(*(one(entity_id) for entity_id in sorted(entity_ids)))

    def _record(self, message: str, *, problem: bool = False) -> None:
        if self._log is not None:
            self._log.add(LogCategory.CONNECTION, message, problem=problem)


def _statistic_row(item: Any) -> StatisticRow | None:
    if not isinstance(item, dict):
        return None
    start = item.get("start")
    # Milliseconds since 1970 (HA 2023.3+), earlier an ISO string.
    moment = (
        datetime.fromtimestamp(start / 1000, UTC)
        if isinstance(start, int | float) and not isinstance(start, bool)
        else _time(start)
    )
    if moment is None:
        return None

    def number(key: str) -> float | None:
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value) if value == value and abs(value) != float("inf") else None

    return StatisticRow(
        start=moment, mean=number("mean"), min=number("min"), max=number("max"), change=number("change")
    )


def position_read_topic(base_topic: str, device: str) -> str:
    """The topic that makes Zigbee2MQTT read a device's state; ValueError for anything that is not
    "<base topic>/<device name>/get" with a plain base topic and a safe name (ids.device_name_problem)."""
    if not MQTT_BASE_TOPIC.match(base_topic):
        raise ValueError(f"{base_topic!r} is not an MQTT base topic")
    problem = device_name_problem(device)
    if problem is not None:
        raise ValueError(problem)
    return f"{base_topic}/{device}/get"


def _check_own(entity_id: str) -> None:
    if not OWN_ENTITY.match(entity_id):
        raise ValueError(f"{entity_id!r} is not one of this app's sensors")


def _api_time(moment: datetime) -> str:
    # UTC with "Z": a "+" would turn into a space in the query string.
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
