from __future__ import annotations

import queue
import time

import app.connection as connection_module
from app.connection import SerialConnection


class FakePort:
    def __init__(self):
        self.is_open = True
        self.in_waiting = 0
        self.writes = []
        self.reads = queue.Queue()

    def read(self, _size):
        try:
            return self.reads.get_nowait()
        except queue.Empty:
            time.sleep(0.01)
            return b""

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        return None

    def close(self):
        self.is_open = False


def test_connection_thread():
    fake = FakePort()
    original = connection_module._open_serial_port
    connection_module._open_serial_port = lambda _port, _baud: fake
    lines = []
    states = []
    try:
        conn = SerialConnection("COM3", 115200, lines.append, lambda state, message: states.append((state, message)))
        conn.start()
        deadline = time.time() + 1
        while not conn.is_open and time.time() < deadline:
            time.sleep(0.01)
        assert conn.is_open
        conn.send("status")
        fake.reads.put(b"[SERIAL][STATUS] frame=1\r\n")
        deadline = time.time() + 1
        while (not fake.writes or not lines) and time.time() < deadline:
            time.sleep(0.01)
        assert fake.writes == [b"status\r\n"]
        assert lines == ["[SERIAL][STATUS] frame=1"]
        assert any(state == "opened" for state, _message in states)
        try:
            conn.send("x" * 25)
            raise AssertionError("overlong command was queued")
        except ValueError:
            pass
        conn.close()
        assert not conn.is_running
    finally:
        connection_module._open_serial_port = original


if __name__ == "__main__":
    test_connection_thread()
    print("connection test passed")
