# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0004 : retours sur les réponses. Note 👍/👎 de chaque réponse et signalements transmis aux référents.

Un signalement est une copie de l'échange (question, réponse, titres des sources), faite à la demande de l'agent : il
survit à la suppression de la conversation, et il est effacé au bout d'un an (`retours.CONSERVATION_JOURS`).
"""

VERSION = 4
NOM = 'retours'

TABLES = (
    "CREATE TABLE IF NOT EXISTS signalements(id TEXT PRIMARY KEY, message_id INTEGER UNIQUE, quand TEXT NOT NULL, "
    "auteur_nom TEXT NOT NULL DEFAULT '', assistant TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '', "
    "question TEXT NOT NULL, reponse TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', commentaire TEXT NOT NULL DEFAULT '', "
    "statut TEXT NOT NULL DEFAULT 'nouveau', traite_le TEXT NOT NULL DEFAULT '')",
)


def appliquer(c):
    for creation in TABLES:
        c.execute(creation)
    if 'note' not in {ligne[1] for ligne in c.execute('PRAGMA table_info(messages)')}:
        c.execute('ALTER TABLE messages ADD COLUMN note INTEGER NOT NULL DEFAULT 0')
