# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import math
import sys
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import llm_backend  # noqa: E402
import server
from territoria import semantique
from tests.test_comptes import Base

# Faux modèle : un axe par thème ; un texte reçoit les axes de ses mots-clés de thème (synonymes compris).
THEMES = (('conge', 'absence', 'vacance'), ('probite', 'honnetete', 'integrite'), ('stationn', 'garer'))


def faux_vecteur(texte):
    simple = texte.lower().replace('é', 'e').replace('ê', 'e')
    v = [1.0 if any(m in simple for m in mots) else 0.0 for mots in THEMES] + [0.3]
    n = math.sqrt(sum(x * x for x in v))
    return array('f', (x / n for x in v))


def faux_plonger(donnees, nom, textes, question=False):
    return [faux_vecteur(t) for t in textes]


class SemantiqueTests(Base):
    def setUp(self):
        super().setUp()
        for cible, nom, valeur in ((semantique, 'plonger', faux_plonger), (semantique, 'modele', lambda donnees: 'faux-modele')):
            correctif = patch.object(cible, nom, valeur)
            correctif.start()
            self.addCleanup(correctif.stop)
        semantique._etat['echec'] = 0.0
        self.conges = server.add_doc('interne', 'conges.txt', 'Chaque agent bénéficie de vingt-cinq jours de congés annuels.'.encode())
        server.add_doc('interne', 'voirie.txt', 'Le stationnement est interdit rue des Lilas le mardi.'.encode())

    def test_sans_index_aucun_appel_au_modele(self):
        with patch.object(semantique, 'plonger') as modele:
            self.assertEqual(semantique.similarites(server.DB, server.DATA, 'absences', [1, 2, 3]), {})
        modele.assert_not_called()

    def test_indexation_puis_resultat_sans_mot_commun(self):
        self.assertEqual(server.search('Combien de jours d’absence ?', 'interne')[0]['title'], 'conges.txt')  # mot commun « jours »
        self.assertEqual(server.search('règles sur les absences', 'interne'), [])  # pas encore d'index : mots-clés seuls
        while semantique.indexer(server.DB, server.DATA, 'faux-modele'):
            pass
        self.assertEqual([r['title'] for r in server.search('règles sur les absences', 'interne')], ['conges.txt'])
        self.assertEqual([r['title'] for r in server.search('où peut-on se garer ?', 'interne')], ['voirie.txt'])
        self.assertEqual(server.search('recette de la tarte aux pommes', 'interne'), [])

    def test_les_droits_s_appliquent_aussi_au_sens(self):
        while semantique.indexer(server.DB, server.DATA, 'faux-modele'):
            pass
        self.assertEqual(server.search('règles sur les absences', 'all', espaces=['public']), [])

    def test_suppression_du_document_efface_ses_vecteurs(self):
        while semantique.indexer(server.DB, server.DATA, 'faux-modele'):
            pass
        server.supprimer_document(self.conges)
        with server.db() as c:
            restants = c.execute('SELECT COUNT(*) FROM chunk_vecteurs v LEFT JOIN chunks ch ON ch.id=v.chunk_id WHERE ch.id IS NULL').fetchone()[0]
        self.assertEqual(restants, 0)
        self.assertEqual(server.search('règles sur les absences', 'interne'), [])

    def test_etat_et_reglage_par_l_administration(self):
        with patch.object(semantique, 'disponibles', return_value=[{'id': 'faux-modele', 'charge': True}]):
            statut, etat = self.appel('GET', '/api/admin/recherche')
        self.assertEqual((statut, etat['modele'], etat['indexes']), (200, 'faux-modele', 0))
        self.assertEqual(self.appel('POST', '/api/admin/recherche', {'modele': 'autre-modele'})[0], 200)
        self.assertEqual(semantique.reglage(server.DATA), 'autre-modele')
        self.assertEqual(self.appel('POST', '/api/admin/recherche', {'modele': 'x\n'})[0], 400)


class PrefixesTests(unittest.TestCase):
    def test_prefixes_par_famille(self):
        self.assertEqual(semantique._prefixe('text-embedding-embeddinggemma-2', True), 'task: search result | query: ')
        self.assertEqual(semantique._prefixe('nomic-embed-text', False), 'search_document: ')
        self.assertEqual(semantique._prefixe('autre', True), '')


class ChoixDesPlongementsTests(unittest.TestCase):
    """Recette de la démonstration : le choix du modèle de plongements suit le fournisseur."""
    LISTE = [{'id': 'amazon/titan-embed-image-v1', 'charge': True}, {'id': 'mistralai/mistral-embed-2312', 'charge': True},
             {'id': 'openai/text-embedding-3-small', 'charge': True}]

    def test_choix_automatique_prefere_un_modele_de_texte_connu(self):
        with patch.object(semantique, 'reglage', return_value=''), patch.object(semantique, 'disponibles', return_value=self.LISTE):
            self.assertEqual(semantique.modele(Path('/donnees')), 'mistralai/mistral-embed-2312')
        with patch.object(semantique, 'reglage', return_value=''), patch.object(semantique, 'disponibles', return_value=self.LISTE[::2]):
            self.assertEqual(semantique.modele(Path('/donnees')), 'openai/text-embedding-3-small')


class ChangementDeFournisseurTests(Base):
    def test_changer_de_fournisseur_remet_le_choix_des_plongements_en_automatique(self):
        from dataclasses import replace
        import config
        import web
        semantique.enregistrer(server.DATA, 'text-embedding-embeddinggemma-2')  # modèle de LM Studio
        demo = replace(config.charger({}), demonstration=True)
        modeles = {'data': [{'id': 'mistralai/mistral-medium-3-5', 'supported_parameters': ['structured_outputs']}]}
        with patch.object(llm_backend, 'REGLAGES', demo), patch.object(llm_backend, '_request', return_value=modeles), \
                patch.object(semantique, 'lancer'):
            llm_backend.save(server.DATA, {'provider': 'lmstudio'})  # point de départ : l'IA locale
            statut, _ = self.appel('POST', '/api/ia', {'provider': 'openrouter', 'model': 'mistralai/mistral-medium-3-5',
                                                       'base_url': 'https://openrouter.ai/api/v1', 'api_key': 'sk-or-essai'})
        self.assertEqual(statut, 200)
        self.assertEqual(semantique.reglage(server.DATA), '')

    def test_meme_fournisseur_garde_le_choix(self):
        semantique.enregistrer(server.DATA, 'text-embedding-embeddinggemma-2')
        ia = llm_backend.public(server.DATA)
        with patch.object(semantique, 'lancer'):
            self.assertEqual(self.appel('POST', '/api/ia', {'provider': ia['provider'], 'model': ia['model'], 'base_url': ia['base_url']})[0], 200)
        self.assertEqual(semantique.reglage(server.DATA), 'text-embedding-embeddinggemma-2')


class OpenRouterPlongementsTests(unittest.TestCase):
    """Démonstration : plongements par OpenRouter, avec la préférence sur les données ; en local, rien ne change."""
    OPENROUTER = {'provider': 'openrouter', 'base_url': 'https://openrouter.ai/api/v1', 'model': 'mistral/modele', 'api_key': 'k'}
    LOCAL = {'provider': 'lmstudio', 'base_url': 'http://127.0.0.1:1234/v1', 'model': 'gemma', 'api_key': ''}
    VECTEUR = {'data': [{'index': 0, 'embedding': [1.0, 0.0]}]}

    def test_plongements_openrouter_avec_preference(self):
        with patch.object(llm_backend, 'read', return_value=self.OPENROUTER), \
                patch.object(llm_backend, '_request', return_value=self.VECTEUR) as requete:
            semantique.plonger(Path('/donnees'), 'openai/text-embedding-3-small', ['texte'])
        config, chemin, corps = requete.call_args.args[:3]
        self.assertEqual((config['provider'], chemin), ('openrouter', '/embeddings'))
        self.assertEqual(corps['provider'], llm_backend.PREFERENCE_OPENROUTER)

    def test_disponibles_openrouter_sans_api_v0(self):
        liste = {'data': [{'id': 'openai/text-embedding-3-small'}, {'id': 'qwen/qwen3-embedding-0.6b'}]}
        with patch.object(llm_backend, 'read', return_value=self.OPENROUTER), \
                patch.object(llm_backend, '_request', return_value=liste) as requete:
            disponibles = semantique.disponibles(Path('/donnees'))
        self.assertEqual(disponibles, [{'id': 'openai/text-embedding-3-small', 'charge': True},
                                       {'id': 'qwen/qwen3-embedding-0.6b', 'charge': True}])
        self.assertEqual([appel.args[1] for appel in requete.call_args_list], ['/embeddings/models'])

    def test_plongements_locaux_inchanges(self):
        with patch.object(llm_backend, 'read', return_value=self.LOCAL), \
                patch.object(llm_backend, '_request', return_value=self.VECTEUR) as requete:
            semantique.plonger(Path('/donnees'), 'nomic-embed-text', ['texte'], question=True)
        self.assertEqual(requete.call_args.args[1:3], ('/embeddings', {'model': 'nomic-embed-text', 'input': ['search_query: texte']}))


class MistralPlongementsTests(unittest.TestCase):
    """Option Mistral : plongements par mistral-embed, sans préférence de routage ni API /api/v0."""
    MISTRAL = {'provider': 'mistral', 'base_url': 'https://api.mistral.ai/v1', 'model': 'mistral-small-latest', 'api_key': 'k'}
    MODELES = {'data': [{'id': 'mistral-small-latest', 'capabilities': {'completion_chat': True}},
                        {'id': 'mistral-embed', 'capabilities': {'completion_chat': False}}]}

    def test_plongements_mistral_sans_preference(self):
        with patch.object(llm_backend, 'read', return_value=self.MISTRAL), \
                patch.object(llm_backend, '_request', return_value=OpenRouterPlongementsTests.VECTEUR) as requete:
            semantique.plonger(Path('/donnees'), 'mistral-embed', ['texte'])
        self.assertEqual(requete.call_args.args[1:3], ('/embeddings', {'model': 'mistral-embed', 'input': ['texte']}))

    def test_disponibles_mistral(self):
        with patch.object(llm_backend, 'read', return_value=self.MISTRAL), \
                patch.object(llm_backend, '_request', return_value=self.MODELES) as requete:
            disponibles = semantique.disponibles(Path('/donnees'))
        self.assertEqual(disponibles, [{'id': 'mistral-embed', 'charge': True}])
        self.assertEqual([appel.args[1] for appel in requete.call_args_list], ['/models'])

    def test_modele_mistral_embed_choisi_automatiquement(self):
        with patch.object(semantique, 'reglage', return_value=''), patch.object(llm_backend, 'read', return_value=self.MISTRAL), \
                patch.object(llm_backend, '_request', return_value=self.MODELES):
            self.assertEqual(semantique.modele(Path('/donnees')), 'mistral-embed')


class ChoixParFournisseurTests(unittest.TestCase):
    """Le choix des plongements vaut pour le fournisseur qui l'a vu : un changement par l'installation (étapes mistral ou
    lmstudio, sans passer par l'écran) ne doit pas envoyer à Mistral l'identifiant d'un modèle de LM Studio."""
    def setUp(self):
        import tempfile
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        self.donnees = Path(dossier.name)
        self.fournisseur()

    def fournisseur(self, **valeurs):
        from dataclasses import replace
        import config
        correctif = patch.object(llm_backend, 'REGLAGES', replace(config.charger({}), **valeurs))
        correctif.start()
        self.addCleanup(correctif.stop)

    def test_choix_local_ignore_quand_mistral_est_active(self):
        semantique.enregistrer(self.donnees, 'text-embedding-nomic-embed-text-v1.5')
        self.fournisseur(ia_en_ligne='mistral', mistral_cle='k')
        self.assertEqual(semantique.reglage(self.donnees), '')
        with patch.object(llm_backend, '_request', return_value=MistralPlongementsTests.MODELES):
            self.assertEqual(semantique.modele(self.donnees), 'mistral-embed')

    def test_choix_mistral_ignore_au_retour_au_local(self):
        self.fournisseur(ia_en_ligne='mistral', mistral_cle='k')
        semantique.enregistrer(self.donnees, 'mistral-embed')
        self.fournisseur()
        self.assertEqual(semantique.reglage(self.donnees), '')

    def test_ancien_fichier_sans_fournisseur_vaut_pour_le_local(self):
        (self.donnees / 'configuration').mkdir()
        (self.donnees / 'configuration' / 'recherche.json').write_text('{"modele_plongements": "nomic-embed"}')
        self.assertEqual(semantique.reglage(self.donnees), 'nomic-embed')
        self.fournisseur(ia_en_ligne='mistral', mistral_cle='k')
        self.assertEqual(semantique.reglage(self.donnees), '')


if __name__ == '__main__':
    unittest.main()
