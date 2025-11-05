# app/blueprints/offices.py
# -------------------------------------------------------------
# Управление на офис локации:
# - списък офиси (за логнати потребители)
# - добавяне/редакция на офис (само employee/admin)
# - автоматично геокодиране чрез services.routing.geocode_address
#
# Контекст:
# - офисите са точки, където се приемат/изпращат пратки
# - lat/lon се използват за изчисляване на разстояния
#
# Security:
# - виждане → изисква логин (current_user())
# - модификация → изисква роли employee/admin
# -------------------------------------------------------------

from flask import Blueprint, render_template, redirect, url_for, request, abort, flash
from ..db import get_db
from ..utils.auth import current_user, login_required
from ..services.routing import geocode_address

bp = Blueprint("offices", __name__)

@bp.route('/offices', endpoint='offices_list')
def offices_list():
    """
    Списък офиси.

    Достъп:
      - само логнати потребители → redirect към login ако липсва user
        (тук НЕ използваме login_required, защото route може да бъде достъпена без JS)
    """
    user = current_user()
    if not user:
        return redirect(url_for('auth.login'))

    db = get_db()

    offices = db.execute(
        "SELECT * FROM offices ORDER BY city, name"
    ).fetchall()

    return render_template("offices_list.html", title="Офиси", offices=offices)


@bp.route('/offices/new', methods=['GET','POST'], endpoint='offices_new')
@login_required(roles=['employee','admin'])
def offices_new():
    """
    Създаване на нов офис.

    Поведение:
     - Събира име, град, адрес
     - Опитва автоматично геокодиране (lat/lon) чрез geocode_address()
     - Ако геокодирането се провали → записва офис без координати,
       като позволява ръчно добавяне/корекция по-късно

    UX:
     - flash съобщение указва дали геокодирането е успешно
    """
    if request.method == 'POST':
        db = get_db()

        name = request.form['name'].strip()
        city = request.form['city'].strip()
        address = request.form['address'].strip()

        # Пълен адрес за геокодиране + стандартен country suffix
        full = f"{address}, {city}, Bulgaria"

        latlon = geocode_address(full)
        lat, lon = (latlon if latlon else (None, None))

        db.execute(
            "INSERT INTO offices(name, city, address, lat, lon) VALUES (?,?,?,?,?)",
            (name, city, address, lat, lon)
        )
        db.commit()

        flash("Офисът е създаден." + ("" if latlon else " (неуспешно геокодиране)"))
        return redirect(url_for('.offices_list'))

    # GET → празна форма
    return render_template("offices_form.html", title="Нов офис", office=None)


@bp.route('/offices/<int:office_id>/edit', methods=['GET','POST'], endpoint='offices_edit')
@login_required(roles=['employee','admin'])
def offices_edit(office_id):
    """
    Редакция на офис.

    Логика:
      - Зарежда текущите стойности
      - При POST:
          1) опитва ново геокодиране по въведения адрес
          2) ако е успешно → обновява lat/lon
          3) ако не → запазва старите координати (по-добро UX от None)

    Сценарий:
      - полезно, ако първоначалното геокодиране е било неуспешно
      - или при местене на офис
    """
    db = get_db()

    # Взимаме текущия офис
    office = db.execute(
        "SELECT * FROM offices WHERE id=?", (office_id,)
    ).fetchone()

    if not office:
        abort(404)

    if request.method == 'POST':
        name = request.form['name'].strip()
        city = request.form['city'].strip()
        address = request.form['address'].strip()

        full = f"{address}, {city}, Bulgaria"

        # Опит за ново геокодиране
        latlon = geocode_address(full)

        # Ако геокодирането се провали → запазваме старите координати
        lat, lon = (latlon if latlon else (office["lat"], office["lon"]))

        db.execute(
            "UPDATE offices SET name=?, city=?, address=?, lat=?, lon=? WHERE id=?",
            (name, city, address, lat, lon, office_id)
        )
        db.commit()

        flash("Офисът е обновен." + ("" if latlon else " (запазени са предишните координати)"))
        return redirect(url_for('.offices_list'))

    # GET → показваме форма с текущите стойности
    return render_template("offices_form.html", title="Редакция на офис", office=office)


@bp.route('/offices/<int:office_id>/delete', endpoint='offices_delete')
@login_required(roles=['employee','admin'])
def offices_delete(office_id):
    """
    Изтриване на офис.

    ⚠️ Важно:
    - Ако има пратки асоциирани с този офис, те може да загубят връзката си.
      (в реален продукт → soft delete или 'deactivate' поле е по-добър подход)
    """
    db = get_db()
    db.execute("DELETE FROM offices WHERE id=?", (office_id,))
    db.commit()

    return redirect(url_for('.offices_list'))
