# FastAPI service image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

# Install dependencies first so Docker caches this layer between code changes
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Run as a non-root user, who owns the application code
# (--chown makes the image work whatever the file permissions are on the host)
RUN useradd --create-home appuser
COPY --chown=appuser:appuser app ./app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
