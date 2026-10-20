"""Access to Home Assistant Core through the Supervisor (spec 6.3).

The app reads the states of the entities the user chose and reports its own
sensors. It never calls services and never changes other entities:
set_state() and remove_state() refuse every entity outside this app's prefix.

One WebSocket connection carries the state changes of the watched entities
and the "homeassistant_started" event; the REST API serves single states and
the app's own sensors. Watched entities are also read again every few
minutes, because a value reported unchanged moves only last_reported, which
triggers no event.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from vaillant_eebus.bridge_client import Backoff
from vaillant_eebus.log_store import LogCategory, LogStore

_LOGGER = logging.getLogger(__name__)

# Home Assistant Core behind the Supervisor's proxy; the token comes from SUPERVISOR_TOKEN.
SUPERVISOR_CORE_URL = "http://supervisor/core"
# The only entities this app ever writes.
OWN_ENTITY = re.compile(r"^(sensor|binary_sensor)\.vaillant_eebus_[a-z0-9_]+$")
# Seconds between reading the watched entities again.
REFRESH_INTERVAL = 300.0
# Seconds to wait for an answer of Home Assistant.
REQUEST_TIMEOUT = 10.0


class HomeAssistantUnavailable(Exception):
    """Home Assistant cannot be reached or refused the request."""


@dataclass(frozen=True, slots=True)
class EntityState:
    entity_id: str
    state: str
    attributes: Mapping[str, Any]
    last_updated: datetime | None
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
            last_updated=_time(data.get("last_updated")),
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

    async def _rest(self, method: str, path: str, *, body: Any = None, missing_ok: bool = False) -> Any:
        if self._session is None:
            raise HomeAssistantUnavailable("client not started")
        try:
            async with self._session.request(
                method, self._url + path, json=body, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as response:
                if response.status == 404 and missing_ok:
                    return None
                if response.status >= 400:
                    raise HomeAssistantUnavailable(f"{method} {path}: HTTP {response.status}")
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
            ws = await self._session.ws_connect(ws_url, heartbeat=30)
        async with ws:
            await self._authenticate(ws)
            reader = asyncio.create_task(self._read(ws))
            try:
                await self._command(ws, {"type": "subscribe_events", "event_type": "homeassistant_started"})
                await self._follow(ws, reader)
            finally:
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
        command_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._results[command_id] = future
        await ws.send_json({"id": command_id, **payload})
        async with asyncio.timeout(REQUEST_TIMEOUT):
            result = await future
        if not result.get("success"):
            error = result.get("error") or {}
            raise HomeAssistantUnavailable(f"{payload['type']} refused: {error.get('message', error)}")
        return command_id

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


def _check_own(entity_id: str) -> None:
    if not OWN_ENTITY.match(entity_id):
        raise ValueError(f"{entity_id!r} is not one of this app's sensors")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
