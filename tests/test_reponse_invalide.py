# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Un modèle qui répond à vide ou hors forme : une seule relance, puis un message qui ne parle pas de « modèle local »."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arretes
import llm_backend
import server
from territoria import deliberations

MESSAGE = 'Le service d’IA n’a pas renvoyé de réponse exploitable ; réessayez dans un instant.'
VIDE = {'choices': []}


class MessageTests(unittest.TestCase):
    def test_reponse_vide_message_clair_et_type_dedie(self):
        for lire in (lambda: deliberations.lire(VIDE, {}), lambda: arretes.read_completion(VIDE, {})):
            with self.assertRaises(deliberations.ReponseInvalide) as erreur:
                lire()
            self.assertEqual(str(erreur.exception), MESSAGE)
            self.assertNotIn('local', str(erreur.exception))


class RelanceTests(unittest.TestCase):
    def test_rediger_relance_une_fois_puis_reussit(self):
        module = MagicMock(ReferenceInventee=deliberations.ReferenceInventee)
        module.lire.side_effect = [deliberations.ReponseInvalide(MESSAGE), {'objet': 'ok'}]
        with patch.object(llm_backend, 'require_configured', return_value={'model': 'm'}), \
                patch.object(llm_backend, 'chat', return_value=VIDE) as chat:
            self.assertEqual(server._rediger(module, {}, {}), {'objet': 'ok'})
        self.assertEqual(chat.call_count, 2)

    def test_rediger_abandonne_apres_la_seconde_reponse_invalide(self):
        module = MagicMock(ReferenceInventee=deliberations.ReferenceInventee)
        module.lire.side_effect = deliberations.ReponseInvalide(MESSAGE)
        with patch.object(llm_backend, 'require_configured', return_value={'model': 'm'}), \
                patch.object(llm_backend, 'chat', return_value=VIDE) as chat, self.assertRaises(deliberations.ReponseInvalide):
            server._rediger(module, {}, {})
        self.assertEqual(chat.call_count, 2)

    def test_arrete_interne_relance_une_fois(self):
        preparee = {'request': {}, 'provider': 'openrouter'}
        with patch.object(arretes, 'analyze', return_value={}), patch.object(arretes, 'enrich', return_value={}), \
                patch.object(arretes, 'prepare', return_value=preparee), \
                patch.object(llm_backend, 'read', return_value={'model': 'm', 'provider': 'openrouter'}), \
                patch.object(llm_backend, 'chat', return_value=VIDE) as chat, \
                patch.object(arretes, 'finish', side_effect=[deliberations.ReponseInvalide(MESSAGE), {'id': 'a'}]):
            self.assertEqual(server._arrete_interne({}), {'id': 'a'})
        self.assertEqual(chat.call_count, 2)


class LimiteDeReponseTests(unittest.TestCase):
    def test_les_redactions_laissent_au_modele_la_place_de_reflechir(self):
        # Avec 3 000 jetons, un modèle qui réfléchit avant de répondre épuisait la limite : réponse vide (délégation,
        # deux fois sur trois sur la démonstration). 6 000 : trois réussites sur trois.
        from territoria import arretes_delegations, arretes_police, arretes_rh
        cas = ((arretes_delegations, 'Délégation de signature à Mme Claire Exemple, adjointe, pour l’état civil.'),
               (arretes_police, 'Fermeture de la rue des Lilas et déviation par la rue du Moulin le 3 mars 2027 pour des travaux.'),
               (arretes_rh, 'Nomination de Mme Durand, adjoint administratif, stagiaire au 1er mars 2027.'),
               (deliberations, 'Attribution d’une subvention de 500 euros à l’association sportive.'))
        for module, contexte in cas:
            with self.subTest(module=module.__name__):
                demande = {'collectivite': 'Commune de Testville', 'contexte': contexte}
                self.assertGreaterEqual(module.requete(demande, 'modele', module.textes())['max_tokens'], 6000)


class ChatLibreTests(unittest.TestCase):
    """Chat (assistants en rédaction libre, questions avec sources) : même relance, même message, même place pour réfléchir."""
    def setUp(self):
        from dataclasses import replace
        correctif = patch.object(server, 'REGLAGES', replace(server.REGLAGES, orchestration='interne'))
        correctif.start()
        self.addCleanup(correctif.stop)
        correctif = patch.object(llm_backend, 'require_configured', return_value={'model': 'm', 'provider': 'openrouter'})
        correctif.start()
        self.addCleanup(correctif.stop)

    @staticmethod
    def reponse(texte):
        return {'choices': [{'message': {'content': texte}}]}

    def test_reponse_vide_relancee_une_fois(self):
        with patch.object(server, '_conversation_interne', side_effect=[self.reponse(''), self.reponse('Bonjour')]) as appel:
            self.assertEqual(server.call_n8n([{'role': 'user', 'content': 'x'}], 'libre', []), 'Bonjour')
        self.assertEqual(appel.call_count, 2)

    def test_deux_reponses_vides_message_clair(self):
        with patch.object(server, '_conversation_interne', return_value=self.reponse('')), self.assertRaises(RuntimeError) as erreur:
            server.call_n8n([{'role': 'user', 'content': 'x'}], 'libre', [])
        self.assertEqual(str(erreur.exception), MESSAGE)

    def test_place_pour_reflechir(self):
        with patch.object(server, '_conversation_interne', return_value=self.reponse('ok')) as appel:
            server.call_n8n([{'role': 'user', 'content': 'x'}], 'libre', [])
        self.assertGreaterEqual(appel.call_args.args[0]['request']['max_tokens'], 6000)


if __name__ == '__main__':
    unittest.main()
