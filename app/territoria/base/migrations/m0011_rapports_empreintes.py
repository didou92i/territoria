# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0011 : rapport au vu duquel chaque délibération est adoptée (L2121-15) et empreinte des passages d'après
lesquels la teneur d'un point a été rédigée (une teneur dont les passages ont changé n'est plus à jour)."""

VERSION = 11
NOM = 'rapports_empreintes'


def appliquer(c):
    colonnes = {ligne[1] for ligne in c.execute('PRAGMA table_info(seance_points)')}
    if 'rapport' not in colonnes:
        c.execute('ALTER TABLE seance_points ADD COLUMN rapport TEXT')
    if 'empreinte' not in colonnes:
        c.execute('ALTER TABLE seance_points ADD COLUMN empreinte TEXT')
