import importlib
from pathlib import Path


def test_settings_rejects_missing_helius_key(monkeypatch):
    try:
        settings_module = importlib.import_module("golden_dog.config")
    except ModuleNotFoundError:
        settings_module = None

    assert settings_module is not None

    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    monkeypatch.setenv("BARK_BASE_URL", "https://api.day.app")

    settings = settings_module.Settings.from_env()

    assert settings.helius_api_key is None
    assert settings.bark_base_url == "https://api.day.app"


def test_env_example_includes_only_public_wallet_address_setting():
    env_example = (Path(__file__).parent.parent / ".env.example").read_text()
    normalized = env_example.lower()

    assert "WATCH_WALLET_ADDRESS=" in env_example
    assert "private_key" not in normalized
    assert "seed" not in normalized
