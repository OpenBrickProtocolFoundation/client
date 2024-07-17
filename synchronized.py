from threading import Lock
from types import TracebackType
from typing import Optional
from typing import Self


class Synchronized[T]:
    class _Proxy:
        def __init__(self, synchronized: "Synchronized[T]") -> None:
            self._synchronized = synchronized

        def get(self) -> T:
            return self._synchronized._value

        def set(self, value: T) -> None:
            self._synchronized._value = value

        def __enter__(self) -> Self:
            return self

        def __exit__(
                self,
                exc_type: Optional[type[BaseException]],
                exc_val: Optional[BaseException],
                exc_tb: Optional[TracebackType]
        ) -> bool:
            self._synchronized._lock.release()
            return exc_type is None

    def __init__(self, value: T) -> None:
        self._value = value
        self._lock = Lock()

    def lock(self) -> _Proxy:
        self._lock.acquire()
        return self._Proxy(self)
