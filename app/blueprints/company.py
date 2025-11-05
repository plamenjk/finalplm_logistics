# app/blueprints/company.py
# -------------------------------------------------------------
# Панел за настройки на компанията:
#  - промяна на ценови параметри (базова цена/км/размери)
#  - само служители/админи имат достъп
#
# Данните се пазят в таблица `company`, която съдържа
# глобални конфигурационни параметри за логистичната фирма.
#
# Забележка: предполага се само един ред (id=1) → single config record.
# -------------------------------------------------------------

from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..db import get_db
from ..utils.auth import login_required

# Blueprint → отделяме страницата за настройки от другите модули
bp = Blueprint("company", __name__)

@bp.route('/company', methods=['GET','POST'], endpoint='company_form')
@login_required(roles=['employee','admin'])
def company_form():
    """
    Страница за управление на фирмени настройки.

    Функционалност:
    - GET → зарежда формата с текущите стойности
    - POST → записва нови стойности в БД (update / insert)

    SECURITY:
    - достъп само за служители/админи (`login_required` с роли)
    - стойностите се валидират базово (float -> fallback)
    - flash съобщения информират за резултат от операцията
    """

    db = get_db()

    # Вземаме текущите настройки; очакваме един ред (id=1)
    c = db.execute("SELECT * FROM company WHERE id=1").fetchone()

    # Ако потребителят изпраща формата (POST)
    if request.method == 'POST':

        # Взимаме стойности от формата и ги нормализираме
        # `or <default>` осигурява fallback ако формата е празна
        name       = request.form.get('name','').strip()
        base_price = float(request.form.get('base_price_per_kg','2.5') or 2.5)
        per_km_rate = float(request.form.get('per_km_rate','0.40') or 0.40)

        # Множители за тип доставка и размер на пратката
        off_mult   = float(request.form.get('office_delivery_multiplier','1.0') or 1.0)
        addr_mult  = float(request.form.get('address_delivery_multiplier','1.4') or 1.4)

        size_s     = float(request.form.get('size_multiplier_s','0.90') or 0.90)
        size_m     = float(request.form.get('size_multiplier_m','1.00') or 1.00)
        size_l     = float(request.form.get('size_multiplier_l','1.20') or 1.20)

        # Update, ако има съществуващ запис
        if c:
            db.execute(
                """
                UPDATE company 
                SET name=?, base_price_per_kg=?, per_km_rate=?, 
                    office_delivery_multiplier=?, address_delivery_multiplier=?,
                    size_multiplier_s=?, size_multiplier_m=?, size_multiplier_l=?
                WHERE id=1
                """,
                (name, base_price, per_km_rate,
                 off_mult, addr_mult, size_s, size_m, size_l)
            )

        # Insert, ако таблицата е празна (първо конфигуриране)
        else:
            db.execute(
                """
                INSERT INTO company 
                (id, name, base_price_per_kg, per_km_rate, 
                 office_delivery_multiplier, address_delivery_multiplier,
                 size_multiplier_s, size_multiplier_m, size_multiplier_l)
                VALUES (1,?,?,?,?,?,?,?,?)
                """,
                (name, base_price, per_km_rate,
                 off_mult, addr_mult, size_s, size_m, size_l)
            )

        # Persistent save в БД
        db.commit()

        # Потвърждение към потребителя
        flash("Настройките са записани.")

        # Redirect, за да избегнем повторно POST действие при refresh → PRG Pattern
        return redirect(url_for('company.company_form'))

    # GET → зареждаме HTML шаблона с текущата конфигурация
    return render_template('company_form.html', title="Компания", c=c)
