# tests/test_app.py
import logging
from unittest.mock import patch, MagicMock

from app.db import get_db
from app.services.routing import haversine_km
from app.services.pricing import compute_price

log = logging.getLogger(__name__)

def test_root_redirects_to_login_when_not_authenticated(client):
    log.info("Проверка: нерегистриран потребител на '/' се пренасочва към /login")
    r = client.get("/")
    assert r.status_code == 302
    assert "/login" in (r.headers.get("Location") or "")
    log.info("ОК: статус 302 и Location съдържа /login")

def test_haversine():
    log.info("Проверка: haversine_km за София→Пловдив е в разумен диапазон 100–160 км")
    km = haversine_km(42.6977, 23.3219, 42.1354, 24.7453)
    assert 100 <= km <= 160
    log.info(f"ОК: изчислено разстояние {km:.1f} км (в диапазона)")

def test_company_seed_and_compute_price(app):
    log.info("Проверка: има seed-ната компания id=1 и compute_price връща > 0")
    with app.app_context():
        db = get_db()
        c = db.execute("SELECT * FROM company WHERE id=1").fetchone()
        assert c is not None
        price = compute_price(2.0, True, 10.0, size='M')
        assert price > 0
    log.info(f"ОК: company #1 съществува; compute_price={price:.2f} > 0")

@patch("app.services.routing.requests.get")
def test_api_geocode_rate_limit(mock_get, client):
    log.info("Проверка: /api/geocode при две бързи заявки връща 200, после 429 (rate limit)")
    mock_get.return_value = MagicMock(ok=True, json=lambda: [])
    r1 = client.get("/api/geocode?q=so")
    assert r1.status_code == 200
    r2 = client.get("/api/geocode?q=sofia")
    assert r2.status_code == 429
    log.info("ОК: първа заявка 200, втора 429 (rate limit задейства)")

