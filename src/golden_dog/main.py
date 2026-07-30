"""ASGI entry point for the read-only signal dashboard."""

from .api import create_app
from .config import Settings
from .repository import Repository


app = create_app(Repository(Settings.from_env().database_path))
