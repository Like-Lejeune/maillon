"""Erreurs de l'API, au format du contrat (même forme que les erreurs OpenAI)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger("maillon")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        error_type: str,
        message: str,
        *,
        code: str | None = None,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        self.code = code
        self.param = param
        self.headers = headers


def _body(error_type: str, message: str, code: str | None = None, param: str | None = None) -> dict:
    return {"error": {"message": message, "type": error_type, "code": code, "param": param}}


_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    405: "invalid_request_error",
    429: "rate_limit_error",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.error_type, exc.message, exc.code, exc.param),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = [str(part) for part in first.get("loc", []) if part != "body"]
        param = ".".join(location) or None
        message = first.get("msg", "Requête invalide.")
        if param:
            message = f"{param} : {message}"
        return JSONResponse(
            status_code=422,
            content=_body("invalid_request_error", message, "validation_error", param),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        error_type = _TYPE_BY_STATUS.get(exc.status_code, "server_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(error_type, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Le détail part dans les journaux, jamais dans la réponse.
        logger.exception("Erreur non gérée sur %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_body("server_error", "Erreur interne du serveur."),
        )
