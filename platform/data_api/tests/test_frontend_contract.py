"""
Every `/api/...` URL the React client fetches must be a real data_api route.

Regression: `/api/rankings/technicals-columns` 404'd/405'd in the browser because the
frontend called routes the running backend didn't serve. A stale image can't be caught here,
but frontend/backend drift in this repo can -- the failure shows up in CI instead of a console.
"""

import os
import re
from pathlib import Path

import data_api.app as app_module


# `frontend/` is source, never shipped in an image: `make test` mounts the checkout at
# PLATFORM_SOURCE_DIR; a host run finds it two levels up.
_SOURCE = os.environ.get("PLATFORM_SOURCE_DIR")
_PLATFORM_DIR = Path(_SOURCE) if _SOURCE else Path(__file__).resolve().parents[2]
_CLIENT = _PLATFORM_DIR / "frontend" / "src" / "api" / "client.ts"
_CALL = re.compile(
    r"""fetch\(\s*[`"'](/api/[^`"'?]*)[`"']?(?:[^)]*?method:\s*"(\w+)")?""", re.DOTALL
)


def _routes() -> list[tuple[re.Pattern[str], set[str]]]:
    # OpenAPI, not app.routes: included routers are nested objects in this FastAPI version.
    paths = app_module.app.openapi()["paths"]
    return [
        (re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"), {m.upper() for m in ops})
        for path, ops in paths.items()
        if path != "/api/{full_path}"  # SPA/404 catch-all would make every GET pass
    ]


def test_every_client_fetch_hits_a_registered_route() -> None:
    calls = _CALL.findall(_CLIENT.read_text())
    assert len(calls) >= 8  # guards the regex itself against silently matching nothing
    routes = _routes()
    for raw_path, method in calls:
        path = re.sub(r"\$\{[^}]+\}", "x", raw_path)
        verb = method or "GET"
        assert any(p.match(path) and verb in m for p, m in routes), f"{verb} {raw_path}"
