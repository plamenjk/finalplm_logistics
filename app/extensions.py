import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def configure_logging(app):
    """
    Конфигурира централизирано логването за приложението.

    ➤ Какво прави тази функция:
      - Създава директория logs/ ако не съществува
      - Записва логове във файл app.log
      - Използва RotatingFileHandler → не позволява файлът да стане огромен
      - Задава INFO ниво по подразбиране (могат да се логват и WARNING/ERROR)
      - Добавя timestamp, ниво, файл и ред → полезно за дебъг
      - Активира логерът на Flask (`app.logger`)
    """

    # Път към директорията, където ще пазим лог файловете
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)  # създава директория, ако липсва

    # Главният лог файл на приложението
    log_file = log_dir / "app.log"

    # RotatingFileHandler:
    # - maxBytes → максимален размер на файла (≈ 1MB)
    # - backupCount → колко ротации пазим (app.log.1, .2, .3)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)

    # Формат на лог редовете — включва време, ниво, логер, съобщение, файл и linе
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s -> %(message)s [in %(pathname)s:%(lineno)d]"
    )
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)  # минимално ниво за този handler

    # Настройваме главния logger на Flask приложението
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(handler)

    # Първоначално лог събитие — полезно да видим в app.log, че логерът е активен
    app.logger.info("Application logger configured.")
