from app.smart_home import DEFAULT_GESTURE_MAPPING, DEFAULT_HOLD_MAPPING, SmartHomeController


def test_gesture_confirmation_and_release_lock():
    controller = SmartHomeController(confirm_samples=2, release_seconds=1.0)

    assert controller.observe(0, 0.90, now=100.0) is None
    event = controller.observe(0, 0.91, now=100.2)
    assert event is not None
    assert event.action == "toggle_living_light"
    assert controller.devices["living_light"]

    assert controller.observe(0, 0.95, now=101.0) is None
    assert controller.devices["living_light"]

    assert controller.observe(-1, 0.0, now=102.0) is None
    assert controller.observe(-1, 0.0, now=103.1) is None
    assert controller.observe(0, 0.92, now=103.2) is None
    event = controller.observe(0, 0.93, now=103.4)
    assert event is not None
    assert not controller.devices["living_light"]


def test_score_filter_and_different_gesture():
    controller = SmartHomeController(minimum_score=0.8, confirm_samples=2)
    assert controller.observe(0, 0.79, now=10.0) is None
    assert controller.observe(0, 0.79, now=11.0) is None
    assert not controller.devices["living_light"]

    controller.observe(0, 0.90, now=12.0)
    controller.observe(0, 0.90, now=12.1)
    assert controller.devices["living_light"]
    controller.observe(1, 0.90, now=12.2)
    event = controller.observe(1, 0.90, now=12.3)
    assert event is not None
    assert controller.devices["bedroom_light"]


def test_manual_scenes_and_custom_mapping():
    controller = SmartHomeController(
        confirm_samples=1,
        mapping={**DEFAULT_GESTURE_MAPPING, 4: "away_scene"},
    )
    controller.execute("home_scene")
    assert controller.devices == {
        "living_light": True,
        "bedroom_light": True,
        "curtain": True,
        "air_conditioner": True,
        "television": False,
    }
    event = controller.observe(4, 0.9, now=20.0)
    assert event is not None and event.action == "away_scene"
    assert not any(controller.devices.values())


def test_configuration_validation():
    for kwargs in (
        {"minimum_score": 1.1},
        {"confirm_samples": 0},
        {"release_seconds": -1},
        {"hold_seconds": 10.1},
    ):
        try:
            SmartHomeController(**kwargs)
            raise AssertionError("invalid smart-home configuration was accepted")
        except ValueError:
            pass


def test_tap_and_hold_are_distinct_actions():
    controller = SmartHomeController(
        confirm_samples=1,
        release_seconds=0.5,
        hold_seconds=2.0,
        hold_mapping=DEFAULT_HOLD_MAPPING,
    )

    assert controller.observe(0, 0.9, now=10.0) is None
    assert controller.active_class == 0
    assert controller.observe(-1, 0.0, now=10.2) is None
    tap = controller.observe(-1, 0.0, now=10.8)
    assert tap is not None and tap.gesture_kind == "tap"
    assert tap.action == "toggle_living_light"
    assert controller.devices["living_light"]

    assert controller.observe(2, 0.9, now=11.0) is None
    assert controller.hold_progress(now=12.0) == 0.5
    held = controller.observe(2, 0.9, now=13.1)
    assert held is not None and held.gesture_kind == "hold"
    assert held.action == "movie_scene"
    assert controller.devices["television"]
    assert not controller.devices["living_light"]

    assert controller.observe(2, 0.95, now=14.0) is None
    assert controller.observe(-1, 0.0, now=14.1) is None
    assert controller.observe(-1, 0.0, now=14.7) is None
    assert controller.devices["television"]

    assert controller.observe(1, 0.9, now=20.0) is None
    assert controller.observe(-1, 0.0, now=20.1) is None
    tap_on_return = controller.observe(2, 0.9, now=20.7)
    assert tap_on_return is not None and tap_on_return.gesture_class == 1
    assert tap_on_return.action == "toggle_bedroom_light"


def test_rich_device_actions_and_scenes():
    controller = SmartHomeController()
    controller.execute("reading_scene")
    assert controller.devices["living_light"]
    assert controller.devices["curtain"]
    assert controller.levels["living_brightness"] == 100

    for _ in range(20):
        controller.execute("living_dimmer")
        controller.execute("ac_cooler")
        controller.execute("tv_volume_up")
    assert controller.levels["living_brightness"] == 20
    assert controller.levels["ac_temperature"] == 16
    assert controller.levels["tv_volume"] == 100
    assert controller.device_state_text("air_conditioner") == "已开启 · 16℃"

    controller.execute("sleep_scene")
    assert controller.devices["bedroom_light"]
    assert controller.levels["bedroom_brightness"] == 20
    assert not controller.devices["television"]


if __name__ == "__main__":
    test_gesture_confirmation_and_release_lock()
    test_score_filter_and_different_gesture()
    test_manual_scenes_and_custom_mapping()
    test_configuration_validation()
    test_tap_and_hold_are_distinct_actions()
    test_rich_device_actions_and_scenes()
    print("smart-home test passed")
