# app/blueprints/auth.py
# ---------------------------------------------
# Blueprint, който обслужва:
#  - вход на потребители (login)
#  - изход (logout)
#  - клиентска регистрация (register)
# 
# Използваме:
#  - SQLite през get_db()
#  - Werkzeug криптиране на пароли
#  - Flask session за login state
# ---------------------------------------------

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from ..db import get_db

# Flask Blueprint → групира URL-и и контролери за auth
bp = Blueprint("auth", __name__)

@bp.route("/login", methods=["GET", "POST"], endpoint="login")
def login():
    """
    Вход в системата.
    
    ЦЕЛИ:
    - Проверка на имейл и парола
    - Проверка на роля (client / staff)
    - Записване на user_id в session, ако проверките минат
    - Пренасочване към Dashboard или защитена страница (next=?)
    
    SECURITY:
    - Паролите НЕ се съхраняват в чист вид → сверяваме с hash
    - По време на login не издаваме конкретна причина (email vs password)
      → избягва информация за злонамерени потребители
    """

    if request.method == "POST":
        # role_expect → UI избира дали потребителят влиза като клиент или служител
        role_expect = request.form.get('role_expect')

        # Почистваме входните данни
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Вземаме потребителя от БД (или None)
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

        # Проверка: съществува ли потребител и валидна ли е паролата
        if user and check_password_hash(user["password_hash"], password):

            # Клиент → клиентска роля
            if role_expect == "client" and user["role"] != "client":
                flash("Профилът не е клиентски. Изберете таб „Служител/Куриер“.")  
                return redirect(url_for(".login"))

            # Служител → employee / admin / courier
            if role_expect == "staff" and user["role"] not in ("employee", "admin", "courier"):
                flash("Профилът не е служителски. Изберете таб „Клиент“.")  
                return redirect(url_for(".login"))

            # УСПЕШНО ЛОГВАНЕ
            session["user_id"] = user["id"]   # Съхраняваме ID в session cookie
            
            # next=? → пренасочване към защитена страница, ако е подадена
            return redirect(request.args.get("next") or url_for("main.dashboard"))

        # Грешни данни (email не съществува или hash mismatch)
        flash("Невалиден имейл или парола.")

    # GET → показваме login формата
    return render_template("login.html", title="Вход")


@bp.route("/logout", endpoint="logout")
def logout():
    """
    Изход от системата.

    SECURITY:
    - Изчистваме цялата session информация
    - Пренасочваме обратно към login страница
    - Няма чувствителни операции извън session.clear()
    """
    session.clear()
    return redirect(url_for(".login"))


@bp.route("/register", methods=["GET","POST"], endpoint="register_client")
def register_client():
    """
    Регистрация на нов клиентски профил.

    FLOW:
    - Проверяваме дали email вече съществува
    - Ако не → създаваме запис в users + свързан запис в clients
    - Паролата се хешира преди запис (generate_password_hash)
    - Използваме 2 таблици: users (auth), clients (данни за клиент)

    SECURITY:
    - Email е уникален
    - НИКОГА не пазим парола в чист текст
    - Можем лесно да добавим валидации (минимална дължина на парола и др.)
    """

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        phone = request.form.get("phone", "").strip()

        db = get_db()

        # Проверка за дублиращ email (username)
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("Имейлът вече е зает.")

        else:
            # Запис в таблица users
            db.execute(
                "INSERT INTO users(email, password_hash, role) VALUES (?, ?, 'client')",
                (email, generate_password_hash(password))
            )

            # ID на новия user
            uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Запис в таблица clients за потребителски данни
            db.execute(
                "INSERT INTO clients(user_id, first_name, last_name, phone) VALUES (?,?,?,?)",
                (uid, first_name, last_name, phone)
            )

            db.commit()

            flash("Успешна регистрация. Влезте в профила си.")
            return redirect(url_for(".login"))

    # GET → показваме register форма
    return render_template("register.html", title="Регистрация")
