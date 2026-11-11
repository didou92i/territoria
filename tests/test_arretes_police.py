# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import llm_backend
import server
from territoria import arretes_police as po
from tests.test_comptes import Base

TRAVAUX = ('Rédige un arrêté pour la commune de Villeneuve-sur-Exemple : la rue des Lilas sera barrée à la circulation entre le n° 2 '
           'et le n° 40 du 3 au 14 mars 2027 pour des travaux de réfection de chaussée, avec une déviation par la rue des Tilleuls ; '
           'accès riverains et secours maintenus.')
TERRASSE = ('Rédige un arrêté autorisant le café Le Central à installer une terrasse de 12 m² devant le 5 place de la Mairie, '
            'du 1er avril au 30 septembre 2027. Commune de Villeneuve-sur-Exemple.')


def completion(objet='Réglementation de la circulation rue des Lilas pendant des travaux',
               considerants=('Considérant les travaux de réfection de chaussée de la rue des Lilas du 3 au 14 mars 2027,',),
               decisions=('Du 3 au 14 mars 2027, la circulation est interdite rue des Lilas entre le n° 2 et le n° 40, sauf pour les riverains et les véhicules de secours.',
                          'Une déviation est mise en place par la rue des Tilleuls.')):
    contenu = json.dumps({'objet': objet, 'considerants': list(considerants), 'decisions': list(decisions)}, ensure_ascii=False)
    return {'choices': [{'message': {'content': contenu}}]}


class TextesEtThemesTests(unittest.TestCase):
    def test_la_copie_livree_est_complete_et_verifiee(self):
        self.assertEqual(set(po.textes()), {'L2212-1', 'L2212-2', 'L2213-1', 'L2213-6', 'R411-8', 'R411-25', 'R417-10', 'L2122-1',
                                            'L2122-2', 'L2122-3', 'L2125-1', 'L2131-1', 'L2131-2', 'R421-1', 'L3334-2', 'L3321-1',
                                            'L3335-4', 'L3335-1'})

    def test_themes(self):
        for texte, attendu in ((TRAVAUX, 'circulation_travaux'),
                               ('alternat de circulation par feux sur la route de Paris pendant les travaux du réseau d’eau', 'circulation_travaux'),
                               ('déviation de la circulation pendant la fermeture de la rue Haute', 'circulation_travaux'),
                               (TERRASSE, 'occupation'),
                               ('pose d’un échafaudage sur le trottoir du 12 rue Basse', 'occupation'),
                               ('benne à gravats devant le 3 rue Neuve', 'occupation')):
            with self.subTest(texte=texte):
                self.assertEqual(po.theme(texte)['nom'], attendu)
        for texte in ('interdire le stationnement rue Victor-Hugo le 12 mai pour un déménagement', 'interdire les pétards le 14 juillet'):
            with self.subTest(texte=texte):
                self.assertIsNone(po.theme(texte))  # restent au moteur des arrêtés de police existant

    def test_reconnaissance_d_une_demande(self):
        import arrete_conversation
        self.assertTrue(po.est_demande(TRAVAUX))
        self.assertTrue(po.est_demande(TERRASSE))
        self.assertTrue(po.est_demande('Benne devant le 3 rue Neuve du 2 au 4 mai 2027', assistant='arrete'))
        self.assertFalse(po.est_demande('Faut-il un arrêté pour une terrasse ?'))
        demenagement = 'Rédige un arrêté interdisant le stationnement rue Victor-Hugo le 12 mai 2027 pour un déménagement'
        self.assertFalse(po.est_demande(demenagement))
        self.assertTrue(arrete_conversation.is_drafting(demenagement))
        self.assertTrue(po.est_demande('Ajoute le samedi 15 mars', {'kind': 'arrete_police', 'inputs': {'contexte': TRAVAUX}}))

    def test_sans_date_une_question(self):
        donnees, question = po.preparer('Rédige un arrêté de déviation pour des travaux rue Haute', None, 'Commune de Testville')
        self.assertEqual(question, po.CLARIFICATION)
        donnees, question = po.preparer(TRAVAUX, None, 'Commune de Testville')
        self.assertEqual((question, donnees['collectivite']), ('', 'Commune de Villeneuve-sur-Exemple'))


class DocumentTests(unittest.TestCase):
    def test_arrete_de_circulation_pour_travaux(self):
        donnees = {'contexte': TRAVAUX, 'collectivite': 'Commune de Villeneuve-sur-Exemple'}
        genere = po.lire(completion(), donnees)
        self.assertIn('sauf pour les riverains', genere['decisions'][0])  # la règle et son exception restent ensemble
        self.assertIn('n° 2 et le n° 40', genere['decisions'][0])
        document = po.assembler(donnees, genere, po.textes())
        self.assertEqual(document['kind'], 'arrete_police')
        self.assertEqual(document['visas'][:2], ['Vu le code général des collectivités territoriales, notamment ses articles L2212-1, L2212-2 et L2213-1,',
                                                 'Vu le code de la route, notamment ses articles R411-8, R411-25 et R417-10,'])
        self.assertTrue(any('huitième partie' in v for v in document['visas']))
        self.assertEqual([a['role'] for a in document['articles']], ['mesure', 'mesure', 'signalisation', 'controle', 'publicite', 'recours', 'execution'])
        self.assertTrue(any('L2131-2' in p for p in document['points']))
        self.assertTrue(any('grande circulation' in p for p in document['points']))

    def test_permis_de_stationnement_pour_une_terrasse(self):
        donnees = {'contexte': TERRASSE, 'collectivite': 'Commune de Villeneuve-sur-Exemple'}
        genere = po.lire(completion(objet='Terrasse du café Le Central', considerants=('Considérant la demande du café Le Central,',),
                                    decisions=('Le café Le Central est autorisé à installer une terrasse de 12 m² devant le 5 place de la Mairie, du 1er avril au 30 septembre 2027.',)), donnees)
        document = po.assembler(donnees, genere, po.textes())
        self.assertEqual(document['visas'][:2], ['Vu le code général des collectivités territoriales, notamment son article L2213-6,',
                                                 'Vu le code général de la propriété des personnes publiques, notamment ses articles L2122-1, L2122-2, L2122-3 et L2125-1,'])
        self.assertEqual([a['role'] for a in document['articles']], ['mesure', 'conditions', 'redevance', 'precarite', 'recours', 'execution'])
        self.assertIn('précaire et révocable', document['articles'][3]['texte'])
        self.assertTrue(any('permission de voirie' in p for p in document['points']))

    def test_debit_de_boissons_temporaire(self):
        demande = ('Rédige un arrêté autorisant le comité des fêtes à ouvrir une buvette lors de la fête du village, place de la Mairie, '
                   'le 13 juillet 2027 de 18 heures à 1 heure. Commune de Villeneuve-sur-Exemple.')
        self.assertEqual(po.theme(demande)['nom'], 'debit_boissons')
        donnees = {'contexte': demande, 'collectivite': 'Commune de Villeneuve-sur-Exemple'}
        genere = po.lire(completion(objet='Débit de boissons temporaire', considerants=('Considérant la demande du comité des fêtes,',),
                                    decisions=('Le comité des fêtes est autorisé à ouvrir un débit de boissons temporaire place de la Mairie le 13 juillet 2027 de 18 heures à 1 heure.',)), donnees)
        document = po.assembler(donnees, genere, po.textes())
        self.assertEqual(document['visas'][0], 'Vu le code de la santé publique, notamment ses articles L3321-1 et L3334-2,')
        self.assertEqual([a['role'] for a in document['articles']], ['mesure', 'boissons', 'obligations', 'precarite', 'recours', 'execution'])
        self.assertIn('groupes un et trois', document['articles'][1]['texte'])
        self.assertTrue(any('cinq autorisations' in p for p in document['points']))
        self.assertTrue(any('L2131-2' in p for p in document['points']))

    def test_le_modele_ne_cite_aucun_texte(self):
        with self.assertRaises(po.ReferenceInventee):
            po.lire(completion(considerants=('Vu l’article L2213-1 du code général des collectivités territoriales,',)), {'contexte': TRAVAUX})


class ConversationTests(Base):
    def test_projet_complet_dans_la_conversation_avec_word(self):
        with patch.object(llm_backend, 'chat', return_value=completion()):
            statut, reponse = self.appel('POST', '/api/chat', {'question': TRAVAUX, 'mode': 'libre'})
        self.assertEqual(statut, 200)
        artefact = reponse['artifact']
        self.assertEqual(artefact['kind'], 'arrete_police')
        self.assertEqual([s['article'] for s in artefact['legal']['sources']],
                         ['L2212-1', 'L2212-2', 'L2213-1', 'R411-8', 'R411-25', 'R417-10', 'L2131-1', 'L2131-2', 'R421-1'])
        statut, word = self.appel('GET', artefact['download'])
        self.assertEqual(statut, 200)
        from docx import Document
        texte = '\n'.join(p.text for p in Document(io.BytesIO(word)).paragraphs)
        for attendu in ('ARRÊTE', 'huitième partie', 'sauf pour les riverains'):
            self.assertIn(attendu, texte)
        from territoria import administration
        self.assertEqual(administration.activite(server.DB, 5)[0]['action'], 'arrete')

    def test_sans_article_de_signalisation_le_message_n_en_parle_pas(self):
        # Test de la démonstration : un arrêté d'échafaudage demandait de compléter « l'exécutant de la signalisation ».
        occupation = ('Arrêté pour une occupation du domaine public : l’entreprise Exemple BTP installe un échafaudage devant le '
                      '12 rue de la Gare du 3 au 28 mai 2027.')
        genere = completion(objet='Occupation du domaine public par un échafaudage rue de la Gare',
                            decisions=('L’entreprise Exemple BTP est autorisée à installer un échafaudage devant le 12 rue de la Gare du 3 au 28 mai 2027.',))
        with patch.object(llm_backend, 'chat', return_value=genere):
            statut, reponse = self.appel('POST', '/api/chat', {'question': occupation, 'mode': 'libre'})
        self.assertEqual(statut, 200)
        roles = [a['role'] for a in reponse['artifact']['document']['articles']]
        self.assertNotIn('signalisation', roles)
        self.assertNotIn('signalisation', reponse['answer'])
        self.assertNotIn('signalisation', reponse['artifact']['vigilances'][0])
        with patch.object(llm_backend, 'chat', return_value=completion()):
            statut, reponse = self.appel('POST', '/api/chat', {'question': TRAVAUX, 'mode': 'libre'})
        self.assertIn('signalisation', reponse['answer'])  # un arrêté de circulation, lui, en a une

    def test_une_modification_transmet_les_decisions_precedentes_a_reprendre(self):
        # Test de la démonstration : en ajoutant l'arrêt de bus, le modèle avait réécrit l'article 2 et perdu ses dates. Il
        # reçoit désormais les décisions du projet précédent, avec la consigne de reprendre telles quelles celles que la
        # précision ne touche pas.
        with patch.object(llm_backend, 'chat', return_value=completion()):
            statut, premiere = self.appel('POST', '/api/chat', {'question': TRAVAUX, 'mode': 'libre'})
        self.assertEqual(statut, 200)
        with patch.object(llm_backend, 'chat', return_value=completion()) as chat:
            statut, seconde = self.appel('POST', '/api/chat', {'question': 'Ajoute que l’accès à la boulangerie du n° 12 reste possible.',
                                                               'mode': 'libre', 'conversation': premiere['conversation']})
        self.assertEqual(statut, 200)
        requete = chat.call_args.args[1]
        requete = requete.get('request', requete)
        utilisateur = json.loads(requete['messages'][1]['content'])
        self.assertEqual(utilisateur['decisions_du_projet_precedent'], [
            'Du 3 au 14 mars 2027, la circulation est interdite rue des Lilas entre le n° 2 et le n° 40, sauf pour les riverains et les véhicules de secours.',
            'Une déviation est mise en place par la rue des Tilleuls.'])
        self.assertIn('mot pour mot', requete['messages'][0]['content'])
        # Première rédaction : rien de tel.
        with patch.object(llm_backend, 'chat', return_value=completion()) as chat:
            self.appel('POST', '/api/chat', {'question': TRAVAUX, 'mode': 'libre'})
        requete = chat.call_args.args[1]
        requete = requete.get('request', requete)
        self.assertNotIn('decisions_du_projet_precedent', json.loads(requete['messages'][1]['content']))


if __name__ == '__main__':
    unittest.main()
