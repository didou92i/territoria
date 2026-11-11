# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Un fichier que TerritorIA ne sait pas lire est refusé avec une explication, pas par une panne."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
from tests.test_comptes import NAVIGATEUR, Base
from tests.test_publication_web import docx

BINAIRE = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
ARCHIVE_VIDE = b'PK\x05\x06' + b'\0' * 18


class ImportIllisibleTests(Base):
    def importer(self, nom, corps):
        return self.appel('POST', f'/api/upload/interne?name={nom}', corps, None, BINAIRE)

    def test_fichier_illisible_refuse_avec_une_explication(self):
        with server.db() as c:
            avant = c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        fichiers = sorted(server.FILES.iterdir())
        cas = (('faux.docx', b'ceci n est pas un Word', server.WORD_ILLISIBLE), ('vide.docx', ARCHIVE_VIDE, server.WORD_ILLISIBLE),
               ('faux.pdf', b'ceci n est pas un PDF', server.PDF_ILLISIBLE))
        for nom, corps, message in cas:
            with self.subTest(nom=nom):
                self.assertEqual(self.importer(nom, corps), (400, {'error': message}))
        with server.db() as c:  # rien n'est gardé : ni ligne, ni fichier
            self.assertEqual(c.execute('SELECT COUNT(*) FROM documents').fetchone()[0], avant)
        self.assertEqual(sorted(server.FILES.iterdir()), fichiers)

    def test_word_au_paragraphe_demesure_refuse_sans_panne(self):
        # Au-delà de 10 Mo dans un seul paragraphe, la lecture du XML échoue : c'est un refus, pas une erreur du serveur.
        self.assertEqual(self.importer('enorme.docx', docx('a' * (11 * 1024 * 1024))), (400, {'error': server.WORD_ILLISIBLE}))

    def test_un_fichier_lisible_s_importe_toujours(self):
        statut, corps = self.importer('note.docx', docx('Les archives municipales ouvrent le mardi.'))
        self.assertEqual(statut, 201, corps)
        self.assertEqual(self.importer('protege.pdf', b'%PDF-1.4 tronque')[0], 400)


if __name__ == '__main__':
    unittest.main()
