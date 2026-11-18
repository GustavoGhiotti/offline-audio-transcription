from __future__ import annotations

import os

import uvicorn


def executar() -> None:
    uvicorn.run(
        "src.api.app:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
        workers=int(os.getenv("API_WORKERS", "1")),
    )


if __name__ == "__main__":
    executar()
