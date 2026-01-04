from fastapi import APIRouter
from app.api.v1.endpoints import auth, incidents, logs

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
