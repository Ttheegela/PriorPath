from fastapi import FastAPI

from app import __version__

app = FastAPI(
    title="PriorPath",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
