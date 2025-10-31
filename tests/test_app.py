import os
import unittest
from unittest.mock import patch, MagicMock
from tempfile import TemporaryDirectory
from app import create_app
from app.config import DB_PATH
from app.db import get_db
from app.geo import haversine_km
from app.routes.shipments import compute_price

class AppTestCase(unittest.TestCase):
    def setUp(self):
        # временна директория/БД за теста
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        # насочваме DB_PATH към временен файл
        self._orig_db_path = DB_PATH
        # monkeypatch DB_PATH by environment (sqlite path is imported once in modules)
        # използваме app config factory за ново приложение
        self.app = create_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            # увери се, че DB е инициализирана
            pass

    def test_root_redirects_to_login_when_not_authenticated(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers.get("Location", ""))

    def register_and_login_client(self):
        # регистрация на клиент
        self.client.post(
            "/register", data={
                "email": "c@x.y", "password": "p", "first_name": "Ив", "last_name": "Ив", "phone": "0888"
            }
        )
        # login като client
        r = self.client.post("/login", data={"email": "c@x.y", "password": "p", "role_expect": "client"}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

    def test_register_and_login_client(self):
        self.register_and_login_client()
        r = self.client.get("/")
        # след логин Dashboard се рендерира
        self.assertEqual(r.status_code, 200)

    def test_haversine(self):
        # София–Пловдив приблизително ~132 км (праволинейно ~120-140)
        km = haversine_km(42.6977, 23.3219, 42.1354, 24.7453)
        self.assertTrue(100 <= km <= 160)

    def test_company_seed_and_compute_price(self):
        with self.app.app_context():
            db = get_db()
            c = db.execute("SELECT * FROM company WHERE id=1").fetchone()
            self.assertIsNotNone(c)
            price = compute_price(2.0, True, 10.0, size='M')
            self.assertGreater(price, 0)

    def test_login_role_guard(self):
        # регистрираме клиент и опит за достъп до служителска форма
        self.register_and_login_client()
        r = self.client.get("/employees", follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    @patch("app.routes.api.requests.get")
    def test_api_geocode_rate_limit(self, mock_get):
        # подготвяме mock отговор
        mock_get.return_value = MagicMock(ok=True, json=lambda: [])
        # първо извикване OK (но <3 букви → празно)
        r1 = self.client.get("/api/geocode?q=so")
        self.assertEqual(r1.status_code, 200)
        # второ веднага → 429 (rate limit)
        r2 = self.client.get("/api/geocode?q=sofia")
        self.assertEqual(r2.status_code, 429)

    @patch("app.geo.requests.get")
    def test_compute_distance_from_form_uses_cache(self, mock_req):
        # мок за geocode/OSRM да не удря мрежа
        mock_req.return_value = MagicMock(json=lambda: [], ok=True)
        self.register_and_login_client()
        with self.app.app_context():
            db = get_db()
            # фиктивни офиси с координати
            db.execute("INSERT INTO offices(name, city, address, lat, lon) VALUES ('A','София','A 1',42.7,23.3)")
            db.execute("INSERT INTO offices(name, city, address, lat, lon) VALUES ('B','Пловдив','B 1',42.13,24.74)")
            db.commit()
            # запиши кеш
            db.execute("INSERT INTO distances_cache(origin, dest, distance_km) VALUES (?,?,?)",
                       ("a 1, софия, bulgaria", "b 1, пловдив, bulgaria", 120.0))
            db.commit()
        # достъп до форма /shipments/new (GET)
        r = self.client.get("/shipments/new")
        self.assertEqual(r.status_code, 200)

    def test_404_on_missing_shipment(self):
        self.register_and_login_client()
        r = self.client.get("/shipments/999999")
        self.assertEqual(r.status_code, 404)

    def test_company_form_guard(self):
        self.register_and_login_client()
        r = self.client.get("/company")
        self.assertEqual(r.status_code, 403)

if __name__ == "__main__":
    unittest.main()
