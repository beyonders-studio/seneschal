"""The served app: ``uvicorn hoard.main:app --host 0.0.0.0 --port 8300``."""

from hoard.app import create_app

app = create_app()
