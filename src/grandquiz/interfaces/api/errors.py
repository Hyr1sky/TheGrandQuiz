"""HTTP 边界的稳定错误 envelope。"""

from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool
    trace_id: str | None


class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.trace_id = trace_id


def install_error_handlers(app: FastAPI) -> None:
    async def handle_api_error(_request: Request, error: Exception) -> JSONResponse:
        api_error = cast("ApiError", error)
        payload = ErrorResponse(
            code=api_error.code,
            message=api_error.message,
            retryable=api_error.retryable,
            trace_id=api_error.trace_id,
        )
        return JSONResponse(status_code=api_error.status_code, content=payload.model_dump())

    async def handle_validation_error(_request: Request, _error: Exception) -> JSONResponse:
        payload = ErrorResponse(
            code="invalid_request",
            message="请求参数无效",
            retryable=False,
            trace_id=None,
        )
        return JSONResponse(status_code=422, content=payload.model_dump())

    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
