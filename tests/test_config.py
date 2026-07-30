import importlib


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
