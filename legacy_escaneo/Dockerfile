FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Sin privilegios de root
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

# Servicio web (por defecto). El servicio "scheduler" sobreescribe el comando con: python scheduler.py
# Un solo worker con hilos: el límite de intentos de login vive en memoria.
CMD ["sh", "-c", "gunicorn 'app_escaneo:create_app()' --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 90 --access-logfile -"]
