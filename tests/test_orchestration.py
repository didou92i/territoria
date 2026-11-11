# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Démonstration légère (hébergement sans conteneurs) : TerritorIA enchaîne lui-même les étapes des workflows n8n."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import config
import llm_backend
import server
import web
from territoria import comptes, seances
from tests.test_comptes import Base

N8N_INTERDIT = AssertionError('n8n ne doit pas être appelé en orchestration interne')


class ReglageOrchestrationTests(unittest.TestCase):
    def test_n8n_par_defaut(self):
        self.assertEqual(config.charger({}).orchestration, 'n8n')

    def test_interne(self):
        self.assertEqual(config.charger({'TERRITORIA_ORCHESTRATION': 'interne'}).orchestration, 'interne')

    def test_valeur_inconnue_refusee(self):
        with self.assertRaises(ValueError):
            config.charger({'TERRITORIA_ORCHESTRATION': 'zapier'})


class OrchestrationInterneTests(Base):
    def setUp(self):
        super().setUp()
        correctif = patch.object(server, 'REGLAGES', replace(server.REGLAGES, orchestration='interne'))
        correctif.start()
        self.addCleanup(correctif.stop)
        correctif = patch.object(server, 'ouvrir_sans_proxy', side_effect=N8N_INTERDIT)
        correctif.start()
        self.addCleanup(correctif.stop)

    def test_question_sans_n8n(self):
        vus = []

        def chat(donnees, charge):
            vus.append(charge)
            return {'choices': [{'message': {'content': ' Réponse [1]. '}}]}
        with patch.object(llm_backend, 'chat', side_effect=chat):
            self.assertEqual(server.call_n8n([{'role': 'user', 'content': 'Q'}], 'libre', []), 'Réponse [1].')
        self.assertEqual(vus[0]['request']['messages'], [{'role': 'user', 'content': 'Q'}])
        self.assertEqual(vus[0]['provider'], llm_backend.read(server.DATA)['provider'])

    def test_question_cause_du_fournisseur_affichee(self):
        with patch.object(llm_backend, 'chat', side_effect=RuntimeError('Clé API refusée.')), \
                self.assertRaises(RuntimeError) as erreur:
            server.call_n8n([{'role': 'user', 'content': 'Q'}], 'libre', [])
        self.assertEqual(str(erreur.exception), 'Clé API refusée.')

    def test_arrete_sans_n8n_enchaine_les_etapes_du_workflow(self):
        ordre = []
        preparee = {'request': {'model': 'm', 'messages': []}, 'etat': 'prepare'}
        completion = {'choices': [{'message': {'content': '{}'}}]}

        def etape(nom, sortie):
            def appel(entree, *_):
                ordre.append((nom, entree))
                return sortie
            return appel

        def chat(donnees, charge):
            ordre.append(('chat', charge))
            return completion
        arretes = server.arretes
        with patch.object(arretes, 'verify_reference'), patch.object(arretes, 'clean_input', return_value={'demande': 'x'}), \
                patch.object(arretes, 'analyze', side_effect=etape('analyser', {'analyse': 1})), \
                patch.object(arretes, 'enrich', side_effect=etape('sources', {'sources': 1})), \
                patch.object(arretes, 'prepare', side_effect=etape('preparer', preparee)), \
                patch.object(llm_backend, 'chat', side_effect=chat), \
                patch.object(arretes, 'finish', side_effect=etape('finaliser', {'id': 'd' * 32})):
            self.assertEqual(server.draft_arrete({'demande': 'x'})['id'], 'd' * 32)
        fournisseur = llm_backend.read(server.DATA)['provider']
        self.assertEqual([nom for nom, _ in ordre], ['analyser', 'sources', 'preparer', 'chat', 'finaliser'])
        self.assertEqual(ordre[0][1], {'demande': 'x'})
        self.assertEqual(ordre[1][1], {'analyse': 1})
        self.assertEqual(ordre[2][1], {'sources': 1})
        self.assertEqual(ordre[3][1], {'request': preparee['request'], 'provider': fournisseur})
        self.assertEqual(ordre[4][1], {'prepared': preparee | {'provider': fournisseur}, 'completion': completion})

    def test_arrete_refuse_cause_affichee(self):
        arretes = server.arretes
        with patch.object(arretes, 'verify_reference'), patch.object(arretes, 'clean_input', return_value={}), \
                patch.object(arretes, 'analyze', side_effect=ValueError('Précisez le lieu.')), \
                self.assertRaises(RuntimeError) as erreur:
            server.draft_arrete({})
        self.assertEqual(str(erreur.exception), 'Précisez le lieu.')

    def test_session_annonce_l_absence_des_seances(self):
        # Démo légère : aucun conteneur de transcription, une séance déposée ne serait jamais transcrite.
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, orchestration='interne', demonstration=True)):
            self.assertIs(self.appel('GET', '/api/session')[1]['seances'], False)
        self.assertNotIn('seances', self.appel('GET', '/api/session')[1])

    def test_seances_refusees_cote_serveur(self):
        # Masquer la page ne suffit pas : sans transcription, chaque séance garderait jusqu'à 1 Gio d'audio sur le disque.
        seance = 'a' * 32
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, orchestration='interne')):
            for methode, chemin, corps in (('POST', '/api/seances', {'type': 'conseil'}),
                                           ('GET', f'/api/seances/{seance}', None), ('GET', f'/api/seances/{seance}/audio', None),
                                           ('POST', f'/api/seances/{seance}/audio?rang=0&format=webm', b'x'),
                                           ('POST', f'/api/seances/{seance}/audio/fin', {})):
                with self.subTest(methode=methode, chemin=chemin):
                    self.assertEqual(self.appel(methode, chemin, corps), (404, web.SEANCES_INDISPONIBLES))
            with server.db() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM seances').fetchone()[0], 0)
        self.assertEqual(self.appel('GET', '/api/seances'), (200, {'seances': []}))  # installation normale : inchangé

    def test_une_seance_deja_la_reste_visible_et_supprimable(self):
        # Données venues d'une installation complète : rien ne s'y ajoute, mais on doit pouvoir libérer la place.
        ancienne = seances.creer(server.DB, comptes.IMPLICITE['id'], {'type': 'conseil', 'titre': 'Conseil municipal', 'date': '2026-10-14'})
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, orchestration='interne')):
            self.assertEqual([s['id'] for s in self.appel('GET', '/api/seances')[1]['seances']], [ancienne])
            self.assertEqual(self.appel('DELETE', f'/api/seances/{ancienne}'), (200, {'supprime': True}))
            self.assertEqual(self.appel('GET', '/api/seances'), (200, {'seances': []}))

    def test_etat_n8n_sans_sonde(self):
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, orchestration='interne')), \
                patch.object(llm_backend, 'test', return_value={'model_available': True}):
            self.assertTrue(web._statut()['n8n'])


if __name__ == '__main__':
    unittest.main()
