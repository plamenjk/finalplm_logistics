# -------------------------------------------------------------
# Този blueprint служи като backend прокси към външните гео услуги:
#   • Nominatim (геокодиране: адрес → координати)
#   • OSRM (маршрут: координати → път + разстояние + геометрия)
# -------------------------------------------------------------

from flask import Blueprint, request, jsonify
from ..services.routing import GEO_THROTTLE, compute_distance_from_form, office_coords, geocode_address
import time
import requests

bp = Blueprint("api", __name__)

@bp.get("/api/geocode", endpoint="api_geocode")
def api_geocode():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    now = time.time()
    last = GEO_THROTTLE.get(ip, 0)
    if now - last < 1.0:
        return jsonify([]), 429
    GEO_THROTTLE[ip] = now
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify([])

    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q + ", Bulgaria", "format": "jsonv2","addressdetails": 1,"limit": 6,"countrycodes": "bg"},
            headers={"User-Agent": "PLM Logistics App/1.0 (contact: demo@example.com)"},
            timeout=10
        )
        data = r.json() if r.ok else []
        return jsonify(data)
    except Exception:
        return jsonify([]), 200


@bp.get("/api/route", endpoint="api_route")
def api_route():
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
    except Exception:
        return jsonify({"error": "routing_failed"}), 502


@bp.post("/api/route_from_form")
def api_route_from_form():
    form = request.form

    distance_km = compute_distance_from_form(form)

    pickup_from_office = (form.get('pickup_from_office') == 'on')
    to_office = (form.get('to_office') == 'on')

    origin_office_id = int(form.get('origin_office_id')) if form.get('origin_office_id') else None # type: ignore
    pickup_address = form.get('pickup_address') or None

    dest_office_id = int(form.get('destination_office_id')) if form.get('destination_office_id') else None # type: ignore
    delivery_address = form.get('delivery_address') or None

    o_ll = office_coords(origin_office_id) if pickup_from_office else geocode_address(pickup_address)
    d_ll = office_coords(dest_office_id) if to_office else geocode_address(delivery_address)

    geometry = None
    if o_ll and d_ll:
        try:
            o_lat, o_lon = o_ll
            d_lat, d_lon = d_ll
            r = requests.get(
                f"https://router.project-osrm.org/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}",
                params={"overview": "full", "geometries": "geojson"},
                timeout=10
            )
            j = r.json()
            if j.get("routes"):
                geometry = j["routes"][0]["geometry"]
        except Exception:
            geometry = None

    return jsonify({"distance_km": distance_km, "geometry": geometry})
