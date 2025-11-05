# tests/conftest.py
import pytest 
import config 
from app import create_app
from app.db import get_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    """
    Създава Flask app за теста и насочва БД към временен файл.
    VS Code ще показва бутони за pytest тестове автоматично.
    """

    test_db_file = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", str(test_db_file), raising=False)


    app = create_app()


    with app.app_context():
        _ = get_db()

    yield app 


@pytest.fixture
def client(app):
    """Flask test клиент за HTTP заявки."""
    return app.test_client()


# ---------------- Помощници за логин като фикстури ----------------

@pytest.fixture
def register_and_login_client(client):
    """Регистрира клиент и влиза като клиент. Използване: просто извикай фикстурата в теста."""
    # регистрация (/register -> auth.register_client)
    client.post(
        "/register",
        data={
            "email": "c@x.y",
            "password": "p",
            "first_name": "Ив",
            "last_name": "Ив",
            "phone": "0888",
        },
        follow_redirects=True,
    )
    # вход като клиент
    r = client.post(
        "/login",
        data={"email": "c@x.y", "password": "p", "role_expect": "client"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    return client  # върни готов, логнат client


@pytest.fixture
def login_admin(client):
    """Вход като seed-натия админ (admin@company.com / admin123)."""
    r = client.post(
        "/login",
        data={
            "email": "admin@company.com",
            "password": "admin123",
            "role_expect": "staff",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    return client
