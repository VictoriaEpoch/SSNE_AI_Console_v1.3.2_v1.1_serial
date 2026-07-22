from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    hwid: str = ""


@dataclass
class ConnectionStats:
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_lines: int = 0


def _port_sort_key(name: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", name.strip())
    if match:
        return match.group(1).upper(), int(match.group(2))
    return name.upper(), 0


def _list_ports_pyserial() -> list[PortInfo]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    result = [
        PortInfo(port.device, port.description or "", port.hwid or "")
        for port in list_ports.comports()
    ]
    return sorted(result, key=lambda item: _port_sort_key(item.device))


def _list_ports_windows_registry() -> list[PortInfo]:
    if not sys.platform.startswith("win"):
        return []
    found: dict[str, PortInfo] = {}
    try:
        import winreg

        key_path = r"HARDWARE\DEVICEMAP\SERIALCOMM"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            index = 0
            while True:
                try:
                    value_name, value_data, _value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                device = str(value_data).strip()
                if device:
                    found[device] = PortInfo(device, value_name, "Windows Registry")
                index += 1
    except OSError:
        pass

    # 某些 USB 串口没有及时写入 SERIALCOMM，使用 QueryDosDevice 再补扫一次。
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query_dos_device = kernel32.QueryDosDeviceW
        query_dos_device.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        query_dos_device.restype = ctypes.c_ulong
        buffer = ctypes.create_unicode_buffer(1024)
        for number in range(1, 257):
            device = f"COM{number}"
            if query_dos_device(device, buffer, len(buffer)):
                found.setdefault(device, PortInfo(device, "Windows 串口", buffer.value))
    except Exception:
        pass

    return sorted(found.values(), key=lambda item: _port_sort_key(item.device))


def list_serial_ports() -> list[PortInfo]:
    """枚举串口。优先使用 pyserial；未安装时直接调用 Windows 系统接口。"""
    ports = _list_ports_pyserial()
    if ports:
        return ports
    return _list_ports_windows_registry()


class _PySerialPort:
    def __init__(self, port: str, baudrate: int):
        import serial

        self._serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=0.08,
            write_timeout=1.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        return bool(self._serial.is_open)

    @property
    def in_waiting(self) -> int:
        return int(self._serial.in_waiting or 0)

    def read(self, size: int) -> bytes:
        return bytes(self._serial.read(size))

    def write(self, data: bytes) -> int:
        return int(self._serial.write(data))

    def flush(self) -> None:
        self._serial.flush()

    def close(self) -> None:
        self._serial.close()


if sys.platform.startswith("win"):
    from ctypes import wintypes

    class _DCB(ctypes.Structure):
        _fields_ = [
            ("DCBlength", wintypes.DWORD),
            ("BaudRate", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("wReserved", wintypes.WORD),
            ("XonLim", wintypes.WORD),
            ("XoffLim", wintypes.WORD),
            ("ByteSize", wintypes.BYTE),
            ("Parity", wintypes.BYTE),
            ("StopBits", wintypes.BYTE),
            ("XonChar", ctypes.c_char),
            ("XoffChar", ctypes.c_char),
            ("ErrorChar", ctypes.c_char),
            ("EofChar", ctypes.c_char),
            ("EvtChar", ctypes.c_char),
            ("wReserved1", wintypes.WORD),
        ]

    class _COMMTIMEOUTS(ctypes.Structure):
        _fields_ = [
            ("ReadIntervalTimeout", wintypes.DWORD),
            ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
            ("ReadTotalTimeoutConstant", wintypes.DWORD),
            ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
            ("WriteTotalTimeoutConstant", wintypes.DWORD),
        ]


class _WindowsNativeSerialPort:
    """不依赖 pyserial 的 Windows COM 口实现，参数固定为 8-N-1。"""

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    PURGE_RXCLEAR = 0x0008
    PURGE_TXCLEAR = 0x0004
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self, port: str, baudrate: int):
        if not sys.platform.startswith("win"):
            raise RuntimeError("当前系统未安装 pyserial，且不是 Windows，无法打开串口。")

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_functions()
        path = port if port.startswith("\\\\.\\") else rf"\\.\{port}"
        handle = self._CreateFileW(
            path,
            self.GENERIC_READ | self.GENERIC_WRITE,
            0,
            None,
            self.OPEN_EXISTING,
            0,
            None,
        )
        if handle == self.INVALID_HANDLE_VALUE:
            self._raise_last_error(f"无法打开串口 {port}")
        self._handle = handle
        self._is_open = True

        try:
            self._configure_port(int(baudrate))
            self._PurgeComm(self._handle, self.PURGE_RXCLEAR | self.PURGE_TXCLEAR)
        except Exception:
            self.close()
            raise

    def _configure_functions(self) -> None:
        from ctypes import wintypes

        self._CreateFileW = self._kernel32.CreateFileW
        self._CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._CreateFileW.restype = wintypes.HANDLE

        self._GetCommState = self._kernel32.GetCommState
        self._GetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
        self._GetCommState.restype = wintypes.BOOL

        self._SetCommState = self._kernel32.SetCommState
        self._SetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
        self._SetCommState.restype = wintypes.BOOL

        self._SetCommTimeouts = self._kernel32.SetCommTimeouts
        self._SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(_COMMTIMEOUTS)]
        self._SetCommTimeouts.restype = wintypes.BOOL

        self._ReadFile = self._kernel32.ReadFile
        self._ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._ReadFile.restype = wintypes.BOOL

        self._WriteFile = self._kernel32.WriteFile
        self._WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._WriteFile.restype = wintypes.BOOL

        self._PurgeComm = self._kernel32.PurgeComm
        self._PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._PurgeComm.restype = wintypes.BOOL

        self._CloseHandle = self._kernel32.CloseHandle
        self._CloseHandle.argtypes = [wintypes.HANDLE]
        self._CloseHandle.restype = wintypes.BOOL

    def _configure_port(self, baudrate: int) -> None:
        dcb = _DCB()
        dcb.DCBlength = ctypes.sizeof(_DCB)
        if not self._GetCommState(self._handle, ctypes.byref(dcb)):
            self._raise_last_error("读取串口参数失败")
        dcb.BaudRate = baudrate
        dcb.flags = 0x00000001  # fBinary=1，其余流控、DTR、RTS 均关闭。
        dcb.ByteSize = 8
        dcb.Parity = 0
        dcb.StopBits = 0
        if not self._SetCommState(self._handle, ctypes.byref(dcb)):
            self._raise_last_error("设置串口参数失败")

        timeouts = _COMMTIMEOUTS(
            ReadIntervalTimeout=30,
            ReadTotalTimeoutMultiplier=0,
            ReadTotalTimeoutConstant=80,
            WriteTotalTimeoutMultiplier=0,
            WriteTotalTimeoutConstant=1000,
        )
        if not self._SetCommTimeouts(self._handle, ctypes.byref(timeouts)):
            self._raise_last_error("设置串口超时失败")

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def in_waiting(self) -> int:
        # 原生同步读取已经设置 80 ms 超时，不需要额外查询队列长度。
        return 1

    def read(self, size: int) -> bytes:
        if not self._is_open:
            return b""
        from ctypes import wintypes

        size = max(1, min(int(size), 4096))
        buffer = ctypes.create_string_buffer(size)
        received = wintypes.DWORD(0)
        ok = self._ReadFile(self._handle, buffer, size, ctypes.byref(received), None)
        if not ok:
            error = ctypes.get_last_error()
            if error in (0, 995):
                return b""
            self._raise_last_error("串口读取失败", error)
        return buffer.raw[: received.value]

    def write(self, data: bytes) -> int:
        if not self._is_open:
            raise RuntimeError("串口已经关闭。")
        from ctypes import wintypes

        raw = bytes(data)
        if not raw:
            return 0
        buffer = ctypes.create_string_buffer(raw, len(raw))
        written = wintypes.DWORD(0)
        if not self._WriteFile(self._handle, buffer, len(raw), ctypes.byref(written), None):
            self._raise_last_error("串口发送失败")
        return int(written.value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        if getattr(self, "_is_open", False):
            self._is_open = False
            try:
                self._CloseHandle(self._handle)
            except Exception:
                pass

    @staticmethod
    def _format_win_error(error: int) -> str:
        try:
            return ctypes.FormatError(error).strip()
        except Exception:
            return f"Windows 错误 {error}"

    def _raise_last_error(self, prefix: str, error: Optional[int] = None) -> None:
        code = ctypes.get_last_error() if error is None else error
        raise OSError(code, f"{prefix}：{self._format_win_error(code)}")


def _open_serial_port(port: str, baudrate: int):
    try:
        return _PySerialPort(port, baudrate)
    except ImportError:
        return _WindowsNativeSerialPort(port, baudrate)


class SerialConnection:
    """Simple 8-N-1 serial connection matching the reference UART client."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        on_line: Callable[[str], None],
        on_state: Callable[[str, str], None],
    ):
        self.port = port.strip()
        self.baudrate = int(baudrate)
        self.on_line = on_line
        self.on_state = on_state
        self.stats = ConnectionStats()
        self._stop = threading.Event()
        self._tx_queue: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._serial = None
        self._opened = threading.Event()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def is_open(self) -> bool:
        return self._opened.is_set()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ssne-serial", daemon=True)
        self._thread.start()

    def send(self, command: str) -> None:
        command = command.strip("\r\n")
        if command:
            self._tx_queue.put(command)

    def close(self) -> None:
        self._stop.set()
        serial_port = self._serial
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        self.on_state("opening", f"正在连接 {self.port} @ {self.baudrate}")
        try:
            self._serial = _open_serial_port(self.port, self.baudrate)
            self._opened.set()
            self.on_state("opened", f"已连接 {self.port} @ {self.baudrate}")
            buffer = bytearray()

            while not self._stop.is_set():
                serial_port = self._serial
                if serial_port is None or not serial_port.is_open:
                    break

                try:
                    chunk = serial_port.read(min(max(serial_port.in_waiting, 1), 4096))
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    raise RuntimeError(f"串口读取失败：{exc}") from exc

                if chunk:
                    self.stats.rx_bytes += len(chunk)
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        raw, _, remainder = buffer.partition(b"\n")
                        buffer = bytearray(remainder)
                        raw = raw.rstrip(b"\r")
                        if raw:
                            self.stats.rx_lines += 1
                            self.on_line(raw.decode("utf-8", errors="replace"))

                try:
                    while True:
                        command = self._tx_queue.get_nowait()
                        # The reference uart_command_client.py uses CRLF. The board
                        # reads through std::getline and trims the remaining CR.
                        packet = (command + "\r\n").encode("utf-8")
                        serial_port.write(packet)
                        serial_port.flush()
                        self.stats.tx_bytes += len(packet)
                except queue.Empty:
                    pass

                time.sleep(0.005)
        except Exception as exc:
            self.on_state("error", self._friendly_error(exc))
        finally:
            serial_port, self._serial = self._serial, None
            self._opened.clear()
            if serial_port is not None:
                try:
                    serial_port.close()
                except Exception:
                    pass
            self.on_state("closed", "串口连接已关闭")

    def _friendly_error(self, exc: Exception) -> str:
        text = str(exc)
        low = text.lower()
        if "access is denied" in low or "permissionerror" in low or "拒绝访问" in text or "错误 5" in text:
            return f"无法打开 {self.port}：端口被其他程序占用。请关闭串口助手、VS Code 串口监视器等程序。"
        if "cannot find" in low or "file not found" in low or "系统找不到" in text or "错误 2" in text:
            return f"找不到串口 {self.port}。请重新插拔设备并刷新端口。"
        if not sys.platform.startswith("win") and "pyserial" in low:
            return "当前系统没有 pyserial，无法打开串口。"
        return text
