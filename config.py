import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    # име на приложението (както в app.py)
    APP_NAME = "PLM Logistics"

    # файл на SQLite БД (както в app.py -> DB_PATH = Path("logistics.db"))
    DATABASE = BASE_DIR / "logistics.db"

    # Flask секрет за сесии (както в app.py)
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    # Optional: ORS API key (free tier available). If missing: OSRM demo -> Haversine.
    ORS_API_KEY = os.getenv("ORS_API_KEY")
