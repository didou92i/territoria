# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import contextlib
import io
import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # faux_llm.py est à côté des tests
import arretes
import demo
import faux_llm

QUESTION = 'Rédige un arrêté pour la Commune de Démoville interdisant le stationnement rue des Essais pour un déménagement.'
LEGAL_VIDE = {'as_of': '', 'sources': [], 'status': 'incomplet'}


class FauxLlmBase(unittest.TestCase):
    def setUp(self):
        self.serveur = faux_llm.creer_serveur('127.0.0.1', 0)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.addCleanup(self.serveur.server_close)
        self.addCleanup(self.serveur.shutdown)
        self.base = f'http://127.0.0.1:{self.serveur.server_address[1]}'

    def appeler(self, chemin, corps=None, methode=None):
        requete = urllib.request.Request(self.base + chemin, method=methode or ('POST' if corps is not None else 'GET'),
                                         data=None if corps is None else (corps if isinstance(corps, bytes) else json.dumps(corps).encode()),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(requete, timeout=10) as reponse:
                return reponse.status, json.loads(reponse.read())
        except urllib.error.HTTPError as erreur:
            return erreur.code, json.loads(erreur.read())

    def conversation(self, *messages):
        statut, corps = self.appeler('/v1/chat/completions', {'model': 'faux-modele', 'messages': [
            {'role': 'user', 'content': m} for m in messages]})
        self.assertEqual(statut, 200)
        return corps


class FauxLlmTests(FauxLlmBase):
    def test_le_port_par_defaut_est_1234(self):
        self.assertEqual(faux_llm.PORT, 1234)

    def test_modeles(self):
        self.assertEqual(self.appeler('/v1/models'), (200, {'data': [{'id': 'faux-modele'}]}))

    def test_reponse_libre_cite_la_source(self):
        corps = self.conversation('Quand ouvrent les archives ?')
        self.assertEqual(corps['choices'][0]['message'],
                         {'role': 'assistant', 'content': 'Les archives municipales fictives ouvrent le mardi matin [1].'})

    def test_un_message_contenant_json_donne_la_structure_de_l_arrete(self):
        for messages in (('Retourne un objet JSON',), ('Bonjour', 'exactement un JSON valide')):
            with self.subTest(messages=messages):
                contenu = self.conversation(*messages)['choices'][0]['message']['content']
                structure = json.loads(contenu)
                self.assertEqual(set(structure), {'objet', 'considerants', 'mesures'})
                self.assertFalse(re.search(r'\d', contenu), 'aucun chiffre dans la réponse')

    def test_seul_le_mot_json_en_majuscules_declenche_la_structure(self):
        contenu = self.conversation('Voici un fichier json')['choices'][0]['message']['content']
        self.assertEqual(contenu, faux_llm.REPONSE_SOURCEE)

    def test_le_json_peut_etre_dans_n_importe_quel_message(self):
        statut, corps = self.appeler('/v1/chat/completions', {'messages': [
            {'role': 'system', 'content': 'Retourne un objet JSON'}, {'role': 'user', 'content': 'Question'}]})
        self.assertEqual(statut, 200)
        self.assertEqual(set(json.loads(corps['choices'][0]['message']['content'])), {'objet', 'considerants', 'mesures'})

    def test_requetes_invalides(self):
        self.assertEqual(self.appeler('/inconnu')[0], 404)
        self.assertEqual(self.appeler('/v1/inconnu', {})[0], 404)
        self.assertEqual(self.appeler('/v1/chat/completions', b'pas du json')[0], 400)
        self.assertEqual(self.appeler('/v1/chat/completions', b'[1, 2]')[0], 200)  # corps non objet : réponse libre

    def test_aucune_ligne_de_journal(self):
        with contextlib.redirect_stderr(io.StringIO()) as erreur, contextlib.redirect_stdout(io.StringIO()) as sortie:
            self.conversation('Une question confidentielle')
            self.appeler('/inconnu')
        self.assertEqual((erreur.getvalue(), sortie.getvalue()), ('', ''))


class ValidationParReadCompletionTests(FauxLlmBase):
    """La réponse JSON doit passer la vraie validation, avec les vrais faits que `arretes.prepare` construit."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(demo.installer(self.data))

    def preparer(self, contexte):
        with patch('arretes.legal_sources.select', return_value=LEGAL_VIDE):
            return arretes.prepare({'collectivite': 'Commune de Démoville', 'contexte': contexte}, self.data, 'faux-modele')

    def test_la_demande_de_redaction_reclame_du_json(self):
        prepare = self.preparer(QUESTION)
        self.assertTrue(faux_llm.demande_un_arrete(prepare['request']['messages']))

    def test_les_questions_de_la_conversation_ne_reclament_pas_de_json(self):
        # Messages construits par server.chat en mode « sources » : consigne système + extraits + question.
        messages = [{'role': 'system', 'content': 'Tu es un assistant pour les agents territoriaux. Cite les passages par '
                                                  'leur numéro [1], [2].'},
                    {'role': 'user', 'content': 'EXTRAITS DOCUMENTAIRES :\n[1] Essai, page 2\nLes archives ouvrent.\n\n'
                                                'QUESTION :\nQuand ouvrent les archives municipales fictives ?'}]
        self.assertFalse(faux_llm.demande_un_arrete(messages))

    def test_read_completion_accepte_la_reponse_pour_la_demande_de_l_e2e(self):
        prepare = self.preparer(QUESTION)
        statut, completion = self.appeler('/v1/chat/completions', prepare['request'])
        self.assertEqual(statut, 200)
        resultat = arretes.read_completion(completion, prepare['data'])
        self.assertEqual([a['role'] for a in resultat['articles']], ['mesure', 'mise_en_oeuvre', 'controle'])
        self.assertIn('interdit', resultat['articles'][0]['texte'])
        self.assertNotIn('[Préciser', resultat['articles'][0]['texte'])  # la mesure n'a pas été neutralisée
        # Ni le considérant ni la mesure n'ont été réécrits : seuls restent les points fixes d'une mesure de police.
        fixes = ('Vérifier que la mesure est nécessaire', 'Faire poser la signalisation')
        self.assertEqual([v for v in resultat['_vigilances'] if not v.startswith(fixes)], [])

    def test_read_completion_accepte_aussi_un_contexte_sans_interdiction(self):
        prepare = self.preparer('Réglementer le stationnement rue des Essais pour un déménagement.')
        _, completion = self.appeler('/v1/chat/completions', prepare['request'])
        arretes.read_completion(completion, prepare['data'])  # ne lève rien (la mesure est remplacée par un point à préciser)


if __name__ == '__main__':
    unittest.main()
