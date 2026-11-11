# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Recherche par mots-clés : les mots du métier (« agent », « public », « territorial ») ne sont pas des mots vides."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
from tests.test_comptes import Base


class RechercheMetierTests(Base):
    def setUp(self):
        super().setUp()
        server.add_doc('interne', 'deontologie.txt', 'L’agent public exerce ses fonctions avec dignité et probité.'.encode())
        server.add_doc('interne', 'voirie.txt', 'Le service public de la voirie est assuré par la commune.'.encode())

    def titres(self, question):
        return [r['title'] for r in server.search(question, 'interne')]

    def test_requete_faite_de_mots_du_metier_trouve(self):
        self.assertIn('deontologie.txt', self.titres('agent public'))
        self.assertIn('deontologie.txt', self.titres('agents publics'))
        self.assertIn('voirie.txt', self.titres('service public'))

    def test_un_mot_du_metier_ne_suffit_pas_quand_la_requete_a_un_autre_mot(self):
        # Comportement inchangé : « voirie » décide, le mot « public » seul ne fait pas remonter les autres documents.
        self.assertEqual(self.titres('public voirie'), ['voirie.txt'])

    def test_mots_vides_ordinaires_toujours_ignores(self):
        self.assertEqual(self.titres('les des une'), [])


class ExtraitsNumerotesTests(Base):
    """Deux extraits d'un même document sans page (CSV, texte) ne doivent pas s'afficher sous un titre identique."""

    def test_deux_extraits_du_meme_document_sont_numerotes(self):
        lignes = ['ASSOCIATION %03d;subvention;650' % i for i in range(120)]
        server.add_doc('interne', 'subventions.txt', '\n'.join(lignes).encode())
        server.add_doc('interne', 'autre.txt', 'Une subvention de fonctionnement.'.encode())
        resultats = server.search('subvention 650', 'interne')
        meme = [r for r in resultats if r['title'] == 'subventions.txt']
        self.assertEqual(len(meme), 2)
        self.assertEqual(sorted(r['extrait'] for r in meme), [1, 2])
        self.assertTrue(all('extrait' not in r for r in resultats if r['title'] == 'autre.txt'))


class ListePartielleTests(Base):
    """Une question qui attend une liste, posée sur des extraits d'un document plus long, est prévenue qu'elle peut être incomplète."""

    def setUp(self):
        super().setUp()
        lignes = ['ASSOCIATION %03d;subvention;650' % i for i in range(120)]
        server.add_doc('interne', 'subventions.txt', '\n'.join(lignes).encode())
        server.add_doc('interne', 'court.txt', 'Subvention de 650 euros pour le club de judo.'.encode())

    def test_question_de_liste_sur_un_document_lu_en_partie(self):
        sources = server.search('subvention 650', 'interne')
        note = server.liste_partielle('Quelles associations ont reçu une subvention de 650 euros ?', sources)
        self.assertIn('incomplète', note)
        self.assertIn('subventions.txt', note)
        self.assertNotIn('court.txt', note)

    def test_question_ordinaire_ou_document_lu_en_entier(self):
        sources = server.search('subvention 650', 'interne')
        self.assertEqual(server.liste_partielle('Quel montant a reçu le club de judo ?', sources), '')
        courts = [s for s in sources if s['title'] == 'court.txt']
        self.assertEqual(server.liste_partielle('Quelles subventions de 650 euros ?', courts), '')


if __name__ == '__main__':
    unittest.main()
