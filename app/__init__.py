from flask import Flask
from config import Config
from .extensions import configure_logging
from .db import init_app as init_db, ensure_file_exists, init_schema, ensure_migrations
from .utils.auth import current_user

def create_app(config_object: type = Config) -> Flask:
    """
    Factory функция за създаване на Flask приложение.

    Защо factory?
      - позволява тестове с различни настройки
      - добра практика при по-големи проекти
      - лесно създаване на dev/test/prod инстанции

    Тук:
      - Зареждаме конфигурация
      - Настройваме логове
      - Инициализираме и подготвяме базата
      - Регистрираме blueprints (модулна структура)
      - Вкарваме глобални променливи достъпни от templates (user, app_name)
    """

    # Създаване на Flask app + пътища към шаблони и статика
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(config_object)  # зареждане на Config класа

    # Конфигурация на логване (rotation, нива, без външни пакети)
    configure_logging(app)

    # Инициализация на DB (open/close на връзки per request)
    init_db(app)

    # before_request — гарантира че БД файл и схема винаги са налични
    @app.before_request
    def _before_request():
        ensure_file_exists(app.config["DATABASE"])  # създава .db файла ако липсва
        init_schema()                              # при първи старт създава таблици
        ensure_migrations()                        # изпълнява вътрешни миграции (ако има)

    # Error handlers (404/500) — могат да използват шаблони, ако ги добавиш
    from .errors import init_error_handlers
    init_error_handlers(app)

    # context_processor — глобални променливи достъпни във всички templates
    @app.context_processor
    def inject_globals():
        return {
            "app_name": app.config.get("APP_NAME", Config.APP_NAME),
            "user": current_user()   # позволява {{ user }} в HTML без import
        }

    # ==========================
    #   Регистрация на Blueprints
    # ==========================

    from .blueprints.main import bp as main_bp
    app.register_blueprint(main_bp)  # Dashboard / Home

    from .blueprints.auth import bp as auth_bp
    app.register_blueprint(auth_bp)  # Login / Logout / Register

    from .blueprints.offices import bp as offices_bp
    app.register_blueprint(offices_bp)  # Offices CRUD

    from .blueprints.customers import bp as customers_bp
    app.register_blueprint(customers_bp)  # Clients CRUD (служители/админ)

    from .blueprints.employees import bp as employees_bp
    app.register_blueprint(employees_bp)  # Employees CRUD (админ)

    from .blueprints.shipments import bp as shipments_bp
    app.register_blueprint(shipments_bp)  # Shipments — създаване/листване

    from .blueprints.company import bp as company_bp
    app.register_blueprint(company_bp)  # Company config / pricing

    from .blueprints.api import bp as api_bp
    app.register_blueprint(api_bp)  # Public proxy API (Nominatim/OSRM)

    return app
