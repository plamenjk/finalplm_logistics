# app/blueprints/shipments.py
# -------------------------------------------------------------
# Управление на пратки:
# - списък (клиент → свои / служител → всички)
# - създаване на пратка (служители/админ)
# - преглед с контрол на достъпа
# - маркиране като доставена
# - изтриване
#
# Важни зависимости:
# - current_user() → кой е логнат
# - login_required() → роли
# - compute_price() → калкулация на цена
# - compute_distance_from_form() → автоматично разстояние
# -------------------------------------------------------------

from flask import Blueprint, render_template, redirect, url_for, request, abort, flash
from ..db import get_db
from ..utils.auth import current_user, login_required
from ..services.pricing import compute_price
from ..services.routing import compute_distance_from_form

bp = Blueprint("shipments", __name__)

@bp.route('/shipments', endpoint='shipments_list')
def shipments_list():
    """Списък пратки: клиенти виждат свои; служители/админ – всички."""
    user = current_user()  # проверка кой е логнат
    if not user:
        # ако не е логнат → към login
        return redirect(url_for('auth.login'))

    db = get_db()

    # базов SQL за списък пратки
    base_query = """
        SELECT s.*, sc.first_name||' '||sc.last_name as sender_name,
               rc.first_name||' '||rc.last_name as recipient_name
        FROM shipments s
        JOIN clients sc ON sc.id = s.sender_client_id
        JOIN clients rc ON rc.id = s.recipient_client_id
    """
    params = []

    # ако е клиент → ограничи резултатите до неговите
    if user['role'] == 'client':
        me = db.execute("SELECT id FROM clients WHERE user_id=?", (user['id'],)).fetchone()
        base_query += " WHERE s.sender_client_id=? OR s.recipient_client_id=?"
        params += [me['id'], me['id']]

    # винаги сортираме по последно създадени
    base_query += " ORDER BY s.id DESC"

    shipments = db.execute(base_query, params).fetchall()
    return render_template("shipments_list.html", title="Пратки", shipments=shipments)


@bp.route('/shipments/new', methods=['GET','POST'], endpoint='shipments_new')
@login_required(roles=['employee','admin'])
def shipments_new():
    """Създаване на пратка: форма + запис; смята цена/разстояние."""
    db = get_db()

    # dropdown списъци от базата
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

    # текущ служител (ако е логнат служител)
    my_emp = db.execute("SELECT id FROM employees WHERE user_id=?", (current_user()['id'],)).fetchone() # type: ignore

    if request.method == 'POST':
        # ---- данни за клиенти ----
        sender_id = int(request.form['sender_client_id'])
        recipient_id = int(request.form['recipient_client_id'])

        # ---- размер ----
        size = request.form.get('size') or 'M'
        if size not in ('S','M','L'):
            size = 'M'  # валидиране по допустими стойности

        # ---- флагове офис/адрес ----
        to_office = 1 if request.form.get('to_office') == 'on' else 0
        pickup_from_office = 1 if request.form.get('pickup_from_office') == 'on' else 0
        return_to_office = 1 if request.form.get('return_to_office') == 'on' else 0

        return_office_id = request.form.get('return_office_id')
        return_office_id = int(return_office_id) if (return_to_office and return_office_id) else None

        # ---- тегло ----
        weight = float(request.form['weight_kg'])

        # ---- разстояние ----
        auto_distance = compute_distance_from_form(request.form)  # опит чрез geocoding/API
        fallback_distance = float(request.form.get('distance_km') or 1.0)  # fallback
        distance_km = auto_distance if auto_distance is not None else fallback_distance

        # ---- адреси/офиси ----
        dest_office_id = request.form.get('destination_office_id')
        dest_office_id = int(dest_office_id) if dest_office_id else None
        delivery_address = request.form.get('delivery_address') or None
        if to_office:
            delivery_address = None  # ако е „до офис“ → няма адрес

        origin_office_id = request.form.get('origin_office_id')
        origin_office_id = int(origin_office_id) if origin_office_id else None
        pickup_address = request.form.get('pickup_address') or None
        if pickup_from_office:
            pickup_address = None  # ако е „от офис“ → няма адрес

        # ---- цена ----
        price = compute_price(weight, bool(to_office), distance_km, size=size)

        # ---- служител, регистрирал пратката ----
        rbe = request.form.get('registered_by_employee_id')
        emp_id_final = None
        if rbe:
            try: emp_id_final = int(rbe)
            except ValueError: emp_id_final = None

        if not emp_id_final and my_emp:
            emp_id_final = my_emp['id']  # текущият служител

        # ако админ няма запис в employees → създаваме един автоматично
        if not emp_id_final:
            cur = current_user()
            if cur['role'] == 'admin':  # type: ignore
                db.execute("INSERT INTO employees(user_id, first_name, last_name, office_id, phone) VALUES (?,?,?,?,?)",
                           (cur['id'], 'Админ', 'Потребител', None, None)) # type: ignore
                db.commit()
                emp_id_final = db.execute("SELECT id FROM employees WHERE user_id=?", (cur['id'],)).fetchone()['id'] # type: ignore
            else:
                flash("Няма избран служител и текущият профил няма служителски запис.")
                return redirect(url_for('.shipments_new'))

        # ---- запис в база ----
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
        return redirect(url_for('.shipments_list'))

    # GET → зарежда форма
    return render_template("shipments_form.html", title="Нова пратка",
                           clients=clients, offices=offices, employees=employees, my_emp=my_emp, c=c)


@bp.route('/shipments/<int:shipment_id>', endpoint='shipments_view')
def shipments_view(shipment_id):
    """Детайлен изглед на пратка (контрол на достъпа за клиенти)."""
    user = current_user()
    if not user:
        return redirect(url_for('auth.login'))

    db = get_db()

    # зареждаме всички данни + имена + офис имена
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

    if not s:
        abort(404)  # няма такава пратка

    # клиент → достъп само до свои пратки
    if user['role'] == 'client':
        me = db.execute("SELECT id FROM clients WHERE user_id=?", (user['id'],)).fetchone()
        if s['sender_client_id'] != me['id'] and s['recipient_client_id'] != me['id']:
            abort(403)  # чужда пратка → забранено

    return render_template("shipments_view.html", title=f"Пратка #{shipment_id}", s=s)


@bp.route('/shipments/<int:shipment_id>/delivered', endpoint='shipments_mark_delivered')
@login_required(roles=['employee','admin'])
def shipments_mark_delivered(shipment_id):
    """Маркира пратка като доставена и записва delivered_at (UTC)."""
    from datetime import datetime

    db = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    db.execute("UPDATE shipments SET status='delivered', delivered_at=? WHERE id=?", (now, shipment_id))
    db.commit()

    flash("Пратката е маркирана като доставена.")
    return redirect(url_for('.shipments_view', shipment_id=shipment_id))


@bp.route('/shipments/<int:shipment_id>/delete', endpoint='shipments_delete')
@login_required(roles=['employee','admin'])
def shipments_delete(shipment_id):
    """Изтрива пратка по id (404 ако липсва)."""
    db = get_db()

    # проверка дали съществува
    row = db.execute("SELECT id FROM shipments WHERE id=?", (shipment_id,)).fetchone()
    if not row:
        abort(404)

    # триене
    db.execute("DELETE FROM shipments WHERE id=?", (shipment_id,))
    db.commit()

    flash("Пратката е изтрита.")
    return redirect(url_for('.shipments_list'))
