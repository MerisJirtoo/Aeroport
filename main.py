from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from api.routes import router, session
import api.role_routes
from db import seed_db

SESSION_SECRET = "ramp-oto-perron-demo-key"

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    seed_db()
    session.ensure()
    validation = session.state.apron.validate()
    if not validation.ok:
        raise RuntimeError("Некорректная конфигурация карты перрона: " + "; ".join(validation.errors))
    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
    app.include_router(router)

    app.mount("/css", StaticFiles(directory="frontend/css"), name="css")
    app.mount("/js", StaticFiles(directory="frontend/js"), name="js")

    @app.get("/", include_in_schema=False)
    def index_page() -> FileResponse:
        return FileResponse("frontend/index.html")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)