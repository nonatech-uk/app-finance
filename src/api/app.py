"""Finance API — FastAPI application."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure project root is on sys.path so `config.*` and `src.*` imports work
# when running via uvicorn from any directory.
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config.settings import settings
from src.api.deps import close_pool, init_pool
from src.api.routers import accounts, assets, auth, cash, categories, imports, merchants, receipts, stats, stocks, tag_rules, transactions
from src.api.routers import settings as settings_router

STATIC_DIR = Path(_project_root) / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage connection pool lifecycle."""
    init_pool()
    yield
    close_pool()


app = FastAPI(
    title="Finance API",
    version="0.1.0",
    description="Personal finance system API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(transactions.router, prefix="/api/v1", tags=["transactions"])
app.include_router(accounts.router, prefix="/api/v1", tags=["accounts"])
app.include_router(categories.router, prefix="/api/v1", tags=["categories"])
app.include_router(merchants.router, prefix="/api/v1", tags=["merchants"])
app.include_router(stats.router, prefix="/api/v1", tags=["stats"])
app.include_router(imports.router, prefix="/api/v1", tags=["imports"])
app.include_router(stocks.router, prefix="/api/v1", tags=["stocks"])
app.include_router(assets.router, prefix="/api/v1", tags=["assets"])
app.include_router(tag_rules.router, prefix="/api/v1", tags=["tag-rules"])
app.include_router(settings_router.router, prefix="/api/v1", tags=["settings"])
app.include_router(cash.router, prefix="/api/v1", tags=["cash"])
app.include_router(receipts.router, prefix="/api/v1", tags=["receipts"])


@app.get("/health")
def health():
    return {"status": "ok"}


# CalDAV for Apple Reminders — serves todo-tagged transactions as VTODOs
from src.caldav.routes import caldav_router, server_root_routes, well_known_routes  # noqa: E402

# Well-known and root PROPFIND must be mounted before /caldav and SPA catch-all
app.mount("/.well-known", well_known_routes)
app.routes.insert(0, server_root_routes.routes[0])  # PROPFIND on / before other routes
app.mount("/caldav", caldav_router)


# Serve React SPA — static assets first, then fallback to index.html
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        file_path = STATIC_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / "index.html")
