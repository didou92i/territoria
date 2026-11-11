# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import unittest
from pathlib import Path

import config

RACINE = Path(__file__).resolve().parents[1]
DOSSIER = RACINE / 'n8n' / 'workflows'

# Graphe du pilote (workflow-n8n.json et workflow-arrete-n8n.json) : noms de nœuds et enchaînement conservés.
CONVERSATION = ['Entrée locale', 'Vérification automatique des sources', 'Modèle local LM Studio']
ARRETES = ['Entrée du projet', 'Recherche des exemples locaux', 'Textes officiels datés', 'Vérification avant rédaction',
           'Rédaction LM Studio', 'Contrôle et document Word']
DELAIS = {  # secondes
    'Vérification automatique des sources': 30, 'Modèle local LM Studio': 600,
    'Recherche des exemples locaux': 60, 'Textes officiels datés': 120, 'Vérification avant rédaction': 60,
    'Rédaction LM Studio': 600, 'Contrôle et document Word': 60,
}
URLS = {
    'Vérification automatique des sources': 'http://territoria:8878/api/verify',
    'Modèle local LM Studio': 'http://territoria:8878/api/llm/chat',
    'Recherche des exemples locaux': 'http://territoria:8878/api/arretes/analyser',
    'Textes officiels datés': 'http://territoria:8878/api/arretes/sources',
    'Vérification avant rédaction': 'http://territoria:8878/api/arretes/preparer',
    'Rédaction LM Studio': 'http://territoria:8878/api/llm/chat',
    'Contrôle et document Word': 'http://territoria:8878/api/arretes/finaliser',
}


def charger(nom):
    return json.loads((DOSSIER / nom).read_text(encoding='utf-8'))


class WorkflowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conversation = charger('conversation.json')
        cls.arretes = charger('arretes.json')
        cls.workflows = [cls.conversation, cls.arretes]

    def noeuds(self, workflow, type_noeud):
        return [n for n in workflow['nodes'] if n['type'] == type_noeud]

    def test_workflows_internes_et_proteges(self):
        for wf in self.workflows:
            for n in wf['nodes']:
                if n['type'] == 'n8n-nodes-base.httpRequest':
                    self.assertTrue(n['parameters']['url'].startswith('http://territoria:8878/api/'))
                    self.assertNotIn('Host', json.dumps(n['parameters'].get('headerParameters', {})))

    def test_delais_inferieurs_au_delai_de_territoria(self):
        for wf in self.workflows:
            total = sum(n['parameters'].get('options', {}).get('timeout', 0) for n in wf['nodes']) / 1000
            self.assertLess(total, config.charger({}).delai_n8n)

    def test_identifiants_et_chemins_de_webhook(self):
        self.assertEqual(self.conversation['id'], 'TerritoriaConv01')
        self.assertEqual(self.arretes['id'], 'TerritoriaArre01')
        for wf, chemin in ((self.conversation, 'territoria-conversation'), (self.arretes, 'territoria-arrete')):
            (webhook,) = self.noeuds(wf, 'n8n-nodes-base.webhook')
            self.assertEqual(webhook['parameters']['path'], chemin)
            self.assertEqual(webhook['parameters']['httpMethod'], 'POST')
            self.assertEqual((webhook['parameters']['responseMode'], webhook['parameters']['responseData']),
                             ('lastNode', 'firstEntryJson'))

    def test_webhook_protege_par_le_jeton_d_entree(self):
        for wf in self.workflows:
            (webhook,) = self.noeuds(wf, 'n8n-nodes-base.webhook')
            self.assertEqual(webhook['parameters']['authentication'], 'headerAuth')
            self.assertEqual(webhook['credentials'], {'httpHeaderAuth': {'id': 'TerritoriaWebh01', 'name': 'Territoria — entrée privée'}})

    def test_noeuds_http_authentifies_par_le_jeton_de_service(self):
        for wf in self.workflows:
            for n in self.noeuds(wf, 'n8n-nodes-base.httpRequest'):
                self.assertEqual(n['parameters']['authentication'], 'genericCredentialType', n['name'])
                self.assertEqual(n['parameters']['genericAuthType'], 'httpHeaderAuth', n['name'])
                self.assertEqual(n['credentials'], {'httpHeaderAuth': {'id': 'TerritoriaServ01', 'name': 'Territoria — services internes'}}, n['name'])

    def test_aucun_en_tete_host_origin_ni_jeton_en_clair(self):
        for wf in self.workflows:
            for n in self.noeuds(wf, 'n8n-nodes-base.httpRequest'):
                self.assertFalse(n['parameters'].get('sendHeaders'), n['name'])
                self.assertNotIn('headerParameters', n['parameters'], n['name'])
            texte = json.dumps(wf, ensure_ascii=False)
            for interdit in ('Host', 'Origin', 'host.docker.internal', '8877', 'X-Territoria'):
                self.assertNotIn(interdit, texte, interdit)

    def test_graphe_du_pilote_conserve(self):
        for wf, noms in ((self.conversation, CONVERSATION), (self.arretes, ARRETES)):
            actifs = [n['name'] for n in wf['nodes'] if n['type'] != 'n8n-nodes-base.stickyNote']
            self.assertEqual(sorted(actifs), sorted(noms))
            self.assertEqual(wf['connections'], {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]}
                                                 for a, b in zip(noms, noms[1:])})

    def test_url_et_delai_de_chaque_noeud(self):
        for wf in self.workflows:
            for n in self.noeuds(wf, 'n8n-nodes-base.httpRequest'):
                self.assertEqual(n['parameters']['url'], URLS[n['name']], n['name'])
                self.assertEqual(n['parameters']['options'], {'timeout': DELAIS[n['name']] * 1000}, n['name'])
                self.assertEqual(n['parameters']['method'], 'POST')

    def test_expressions_de_transmission_du_pilote_conservees(self):
        conv = {n['name']: n['parameters'] for n in self.conversation['nodes']}
        self.assertEqual(conv['Vérification automatique des sources']['jsonBody'], '={{ JSON.stringify($json.body) }}')
        self.assertEqual(conv['Modèle local LM Studio']['jsonBody'],
                         '={{ JSON.stringify({request: $json.request, provider: $json.provider}) }}')
        arr = {n['name']: n['parameters'] for n in self.arretes['nodes']}
        self.assertEqual(arr['Recherche des exemples locaux']['jsonBody'], '={{ JSON.stringify($json.body) }}')
        for nom in ('Textes officiels datés', 'Vérification avant rédaction'):
            self.assertEqual(arr[nom]['jsonBody'], '={{ JSON.stringify($json) }}')
        self.assertEqual(arr['Rédaction LM Studio']['jsonBody'],
                         '={{ JSON.stringify({request: $json.request, provider: $json.provider}) }}')
        self.assertEqual(arr['Contrôle et document Word']['jsonBody'],
                         "={{ JSON.stringify({prepared: $('Vérification avant rédaction').first().json, completion: $json}) }}")

    def test_reglages_du_pilote_conserves(self):
        for wf in self.workflows:
            self.assertEqual(wf['settings'], {'saveManualExecutions': False, 'saveDataErrorExecution': 'none',
                                              'saveDataSuccessExecution': 'none', 'executionOrder': 'v1'})

    def test_identifiants_de_noeuds_uniques_et_workflows_inactifs_a_l_import(self):
        for wf in self.workflows:
            ids = [n['id'] for n in wf['nodes']]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertNotIn('active', wf)
        self.assertFalse({n['id'] for n in self.conversation['nodes']} & {n['id'] for n in self.arretes['nodes']})

    def test_notes_sans_lien_vers_le_pilote(self):
        for wf in self.workflows:
            for note in self.noeuds(wf, 'n8n-nodes-base.stickyNote'):
                self.assertNotIn('localhost', note['parameters']['content'])
                self.assertNotIn('YT8JDBIKrTZY3k15', note['parameters']['content'])


if __name__ == '__main__':
    unittest.main()
