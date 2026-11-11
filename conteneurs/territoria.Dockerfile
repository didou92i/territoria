# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Image Territoria. Contexte de construction : la racine du dépôt (voir compose.yaml).
# Les deux images de base sont épinglées par l'empreinte de leur index multi-architecture
# (relevée avec `docker buildx imagetools inspect`) : la même référence vaut pour amd64 et arm64.

# Étape 1 : interface (composants React), construite avec les versions figées de package-lock.json.
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS interface
WORKDIR /construction/app/frontend
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY app/frontend/src ./src
# Écrit ../public/21st-components.js, soit /construction/app/public/21st-components.js.
RUN npm run build

# Étape 2 : application.
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

# OCR (français) et conversion des PDF en images, sans paquets recommandés.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python figées ; la roue vendue est vérifiée avant installation, puis posée sans résolution de dépendances.
WORKDIR /installation
COPY requirements.lock ./
COPY vendor ./vendor
RUN cd vendor && sha256sum -c SHA256SUMS \
    && cd .. \
    && pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps vendor/droit_territorial-0.4.0-py3-none-any.whl

# Utilisateur sans privilèges ; /donnees est le seul dossier où l'application écrit (monté par Compose).
RUN groupadd --gid 10001 territoria \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin territoria \
    && mkdir /donnees \
    && chown 10001:10001 /donnees

WORKDIR /app
COPY app/*.py ./
COPY app/territoria ./territoria
# Protocole par fichiers partagé avec le conteneur de transcription (travaux.protocole).
COPY app/travaux ./travaux
COPY app/public ./public
# Workflows n8n : `outils provision-n8n` les copie vers n8n (le dépôt les garde dans n8n/workflows).
COPY n8n/workflows ./workflows
COPY --from=interface /construction/app/public/21st-components.js ./public/21st-components.js
# Le code reste la propriété de root et en lecture seule pour l'utilisateur du service.
RUN python -m compileall -q /app

ENV PYTHONPATH=/app TERRITORIA_ECOUTE=0.0.0.0 TERRITORIA_DONNEES=/donnees
# poppler (fontconfig) écrit un cache de polices : /tmp est le seul endroit en écriture avec la racine en lecture seule.
ENV XDG_CACHE_HOME=/tmp
USER 10001:10001
EXPOSE 8878
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8878/healthz', timeout=4).read()"]
CMD ["python", "-m", "serve"]
