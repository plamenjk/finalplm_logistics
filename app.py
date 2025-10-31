import os  # системни променливи/пътеки
import sqlite3  # вградената SQLite БД
import math  # математика (Haversine)
from datetime import datetime  # времена/дати
import time  # throttling за API
from pathlib import Path  # удобни пътища

import requests  # HTTP заявки към външни услуги
from flask import Flask, g, redirect, render_template, request, session, url_for, abort, flash, jsonify  # Flask уеб фреймуърк
from werkzeug.security import generate_password_hash, check_password_hash  # хеширане на пароли

APP_NAME = "PLM Logistics"  # име на приложението
DB_PATH = Path("logistics.db")  # файл на SQLite БД
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")  # Flask секрет за сесии

# Optional: ORS API key (free tier available). If missing: OSRM demo -> Haversine.
ORS_API_KEY = os.getenv("ORS_API_KEY")  # ключ за OpenRouteService (ако има)

app = Flask(__name__)  # Flask приложение
GEO_THROTTLE = {}  # ip -> last_request_ts (опростен rate limit)
app.config.update(SECRET_KEY=SECRET_KEY)  # настройка на секретния ключ

# ---------------- DB ----------------
def get_db():
    """Отваря/връща connection към SQLite и задава Row factory."""
    if "db" not in g:  # lazy инициализация в request context
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row  # достъп по име на колони
    return g.db

@app.teardown_appcontext
def close_db(exc):
    """Затваря DB connection след request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()

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
"""  # SQL схема: таблици за компания/потребители/офиси/служители/клиенти/пратки/кеш за разстояния

def init_db():
    """Създава схема и seed-ва компания+админ, ако липсват."""
    db = get_db()
    db.executescript(SCHEMA_SQL)  # изпълнява целия скрипт
    # seed company
    if not db.execute("SELECT 1 FROM company WHERE id=1").fetchone():
        db.execute("INSERT INTO company (id, name) VALUES (1, ?)", ("PLM Logistics",))
    # seed admin
    if not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        db.execute("INSERT INTO users(email, password_hash, role) VALUES (?,?, 'admin')",
                   ("admin@company.com", generate_password_hash("admin123")))
    db.commit()

def ensure_migrations():
    """Проверява и добавя липсващи колони (прости миграции)."""
    db = get_db()
    def has_col(table, col):
        return any(r["name"] == col for r in db.execute(f"PRAGMA table_info({table})").fetchall())
    # offices lat/lon
    if not has_col("offices","lat"):
        db.execute("ALTER TABLE offices ADD COLUMN lat REAL")
    if not has_col("offices","lon"):
        db.execute("ALTER TABLE offices ADD COLUMN lon REAL")
    # shipments new fields
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

@app.before_request
def before_request():
    """Инициализация на БД и „миграции“ преди всеки request."""
    if not DB_PATH.exists():
        DB_PATH.touch()  # създай празен файл, ако липсва
    init_db()
    ensure_migrations()

# -------------- helpers & auth --------------
def current_user():
    """Връща текущия потребител от сесията (Row) или None."""
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def login_required(roles=None):
    """Декоратор: изисква логин (+по избор роля/и)."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for('login', next=request.path))  # праща към login
            if roles and user['role'] not in roles:
                abort(403)  # забранен достъп
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__  # запази името за Flask
        return wrapper
    return decorator

def compute_price(weight_kg, to_office, distance_km, size='M'):
    """Изчислява цена по формулата и настройки на компанията."""
    db = get_db()
    c = db.execute("""SELECT base_price_per_kg, per_km_rate,
                             office_delivery_multiplier, address_delivery_multiplier,
                             size_multiplier_s, size_multiplier_m, size_multiplier_l
                      FROM company WHERE id=1""").fetchone()
    base = c["base_price_per_kg"] * max(float(weight_kg), 0.0)  # базова по кг
    dist = c["per_km_rate"] * max(float(distance_km or 0.0), 0.0)  # по км
    mult_delivery = c["office_delivery_multiplier"] if to_office else c["address_delivery_multiplier"]
    size = (size or 'M').upper()
    mult_size = c["size_multiplier_m"]
    if size == 'S': mult_size = c["size_multiplier_s"]
    if size == 'L': mult_size = c["size_multiplier_l"]
    return round((base + dist) * mult_delivery * mult_size, 2)  # крайна цена

def office_full_address(office_id):
    """Строи пълен адрес на офис (за геокодиране)."""
    if not office_id:
        return None
    db = get_db()
    o = db.execute("SELECT city, address FROM offices WHERE id=?", (office_id,)).fetchone()
    if not o:
        return None
    return f"{o['address']}, {o['city']}, Bulgaria"

def office_coords(office_id):
    """Връща (lat, lon) на офис, ако има координати."""
    if not office_id:
        return None
    db = get_db()
    o = db.execute("SELECT lat, lon FROM offices WHERE id=?", (office_id,)).fetchone()
    if o and o["lat"] is not None and o["lon"] is not None:
        return float(o["lat"]), float(o["lon"])
    return None

# ---- Open geocoding/routing ----
def geocode_address(addr: str):
    """Геокодира адрес чрез Nominatim → (lat,lon) или None."""
    if not addr:
        return None
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": addr, "format": "json", "limit": 1},
            headers={"User-Agent": "LogisticsApp/1.0 (contact: demo@example.com)"},
            timeout=10
        )
        j = r.json()
        if isinstance(j, list) and j:
            return float(j[0]["lat"]), float(j[0]["lon"])
    except Exception:
        return None
    return None

def haversine_km(lat1, lon1, lat2, lon2):
    """Право-линейно разстояние (Haversine) в км."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return round(2*R*math.asin(math.sqrt(a)), 2)

def ors_distance_km_coords(o_lat, o_lon, d_lat, d_lon):
    """Разстояние по път (ORS directions) между координати."""
    if not ORS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.openrouteservice.org/v2/directions/driving-car",
            params={"api_key": ORS_API_KEY, "start": f"{o_lon},{o_lat}", "end": f"{d_lon},{d_lat}"},
            timeout=12
        )
        data = r.json()
        if data.get("features"):
            meters = data["features"][0]["properties"]["summary"]["distance"]
            return round(meters / 1000.0, 2)
    except Exception:
        return None
    return None

def osrm_distance_km_coords(o_lat, o_lon, d_lat, d_lon):
    """Разстояние по път (OSRM public demo) между координати."""
    try:
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}",
            params={"overview": "false", "alternatives": "false", "steps": "false"},
            timeout=10
        )
        j = r.json()
        if j.get("routes"):
            meters = j["routes"][0]["distance"]
            return round(meters/1000.0, 2)
    except Exception:
        return None
    return None

def ors_distance_km(origin_addr: str, dest_addr: str):
    """ORS разстояние по адреси (геокодира + directions)."""
    if not ORS_API_KEY:
        return None
    o = geocode_address(origin_addr)
    d = geocode_address(dest_addr)
    if not o or not d:
        return None
    return ors_distance_km_coords(o[0], o[1], d[0], d[1])

def osrm_distance_km(origin_addr: str, dest_addr: str):
    """OSRM разстояние по адреси (геокодира + route)."""
    o = geocode_address(origin_addr)
    d = geocode_address(dest_addr)
    if not o or not d:
        return None
    return osrm_distance_km_coords(o[0], o[1], d[0], d[1])

def compute_distance_from_form(form):
    """Изчислява разстояние според полетата от форма + кеш/ORS/OSRM/Haversine."""
    pickup_from_office = form.get('pickup_from_office') == 'on'
    origin_office_id = form.get('origin_office_id')
    origin_office_id = int(origin_office_id) if origin_office_id else None
    pickup_address = form.get('pickup_address') or None

    to_office = form.get('to_office') == 'on'
    dest_office_id = form.get('destination_office_id')
    dest_office_id = int(dest_office_id) if dest_office_id else None
    delivery_address = form.get('delivery_address') or None

    origin_addr = office_full_address(origin_office_id) if pickup_from_office else pickup_address
    dest_addr   = office_full_address(dest_office_id) if to_office else delivery_address

    if not (origin_addr and dest_addr):
        return None  # липсват данни

    db = get_db()
    key_o = origin_addr.strip().lower()  # ключ за кеш
    key_d = dest_addr.strip().lower()
    row = db.execute("SELECT distance_km FROM distances_cache WHERE origin=? AND dest=?", (key_o, key_d)).fetchone()
    if row:
        return float(row['distance_km'])  # връща от кеша

    # вземи координати (офис → директно; адрес → геокодиране)
    o_ll = office_coords(origin_office_id) if pickup_from_office else geocode_address(pickup_address)
    d_ll = office_coords(dest_office_id)   if to_office         else geocode_address(delivery_address)

    dist = None
    if o_ll and d_ll:
        dist = ors_distance_km_coords(o_ll[0], o_ll[1], d_ll[0], d_ll[1])  # 1) ORS
        if dist is None:
            dist = osrm_distance_km_coords(o_ll[0], o_ll[1], d_ll[0], d_ll[1])  # 2) OSRM

    if dist is None:
        dist = ors_distance_km(origin_addr, dest_addr)  # 3) ORS by адреси
    if dist is None:
        dist = osrm_distance_km(origin_addr, dest_addr)  # 4) OSRM by адреси
    if dist is None and o_ll and d_ll:
        dist = haversine_km(o_ll[0], o_ll[1], d_ll[0], d_ll[1])  # 5) fallback Haversine

    if dist is not None:
        try:
            db.execute("INSERT OR REPLACE INTO distances_cache(origin, dest, distance_km) VALUES (?,?,?)", (key_o, key_d, dist))
            db.commit()
        except Exception:
            pass  # кешът е „best-effort“
    return dist

@app.context_processor
def inject_globals():
    """Инжектира глобали към шаблоните (име на app и текущ потребител)."""
    return {"app_name": APP_NAME, "user": current_user()}

# -------------- AUTH --------------
@app.route("/login", methods=["GET","POST"])
def login():
    """Вход: проверка на имейл/парола и очаквана роля (client/staff)."""
    if request.method == "POST":
        role_expect = request.form.get('role_expect')
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if role_expect == 'client' and user['role'] != 'client':
                flash('Профилът не е клиентски. Изберете таб „Служител/Куриер“.')
                return redirect(url_for('login'))
            if role_expect == 'staff' and user['role'] not in ('employee','admin','courier'):
                flash('Профилът не е служителски. Изберете таб „Клиент“.')
                return redirect(url_for('login'))
            session["user_id"] = user["id"]  # логване
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Невалиден имейл или парола.")
    return render_template("login.html", title="Вход")

@app.route("/logout")
def logout():
    """Изход: чисти сесията и праща към login."""
    session.clear()
    return redirect(url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register_client():
    """Регистрация на клиентски профил + запис в clients."""
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        first_name = request.form.get("first_name","").strip()
        last_name = request.form.get("last_name","").strip()
        phone = request.form.get("phone","").strip()
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("Имейлът вече е зает.")
        else:
            db.execute("INSERT INTO users(email, password_hash, role) VALUES (?,?, 'client')",
                       (email, generate_password_hash(password)))
            uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO clients(user_id, first_name, last_name, phone) VALUES (?,?,?,?)",
                       (uid, first_name, last_name, phone))
            db.commit()
            flash("Успешна регистрация. Влезте в профила си.")
            return redirect(url_for("login"))
    return render_template("register.html", title="Регистрация")

# -------------- DASHBOARD --------------
@app.route("/")
def dashboard():
    """Начална страница: списък пратки (последни 20)."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    if user["role"] in ("employee","admin","courier"):  # вътрешни роли → всички
        shipments = db.execute(
            """
            SELECT s.*, sc.first_name||' '||sc.last_name as sender_name,
                   rc.first_name||' '||rc.last_name as recipient_name
            FROM shipments s
            JOIN clients sc ON sc.id=s.sender_client_id
            JOIN clients rc ON rc.id=s.recipient_client_id
            ORDER BY s.id DESC LIMIT 20
            """
        ).fetchall()
    else:  # клиент → само неговите
        client = db.execute("SELECT id FROM clients WHERE user_id=?", (user["id"],)).fetchone()
        shipments = db.execute(
            """
            SELECT s.*, sc.first_name||' '||sc.last_name as sender_name,
                   rc.first_name||' '||rc.last_name as recipient_name
            FROM shipments s
            JOIN clients sc ON sc.id=s.sender_client_id
            JOIN clients rc ON rc.id=s.recipient_client_id
            WHERE s.sender_client_id=? OR s.recipient_client_id=?
            ORDER BY s.id DESC LIMIT 20
            """,
            (client["id"], client["id"])
        ).fetchall()
    return render_template("dashboard.html", title="Табло", shipments=shipments)

# -------------- OFFICES --------------
@app.route('/offices')
def offices_list():
    """Списък офиси (достъпен само след логин)."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    offices = db.execute("SELECT * FROM offices ORDER BY city, name").fetchall()
    return render_template("offices_list.html", title="Офиси", offices=offices)

@app.route('/offices/new', methods=['GET','POST'])
@login_required(roles=['employee','admin'])
def offices_new():
    """Създаване на нов офис + опит за геокодиране."""
    if request.method == 'POST':
        db = get_db()
        name = request.form['name'].strip()
        city = request.form['city'].strip()
        address = request.form['address'].strip()
        full = f"{address}, {city}, Bulgaria"
        latlon = geocode_address(full)
        lat, lon = (latlon if latlon else (None, None))
        db.execute("INSERT INTO offices(name, city, address, lat, lon) VALUES (?,?,?,?,?)",
                   (name, city, address, lat, lon))
        db.commit()
        flash("Офисът е създаден." + ("" if latlon else " (неуспешно геокодиране)"))
        return redirect(url_for('offices_list'))
    return render_template("offices_form.html", title="Нов офис", office=None)

@app.route('/offices/<int:office_id>/edit', methods=['GET','POST'])
@login_required(roles=['employee','admin'])
def offices_edit(office_id):
    """Редакция на офис (обновява координати при успех)."""
    db = get_db()
    office = db.execute("SELECT * FROM offices WHERE id=?", (office_id,)).fetchone()
    if not office: abort(404)
    if request.method == 'POST':
        name = request.form['name'].strip()
        city = request.form['city'].strip()
        address = request.form['address'].strip()
        full = f"{address}, {city}, Bulgaria"
        latlon = geocode_address(full)
        lat, lon = (latlon if latlon else (office["lat"], office["lon"]))
        db.execute("UPDATE offices SET name=?, city=?, address=?, lat=?, lon=? WHERE id=?",
                   (name, city, address, lat, lon, office_id))
        db.commit()
        flash("Офисът е обновен." + ("" if latlon else " (запазени са предишните координати)"))
        return redirect(url_for('offices_list'))
    return render_template("offices_form.html", title="Редакция на офис", office=office)

@app.route('/offices/<int:office_id>/delete')
@login_required(roles=['employee','admin'])
def offices_delete(office_id):
    """Изтриване на офис."""
    db = get_db()
    db.execute("DELETE FROM offices WHERE id=?", (office_id,))
    db.commit()
    return redirect(url_for('offices_list'))

# -------------- CLIENTS --------------
@app.route('/clients')
@login_required(roles=['employee','admin'])
def clients_list():
    """Списък клиенти (служители/админ)."""
    db = get_db()
    clients = db.execute("SELECT c.*, u.email FROM clients c JOIN users u ON u.id=c.user_id ORDER BY c.first_name, c.last_name").fetchall()
    return render_template("clients_list.html", title="Клиенти", clients=clients)

@app.route('/clients/new', methods=['GET','POST'])
@login_required(roles=['employee','admin'])
def clients_new():
    """Създава клиент (users+clients)."""
    if request.method == 'POST':
        db = get_db()
        email = request.form['email'].strip().lower()
        password = request.form.get('password') or 'changeme123'
        db.execute("INSERT INTO users(email, password_hash, role) VALUES (?,?, 'client')",
                   (email, generate_password_hash(password)))
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO clients(user_id, first_name, last_name, phone) VALUES (?,?,?,?)",
                   (uid, request.form['first_name'], request.form['last_name'], request.form['phone']))
        db.commit()
        return redirect(url_for('clients_list'))
    return render_template("clients_form.html", title="Нов клиент", client=None)

@app.route('/clients/<int:client_id>/edit', methods=['GET','POST'])
@login_required(roles=['employee','admin'])
def clients_edit(client_id):
    """Редакция на клиент (имена/телефон)."""
    db = get_db()
    client = db.execute("SELECT c.*, u.email FROM clients c JOIN users u ON u.id=c.user_id WHERE c.id=?", (client_id,)).fetchone()
    if not client: abort(404)
    if request.method == 'POST':
        db.execute("UPDATE clients SET first_name=?, last_name=?, phone=? WHERE id=?",
                   (request.form['first_name'], request.form['last_name'], request.form['phone'], client_id))
        db.commit()
        return redirect(url_for('clients_list'))
    return render_template("clients_form.html", title="Редакция на клиент", client=client)

@app.route('/clients/<int:client_id>/delete')
@login_required(roles=['employee','admin'])
def clients_delete(client_id):
    """Изтрива клиент, като трие и свързания user (ON DELETE CASCADE не важи през join)."""
    db = get_db()
    row = db.execute("SELECT user_id FROM clients WHERE id=?", (client_id,)).fetchone()
    if row:
        db.execute("DELETE FROM users WHERE id=?", (row["user_id"],))
        db.commit()
    return redirect(url_for('clients_list'))

# -------------- COMPANY --------------
@app.route('/company', methods=['GET','POST'])
@login_required(roles=['employee','admin'])
def company_form():
    """Форма за настройки на компанията (цени/множители)."""
    db = get_db()
    c = db.execute("SELECT * FROM company WHERE id=1").fetchone()
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        base_price = float(request.form.get('base_price_per_kg','2.5') or 2.5)
        per_km_rate = float(request.form.get('per_km_rate','0.40') or 0.40)
        off_mult = float(request.form.get('office_delivery_multiplier','1.0') or 1.0)
        addr_mult = float(request.form.get('address_delivery_multiplier','1.4') or 1.4)
        size_s = float(request.form.get('size_multiplier_s','0.90') or 0.90)
        size_m = float(request.form.get('size_multiplier_m','1.00') or 1.00)
        size_l = float(request.form.get('size_multiplier_l','1.20') or 1.20)
        if c:
            db.execute("""UPDATE company 
                          SET name=?, base_price_per_kg=?, per_km_rate=?, 
                              office_delivery_multiplier=?, address_delivery_multiplier=?,
                              size_multiplier_s=?, size_multiplier_m=?, size_multiplier_l=?
                          WHERE id=1""",
                       (name, base_price, per_km_rate, off_mult, addr_mult, size_s, size_m, size_l))
        else:
            db.execute("""INSERT INTO company 
                          (id, name, base_price_per_kg, per_km_rate, office_delivery_multiplier, address_delivery_multiplier,
                           size_multiplier_s, size_multiplier_m, size_multiplier_l)
                          VALUES (1,?,?,?,?,?,?,?,?)""",
                       (name, base_price, per_km_rate, off_mult, addr_mult, size_s, size_m, size_l))
        db.commit()
        flash("Настройките са записани.")
        return redirect(url_for('company_form'))
    return render_template('company_form.html', title="Компания", c=c)

# -------------- EMPLOYEES (admin) --------------
@app.route('/employees')
@login_required(roles=['admin'])
def employees_list():
    """Списък служители (само за администратори)."""
    db = get_db()
    emps = db.execute(
        """
        SELECT e.id, e.first_name, e.last_name, e.phone, u.id as user_id, u.email, u.role, o.name AS office_name, o.city, e.office_id
        FROM employees e
        JOIN users u ON u.id = e.user_id
        LEFT JOIN offices o ON o.id = e.office_id
        ORDER BY e.first_name, e.last_name
        """
    ).fetchall()
    return render_template("employees_list.html", title="Служители", emps=emps)

@app.route('/employees/new', methods=['GET','POST'])
@login_required(roles=['admin'])
def employees_new():
    """Създава служител (user с роля employee/courier + employees запис)."""
    db = get_db()
    offices = db.execute("SELECT id, name||' ('||city||')' as name FROM offices ORDER BY city, name").fetchall()
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form.get('password') or 'changeme123'
        role = request.form.get('role')
        if role not in ('employee','courier'):
            flash("Невалидна роля.")
            return redirect(url_for('employees_new'))
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("Този имейл вече съществува.")
            return redirect(url_for('employees_new'))
        db.execute("INSERT INTO users(email, password_hash, role) VALUES (?,?,?)",
                   (email, generate_password_hash(password), role))
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        office_id = request.form.get('office_id') or None
        office_id = int(office_id) if office_id else None
        db.execute("INSERT INTO employees(user_id, first_name, last_name, office_id, phone) VALUES (?,?,?,?,?)",
                   (uid, request.form['first_name'], request.form['last_name'], office_id, request.form.get('phone')))
        db.commit()
        flash("Служителят е създаден.")
        return redirect(url_for('employees_list'))
    return render_template("employees_form.html", title="Нов служител", offices=offices, emp=None)

@app.route('/employees/<int:emp_id>/edit', methods=['GET','POST'])
@login_required(roles=['admin'])
def employees_edit(emp_id):
    """Редакция на служител (име/телефон/офис/роля)."""
    db = get_db()
    emp = db.execute(
        "SELECT e.*, u.email, u.role FROM employees e JOIN users u ON u.id=e.user_id WHERE e.id=?", (emp_id,)
    ).fetchone()
    if not emp:
        abort(404)
    offices = db.execute("SELECT id, name||' ('||city||')' as name FROM offices ORDER BY city, name").fetchall()
    if request.method == 'POST':
        office_id = request.form.get('office_id') or None
        office_id = int(office_id) if office_id else None
        role = request.form.get('role')
        if role not in ('employee','courier'):
            flash("Невалидна роля.")
            return redirect(url_for('employees_edit', emp_id=emp_id))
        db.execute("UPDATE employees SET first_name=?, last_name=?, phone=?, office_id=? WHERE id=?",
                   (request.form['first_name'], request.form['last_name'], request.form.get('phone'), office_id, emp_id))
        db.execute("UPDATE users SET role=? WHERE id=?", (role, emp['user_id']))
        db.commit()
        flash("Промените са записани.")
        return redirect(url_for('employees_list'))
    return render_template("employees_form.html", title="Редакция на служител", offices=offices, emp=emp)

@app.route('/employees/<int:emp_id>/delete')
@login_required(roles=['admin'])
def employees_delete(emp_id):
    """Триене на служител: премахва свързания user (каскадно заличава и employee реда)."""
    db = get_db()
    row = db.execute("SELECT user_id FROM employees WHERE id=?", (emp_id,)).fetchone()
    if row:
        db.execute("DELETE FROM users WHERE id=?", (row['user_id'],))
        db.commit()
    flash("Служителят е изтрит.")
    return redirect(url_for('employees_list'))

# -------------- SHIPMENTS --------------
@app.route('/shipments')
def shipments_list():
    """Списък пратки: клиенти виждат свои; служители/админ – всички."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    base_query = """
        SELECT s.*, sc.first_name||' '||sc.last_name as sender_name,
               rc.first_name||' '||rc.last_name as recipient_name
        FROM shipments s
        JOIN clients sc ON sc.id = s.sender_client_id
        JOIN clients rc ON rc.id = s.recipient_client_id
    """
    params = []
    if user['role'] == 'client':
        me = db.execute("SELECT id FROM clients WHERE user_id=?", (user['id'],)).fetchone()
        base_query += " WHERE s.sender_client_id=? OR s.recipient_client_id=?"
        params += [me['id'], me['id']]
    base_query += " ORDER BY s.id DESC"
    shipments = db.execute(base_query, params).fetchall()
    return render_template("shipments_list.html", title="Пратки", shipments=shipments)

@app.route('/shipments/new', methods=['GET','POST'])
@login_required(roles=['employee','admin'])
def shipments_new():
    """Създаване на пратка: форма + запис; смята цена/разстояние."""
    db = get_db()
    clients = db.execute("SELECT id, first_name||' '||last_name as name FROM clients ORDER BY first_name, last_name").fetchall()
    offices = db.execute("SELECT id, name||' ('||city||')' as name, city, address, lat, lon FROM offices ORDER BY city, name").fetchall()
    c = db.execute("SELECT * FROM company WHERE id=1").fetchone()
    employees = db.execute(
        """
        SELECT e.id, e.first_name||' '||e.last_name as name, u.role, COALESCE(o.name||' ('||o.city||')','–') as office_name
        FROM employees e
        JOIN users u ON u.id=e.user_id
        LEFT JOIN offices o ON o.id=e.office_id
        ORDER BY name
        """
    ).fetchall()
    my_emp = db.execute("SELECT id FROM employees WHERE user_id=?", (current_user()['id'],)).fetchone()

    if request.method == 'POST':
        sender_id = int(request.form['sender_client_id'])
        recipient_id = int(request.form['recipient_client_id'])

        size = request.form.get('size') or 'M'
        if size not in ('S','M','L'):
            size = 'M'

        to_office = 1 if request.form.get('to_office') == 'on' else 0
        pickup_from_office = 1 if request.form.get('pickup_from_office') == 'on' else 0
        return_to_office = 1 if request.form.get('return_to_office') == 'on' else 0
        return_office_id = request.form.get('return_office_id')
        return_office_id = int(return_office_id) if (return_to_office and return_office_id) else None

        weight = float(request.form['weight_kg'])

        auto_distance = compute_distance_from_form(request.form)  # опит за автоматично разстояние
        fallback_distance = float(request.form.get('distance_km') or 1.0)  # резервно
        distance_km = auto_distance if auto_distance is not None else fallback_distance

        dest_office_id = request.form.get('destination_office_id')
        dest_office_id = int(dest_office_id) if dest_office_id else None
        delivery_address = request.form.get('delivery_address') or None
        if to_office:
            delivery_address = None  # няма адрес при „до офис“

        origin_office_id = request.form.get('origin_office_id')
        origin_office_id = int(origin_office_id) if origin_office_id else None
        pickup_address = request.form.get('pickup_address') or None
        if pickup_from_office:
            pickup_address = None  # няма адрес при „от офис“

        price = compute_price(weight, bool(to_office), distance_km, size=size)  # финална цена

        rbe = request.form.get('registered_by_employee_id')
        emp_id_final = None
        if rbe:
            try: emp_id_final = int(rbe)
            except ValueError: emp_id_final = None
        if not emp_id_final and my_emp:
            emp_id_final = my_emp['id']  # текущ служител
        if not emp_id_final:
            cur = current_user()
            if cur['role'] == 'admin':  # ако админ няма employees ред → създай
                db.execute("INSERT INTO employees(user_id, first_name, last_name, office_id, phone) VALUES (?,?,?,?,?)",
                           (cur['id'], 'Админ', 'Потребител', None, None))
                db.commit()
                emp_id_final = db.execute("SELECT id FROM employees WHERE user_id=?", (cur['id'],)).fetchone()['id']
            else:
                flash("Няма избран служител и текущият профил няма служителски запис.")
                return redirect(url_for('shipments_new'))

        db.execute(
            """
            INSERT INTO shipments(
                sender_client_id, recipient_client_id, origin_office_id, destination_office_id,
                delivery_address, pickup_address, weight_kg, distance_km, to_office,
                size, return_to_office, return_office_id,
                price, status, registered_by_employee_id
            )
            VALUES (?,?,?,?,?,?,?,?,?, ?,?,?, ?, 'registered', ?)
            """,
            (
                sender_id, recipient_id, origin_office_id, dest_office_id,
                delivery_address, pickup_address, weight, distance_km, to_office,
                size, return_to_office, return_office_id,
                price, emp_id_final
            )
        )
        db.commit()
        flash(f"Пратката е създадена. Разстояние: {distance_km} км.")
        return redirect(url_for('shipments_list'))

    return render_template("shipments_form.html", title="Нова пратка",
                           clients=clients, offices=offices, employees=employees, my_emp=my_emp, company=c)

@app.route('/shipments/<int:shipment_id>')
def shipments_view(shipment_id):
    """Детайлен изглед на пратка (контрол на достъпа за клиенти)."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    s = db.execute(
        """
        SELECT s.*, sc.first_name||' '||sc.last_name as sender_name, rc.first_name||' '||rc.last_name as recipient_name,
               oo.name as origin_office_name, dof.name as dest_office_name, ro.name as return_office_name
        FROM shipments s
        JOIN clients sc ON sc.id = s.sender_client_id
        JOIN clients rc ON rc.id = s.recipient_client_id
        LEFT JOIN offices oo ON oo.id = s.origin_office_id
        LEFT JOIN offices dof ON dof.id = s.destination_office_id
        LEFT JOIN offices ro ON ro.id = s.return_office_id
        WHERE s.id=?
        """, (shipment_id,)
    ).fetchone()
    if not s: abort(404)
    if user['role'] == 'client':
        me = db.execute("SELECT id FROM clients WHERE user_id=?", (user['id'],)).fetchone()
        if s['sender_client_id'] != me['id'] and s['recipient_client_id'] != me['id']:
            abort(403)  # клиент няма достъп до чужди пратки
    return render_template("shipments_view.html", title=f"Пратка #{shipment_id}", s=s)

@app.route('/shipments/<int:shipment_id>/delivered')
@login_required(roles=['employee','admin'])
def shipments_mark_delivered(shipment_id):
    """Маркира пратка като доставена и записва delivered_at (UTC)."""
    db = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db.execute("UPDATE shipments SET status='delivered', delivered_at=? WHERE id=?", (now, shipment_id))
    db.commit()
    flash("Пратката е маркирана като доставена.")
    return redirect(url_for('shipments_view', shipment_id=shipment_id))

@app.route('/shipments/<int:shipment_id>/delete')
@login_required(roles=['employee','admin'])
def shipments_delete(shipment_id):
    """Изтрива пратка по id (404 ако липсва)."""
    db = get_db()
    row = db.execute("SELECT id FROM shipments WHERE id=?", (shipment_id,)).fetchone()
    if not row:
        abort(404)
    db.execute("DELETE FROM shipments WHERE id=?", (shipment_id,))
    db.commit()
    flash("Пратката е изтрита.")
    return redirect(url_for('shipments_list'))


# -------------- DEV seed --------------
@app.route('/dev/create-employee')
@login_required(roles=['admin'])
def dev_create_employee():
    """DEV помощник: създава примерен офис и служител, ако липсват."""
    db = get_db()
    office = db.execute("SELECT id FROM offices LIMIT 1").fetchone()
    if not office:
        db.execute("INSERT INTO offices(name, city, address) VALUES ('Централен', 'София', 'бул. Демото 1')")
        db.commit()
        office = db.execute("SELECT id FROM offices LIMIT 1").fetchone()
    email = 'employee@company.com'
    exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if not exists:
        db.execute("INSERT INTO users(email, password_hash, role) VALUES (?,?, 'employee')", (email, generate_password_hash('emp1234')))
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO employees(user_id, first_name, last_name, office_id, phone) VALUES (?,?,?,?,?)",
                   (uid, 'Офис', 'Служител', office['id'], '0888123456'))
        db.commit()
        flash("Създаден офис-служител employee@company.com / emp1234")
    else:
        flash("Офис-служителят вече съществува.")
    return redirect(url_for('dashboard'))


# --------- Public API proxies for frontend (Nominatim/OSRM) ---------
@app.get("/api/geocode")
def api_geocode():
    """Прокси към Nominatim със семпъл rate-limit (1 req/sec/IP)."""
    # Simple per-IP rate limit: max 1 req/sec (align with Nominatim policy)
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    now = time.time()
    last = GEO_THROTTLE.get(ip, 0)
    if now - last < 1.0:
        return jsonify([]), 429  # твърде често
    GEO_THROTTLE[ip] = now
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify([])
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q + ", Bulgaria", "format": "jsonv2", "addressdetails": 1, "limit": 6, "countrycodes": "bg"},
            headers={"User-Agent": "PLM Logistics App/1.0 (contact: demo@example.com)"},
            timeout=10,
        )
        data = r.json() if r.ok else []
        return jsonify(data)
    except Exception:
        return jsonify([]), 200  # тих fail → празен списък

@app.get("/api/route")
def api_route():
    """Прокси към OSRM за маршрут/геометрия (GeoJSON)."""
    try:
        o_lat = float(request.args["o_lat"]); o_lon = float(request.args["o_lon"])
        d_lat = float(request.args["d_lat"]); d_lon = float(request.args["d_lon"])
    except Exception:
        return jsonify({"error": "Missing or invalid coordinates"}), 400
    try:
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}",
            params={"overview": "full", "geometries": "geojson"},
            timeout=10
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error":"routing_failed"}), 502  # проблем с външната услуга


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)  # dev сървър (debug)
