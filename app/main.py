from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.core.config import settings
from app.api.middleware import IncuBrixAPIMiddleware
from app.api.v1.router import root_router

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="IncuBrix Prompt-Based Video Editing Engine API. All API routes under /api/v1/.",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom IncuBrix API Middleware (Correlation ID, JSON error standardization)
app.add_middleware(IncuBrixAPIMiddleware)

from fastapi.requests import Request
from fastapi.responses import JSONResponse
from app.core.errors import IncuBrixException

@app.exception_handler(IncuBrixException)
async def incubrix_exception_handler(request: Request, exc: IncuBrixException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable, "details": exc.details}}
    )

# Mount Routes
app.include_router(root_router)

# Mount frontend build if available
frontend_dist = settings.BASE_DIR / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
