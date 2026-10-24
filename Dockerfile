# FixMyTech backend + web UI. The local diagnostic agent is NOT containerised: it must run on the customer's
# own computer next to the USB-connected phone.
# NOTE: written 2026-10-19 but not yet built (Docker daemon unavailable in the authoring environment).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FIXMYTECH_ENV=production \
    FIXMYTECH_FRONTEND_DIR=/app/frontend \
    FIXMYTECH_DATABASE_URL=sqlite:////app/var/fixmytech.db \
    FIXMYTECH_PRICING_DIR=/app/pricing_seed \
    FIXMYTECH_LOG_FORMAT=json

WORKDIR /app
COPY requirements.lock.txt pyproject.toml README.md ./
COPY src ./src
COPY frontend ./frontend
# Price data seed (dataset.json + .gitkeep)
COPY pricing_seed /app/pricing_seed
RUN pip install --no-cache-dir -r requirements.lock.txt && pip install --no-cache-dir --no-deps . \
    && useradd --system --uid 10001 fixmytech && mkdir -p /app/var/pricing && chown -R fixmytech /app/var
USER fixmytech
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
# Behind a TLS-terminating reverse proxy; set FIXMYTECH_TRUST_PROXY_HEADERS=true only in that case.
CMD ["uvicorn", "fixmytech.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
