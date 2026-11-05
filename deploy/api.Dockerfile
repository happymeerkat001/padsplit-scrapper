# Supply a reviewed Python 3.12 slim image pinned by digest at build time.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/api/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir -r /tmp/requirements.lock && rm /tmp/requirements.lock
COPY backend/api/ /app/backend/api/
COPY backend/storage/__init__.py backend/storage/secrets.py /app/backend/storage/
USER 65532:65532
# Compose sets the explicit reviewed port and disables access logs.
