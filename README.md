# Логистична компания – OSM v5 (Autocomplete + Auto Route)
- Полета за адрес (офис и пратка) с **подсказки** чрез Nominatim (без ключове).
- Маршрутът на картата се **рисува автоматично** при промяна на полетата — без бутон.
- Всички предишни екстри: размери S/M/L, формула за цена, Leaflet + OSM, OSRM дистанция.

## Стартиране
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
# http://127.0.0.1:5000
```

### Профили
- Admin: admin@company.com / admin123
- /dev/create-employee → employee@company.com / emp1234
