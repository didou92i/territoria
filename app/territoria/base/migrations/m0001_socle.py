# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0001 : le socle. Reprend l'existant, qu'il vienne d'une installation neuve, du socle ou du pilote."""

VERSION = 1
NOM = 'socle'

TABLES = (
    'CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, created TEXT NOT NULL)',
    "CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id), name TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', captured TEXT NOT NULL, created TEXT NOT NULL)",
    'CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id), page INTEGER, content TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS assistants(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, instructions TEXT NOT NULL, custom INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)',
    "CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, text TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL)",
)
# Colonnes absentes de la base du pilote : (table, colonne, valeur par défaut).
COLONNES = (
    ('conversations', 'space_id', 'all'),
    ('conversations', 'mode', 'sources'),
    ('conversations', 'assistant_id', ''),
    ('messages', 'artifact', '{}'),
)
# Anciennes descriptions des deux espaces de départ, remplacées seulement si elles n'ont pas été modifiées.
DESCRIPTIONS = (
    ('public', 'Textes et ressources publics importés dans votre espace.', 'Textes et ressources publics importés sur ce Mac.'),
    ('interne', 'Vos documents, rangés dans votre espace privé.', 'Vos documents, rangés et interrogés localement.'),
)


def appliquer(c):
    for creation in TABLES:
        c.execute(creation)
    for table, colonne, defaut in COLONNES:
        if colonne not in {ligne[1] for ligne in c.execute(f'PRAGMA table_info({table})')}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} TEXT NOT NULL DEFAULT '{defaut}'")
    for espace, nouvelle, ancienne in DESCRIPTIONS:
        c.execute('UPDATE spaces SET description=? WHERE id=? AND description=?', (nouvelle, espace, ancienne))
