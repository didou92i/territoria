# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0012 : demandes à l'IA des visiteurs du serveur de démonstration, comptées pour plafonner leur consommation
des crédits de l'administrateur (par visiteur, et pour l'ensemble des visiteurs sur 24 heures)."""

VERSION = 12
NOM = 'essais_ia'


def appliquer(c):
    c.execute('CREATE TABLE IF NOT EXISTS essais_ia(utilisateur_id TEXT NOT NULL, moment TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS essais_ia_moment ON essais_ia(moment)')
    c.execute('CREATE INDEX IF NOT EXISTS essais_ia_utilisateur ON essais_ia(utilisateur_id)')
