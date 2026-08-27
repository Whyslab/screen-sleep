"""
The night-filter schedule.

The tricky part is that the schedule normally crosses midnight (on at 22:00,
off at 07:00), so "are we inside the window" cannot be a simple comparison.
These tests pin down the behaviour at every interesting moment.
"""
import pytest
from common import MAX_TEMP, RAMP_INTERVAL_MIN, RAMP_STEP
from night import compute_target_temperature, ramp_duration_minutes


def at(hour, minute=0):
    return hour * 60 + minute


NIGHT = {"on_hour": 22, "on_minute": 0, "off_hour": 7, "off_minute": 0,
         "target_temp": 2700, "manual_override": False}


def test_ramp_duration_is_proportional_to_the_drop():
    # 6500 -> 2700 is 3800 K, at 500 K per 15 min
    assert ramp_duration_minutes(2700) == pytest.approx(3800 / RAMP_STEP * RAMP_INTERVAL_MIN)


def test_no_ramp_when_the_target_is_already_neutral():
    assert ramp_duration_minutes(MAX_TEMP) == 0.0
    assert ramp_duration_minutes(MAX_TEMP + 500) == 0.0


def test_daytime_is_neutral():
    assert compute_target_temperature(at(12), NIGHT) == MAX_TEMP
    assert compute_target_temperature(at(21, 59), NIGHT) == MAX_TEMP


def test_the_ramp_starts_at_the_on_time_and_steps_down():
    assert compute_target_temperature(at(22, 0), NIGHT) == MAX_TEMP
    assert compute_target_temperature(at(22, 15), NIGHT) == MAX_TEMP - RAMP_STEP
    assert compute_target_temperature(at(22, 30), NIGHT) == MAX_TEMP - 2 * RAMP_STEP


def test_the_ramp_never_overshoots_the_target():
    # Long after the ramp should have finished, but still at night.
    assert compute_target_temperature(at(3), NIGHT) == 2700


def test_deep_night_after_midnight_is_still_night():
    # The window crosses midnight: 01:00 is inside it, and a naive
    # "on <= now <= off" comparison would get this wrong.
    assert compute_target_temperature(at(1), NIGHT) == 2700
    assert compute_target_temperature(at(6, 59), NIGHT) == 2700


def test_morning_snaps_straight_back():
    assert compute_target_temperature(at(7, 0), NIGHT) == MAX_TEMP
    assert compute_target_temperature(at(7, 1), NIGHT) == MAX_TEMP


def test_a_window_that_does_not_cross_midnight():
    day = dict(NIGHT, on_hour=13, off_hour=18)
    assert compute_target_temperature(at(12), day) == MAX_TEMP
    assert compute_target_temperature(at(17), day) == 2700
    assert compute_target_temperature(at(19), day) == MAX_TEMP


def test_fractional_minutes_are_accepted():
    # The daemon passes seconds as a fraction; this must not raise.
    assert compute_target_temperature(at(22) + 0.5, NIGHT) == MAX_TEMP
