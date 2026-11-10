"""RoomShift API (frozen contract v0.1.0, see contracts/api-contract.md).

Run with `uvicorn roomshift_api.main:app` (one worker: jobs and locks live in this process).
`app` is created on first access, so importing this module has no side effects.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import SCHEMA_VERSION, Settings
from .dev import seed_fixture
from .errors import ApiError, error_body
from .jobs import JobRunner
from .middleware import LocalGuard
from .routers import assemblies, captures, jobs, projects
from .storage import Storage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    schema_path = settings.contracts_dir / "scene.schema.json"
    if not schema_path.is_file():
        raise RuntimeError(f"Frozen contract not found at {schema_path}; set ROOMSHIFT_CONTRACTS_DIR.")
    storage = Storage(settings.data_dir)
    runner = JobRunner(storage, schema_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.dev_seed_fixture:
            seed_fixture(storage, settings.contracts_dir)
        runner.start()
        yield
        runner.stop()

    app = FastAPI(title="RoomShift API", version=SCHEMA_VERSION, lifespan=lifespan)
    app.state.settings, app.state.storage, app.state.runner = settings, storage, runner
    # Order matters: CORS is outermost so the guard's rejections and 500s still carry CORS headers.
    # Unexpected exceptions become INTERNAL_ERROR inside LocalGuard for the same reason.
    app.add_middleware(LocalGuard, settings=settings)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse(e.body(), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _req_validation(_: Request, e: RequestValidationError):
        details = [{"path": "/" + "/".join(str(p) for p in err.get("loc", ())), "message": err.get("msg", "")}
                   for err in e.errors()]
        return JSONResponse(error_body("VALIDATION_ERROR", "Request validation failed.", details), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, "HTTP_ERROR")
        return JSONResponse(error_body(code, str(e.detail)), status_code=e.status_code)

    for router in (assemblies.router, projects.router, captures.router, jobs.router):
        app.include_router(router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """Lazily build `app` for uvicorn, so tests and scripts can import create_app without side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
