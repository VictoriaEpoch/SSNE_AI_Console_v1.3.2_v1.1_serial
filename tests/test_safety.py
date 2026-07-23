from datetime import datetime

from app.commands import (
    MAX_COMMAND_CHARS,
    build_benchmark,
    build_enroll,
    build_print_interval,
    build_zone_rect,
    build_zone_set,
    build_zone_upload,
    validate_command,
)
from app.monitoring import NightRiseMonitor, SedentaryMonitor


def test_commands():
    assert build_zone_set([(0, 0), (1, 0), (1, 1)]) == "zone set 0 0 1 0 1 1"
    assert build_zone_rect(0, 0, 1, 1) == "zone rect 0 0 1 1"
    assert build_zone_upload([(0.1, 0.2), (0.8, 0.2), (0.5, 0.9)]) == [
        "zone clear",
        "zone add 0.1000 0.2000",
        "zone add 0.8000 0.2000",
        "zone add 0.5000 0.9000",
        "zone on",
        "zone list",
    ]
    assert build_enroll("u1", "alice", 20) == "reg u1 alice 20"
    assert build_print_interval(120) == "set print_interval 120"
    assert build_benchmark("each", 60, 30) == "test each 60 30"
    assert validate_command("x" * MAX_COMMAND_CHARS) == "x" * MAX_COMMAND_CHARS
    assert all(
        len(command) <= MAX_COMMAND_CHARS
        and len(command.encode("utf-8")) <= MAX_COMMAND_CHARS
        for command in build_zone_upload([(0, 0), (1, 0), (1, 1)])
    )

    for builder in (build_zone_set, build_zone_upload):
        for invalid in ([], [(0, 0), (1, 1)], [(0, 0), (1, 0), (2, 1)]):
            try:
                builder(invalid)
                raise AssertionError("invalid polygon was accepted")
            except ValueError:
                pass

    for invalid_command in (
        "x" * (MAX_COMMAND_CHARS + 1),
        "reg u1234567890 abcdef 120",
        "set print_interval 100000",
        "测试命令测试命令测试命令",
    ):
        try:
            validate_command(invalid_command)
            raise AssertionError("overlong command was accepted")
        except ValueError:
            pass

    for builder, args in (
        (build_enroll, ("u1234567890", "abcdef", 120)),
        (build_print_interval, (100000,)),
        (build_benchmark, ("each", 123456789012, 30)),
        (build_zone_rect, (0.2, 0.2, 0.8, 0.8)),
    ):
        try:
            builder(*args)
            raise AssertionError("builder emitted an overlong command")
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
