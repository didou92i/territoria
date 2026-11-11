# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0009 : séances (conseil ou réunion), intervenants, passages transcrits, et points de l'ordre du jour (lot 3)."""

VERSION = 9
NOM = 'seances'

ETATS = "'creee','reception','attente','transcription','relecture','validee','redaction','prete','echec'"


def appliquer(c):
    c.execute("CREATE TABLE IF NOT EXISTS seances(id TEXT PRIMARY KEY, proprietaire TEXT NOT NULL, "
              "type TEXT NOT NULL CHECK(type IN ('conseil','reunion')), titre TEXT NOT NULL, date TEXT NOT NULL, heure TEXT, "
              f"lieu TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL CHECK(etat IN ({ETATS})), erreur TEXT, progression TEXT, "
              "duree_s REAL, details TEXT NOT NULL DEFAULT '{}', cree TEXT NOT NULL, modifie TEXT NOT NULL)")
    c.execute('CREATE INDEX IF NOT EXISTS seances_proprietaire ON seances(proprietaire, cree)')
    c.execute("CREATE TABLE IF NOT EXISTS seance_intervenants(seance_id TEXT NOT NULL REFERENCES seances(id) ON DELETE CASCADE, "
              "etiquette TEXT NOT NULL, nom TEXT NOT NULL DEFAULT '', PRIMARY KEY(seance_id, etiquette))")
    c.execute('CREATE TABLE IF NOT EXISTS seance_segments(id INTEGER PRIMARY KEY, seance_id TEXT NOT NULL REFERENCES seances(id) '
              'ON DELETE CASCADE, debut REAL NOT NULL, fin REAL NOT NULL, etiquette TEXT NOT NULL, texte TEXT NOT NULL, '
              'corrige INTEGER NOT NULL DEFAULT 0)')
    c.execute('CREATE INDEX IF NOT EXISTS seance_segments_seance ON seance_segments(seance_id, debut)')
    c.execute('CREATE TABLE IF NOT EXISTS seance_points(id INTEGER PRIMARY KEY, seance_id TEXT NOT NULL REFERENCES seances(id) '
              'ON DELETE CASCADE, rang INTEGER NOT NULL, titre TEXT NOT NULL, deliberation_numero TEXT, rapporteur TEXT, vote TEXT, '
              'scrutin_particulier INTEGER NOT NULL DEFAULT 0, debut REAL, teneur TEXT)')
