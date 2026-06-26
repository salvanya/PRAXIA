"""Regresión: el runner dev.py debe fijar SelectorEventLoop en Windows.

uvicorn arranca con ProactorEventLoop por defecto en Windows, que psycopg async
(AsyncPostgresSaver del checkpointer) no soporta → el backend crasheaba en el
startup. dev.py fija WindowsSelectorEventLoopPolicy a nivel de módulo, antes de que
uvicorn cree su loop. Este test congela ese contrato.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

DEV_PY = Path(__file__).resolve().parents[1] / "dev.py"


def _load_dev_module():
    spec = importlib.util.spec_from_file_location("praxia_dev_runner", DEV_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # corre el código a nivel de módulo (no el __main__)
    return module


@pytest.mark.skipif(sys.platform != "win32", reason="solo aplica al ProactorEventLoop de Windows")
def test_dev_runner_forces_selector_loop_on_windows():
    saved = asyncio.get_event_loop_policy()
    try:
        _load_dev_module()
        policy = asyncio.get_event_loop_policy()
        assert isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy)
    finally:
        asyncio.set_event_loop_policy(saved)
