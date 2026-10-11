from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from releasewatch import __version__
from releasewatch.api import events, releases, services, stats, webhooks
from releasewatch.config import get_settings
from releasewatch.db import get_session


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ReleaseWatch",
        version=__version__,
        description="Ingests CI deployment webhooks and verifies release health.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (webhooks, releases, services, events, stats):
        app.include_router(module.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str | None]:
        """Liveness: the process is up, and which build it is running."""
        return {"status": "ok", "version": __version__, "commit": settings.git_sha}

    @app.get("/readyz", tags=["ops"])
    def readyz(session: Session = Depends(get_session)) -> dict[str, str]:
        """Readiness: the database is reachable and migrated."""
        try:
            session.execute(text("SELECT 1 FROM releases LIMIT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ready"}

    if settings.static_dir and Path(settings.static_dir, "index.html").is_file():
        _mount_frontend(app, Path(settings.static_dir))

    return app


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve the built React app, falling back to index.html for client-side routes."""
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    index = static_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
