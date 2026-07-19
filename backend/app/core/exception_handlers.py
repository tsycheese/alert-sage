from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import ApiError
from app.schemas.error import ApiErrorDetail, ApiErrorResponse


def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    content = ApiErrorResponse(
        error=ApiErrorDetail(code=exc.code, message=exc.message, context=exc.context)
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=content.model_dump(mode="json"),
        headers=exc.headers,
    )


def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    issues = [
        {
            "field": ".".join(str(part) for part in error["loc"] if part != "body"),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    content = ApiErrorResponse(
        error=ApiErrorDetail(
            code="validation_error",
            message="Request validation failed",
            context={"issues": issues},
        )
    )
    return JSONResponse(status_code=422, content=content.model_dump(mode="json"))
