# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migrations versionnées de la base SQLite.

Les migrations sont les modules `migrations/mNNNN_nom.py`, appliquées dans l'ordre des noms. Chacune s'exécute dans sa
propre transaction (instructions de définition comprises) : un échec l'annule en entier et laisse les précédentes en place.
`schema_migrations` garde la trace de ce qui a été appliqué, et `PRAGMA user_version` la dernière version.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import migrations
from .connexion import ouvrir

NOM_MIGRATION = re.compile(r'm\d{4}_\w+')


def _migrations() -> list:
    noms = sorted(module.name for module in pkgutil.iter_modules(migrations.__path__) if NOM_MIGRATION.fullmatch(module.name))
    return [importlib.import_module(f'{migrations.__name__}.{nom}') for nom in noms]


def version_maximale() -> int:
    """Version de la dernière migration livrée avec ce logiciel."""
    return max(migration.VERSION for migration in _migrations())


def appliquer(chemin: Path) -> list[str]:
    """Applique les migrations en attente et rend leurs noms (liste vide si la base est à jour, sans rien écrire)."""
    c = ouvrir(chemin)
    c.isolation_level = None  # les transactions sont pilotées ici : sans cela, Python valide les définitions de table d'office
    appliquees = []
    try:
        c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, nom TEXT NOT NULL, applique_le TEXT NOT NULL)')
        faites = {version for (version,) in c.execute('SELECT version FROM schema_migrations')}
        for migration in _migrations():
            if migration.VERSION in faites:
                continue
            c.execute('BEGIN IMMEDIATE')
            try:
                migration.appliquer(c)
                c.execute('INSERT INTO schema_migrations(version, nom, applique_le) VALUES (?,?,?)',
                          (migration.VERSION, migration.NOM, datetime.now(timezone.utc).isoformat(timespec='seconds')))
                c.execute(f'PRAGMA user_version={int(migration.VERSION)}')
                c.execute('COMMIT')
            except BaseException:
                if c.in_transaction:
                    c.execute('ROLLBACK')
                raise
            appliquees.append(migration.NOM)
    finally:
        c.close()
    return appliquees


def essai(chemin: Path) -> dict:
    """Joue les migrations en attente sur une copie de la base, puis contrôle l'intégrité de la copie.

    Rend `{'appliquees': [...], 'integrite': 'ok'}` (`integrite` porte le premier message de SQLite si la copie est
    abîmée). L'original n'est ouvert qu'en lecture seule, et la copie est supprimée à la fin."""
    import sauvegarde  # tardif : sinon le démarrage du serveur charge tout le chiffrement des sauvegardes
    with tempfile.TemporaryDirectory() as dossier:
        copie = Path(dossier) / 'essai.sqlite3'
        sauvegarde._sauvegarde_sqlite(f'{Path(chemin).resolve().as_uri()}?mode=ro', copie)
        appliquees = appliquer(copie)
        c = sqlite3.connect(copie)
        try:
            integrite = c.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            c.close()
    return {'appliquees': appliquees, 'integrite': integrite}
