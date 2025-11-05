# app/blueprints/main.py
# -------------------------------------------------------------
# Основно "табло" (dashboard) на приложението.
#
# Поведение:
#  - Ако потребителят не е логнат → redirect към login
#  - Ако е служител/куриер/админ → вижда последните 20 всички пратки
#  - Ако е клиент → вижда само собствените си пратки ( изпратени/получени )
#
# Архитектура:
#  - current_user() чете user_id от сесията и връща запис от users
#  - JOIN към clients → получаваме имена на подател и получател
#  - Използваме LIMIT 20 → оптимизация за списъка на началната страница
# -------------------------------------------------------------

from flask import Blueprint, render_template, redirect, url_for
from ..db import get_db
from ..utils.auth import current_user

bp = Blueprint("main", __name__)

@bp.route("/", endpoint="dashboard")
def dashboard():
    """
    Начална страница (Dashboard).

    Логика:
    1) Ако няма логнат потребител → връщаме към login
    2) Извличаме потребителската роля
    3) Служител/Админ/Куриер:
         - вижда последните 20 пратки (всички)
       Клиент:
         - извличаме client_id от таблицата clients (по user ID)
         - показваме само пратките, в които участва като подател/получател

    Business Logic:
    - Служители имат пълен достъп, защото обработват пратки
    - Клиентите виждат само личните си → защита на данни

    Security:
    - Защитаваме route чрез проверка за логнат user (current_user())
      вместо декоратор, за да позволим публичен root → redirect logic
    """

    # Текущ логнат user или None
    user = current_user()

    # Ако потребителят не е логнат → изпращаме към /login
    if not user:
        return redirect(url_for('auth.login'))

    db = get_db()

    # ✅ Служители, куриери и админи виждат всички пратки
    if user["role"] in ("employee", "admin", "courier"):
        shipments = db.execute(
            """
            SELECT 
                s.*,
                sc.first_name || ' ' || sc.last_name AS sender_name,
                rc.first_name || ' ' || rc.last_name AS recipient_name
            FROM shipments s
            JOIN clients sc ON sc.id = s.sender_client_id
            JOIN clients rc ON rc.id = s.recipient_client_id
            ORDER BY s.id DESC
            LIMIT 20
            """
        ).fetchall()

    # ✅ Клиент → вижда САМО свои пратки (като подател или получател)
    else:
        # Намираме client_id по текущ user_id
        client = db.execute(
            "SELECT id FROM clients WHERE user_id = ?",
            (user["id"],)
        ).fetchone()

        # Извличаме последните 20 пратки, свързани с този клиент
        shipments = db.execute(
            """
            SELECT 
                s.*,
                sc.first_name || ' ' || sc.last_name AS sender_name,
                rc.first_name || ' ' || rc.last_name AS recipient_name
            FROM shipments s
            JOIN clients sc ON sc.id = s.sender_client_id
            JOIN clients rc ON rc.id = s.recipient_client_id
            WHERE s.sender_client_id = ? OR s.recipient_client_id = ?
            ORDER BY s.id DESC
            LIMIT 20
            """,
            (client["id"], client["id"])
        ).fetchall()

    # Рендерираме началното табло
    return render_template("dashboard.html", title="Табло", shipments=shipments)
