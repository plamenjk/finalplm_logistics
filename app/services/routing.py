# --------------------------------------------------------------
# Този модул съдържа всички гео-функции:
#  - геокодиране (адрес → координати)
#  - изчисляване на разстояние между точки
#  - fallback стратегия: ORS → OSRM → Haversine
#  - работа с офис адреси/координати
#  - кеширане на разстояния в БД
#
# Цел:
#  - минимално API натоварване (кеш + бърз Haversine fallback)
#  - устойчивост при липса на външни услуги
# --------------------------------------------------------------

import os
import math
import time
import requests
from typing import Optional, Tuple
from ..db import get_db

# Опционален ключ за OpenRouteService (ако липсва → ползваме OSRM/Haversine)
ORS_API_KEY = os.getenv("ORS_API_KEY")

# Проста структура: IP → timestamp за последна geo заявка
GEO_THROTTLE = {}  # ip -> last_request_ts (опростен rate limit)

def geocode_address(addr: Optional[str]) -> Optional[Tuple[float, float]]:
    """
    Геокодира адрес чрез Nominatim → (lat, lon) или None.
    Нискорисков подход: връща None при всяка грешка, за да не спира логиката.
    """
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
    """
    Haversine формула → въздушно разстояние (км).
    Използва се при fallback, когато routing APIs не са достъпни.
    """
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return round(2*R*math.asin(math.sqrt(a)), 2)

def ors_distance_km_coords(o_lat, o_lon, d_lat, d_lon):
    """
    Разстояние по път чрез ORS (на база координати).
    Ако няма API ключ или грешка → None.
    """
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
    """
    Разстояние по път чрез OSRM public demo (координати).
    Без API ключ → бърз fallback вариант.
    """
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
    """ORS → геокодира + route; ако няма ключ или грешка → None."""
    if not ORS_API_KEY:
        return None
    o = geocode_address(origin_addr)
    d = geocode_address(dest_addr)
    if not o or not d:
        return None
    return ors_distance_km_coords(o[0], o[1], d[0], d[1])

def osrm_distance_km(origin_addr: str, dest_addr: str):
    """OSRM → геокодира + route; fallback без ключ."""
    o = geocode_address(origin_addr)
    d = geocode_address(dest_addr)
    if not o or not d:
        return None
    return osrm_distance_km_coords(o[0], o[1], d[0], d[1])

def office_full_address(office_id):
    """
    Конструира пълен текстов адрес на офис (за геокодиране).
    Пр.: "ул. Ивайло 5, София, Bulgaria"
    """
    if not office_id:
        return None
    db = get_db()
    o = db.execute("SELECT city, address FROM offices WHERE id=?", (office_id,)).fetchone()
    if not o:
        return None
    return f"{o['address']}, {o['city']}, Bulgaria"

def office_coords(office_id):
    """
    Връща координати на офис ако има записани lat/lon.
    Иначе → None (за да задейства fallback геокодиране).
    """
    if not office_id:
        return None
    db = get_db()
    o = db.execute("SELECT lat, lon FROM offices WHERE id=?", (office_id,)).fetchone()
    if o and o["lat"] is not None and o["lon"] is not None:
        return float(o["lat"]), float(o["lon"])
    return None

def compute_distance_from_form(form):
    """
    Основна функция за UI формата.
    Изчислява разстояние според:
      - офис → адрес
      - адрес → офис
      - офис → офис
      - адрес → адрес

    Последователност:
      1) проверка кеш в SQLite
      2) ORS (coords)
      3) OSRM (coords)
      4) ORS (addresses)
      5) OSRM (addresses)
      6) Haversine fallback

    Гарантира, че UI винаги получава някакво разстояние или None при липсващи данни.
    """

    # Четене на флагове от формата
    pickup_from_office = form.get('pickup_from_office') == 'on'
    origin_office_id = form.get('origin_office_id')
    origin_office_id = int(origin_office_id) if origin_office_id else None
    pickup_address = form.get('pickup_address') or None

    to_office = form.get('to_office') == 'on'
    dest_office_id = form.get('destination_office_id')
    dest_office_id = int(dest_office_id) if dest_office_id else None
    delivery_address = form.get('delivery_address') or None

    # Определяне на текстови адреси за пресмятане
    origin_addr = office_full_address(origin_office_id) if pickup_from_office else pickup_address
    dest_addr   = office_full_address(dest_office_id) if to_office else delivery_address

    # Ако нямаме достатъчно данни → няма изчисление
    if not (origin_addr and dest_addr):
        return None

    db = get_db()

    # Ключове за кеш (normalised string)
    key_o = origin_addr.strip().lower()
    key_d = dest_addr.strip().lower()
    row = db.execute("SELECT distance_km FROM distances_cache WHERE origin=? AND dest=?", (key_o, key_d)).fetchone()
    if row:
        return float(row['distance_km'])  # взимаме кеширано

    # Вземаме координати ако офис → директно, ако адрес → геокодиране
    o_ll = office_coords(origin_office_id) if pickup_from_office else geocode_address(pickup_address)
    d_ll = office_coords(dest_office_id)   if to_office         else geocode_address(delivery_address)

    dist = None
    if o_ll and d_ll:
        dist = ors_distance_km_coords(o_ll[0], o_ll[1], d_ll[0], d_ll[1])  # 1) ORS coords
        if dist is None:
            dist = osrm_distance_km_coords(o_ll[0], o_ll[1], d_ll[0], d_ll[1])  # 2) OSRM coords

    if dist is None:
        dist = ors_distance_km(origin_addr, dest_addr)  # 3) ORS addr
    if dist is None:
        dist = osrm_distance_km(origin_addr, dest_addr)  # 4) OSRM addr
    if dist is None and o_ll and d_ll:
        dist = haversine_km(o_ll[0], o_ll[1], d_ll[0], d_ll[1])  # 5) Haversine fallback

    # Запис в кеш (best effort)
    if dist is not None:
        try:
            db.execute(
                "INSERT OR REPLACE INTO distances_cache(origin, dest, distance_km) VALUES (?,?,?)",
                (key_o, key_d, dist)
            )
            db.commit()
        except Exception:
            pass

    return dist
