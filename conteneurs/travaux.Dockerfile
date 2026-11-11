# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Image du conteneur de transcription (conversion, transcription, séparation des voix). Contexte : la racine du dépôt.
# Le conteneur tourne sans réseau (compose.yaml, service `travaux`) ; les modèles sont montés en lecture seule sur
# /modeles, téléchargés à l'installation par le service ponctuel `travaux-modeles`.
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

# ffmpeg : conversion de tout format audio ou vidéo en piste mono 16 kHz (sans paquets recommandés).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python figées, vérifiées par leurs empreintes (pip-compile --generate-hashes).
WORKDIR /installation
COPY conteneurs/travaux-requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r travaux-requirements.lock

# Même utilisateur sans privilèges que Territoria : les deux conteneurs écrivent dans le dossier audio des séances.
RUN groupadd --gid 10001 territoria \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin territoria \
    && mkdir /audio /modeles \
    && chown 10001:10001 /audio

WORKDIR /app
COPY app/travaux ./travaux
COPY conteneurs/modeles-audio.json ./modeles-audio.json
RUN python -m compileall -q /app

# Aucun cache écrit hors de /tmp (racine en lecture seule) ; aucun accès au réseau, même si une bibliothèque le tentait.
ENV PYTHONPATH=/app XDG_CACHE_HOME=/tmp HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
USER 10001:10001
CMD ["python", "-m", "travaux.service"]
