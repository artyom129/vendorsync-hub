from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("VENDORSYNC_HOST", "0.0.0.0"),
        port=int(os.getenv("VENDORSYNC_PORT", "8000")),
        reload=False,
    )
