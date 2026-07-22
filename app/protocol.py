from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")

@dataclass(frozen=True)
class ProtocolEvent:
    kind: str
    data: Dict[str, Any]
    raw: str


def clean_line(line: str) -> str:
    return ANSI_RE.sub("", line).replace("\x00", "").strip("\r\n")


def _coerce(value: str) -> Any:
    value = value.rstrip(",")
    low = value.lower()
    if low in {"on", "true"}: return True
    if low in {"off", "false"}: return False
    try:
        if re.fullmatch(r"[-+]?\d+", value): return int(value)
        if re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", value): return float(value)
    except ValueError:
        pass
    return value


def parse_kv(line: str) -> Dict[str, Any]:
    return {k: _coerce(v) for k, v in KV_RE.findall(line)}


def parse_line(raw_line: str) -> Optional[ProtocolEvent]:
    line = clean_line(raw_line)
    if not line:
        return None
    if line.startswith("[SERIAL][STATUS]"):
        return ProtocolEvent("status", parse_kv(line), line)
    if line.startswith("[SERIAL][ERROR]"):
        return ProtocolEvent("error", {"message": line[len("[SERIAL][ERROR]"):].strip()}, line)
    if line.startswith("[SERIAL][WARN]"):
        return ProtocolEvent("warning", {"message": line[len("[SERIAL][WARN]"):].strip()}, line)
    if line.startswith("[SERIAL] enroll success:"):
        return ProtocolEvent("enroll_success", parse_kv(line), line)
    if line.startswith("[SERIAL] enroll request queued:"):
        return ProtocolEvent("enroll_queued", parse_kv(line), line)
    if "enroll request canceled" in line or "no pending enroll" in line:
        return ProtocolEvent("enroll_canceled", {"message": line}, line)
    if line.startswith("[SERIAL] osd_mode set to"):
        return ProtocolEvent("mode_ack", {"osd_mode": line.rsplit(" ", 1)[-1]}, line)
    if line.startswith("[SERIAL] print_interval set to"):
        return ProtocolEvent("interval_ack", {"cfg_print_interval": _coerce(line.rsplit(" ", 1)[-1])}, line)
    if line.startswith("[SERIAL]"):
        return ProtocolEvent("ack", {"message": line}, line)
    return ProtocolEvent("raw", {"message": line}, line)
