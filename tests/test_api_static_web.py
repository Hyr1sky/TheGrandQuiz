"""已安装 ``grandquiz-web`` 的同源静态工作台契约。"""

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from grandquiz.interfaces.api.serve import mount_web_static


def _static_fixture(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>TheGrandQuiz</title>")
    (static / "app.js").write_text("console.log('app')")
    return static


def test_static_web_keeps_api_precedence_and_supports_spa_routes(tmp_path: Path) -> None:
    app = FastAPI()

    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/api/v1/health", health, methods=["GET"])
    assert mount_web_static(app, _static_fixture(tmp_path)) is True

    with cast("Any", TestClient(app)) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert "TheGrandQuiz" in client.get("/").text
        assert "TheGrandQuiz" in client.get("/study/session").text
        assert client.get("/api/v1/missing").status_code == 404
        assert client.get("/missing.js").status_code == 404


def test_missing_static_directory_is_an_explicit_noop(tmp_path: Path) -> None:
    assert mount_web_static(FastAPI(), tmp_path / "missing") is False
