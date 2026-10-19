from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.jobs import router as jobs_router
from app.api.v1.endpoints.projects import router as projects_router
from app.api.v1.endpoints.instructions import router as instructions_router
from app.api.v1.endpoints.render import router as render_router
from app.api.v1.endpoints.outputs import router as outputs_router

api_v1_router = APIRouter(prefix="/api/v1")

# Mount endpoints
api_v1_router.include_router(health_router)
api_v1_router.include_router(jobs_router)
api_v1_router.include_router(projects_router)
api_v1_router.include_router(instructions_router)
api_v1_router.include_router(render_router)
api_v1_router.include_router(outputs_router)

# Also expose health at root for standard monitors
root_router = APIRouter()
root_router.include_router(health_router)
root_router.include_router(api_v1_router)
