from app.commands import build_enroll, build_zone_set
from app.monitoring import SedentaryMonitor


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


if __name__ == "__main__":
    test_commands()
    test_sedentary_monitor()
    print("safety test passed")
