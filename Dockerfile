FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Storage is mounted as a volume in docker-compose; create the fallback dir.
RUN mkdir -p /app/app/var

# Run as an unprivileged user so a container-escape via the app can't touch
# /etc or anything root-owned. UID 1001 matches the convention used by the
# Dentsu infra base images.
RUN groupadd -r app --gid 1001 && useradd -r -u 1001 -g app -d /app app \
 && chown -R app:app /app /data 2>/dev/null || true
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
