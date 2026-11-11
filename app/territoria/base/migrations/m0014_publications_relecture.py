# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0014 : date du repérage (pas de validation sans repérage), avertissement quand l'IA n'a pas cherché les
noms (vu par celui qui valide), et date du dossier (la date de mise en ligne est celle imprimée sur le PDF)."""

VERSION = 14
NOM = 'publications_relecture'


def appliquer(c):
    colonnes = {ligne[1] for ligne in c.execute('PRAGMA table_info(publications)')}
    for colonne in ('repere_le', 'avertissement', 'date_dossier'):
        if colonne not in colonnes:
            c.execute(f"ALTER TABLE publications ADD COLUMN {colonne} TEXT NOT NULL DEFAULT ''")
