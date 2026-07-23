from app.commands import build_zone_upload
from app.main_window import MainWindow, ZONE_STEP_DELAY_MS, ZONE_STEP_TIMEOUT_MS


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class FakeRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, delay_ms, callback):
        self.callbacks.append((delay_ms, callback))

    def run_next(self, delay_ms):
        for index, (delay, callback) in enumerate(self.callbacks):
            if delay == delay_ms:
                self.callbacks.pop(index)
                callback()
                return
        raise AssertionError(f"no callback scheduled for {delay_ms} ms")


class FakeConnection:
    is_running = True


def make_window():
    window = MainWindow.__new__(MainWindow)
    window.root = FakeRoot()
    window.connection = FakeConnection()
    window.zone_transfer_status_var = FakeVar()
    window.zone_action_buttons = []
    window._zone_transfer_active = False
    window._zone_transfer_steps = []
    window._zone_transfer_index = 0
    window._zone_transfer_waiting = False
    window._zone_transfer_generation = 0
    window._zone_transfer_expected_points = 0
    window.sent = []
    window.logs = []
    window.send_command = lambda command, quiet=False: window.sent.append(command) or True
    window._append_log = window.logs.append
    return window


def test_acknowledged_zone_transfer():
    points = [(0.1, 0.2), (0.8, 0.2), (0.5, 0.9)]
    commands = build_zone_upload(points)
    window = make_window()
    window._start_zone_transfer(commands, len(points))

    replies = [
        "[SERIAL] danger_zone cleared and disabled.",
        "[SERIAL] danger_zone add point=(0.1,0.2)",
        "[SERIAL] danger_zone add point=(0.8,0.2)",
        "[SERIAL] danger_zone add point=(0.5,0.9)",
        "[SERIAL] danger_zone set to on",
        "[SERIAL] danger_zone enabled=on points=3",
    ]
    for reply in replies:
        window._handle_zone_transfer_line(reply)
        window.root.run_next(ZONE_STEP_DELAY_MS)

    assert window.sent == commands
    assert not window._zone_transfer_active
    assert "成功" in window.zone_transfer_status_var.value
    assert any("[ZONE][SUCCESS]" in line for line in window.logs)


def test_zone_transfer_timeout():
    points = [(0.1, 0.2), (0.8, 0.2), (0.5, 0.9)]
    window = make_window()
    window._start_zone_transfer(build_zone_upload(points), len(points))
    window.root.run_next(ZONE_STEP_TIMEOUT_MS)

    assert not window._zone_transfer_active
    assert "超时" in window.zone_transfer_status_var.value
    assert any("[ZONE][FAILED]" in line for line in window.logs)


if __name__ == "__main__":
    test_acknowledged_zone_transfer()
    test_zone_transfer_timeout()
    print("zone-transfer test passed")
