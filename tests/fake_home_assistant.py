"""A small Home Assistant Core for tests and the local UI preview.

Serves the parts of the REST and WebSocket API the app uses: states, the
app's own sensors, authentication, subscribe_trigger for state changes,
subscribe_events for homeassistant_started, and a restart that drops every
connection and every state the app set.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiohttp import WSMsgType, web

TOKEN = "fake-supervisor-token"


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
        app.router.add_get("/websocket", self._websocket)
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
    ) -> None:
        """Set an entity's state as an integration would, and notify subscribers."""
        old = self.states.get(entity_id)
        attributes: dict[str, Any] = {}
        if unit is not None:
            attributes["unit_of_measurement"] = unit
        if name is not None:
            attributes["friendly_name"] = name
        moment = (reported or datetime.now(UTC)).isoformat()
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


async def start_fake_home_assistant(**kwargs: Any) -> FakeHomeAssistant:
    fake = FakeHomeAssistant(**kwargs)
    await fake.start()
    return fake
