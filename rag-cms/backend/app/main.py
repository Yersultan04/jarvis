import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api import agent, auth, files, ingest, rags, search
from app.auth import hash_password
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import User, UserRole

log = logging.getLogger("startup")

settings = get_settings()


async def _bootstrap_admin_from_env() -> None:
    s = get_settings()
    if not s.bootstrap_admin_email or not s.bootstrap_admin_password:
        return
    async with SessionLocal() as db:
        existing = (await db.execute(select(func.count(User.id)))).scalar_one()
        if existing > 0:
            return
        user = User(
            id=uuid.uuid4(),
            email=s.bootstrap_admin_email.lower().strip(),
            password_hash=hash_password(s.bootstrap_admin_password),
            role=UserRole.admin,
        )
        db.add(user)
        await db.commit()
        log.info("bootstrap admin created from env: %s", user.email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic (`alembic upgrade head`). Do not create_all here —
    # silent table creation diverges from migration history and bites on the first ALTER.
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        await _bootstrap_admin_from_env()
    except Exception as e:
        log.warning("bootstrap admin failed (skipping): %s", e)
    yield
    await engine.dispose()


app = FastAPI(
    title="rag-cms",
    description="Multi-tenant agentic RAG platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(rags.router, prefix="/api/rags", tags=["rags"])
app.include_router(files.router, prefix="/api/rags", tags=["files"])
app.include_router(ingest.router, prefix="/api/rags", tags=["ingest"])
app.include_router(search.router, prefix="/api/rags", tags=["search"])
app.include_router(agent.router, prefix="/api/rags", tags=["agent"])
