"""Runner de desarrollo del backend.

En Windows, asyncio usa ProactorEventLoop por defecto, incompatible con psycopg
async (el AsyncPostgresSaver del checkpointer de LangGraph). Hay que fijar
SelectorEventLoop ANTES de que uvicorn cree su loop, por eso esto vive en un runner
y no en app.main (uvicorn importa la app recién dentro del loop ya creado).

Uso:  backend\\.venv\\Scripts\\python backend\\dev.py
"""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402  (debe importarse después de fijar la policy)

# Carpeta backend/ (donde vive el paquete app/), independiente del cwd del shell.
BACKEND_DIR = str(Path(__file__).resolve().parent)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[BACKEND_DIR],  # solo backend/, no node_modules/.git/.next (cwd-agnóstico)
        app_dir=BACKEND_DIR,
    )
