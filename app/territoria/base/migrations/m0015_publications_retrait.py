# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0015 : retrait d'un acte publié (date, auteur et motif ; le PDF et son empreinte restent conservés)."""

VERSION = 15
NOM = 'publications_retrait'


def appliquer(c):
    colonnes = {ligne[1] for ligne in c.execute('PRAGMA table_info(publications)')}
    for colonne in ('retire_le', 'retire_par', 'retire_motif'):
        if colonne not in colonnes:
            c.execute(f"ALTER TABLE publications ADD COLUMN {colonne} TEXT NOT NULL DEFAULT ''")
