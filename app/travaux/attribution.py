# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Intervenant de chaque segment transcrit, d'après les tours de parole de la séparation des voix.

Le groupe retenu pour un segment est celui dont les tours le recouvrent le plus longtemps ; sans aucun recouvrement, celui
du tour le plus proche. Les groupes sont renumérotés « Intervenant 1, 2… » dans l'ordre où ils apparaissent dans la séance.
"""
from __future__ import annotations


def _recouvrement(a: dict, b: dict) -> float:
    return max(0.0, min(a['fin'], b['fin']) - max(a['debut'], b['debut']))


def _distance(a: dict, b: dict) -> float:
    return max(0.0, b['debut'] - a['fin'], a['debut'] - b['fin'])


def _groupe(segment: dict, tours: list[dict]):
    if not tours:
        return None
    cumul = {}
    for tour in tours:
        cumul[tour['groupe']] = cumul.get(tour['groupe'], 0.0) + _recouvrement(segment, tour)
    meilleur = max(cumul, key=cumul.get)
    if cumul[meilleur] > 0:
        return meilleur
    return min(tours, key=lambda tour: _distance(segment, tour))['groupe']


def attribuer(segments: list[dict], tours: list[dict]) -> list[dict]:
    numeros = {}
    resultat = []
    for segment in segments:
        groupe = _groupe(segment, tours)
        numeros.setdefault(groupe, len(numeros) + 1)
        resultat.append(dict(segment, intervenant=f'Intervenant {numeros[groupe]}'))
    return resultat
