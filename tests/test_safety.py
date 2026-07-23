from datetime import datetime

from app.commands import build_enroll, build_zone_set
from app.monitoring import NightRiseMonitor, SedentaryMonitor


def test_commands():
    command = build_zone_set([(0.1, 0.2), (0.8, 0.2), (0.5, 0.9)])
    assert command == "zone set 0.100000 0.200000 0.800000 0.200000 0.500000 0.900000"
    assert build_enroll("u1", "alice", 20) == "reg u1 alice 20"

    for invalid in ([], [(0, 0), (1, 1)], [(0, 0), (1, 0), (2, 1)]):
        try:
            build_zone_set(invalid)
            raise AssertionError("invalid polygon was accepted")
        except ValueError:
            pass


def test_sedentary_monitor():
    monitor = SedentaryMonitor(threshold_seconds=10, grace_seconds=2)
    first = monitor.observe(person_active=True, posture_class=1, posture_valid=True, now=100)
    assert first.active and first.elapsed_seconds == 0 and not first.triggered
    before = monitor.observe(person_active=True, posture_class=1, posture_valid=True, now=109)
    assert before.active and not before.triggered
    alarm = monitor.observe(person_active=True, posture_class=1, posture_valid=True, now=110)
    assert alarm.active and alarm.triggered
    assert not monitor.tick(111).triggered

    grace = monitor.observe(person_active=True, posture_class=None, posture_valid=False, now=111.5)
    assert grace.active
    expired = monitor.tick(113)
    assert not expired.active and expired.elapsed_seconds == 0

    monitor.observe(person_active=True, posture_class=1, posture_valid=True, now=200)
    standing = monitor.observe(person_active=True, posture_class=2, posture_valid=True, now=201)
    assert not standing.active


def test_night_rise_monitor():
    monitor = NightRiseMonitor(
        start_time="22:00",
        end_time="06:00",
        lying_confirm_seconds=10,
        rise_confirm_seconds=2,
    )
    late_night = datetime(2026, 7, 22, 23, 30)
    early_morning = datetime(2026, 7, 23, 2, 0)
    daytime = datetime(2026, 7, 23, 12, 0)
    assert monitor.is_in_schedule(late_night)
    assert monitor.is_in_schedule(early_morning)
    assert not monitor.is_in_schedule(daytime)

    lying = monitor.observe(
        person_active=True,
        posture_class=0,
        posture_valid=True,
        now=100,
        wall_time=late_night,
    )
    assert lying.phase == "confirming_lying" and not lying.armed
    armed = monitor.observe(
        person_active=True,
        posture_class=0,
        posture_valid=True,
        now=110,
        wall_time=late_night,
    )
    assert armed.armed and armed.phase == "armed"

    rising = monitor.observe(
        person_active=True,
        posture_class=1,
        posture_valid=True,
        now=111,
        wall_time=late_night,
    )
    assert rising.armed and not rising.triggered
    alert = monitor.observe(
        person_active=True,
        posture_class=2,
        posture_valid=True,
        now=113,
        wall_time=late_night,
    )
    assert alert.triggered and not alert.armed
    duplicate = monitor.observe(
        person_active=True,
        posture_class=2,
        posture_valid=True,
        now=114,
        wall_time=late_night,
    )
    assert not duplicate.triggered and duplicate.phase == "waiting_lying"

    monitor.observe(
        person_active=True,
        posture_class=0,
        posture_valid=True,
        now=200,
        wall_time=early_morning,
    )
    rearmed = monitor.observe(
        person_active=True,
        posture_class=0,
        posture_valid=True,
        now=210,
        wall_time=early_morning,
    )
    assert rearmed.armed
    outside = monitor.observe(
        person_active=True,
        posture_class=2,
        posture_valid=True,
        now=211,
        wall_time=daytime,
    )
    assert not outside.in_schedule and not outside.armed


if __name__ == "__main__":
    test_commands()
    test_sedentary_monitor()
    test_night_rise_monitor()
    print("safety test passed")
