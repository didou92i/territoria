# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Nomenclature ACTES (niveau 2) : matière d'une délibération, pour le CSV national « Délibérations » (SCDL 2.1.6).

Source et date dans `ressources/nomenclature-actes.json`."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

FICHIER = Path(__file__).resolve().parent / 'ressources' / 'nomenclature-actes.json'


@lru_cache(maxsize=1)
def _donnees() -> dict:
    return json.loads(FICHIER.read_text(encoding='utf-8'))


def matieres() -> list[dict]:
    """Matières de niveau 2, dans l'ordre de la nomenclature : [{'code': '1.1', 'nom': 'Marchés publics'}, …]."""
    return [dict(m) for m in _donnees()['matieres']]


def nom(code: str) -> str:
    """Intitulé de la matière, tel que la nomenclature l'écrit."""
    for matiere in _donnees()['matieres']:
        if matiere['code'] == code:
            return matiere['nom']
    raise ValueError('Matière inconnue de la nomenclature ACTES.')


def _sans_ponctuation(texte: str) -> str:
    sans_parentheses = re.sub(r'\s*\([^)]*\)', '', texte)
    return ' '.join(sans_parentheses.replace(',', ' ').split()).lower()


def nom_scdl(code: str) -> str:
    """`DELIB_MATIERE_NOM` du schéma : niveau 1 puis niveau 2, en minuscules accentuées, sans virgule ni parenthèse,
    séparés par une barre oblique (« finances locales/décisions budgétaires »)."""
    intitule = nom(code)
    famille = _donnees()['familles'][code.split('.')[0]]
    return f'{_sans_ponctuation(famille)}/{_sans_ponctuation(intitule)}'
