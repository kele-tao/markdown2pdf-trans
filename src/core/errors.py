"""统一错误码与受控异常。

所有 API 错误响应形如 {"error": "...", "code": "E_..."}，前端按 code 做精准提示。
"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    """错误码常量。集中定义，避免各处硬编码字符串。"""

    BAD_REQUEST = "E_BAD_REQUEST"
    NOT_FOUND = "E_NOT_FOUND"
    TASK_NOT_FOUND = "E_TASK_NOT_FOUND"
    TASK_BUSY = "E_TASK_BUSY"
    TASK_STATE = "E_TASK_STATE"
    TASK_RUNNING = "E_TASK_RUNNING"
    AI_UNCONFIGURED = "E_AI_UNCONFIGURED"
    AI_CONNECT = "E_AI_CONNECT"
    AI_UPSTREAM = "E_AI_UPSTREAM"
    UPLOAD_NO_FILE = "E_UPLOAD_NO_FILE"
    UPLOAD_BAD_TYPE = "E_UPLOAD_BAD_TYPE"
    UPLOAD_TOO_LARGE = "E_UPLOAD_TOO_LARGE"
    UPLOAD_BAD_ZIP = "E_UPLOAD_BAD_ZIP"
    UPLOAD_EMPTY = "E_UPLOAD_EMPTY"
    PATH_UNSAFE = "E_PATH_UNSAFE"
    RENDER_FAILED = "E_RENDER_FAILED"
    VALIDATION_FAILED = "E_VALIDATION_FAILED"
    INTERNAL = "E_INTERNAL"


class AppError(Exception):
    """可安全回传给前端的业务异常。"""

    def __init__(
        self,
        message: str,
        code: str = ErrorCode.BAD_REQUEST,
        status: int = 400,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


def not_found(message: str = "资源不存在", code: str = ErrorCode.NOT_FOUND) -> AppError:
    return AppError(message, code=code, status=404)


def conflict(message: str, code: str = ErrorCode.TASK_STATE) -> AppError:
    return AppError(message, code=code, status=409)
