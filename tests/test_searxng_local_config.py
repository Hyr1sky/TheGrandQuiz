"""可选本地 SearXNG 配置必须保持单容器、loopback 与 JSON API 边界。"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).parents[1]


def test_local_searxng_compose_is_private_and_has_no_valkey_dependency() -> None:
    compose = yaml.safe_load((_ROOT / "deploy/searxng/compose.yaml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"searxng"}
    service = compose["services"]["searxng"]
    assert service["ports"] == ["127.0.0.1:8080:8080"]
    assert service["volumes"] == ["./settings.yml:/etc/searxng/settings.yml:ro"]


def test_local_searxng_enables_json_without_public_instance_features() -> None:
    settings = yaml.safe_load((_ROOT / "deploy/searxng/settings.yml").read_text(encoding="utf-8"))

    assert settings["search"]["formats"] == ["html", "json"]
    assert settings["server"]["limiter"] is False
    assert settings["server"]["image_proxy"] is False
