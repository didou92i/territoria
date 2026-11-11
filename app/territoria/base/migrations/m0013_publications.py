# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0013 : actes à publier (anonymisation proposée puis validée, dossier à déposer sur le site de la collectivité)."""

VERSION = 13
NOM = 'publications'


def appliquer(c):
    c.execute('CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY, type TEXT NOT NULL, numero TEXT NOT NULL, '
              'date_acte TEXT NOT NULL, objet TEXT NOT NULL, source TEXT NOT NULL, acte_id TEXT NOT NULL DEFAULT \'\', '
              'matiere_code TEXT NOT NULL DEFAULT \'\', votes TEXT NOT NULL DEFAULT \'{}\', '
              'statut TEXT NOT NULL DEFAULT \'a_relire\', passages TEXT NOT NULL DEFAULT \'[]\', '
              'cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, valide_par TEXT NOT NULL DEFAULT \'\', '
              'valide_le TEXT NOT NULL DEFAULT \'\', publie_le TEXT NOT NULL DEFAULT \'\', '
              'empreinte_pdf TEXT NOT NULL DEFAULT \'\')')
    c.execute('CREATE INDEX IF NOT EXISTS publications_statut ON publications(statut)')
