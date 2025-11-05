# app/blueprints/employees.py
# -------------------------------------------------------------
# Модул за управление на служители (само за администратори):
#  - списък на служители (+ роля и офис)
#  - създаване на служител (users + employees)
#  - редакция (име/телефон/офис/роля)
#  - изтриване (каскадно през users → employees)
#
# Архитектура:
#  - Таблица users: автентикация и роля (employee/courier/admin/client)
#  - Таблица employees: данни за служители (име, телефон, офис, FK към users)
#  - Таблица offices: офис локации (опционално свързване към служител)
#
# Security:
#  - достъп само за admin (чрез @login_required(roles=['admin']))
#  - валидации за роля и дублиращ email при създаване/редакция
# -------------------------------------------------------------

from flask import Blueprint, render_template, redirect, url_for, request, abort, flash
from ..db import get_db
from ..utils.auth import login_required

bp = Blueprint("employees", __name__)

@bp.route('/employees', endpoint='employees_list')
@login_required(roles=['admin'])
def employees_list():
    """
    Списък служители (само за администратори).

    Данни:
    - JOIN към users за email и role
    - LEFT JOIN към offices (служител може да няма офис)
    - Подредба по име/фамилия за по-добра четимост
    """
    db = get_db()
    emps = db.execute(
        """
        SELECT
            e.id,
            e.first_name,
            e.last_name,
            e.phone,
            u.id AS user_id,
            u.email,
            u.role,
            o.name AS office_name,
            o.city,
            e.office_id
        FROM employees e
        JOIN users u ON u.id = e.user_id
        LEFT JOIN offices o ON o.id = e.office_id
        ORDER BY e.first_name, e.last_name
        """
    ).fetchall()
    return render_template("employees_list.html", title="Служители", emps=emps)


@bp.route('/employees/new', methods=['GET','POST'], endpoint='employees_new')
@login_required(roles=['admin'])
def employees_new():
    """
    Създава служител:
    1) Създава запис в users (email, password_hash, role = employee|courier)
    2) Взема генерираното user_id
    3) Създава запис в employees с FK към users + (опционален) office_id

    Валидации:
    - role ∈ {'employee','courier'}
    - email да не съществува в users
    - office_id е по избор (None, ако не е избран)
    """
    from werkzeug.security import generate_password_hash
    db = get_db()

    # Списък офиси за dropdown (име + град за яснота)
    offices = db.execute(
        "SELECT id, name||' ('||city||')' AS name FROM offices ORDER BY city, name"
    ).fetchall()

    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form.get('password') or 'changeme123'  # временна парола за първи вход
        role = request.form.get('role')

        # Ролята е строго ограничена до служителски типове
        if role not in ('employee','courier'):
            flash("Невалидна роля.")
            return redirect(url_for('.employees_new'))

        # Забрана за дублиращ email (уникалност в users)
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("Този имейл вече съществува.")
            return redirect(url_for('.employees_new'))

        # 1) users
        db.execute(
            "INSERT INTO users(email, password_hash, role) VALUES (?,?,?)",
            (email, generate_password_hash(password), role)
        )
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # office_id е опционално; празно → None
        office_id = request.form.get('office_id') or None
        office_id = int(office_id) if office_id else None

        # 2) employees
        db.execute(
            "INSERT INTO employees(user_id, first_name, last_name, office_id, phone) VALUES (?,?,?,?,?)",
            (uid, request.form['first_name'], request.form['last_name'], office_id, request.form.get('phone'))
        )

        db.commit()
        flash("Служителят е създаден.")
        return redirect(url_for('.employees_list'))

    # GET → форма за нов служител
    return render_template("employees_form.html", title="Нов служител", offices=offices, emp=None)


@bp.route('/employees/<int:emp_id>/edit', methods=['GET','POST'], endpoint='employees_edit')
@login_required(roles=['admin'])
def employees_edit(emp_id):
    """
    Редакция на служител:
    - име, фамилия, телефон, офис
    - смяна на роля (employee/courier) в users

    Забележка:
    - email не се променя тук (обикновено има отделна форма/процес)
    """
    db = get_db()

    # Зареждаме свързаните данни (вкл. текущата роля от users)
    emp = db.execute(
        "SELECT e.*, u.email, u.role FROM employees e JOIN users u ON u.id = e.user_id WHERE e.id = ?",
        (emp_id,)
    ).fetchone()
    if not emp:
        abort(404)

    offices = db.execute(
        "SELECT id, name||' ('||city||')' AS name FROM offices ORDER BY city, name"
    ).fetchall()

    if request.method == 'POST':
        # Офис може да е празен → None
        office_id = request.form.get('office_id') or None
        office_id = int(office_id) if office_id else None

        role = request.form.get('role')
        if role not in ('employee','courier'):
            flash("Невалидна роля.")
            return redirect(url_for('.employees_edit', emp_id=emp_id))

        # Обновяване на employees (лични данни/офис)
        db.execute(
            "UPDATE employees SET first_name=?, last_name=?, phone=?, office_id=? WHERE id=?",
            (request.form['first_name'], request.form['last_name'], request.form.get('phone'), office_id, emp_id)
        )

        # Обновяване на users (роля)
        db.execute(
            "UPDATE users SET role=? WHERE id=?",
            (role, emp['user_id'])
        )

        db.commit()
        flash("Промените са записани.")
        return redirect(url_for('.employees_list'))

    # GET → форма за редакция с текущи стойности
    return render_template("employees_form.html", title="Редакция на служител", offices=offices, emp=emp)


@bp.route('/employees/<int:emp_id>/delete', endpoint='employees_delete')
@login_required(roles=['admin'])
def employees_delete(emp_id):
    """
    Изтриване на служител:
    - трием свързания user; при правилни FK/ON DELETE CASCADE това премахва и employees реда
    - ако CASCADE не е включен, ще трябва ръчно да се изтрие и от employees

    UX:
    - flash съобщение и redirect към списъка
    """
    db = get_db()
    row = db.execute("SELECT user_id FROM employees WHERE id=?", (emp_id,)).fetchone()
    if row:
        db.execute("DELETE FROM users WHERE id=?", (row['user_id'],))
        db.commit()
    flash("Служителят е изтрит.")
    return redirect(url_for('.employees_list'))
