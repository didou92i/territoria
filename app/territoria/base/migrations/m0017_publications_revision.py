# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Révision de la fiche relue, y compris pour les actes déjà enregistrés."""

VERSION = 17
NOM = 'publications_revision'


def appliquer(c):
    c.execute('ALTER TABLE publications ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
    c.execute('CREATE TRIGGER publications_revision_suivante AFTER UPDATE ON publications '
              'WHEN NEW.revision = OLD.revision BEGIN '
              'UPDATE publications SET revision=OLD.revision+1 WHERE id=NEW.id; END')
