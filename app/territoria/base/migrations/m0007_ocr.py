# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Migration 0007 : extraits lus par lecture optique (OCR) à l'import d'un PDF scanné, signalés « à vérifier »."""

VERSION = 7
NOM = 'ocr'


def appliquer(c):
    if 'ocr' not in {ligne[1] for ligne in c.execute('PRAGMA table_info(chunks)')}:
        c.execute('ALTER TABLE chunks ADD COLUMN ocr INTEGER NOT NULL DEFAULT 0')
