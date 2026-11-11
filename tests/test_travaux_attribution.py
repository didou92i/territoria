# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from travaux.attribution import attribuer
from travaux.mesures import justesse_intervenants, taux_erreur_mots


class AttributionTests(unittest.TestCase):
    def test_plus_grand_recouvrement_et_numerotation_par_apparition(self):
        tours = [{'debut': 0, 'fin': 5, 'groupe': 7}, {'debut': 5, 'fin': 9, 'groupe': 2}, {'debut': 9, 'fin': 12, 'groupe': 7}]
        segs = [{'debut': 0.5, 'fin': 4, 'texte': 'a'}, {'debut': 4.5, 'fin': 8, 'texte': 'b'}, {'debut': 9.5, 'fin': 11, 'texte': 'c'}]
        resultat = attribuer(segs, tours)
        self.assertEqual([s['intervenant'] for s in resultat], ['Intervenant 1', 'Intervenant 2', 'Intervenant 1'])
        self.assertEqual([s['texte'] for s in resultat], ['a', 'b', 'c'])
        self.assertNotIn('intervenant', segs[0])  # des copies : les segments reçus ne sont pas modifiés

    def test_sans_recouvrement_tour_le_plus_proche_et_sans_tour_un_seul_intervenant(self):
        tours = [{'debut': 0, 'fin': 2, 'groupe': 4}, {'debut': 10, 'fin': 12, 'groupe': 9}]
        segs = [{'debut': 8, 'fin': 9, 'texte': 'proche du second'}, {'debut': 2.5, 'fin': 3, 'texte': 'proche du premier'}]
        self.assertEqual([s['intervenant'] for s in attribuer(segs, tours)], ['Intervenant 1', 'Intervenant 2'])
        self.assertEqual([s['intervenant'] for s in attribuer(segs, [])], ['Intervenant 1', 'Intervenant 1'])
        self.assertEqual(attribuer([], tours), [])


class MesuresTests(unittest.TestCase):
    def test_taux_erreur_mots(self):
        self.assertEqual(taux_erreur_mots('Le conseil municipal délibère.', 'le conseil municipal délibère'), 0.0)
        self.assertAlmostEqual(taux_erreur_mots('un deux trois quatre', 'un trois quatre cinq'), 0.5)
        self.assertEqual(taux_erreur_mots('', ''), 0.0)
        self.assertEqual(taux_erreur_mots('élu', 'elu'), 1.0)  # les accents comptent

    def test_justesse_intervenants_apres_appariement(self):
        ref = [{'debut': 0, 'fin': 10, 'groupe': 1}, {'debut': 10, 'fin': 20, 'groupe': 2}]
        pred = [{'debut': 0, 'fin': 10, 'intervenant': 'Intervenant 2'}, {'debut': 10, 'fin': 18, 'intervenant': 'Intervenant 1'},
                {'debut': 18, 'fin': 20, 'intervenant': 'Intervenant 2'}]
        self.assertAlmostEqual(justesse_intervenants(ref, pred), 0.9)
        self.assertEqual(justesse_intervenants(ref, []), 0.0)


if __name__ == '__main__':
    unittest.main()
