# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0010 : rédaction des séances (lot 3) — document demandé et compte rendu, échec d'un point de l'ordre du jour."""

VERSION = 10
NOM = 'redaction'


def _colonnes(c, table):
    return {ligne[1] for ligne in c.execute(f'PRAGMA table_info({table})')}


def appliquer(c):
    if 'redaction' not in _colonnes(c, 'seances'):
        c.execute("ALTER TABLE seances ADD COLUMN redaction TEXT NOT NULL DEFAULT '{}'")
    if 'erreur' not in _colonnes(c, 'seance_points'):
        c.execute('ALTER TABLE seance_points ADD COLUMN erreur TEXT')
    c.execute('CREATE INDEX IF NOT EXISTS seance_points_seance ON seance_points(seance_id, rang)')
