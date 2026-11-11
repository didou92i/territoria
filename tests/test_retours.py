# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
from territoria import administration, retours
from tests.test_comptes import Base, MDP_ADMIN


class RetoursUnPosteTests(Base):
    def repondre(self, question='Quelles obligations de probité et de dignité ?', mode='sources', jeton=None):
        with patch.object(server, 'call_n8n', return_value='L’agent exerce avec dignité et probité [1].'):
            statut, corps = self.appel('POST', '/api/chat', {'question': question, 'mode': mode, 'space': 'all'}, jeton)
        self.assertEqual(statut, 200)
        return corps

    def test_noter_une_reponse_et_retrouver_la_note(self):
        reponse = self.repondre()
        self.assertIsInstance(reponse['message'], int)
        self.assertEqual(self.appel('POST', f"/api/messages/{reponse['message']}/evaluation", {'note': 1}), (200, {'note': 1}))
        messages = self.appel('GET', f"/api/conversations/{reponse['conversation']}")[1]['messages']
        self.assertEqual([(m['role'], m['note'], m['signale']) for m in messages], [('user', 0, 0), ('assistant', 1, 0)])
        ligne = administration.activite(server.DB, 1)[0]
        self.assertEqual((ligne['action'], ligne['objet'], ligne['detail']), ('evaluation', str(reponse['message']), '1'))

    def test_note_invalide_ou_message_de_l_agent_refuses(self):
        reponse = self.repondre()
        for note in (2, True, '1', None):
            with self.subTest(note=note):
                self.assertEqual(self.appel('POST', f"/api/messages/{reponse['message']}/evaluation", {'note': note})[0], 400)
        self.assertEqual(self.appel('POST', f"/api/messages/{reponse['message'] - 1}/evaluation", {'note': 1})[0], 404)
        self.assertEqual(self.appel('POST', '/api/messages/999999/evaluation', {'note': 1})[0], 404)

    def test_signalement_sans_le_texte_des_documents(self):
        reponse = self.repondre()
        extrait = reponse['sources'][0]['excerpt']
        self.assertEqual(self.appel('POST', f"/api/messages/{reponse['message']}/signalement", {'commentaire': 'Référence à vérifier.'})[0], 201)
        self.assertEqual(self.appel('POST', f"/api/messages/{reponse['message']}/signalement", {})[0], 400)  # une seule fois
        statut, liste = self.appel('GET', '/api/admin/retours')
        self.assertEqual(statut, 200)
        x = liste[0]
        self.assertEqual((x['question'], x['commentaire'], x['statut'], x['mode']),
                         ('Quelles obligations de probité et de dignité ?', 'Référence à vérifier.', 'nouveau', 'sources'))
        self.assertEqual(x['sources'], [{'title': 'CGFP — article L121-1', 'page': None}])
        self.assertNotIn(extrait, json.dumps(liste, ensure_ascii=False))
        self.assertNotIn('message_id', x)
        self.assertTrue(self.appel('GET', f"/api/conversations/{reponse['conversation']}")[1]['messages'][1]['signale'])

    def test_commentaire_trop_long(self):
        reponse = self.repondre()
        self.assertEqual(self.appel('POST', f"/api/messages/{reponse['message']}/signalement", {'commentaire': 'x' * 1001})[0], 400)

    def test_traiter_rouvrir_supprimer(self):
        reponse = self.repondre()
        self.appel('POST', f"/api/messages/{reponse['message']}/signalement", {})
        ident = self.appel('GET', '/api/admin/retours')[1][0]['id']
        self.assertEqual(self.appel('POST', f'/api/admin/retours/{ident}', {'statut': 'traite'})[0], 200)
        self.assertEqual(self.appel('GET', '/api/admin/retours')[1][0]['statut'], 'traite')
        self.assertEqual(self.appel('POST', f'/api/admin/retours/{ident}', {'statut': 'perdu'})[0], 400)
        self.assertEqual(self.appel('DELETE', f'/api/admin/retours/{ident}')[0], 200)
        self.assertEqual(self.appel('GET', '/api/admin/retours')[1], [])
        self.assertEqual(self.appel('DELETE', f'/api/admin/retours/{ident}')[0], 404)

    def test_le_signalement_survit_a_la_conversation_sans_s_attacher_a_un_nouveau_message(self):
        reponse = self.repondre()
        self.appel('POST', f"/api/messages/{reponse['message']}/signalement", {})
        self.assertEqual(self.appel('DELETE', f"/api/conversations/{reponse['conversation']}")[0], 200)
        self.assertEqual(len(self.appel('GET', '/api/admin/retours')[1]), 1)
        nouvelle = self.repondre()  # peut reprendre les mêmes numéros de messages
        messages = self.appel('GET', f"/api/conversations/{nouvelle['conversation']}")[1]['messages']
        self.assertFalse(messages[1]['signale'])
        self.assertEqual(self.appel('POST', f"/api/messages/{nouvelle['message']}/signalement", {})[0], 201)

    def test_signalement_efface_au_bout_d_un_an(self):
        reponse = self.repondre()
        self.appel('POST', f"/api/messages/{reponse['message']}/signalement", {})
        with sqlite3.connect(server.DB) as c:
            c.execute("UPDATE signalements SET quand='2020-01-01T00:00:00+00:00'")
        server.appliquer_conservation()
        self.assertEqual(self.appel('GET', '/api/admin/retours')[1], [])

    def test_statistiques_comptent_la_derniere_note_de_chaque_reponse(self):
        a, b = self.repondre(), self.repondre('Quelle dignité ?')
        self.appel('POST', f"/api/messages/{a['message']}/evaluation", {'note': -1})
        self.appel('POST', f"/api/messages/{a['message']}/evaluation", {'note': 1})  # changement d'avis
        self.appel('POST', f"/api/messages/{b['message']}/evaluation", {'note': -1})
        self.appel('POST', f"/api/messages/{b['message']}/signalement", {})
        self.assertEqual(administration.statistiques(server.DB, 30)['retours'], {'utiles': 1, 'a_ameliorer': 1, 'signalements': 1})


class RetoursEquipeTests(RetoursUnPosteTests.__base__):
    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()
        self.agent_id, self.agent = self.creer(self.admin, 'bob')
        with patch.object(server, 'call_n8n', return_value='Brouillon de courriel.'):
            self.reponse = self.appel('POST', '/api/chat', {'question': 'Courriel aux agents', 'mode': 'libre'}, self.agent)[1]

    def test_seul_l_auteur_note_et_signale(self):
        message = self.reponse['message']
        self.assertEqual(self.appel('POST', f'/api/messages/{message}/evaluation', {'note': 1}, self.admin)[0], 404)
        self.assertEqual(self.appel('POST', f'/api/messages/{message}/signalement', {}, self.admin)[0], 404)
        self.assertEqual(self.appel('POST', f'/api/messages/{message}/evaluation', {'note': -1}, self.agent)[0], 200)

    def test_nom_joint_seulement_sur_demande_et_retours_reserves(self):
        self.appel('POST', f"/api/messages/{self.reponse['message']}/signalement", {'avec_nom': True}, self.agent)
        self.assertEqual(self.appel('GET', '/api/admin/retours', jeton=self.agent)[0], 403)
        _, referent = self.creer(self.admin, 'rachel', 'referent')
        statut, liste = self.appel('GET', '/api/admin/retours', jeton=referent)
        self.assertEqual((statut, liste[0]['auteur_nom']), (200, 'Bob'))
        self.assertEqual(self.appel('POST', f"/api/admin/retours/{liste[0]['id']}", {'statut': 'traite'}, referent)[0], 200)
        with patch.object(server, 'call_n8n', return_value='Autre brouillon.'):
            autre = self.appel('POST', '/api/chat', {'question': 'Autre', 'mode': 'libre'}, self.agent)[1]
        self.appel('POST', f"/api/messages/{autre['message']}/signalement", {'avec_nom': 'oui'}, self.agent)
        self.assertEqual(self.appel('GET', '/api/admin/retours', jeton=self.admin)[1][0]['auteur_nom'], '')


if __name__ == '__main__':
    unittest.main()
