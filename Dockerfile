# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml ./
COPY jobintel ./jobintel
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir .; else pip install --no-cache-dir .; fi
RUN useradd --uid 10001 --create-home app && chmod -R a+rX /app
USER app
EXPOSE 8000
CMD ["uvicorn", "jobintel.api:app", "--host", "0.0.0.0", "--port", "8000"]
