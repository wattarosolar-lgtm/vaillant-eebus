"""The planner's rules, as scenarios of a day."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone

from vaillant_eebus.hems.config import HemsSettings, PlannerMode, Tariff, ZoneLimits
from vaillant_eebus.hems.planner import (
    DHW,
    Action,
    Block,
    DeviceView,
    Plan,
    PlanSlot,
    ZoneView,
    make_plan,
)
from vaillant_eebus.hems.timeline import horizon

BERLIN = timezone(timedelta(hours=2), "CEST")
# Wednesday 21.10.2026, 07:00 local.
NOW = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
TODAY = NOW.astimezone(BERLIN).date()
DEVICE = DeviceView(
    zones=[ZoneView(id="5.1.1", label="Zone 1", mode="on", writable=True)],
    dhw_mode="auto",
    dhw_writable=True,
    boost_writable=True,
)
SETTINGS = HemsSettings(mode=PlannerMode.CONTROL, tariff=Tariff(price_ct=30.0, feed_in_ct=8.0))

type Curve = Callable[[time], float | None]


def flat(value: float | None) -> Curve:
    return lambda _local: value


def between(start: int, end: int, inside: float, outside: float) -> Curve:
    return lambda local: inside if time(start) <= local < time(end) else outside


USUAL_PRICE = flat(0.30)
NO_SURPLUS = flat(0.0)
MILD = flat(5.0)


def plan(
    *,
    price: Curve = USUAL_PRICE,
    surplus: Curve = NO_SURPLUS,
    outdoor: Curve = MILD,
    settings: HemsSettings = SETTINGS,
    device: DeviceView = DEVICE,
    dhw_base: float | None = 55.0,
    **kwargs: object,
) -> Plan:
    slots = []
    for start in horizon(NOW, BERLIN):
        local = start.astimezone(BERLIN).time()
        slots.append(
            PlanSlot(
                start=start,
                price=price(local),
                pv_w=None,
                base_w=None,
                surplus_w=surplus(local),
                battery_soc=None,
                outdoor_c=outdoor(local),
            )
        )
    return make_plan(NOW, slots, settings, device, zone=BERLIN, dhw_base=dhw_base, **kwargs)  # type: ignore[arg-type]


def local(block: Block) -> tuple[str, str]:
    return block.start.astimezone(BERLIN).strftime("%d. %H:%M"), block.end.astimezone(BERLIN).strftime("%d. %H:%M")


def blocks(result: Plan, control: str) -> list[Block]:
    return [block for block in result.blocks if block.control == control]


# --- hot water --------------------------------------------------------------------


def test_hot_water_is_charged_once_a_day_in_the_sunniest_hours() -> None:
    def sun(local: time) -> float:
        if time(12) <= local < time(13, 30):
            return 3000.0
        return 2500.0 if time(11) <= local < time(14) else 0.0

    result = plan(surplus=sun)

    charges = blocks(result, DHW)
    assert [local(block) for block in charges] == [("21. 12:00", "21. 13:30"), ("22. 12:00", "22. 13:30")]
    assert all(block.action is Action.CHARGE and block.target_c == 60.0 for block in charges)
    assert charges[0].reason == "PV-Überschuss 3,0 kW erwartet"


def test_a_day_already_charged_is_not_charged_again() -> None:
    result = plan(surplus=between(11, 14, 2500.0, 0.0), dhw_done=frozenset({TODAY}))

    assert [local(block)[0][:3] for block in blocks(result, DHW)] == ["22."]


def test_without_sun_hot_water_waits_for_clearly_cheaper_electricity() -> None:
    result = plan(price=between(2, 4, 0.18, 0.32))

    assert [local(block) for block in blocks(result, DHW)] == [("22. 02:00", "22. 03:30")]
    assert blocks(result, DHW)[0].reason == "Günstiger Strom: 18,0 ct/kWh, Tagesmittel 30,8 ct/kWh"


def test_hot_water_never_goes_below_its_base_value() -> None:
    result = plan(surplus=flat(3000.0), dhw_base=60.0)

    assert blocks(result, DHW) == []
    assert result.notes[DHW] == "Der Grundwert liegt schon beim Höchstwert; Warmwasser wird nur angehoben."


def test_switched_off_hot_water_is_left_alone() -> None:
    result = plan(surplus=flat(3000.0), device=replace(DEVICE, dhw_mode="off"))

    assert blocks(result, DHW) == []
    assert result.notes[DHW] == "Warmwasser ist ausgeschaltet."


# --- zones ---------------------------------------------------------------------------


def test_a_zone_is_preheated_while_surplus_is_expected() -> None:
    result = plan(surplus=between(11, 15, 1200.0, 0.0))

    zone = blocks(result, "5.1.1")
    assert [local(block) for block in zone] == [("21. 11:00", "21. 15:00"), ("22. 11:00", "22. 15:00")]
    assert {(block.action, block.shift_k) for block in zone} == {(Action.RAISE, 1.0)}


def test_zones_shift_only_in_operation_mode_on() -> None:
    device = replace(DEVICE, zones=[ZoneView(id="6.1.1", label="Zone 2", mode="auto", writable=True)])

    result = plan(surplus=flat(3000.0), device=device)

    assert blocks(result, "6.1.1") == []
    assert result.notes["6.1.1"] == "Der Planer verschiebt Zonen nur in der Betriebsart Ein."


def test_zones_are_not_shifted_above_the_heating_limit() -> None:
    result = plan(surplus=flat(3000.0), outdoor=flat(17.0))

    assert blocks(result, "5.1.1") == []
    assert result.notes["5.1.1"] == "Über der Heizgrenze: Die Zonen werden nicht verschoben."


def test_without_a_temperature_forecast_zones_are_not_shifted() -> None:
    result = plan(surplus=flat(3000.0), outdoor=flat(None))

    assert blocks(result, "5.1.1") == []
    assert result.notes["5.1.1"] == "Ohne Temperaturprognose werden die Zonen nicht verschoben."


def test_a_fixed_price_without_surplus_plans_nothing() -> None:
    result = plan()

    assert result.blocks == []
    assert result.notes == {
        DHW: "Kein PV-Überschuss erwartet und kein deutlich günstigerer Strom.",
        "5.1.1": "Kein PV-Überschuss erwartet und kein deutlich günstigerer Strom.",
    }


def test_dynamic_prices_preheat_before_and_lower_during_the_evening_peak() -> None:
    def price(local: time) -> float:
        if time(13) <= local < time(16):
            return 0.20
        if time(17) <= local < time(22):
            return 0.40 if local < time(19) else 0.45
        return 0.30

    result = plan(price=price)

    zone = [(local(block), block.action, block.shift_k) for block in blocks(result, "5.1.1")]
    assert zone[:2] == [
        (("21. 13:00", "21. 16:00"), Action.RAISE, 1.0),
        # At most three hours a day, the most expensive ones.
        (("21. 19:00", "21. 22:00"), Action.LOWER, -1.0),
    ]


def test_cheap_hours_alone_preheat_at_most_four_hours_a_day() -> None:
    def low_tariff(local: time) -> float:
        return 0.18 if local >= time(22) or local < time(6) else 0.30

    result = plan(price=low_tariff)

    raised = [local(block) for block in blocks(result, "5.1.1") if block.action is Action.RAISE]
    # Two hours of Wednesday and the four cheapest (earliest) of Thursday, joined over midnight.
    assert raised == [("21. 22:00", "22. 04:00")]


def test_a_zone_with_zero_downward_shift_is_never_lowered() -> None:
    settings = replace(SETTINGS, zones=(ZoneLimits(zone_id="5.1.1", up_k=2.0, down_k=0.0),))

    result = plan(price=between(17, 21, 0.45, 0.30), settings=settings)

    assert blocks(result, "5.1.1") == []


def test_blocks_shorter_than_an_hour_are_dropped_and_short_breaks_joined() -> None:
    def surplus(local: time) -> float:
        if time(10) <= local < time(10, 45):
            return 1500.0
        if time(12) <= local < time(13) or time(13, 15) <= local < time(14):
            return 1500.0
        return 0.0

    result = plan(surplus=surplus)

    assert [local(block) for block in blocks(result, "5.1.1")][:1] == [("21. 12:00", "21. 14:00")]


def test_a_running_block_stays_as_it_is() -> None:
    running = Block(
        control="5.1.1",
        action=Action.RAISE,
        start=NOW - timedelta(minutes=30),
        end=NOW + timedelta(hours=2),
        shift_k=1.0,
        target_c=None,
        reason="PV-Überschuss 1,2 kW erwartet",
    )

    result = plan(surplus=flat(3000.0), keep=[running])

    zone = blocks(result, "5.1.1")
    assert zone[0] == running
    assert all(block.start >= running.end for block in zone[1:])


def test_missing_prices_and_forecast_are_named() -> None:
    result = plan(price=flat(None), surplus=flat(None))

    assert result.blocks == []
    assert result.notes[DHW] == "Es fehlen Strompreise und PV-Prognose."
