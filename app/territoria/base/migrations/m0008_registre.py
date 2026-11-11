# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0008 : registre des actes numérotés (un compteur par type d'acte et par année, numéros jamais réutilisés)."""

VERSION = 8
NOM = 'registre'


def appliquer(c):
    c.execute('CREATE TABLE IF NOT EXISTS registre_actes(id INTEGER PRIMARY KEY, type TEXT NOT NULL, annee INTEGER NOT NULL, '
              'sequence INTEGER NOT NULL, numero TEXT NOT NULL UNIQUE, acte_id TEXT NOT NULL UNIQUE, objet TEXT NOT NULL, '
              'attribue_le TEXT NOT NULL, attribue_par TEXT NOT NULL, UNIQUE(type, annee, sequence))')
