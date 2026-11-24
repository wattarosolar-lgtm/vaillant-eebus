"""The rule planner with invented weather, zones and rooms."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from vaillant_eebus.hems.config import HemsSettings, ZoneConfig, ZoneKind
from vaillant_eebus.hems.defrost import RiskHour, RiskLevel
from vaillant_eebus.hems.forecast import WeatherHour
from vaillant_eebus.hems.rules import Part, Plan, PlanHour, PlanInputs, RoomInput, ZoneInput, make_plan
from vaillant_eebus.room_data import RoomDetails

LOCAL = timezone(timedelta(hours=1), "CET")
# Thursday 20.11.2026, 18:00 local.
NOW = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
FLOOR = ZoneInput(
    zone_id="climate.zone_floor",
    label="Zone 1",
    config=ZoneConfig("climate.zone_floor", kind=ZoneKind.FLOOR),
    base_c=21.0,
    flow_line=(30.0, -0.25),
)
RADIATORS = ZoneInput(
    zone_id="climate.zone_radiators",
    label="Zone 2",
    config=ZoneConfig("climate.zone_radiators", kind=ZoneKind.RADIATOR),
    base_c=21.5,
)
ROOM_A = RoomInput("sensor.room_a", "Raum A", RADIATORS.zone_id, RoomDetails(setpoint_c=20.0))
ROOM_B = RoomInput("sensor.room_b", "Raum B", RADIATORS.zone_id, RoomDetails(setpoint_c=20.0, night_loss_k=1.0))


def weather(celsius: float | Sequence[float], *, humidity: float = 80.0, hours: int = 52) -> list[WeatherHour]:
    values = [celsius] * hours if isinstance(celsius, int | float) else list(celsius)
    return [
        WeatherHour(start=NOW + timedelta(hours=index), temperature_c=value, humidity=humidity)
        for index, value in enumerate(values)
    ]


def plan(
    hours: list[WeatherHour],
    *,
    settings: HemsSettings | None = None,
    zones: Sequence[ZoneInput] = (FLOOR, RADIATORS),
    rooms: Sequence[RoomInput] = (ROOM_A, ROOM_B),
    risks: dict[datetime, RiskHour] | None = None,
    frost: frozenset[datetime] = frozenset(),
) -> Plan:
    return make_plan(
        PlanInputs(
            now=NOW,
            zone=LOCAL,
            settings=settings or HemsSettings(),
            weather=hours,
            risks=risks or {},
            frost=frost,
            demand={},
            zones=zones,
            rooms=rooms,
        )
    )


def at(result: Plan, local_hour: int, day: int = 21) -> PlanHour:
    start = datetime(2026, 11, day, local_hour, 0, tzinfo=LOCAL).astimezone(UTC)
    return next(hour for hour in result.hours if hour.start == start)


def shift(hour: PlanHour, zone: ZoneInput) -> float:
    return next(entry.shift_k for entry in hour.zones if entry.zone_id == zone.zone_id)


def room(hour: PlanHour, item: RoomInput) -> float:
    return next(entry.setpoint_c for entry in hour.rooms if entry.entity_id == item.entity_id)


def test_the_plan_covers_48_hours_from_the_current_one() -> None:
    result = plan(weather(5.0))

    assert len(result.hours) == 48
    assert result.hours[0].start == NOW


def off(hour: PlanHour, zone: ZoneInput) -> bool:
    return next(entry.off for entry in hour.zones if entry.zone_id == zone.zone_id)


def night_part(hour: PlanHour, zone: ZoneInput) -> float:
    return next(entry.parts.get(Part.NIGHT, 0.0) for entry in hour.zones if entry.zone_id == zone.zone_id)


def _hours_from(start: datetime, moment: datetime) -> timedelta:
    return timedelta(hours=int((moment - start).total_seconds() // 3600))


def test_a_mild_morning_switches_the_zones_off_from_5_30_to_10() -> None:
    # F3 (user, 24.11.2026): mean of the forecast over 5:30-10:00 above 3 °C: off; the rooms keep their setpoints.
    result = plan(weather(5.0))

    assert [off(at(result, hour), RADIATORS) for hour in (4, 5, 9, 10)] == [False, True, True, False]
    assert off(at(result, 7), FLOOR)
    assert "Nachtabsenkung: Abschalten (Prognose im Mittel 5 °C)" in at(result, 6).reasons
    assert room(at(result, 7), ROOM_A) == 20.0
    assert night_part(at(result, 3), RADIATORS) == 0.0


def test_a_cold_morning_goes_down_to_the_minimum_power() -> None:
    result = plan(weather(-2.0))

    morning = at(result, 6)
    assert not off(morning, RADIATORS)
    # Zone 2 starts at -1 K (learned later); zone 1 only with the frost protection.
    assert night_part(morning, RADIATORS) == -1.0
    assert night_part(morning, FLOOR) == 0.0
    assert any(reason.startswith("Nachtabsenkung auf Minimalleistung") for reason in morning.reasons)


def test_the_frost_protection_rules_out_switching_off() -> None:
    hours = weather(3.5)
    frost = frozenset(hour.start for hour in hours)

    morning = at(plan(hours, frost=frost), 7)

    assert not off(morning, RADIATORS)
    assert night_part(morning, RADIATORS) == -1.0
    assert night_part(morning, FLOOR) == -1.0
    assert any("Frostschutz" in reason for reason in morning.reasons)


def test_in_the_window_the_live_state_counts() -> None:
    # 5:45 local: the night control holds zone 2 at -1.5 K, zone 1 is not lowered.
    now = datetime(2026, 11, 21, 5, 45, tzinfo=LOCAL).astimezone(UTC)
    live = replace(RADIATORS, night_active=True, night_shift_k=-1.5)
    result = make_plan(
        PlanInputs(
            now=now,
            zone=LOCAL,
            settings=HemsSettings(),
            weather=[replace(hour, start=hour.start + _hours_from(NOW, now)) for hour in weather(-2.0)],
            risks={},
            frost=frozenset(),
            demand={},
            zones=(FLOOR, live),
            rooms=(ROOM_A,),
        )
    )

    assert result.hours[0].start == datetime(2026, 11, 21, 5, 0, tzinfo=LOCAL)
    assert night_part(result.hours[0], live) == -1.5
    assert night_part(result.hours[3], live) == -1.5
    assert shift(result.hours[0], live) == -1.5
    assert night_part(result.hours[5], live) == 0.0


def test_the_hour_before_the_window_starts_has_nothing_yet() -> None:
    # 5:10 local, the window opens at 5:30: the executor must not send from the hour's plan.
    now = datetime(2026, 11, 21, 5, 10, tzinfo=LOCAL).astimezone(UTC)
    result = make_plan(
        PlanInputs(
            now=now,
            zone=LOCAL,
            settings=HemsSettings(),
            weather=[replace(hour, start=hour.start + _hours_from(NOW, now)) for hour in weather(-2.0)],
            risks={},
            frost=frozenset(),
            demand={},
            zones=(FLOOR, RADIATORS),
            rooms=(ROOM_A,),
        )
    )

    assert night_part(result.hours[0], RADIATORS) == 0.0
    assert not off(result.hours[0], RADIATORS)


def test_a_blocked_zone_gets_no_night() -> None:
    blocked = replace(RADIATORS, night_block="Feuchteschutz: Raum A")

    morning = at(plan(weather(5.0), zones=(FLOOR, blocked)), 7)

    assert not off(morning, blocked)
    assert off(morning, FLOOR)
    assert "Zone 2 ohne Nachtabsenkung: Feuchteschutz: Raum A" in morning.reasons


def test_risky_hours_get_less_load_and_the_floor_stores_before() -> None:
    hours = weather(1.0)
    risky = [datetime(2026, 11, 21, hour, 0, tzinfo=LOCAL).astimezone(UTC) for hour in range(14, 19)]
    risks = {start: RiskHour(start, 0.6 if start == risky[2] else 0.3, RiskLevel.MEDIUM) for start in risky}

    result = plan(hours, risks=risks)

    reduced = [hour for hour in result.hours if any("weniger Last" in reason for reason in hour.reasons)]
    assert len(reduced) == 3
    assert at(result, 16) in reduced
    assert shift(at(result, 16), RADIATORS) == -0.5
    assert shift(at(result, 12), FLOOR) == 0.5
    assert shift(at(result, 12), RADIATORS) == 0.0


def test_a_warm_day_is_not_heated() -> None:
    hours = weather([5.0] * 7 + [17.0] * 45)

    result = plan(hours)
    warm = at(result, 12)

    assert shift(warm, RADIATORS) == -2.0
    assert shift(warm, FLOOR) == -1.0
    assert warm.reasons[0].startswith("Warmer Tag")


def test_colder_weather_ahead_heats_earlier() -> None:
    hours = weather([5.0] * 6 + [0.0] * 46)

    result = plan(hours)

    # Floor heating reacts three hours ahead, radiators one.
    assert shift(result.hours[3], FLOOR) == 1.0
    assert shift(result.hours[3], RADIATORS) == 0.0
    assert shift(result.hours[5], RADIATORS) == 1.0
    assert any("kälter" in reason for reason in result.hours[3].reasons)


def test_mild_hours_are_block_operation_unless_the_frost_protection_runs() -> None:
    mild = plan(weather(8.0))
    frosty = plan(weather(2.0), frost=frozenset(hour.start for hour in weather(2.0)))

    assert all(entry.block for entry in mild.hours[0].zones)
    assert not any(entry.block for entry in frosty.hours[0].zones)
    assert frosty.hours[0].frost
    assert any("Frostschutz" in reason for reason in frosty.hours[0].reasons)
    assert any("Frostschutz" in reason for reason in at(frosty, 3).reasons)
    assert shift(at(frosty, 3), RADIATORS) == 0.0


def test_the_flow_offset_is_carried_on_and_kept_in_the_band() -> None:
    lifted = replace(RADIATORS, flow_offset_k=1.5, flow_reason="Vorlauf höher: Raum A unterversorgt")
    settings = HemsSettings(zones=(ZoneConfig(RADIATORS.zone_id, kind=ZoneKind.RADIATOR, band_up_k=1.0),))

    result = plan(weather(5.0), zones=(replace(lifted, config=settings.zones[0]),), settings=settings)

    assert shift(result.hours[0], RADIATORS) == 1.0
    assert result.hours[0].zones[0].target_c == 22.5
    assert "Vorlauf höher: Raum A unterversorgt" in result.hours[0].reasons


def test_the_expected_flow_follows_the_learned_line_and_the_shift() -> None:
    result = plan(weather([5.0] * 6 + [0.0] * 46))

    # 30 - 0.25 * 5 = 28.75, plus 1 K shift * 1.25.
    assert result.hours[3].zones[0].flow_c == 30.0
    assert result.hours[3].zones[1].flow_c is None


def test_without_zones_the_rooms_keep_their_setpoints_at_night() -> None:
    result = plan(weather(5.0), zones=())

    assert room(at(result, 7), ROOM_B) == 20.0
    assert at(result, 7).zones == []
