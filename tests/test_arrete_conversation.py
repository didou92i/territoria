# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Demande d'arrêté trop vague : phrases types d'abord, exemple fictif ensuite, jamais deux fois la même relance."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arrete_conversation
import branding
from legal_sources import classify

VAGUE = 'Peux-tu m’aider à rédiger un arrêté ?'
SANS_COMMUNE = 'Rédige un arrêté interdisant le stationnement rue des Lilas pour un déménagement.'


def puces(texte):
    return [ligne[2:].strip() for ligne in texte.splitlines() if ligne.startswith('- ')]


class DemandeVagueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_la_premiere_demande_vague_propose_des_phrases_types_qui_aboutissent(self):
        _, clarification = arrete_conversation.prepare(VAGUE, None, self.data)
        phrases = puces(clarification)
        self.assertGreaterEqual(len(phrases), 3)
        # Renvoyée telle quelle, chaque phrase doit mener à une rédaction : thème reconnu et commune nommée.
        self.assertEqual({classify(p) for p in phrases}, {'circulation', 'marche', 'police'})
        for phrase in phrases:
            self.assertEqual(arrete_conversation.collectivity(phrase), 'Commune de Démoville')

    def test_les_phrases_types_reprennent_la_commune_enregistree(self):
        branding.save(self.data, {'collectivite': 'Commune de Senlis', 'logo': ''})
        _, clarification = arrete_conversation.prepare(VAGUE, None, self.data)
        phrases = puces(clarification)
        self.assertTrue(phrases)
        for phrase in phrases:
            self.assertEqual(arrete_conversation.collectivity(phrase), 'Commune de Senlis')

    def test_une_demande_d_exemple_redige_aussitot_un_exemple_fictif(self):
        for demande in ('redige moi un exemple d\'arreté', 'Rédige-moi un modèle d’arrêté.', 'Rédige-moi un arrêté type.'):
            with self.subTest(demande=demande):
                data, clarification = arrete_conversation.prepare(demande, None, self.data)
                self.assertEqual(clarification, '')
                self.assertTrue(arrete_conversation.est_exemple(data))
                self.assertEqual(classify(data['contexte']), 'circulation')
                self.assertIn('fictif', data['contexte'])
                self.assertEqual(data['collectivite'], 'Commune de Démoville')

    def test_l_exemple_fictif_porte_la_collectivite_enregistree(self):
        branding.save(self.data, {'collectivite': 'Commune de Senlis', 'logo': ''})
        data, _ = arrete_conversation.prepare('redige moi un exemple d\'arreté', None, self.data)
        self.assertEqual(data['collectivite'], 'Commune de Senlis')

    def test_la_deuxieme_demande_vague_d_affilee_redige_l_exemple(self):
        precedent = {'kind': 'arrete_clarification', 'inputs': {'contexte': VAGUE, 'collectivite': ''}}
        data, clarification = arrete_conversation.prepare('je ne sais pas trop quoi mettre', precedent, self.data)
        self.assertEqual(clarification, '')
        self.assertTrue(arrete_conversation.est_exemple(data))

    def test_le_seuil_de_precision_ne_compte_que_les_mots_de_l_utilisateur(self):
        # 10 + 7 mots de l'utilisateur ; la mention « Précision la plus récente, prioritaire » en ajouterait 5 (22).
        precedent = {'kind': 'arrete_clarification',
                     'inputs': {'contexte': 'J’ai besoin d’aide pour rédiger un arrêté municipal', 'collectivite': ''}}
        data, clarification = arrete_conversation.prepare('je ne sais pas quoi mettre dedans', precedent, self.data)
        self.assertEqual(clarification, '')
        self.assertTrue(arrete_conversation.est_exemple(data))

    def test_un_exemple_demande_apres_la_question_sur_la_mesure_est_redige(self):
        # Les relances proposent de répondre « un exemple » : la réponse doit aboutir même après une demande déjà précise.
        precedent = {'kind': 'arrete_clarification',
                     'inputs': {'contexte': 'Rédige un arrêté pour un marché de Noël place du Test le samedi 19 décembre 2026.',
                                'collectivite': 'Commune de Testville'}}
        data, clarification = arrete_conversation.prepare('un exemple', precedent, self.data)
        self.assertEqual(clarification, '')
        self.assertTrue(arrete_conversation.est_exemple(data))
        self.assertEqual(data['collectivite'], 'Commune de Testville')

    def test_le_mot_exemple_ne_remplace_pas_un_arrete_en_cours_de_modification(self):
        precedent = {'kind': 'arrete', 'inputs': {'contexte': 'Interdire le stationnement rue des Lilas le 10 janvier 2027.',
                                                  'collectivite': 'Commune de Senlis'}}
        data, _ = arrete_conversation.prepare('Ajoute un exemple de panneau.', precedent, self.data)
        self.assertFalse(arrete_conversation.est_exemple(data))
        self.assertIn('rue des Lilas', data['contexte'])

    def test_par_exemple_dans_une_reponse_ne_remplace_pas_la_demande(self):
        # Les relances disent « par exemple » ; l'utilisateur reprend souvent l'expression dans sa réponse.
        sur_la_commune = {'kind': 'arrete_clarification', 'inputs': {'contexte': SANS_COMMUNE, 'collectivite': ''}}
        sur_la_mesure = {'kind': 'arrete_clarification',
                         'inputs': {'contexte': 'Rédige un arrêté pour un marché de Noël place du Test le samedi 19 décembre 2026.',
                                    'collectivite': 'Commune de Testville'}}
        cas = ((sur_la_commune, 'Par exemple, la commune de Senlis.', 'rue des Lilas'),
               (sur_la_mesure, 'Par exemple, autoriser l’occupation de la place du Test par les exposants.', 'marché de Noël'),
               (sur_la_mesure, 'Un exemple de mesure : autoriser l’occupation de la place du Test par les exposants.', 'marché de Noël'))
        for precedent, reponse, garde in cas:
            with self.subTest(reponse=reponse):
                data, _ = arrete_conversation.prepare(reponse, precedent, self.data)
                self.assertFalse(arrete_conversation.est_exemple(data))
                self.assertIn(garde, data['contexte'])

    def test_de_type_n_est_pas_une_demande_d_exemple(self):
        data, clarification = arrete_conversation.prepare('Rédige un arrêté pour une fête de type brocante.', None, self.data)
        self.assertFalse(arrete_conversation.est_exemple(data))
        self.assertTrue(clarification)

    def test_une_demande_precise_n_est_pas_un_exemple(self):
        data, clarification = arrete_conversation.prepare(
            'Rédige un arrêté pour la ville de Senlis interdisant le stationnement rue des Lilas, par exemple pour un déménagement.',
            None, self.data)
        self.assertEqual(clarification, '')
        self.assertFalse(arrete_conversation.est_exemple(data))
        self.assertIn('rue des Lilas', data['contexte'])

    def test_une_relance_identique_a_la_precedente_est_remplacee(self):
        data, question_commune = arrete_conversation.prepare(SANS_COMMUNE, None, self.data)
        self.assertTrue(question_commune)
        question_mesure = 'Que doit prévoir cet arrêté ? Précisez la mesure.'
        for texte in (question_commune, question_mesure):
            with self.subTest(texte=texte):
                autre = arrete_conversation.sans_repetition(texte, texte, data)
                self.assertTrue(autre.strip())
                self.assertNotEqual(autre.strip(), texte.strip())

    def test_une_relance_differente_de_la_precedente_est_gardee(self):
        self.assertEqual(arrete_conversation.sans_repetition('Question A', 'Question B', {}), 'Question A')
        self.assertEqual(arrete_conversation.sans_repetition('Question A', '', {}), 'Question A')


if __name__ == '__main__':
    unittest.main()
