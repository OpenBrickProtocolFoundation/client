import ctypes
from typing import NamedTuple
from typing import Self


class ObpfLineClearDelayState(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint8),
        ("first", ctypes.c_uint8),
        ("second", ctypes.c_uint8),
        ("third", ctypes.c_uint8),
        ("fourth", ctypes.c_uint8),
        ("countdown", ctypes.c_uint64),
        ("delay", ctypes.c_uint64),
    ]


class LineClearDelayState(NamedTuple):
    lines: list[int]
    countdown: int
    delay: int

    @classmethod
    def from_obpf(cls, obpf: ObpfLineClearDelayState) -> Self:
        lines: list[int] = []
        if obpf.count > 0:
            lines.append(obpf.first)
        if obpf.count > 1:
            lines.append(obpf.second)
        if obpf.count > 2:
            lines.append(obpf.third)
        if obpf.count > 3:
            lines.append(obpf.fourth)
        return cls(lines, obpf.countdown, obpf.delay)
