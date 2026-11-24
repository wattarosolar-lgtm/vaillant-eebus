"""The energy management's pure parts with invented values: settings, forecast, models, detection."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from vaillant_eebus.ha_client import StatisticRow
from vaillant_eebus.hems.config import (
    HemsSettings,
    PlannerMode,
    WeatherSource,
    ZoneConfig,
    ZoneKind,
    hems_from_json,
    hems_to_json,
    in_window,
)
from vaillant_eebus.hems.cycles import (
    CycleTracker,
    Defrost,
    Run,
    detect_defrosts,
    detect_runs,
    split_run,
    summarize,
    value_at,
)
from vaillant_eebus.hems.defrost import RiskLevel, learn_rates, risk
from vaillant_eebus.hems.forecast import WeatherHour, calibrated, calibration, parse_open_meteo, parse_weather_entity
from vaillant_eebus.hems.frost import frost_hours, frost_state
from vaillant_eebus.hems.house_model import (
    CopModel,
    demand,
    find_lag,
    fit_cop,
    fit_house,
    start_model,
    zone_shares,
)
from vaillant_eebus.hems.room_model import FlowHint, Supply, flow_hint, learn, minutes_until, supply
from vaillant_eebus.hems.statistics import DayValues, daily_values, hourly_changes
from vaillant_eebus.hems.windows import Outside, Signal, WindowDetector, absolute_humidity, humidity_threshold
from vaillant_eebus.room_data import RoomDetails, ScheduleWindow, details_from_json, details_to_json

BERLIN = timezone(timedelta(hours=1), "CET")
T0 = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)


def minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


# --- settings ----------------------------------------------------------------------------


def test_the_settings_survive_a_round_trip() -> None:
    settings = HemsSettings(
        mode=PlannerMode.PREVIEW,
        weather_source=WeatherSource.HOME_ASSISTANT,
        weather_entity="weather.home",
        latitude=50.12,
        longitude=8.68,
        heat_heating_entity="sensor.heat_heating",
        heat_dhw_entity="sensor.heat_dhw",
        buffer_entity="sensor.buffer",
        design_outdoor_c=-12.0,
        heat_load_w=7000.0,
        zones=(
            ZoneConfig(zone_id="climate.zone_a", kind=ZoneKind.FLOOR, flow_entity="sensor.flow_a", band_up_k=1.5),
            ZoneConfig(
                zone_id="climate.zone_b",
                kind=ZoneKind.RADIATOR,
                design_flow_c=50.0,
                design_return_c=35.0,
                heating_curve_entity="sensor.circuit_1_heating_curve",
            ),
        ),
        night_start=time(22, 0),
        night_end=time(6, 0),
        night_loss_k=0.7,
        target_starts=8,
    )

    assert hems_from_json(hems_to_json(settings)) == settings


def test_invalid_values_fall_back_one_by_one() -> None:
    settings = hems_from_json(
        {
            "mode": "control",
            "weather_source": "somewhere",
            "heat_heating_entity": "weather.not_a_sensor",
            "night_start": "25:00",
            "night_loss_k": 9,
            "step_k": 0.7,
            "zones": [{"zone_id": "climate.zone_a", "kind": "floor", "band_down_k": 0.3}, {"zone_id": "bad id"}],
            "tariff": {"kind": "fixed", "price_ct": 30},
        }
    )

    # "Steuern" of a planner before 1.5.0 (1.1/1.2) previews.
    assert settings.mode is PlannerMode.PREVIEW
    assert settings.weather_source is WeatherSource.OPEN_METEO
    assert settings.heat_heating_entity is None
    assert settings.night_start == time(0, 0)
    assert settings.night_loss_k == 0.5
    assert settings.step_k == 0.5
    assert settings.zones == (ZoneConfig(zone_id="climate.zone_a", kind=ZoneKind.FLOOR),)


def test_zone_bands_and_delays_follow_the_kind() -> None:
    assert (ZoneConfig("a", kind=ZoneKind.FLOOR).down_k, ZoneConfig("a", kind=ZoneKind.FLOOR).up_k) == (1.0, 2.0)
    assert (ZoneConfig("b", kind=ZoneKind.RADIATOR).down_k, ZoneConfig("b", kind=ZoneKind.RADIATOR).up_k) == (2.0, 2.0)
    assert ZoneConfig("c").down_k == 1.0
    assert ZoneConfig("a", kind=ZoneKind.FLOOR).lag_h == 3.0
    assert ZoneConfig("b", kind=ZoneKind.RADIATOR, band_down_k=0.5).down_k == 0.5


def test_a_window_may_cross_midnight() -> None:
    assert in_window(time(23, 30), time(22, 0), time(6, 0))
    assert in_window(time(5, 59), time(22, 0), time(6, 0))
    assert not in_window(time(6, 0), time(22, 0), time(6, 0))
    assert in_window(time(0, 0), time(0, 0), time(10, 0))
    assert not in_window(time(10, 0), time(0, 0), time(10, 0))


def test_room_details_survive_a_round_trip_and_follow_the_schedule() -> None:
    details = details_from_json(
        {
            "heat_load_w": 640,
            "design_c": 20.5,
            "surfaces": [
                {"kind": "radiator", "rated_w": 1500, "rated_basis": "75/65", "flow_lph": 20, "presetting": "3"}
            ],
            "trv_entity": "climate.trv_test",
            "valve_entity": "sensor.trv_test_position",
            "humidity_entity": "sensor.room_test_humidity",
            "setpoint_c": 20,
            "schedule": [{"start": "06:00", "end": "08:00", "setpoint_c": 21.5}],
            "night_loss_k": 1.0,
            "throttle": False,
            "emergency_c": 19,
        }
    )

    assert details_from_json(details_to_json(details)) == details
    assert details.setpoint_at(time(7, 0)) == 21.5
    assert details.setpoint_at(time(9, 0)) == 20.0
    assert details.heat_loss_w_per_k(-11.5) == pytest.approx(640 / 32)


def test_invalid_room_details_are_dropped_one_by_one() -> None:
    details = details_from_json(
        {
            "trv_entity": "sensor.not_a_thermostat",
            "setpoint_c": 20.3,
            "schedule": [{"start": "06:00", "end": "06:00", "setpoint_c": 21}, {"start": "x", "end": "y"}],
            "surfaces": [{"kind": "stove", "rated_w": -5}],
            "throttle": "yes",
        }
    )

    assert details.trv_entity is None
    assert details.setpoint_c == 20.0
    assert details.schedule == ()
    assert details.surfaces[0].rated_w is None
    assert details.throttle is True


# --- forecast -----------------------------------------------------------------------------


def test_open_meteo_hours_are_parsed_with_sums_of_the_hour_before() -> None:
    start = int(T0.timestamp())
    hours = parse_open_meteo(
        {
            "hourly": {
                "time": [start, start + 3600],
                "temperature_2m": [2.5, 1.5],
                "relative_humidity_2m": [91, 93],
                "shortwave_radiation": [0, 120],
                "precipitation": [None, 0.4],
            }
        }
    )

    first = next(hour for hour in hours if hour.start == T0)
    second = next(hour for hour in hours if hour.start == T0 + timedelta(hours=1))
    assert first == WeatherHour(start=T0, temperature_c=2.5, humidity=91, radiation_w=120.0, precipitation_mm=0.4)
    assert second.temperature_c == 1.5
    assert second.radiation_w is None


def test_open_meteo_without_hours_is_an_error() -> None:
    with pytest.raises(ValueError, match="no hourly data"):
        parse_open_meteo({"error": True})


def test_a_weather_entity_in_fahrenheit_and_kmh_is_converted() -> None:
    hours = parse_weather_entity(
        [{"datetime": "2026-11-20T01:30:00+00:00", "temperature": 41.0, "wind_speed": 36.0, "humidity": 80}],
        {"temperature_unit": "°F", "wind_speed_unit": "km/h"},
    )

    assert len(hours) == 1
    assert hours[0].start == T0 + timedelta(hours=1)
    assert hours[0].temperature_c == pytest.approx(5.0)
    assert hours[0].wind_ms == pytest.approx(10.0)
    assert hours[0].humidity == 80


def test_the_forecast_is_calibrated_per_local_hour() -> None:
    past = [WeatherHour(start=T0 + timedelta(hours=hour), temperature_c=5.0) for hour in range(5 * 24)]
    # The heat pump's sensor reads 1 K more at night (UTC 0-6) and the same by day.
    measured = {hour.start: 6.0 if hour.start.hour < 6 else 5.0 for hour in past}

    bias = calibration(past, measured, UTC)
    corrected = calibrated([WeatherHour(start=T0 + timedelta(days=6, hours=2), temperature_c=0.0)], bias, UTC)

    assert bias[2] == 1.0
    assert bias[12] == 0.0
    assert corrected[0].temperature_c == 1.0


def test_calibration_needs_three_days() -> None:
    past = [WeatherHour(start=T0 + timedelta(hours=hour), temperature_c=5.0) for hour in range(48)]

    assert calibration(past, {hour.start: 7.0 for hour in past}, UTC) == {}


# --- statistics and the house model ------------------------------------------------------------


def invented_days(count: int, *, loss_w_per_k: float = 150.0, gains_w: float = 300.0) -> list[DayValues]:
    days = []
    for index in range(count):
        outdoor = -2.0 + index % 12
        heating = (loss_w_per_k * (21.0 - outdoor) - gains_w) * 24 / 1000
        days.append(
            DayValues(
                day=date(2026, 1, 1) + timedelta(days=index),
                heating_kwh=round(heating, 3),
                dhw_kwh=2.0,
                electricity_kwh=round((heating + 2.0) / (3.0 + 0.1 * outdoor), 3),
                outdoor_c=outdoor,
                indoor_c=21.0,
                hours=24,
                cop_heat_kwh=round(heating + 2.0, 3),
                cop_electricity_kwh=round((heating + 2.0) / (3.0 + 0.1 * outdoor), 3),
                cop_hours=24,
            )
        )
    return days


def test_the_house_model_learns_heat_loss_and_gains() -> None:
    model = fit_house(invented_days(14), heating_limit_c=15.0)

    assert model is not None
    assert model.learned
    assert model.loss_w_per_k == pytest.approx(150.0)
    assert model.gains_w == pytest.approx(300.0)
    assert model.r2 == pytest.approx(1.0)
    assert model.heat_w(21.0, 1.0) == pytest.approx(150 * 20 - 300)


def test_the_house_model_needs_ten_heating_days() -> None:
    assert fit_house(invented_days(9), heating_limit_c=15.0) is None


def test_the_sun_lowers_the_demand() -> None:
    days = invented_days(20)
    radiation = {day.day: 1000.0 * ((index * 7) % 5) for index, day in enumerate(days)}
    # Each Wh/m² of radiation saves 5 Wh of heat (an effective window of 5 m²).
    sunny = [_with_heat(day, (day.heating_kwh or 0) - 5 * radiation[day.day] / 1000) for day in days]

    model = fit_house(sunny, heating_limit_c=15.0, radiation_wh=radiation)

    assert model is not None
    assert model.sun_m2 == pytest.approx(5.0, rel=0.2)


def _with_heat(day: DayValues, kwh: float) -> DayValues:
    return DayValues(
        day=day.day,
        heating_kwh=kwh,
        dhw_kwh=day.dhw_kwh,
        electricity_kwh=day.electricity_kwh,
        outdoor_c=day.outdoor_c,
        indoor_c=day.indoor_c,
        hours=day.hours,
    )


def test_the_performance_factor_comes_from_real_heat_and_electricity() -> None:
    cop = fit_cop(invented_days(14))

    assert cop is not None
    assert cop.at(0.0) == pytest.approx(3.0, abs=0.05)
    assert cop.at(10.0) == pytest.approx(4.0, abs=0.1)
    # Far beyond the learned days the line stays flat; the lower limit still holds for steep lines.
    assert cop.at(-100.0) == pytest.approx(cop.at(-4.0))
    assert CopModel(a=1.0, b=0.2, days=10).at(-10.0) == 1.5


def test_the_performance_factor_is_not_drawn_far_into_the_frost() -> None:
    # K1 (24.11.2026): learned on mild days (8 to 12 °C), the line keeps its value 2 K below the coldest of them.
    days = [day for day in invented_days(70) if day.outdoor_c is not None and day.outdoor_c >= 8.0]
    cop = fit_cop(days)

    assert cop is not None
    assert (cop.low_c, cop.high_c) == (8.0, 9.0)
    assert cop.at(-4.5) == pytest.approx(cop.at(6.0)) == pytest.approx(3.6, abs=0.05)
    assert cop.at(25.0) == pytest.approx(cop.at(11.0))
    assert CopModel(a=2.0, b=0.1, days=10).at(-4.0) == pytest.approx(1.6)


def test_heat_and_electricity_count_over_the_same_hours() -> None:
    # K1: the cloud's heat values miss half the day; electricity of those hours must not count against them.
    hours = [T0 + timedelta(hours=hour) for hour in range(24)]
    days = daily_values(
        heating_kwh={hour: 1.5 for hour in hours[:21]},
        dhw_kwh={},
        power_w={hour: 500.0 for hour in hours},
        outdoor_c={hour: 1.0 for hour in hours},
        indoor_c=[],
        zone=UTC,
    )
    gappy = daily_values(
        heating_kwh={hour: 1.5 for hour in hours[:12]},
        dhw_kwh={},
        power_w={hour: 500.0 for hour in hours},
        outdoor_c={hour: 1.0 for hour in hours},
        indoor_c=[],
        zone=UTC,
    )

    day = days[0]
    assert (day.cop_heat_kwh, day.cop_electricity_kwh, day.cop_hours) == (31.5, 10.5, 21)
    assert day.electricity_kwh == 12.0
    # Only 12 hours with both values: the day says nothing about the Arbeitszahl.
    assert gappy[0].cop_hours == 12 and gappy[0].cop_heat_kwh is None and gappy[0].cop_electricity_kwh is None


def test_an_hour_without_its_hot_water_value_does_not_count() -> None:
    hours = [T0 + timedelta(hours=hour) for hour in range(24)]
    days = daily_values(
        heating_kwh={hour: 1.0 for hour in hours},
        dhw_kwh={hour: 0.5 for hour in hours[:22]},
        power_w={hour: 600.0 for hour in hours},
        outdoor_c={hour: 1.0 for hour in hours},
        indoor_c=[],
        zone=UTC,
    )

    assert (days[0].cop_heat_kwh, days[0].cop_electricity_kwh, days[0].cop_hours) == (33.0, 13.2, 22)


def test_the_start_model_comes_from_the_heat_load() -> None:
    model = start_model(6000.0, -10.0)

    assert model is not None
    assert not model.learned
    assert model.loss_w_per_k == 200.0
    assert start_model(None, -10.0) is None


def test_the_delay_of_the_house_is_found() -> None:
    difference = {T0 + timedelta(hours=hour): 15 + 5 * math.sin(hour / 7) for hour in range(10 * 24)}
    # Heat follows the difference three hours later.
    heat = {hour + timedelta(hours=3): value * 0.15 for hour, value in difference.items()}

    assert find_lag(heat, difference) == 3.0


def test_a_delay_at_the_end_of_the_search_is_not_learned() -> None:
    # K4 (24.11.2026): the learned 8 h was exactly the longest delay searched - no result.
    difference = {T0 + timedelta(hours=hour): 15 + 5 * math.sin(hour / 7) for hour in range(10 * 24)}
    heat = {hour + timedelta(hours=12): value * 0.15 for hour, value in difference.items()}

    assert find_lag(heat, difference) is None


def test_hourly_demand_is_split_by_the_zones_heat_loads() -> None:
    model = start_model(6000.0, -10.0)
    assert model is not None
    shares = zone_shares({"zone_a": 3000.0, "zone_b": 1000.0}, ["zone_a", "zone_b"])

    hours = demand(model, [(T0, 1.0, None), (T0 + timedelta(hours=1), None, None)], 21.0, shares)

    assert len(hours) == 1
    assert hours[0].heat_w == 4000
    assert hours[0].zones == {"zone_a": 3000, "zone_b": 1000}
    assert hours[0].electricity_w is None
    assert zone_shares({}, ["zone_a", "zone_b"]) == {"zone_a": 0.5, "zone_b": 0.5}


def test_daily_values_add_hours_up_per_local_day() -> None:
    hours = [T0 + timedelta(hours=hour) for hour in range(48)]
    days = daily_values(
        heating_kwh={hour: 0.5 for hour in hours},
        dhw_kwh={hours[36]: 1.8},
        power_w={hour: 500.0 for hour in hours},
        outdoor_c={hour: 3.0 for hour in hours},
        indoor_c=[{hour: 21.0 for hour in hours}, {hour: 20.0 for hour in hours}],
        zone=BERLIN,
    )

    # Local time is one hour ahead: the 21st is the first whole local day.
    full = next(day for day in days if day.day == date(2026, 11, 21))
    assert full.heating_kwh == 12.0
    assert full.dhw_kwh == 1.8
    assert full.electricity_kwh == 12.0
    assert full.indoor_c == 20.5
    assert full.hours == 24


def test_negative_counter_changes_are_ignored() -> None:
    rows = [StatisticRow(start=T0, change=1500.0), StatisticRow(start=T0 + timedelta(hours=1), change=-3.0)]

    assert hourly_changes(rows, 0.001) == {T0: 1.5}


# --- cycles and defrosts ----------------------------------------------------------------------


def defrost_profile(start: datetime) -> list[tuple[datetime, float]]:
    """A run at 700 W, a defrost plateau of 5 min at 450 W, recovery at 1800 W, then off."""
    points = [(start + minutes(index), 700.0 + index) for index in range(20)]
    points += [(start + minutes(20), 450.0), (start + minutes(23), 460.0)]
    points += [(start + minutes(25), 1800.0), (start + minutes(30), 1500.0), (start + minutes(40), 900.0)]
    points += [(start + minutes(60), 0.0)]
    return points


def test_runs_and_pauses_are_found() -> None:
    points = [(T0, 0.0), (T0 + minutes(10), 600.0), (T0 + minutes(70), 0.0), (T0 + minutes(120), 650.0)]

    runs = detect_runs(points, T0 + minutes(130))

    assert [(run.start, run.end) for run in runs] == [
        (T0 + minutes(10), T0 + minutes(70)),
        (T0 + minutes(120), None),
    ]
    assert runs[0].energy_wh == 600.0
    assert runs[0].measured


def test_a_gap_makes_the_neighbouring_run_unmeasured() -> None:
    points = [(T0, math.nan), (T0 + minutes(10), 600.0), (T0 + minutes(40), math.nan), (T0 + minutes(90), 0.0)]

    runs = detect_runs(points, T0 + minutes(100))

    assert len(runs) == 1
    assert not runs[0].measured


def test_the_defrost_signature_is_recognized() -> None:
    defrosts = detect_defrosts(defrost_profile(T0))

    assert len(defrosts) == 1
    assert defrosts[0].at == T0 + minutes(20)
    assert defrosts[0].minutes == 5.0
    assert defrosts[0].peak_w == 1800.0


def test_a_stop_through_the_plateau_is_no_defrost() -> None:
    points = [(T0, 700.0), (T0 + minutes(5), 450.0), (T0 + minutes(9), 0.0)]

    assert detect_defrosts(points) == []


def test_a_short_dip_without_recovery_is_no_defrost() -> None:
    points = [(T0, 700.0), (T0 + minutes(5), 450.0), (T0 + minutes(10), 650.0)]

    assert detect_defrosts(points) == []


def test_days_and_bands_are_summarized() -> None:
    now = T0 + timedelta(hours=12)
    runs = [
        Run(start=T0 + timedelta(hours=1), end=T0 + timedelta(hours=2), energy_wh=600, outdoor_c=1.0),
        Run(start=T0 + timedelta(hours=3), end=T0 + timedelta(hours=3, minutes=30), energy_wh=300, outdoor_c=8.0),
    ]

    days, bands = summarize(runs, [], now, UTC)

    assert days[0].starts == 2
    assert days[0].run_minutes == 45.0
    assert days[0].pause_minutes == 60.0
    assert bands[1].starts == 1
    assert bands[2].run_hours == 0.5


def test_the_tracker_counts_live_and_remembers_events(tmp_path: Path) -> None:
    now = [T0]
    tracker = CycleTracker(tmp_path, clock=lambda: now[0])
    tracker.feed_outdoor(T0, 1.0)
    for moment, watts in defrost_profile(T0):
        tracker.feed_power(moment, watts)
    now[0] = T0 + minutes(70)

    found = tracker.update()
    tracker.save()
    again = CycleTracker(tmp_path, clock=lambda: now[0])

    assert len(found) == 1
    assert found[0].outdoor_c == 1.0
    assert tracker.update() == []
    assert len(again.defrosts) == 1
    assert [run.start for run in again.runs] == [T0]
    stats = tracker.stats()
    assert stats.today.defrosts == 1
    assert stats.running is False


def test_history_from_home_assistant_replaces_the_stored_events(tmp_path: Path) -> None:
    now = T0 + timedelta(days=1)
    tracker = CycleTracker(tmp_path, clock=lambda: now)
    tracker.runs = [Run(start=T0 + timedelta(hours=5), end=T0 + timedelta(hours=6), energy_wh=1.0)]

    tracker.import_history(defrost_profile(T0 + timedelta(hours=2)), [(T0, -1.0)])

    assert [run.start for run in tracker.runs] == [T0 + timedelta(hours=2)]
    assert tracker.defrosts[0].outdoor_c == -1.0
    assert value_at(tracker.power, T0 + timedelta(hours=2, minutes=21)) == 450.0


def test_the_defrost_rates_start_from_the_field_data_and_learn() -> None:
    now = T0 + timedelta(days=2)
    runs = [Run(start=T0, end=T0 + timedelta(hours=40), energy_wh=1.0, outdoor_c=5.0)]
    rates = learn_rates(runs, [], now)

    assert rates[0].rate == 0.8
    assert not rates[0].learned
    # 40 running hours at 4-7 °C without a defrost pull the start value 0.02 down.
    assert rates[3].rate == pytest.approx(0.2 / 50)
    assert rates[3].learned


def test_a_run_into_the_frost_counts_its_hours_where_they_ran() -> None:
    # K2 (24.11.2026): a run starting at +1 °C counted all its hours at +1 °C, its defrosts at their own
    # temperature below 0 °C - "unter 0 °C" showed 13 defrosts on 1.2 running hours (1.88 per hour).
    outdoor = [(T0, 1.0), (T0 + timedelta(hours=1), -0.5), (T0 + timedelta(hours=2), -2.0)]
    run = Run(start=T0 + minutes(30), end=T0 + timedelta(hours=5), energy_wh=4000.0, outdoor_c=1.0)
    pieces = split_run(run, outdoor, lambda _moment: 80.0, T0 + timedelta(hours=6))
    defrosts = [Defrost(at=T0 + timedelta(hours=hour), minutes=5.0, peak_w=1800.0, outdoor_c=-2.0) for hour in (3, 4)]

    assert [piece[2] for piece in pieces] == [30.0, 60.0, 60.0, 60.0, 60.0]
    assert [piece[0] for piece in pieces] == [1.0, -0.5, -2.0, -2.0, -2.0]
    rates = learn_rates([replace(run, pieces=pieces)], defrosts, T0 + timedelta(hours=6))
    # 4 of the 4.5 hours below 0 °C: (2 + 10 * 0.8) / (4 + 10), no longer 2 defrosts on no hour there.
    assert (rates[0].run_hours, rates[0].defrosts) == (4.0, 2)
    assert rates[0].rate == pytest.approx(10 / 14, abs=0.001)
    assert rates[1].run_hours == 0.5
    # The same run stored before 1.5.0 (without pieces) still counts at its start.
    assert learn_rates([run], defrosts, T0 + timedelta(hours=6))[1].run_hours == 4.5


def test_humidity_counts_once() -> None:
    # 20 hours at 0-2 °C in fog (95 %) with 9 defrosts: the rate is learned per hour of normal humidity
    # (20 h weigh 30), so the forecast's factor 1.5 brings it back to what was seen - not 1.5 times more.
    run = Run(start=T0, end=T0 + timedelta(hours=20), energy_wh=1.0, outdoor_c=1.0)
    foggy = replace(run, pieces=split_run(run, [(T0, 1.0)], lambda _moment: 95.0, T0 + timedelta(hours=21)))
    defrosts = [Defrost(at=T0 + timedelta(hours=hour), minutes=5.0, peak_w=1800.0, outdoor_c=1.0) for hour in range(9)]

    rates = learn_rates([foggy], defrosts, T0 + timedelta(hours=21))

    assert (rates[1].run_hours, rates[1].weighted_hours) == (20.0, 30.0)
    assert rates[1].rate == pytest.approx((9 + 10 * 0.3) / (30 + 10))
    expected = risk([WeatherHour(start=T0, temperature_c=1.0, humidity=95.0)], rates)[0].per_run_hour
    assert expected == pytest.approx(1.5 * 12 / 40, abs=0.001)


def test_the_band_statistics_count_hours_where_they_ran(tmp_path: Path) -> None:
    now = T0 + timedelta(hours=8)
    tracker = CycleTracker(tmp_path, clock=lambda: now)
    tracker.feed_outdoor(T0, 6.0)
    tracker.feed_outdoor(T0 + timedelta(hours=2), -1.0)
    for moment, watts in [(T0, 800.0), (T0 + timedelta(hours=4), 0.0)]:
        tracker.feed_power(moment, watts)

    tracker.update()
    tracker.save()
    again = CycleTracker(tmp_path, clock=lambda: now)
    bands = again.stats().bands

    assert [band.run_hours for band in bands[:3]] == [2.0, 0.0, 2.0]
    assert bands[2].starts == 1
    assert again.runs[0].pieces == tracker.runs[0].pieces


def test_humid_air_raises_the_risk() -> None:
    rates = learn_rates([], [], T0)
    hours = [
        WeatherHour(start=T0, temperature_c=1.0, humidity=95.0),
        WeatherHour(start=T0 + timedelta(hours=1), temperature_c=1.0, humidity=60.0),
        WeatherHour(start=T0 + timedelta(hours=2), temperature_c=9.0, humidity=95.0),
    ]

    levels = [item.level for item in risk(hours, rates)]

    assert levels == [RiskLevel.MEDIUM, RiskLevel.LOW, RiskLevel.NONE]


# --- frost protection -------------------------------------------------------------------------


def test_frost_protection_starts_after_four_hours_below_four_degrees() -> None:
    outdoor = [(T0, 6.0), (T0 + timedelta(hours=1), 3.5), (T0 + timedelta(hours=3), 2.0)]

    assert not frost_state(outdoor, T0 + timedelta(hours=4)).active
    state = frost_state(outdoor, T0 + timedelta(hours=5, minutes=1))
    assert state.active
    assert state.below_since == T0 + timedelta(hours=1)
    assert not frost_state([*outdoor, (T0 + timedelta(hours=6), 4.5)], T0 + timedelta(hours=7)).active


def test_frost_hours_follow_the_forecast() -> None:
    state = frost_state([(T0, 3.0)], T0 + timedelta(hours=2))
    hours = [WeatherHour(start=T0 + timedelta(hours=2 + index), temperature_c=2.0) for index in range(4)]

    # Below 4 °C since T0: active from T0 + 4 h on.
    assert frost_hours(hours, state) == {T0 + timedelta(hours=4), T0 + timedelta(hours=5)}


# --- windows (1.4.3, plan-1.4.3.md Teil B) ------------------------------------------------------------

# 23.11.2026, 0 °C outside: the live airing runs.
WINTER = Outside(celsius=0.1, relative=90.0)


def test_absolute_humidity() -> None:
    assert absolute_humidity(20.0, 50.0) == pytest.approx(8.6, abs=0.1)


def test_the_humidity_threshold_follows_the_weather() -> None:
    # Inside 21 °C / 43 % = 8.0 g/m³, outside 0 °C / 90 % = 4.4 g/m³: 0.2 x 3.6.
    assert humidity_threshold(8.0, WINTER) == pytest.approx(0.72, abs=0.02)
    # Very dry and cold outside: at most 1 g/m³; mild and damp: the humidity does not count.
    assert humidity_threshold(10.0, Outside(-10.0, 50.0)) == 1.0
    assert humidity_threshold(8.0, Outside(10.0, 95.0)) is None
    # Without an outdoor temperature a middle threshold.
    assert humidity_threshold(8.0, Outside()) == 0.7


def test_franz_is_seen_against_a_report_34_minutes_old() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.41, 43.21, WINTER)

    opened = detector.feed(T0 + minutes(33.8), 21.12, 37.04, WINTER)

    assert opened is not None and opened.opened and opened.signal is Signal.HUMIDITY
    assert opened.change_g == pytest.approx(1.27, abs=0.02)
    assert opened.describe() == "Feuchte \u22121,3 g/m³ in 34 min"


def test_franz_closes_by_the_humidity_six_minutes_later() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.41, 43.21, WINTER)
    detector.feed(T0 + minutes(33.8), 21.12, 37.04, WINTER)

    closed = detector.feed(T0 + minutes(41.4), 21.04, 41.84, WINTER)

    assert closed is not None and not closed.opened and closed.signal is Signal.HUMIDITY
    assert not detector.open


def test_the_office_is_seen_at_the_first_report_after_opening() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.57, 42.61, WINTER)

    opened = detector.feed(T0 + minutes(10.5), 21.07, 36.51, WINTER)

    assert opened is not None and opened.signal is Signal.HUMIDITY


def test_temperature_and_humidity_of_one_report_count_once() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.57, 42.61, WINTER)
    # The temperature arrives a second before the humidity: the first half alone shows nothing.
    assert detector.feed(T0 + minutes(10), 21.27, 42.61, WINTER) is None

    opened = detector.feed(T0 + minutes(10) + timedelta(seconds=1), 21.27, 36.51, WINTER)

    assert opened is not None and opened.signal is Signal.HUMIDITY


def test_a_humidity_fall_needs_a_recent_reference_and_speed() -> None:
    old = WindowDetector()
    old.feed(T0, 21.4, 43.0, WINTER)
    assert old.feed(T0 + minutes(95), 21.2, 37.0, WINTER) is None
    # 0.8 g/m³ in an hour: someone left the room, no window.
    slow = WindowDetector()
    slow.feed(T0, 21.4, 43.0, WINTER)
    assert slow.feed(T0 + minutes(80), 21.3, 39.0, WINTER) is None


def test_a_humidity_fall_with_a_rising_temperature_is_no_window() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.0, 45.0, WINTER)

    assert detector.feed(T0 + minutes(10), 21.3, 38.0, WINTER) is None


def test_mild_damp_weather_leaves_the_humidity_out() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.4, 43.2, Outside(10.0, 95.0))

    assert detector.feed(T0 + minutes(10), 21.2, 37.0, Outside(10.0, 95.0)) is None


def test_without_humidity_the_temperature_must_fall_fast() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.0, None, WINTER)
    assert detector.feed(T0 + minutes(5), 20.6, None, WINTER) is None

    opened = detector.feed(T0 + minutes(9), 20.4, None, WINTER)

    assert opened is not None and opened.signal is Signal.TEMPERATURE
    # Against a report more than 30 minutes old the temperature says nothing.
    late = WindowDetector()
    late.feed(T0, 21.0, None, WINTER)
    assert late.feed(T0 + minutes(31), 20.0, None, WINTER) is None


def test_the_temperature_rule_needs_a_cold_outside() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.0, None, Outside(15.0, 80.0))

    assert detector.feed(T0 + minutes(9), 20.4, None, Outside(15.0, 80.0)) is None


def test_the_thermostat_sees_franz_three_minutes_after_the_room_sensor_was_silent() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.41, 43.21, WINTER)
    detector.feed_trv(T0, 22.0)
    assert detector.feed_trv(T0 + minutes(11.2), 21.5) is None

    opened = detector.feed_trv(T0 + minutes(13.3), 21.0)

    assert opened is not None and opened.signal is Signal.THERMOSTAT
    assert opened.describe() == "Thermostat \u22121,0 K"


def test_the_thermostat_is_not_believed_right_after_a_command_or_when_the_room_warms() -> None:
    guarded = WindowDetector()
    guarded.feed_trv(T0, 22.0)
    assert guarded.feed_trv(T0 + minutes(2), 21.0, guarded=True) is None
    warming = WindowDetector()
    warming.feed(T0, 21.0, 45.0, WINTER)
    warming.feed_trv(T0, 22.0)
    warming.feed(T0 + minutes(1), 21.2, 45.0, WINTER)
    assert warming.feed_trv(T0 + minutes(2), 21.0) is None


def test_a_room_measured_at_its_thermostat_needs_one_and_a_half_kelvin() -> None:
    detector = WindowDetector(alone=True)
    detector.feed_trv(T0, 22.5)
    assert detector.feed_trv(T0 + minutes(2), 21.5) is None

    assert detector.feed_trv(T0 + minutes(4), 21.0) is not None


def test_the_office_closes_by_its_thermostat() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.57, 42.61, WINTER)
    detector.feed_trv(T0, 20.5)
    detector.feed(T0 + minutes(10.5), 21.07, 36.51, WINTER)
    for minute, value in ((11, 20.0), (14, 19.5), (16, 19.0), (18, 18.5)):
        assert detector.feed_trv(T0 + minutes(minute), value) is None

    closed = detector.feed_trv(T0 + minutes(25.5), 19.0)

    assert closed is not None and closed.signal is Signal.THERMOSTAT
    assert closed.describe() == "Thermostat +0,5 K"


def test_a_window_does_not_close_within_three_minutes() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.0, None, WINTER)
    detector.feed(T0 + minutes(5), 20.4, None, WINTER)

    assert detector.feed(T0 + minutes(6), 20.8, None, WINTER) is None
    assert detector.open


def test_an_open_window_closes_after_half_an_hour() -> None:
    detector = WindowDetector()
    detector.feed(T0, 21.0, None, WINTER)
    detector.feed(T0 + minutes(5), 20.4, None, WINTER)

    assert detector.tick(T0 + minutes(20)) is None
    closed = detector.tick(T0 + minutes(36))
    assert closed is not None and not closed.opened and closed.signal is Signal.TIMEOUT


def test_a_room_keeps_its_window_contact() -> None:
    details = details_from_json({"window_entity": "binary_sensor.buero_fenster"})

    assert details.window_entity == "binary_sensor.buero_fenster"
    assert details_to_json(details)["window_entity"] == "binary_sensor.buero_fenster"
    # Only a binary_sensor is a contact.
    assert details_from_json({"window_entity": "sensor.buero_fenster"}).window_entity is None


# --- the room model ---------------------------------------------------------------------------


type Samples = list[tuple[datetime, float]]


def cooling_series(tau_h: float, nights: int) -> tuple[Samples, Samples, Samples]:
    """A room that cools from 21 °C each night (valve shut) at -4, -2, 0 ... °C outside, 5-minute samples."""
    temperature, valve, outdoor = [], [], []
    for night in range(nights):
        start = T0 + timedelta(days=night)
        outside = -4.0 + 2 * night
        room = 21.0
        for step in range(36):
            moment = start + minutes(5 * step)
            temperature.append((moment, round(room, 4)))
            valve.append((moment, 0.0))
            outdoor.append((moment, outside))
            room += -(room - outside) / tau_h * 5 / 60
        # Daytime: heated back up with the valve open.
        for step in range(12):
            moment = start + timedelta(hours=4) + minutes(5 * step)
            temperature.append((moment, 21.0))
            valve.append((moment, 60.0))
            outdoor.append((moment, outside))
    return temperature, valve, outdoor


def test_the_room_model_learns_the_time_constant() -> None:
    temperature, valve, outdoor = cooling_series(40.0, 6)
    power = [(moment, 0.0) for moment, _ in temperature]

    model = learn(temperature=temperature, outdoor=outdoor, power=power, valve=valve)

    assert model.tau_h.value == pytest.approx(40.0, rel=0.1)
    assert model.tau_h.learned
    assert model.gains_k_per_h.value == pytest.approx(0.0, abs=0.05)


def test_cooling_needs_a_spread_of_situations_and_otherwise_keeps_the_start_value() -> None:
    model = learn(temperature=[], outdoor=[], power=[])

    assert model.tau_h.value == 150.0
    assert not model.tau_h.learned
    assert model.learning
    assert model.startup_min.value == 30.0


def test_a_zone_switched_off_is_a_cooling_phase_whatever_the_valve() -> None:
    # K5: with the zone off the valves open (the room gets colder), yet no heat comes - the room cools.
    temperature, _valve, outdoor = cooling_series(150.0, 6)
    open_valve = [(moment, 100.0) for moment, _ in temperature]
    running = [(moment, 900.0) for moment, _ in temperature]
    night = {moment for moment, _ in temperature if (moment - T0).total_seconds() % 86400 < 4 * 3600}
    zone_off = [(moment, 1.0 if moment in night else 0.0) for moment, _ in temperature]

    model = learn(temperature=temperature, outdoor=outdoor, power=running, valve=open_valve, zone_off=zone_off)
    without = learn(temperature=temperature, outdoor=outdoor, power=running, valve=open_valve)

    assert model.tau_h.learned and model.tau_h.value == pytest.approx(150.0, rel=0.15)
    assert not without.tau_h.learned


def test_minutes_until_the_lower_comfort_limit() -> None:
    model = learn(temperature=[], outdoor=[], power=[])

    # tau 150 h (K5): from 21 °C at 1 °C outside about 0.13 K/h, so 0.5 K in roughly 3 h 45 min.
    result = minutes_until(model, 21.0, 20.5, lambda _moment: 1.0, T0)

    assert result == pytest.approx(227, abs=10)
    assert minutes_until(model, 21.0, 20.5, lambda _moment: 21.0, T0) is None


def test_supply_and_the_hint_for_the_flow() -> None:
    now = T0 + timedelta(hours=1)
    temperature = [(now - minutes(5), 19.5)]
    wide_open = [(now - minutes(step), 95.0) for step in range(0, 60, 5)]
    shut = [(now - minutes(step), 10.0) for step in range(0, 60, 5)]

    under = supply(temperature, wide_open, 20.0, now)
    over = supply([(now - minutes(5), 20.2)], shut, 20.0, now)
    unknown = supply([], shut, 20.0, now)

    assert under.supply is Supply.UNDER
    assert over.supply is Supply.OVER
    assert unknown.supply is Supply.UNKNOWN
    assert flow_hint([under, over], [wide_open, shut], now) is FlowHint.HIGHER
    assert flow_hint([over], [shut], now) is FlowHint.LOWER
    assert flow_hint([unknown], [], now) is FlowHint.UNKNOWN


def test_room_details_schedule_window_type() -> None:
    details = RoomDetails(schedule=(ScheduleWindow(start=time(22, 0), end=time(6, 0), setpoint_c=18.0),))

    assert details.setpoint_at(time(23, 0)) == 18.0
    assert details.setpoint_at(time(12, 0)) == 20.0
