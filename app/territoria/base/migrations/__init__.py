# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migrations de la base, un fichier `mNNNN_nom.py` par version, appliquées dans l'ordre des noms.

Chaque module définit `VERSION` (l'entier NNNN), `NOM` et `appliquer(c)`, qui reçoit une connexion déjà dans sa transaction :
elle ne doit ni valider ni annuler (pas d'`executescript`, qui valide d'office) et reste rejouable sur une base déjà à jour.
"""
