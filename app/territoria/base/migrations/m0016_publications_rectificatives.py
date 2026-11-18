# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Relie la nouvelle copie publique à l'ancienne, conservée avec son motif de retrait et son empreinte."""

VERSION = 16
NOM = 'publications_rectificatives'


def appliquer(c):
    c.execute("ALTER TABLE publications ADD COLUMN remplace_id TEXT NOT NULL DEFAULT ''")
