# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Journal de fonctionnement : une ligne JSON par événement, sans aucun contenu.

Seuls des identifiants d'étape, des statuts, des durées et des codes y figurent ;
toute chaîne hors du motif autorisé est remplacée par « invalide ».
"""
from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timezone

MOTIF = re.compile(r'[a-z0-9_.:/-]{1,60}')
_verrou = threading.Lock()


def _valeur(valeur):
    if valeur is None or (isinstance(valeur, int) and not isinstance(valeur, bool)):
        return valeur
    if isinstance(valeur, str) and MOTIF.fullmatch(valeur):
        return valeur
    return 'invalide'


def evenement(etape: str, statut: str, duree_ms: int | None = None, code: str | None = None) -> None:
    # Horodatage RFC 3339 en minuscules (« t » et « z » sont permis) pour rester dans le motif.
    t = datetime.now(timezone.utc).strftime('%Y-%m-%dt%H:%M:%S.%f')[:-3] + 'z'
    ligne = {'t': t, 'etape': etape, 'statut': statut, 'duree_ms': duree_ms, 'code': code}
    texte = json.dumps({cle: _valeur(valeur) for cle, valeur in ligne.items()}) + '\n'
    with _verrou:
        sys.stderr.write(texte)
        sys.stderr.flush()
