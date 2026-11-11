# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0002 : administration. Règles d'usage, journal d'activité sans contenu, assistants activables."""

VERSION = 2
NOM = 'administration'

TABLES = (
    'CREATE TABLE IF NOT EXISTS parametres(cle TEXT PRIMARY KEY, valeur TEXT NOT NULL, modifie TEXT NOT NULL)',
    # Une ligne par action : jamais de texte saisi, de titre ni de nom de fichier, seulement des codes et des identifiants.
    "CREATE TABLE IF NOT EXISTS activite(id INTEGER PRIMARY KEY, quand TEXT NOT NULL, utilisateur TEXT NOT NULL DEFAULT '', "
    "action TEXT NOT NULL, objet TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', duree_ms INTEGER, "
    "statut TEXT NOT NULL DEFAULT 'ok')",
    'CREATE INDEX IF NOT EXISTS activite_quand ON activite(quand)',
)


def appliquer(c):
    for creation in TABLES:
        c.execute(creation)
    if 'actif' not in {ligne[1] for ligne in c.execute('PRAGMA table_info(assistants)')}:
        c.execute('ALTER TABLE assistants ADD COLUMN actif INTEGER NOT NULL DEFAULT 1')
    # Les questions déjà posées alimentent les statistiques dès la mise à jour (sans durée, inconnue avant cette version).
    c.execute(
        "INSERT INTO activite(quand, action, objet, detail) "
        "SELECT m.created, CASE WHEN json_extract(r.artifact, '$.kind') = 'arrete' THEN 'arrete' ELSE 'question' END, "
        "c.assistant_id, c.mode FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "LEFT JOIN messages r ON r.id = m.id + 1 AND r.role = 'assistant' AND json_valid(r.artifact) "
        "WHERE m.role = 'user' ORDER BY m.id")
