from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, router
from app.core.config import get_settings

app = FastAPI(title="Maç Analiz API", version="0.1.0")
origins = [s.strip() for s in get_settings().cors_origins.split(",") if s.strip()]
origins = [origin if "://" in origin else f"https://{origin}" for origin in origins]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])
app.get("/health")(health)
app.include_router(router, prefix="/api/v1")
app.include_router(router, prefix="/api")
