import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from app.core.logging import correlation_id_ctx, logger
from app.core.errors import IncuBrixException, ErrorCodes

class IncuBrixAPIMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Correlation ID
        corr_id = request.headers.get("X-Correlation-Id", f"corr_{uuid.uuid4().hex[:10]}")
        token = correlation_id_ctx.set(corr_id)

        try:
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = corr_id
            return response
        except IncuBrixException as exc:
            logger.warning(f"IncuBrixException [{exc.code}]: {exc.message}")
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "details": exc.details,
                    }
                },
                headers={"X-Correlation-Id": corr_id}
            )
        except Exception as exc:
            logger.error(f"Unhandled Exception: {str(exc)}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": ErrorCodes.INTERNAL_SERVER_ERROR,
                        "message": "An internal server error occurred.",
                        "retryable": True,
                        "details": str(exc),
                    }
                },
                headers={"X-Correlation-Id": corr_id}
            )
        finally:
            correlation_id_ctx.reset(token)
