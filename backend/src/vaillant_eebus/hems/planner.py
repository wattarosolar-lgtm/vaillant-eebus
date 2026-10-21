"""The rules that turn prices and forecasts into a plan.

A pure function: forecasts, settings and the device's state in, a plan out;
no bridge, no Home Assistant. A plan is a list of blocks per control:

- Hot water, at most once a day: in the best 1.5 hours with a PV surplus of
  at least 1.5 kW, otherwise with electricity at least 5 ct/kWh below the
  day's mean, the setpoint goes up to its maximum and the one-time charge
  starts. Never below the base value.
- Zones, only in operation mode "on" and below the heating limit: pre-heat by
  the zone's upward shift with a surplus of at least 1 kW or cheap
  electricity, lower by its downward shift with electricity at least 5 ct/kWh
  above the day's mean (at most 3 hours a day). Blocks last at least an hour.

The start values are fixed in 1.1.0; they will be tuned after the field test.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from enum import StrEnum

from vaillant_eebus.hems.config import HemsSettings
from vaillant_eebus.hems.timeline import SLOT, local_day

DHW = "dhw"
DHW_WINDOW_SLOTS = 6
DHW_MIN_SURPLUS_W = 1500.0
ZONE_MIN_SURPLUS_W = 1000.0
# EUR/kWh below or above the day's mean that make electricity cheap or expensive.
PRICE_GAP = 0.05
MIN_BLOCK_SLOTS = 4
MAX_LOWER_SLOTS_PER_DAY = 12
# Blocks of the same kind closer than this are joined, so the setpoint does not bounce.
JOIN_GAP_SLOTS = 2


class Action(StrEnum):
    # Hot water: setpoint up to the maximum and one-time charge.
    CHARGE = "charge"
    RAISE = "raise"
    LOWER = "lower"


@dataclass(frozen=True, slots=True)
class PlanSlot:
    start: datetime
    # Price of electricity from the grid, EUR/kWh.
    price: float | None
    pv_w: float | None
    base_w: float | None
    # What would go to the grid otherwise, W.
    surplus_w: float | None
    battery_soc: float | None
    outdoor_c: float | None


@dataclass(frozen=True, slots=True)
class Block:
    # "dhw" or the zone's ID.
    control: str
    action: Action
    start: datetime
    end: datetime
    # Zones: the shift in K (negative when lowering). Hot water: the setpoint in °C.
    shift_k: float | None
    target_c: float | None
    # German, e.g. "PV-Überschuss 2,1 kW erwartet".
    reason: str

    def covers(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class ZoneView:
    id: str
    label: str
    mode: str | None
    writable: bool


@dataclass(frozen=True, slots=True)
class DeviceView:
    zones: list[ZoneView]
    # None when the device reports no hot water.
    dhw_mode: str | None
    dhw_writable: bool
    boost_writable: bool


@dataclass(frozen=True, slots=True)
class Plan:
    created_at: datetime
    slots: list[PlanSlot]
    blocks: list[Block]
    # Per control: German note why nothing is planned.
    notes: dict[str, str] = field(default_factory=dict)


def make_plan(
    now: datetime,
    slots: Sequence[PlanSlot],
    settings: HemsSettings,
    device: DeviceView,
    *,
    zone: tzinfo,
    dhw_base: float | None,
    dhw_done: frozenset[date] = frozenset(),
    keep: Sequence[Block] = (),
) -> Plan:
    """keep: blocks of the previous plan that are running; they stay as they are."""
    means = _day_means(slots, zone)
    blocks: list[Block] = list(keep)
    notes: dict[str, str] = {}

    dhw_note = _dhw_note(device, settings, dhw_base)
    if dhw_note is not None:
        notes[DHW] = dhw_note
    else:
        kept_days = {local_day(block.start, zone) for block in keep if block.control == DHW}
        planned = _dhw_blocks(slots, settings, means, zone, skip=dhw_done | kept_days)
        blocks += planned
        if not planned and not kept_days:
            notes[DHW] = _nothing_note(slots, DHW_MIN_SURPLUS_W)

    for view in device.zones:
        zone_note = _zone_note(view, settings)
        if zone_note is not None:
            notes[view.id] = zone_note
            continue
        if all(slot.outdoor_c is None for slot in slots):
            notes[view.id] = "Ohne Temperaturprognose werden die Zonen nicht verschoben."
            continue
        running = [block for block in keep if block.control == view.id]
        free_from = max((block.end for block in running), default=None)
        planned = _zone_blocks(view.id, slots, settings, means, zone, free_from=free_from)
        blocks += planned
        if not planned and not running:
            notes[view.id] = _nothing_note(slots, ZONE_MIN_SURPLUS_W, settings.heating_limit_c)

    blocks.sort(key=lambda block: (block.start, block.control))
    return Plan(created_at=now, slots=list(slots), blocks=blocks, notes=notes)


# --- hot water ------------------------------------------------------------------


def _dhw_note(device: DeviceView, settings: HemsSettings, base: float | None) -> str | None:
    if device.dhw_mode is None:
        return "Die Wärmepumpe meldet kein Warmwasser."
    if not device.dhw_writable or not device.boost_writable:
        return "Warmwasser-Soll oder Einmalladung sind am Gerät nicht änderbar."
    if device.dhw_mode == "off":
        return "Warmwasser ist ausgeschaltet."
    if base is not None and base >= settings.dhw_max_c:
        return "Der Grundwert liegt schon beim Höchstwert; Warmwasser wird nur angehoben."
    return None


def _dhw_blocks(
    slots: Sequence[PlanSlot],
    settings: HemsSettings,
    means: dict[date, float],
    zone: tzinfo,
    *,
    skip: set[date] | frozenset[date],
) -> list[Block]:
    blocks = []
    for day in sorted({local_day(slot.start, zone) for slot in slots}):
        if day in skip:
            continue
        indexes = [index for index, slot in enumerate(slots) if local_day(slot.start, zone) == day]
        best_sun: tuple[float, int] | None = None
        best_price: tuple[float, int] | None = None
        for first in indexes[: max(0, len(indexes) - DHW_WINDOW_SLOTS + 1)]:
            window = slots[first : first + DHW_WINDOW_SLOTS]
            surplus = [slot.surplus_w for slot in window]
            if all(value is not None and value >= DHW_MIN_SURPLUS_W for value in surplus):
                total = sum(value for value in surplus if value is not None)
                if best_sun is None or total > best_sun[0]:
                    best_sun = (total, first)
            prices = [slot.price for slot in window]
            mean = means.get(day)
            if mean is not None and all(price is not None for price in prices):
                average = statistics.fmean(price for price in prices if price is not None)
                if average <= mean - PRICE_GAP and (best_price is None or average < best_price[0]):
                    best_price = (average, first)
        if best_sun is not None:
            first = best_sun[1]
            reason = f"PV-Überschuss {_kw(best_sun[0] / DHW_WINDOW_SLOTS)} erwartet"
        elif best_price is not None:
            first = best_price[1]
            reason = f"Günstiger Strom: {_ct(best_price[0])}, Tagesmittel {_ct(means[day])}"
        else:
            continue
        blocks.append(
            Block(
                control=DHW,
                action=Action.CHARGE,
                start=slots[first].start,
                end=slots[first].start + DHW_WINDOW_SLOTS * SLOT,
                shift_k=None,
                target_c=settings.dhw_max_c,
                reason=reason,
            )
        )
    return blocks


# --- zones ------------------------------------------------------------------------


def _zone_note(view: ZoneView, settings: HemsSettings) -> str | None:
    limits = settings.zone_limits(view.id)
    if limits.up_k == 0 and limits.down_k == 0:
        return "Für diese Zone ist keine Verschiebung erlaubt (0 K)."
    if view.mode != "on":
        return "Der Planer verschiebt Zonen nur in der Betriebsart Ein."
    if not view.writable:
        return "Die Solltemperatur ist am Gerät nicht änderbar."
    return None


def _zone_blocks(
    zone_id: str,
    slots: Sequence[PlanSlot],
    settings: HemsSettings,
    means: dict[date, float],
    zone: tzinfo,
    *,
    free_from: datetime | None,
) -> list[Block]:
    limits = settings.zone_limits(zone_id)
    actions: list[Action | None] = []
    for slot in slots:
        action = None
        mean = means.get(local_day(slot.start, zone))
        heating = slot.outdoor_c is not None and slot.outdoor_c < settings.heating_limit_c
        if heating and (free_from is None or slot.start >= free_from):
            cheap = slot.price is not None and mean is not None and slot.price <= mean - PRICE_GAP
            sunny = slot.surplus_w is not None and slot.surplus_w >= ZONE_MIN_SURPLUS_W
            expensive = slot.price is not None and mean is not None and slot.price >= mean + PRICE_GAP
            if limits.up_k > 0 and (sunny or cheap):
                action = Action.RAISE
            elif limits.down_k > 0 and expensive:
                action = Action.LOWER
        actions.append(action)
    runs = [run for run in _join(_runs(actions)) if run[2] - run[1] >= MIN_BLOCK_SLOTS]
    runs = _limit_lowering(runs, slots, zone)
    blocks = []
    for action, first, stop in runs:
        window = slots[first:stop]
        blocks.append(
            Block(
                control=zone_id,
                action=action,
                start=window[0].start,
                end=window[-1].start + SLOT,
                shift_k=limits.up_k if action is Action.RAISE else -limits.down_k,
                target_c=None,
                reason=_zone_reason(action, window),
            )
        )
    return blocks


type _Run = tuple[Action, int, int]


def _runs(actions: Sequence[Action | None]) -> list[_Run]:
    """Consecutive slots with the same action: (action, first index, index after the last)."""
    runs: list[_Run] = []
    for index, action in enumerate(actions):
        if action is None:
            continue
        if runs and runs[-1][0] is action and runs[-1][2] == index:
            runs[-1] = (action, runs[-1][1], index + 1)
        else:
            runs.append((action, index, index + 1))
    return runs


def _join(runs: list[_Run]) -> list[_Run]:
    joined: list[_Run] = []
    for run in runs:
        if joined and joined[-1][0] is run[0] and run[1] - joined[-1][2] < JOIN_GAP_SLOTS:
            joined[-1] = (run[0], joined[-1][1], run[2])
        else:
            joined.append(run)
    return joined


def _limit_lowering(runs: list[_Run], slots: Sequence[PlanSlot], zone: tzinfo) -> list[_Run]:
    """At most MAX_LOWER_SLOTS_PER_DAY lowered slots a day, the most expensive first."""
    lowering = [run for run in runs if run[0] is Action.LOWER]
    kept: set[_Run] = set()
    budget: dict[date, int] = {}
    for run in sorted(lowering, key=lambda run: -_mean_price(slots[run[1] : run[2]])):
        day = local_day(slots[run[1]].start, zone)
        left = MAX_LOWER_SLOTS_PER_DAY - budget.get(day, 0)
        length = min(run[2] - run[1], left)
        if length < MIN_BLOCK_SLOTS:
            continue
        first = _most_expensive_window(slots, run[1], run[2], length)
        kept.add((Action.LOWER, first, first + length))
        budget[day] = budget.get(day, 0) + length
    result = [run for run in runs if run[0] is not Action.LOWER] + sorted(kept)
    return sorted(result, key=lambda run: run[1])


def _most_expensive_window(slots: Sequence[PlanSlot], first: int, stop: int, length: int) -> int:
    return max(range(first, stop - length + 1), key=lambda start: _mean_price(slots[start : start + length]))


def _zone_reason(action: Action, window: Sequence[PlanSlot]) -> str:
    price = _mean_price(window)
    if action is Action.LOWER:
        return f"Teurer Strom: {_ct(price)}"
    surplus = [slot.surplus_w for slot in window if slot.surplus_w is not None]
    if surplus and statistics.fmean(surplus) >= ZONE_MIN_SURPLUS_W:
        return f"PV-Überschuss {_kw(statistics.fmean(surplus))} erwartet"
    return f"Günstiger Strom: {_ct(price)}"


# --- shared -------------------------------------------------------------------------


def _day_means(slots: Sequence[PlanSlot], zone: tzinfo) -> dict[date, float]:
    prices: dict[date, list[float]] = {}
    for slot in slots:
        if slot.price is not None:
            prices.setdefault(local_day(slot.start, zone), []).append(slot.price)
    return {day: statistics.fmean(values) for day, values in prices.items()}


def _mean_price(window: Sequence[PlanSlot]) -> float:
    prices = [slot.price for slot in window if slot.price is not None]
    return statistics.fmean(prices) if prices else 0.0


def _nothing_note(slots: Sequence[PlanSlot], surplus_w: float, heating_limit: float | None = None) -> str:
    if heating_limit is not None and all(
        slot.outdoor_c is not None and slot.outdoor_c >= heating_limit for slot in slots
    ):
        return "Über der Heizgrenze: Die Zonen werden nicht verschoben."
    known_surplus = any(slot.surplus_w is not None for slot in slots)
    known_price = any(slot.price is not None for slot in slots)
    if not known_surplus and not known_price:
        return "Es fehlen Strompreise und PV-Prognose."
    if known_surplus and not any((slot.surplus_w or 0.0) >= surplus_w for slot in slots):
        return "Kein PV-Überschuss erwartet und kein deutlich günstigerer Strom."
    return "Kein passendes Zeitfenster erwartet."


def _kw(watts: float) -> str:
    return f"{watts / 1000:.1f} kW".replace(".", ",")


def _ct(eur: float) -> str:
    return f"{eur * 100:.1f} ct/kWh".replace(".", ",")


def block_at(blocks: Sequence[Block], control: str, moment: datetime) -> Block | None:
    """The block of a control at a moment, if any."""
    return next((block for block in blocks if block.control == control and block.covers(moment)), None)
