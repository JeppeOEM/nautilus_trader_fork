# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Story 15.1 Task 2/3/7: app.frontend() SPA serving, the /api/* 404 boundary, and the
/api/health pilot route.
"""

import importlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import data_api.app as app_module


_REPO_ROOT = Path(__file__).resolve().parents[2]  # platform/data_api/tests -> platform


def test_api_health_returns_pydantic_modeled_json() -> None:
    client = TestClient(app_module.app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unmatched_api_path_returns_json_404_not_spa_html() -> None:
    """
    The exact disaster app.frontend()'s fallback="auto" would otherwise cause: a browser's
    default `Accept: text/html` navigation request to an unmatched /api/* path must not
    silently fall back to the SPA's index.html -- confirmed via real TestClient requests
    during this story (not assumed), see Story 15.1's Dev Agent Record.
    """
    client = TestClient(app_module.app)

    plain = client.get("/api/does-not-exist")
    with_html_accept = client.get("/api/does-not-exist", headers={"accept": "text/html"})

    for response in (plain, with_html_accept):
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_no_fastapi_builtin_docs_route_collides_with_the_spa_docs_page() -> None:
    """docs_url/redoc_url=None -- FastAPI's own Swagger UI must not shadow this app's
    own /docs SPA route (Signal Atlas, FR45)."""
    paths = {route.path for route in app_module.app.routes if hasattr(route, "path")}

    assert "/docs" not in paths
    assert "/redoc" not in paths


def test_docs_spa_route_serves_the_built_index_html(tmp_path: Path) -> None:
    """
    FRONTEND_DIST_PATH is read once at module import time and baked into the
    app.frontend() call (correct for a real deployed process -- no reason to re-resolve
    it per-request) -- so this test reloads the module with the env var pointed at a real
    built dist/, then reloads it back to the default afterward so other test modules in
    this session see the unmodified app.
    """
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>SPA SHELL</body></html>")

    previous = os.environ.get("FRONTEND_DIST_PATH")
    os.environ["FRONTEND_DIST_PATH"] = str(dist_dir)
    try:
        importlib.reload(app_module)
        client = TestClient(app_module.app)

        response = client.get("/docs", headers={"accept": "text/html"})

        assert response.status_code == 200
        assert "SPA SHELL" in response.text
    finally:
        if previous is None:
            os.environ.pop("FRONTEND_DIST_PATH", None)
        else:
            os.environ["FRONTEND_DIST_PATH"] = previous
        importlib.reload(app_module)


def test_committed_openapi_json_matches_the_live_schema() -> None:
    """
    Code-review follow-up: `frontend/openapi.json` (and the `schema.ts` generated from it)
    is a manually-regenerated, committed file -- nothing previously caught it silently
    drifting out of sync with `data_api.app`'s real schema after a route/model change.
    Regenerate via: `PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json`
    (from `platform/`), then `cd frontend && npm run codegen`.
    """
    committed_path = _REPO_ROOT / "frontend" / "openapi.json"
    committed = json.loads(committed_path.read_text())

    assert committed == app_module.app.openapi()
