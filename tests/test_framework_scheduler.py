"""Tests for the daypart scheduler."""

from eden_business_simulator.framework.scheduler import Daypart, DaypartScheduler


def test_daypart_contains_hours():
    morning = Daypart(name="morning", start_hour=6, end_hour=11)
    assert morning.contains(6)
    assert morning.contains(10)
    assert not morning.contains(11)


def test_daypart_wraps_midnight():
    night = Daypart(name="night", start_hour=22, end_hour=4)
    assert night.contains(23)
    assert night.contains(2)
    assert not night.contains(12)


def test_scheduler_current_phase():
    sched = DaypartScheduler(
        dayparts=[
            Daypart("morning", 6, 11),
            Daypart("lunch", 11, 14),
            Daypart("afternoon", 14, 18),
        ]
    )
    assert sched.current_phase(8).name == "morning"
    assert sched.current_phase(12).name == "lunch"
    assert sched.current_phase(15).name == "afternoon"
    assert sched.current_phase(3) is None


def test_scheduler_modifies_weights():
    sched = DaypartScheduler(
        dayparts=[
            Daypart(
                "rush",
                8,
                10,
                weight_modifiers={"order_taken": 3.0},
                default_modifier=1.0,
            ),
        ]
    )
    base = {"order_taken": 5.0, "wastage_logged": 2.0}
    modified = sched.modify_weights(base, hour=9)
    assert modified["order_taken"] == 15.0
    assert modified["wastage_logged"] == 2.0


def test_default_cafe_schedule_has_phases():
    sched = DaypartScheduler.default_cafe_schedule()
    assert sched.current_phase(7).name == "open"
    assert sched.current_phase(9).name == "breakfast_rush"
    assert sched.current_phase(12).name == "lunch_rush"
    assert sched.current_phase(15).name == "afternoon"
    assert sched.current_phase(18).name == "close"
