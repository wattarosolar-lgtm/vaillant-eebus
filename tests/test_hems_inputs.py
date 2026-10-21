"""Settings, tariffs, PV surplus and the time grid of the energy management."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from vaillant_eebus.ha_client import EntityState
from vaillant_eebus.hems.config import (
    HemsSettings,
    PlannerMode,
    Tariff,
    TariffKind,
    TimeWindow,
    WeatherSource,
    ZoneLimits,
    hems_from_json,
    hems_to_json,
)
from vaillant_eebus.hems.supply import base_load, base_profile, pv_forecast, surplus
from vaillant_eebus.hems.tariff import import_prices
from vaillant_eebus.hems.timeline import SLOT, horizon

# Summer time; the change to winter time is tested with the real zone where the system has it.
BERLIN = timezone(timedelta(hours=2), "CEST")
NOW = datetime(2026, 10, 21, 8, 5, tzinfo=UTC)


def state(entity_id: str, value: str = "0", **attributes: object) -> EntityState:
    return EntityState(entity_id=entity_id, state=value, attributes=attributes, last_reported=NOW)


# --- settings ---------------------------------------------------------------------


def test_the_settings_survive_a_round_trip() -> None:
    settings = HemsSettings(
        mode=PlannerMode.PREVIEW,
        tariff=Tariff(
            kind=TariffKind.FIXED_14A,
            price_ct=31.5,
            high_price_ct=40.0,
            low_price_ct=18.0,
            high_windows=(TimeWindow(time(17), time(20)),),
            low_windows=(TimeWindow(time(22), time(6)),),
            feed_in_ct=7.9,
        ),
        pv_forecast_today="sensor.energy_production_today",
        pv_forecast_tomorrow="sensor.energy_production_tomorrow",
        house_power="sensor.house_consumption",
        battery_soc="sensor.battery_state_of_capacity",
        battery_capacity_kwh=10.0,
        battery_charge_kw=5.0,
        battery_min_soc=5.0,
        weather_source=WeatherSource.OPEN_METEO,
        latitude=52.52,
        longitude=13.405,
        zones=(ZoneLimits(zone_id="5.1.1", up_k=1.5, down_k=0.5),),
        dhw_max_c=60.0,
        heating_limit_c=14.0,
    )

    assert hems_from_json(hems_to_json(settings)) == settings


def test_invalid_values_fall_back_one_by_one() -> None:
    raw = hems_to_json(HemsSettings(house_power="sensor.house", heating_limit_c=12.0))
    raw.update(
        mode="turbo",
        battery_soc="not an entity",
        battery_capacity_kwh=-3,
        dhw_max_c=90,
        zones=[{"zone_id": "5.1.1", "up_k": 0.7, "down_k": 1}, {"zone_id": "6.1.1", "up_k": 2, "down_k": 0}],
    )
    raw["tariff"]["high_windows"] = [{"start": "25:00", "end": "06:00"}, {"start": "17:00", "end": "20:00"}]

    settings = hems_from_json(raw)

    assert settings.mode is PlannerMode.OFF
    assert settings.battery_soc is None
    assert settings.battery_capacity_kwh is None
    assert settings.dhw_max_c == 60.0
    assert settings.house_power == "sensor.house"
    assert settings.heating_limit_c == 12.0
    assert settings.zones == (ZoneLimits(zone_id="6.1.1", up_k=2.0, down_k=0.0),)
    assert settings.tariff.high_windows == (TimeWindow(time(17), time(20)),)


def test_a_zone_without_own_limits_uses_the_defaults() -> None:
    assert HemsSettings().zone_limits("5.1.1") == ZoneLimits(zone_id="5.1.1", up_k=1.0, down_k=1.0)


# --- time grid ----------------------------------------------------------------------


def test_the_horizon_runs_from_the_current_quarter_hour_to_the_end_of_tomorrow() -> None:
    slots = horizon(NOW, BERLIN)

    assert slots[0] == datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    assert slots[-1] + SLOT == datetime(2026, 10, 22, 22, 0, tzinfo=UTC)


@pytest.mark.skipif(
    "Europe/Berlin" not in zoneinfo.available_timezones(), reason="no time zone database on this machine"
)
def test_the_horizon_covers_the_25_hour_day_of_the_change_to_winter_time() -> None:
    slots = horizon(datetime(2026, 10, 25, 0, 0, tzinfo=UTC), zoneinfo.ZoneInfo("Europe/Berlin"))

    # From 02:00 summer time on Sunday until Tuesday 00:00 winter time: 47 hours.
    assert len(slots) == 47 * 4


# --- tariffs --------------------------------------------------------------------------


def test_a_fixed_price_holds_for_every_slot() -> None:
    prices = import_prices(Tariff(price_ct=32.0), None, horizon(NOW, BERLIN), BERLIN)

    assert set(prices) == {0.32}


def test_paragraph_14a_windows_follow_local_time_across_midnight() -> None:
    tariff = Tariff(
        kind=TariffKind.FIXED_14A,
        price_ct=30.0,
        high_price_ct=40.0,
        low_price_ct=15.0,
        high_windows=(TimeWindow(time(17), time(20)),),
        low_windows=(TimeWindow(time(22), time(6)),),
    )
    local = [datetime(2026, 10, 21, hour, tzinfo=BERLIN) for hour in (5, 6, 17, 19, 20, 22, 23)]

    prices = import_prices(tariff, None, [moment.astimezone(UTC) for moment in local], BERLIN)

    assert prices == [0.15, 0.30, 0.40, 0.40, 0.30, 0.15, 0.15]


def test_dynamic_prices_come_from_the_epex_spot_sensor_with_surcharge_and_vat() -> None:
    data = [
        {"start_time": "2026-10-21T10:00:00+02:00", "end_time": "2026-10-21T11:00:00+02:00", "price_per_kwh": 0.10},
        {"start_time": "2026-10-21T11:00:00+02:00", "end_time": "2026-10-21T12:00:00+02:00", "price_ct_per_kwh": 20},
    ]
    tariff = Tariff(kind=TariffKind.DYNAMIC, price_entity="sensor.epex", surcharge_ct=2.0, vat_percent=19.0)
    slots = [
        datetime(2026, 10, 21, 8, 30, tzinfo=UTC),
        datetime(2026, 10, 21, 9, 45, tzinfo=UTC),
        NOW + timedelta(hours=5),
    ]

    prices = import_prices(tariff, state("sensor.epex", data=data), slots, BERLIN)

    assert prices[0] == pytest.approx(0.12 * 1.19)
    assert prices[1] == pytest.approx(0.22 * 1.19)
    assert prices[2] is None


def test_without_a_price_sensor_dynamic_prices_are_unknown() -> None:
    tariff = Tariff(kind=TariffKind.DYNAMIC)

    assert set(import_prices(tariff, None, horizon(NOW, BERLIN), BERLIN)) == {None}


# --- PV surplus ------------------------------------------------------------------------


def test_the_pv_forecast_runs_linearly_between_its_points() -> None:
    today = state(
        "sensor.energy_production_today",
        watts={
            "2026-10-21T11:00:00+02:00": 0,
            "2026-10-21T12:00:00+02:00": 2000,
            "2026-10-21T13:00:00+02:00": 4000,
        },
    )
    slots = [datetime(2026, 10, 21, hour, minute, tzinfo=UTC) for hour, minute in ((10, 0), (2, 0), (10, 45))]
    slots.append(datetime(2026, 10, 22, 10, 0, tzinfo=UTC))

    values = pv_forecast([today, None], slots, BERLIN)

    # 12:00-12:15 local: (2000 + 2500) / 2; night of a known day: 0; tomorrow without a sensor: unknown.
    assert values == [2250.0, 0.0, 3750.0, None]


def test_the_usual_consumption_is_the_median_without_the_heat_pump() -> None:
    end = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    house = [(end - timedelta(days=8), "0.8")]
    # One day with the sauna does not count.
    house += [(end - timedelta(days=3), "5.0"), (end - timedelta(days=2), "0.8")]
    pump = [(end - timedelta(days=8), "300")]

    profile = base_profile(house, "kW", pump, end, BERLIN)

    assert set(profile.values()) == {500.0}
    assert len(profile) == 96
    assert base_load(profile, [end], BERLIN) == [500.0]


def test_a_house_power_in_an_unknown_unit_gives_no_profile() -> None:
    end = datetime(2026, 10, 21, tzinfo=UTC)

    assert base_profile([(end - timedelta(days=8), "800")], "VA", [], end, BERLIN) == {}


def test_the_battery_charges_first_and_only_the_rest_is_surplus() -> None:
    settings = HemsSettings(battery_soc="sensor.soc", battery_capacity_kwh=2.0, battery_charge_kw=4.0)
    pv = [6000.0] * 4
    base = [500.0] * 4

    slots = surplus(pv, base, 50.0, settings)

    # 1 kWh free: the first slot charges 4 kW (1 kWh), then the battery is full.
    assert [slot.surplus_w for slot in slots] == [1500.0, 5500.0, 5500.0, 5500.0]
    assert slots[0].battery_soc == 100.0


def test_without_a_battery_everything_above_the_house_is_surplus() -> None:
    slots = surplus([3000.0, 200.0, None], [500.0, 500.0, 500.0], None, HemsSettings())

    assert [slot.surplus_w for slot in slots] == [2500.0, 0.0, None]


def test_an_unknown_state_of_charge_leaves_the_surplus_unknown() -> None:
    settings = HemsSettings(battery_soc="sensor.soc", battery_capacity_kwh=10.0)

    assert {slot.surplus_w for slot in surplus([6000.0], [500.0], None, settings)} == {None}
