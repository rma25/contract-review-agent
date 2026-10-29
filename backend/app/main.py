from fastapi import FastAPI

from app.api import contracts, health


def create_app() -> FastAPI:
    app = FastAPI(title="Contract Review Agent API", version="0.1.0")
    app.include_router(health.router)
    app.include_router(contracts.router)
    return app


app = create_app()
