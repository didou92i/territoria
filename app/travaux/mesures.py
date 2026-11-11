# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Mesures du banc d'essai : taux d'erreur sur les mots, justesse de la séparation des voix."""
from __future__ import annotations

import itertools
import re


def _mots(texte: str) -> list[str]:
    return re.findall(r"[\w'’]+", texte.lower())


def taux_erreur_mots(reference: str, hypothese: str) -> float:
    """Distance d'édition entre les suites de mots, rapportée au nombre de mots de la référence (accents conservés)."""
    ref, hyp = _mots(reference), _mots(hypothese)
    if not ref:
        return 0.0 if not hyp else 1.0
    precedente = list(range(len(hyp) + 1))
    for i, mot in enumerate(ref, 1):
        courante = [i]
        for j, autre in enumerate(hyp, 1):
            courante.append(min(precedente[j] + 1, courante[j - 1] + 1, precedente[j - 1] + (mot != autre)))
        precedente = courante
    return precedente[-1] / len(ref)


def justesse_intervenants(reference: list[dict], segments: list[dict]) -> float:
    """Part du temps de parole de la référence attribuée au bon groupe, après le meilleur appariement des étiquettes."""
    total = sum(t['fin'] - t['debut'] for t in reference)
    etiquettes = sorted({s['intervenant'] for s in segments})
    groupes = sorted({t['groupe'] for t in reference})
    if not total or not etiquettes:
        return 0.0
    if len(etiquettes) > 6 or len(groupes) > 6:
        raise ValueError('6 groupes au plus')
    commun = {(e, g): 0.0 for e in etiquettes for g in groupes}
    for s in segments:
        for t in reference:
            commun[(s['intervenant'], t['groupe'])] += max(0.0, min(s['fin'], t['fin']) - max(s['debut'], t['debut']))
    candidats = groupes + [None] * len(etiquettes)
    meilleur = max(sum(commun.get((e, g), 0.0) for e, g in zip(etiquettes, choix))
                   for choix in set(itertools.permutations(candidats, len(etiquettes))))
    return meilleur / total
