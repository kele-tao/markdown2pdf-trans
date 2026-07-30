"""
轻量级信号量 + 指数退避重试工具。
不引入额外依赖，直接用标准库 threading + time。
"""
from __future__ import annotations

import functools
import random
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimiter:
    """线程安全的信号量，限制同时执行的协程/任务数。"""

    def __init__(self, max_concurrency: int) -> None:
        self._sem = threading.BoundedSemaphore(max_concurrency)

    def acquire(self, timeout: float | None = None) -> bool:
        return self._sem.acquire(timeout=timeout)

    def release(self) -> None:
        self._sem.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def retry_with_backoff(
    max_retries: int,
    base_delay: float,
    max_delay: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """指数退避重试装饰器。"""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except retry_on as e:  # type: ignore[misc]
                    attempt += 1
                    if attempt > max_retries:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay *= 0.5 + random.random()  # 0.5x ~ 1.5x jitter
                    if on_retry:
                        on_retry(attempt, e, delay)
                    time.sleep(delay)

        return wrapper

    return decorator
