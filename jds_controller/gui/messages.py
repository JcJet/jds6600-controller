"""Typed messages passed from worker threads to the Tk UI thread.

Historically the GUI used (kind, payload) tuples with string kinds.
After multiple iterations (polling, resume, FM modulation) the message
surface grew enough that typos and payload-shape drift became a real risk.

This module provides a minimal typed protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union


class MsgKind(str, Enum):
    STATUS = "status"
    PROBE = "probe"
    AUTODETECT = "autodetect"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECT_ERROR = "connect_error"
    DEVICE_STATE = "device_state"
    CHECKPOINT = "checkpoint"
    LOG = "log"
    PROGRESS = "progress"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class ProgressPayload:
    done: int
    total: int
    line: Optional[int]
    est_seconds: float


@dataclass(frozen=True)
class DonePayload:
    rc: int


Payload = Union[str, int, float, bool, None, dict, ProgressPayload, DonePayload]


@dataclass(frozen=True)
class GuiMsg:
    kind: MsgKind
    payload: Payload = None
