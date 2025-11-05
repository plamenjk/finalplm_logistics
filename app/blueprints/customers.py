# app/blueprints/customers.py
# -------------------------------------------------------------
# Управление на клиенти от служител/администратор:
#  - списък клиенти
#  - добавяне на нов клиент (users + clients таблици)
#  - редакция на клиентски профил
#  - изтриване на клиент и свързан user
#
# SECURITY:
#  - достъп само за роля employee/admin
#  - клиентите НЕ могат да управляват други клиенти
# -------------------------------------------------------------

from flask import Blueprint, render_template, redirect, url_for, request, abort
from ..db import get_db
from ..utils.auth import login_required

bp = Blueprint("customers", __name__)


@bp.route('/clients', endpoint='clients_list')
@login_required(roles=['employee','admin'])
def clients_list():
    """
    Показва списък с всички клиенти (видим само за служители/админ).

    Данните идват от:
    - users: автентикация/роля (email)
    - clients: лични данни (имена, телефон)

    JOIN е нужен, защото "user_id" в clients сочи към users.id
    """

    db = get_db()
    clients = db.execute("""
        SELECT c.*, u.email
        FROM clients c
        JOIN users u ON u.id = c.user_id
        ORDER BY c.first_name, c.last_name
    """).fetchall()

    return render_template("clients_list.html", title="Клиенти", clients=clients)


@bp.route('/clients/new', methods=['GET', 'POST'], endpoint='clients_new')
@login_required(roles=['employee','admin'])
def clients_new():
    """
    Създава нов клиентски профил.

    Процес:
    1) Създава запис в users (email + hash на парола)
    2) Взима user_id
    3) Създава запис в clients с user_id

    Забележка:
    - Ако паролата липсва → задаваме временна "changeme123"
      (подходящо за вътрешно създаване от служител)
    """

    if request.method == 'POST':
        from werkzeug.security import generate_password_hash

        db = get_db()
        email = request.form['email'].strip().lower()
        password = request.form.get('password') or "changeme123"

        # Вмъкваме в таблицата users роля "client"
        db.execute("""
            INSERT INTO users(email, password_hash, role)
            VALUES (?, ?, 'client')
        """, (email, generate_password_hash(password)))

        # Взимаме последното генерирано user_id
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Създаваме записа в clients таблица
        db.execute("""
            INSERT INTO clients(user_id, first_name, last_name, phone)
            VALUES (?, ?, ?, ?)
        """, (uid, request.form['first_name'], request.form['last_name'], request.form['phone']))

        db.commit()
        return redirect(url_for('.clients_list'))

    # GET → форма за създаване
    return render_template("clients_form.html", title="Нов клиент", client=None)


@bp.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'], endpoint='clients_edit')
@login_required(roles=['employee','admin'])
def clients_edit(client_id):
    """
    Редактира клиентски профил (имена, телефон).

    Забележка:
    - Не се променя email тук, защото е в users таблица
      (обикновено се прави отделно меню/форма за това)
    """

    db = get_db()

    # Намираме клиента по ID → включва email от users
    client = db.execute("""
        SELECT c.*, u.email
        FROM clients c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = ?
    """, (client_id,)).fetchone()

    if not client:
        abort(404)  # клиентът не съществува

    if request.method == 'POST':
        db.execute("""
            UPDATE clients
            SET first_name = ?, last_name = ?, phone = ?
            WHERE id = ?
        """, (request.form['first_name'], request.form['last_name'], request.form['phone'], client_id))

        db.commit()
        return redirect(url_for('.clients_list'))

    # GET → зареждане на форма за редакция
    return render_template("clients_form.html", title="Редакция на клиент", client=client)


@bp.route('/clients/<int:client_id>/delete', endpoint='clients_delete')
@login_required(roles=['employee','admin'])
def clients_delete(client_id):
    """
    Изтрива клиент и свързания потребител от таблица users.

    Важно:
    - Не разчитаме на ON DELETE CASCADE, защото връзката е през user_id
    - Първо взимаме user_id от clients, после трием от users
    - clients редът ще изчезне автоматично, ако FOREIGN KEY CASCADE е активен
      (ако не е → би трябвало да има DELETE и върху clients)
    """

    db = get_db()

    # Намираме user_id на клиента
    row = db.execute("SELECT user_id FROM clients WHERE id=?", (client_id,)).fetchone()

    if row:
        # Трием потребителя, свързан с клиента
        db.execute("DELETE FROM users WHERE id=?", (row["user_id"],))
        db.commit()

    return redirect(url_for('.clients_list'))
