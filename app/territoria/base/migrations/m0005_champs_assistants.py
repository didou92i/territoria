# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0005 : champs à remplir des assistants personnalisés (liste JSON de {libelle, obligatoire})."""

VERSION = 5
NOM = 'champs_assistants'


def appliquer(c):
    if 'champs' not in {ligne[1] for ligne in c.execute('PRAGMA table_info(assistants)')}:
        c.execute("ALTER TABLE assistants ADD COLUMN champs TEXT NOT NULL DEFAULT '[]'")
