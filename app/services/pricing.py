# compute_price
from ..db import get_db

def compute_price(weight_kg, to_office, distance_km, size='M'):
    """
    Изчислява крайна цена на пратка според:
      - тегло (kg)
      - разстояние (km)
      - тип доставка (до офис / до адрес)
      - размер на пратката (S/M/L)
      - конфигурационни множители от таблица company

    Всички бизнес параметри се контролират от админ панела (таблица company),
    което позволява промяна на ценообразуването без промени в кода.
    """

    db = get_db()

    # Взимаме конфигурация за ценообразуване (id=1 → единствен запис)
    c = db.execute(
        """SELECT base_price_per_kg, per_km_rate,
                  office_delivery_multiplier, address_delivery_multiplier,
                  size_multiplier_s, size_multiplier_m, size_multiplier_l
           FROM company WHERE id=1"""
    ).fetchone()

    # Базова цена на база тегло (гарантираме неотрицателни стойности)
    base = c["base_price_per_kg"] * max(float(weight_kg), 0.0)

    # Цена на база разстояние (ако липсва distance_km → 0)
    dist = c["per_km_rate"] * max(float(distance_km or 0.0), 0.0)

    # Множител: доставка до офис или до адрес
    mult_delivery = c["office_delivery_multiplier"] if to_office else c["address_delivery_multiplier"]

    # Нормализираме размера (по подразбиране M)
    size = (size or 'M').upper()
    mult_size = c["size_multiplier_m"]
    if size == 'S': mult_size = c["size_multiplier_s"]
    if size == 'L': mult_size = c["size_multiplier_l"]

    # Финална цена → тегло + разстояние × множители, закръглено до 2 знака
    return round((base + dist) * mult_delivery * mult_size, 2)
