# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0006 : vecteurs des extraits pour la recherche par le sens (un par extrait et par modèle de plongements)."""

VERSION = 6
NOM = 'vecteurs'


def appliquer(c):
    c.execute('CREATE TABLE IF NOT EXISTS chunk_vecteurs(chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, '
              'modele TEXT NOT NULL, vecteur BLOB NOT NULL)')
