FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Sin privilegios de root
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Un solo worker con hilos: el limite de intentos de login y el scheduler de alertas viven en el proceso.
# Railway inyecta PORT y DATABASE_URL.
CMD ["sh", "-c", "gunicorn 'web:create_app()' --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 90 --access-logfile -"]
