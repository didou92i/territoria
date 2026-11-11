# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0003 : comptes et droits. Utilisateurs, services, sessions, partages et propriétaire de chaque contenu.

Le propriétaire vide (`''`) désigne l'utilisateur implicite du mode « un poste » : à la création du premier compte, ses
contenus sont confiés à ce compte administrateur (`comptes.creer_premier_administrateur`).
"""

VERSION = 3
NOM = 'comptes'

TABLES = (
    'CREATE TABLE IF NOT EXISTS services(id TEXT PRIMARY KEY, nom TEXT NOT NULL UNIQUE COLLATE NOCASE, cree TEXT NOT NULL)',
    "CREATE TABLE IF NOT EXISTS utilisateurs(id TEXT PRIMARY KEY, identifiant TEXT NOT NULL UNIQUE COLLATE NOCASE, "
    "nom TEXT NOT NULL, role TEXT NOT NULL, service_id TEXT REFERENCES services(id) ON DELETE SET NULL, empreinte TEXT NOT NULL, "
    "actif INTEGER NOT NULL DEFAULT 1, doit_changer INTEGER NOT NULL DEFAULT 1, echecs INTEGER NOT NULL DEFAULT 0, "
    "bloque_jusqu TEXT NOT NULL DEFAULT '', derniere_connexion TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL)",
    # Le jeton du navigateur n'est jamais gardé : seulement son empreinte SHA-256.
    'CREATE TABLE IF NOT EXISTS sessions(empreinte TEXT PRIMARY KEY, utilisateur_id TEXT NOT NULL REFERENCES utilisateurs(id) '
    'ON DELETE CASCADE, cree TEXT NOT NULL, activite TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS partages(ressource TEXT NOT NULL, ressource_id TEXT NOT NULL, cible TEXT NOT NULL, '
    'cible_id TEXT NOT NULL, niveau TEXT NOT NULL, PRIMARY KEY(ressource, ressource_id, cible, cible_id))',
)
PROPRIETAIRES = ('spaces', 'conversations', 'assistants')


def appliquer(c):
    for creation in TABLES:
        c.execute(creation)
    for table in PROPRIETAIRES:
        if 'proprietaire' not in {ligne[1] for ligne in c.execute(f'PRAGMA table_info({table})')}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN proprietaire TEXT NOT NULL DEFAULT ''")
