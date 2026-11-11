# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Réglages de Territoria : seul endroit qui lit les variables TERRITORIA_*."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

MODES = ('solo', 'equipe')
MESSAGE_MODE_EQUIPE = ("Le mode équipe n'est pas encore disponible : l'authentification (sous-projet 2) "
                       "est requise avant toute ouverture au réseau.")


@dataclass(frozen=True)
class Reglages:
    mode: str
    ecoute: str
    port: int
    origine: str
    donnees: Path
    webhook_conversation: str
    webhook_arrete: str
    n8n_status: str
    webhook_token: str = field(repr=False)
    service_token: str = field(repr=False)
    hors_ligne: bool
    llm_url: str
    modele: str
    delai_n8n: int
    pdftoppm: str
    tesseract: str
    transcription_modele: str
    demonstration: bool = False
    domaine: str = ''
    ia_en_ligne: str = ''
    mistral_cle: str = field(default='', repr=False)
    orchestration: str = 'n8n'


def _texte(environ: Mapping[str, str], nom: str, defaut: str) -> str:
    """Valeur de la variable, ou le défaut quand elle est absente ou vide."""
    return environ.get(nom, '').strip() or defaut


def _entier(environ: Mapping[str, str], nom: str, defaut: int) -> int:
    valeur = _texte(environ, nom, str(defaut))
    try:
        return int(valeur)
    except ValueError:
        raise ValueError(f'{nom} doit être un nombre entier.') from None


def _hors_ligne(environ: Mapping[str, str]) -> bool:
    valeur = _texte(environ, 'TERRITORIA_HORS_LIGNE', '')
    if valeur not in ('', '0', '1'):
        raise ValueError(f"TERRITORIA_HORS_LIGNE doit valoir « 0 » ou « 1 » (valeur reçue : {valeur!r}).")
    return valeur == '1'


def _demonstration(environ: Mapping[str, str]) -> bool:
    """Serveur de démonstration : OpenRouter permis, accès réservé aux comptes (docs/demonstration.md)."""
    valeur = _texte(environ, 'TERRITORIA_DEMONSTRATION', '')
    if valeur not in ('', '0', '1'):
        raise ValueError(f"TERRITORIA_DEMONSTRATION doit valoir « 0 » ou « 1 » (valeur reçue : {valeur!r}).")
    return valeur == '1'


def _orchestration(environ: Mapping[str, str]) -> str:
    """n8n enchaîne les étapes (installation normale) ; « interne » : TerritorIA les enchaîne lui-même, pour la
    démonstration légère sur un hébergement sans conteneurs (docs/demonstration-legere.md)."""
    valeur = _texte(environ, 'TERRITORIA_ORCHESTRATION', 'n8n')
    if valeur not in ('n8n', 'interne'):
        raise ValueError(f"TERRITORIA_ORCHESTRATION doit valoir « n8n » ou « interne » (valeur reçue : {valeur!r}).")
    return valeur


def _ia_en_ligne(environ: Mapping[str, str]) -> str:
    """IA en ligne choisie à l'installation (étape mistral) : vide = IA locale, la valeur par défaut."""
    valeur = _texte(environ, 'TERRITORIA_IA_EN_LIGNE', '')
    if valeur not in ('', 'mistral'):
        raise ValueError(f"TERRITORIA_IA_EN_LIGNE doit être vide ou valoir « mistral » (valeur reçue : {valeur!r}).")
    return valeur


def charger(environ: Mapping[str, str] = os.environ) -> Reglages:
    mode = _texte(environ, 'TERRITORIA_MODE', 'solo')
    if mode not in MODES:
        raise ValueError(f"TERRITORIA_MODE doit valoir « solo » ou « equipe » (valeur reçue : {mode!r}).")
    port = _entier(environ, 'TERRITORIA_PORT', 8878)
    return Reglages(
        mode=mode,
        ecoute=_texte(environ, 'TERRITORIA_ECOUTE', '127.0.0.1'),
        port=port,
        origine=_texte(environ, 'TERRITORIA_ORIGINE', f'http://127.0.0.1:{port}').rstrip('/'),
        donnees=Path(_texte(environ, 'TERRITORIA_DONNEES', './territoria-donnees/territoria')),
        webhook_conversation=_texte(environ, 'TERRITORIA_WEBHOOK_CONVERSATION',
                                    'http://n8n:5678/webhook/territoria-conversation'),
        webhook_arrete=_texte(environ, 'TERRITORIA_WEBHOOK_ARRETE', 'http://n8n:5678/webhook/territoria-arrete'),
        n8n_status=_texte(environ, 'TERRITORIA_N8N_STATUS', 'http://n8n:5678/healthz/readiness'),
        webhook_token=_texte(environ, 'TERRITORIA_WEBHOOK_TOKEN', ''),
        service_token=_texte(environ, 'TERRITORIA_SERVICE_TOKEN', ''),
        hors_ligne=_hors_ligne(environ),
        llm_url=_texte(environ, 'TERRITORIA_LLM_URL', 'http://127.0.0.1:1234/v1'),
        modele=_texte(environ, 'TERRITORIA_MODELE', 'gemma-4-12b-it-mlx'),
        delai_n8n=_entier(environ, 'TERRITORIA_DELAI_N8N', 960),
        pdftoppm=_texte(environ, 'TERRITORIA_PDFTOPPM', 'pdftoppm'),
        tesseract=_texte(environ, 'TERRITORIA_TESSERACT', 'tesseract'),
        transcription_modele=_texte(environ, 'TERRITORIA_TRANSCRIPTION_MODELE', 'large-v3-turbo'),
        demonstration=_demonstration(environ),
        domaine=_texte(environ, 'TERRITORIA_DOMAINE', ''),
        ia_en_ligne=_ia_en_ligne(environ),
        mistral_cle=_texte(environ, 'TERRITORIA_MISTRAL_CLE', ''),
        orchestration=_orchestration(environ),
    )


def verifier_demarrage(r: Reglages) -> None:
    """Refuse de démarrer dans un état que le socle ne sait pas protéger."""
    if r.mode == 'equipe':
        raise SystemExit(MESSAGE_MODE_EQUIPE)


REGLAGES = charger()
