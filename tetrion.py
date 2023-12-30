from __future__ import annotations

import ctypes
import os
import sys
import types
from enum import Enum
from functools import cached_property
from typing import Any
from typing import Iterable
from typing import Iterator
from typing import NamedTuple
from typing import Optional
from typing import Self
from typing import TypeVar

from _ctypes import POINTER
from _ctypes import pointer


class _Matrix(ctypes.Structure):
    pass


class _Tetrion(ctypes.Structure):
    pass


class _ObpfVec2(ctypes.Structure):
    _fields_ = [("x", ctypes.c_uint8),
                ("y", ctypes.c_uint8)]


class Vec2(NamedTuple):
    x: int
    y: int


class Key(Enum):
    LEFT = 0
    RIGHT = 1
    DROP = 2


class EventType(Enum):
    PRESSED = 0
    RELEASED = 1


class _ObpfEvent(ctypes.Structure):
    _fields_ = [("key", ctypes.c_int), ("type", ctypes.c_int), ("frame", ctypes.c_uint64)]


class Event(NamedTuple):
    key: Key
    type: EventType
    frame: int


class _ObpfTetromino(ctypes.Structure):
    _fields_ = [("mino_positions", _ObpfVec2 * 4), ("type_", ctypes.c_int)]


class TetrominoType(Enum):
    EMPTY = 0
    I = 1
    J = 2
    L = 3
    O = 4
    S = 5
    T = 6
    Z = 7


class Tetromino(NamedTuple):
    mino_positions: tuple[Vec2, Vec2, Vec2, Vec2]
    type: TetrominoType


_LIBRARY_PATH_ENVIRONMENT_VARIABLE_KEY = "OBPF_SIMULATOR_LIBRARY_PATH"


def _load_library() -> ctypes.CDLL:
    if _LIBRARY_PATH_ENVIRONMENT_VARIABLE_KEY not in os.environ:
        print(
            f"Though must specify the path to the dynamic library file using the environment variable '{_LIBRARY_PATH_ENVIRONMENT_VARIABLE_KEY}'",
            file=sys.stderr)
        exit(1)
    lib_path = os.path.realpath(os.environ[_LIBRARY_PATH_ENVIRONMENT_VARIABLE_KEY])

    lib = ctypes.windll.LoadLibrary(lib_path)

    # struct Tetrion* obpf_create_tetrion(void);
    lib.obpf_create_tetrion.restype = POINTER(_Tetrion)

    # bool obpf_tetrion_try_get_active_tetromino(struct Tetrion const* tetrion, struct ObpfTetromino* out_tetromino);
    lib.obpf_tetrion_try_get_active_tetromino.argtypes = [POINTER(_Tetrion), POINTER(_ObpfTetromino)]
    lib.obpf_tetrion_try_get_active_tetromino.restype = ctypes.c_bool

    # void obpf_tetrion_simulate_up_until(struct Tetrion* tetrion, uint64_t frame);
    lib.obpf_tetrion_simulate_up_until.argtypes = [POINTER(_Tetrion), ctypes.c_uint64]

    # void obpf_tetrion_enqueue_event(struct Tetrion* tetrion, ObpfEvent event);
    lib.obpf_tetrion_enqueue_event.argtypes = [POINTER(_Tetrion), _ObpfEvent]

    # void obpf_destroy_tetrion(struct Tetrion const* tetrion);
    lib.obpf_destroy_tetrion.argtypes = [POINTER(_Tetrion)]

    # struct Matrix const* obpf_tetrion_matrix(struct Tetrion const* tetrion);
    lib.obpf_tetrion_matrix.argtypes = [POINTER(_Tetrion)]
    lib.obpf_tetrion_matrix.restype = POINTER(_Matrix)

    # uint8_t obpf_tetrion_width(void);
    lib.obpf_tetrion_width.restype = ctypes.c_uint8

    # uint8_t obpf_tetrion_height(void);
    lib.obpf_tetrion_height.restype = ctypes.c_uint8

    # ObpfTetrominoType obpf_matrix_get(struct Matrix const* matrix, ObpfVec2 position);
    lib.obpf_matrix_get.argtypes = [POINTER(_Matrix), _ObpfVec2]

    return lib


_LIB = _load_library()


def _create_tetrion() -> Any:
    return _LIB.obpf_create_tetrion()


def _tetrion_try_get_active_tetromino(tetrion: Any) -> Optional[Tetromino]:
    tetromino = _ObpfTetromino()
    success = _LIB.obpf_tetrion_try_get_active_tetromino(tetrion, pointer(tetromino))
    if not success:
        return None
    pos0, pos1, pos2, pos3 = [Vec2(position.x, position.y) for position in tetromino.mino_positions]
    return Tetromino((pos0, pos1, pos2, pos3), TetrominoType(tetromino.type_))


def _tetrion_simulate_up_until(tetrion: Any, frame: int) -> None:
    _LIB.obpf_tetrion_simulate_up_until(tetrion, ctypes.c_uint64(frame))


def _tetrion_enqueue_event(tetrion: Any, event: Event) -> None:
    obpf_event = _ObpfEvent(key=event.key.value, type=event.type.value, frame=ctypes.c_uint64(event.frame))
    _LIB.obpf_tetrion_enqueue_event(tetrion, obpf_event)


def _destroy_tetrion(tetrion: Any) -> None:
    _LIB.obpf_destroy_tetrion(tetrion)


def _tetrion_matrix(tetrion: Any) -> Any:
    return _LIB.obpf_tetrion_matrix(tetrion)


def _tetrion_width() -> int:
    return int(_LIB.obpf_tetrion_width())


def _tetrion_height() -> int:
    return int(_LIB.obpf_tetrion_height())


def _matrix_get(matrix: Any, position: Vec2) -> TetrominoType:
    return TetrominoType(_LIB.obpf_matrix_get(matrix, _ObpfVec2(position.x, position.y)))


class Tetrion:
    def __init__(self) -> None:
        self._tetrion = _create_tetrion()

    def try_get_active_tetromino(self) -> Optional[Tetromino]:
        return _tetrion_try_get_active_tetromino(self._tetrion)

    def simulate_up_until(self, frame: int) -> None:
        _tetrion_simulate_up_until(self._tetrion, frame)

    def enqueue_event(self, event: Event) -> None:
        _tetrion_enqueue_event(self._tetrion, event)

    def matrix(self) -> Matrix:
        matrix = _tetrion_matrix(self._tetrion)
        minos: list[TetrominoType] = []
        for y in range(self.height):
            for x in range(self.width):
                minos.append(_matrix_get(matrix, Vec2(x, y)))
        return Matrix(minos, self.width)

    @cached_property
    def width(self) -> int:
        return _tetrion_width()

    @cached_property
    def height(self) -> int:
        return _tetrion_height()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException], exc_val: BaseException, exc_tb: types.TracebackType) -> bool:
        self.__del__()
        return exc_type is None

    def __del__(self) -> None:
        if self._tetrion is not None:
            _destroy_tetrion(self._tetrion)
            self._tetrion = None


_T = TypeVar("_T")


def _grouper(iterable: Iterable[_T], n: int) -> Iterator[tuple[_T, ...]]:
    args = [iter(iterable)] * n
    return zip(*args, strict=True)


class Matrix:
    def __init__(self, minos: list[TetrominoType], width: int) -> None:
        assert (len(minos) % width == 0)
        self._minos = minos
        self._width = width

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return len(self._minos) // self.width

    @property
    def rows(self) -> Iterator[tuple[TetrominoType, ...]]:
        return _grouper(self._minos, self._width)

    def __getitem__(self, position: Vec2) -> TetrominoType:
        if position.x not in range(self.width) or position.y not in range(self.height):
            raise IndexError(f"Invalid position: '{position}'")
        return self._minos[position.y * self.width + position.x]
