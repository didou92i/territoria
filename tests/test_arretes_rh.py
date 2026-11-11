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
from territoria import arretes_rh as rh
from tests.test_comptes import Base

NOMINATION = ('Rédige un arrêté de nomination stagiaire de Mme Claire Durand au grade d’adjoint administratif territorial, '
              'à temps complet, à compter du 1er décembre 2026, classée au 3e échelon. Commune de Villeneuve-sur-Exemple.')


def completion(objet='Nomination stagiaire de Mme Claire Durand',
               considerants=('Considérant la vacance de l’emploi d’adjoint administratif territorial,',),
               decisions=('Mme Claire Durand est nommée adjointe administrative territoriale stagiaire, à temps complet, à compter du 1er décembre 2026.',
                          'Mme Claire Durand est classée au 3e échelon de son grade.')):
    contenu = json.dumps({'objet': objet, 'considerants': list(considerants), 'decisions': list(decisions)}, ensure_ascii=False)
    return {'choices': [{'message': {'content': contenu}}]}


class TextesEtThemesTests(unittest.TestCase):
    def test_la_copie_livree_est_complete_et_verifiee(self):
        textes = rh.textes()
        self.assertEqual(set(textes), {'L327-1', 'L327-3', 'L327-7', 'R327-1', 'R327-9', 'R327-13', 'L522-1', 'L522-2', 'L612-1',
                                       'L612-2', 'L612-3', 'L612-5', 'L612-12', 'R421-1', 'L2131-1', 'L2131-2'})
        self.assertIn('six septièmes', textes['L612-5']['text'])

    def test_themes(self):
        for texte, attendu in (('arrêté de nomination stagiaire de M. Martin, adjoint technique', 'nomination'),
                               ('nommer Mme Leroy stagiaire au grade de rédacteur', 'nomination'),
                               ('arrêté de titularisation de M. Martin au 1er mars 2027', 'titularisation'),
                               ('titulariser Mme Leroy dans le grade de rédacteur', 'titularisation'),
                               ('avancement d’échelon de M. Petit au 6e échelon', 'echelon'),
                               ('arrêté portant temps partiel à 80 % de Mme Roux', 'temps_partiel'),
                               ('temps partiel de droit à 80 % pour la naissance de son enfant', 'temps_partiel_droit')):
            with self.subTest(texte=texte):
                self.assertEqual(rh.theme(texte)[0], attendu)
        self.assertIsNone(rh.theme('arrêté interdisant le stationnement rue Victor-Hugo'))

    def test_reconnaissance_d_une_demande(self):
        self.assertTrue(rh.est_demande(NOMINATION))
        self.assertTrue(rh.est_demande('Arrêté d’avancement d’échelon de M. Petit au 6e échelon au 1er janvier 2027'))
        self.assertTrue(rh.est_demande('Titularisation de Mme Leroy au 1er mars 2027', assistant='arrete'))
        self.assertFalse(rh.est_demande('Rédige un arrêté interdisant le stationnement rue Victor-Hugo le 12 mai'))
        self.assertFalse(rh.est_demande('Qu’est-ce qu’un arrêté de titularisation ?'))
        self.assertFalse(rh.est_demande('La titularisation de Mme Leroy est prévue en mars'))  # ni arrêté, ni assistant
        precedent = {'kind': 'arrete_rh', 'inputs': {'contexte': NOMINATION}}
        self.assertTrue(rh.est_demande('Remplace le 3e échelon par le 4e échelon', precedent))
        self.assertTrue(rh.est_demande('Mme Durand, au 1er décembre 2026', {'kind': 'arrete_rh_clarification'}))

    def test_demande_sans_agent_ni_date_appelle_une_question(self):
        donnees, question = rh.preparer('Rédige un arrêté de titularisation', None, 'Commune de Testville')
        self.assertEqual(question, rh.CLARIFICATION)
        donnees, question = rh.preparer(NOMINATION, None, 'Commune de Testville')
        self.assertEqual((question, donnees['collectivite']), ('', 'Commune de Villeneuve-sur-Exemple'))

    def test_temps_partiel_sur_autorisation_ou_de_droit(self):
        textes = rh.textes()
        autorisation = rh.assembler({'contexte': 'Arrêté de temps partiel à 80 % pour Mme Roux', 'collectivite': 'Commune de Testville'},
                                    {'objet': 'Temps partiel', 'considerants': ['Considérant la demande,'], 'decisions': ['Mme Roux est autorisée.']}, textes)
        droit = rh.assembler({'contexte': 'Arrêté de temps partiel de droit à 80 % pour la naissance de son enfant', 'collectivite': 'Commune de Testville'},
                             {'objet': 'Temps partiel', 'considerants': ['Considérant la demande,'], 'decisions': ['Mme Roux est autorisée.']}, textes)
        self.assertIn('L612-1', autorisation['visas'][0])
        self.assertIn('L612-3', droit['visas'][0])
        self.assertTrue(any('2004-777' in v for v in droit['visas']))


class DocumentTests(unittest.TestCase):
    def test_arrete_individuel_complet(self):
        donnees = {'contexte': NOMINATION, 'collectivite': 'Commune de Villeneuve-sur-Exemple'}
        document = rh.assembler(donnees, rh.lire(completion(), donnees), rh.textes())
        self.assertEqual(document['kind'], 'arrete_rh')
        self.assertEqual(document['autorite'], 'Le Maire de la commune de Villeneuve-sur-Exemple')
        self.assertEqual(document['visas'][0], 'Vu le code général de la fonction publique, notamment ses articles L327-1, L327-3, L327-7, R327-1 et R327-9,')
        self.assertTrue(any('[à compléter]' in v and 'statut particulier' in v for v in document['visas']))
        self.assertEqual(document['decision'], 'ARRÊTE')
        self.assertEqual([a['role'] for a in document['articles']], ['decision', 'decision', 'execution', 'recours'])
        self.assertIn('notification', document['articles'][-1]['texte'])
        self.assertTrue(any('Notifié' in s for s in document['signatures']))
        self.assertTrue(any('L2131-2' in p for p in document['points']))  # pas de transmission obligatoire au préfet

    def test_le_modele_ne_cite_ni_ne_calcule(self):
        donnees = {'contexte': NOMINATION, 'collectivite': 'Commune de Testville'}
        with self.assertRaises(rh.ReferenceInventee):
            rh.lire(completion(considerants=('Vu l’article L327-1 du code général de la fonction publique,',)), donnees)
        genere = rh.lire(completion(decisions=('Mme Durand est classée à l’indice brut 367.',)), donnees)
        self.assertEqual(genere['decisions'], ['Mme Durand est classée à l’indice brut [à compléter].'])
        payload = rh.requete(donnees, 'modele', rh.textes())
        self.assertIn('arrêté individuel', payload['messages'][0]['content'])
        self.assertEqual([t['article'] for t in json.loads(payload['messages'][1]['content'])['textes_applicables']],
                         ['L327-1', 'L327-3', 'L327-7', 'R327-1', 'R327-9'])


class ConversationTests(Base):
    def test_projet_complet_dans_la_conversation_avec_word(self):
        with patch.object(llm_backend, 'chat', return_value=completion()):
            statut, reponse = self.appel('POST', '/api/chat', {'question': NOMINATION, 'mode': 'libre'})
        self.assertEqual(statut, 200)
        artefact = reponse['artifact']
        self.assertEqual(artefact['kind'], 'arrete_rh')
        self.assertEqual([s['article'] for s in artefact['legal']['sources']], ['L327-1', 'L327-3', 'L327-7', 'R327-1', 'R327-9', 'R421-1', 'L2131-2'])
        self.assertEqual(reponse['sources'][0]['kind'], 'legifrance')
        statut, word = self.appel('GET', artefact['download'])
        self.assertEqual(statut, 200)
        from docx import Document
        texte = '\n'.join(p.text for p in Document(io.BytesIO(word)).paragraphs)
        for attendu in ('ARRÊTE', 'Vu le code général de la fonction publique', 'Mme Claire Durand est nommée', 'Notifié'):
            self.assertIn(attendu, texte)
        from territoria import administration
        self.assertEqual(administration.activite(server.DB, 5)[0]['action'], 'arrete')

    def test_avec_le_papier_a_en_tete_de_la_collectivite(self):
        import branding
        from docx import Document
        from tests.test_branding import papier_a_en_tete
        branding.upload_gabarit(server.DATA, papier_a_en_tete())
        with patch.object(llm_backend, 'chat', return_value=completion()):
            artefact = self.appel('POST', '/api/chat', {'question': NOMINATION, 'mode': 'libre'})[1]['artifact']
        self.assertFalse(any('logo' in v for v in artefact['vigilances']))  # le papier à en-tête porte déjà l'identité
        word = Document(io.BytesIO(self.appel('GET', artefact['download'])[1]))
        self.assertEqual(word.sections[0].header.paragraphs[0].text, 'Mairie de Testville — 1 place de la Mairie')

    def test_un_arrete_de_police_reste_au_moteur_des_arretes(self):
        import arrete_conversation
        question = 'Rédige un arrêté interdisant le stationnement rue Victor-Hugo le 12 mai 2027'
        self.assertFalse(rh.est_demande(question))
        self.assertTrue(arrete_conversation.is_drafting(question))

    def test_demande_vague_puis_precision(self):
        with patch.object(llm_backend, 'chat', return_value=completion()) as modele:
            premiere = self.appel('POST', '/api/chat', {'question': 'Rédige un arrêté de titularisation', 'mode': 'libre'})[1]
            self.assertEqual((premiere['answer'], premiere['artifact']['kind']), (rh.CLARIFICATION, 'arrete_rh_clarification'))
            modele.assert_not_called()
            seconde = self.appel('POST', '/api/chat', {'question': 'Mme Leroy, rédactrice, au 1er mars 2027', 'mode': 'libre',
                                                     'conversation': premiere['conversation']})[1]
        self.assertEqual(seconde['artifact']['kind'], 'arrete_rh')
        self.assertEqual(seconde['artifact']['document']['theme'], 'titularisation')


if __name__ == '__main__':
    unittest.main()
