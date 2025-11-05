# Утилити функции за обработка на автентикация / роли
# -----------------------------------------------------
# Този модул предоставя две ключови функции:
#  - current_user() → извлича логнатия потребител от сесията
#  - login_required(...) → декоратор за защита на маршрути
#
# Използва се във всички blueprints.
# Работи със session на Flask и SQLite през get_db().
# -----------------------------------------------------

from flask import session, redirect, url_for, request, abort
from ..db import get_db

def current_user():
    """
    Връща текущия потребител (Row) или None.

    Логика:
      1) Проверява дали в сесията има user_id
      2) Ако няма → връща None (няма логнат потребител)
      3) Ако има → зарежда реда от таблица users по id
      4) Връща целия запис (Row), така че достъпът става user['role'], user['email'] и т.н.

    Бележка:
      - Използва се за прости проверки и в login_required декоратора.
      - Не вдига грешки → безопасно за всички случаи.
    """
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def login_required(roles=None):
    """
    Декоратор за защита на маршрути (views).

    Поведение:
      - Ако потребителят не е логнат → redirect към /login
      - Ако е логнат, но няма нужната роля → HTTP 403 Forbidden
      - Иначе → изпълнява оригиналната функция

    Аргументи:
      roles = None или списък от разрешени роли
        Примери:
          @login_required()                    → изисква просто логин
          @login_required(roles=['admin'])     → само администратори
          @login_required(roles=['employee','admin']) → служители и админ

    Механизъм:
      - Проверява `session["user_id"]`
      - Задава `next=request.path`, за да върнем потребителя обратно след логин

    Защо wrapper.__name__?
      - Flask използва името на функцията като ключ.
      - Презаписът гарантира, че route регистрацията работи нормално.
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            user = current_user()

            # Няма логнат потребител → redirect към /login + next=...
            if not user:
                return redirect(url_for('auth.login', next=request.path))

            # Има логин, но няма права → HTTP 403 Forbidden
            if roles and user['role'] not in roles:
                abort(403)

            # Всичко OK → изпълняваме оригиналната функция
            return fn(*args, **kwargs)

        # Запазване на името за Flask routing/stack trace
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator
