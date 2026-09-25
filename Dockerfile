FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic
COPY locales ./locales

RUN pip install --no-cache-dir . \
    && python -c "from pathlib import Path; from babel.messages.mofile import write_mo; from babel.messages.pofile import read_po; \
root = Path('locales'); \
[write_mo(po.with_suffix('.mo').open('wb'), read_po(po.open('rb'))) for po in root.glob('*/*/*.po')]"

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER bot
ENTRYPOINT ["/entrypoint.sh"]
