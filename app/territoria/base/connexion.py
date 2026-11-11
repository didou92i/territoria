# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Connexions à la base SQLite de Territoria et mode WAL."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import journal


def ouvrir(chemin: Path) -> sqlite3.Connection:
    """Ouvre la base avec les réglages communs : lignes nommées, attente d'un verrou jusqu'à 20 s, clés étrangères
    vérifiées, `synchronous=NORMAL`.

    Le mode du journal n'en fait pas partie : il est inscrit dans le fichier de la base, et c'est `activer_wal` qui le choisit
    (le réappliquer ici annulerait le repli quand le WAL est indisponible)."""
    c = sqlite3.connect(chemin)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA synchronous=NORMAL')
    return c


def activer_wal(chemin: Path) -> bool:
    """Passe la base en mode WAL et rend `True`. Si le support le refuse, revient au journal classique (`DELETE`), le note
    au journal de fonctionnement (`wal_indisponible`) et rend `False`.

    Le refus prend deux formes : SQLite garde un autre mode que `wal`, ou il lève une `OperationalError` (par exemple
    « attempt to write a readonly database » quand `-wal` et `-shm` ne peuvent pas être créés à côté de la base)."""
    c = ouvrir(chemin)
    try:
        try:
            if str(c.execute('PRAGMA journal_mode=WAL').fetchone()[0]).lower() == 'wal':
                return True
        except sqlite3.OperationalError:
            pass  # même traitement qu'un refus silencieux
        c.execute('PRAGMA journal_mode=DELETE')
    finally:
        c.close()
    journal.evenement('base:wal', 'repli', code='wal_indisponible')
    return False
