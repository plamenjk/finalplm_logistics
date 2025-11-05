import sqlite3
from pathlib import Path
from flask import g, current_app

# ======================
# Начална SQL схема на БД
# ======================
# Създава всички таблици, ако не съществуват (idempotent).
# PRAGMA foreign_keys = ON за поддръжка на каскадни изтривания и FK правила в SQLite.
SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS company (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    office_delivery_multiplier REAL NOT NULL DEFAULT 1.0,
    address_delivery_multiplier REAL NOT NULL DEFAULT 1.4,
    base_price_per_kg REAL NOT NULL DEFAULT 2.50,
    per_km_rate REAL NOT NULL DEFAULT 0.40,
    size_multiplier_s REAL NOT NULL DEFAULT 0.90,
    size_multiplier_m REAL NOT NULL DEFAULT 1.00,
    size_multiplier_l REAL NOT NULL DEFAULT 1.20
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('employee','client','admin','courier')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS offices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT NOT NULL,
    lat REAL,
    lon REAL
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    office_id INTEGER,
    phone TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(office_id) REFERENCES offices(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    phone TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_client_id INTEGER NOT NULL,
    recipient_client_id INTEGER NOT NULL,
    origin_office_id INTEGER,
    destination_office_id INTEGER,
    delivery_address TEXT,
    pickup_address TEXT,
    weight_kg REAL NOT NULL CHECK(weight_kg > 0),
    distance_km REAL NOT NULL DEFAULT 1.0,
    to_office INTEGER NOT NULL CHECK (to_office IN (0,1)),
    size TEXT NOT NULL DEFAULT 'M', -- 'S','M','L'
    return_to_office INTEGER NOT NULL DEFAULT 0,
    return_office_id INTEGER,
    price REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('registered','in_transit','delivered')) DEFAULT 'registered',
    registered_by_employee_id INTEGER NOT NULL,
    courier_employee_id INTEGER,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at TEXT,
    FOREIGN KEY(sender_client_id) REFERENCES clients(id),
    FOREIGN KEY(recipient_client_id) REFERENCES clients(id),
    FOREIGN KEY(origin_office_id) REFERENCES offices(id),
    FOREIGN KEY(destination_office_id) REFERENCES offices(id),
    FOREIGN KEY(return_office_id) REFERENCES offices(id),
    FOREIGN KEY(registered_by_employee_id) REFERENCES employees(id),
    FOREIGN KEY(courier_employee_id) REFERENCES employees(id)
);

CREATE TABLE IF NOT EXISTS distances_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    dest TEXT NOT NULL,
    distance_km REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(origin, dest)
);
"""

# ======================
# Помощна функция
# ======================

def ensure_file_exists(pathlike):
    """Създава празен .db файл, ако липсва (без да трие съдържание)."""
    p = Path(pathlike)
    if not p.exists():
        p.touch()

# ======================
# DB Connection per request
# ======================

def get_db():
    """
    Lazy-load връзка към SQLite.
    Записва се в flask.g за текущия request → 1 connection на заявка.
    Row factory: позволява достъп: row["column"] вместо row[0].
    """
    if "db" not in g:
        db_path = current_app.config["DATABASE"]
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(exc=None):
    """
    Затваря DB connection в края на request.
    Flask автоматично извиква това чрез teardown_appcontext.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_app(app):
    """Регистрира автоматично затваряне на DB при край на request."""
    app.teardown_appcontext(close_db)

# ======================
# Schema Init + Seed
# ======================

def init_schema():
    """
    Създава всички таблици (ако липсват) и seed-ва:
      - компанията с id=1
      - един admin акаунт, ако няма
    Изпълнява се при първи достъп.
    """
    db = get_db()
    db.executescript(SCHEMA_SQL)

    # seed компания
    if not db.execute("SELECT 1 FROM company WHERE id=1").fetchone():
        db.execute("INSERT INTO company (id, name) VALUES (1, ?)", ("PLM Logistics",))

    # seed admin
    if not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        from werkzeug.security import generate_password_hash
        db.execute(
            "INSERT INTO users(email, password_hash, role) VALUES (?,?, 'admin')",
            ("admin@company.com", generate_password_hash("admin123")),
        )

    db.commit()

# ======================
# Прости миграции (без Alembic)
# ======================

def ensure_migrations():
    """
    Проверява за липсващи колони и ги добавя.
    Мини-миграции за SQLite (idempotent),
    така че потребителите да получават нови полета без да губят данни.
    """
    db = get_db()

    def has_col(table, col):
        return any(r["name"] == col for r in db.execute(f"PRAGMA table_info({table})").fetchall())

    # offices lat/lon
    if not has_col("offices","lat"):
        db.execute("ALTER TABLE offices ADD COLUMN lat REAL")
    if not has_col("offices","lon"):
        db.execute("ALTER TABLE offices ADD COLUMN lon REAL")

    # shipments нови полета
    if not has_col("shipments","size"):
        db.execute("ALTER TABLE shipments ADD COLUMN size TEXT NOT NULL DEFAULT 'M'")
    if not has_col("shipments","return_to_office"):
        db.execute("ALTER TABLE shipments ADD COLUMN return_to_office INTEGER NOT NULL DEFAULT 0")
    if not has_col("shipments","return_office_id"):
        db.execute("ALTER TABLE shipments ADD COLUMN return_office_id INTEGER")

    # company size multipliers
    if not has_col("company","size_multiplier_s"):
        db.execute("ALTER TABLE company ADD COLUMN size_multiplier_s REAL NOT NULL DEFAULT 0.90")
    if not has_col("company","size_multiplier_m"):
        db.execute("ALTER TABLE company ADD COLUMN size_multiplier_m REAL NOT NULL DEFAULT 1.00")
    if not has_col("company","size_multiplier_l"):
        db.execute("ALTER TABLE company ADD COLUMN size_multiplier_l REAL NOT NULL DEFAULT 1.20")

    db.commit()
