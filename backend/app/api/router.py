from fastapi import APIRouter

from app.api.v1.routes.alerts import router as alerts_router
from app.api.v1.routes.cases import router as cases_router
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.knowledge import router as knowledge_router
from app.api.v1.routes.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(knowledge_router, prefix="/knowledge", tags=["knowledge"])
api_router.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
api_router.include_router(cases_router, prefix="/alerts", tags=["cases"])
api_router.include_router(workflows_router, prefix="/alerts", tags=["workflows"])
