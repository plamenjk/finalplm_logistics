from flask import render_template

def init_error_handlers(app):
    """
    Регистрира глобални обработчици на грешки за приложението.

    Защо ги имаме:
      - По-добро UX → показваме приятни страници вместо суров текст
      - По-безопасно поведение → никога не разкриваме stack trace към клиент
      - Централизиран контрол → не дублираме логика по blueprints
    """

    @app.errorhandler(404)
    def not_found(e):
        """
        HTTP 404 – Страница/ресурсът не е намерен.

        Опитваме се да рендерираме custom темплейт:
            templates/errors/404.html

        Ако темплейтът липсва (напр. по време на тестове) → връщаме fallback текст.
        """
        try:
            return render_template("errors/404.html"), 404
        except Exception:
            # fallback, ако няма темплейт
            return "Not Found", 404

    @app.errorhandler(500)
    def server_error(e):
        """
        HTTP 500 – вътрешна грешка (необработено изключение).

        - Логваме stack trace, за да може да се анализира проблема.
        - Рендерираме custom шаблон, ако го има.
        - Никога не показваме traceback на потребителя (security best practice).
        """
        app.logger.exception("Unhandled exception")
        try:
            return render_template("errors/500.html"), 500
        except Exception:
            # fallback, ако няма темплейт
            return "Internal Server Error", 500
