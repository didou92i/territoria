# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import config
import outils
import server
import web
from territoria import VERSION, comptes

ORIGINE = 'http://127.0.0.1:9001'
NAVIGATEUR = {'Host': '127.0.0.1:9001', 'Origin': ORIGINE, 'Content-Type': 'application/json'}
MDP_ADMIN = 'mot-de-passe-admin-solide'
MDP_AGENT = 'mot-de-passe-agent-solide'
# Routes d'administration (lecture) : toutes refusées à un agent.
ROUTES_ADMIN = ('/api/admin/utilisateurs', '/api/admin/services', '/api/admin/activite', '/api/admin/statistiques',
                '/api/admin/sante', '/api/admin/modeles', '/api/admin/assistants', '/api/ia')


class Base(unittest.TestCase):
    NOM_BASE = 'db.sqlite'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        donnees = Path(self.tmp.name)
        for cible, nom, valeur in [
            (web, 'REGLAGES', replace(config.REGLAGES, origine=ORIGINE, service_token='jeton-test')),
            (server, 'DATA', donnees), (server, 'FILES', donnees / 'documents'), (server, 'DB', donnees / self.NOM_BASE),
        ]:
            correctif = patch.object(cible, nom, valeur)
            correctif.start()
            self.addCleanup(correctif.stop)
        server.init()
        redirection = redirect_stderr(io.StringIO())
        redirection.__enter__()
        self.addCleanup(redirection.__exit__, None, None, None)

    def appel(self, methode, chemin, donnees=None, jeton=None, entetes=None):
        entetes = dict(entetes or NAVIGATEUR)
        if jeton:
            entetes['Cookie'] = f'{comptes.COOKIE}={jeton}'
        corps = b'' if donnees is None else (donnees if isinstance(donnees, bytes) else json.dumps(donnees).encode())
        reponse = web.handle(methode, chemin, entetes, corps)
        self.derniere = reponse
        try:
            return reponse.status, json.loads(reponse.body)
        except ValueError:
            return reponse.status, reponse.body

    def jeton_de(self, reponse):
        cookie = dict(reponse.headers)['Set-Cookie']
        return cookie.split(';', 1)[0].split('=', 1)[1]

    def premier_admin(self):
        statut, _ = self.appel('POST', '/api/comptes/premier', {'identifiant': 'admin', 'nom': 'Alice Admin', 'mot_de_passe': MDP_ADMIN})
        self.assertEqual(statut, 201)
        return self.jeton_de(self.derniere)

    def creer(self, admin, identifiant, role='agent', service_id=None, mot_de_passe=MDP_AGENT):
        """Crée le compte, change son mot de passe provisoire et rend (identifiant interne, jeton)."""
        statut, corps = self.appel('POST', '/api/admin/utilisateurs',
                                   {'identifiant': identifiant, 'nom': identifiant.title(), 'role': role, 'service_id': service_id}, admin)
        self.assertEqual(statut, 201)
        jeton = self.connexion(identifiant, corps['mot_de_passe'])
        self.assertEqual(self.appel('POST', '/api/compte/mot-de-passe', {'ancien': corps['mot_de_passe'], 'nouveau': mot_de_passe}, jeton)[0], 200)
        return corps['utilisateur']['id'], jeton

    def connexion(self, identifiant, mot_de_passe):
        statut, corps = self.appel('POST', '/api/connexion', {'identifiant': identifiant, 'mot_de_passe': mot_de_passe})
        self.assertEqual(statut, 200, corps)
        return self.jeton_de(self.derniere)

    def televerser(self, jeton, espace, texte='Les archives municipales ouvrent le mardi.'):
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        return self.appel('POST', f'/api/upload/{espace}?name=note.txt', texte.encode(), jeton, entetes)


class PremierCompteTests(Base):
    def test_mode_un_poste_sans_connexion(self):
        statut, session = self.appel('GET', '/api/session')
        self.assertEqual((statut, session['mode'], session['utilisateur']['implicite']), (200, 'solo', True))
        self.assertEqual(self.appel('GET', '/api/spaces')[0], 200)

    def test_premier_administrateur_recoit_les_contenus_et_une_session_sure(self):
        conversation = server.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True)['conversation']
        jeton = self.premier_admin()
        cookie = dict(self.derniere.headers)['Set-Cookie']
        for attribut in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(attribut, cookie)
        with server.db() as c:
            proprietaires = {r['id']: r['proprietaire'] for r in c.execute('SELECT id, proprietaire FROM spaces')}
            conv = c.execute('SELECT proprietaire FROM conversations WHERE id=?', (conversation,)).fetchone()['proprietaire']
        admin_id = self.appel('GET', '/api/session', jeton=jeton)[1]['utilisateur']['id']
        self.assertEqual((proprietaires['interne'], proprietaires['public'], conv), (admin_id, '', admin_id))
        self.assertEqual([x['id'] for x in self.appel('GET', '/api/conversations', jeton=jeton)[1]], [conversation])

    def test_premier_administrateur_recoit_les_seances_du_mode_un_poste(self):
        # Recette de la démonstration : une séance créée avant tout compte restait au compte implicite, invisible ensuite.
        from territoria import seances
        ident = seances.creer(server.DB, '', {'type': 'conseil', 'titre': 'Conseil du 2 novembre', 'date': '2026-11-02'})
        jeton = self.premier_admin()
        self.assertEqual([s['id'] for s in self.appel('GET', '/api/seances', jeton=jeton)[1]['seances']], [ident])

    def test_ensuite_tout_exige_une_connexion_sauf_la_page(self):
        self.premier_admin()
        self.assertEqual(self.appel('GET', '/api/spaces'), (401, web.CONNEXION_REQUISE))
        self.assertEqual(self.appel('POST', '/api/chat', {'question': 'Bonjour', 'mode': 'libre'})[0], 401)
        self.assertEqual(web.handle('GET', '/', NAVIGATEUR, b'').status, 200)
        self.assertEqual(self.appel('GET', '/api/session')[1], {'mode': 'equipe', 'utilisateur': None, 'roles': comptes.ROLES})

    def test_un_seul_premier_compte_et_mot_de_passe_assez_long(self):
        self.assertEqual(self.appel('POST', '/api/comptes/premier', {'identifiant': 'admin', 'nom': 'A', 'mot_de_passe': 'court'})[0], 400)
        self.assertFalse(comptes.mode_equipe(server.DB))
        jeton = self.premier_admin()
        donnees = {'identifiant': 'intrus', 'nom': 'Intrus', 'mot_de_passe': 'un-mot-de-passe-long'}
        self.assertEqual(self.appel('POST', '/api/comptes/premier', donnees)[0], 403)
        self.assertEqual(self.appel('POST', '/api/comptes/premier', donnees, jeton)[0], 403)


class VisiteurHorsDemonstrationTests(Base):
    def test_pas_de_visiteur_hors_demonstration(self):
        self.assertEqual(self.appel('POST', '/api/session/visiteur', {})[0], 404)
        self.assertFalse(comptes.mode_equipe(server.DB))


class DemonstrationAccesTests(Base):
    """Serveur de démonstration : aucun accès anonyme ; le premier administrateur se crée sur le serveur."""
    def setUp(self):
        super().setUp()
        self.demonstration(ORIGINE)

    def demonstration(self, origine):
        correctif = patch.object(web, 'REGLAGES', replace(web.REGLAGES, demonstration=True, origine=origine))
        correctif.start()
        self.addCleanup(correctif.stop)

    def admin_du_serveur(self):
        comptes.creer_premier_administrateur(server.DB, {'identifiant': 'admin', 'nom': 'Alice Admin', 'mot_de_passe': MDP_ADMIN})

    def test_demonstration_sans_compte_tout_est_refuse(self):
        self.assertEqual(self.appel('GET', '/api/spaces'), (401, web.CONNEXION_REQUISE))
        self.assertEqual(self.appel('POST', '/api/chat', {'question': 'Bonjour', 'mode': 'libre'})[0], 401)
        self.assertEqual(web.handle('GET', '/', NAVIGATEUR, b'').status, 200)  # la page de connexion reste servie

    def test_demonstration_session_annonce_la_preparation(self):
        self.assertEqual(self.appel('GET', '/api/session'),
                         (200, {'mode': 'equipe', 'utilisateur': None, 'roles': comptes.ROLES, 'demonstration': True, 'pret': False,
                                'fournisseur': '', 'version': VERSION}))
        self.admin_du_serveur()
        self.assertIs(self.appel('GET', '/api/session')[1]['pret'], True)

    def test_visiteur_essaie_sans_compte(self):
        self.admin_du_serveur()
        statut, corps = self.appel('POST', '/api/session/visiteur', {})
        self.assertEqual(statut, 200)
        jeton = self.jeton_de(self.derniere)
        visiteur = self.appel('GET', '/api/session', jeton=jeton)[1]['utilisateur']
        self.assertEqual((visiteur['role'], visiteur['nom'], visiteur['doit_changer']), ('agent', 'Visiteur', False))
        self.assertTrue(visiteur['identifiant'].startswith('visiteur-'))
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=jeton)[0], 200)
        self.assertEqual(self.appel('GET', '/api/admin/utilisateurs', jeton=jeton)[0], 403)

    def test_chaque_visiteur_a_son_propre_compte(self):
        self.admin_du_serveur()
        premier = self.appel('POST', '/api/session/visiteur', {})[1]['utilisateur']['identifiant']
        second = self.appel('POST', '/api/session/visiteur', {})[1]['utilisateur']['identifiant']
        self.assertNotEqual(premier, second)

    def test_visiteurs_plafonnes_par_heure(self):
        self.admin_du_serveur()
        with patch.object(comptes, 'VISITEURS_PAR_HEURE', 2):
            self.assertEqual([self.appel('POST', '/api/session/visiteur', {})[0] for _ in range(3)], [200, 200, 429])
            self.assertEqual(self.appel('POST', '/api/session/visiteur', {})[1]['error'], comptes.MESSAGE_VISITEURS)

    def ia_qui_repond(self):
        """IA simulée derrière la démo légère (orchestration interne) : chaque demande atteint llm_backend.chat."""
        import llm_backend
        llm_backend.save(server.DATA, {'provider': 'lmstudio'})
        for cible, nom, valeur in [(server, 'REGLAGES', replace(server.REGLAGES, orchestration='interne')),
                                   (llm_backend, '_request', lambda *a, **k: {'choices': [{'message': {'content': 'Réponse.'}}]})]:
            correctif = patch.object(cible, nom, valeur)
            correctif.start()
            self.addCleanup(correctif.stop)

    def questions(self, jeton, nombre):
        return [self.appel('POST', '/api/chat', {'question': 'Bonjour', 'mode': 'libre'}, jeton=jeton) for _ in range(nombre)]

    def test_visiteur_plafond_de_demandes_a_l_ia(self):
        # Les crédits d'IA de la démonstration sont ceux de l'administrateur : un visiteur ne peut pas tout consommer.
        self.admin_du_serveur()
        self.ia_qui_repond()
        self.appel('POST', '/api/session/visiteur', {})
        jeton = self.jeton_de(self.derniere)
        with patch.object(comptes, 'ESSAIS_PAR_VISITEUR', 2):
            reponses = self.questions(jeton, 3)
        self.assertEqual([s for s, _ in reponses], [200, 200, 429])  # trop de demandes, pas une panne du serveur
        self.assertEqual(reponses[2][1]['error'], comptes.MESSAGE_ESSAIS_VISITEUR)

    def test_plafond_du_jour_pour_l_ensemble_des_visiteurs(self):
        self.admin_du_serveur()
        self.ia_qui_repond()
        jetons = []
        for _ in range(2):
            self.appel('POST', '/api/session/visiteur', {})
            jetons.append(self.jeton_de(self.derniere))
        with patch.object(comptes, 'ESSAIS_VISITEURS_PAR_JOUR', 3):
            self.assertEqual([s for s, _ in self.questions(jetons[0], 2)], [200, 200])
            premiere, refusee = self.questions(jetons[1], 2)
        self.assertEqual((premiere[0], refusee[0]), (200, 429))
        self.assertEqual(refusee[1]['error'], comptes.MESSAGE_ESSAIS_JOUR)

    def test_administrateur_sans_plafond(self):
        self.admin_du_serveur()
        self.ia_qui_repond()
        _, jeton = comptes.connecter(server.DB, 'admin', MDP_ADMIN)
        with patch.object(comptes, 'ESSAIS_PAR_VISITEUR', 1), patch.object(comptes, 'ESSAIS_VISITEURS_PAR_JOUR', 1):
            self.assertEqual([s for s, _ in self.questions(jeton, 3)], [200, 200, 200])

    def test_visiteur_refuse_tant_que_la_demonstration_est_en_preparation(self):
        self.assertEqual(self.appel('POST', '/api/session/visiteur', {})[0], 403)
        self.assertFalse(comptes.mode_equipe(server.DB))

    def test_demonstration_premier_compte_web_refuse(self):
        statut, corps = self.appel('POST', '/api/comptes/premier', {'identifiant': 'admin', 'nom': 'Intrus', 'mot_de_passe': MDP_ADMIN})
        self.assertEqual((statut, corps.get('code')), (403, 'demonstration'))
        self.assertEqual(corps['error'], 'En démonstration, le premier administrateur se crée sur le serveur (install/demonstration.sh).')
        self.assertFalse(comptes.mode_equipe(server.DB))

    def test_cookie_secure_en_https(self):
        self.demonstration('https://demo.exemple.fr')
        self.admin_du_serveur()
        entetes = {'Host': 'demo.exemple.fr', 'Origin': 'https://demo.exemple.fr', 'Content-Type': 'application/json'}
        statut, _ = self.appel('POST', '/api/connexion', {'identifiant': 'admin', 'mot_de_passe': MDP_ADMIN}, entetes=entetes)
        self.assertEqual(statut, 200)
        self.assertIn('; Secure', dict(self.derniere.headers)['Set-Cookie'])

    def test_cookie_sans_secure_en_http(self):
        self.admin_du_serveur()
        self.connexion('admin', MDP_ADMIN)
        self.assertNotIn('Secure', dict(self.derniere.headers)['Set-Cookie'])

    def test_hors_demonstration_inchange(self):
        self.demonstration(ORIGINE)
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, demonstration=False)):
            self.assertEqual(set(self.appel('GET', '/api/session')[1]), {'mode', 'utilisateur', 'roles'})
            self.assertEqual(self.appel('GET', '/api/spaces')[0], 200)


class ConnexionTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()

    def test_refus_sans_dire_si_l_identifiant_existe(self):
        inconnu = self.appel('POST', '/api/connexion', {'identifiant': 'personne', 'mot_de_passe': 'nimporte-quoi-long'})
        mauvais = self.appel('POST', '/api/connexion', {'identifiant': 'admin', 'mot_de_passe': 'nimporte-quoi-long'})
        self.assertEqual(inconnu, mauvais)
        self.assertEqual(inconnu[0], 401)

    def test_cinq_echecs_bloquent_puis_la_reinitialisation_debloque(self):
        agent_id, _ = self.creer(self.admin, 'bob')
        for _ in range(comptes.ECHECS_MAX):
            self.appel('POST', '/api/connexion', {'identifiant': 'bob', 'mot_de_passe': 'mauvais-mot-de-passe'})
        statut, corps = self.appel('POST', '/api/connexion', {'identifiant': 'bob', 'mot_de_passe': MDP_AGENT})
        self.assertEqual(statut, 401)
        self.assertIn('bloqué', corps['error'])
        provisoire = self.appel('POST', f'/api/admin/utilisateurs/{agent_id}/reinitialiser', {}, self.admin)[1]['mot_de_passe']
        self.connexion('bob', provisoire)

    def test_mot_de_passe_provisoire_a_changer_avant_tout(self):
        corps = self.appel('POST', '/api/admin/utilisateurs', {'identifiant': 'bob', 'nom': 'Bob', 'role': 'agent'}, self.admin)[1]
        self.assertGreaterEqual(len(corps['mot_de_passe']), comptes.LONGUEUR_MIN)
        jeton = self.connexion('bob', corps['mot_de_passe'])
        statut, refus = self.appel('GET', '/api/spaces', jeton=jeton)
        self.assertEqual((statut, refus['code']), (403, 'changer_mot_de_passe'))
        self.assertTrue(self.appel('GET', '/api/session', jeton=jeton)[1]['utilisateur']['doit_changer'])
        self.assertEqual(self.appel('POST', '/api/compte/mot-de-passe', {'ancien': 'faux', 'nouveau': MDP_AGENT}, jeton)[0], 400)
        self.assertEqual(self.appel('POST', '/api/compte/mot-de-passe', {'ancien': corps['mot_de_passe'], 'nouveau': 'court'}, jeton)[0], 400)
        self.assertEqual(self.appel('POST', '/api/compte/mot-de-passe', {'ancien': corps['mot_de_passe'], 'nouveau': MDP_AGENT}, jeton)[0], 200)
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=jeton)[0], 200)

    def test_deconnexion_et_session_expiree(self):
        jeton = self.connexion('admin', MDP_ADMIN)
        self.assertEqual(self.appel('POST', '/api/deconnexion', {}, jeton)[0], 200)
        self.assertIn('Max-Age=0', dict(self.derniere.headers)['Set-Cookie'])
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=jeton)[0], 401)
        jeton = self.connexion('admin', MDP_ADMIN)
        plus_tard = datetime.now(timezone.utc) + comptes.INACTIVITE + timedelta(minutes=1)
        self.assertIsNone(comptes.session(server.DB, jeton, plus_tard))
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=jeton)[0], 401)

    def test_compte_desactive_perd_sa_session(self):
        agent_id, agent = self.creer(self.admin, 'bob')
        self.assertEqual(self.appel('POST', f'/api/admin/utilisateurs/{agent_id}', {'actif': False}, self.admin)[0], 200)
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=agent)[0], 401)
        self.assertEqual(self.appel('POST', '/api/connexion', {'identifiant': 'bob', 'mot_de_passe': MDP_AGENT})[0], 401)

    def test_il_reste_toujours_un_administrateur(self):
        admin_id = self.appel('GET', '/api/session', jeton=self.admin)[1]['utilisateur']['id']
        self.assertEqual(self.appel('POST', f'/api/admin/utilisateurs/{admin_id}', {'role': 'agent'}, self.admin)[0], 403)
        self.assertEqual(self.appel('POST', f'/api/admin/utilisateurs/{admin_id}', {'actif': False}, self.admin)[0], 403)
        self.creer(self.admin, 'second', 'administrateur')
        self.assertEqual(self.appel('POST', f'/api/admin/utilisateurs/{admin_id}', {'role': 'agent'}, self.admin)[0], 200)

    def test_identifiant_unique_et_valide(self):
        self.assertEqual(self.appel('POST', '/api/admin/utilisateurs', {'identifiant': 'Admin', 'nom': 'X', 'role': 'agent'}, self.admin)[0], 400)
        self.assertEqual(self.appel('POST', '/api/admin/utilisateurs', {'identifiant': 'a b', 'nom': 'X', 'role': 'agent'}, self.admin)[0], 400)
        self.assertEqual(self.appel('POST', '/api/admin/utilisateurs', {'identifiant': 'zoe', 'nom': 'X', 'role': 'chef'}, self.admin)[0], 400)

    def test_journal_attribue_les_actions_sans_identifiant_de_connexion(self):
        self.appel('POST', '/api/connexion', {'identifiant': 'identifiant-tape', 'mot_de_passe': 'nimporte-quoi-long'})
        lignes = self.appel('GET', '/api/admin/activite', jeton=self.admin)[1]
        self.assertEqual((lignes[0]['action'], lignes[0]['statut'], lignes[0]['utilisateur']), ('connexion_refusee', 'refuse', ''))
        self.assertNotIn('identifiant-tape', json.dumps(lignes))
        self.assertEqual(next(x for x in lignes if x['action'] == 'premier_compte_cree')['nom_utilisateur'], 'Alice Admin')


class RolesTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()

    def test_routes_d_administration_refusees_a_un_agent(self):
        _, agent = self.creer(self.admin, 'bob')
        for route in ROUTES_ADMIN:
            with self.subTest(route=route):
                self.assertEqual(self.appel('GET', route, jeton=agent)[0], 403)
        for route, donnees in (('/api/admin/regles', {'import_autorise': False}), ('/api/ia', {}), ('/api/identite', {'collectivite': 'X'}),
                               ('/api/admin/utilisateurs', {'identifiant': 'x', 'nom': 'x', 'role': 'administrateur'}),
                               ('/api/admin/assistants/synthese', {'actif': False}), ('/api/admin/services', {'nom': 'RH'}),
                               ('/api/identite/gabarit/supprimer', {})):
            with self.subTest(route=route):
                self.assertEqual(self.appel('POST', route, donnees, agent)[0], 403)
        self.assertEqual(self.appel('DELETE', '/api/admin/services/x', jeton=agent)[0], 403)

    def test_le_referent_gere_les_assistants_et_rien_d_autre(self):
        _, referent = self.creer(self.admin, 'rachel', 'referent')
        self.assertEqual(self.appel('GET', '/api/admin/assistants', jeton=referent)[0], 200)
        self.assertEqual(self.appel('POST', '/api/admin/assistants/synthese', {'actif': False}, referent)[0], 200)
        self.assertEqual(self.appel('GET', '/api/admin/utilisateurs', jeton=referent)[0], 403)
        self.assertEqual(self.televerser(referent, 'public')[0], 201)  # édition des fonds publics

    def test_le_lecteur_ne_cree_rien_et_reste_en_lecture(self):
        _, lecteur = self.creer(self.admin, 'lea', 'lecteur')
        self.assertEqual(self.appel('POST', '/api/spaces', {'title': 'Mon espace'}, lecteur)[0], 403)
        self.assertEqual(self.appel('POST', '/api/assistants', {'title': 'A', 'instructions': 'B'}, lecteur)[0], 403)
        lecteur_id = self.appel('GET', '/api/session', jeton=lecteur)[1]['utilisateur']['id']
        self.appel('POST', '/api/partages/espace/interne', {'cible': 'utilisateur', 'cible_id': lecteur_id, 'niveau': 'contribution'}, self.admin)
        self.assertEqual(next(x for x in self.appel('GET', '/api/spaces', jeton=lecteur)[1] if x['id'] == 'interne')['niveau'], 'lecture')
        self.assertEqual(self.televerser(lecteur, 'interne')[0], 403)


class IsolementTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()
        self.document = self.televerser(self.admin, 'interne')[1]['id']
        self.agent_id, self.agent = self.creer(self.admin, 'bob')

    def test_un_espace_non_partage_ne_laisse_rien_paraitre(self):
        a = self.agent
        self.assertNotIn('interne', [x['id'] for x in self.appel('GET', '/api/spaces', jeton=a)[1]])
        self.assertNotIn(self.document, [x['id'] for x in self.appel('GET', '/api/documents?space=all', jeton=a)[1]])
        self.assertEqual(self.appel('GET', '/api/documents?space=interne', jeton=a)[1], [])
        self.assertNotIn(self.document, [x['id'] for x in self.appel('GET', '/api/search?q=archives+municipales+mardi', jeton=a)[1]])
        self.assertEqual(self.appel('GET', '/api/search?q=archives+municipales+mardi&space=interne', jeton=a)[1], [])
        self.assertEqual(self.appel('GET', f'/api/documents/{self.document}/file', jeton=a)[0], 404)
        self.assertEqual(self.appel('DELETE', f'/api/documents/{self.document}', jeton=a)[0], 404)
        self.assertEqual(self.televerser(a, 'interne')[0], 404)
        with patch.object(server, 'call_n8n', return_value='Les extraits ne répondent pas à la question [1].') as modele:
            statut, reponse = self.appel('POST', '/api/chat', {'question': 'Quand ouvrent les archives municipales ?', 'space': 'all', 'mode': 'sources'}, a)
            self.assertEqual(self.appel('POST', '/api/chat', {'question': 'Archives ?', 'space': 'interne', 'mode': 'sources'}, a)[0], 400)
        # seuls les textes du fonds public lui parviennent, et eux seuls partent au modèle
        self.assertEqual((statut, {s['space'] for s in reponse['sources']}), (200, {'Fonds publics'}))
        self.assertNotIn('mardi', str(modele.call_args_list))
        # le propriétaire, lui, trouve son document
        self.assertEqual(self.appel('GET', '/api/search?q=archives+municipales+mardi', jeton=self.admin)[1][0]['id'], self.document)

    def test_partage_par_niveau_puis_retrait(self):
        a, cible = self.agent, {'cible': 'utilisateur', 'cible_id': self.agent_id}
        self.assertEqual(self.appel('POST', '/api/partages/espace/interne', cible | {'niveau': 'lecture'}, self.admin)[0], 200)
        self.assertEqual(self.appel('GET', '/api/search?q=archives+municipales+mardi', jeton=a)[1][0]['id'], self.document)
        self.assertEqual(self.appel('GET', f'/api/documents/{self.document}/file', jeton=a)[0], 200)
        self.assertEqual(self.televerser(a, 'interne')[0], 403)
        self.appel('POST', '/api/partages/espace/interne', cible | {'niveau': 'contribution'}, self.admin)
        self.assertEqual(self.televerser(a, 'interne')[0], 201)
        self.assertEqual(self.appel('DELETE', f'/api/documents/{self.document}', jeton=a)[0], 403)
        self.appel('POST', '/api/partages/espace/interne', cible | {'niveau': 'edition'}, self.admin)
        self.assertEqual(self.appel('DELETE', f'/api/documents/{self.document}', jeton=a)[0], 200)
        self.appel('POST', '/api/partages/espace/interne/retirer', cible, self.admin)
        self.assertNotIn('interne', [x['id'] for x in self.appel('GET', '/api/spaces', jeton=a)[1]])

    def test_partage_a_un_service_et_a_tout_le_monde(self):
        service = self.appel('POST', '/api/admin/services', {'nom': 'Urbanisme'}, self.admin)[1]['id']
        self.appel('POST', f'/api/admin/utilisateurs/{self.agent_id}', {'service_id': service}, self.admin)
        self.appel('POST', '/api/partages/espace/interne', {'cible': 'service', 'cible_id': service, 'niveau': 'lecture'}, self.admin)
        self.assertIn('interne', [x['id'] for x in self.appel('GET', '/api/spaces', jeton=self.agent)[1]])
        self.assertEqual(self.appel('DELETE', f'/api/admin/services/{service}', jeton=self.admin)[0], 200)
        self.assertNotIn('interne', [x['id'] for x in self.appel('GET', '/api/spaces', jeton=self.agent)[1]])
        self.appel('POST', '/api/partages/espace/interne', {'cible': 'tous', 'niveau': 'lecture'}, self.admin)
        self.assertIn('interne', [x['id'] for x in self.appel('GET', '/api/spaces', jeton=self.agent)[1]])

    def test_seul_le_proprietaire_partage(self):
        self.appel('POST', '/api/partages/espace/interne', {'cible': 'utilisateur', 'cible_id': self.agent_id, 'niveau': 'edition'}, self.admin)
        self.assertEqual(self.appel('POST', '/api/partages/espace/interne', {'cible': 'tous', 'niveau': 'lecture'}, self.agent)[0], 403)
        self.assertEqual(self.appel('POST', '/api/partages/espace/public', {'cible': 'tous', 'niveau': 'lecture'}, self.admin)[0], 403)
        self.assertEqual(self.appel('POST', '/api/partages/espace/interne', {'cible': 'tous', 'niveau': 'proprietaire'}, self.admin)[0], 400)

    def test_regle_de_partage_desactivee(self):
        espace = self.appel('POST', '/api/spaces', {'title': 'Projet'}, self.agent)[1]['id']
        self.appel('POST', '/api/admin/regles', {'partage_autorise': False}, self.admin)
        self.assertEqual(self.appel('POST', f'/api/partages/espace/{espace}', {'cible': 'tous', 'niveau': 'lecture'}, self.agent)[0], 403)
        self.assertEqual(self.appel('POST', '/api/partages/espace/interne', {'cible': 'tous', 'niveau': 'lecture'}, self.admin)[0], 200)

    def test_l_administrateur_ne_lit_pas_les_conversations_des_autres(self):
        with patch.object(server, 'call_n8n', return_value='Brouillon.'):
            conversation = self.appel('POST', '/api/chat', {'question': 'Note confidentielle', 'mode': 'libre'}, self.agent)[1]['conversation']
            self.assertEqual(self.appel('POST', '/api/chat', {'question': 'Suite', 'mode': 'libre', 'conversation': conversation}, self.admin)[0], 400)
        self.assertNotIn(conversation, [x['id'] for x in self.appel('GET', '/api/conversations', jeton=self.admin)[1]])
        self.assertEqual(self.appel('GET', f'/api/conversations/{conversation}', jeton=self.admin)[0], 404)
        self.assertEqual(self.appel('DELETE', f'/api/conversations/{conversation}', jeton=self.admin)[0], 404)
        self.assertEqual(self.appel('GET', f'/api/conversations/{conversation}', jeton=self.agent)[0], 200)

    def test_le_word_d_un_arrete_reste_a_son_auteur(self):
        projet = 'b' * 32
        (server.DATA / 'arretes').mkdir(exist_ok=True)
        (server.DATA / 'arretes' / f'{projet}.docx').write_bytes(b'word')
        server.save_chat('', 'Arrêté', 'Projet.', [], 'sources', 'all', 'arrete', {'kind': 'arrete', 'id': projet}, True, self.agent_id)
        self.assertEqual(self.appel('GET', f'/api/arretes/{projet}/file', jeton=self.admin)[0], 404)
        self.assertEqual(self.appel('GET', f'/api/arretes/{projet}/file', jeton=self.agent)[0], 200)

    def test_assistant_personnel_prive_puis_partage(self):
        assistant = self.appel('POST', '/api/assistants', {'title': 'Note RH', 'instructions': 'Rédige une note.'}, self.agent)[1]['id']
        _, autre = self.creer(self.admin, 'carla')
        self.assertNotIn(assistant, [x['id'] for x in self.appel('GET', '/api/assistants', jeton=autre)[1]])
        with patch.object(server, 'call_n8n', return_value='Brouillon.') as modele:
            self.assertEqual(self.appel('POST', '/api/chat', {'question': 'Note', 'mode': 'libre', 'assistant': assistant}, autre)[0], 400)
            modele.assert_not_called()
        self.assertEqual(self.appel('POST', f'/api/partages/assistant/{assistant}', {'cible': 'tous', 'niveau': 'lecture'}, self.agent)[0], 200)
        self.assertIn(assistant, [x['id'] for x in self.appel('GET', '/api/assistants', jeton=autre)[1]])
        self.assertTrue(next(x for x in self.appel('GET', '/api/assistants', jeton=self.agent)[1] if x['id'] == assistant)['mien'])

    def test_transfert_au_depart_d_un_agent(self):
        espace = self.appel('POST', '/api/spaces', {'title': 'Dossiers Bob'}, self.agent)[1]['id']
        repreneur_id, repreneur = self.creer(self.admin, 'carla')
        statut, bilan = self.appel('POST', f'/api/admin/utilisateurs/{self.agent_id}/transferer', {'vers': repreneur_id}, self.admin)
        self.assertEqual((statut, bilan['espaces']), (200, 1))
        self.assertEqual(next(x for x in self.appel('GET', '/api/spaces', jeton=repreneur)[1] if x['id'] == espace)['niveau'], 'proprietaire')


class SecoursTests(Base):
    def test_commande_de_secours_redonne_l_acces(self):
        self.premier_admin()
        for _ in range(comptes.ECHECS_MAX):
            self.appel('POST', '/api/connexion', {'identifiant': 'admin', 'mot_de_passe': 'mauvais-mot-de-passe'})
        entree = io.TextIOWrapper(io.BytesIO(b'nouveau-mot-de-passe-solide\n'))
        with patch.object(sys, 'stdin', entree), redirect_stderr(io.StringIO()):
            sortie = io.StringIO()
            with patch.object(sys, 'stdout', sortie):
                code = outils.main(['reinitialiser-acces', 'admin', '--base', str(server.DB)])
        self.assertEqual((code, sortie.getvalue().strip()), (0, 'ETAPE reinitialiser-acces : OK'))
        self.connexion('admin', 'nouveau-mot-de-passe-solide')

    def test_commande_de_secours_compte_inconnu(self):
        self.premier_admin()
        entree = io.TextIOWrapper(io.BytesIO(b'nouveau-mot-de-passe-solide\n'))
        sortie = io.StringIO()
        with patch.object(sys, 'stdin', entree), patch.object(sys, 'stdout', sortie):
            self.assertEqual(outils.main(['reinitialiser-acces', 'personne', '--base', str(server.DB)]), 1)
        self.assertEqual(sortie.getvalue().strip(), 'ETAPE reinitialiser-acces : ECHEC compte_introuvable')


class PremierAdministrateurCommandeTests(Base):
    """Démonstration : le premier administrateur se crée sur le serveur (outils premier-administrateur), jamais par le web."""
    def lancer(self, *args, mot_de_passe=MDP_ADMIN):
        entree = io.TextIOWrapper(io.BytesIO(mot_de_passe.encode() + b'\n'))
        sortie, erreurs = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', entree), patch.object(sys, 'stdout', sortie), redirect_stderr(erreurs):
            code = outils.main(['premier-administrateur', *args, '--base', str(server.DB)])
        self.assertNotIn(mot_de_passe, sortie.getvalue() + erreurs.getvalue())
        return code, sortie.getvalue().strip()

    def test_premier_administrateur_cree(self):
        self.assertEqual(self.lancer('admin', '--nom', 'Alice Admin'), (0, 'ETAPE premier-administrateur : OK'))
        jeton = self.connexion('admin', MDP_ADMIN)
        self.assertEqual(self.appel('GET', '/api/session', jeton=jeton)[1]['utilisateur']['role'], 'administrateur')

    def test_premier_administrateur_une_seule_fois(self):
        self.lancer('admin', '--nom', 'Alice Admin')
        self.assertEqual(self.lancer('autre', '--nom', 'Bob Intrus'), (1, 'ETAPE premier-administrateur : ECHEC premier_compte_existant'))

    def test_premier_administrateur_mot_de_passe_court(self):
        self.assertEqual(self.lancer('admin', '--nom', 'Alice Admin', mot_de_passe='k9z-4'),
                         (1, 'ETAPE premier-administrateur : ECHEC mot_de_passe_trop_court'))
        self.assertFalse(comptes.mode_equipe(server.DB))

    def test_premier_administrateur_identifiant_invalide(self):
        self.assertEqual(self.lancer('a b!', '--nom', 'Alice Admin'), (1, 'ETAPE premier-administrateur : ECHEC compte_invalide'))


class EmpreinteTests(unittest.TestCase):
    def test_empreinte_scrypt_salee(self):
        a, b = comptes.empreinte_mot_de_passe('un-mot-de-passe-long'), comptes.empreinte_mot_de_passe('un-mot-de-passe-long')
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith('scrypt$32768$8$1$'))
        self.assertTrue(comptes.verifier_mot_de_passe('un-mot-de-passe-long', a))
        self.assertFalse(comptes.verifier_mot_de_passe('autre-mot-de-passe', a))
        self.assertFalse(comptes.verifier_mot_de_passe('x', 'pas-une-empreinte'))


if __name__ == '__main__':
    unittest.main()


class CompteTechniqueTests(Base):
    NOM_BASE = 'territoire.sqlite3'  # là où `outils verifier` lit la base (REGLAGES.donnees)

    def test_en_mode_un_poste_aucun_compte_n_est_cree(self):
        import e2e
        with patch.object(config, 'REGLAGES', replace(config.REGLAGES, donnees=server.DATA)), \
                patch.object(e2e, 'executer', return_value=[]) as parcours, patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(outils.main(['verifier']), 0)
        parcours.assert_called_once_with(strict=False, jeton='')
        self.assertFalse(comptes.mode_equipe(server.DB))

    def test_en_mode_equipe_un_compte_technique_le_temps_du_parcours(self):
        self.premier_admin()
        vues = []
        import e2e

        def parcours(strict, jeton):
            vues.append(comptes.session(server.DB, jeton))
            return []
        with patch.object(config, 'REGLAGES', replace(config.REGLAGES, donnees=server.DATA)), \
                patch.object(e2e, 'executer', side_effect=parcours), patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(outils.main(['verifier']), 0)
        self.assertEqual((vues[0]['id'], vues[0]['role']), (comptes.TECHNIQUE, 'administrateur'))
        self.assertIsNone(comptes.utilisateur(server.DB, comptes.TECHNIQUE))  # supprimé après le parcours
        self.assertTrue(comptes.mode_equipe(server.DB))
        # compte supprimé même quand un contrôle échoue
        with patch.object(config, 'REGLAGES', replace(config.REGLAGES, donnees=server.DATA)), \
                patch.object(e2e, 'executer', side_effect=AssertionError('espace')), patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(outils.main(['verifier']), 1)
        self.assertIsNone(comptes.utilisateur(server.DB, comptes.TECHNIQUE))


class ProjetEtAssistantsPrivesTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()
        self.agent_id, self.agent = self.creer(self.admin, 'bob')

    def test_un_identifiant_de_projet_tape_dans_une_question_ne_donne_pas_le_word(self):
        projet = 'c' * 32
        (server.DATA / 'arretes').mkdir(exist_ok=True)
        (server.DATA / 'arretes' / f'{projet}.docx').write_bytes(b'word')
        admin_id = self.appel('GET', '/api/session', jeton=self.admin)[1]['utilisateur']['id']
        server.save_chat('', 'Arrêté', 'Projet.', [], 'sources', 'all', 'arrete', {'kind': 'arrete', 'id': projet}, True, admin_id)
        piege = {'kind': 'arrete_clarification', 'inputs': {'contexte': f'texte "{projet}"'}}
        server.save_chat('', f'Question "{projet}', 'Précision ?', [], 'sources', 'all', 'arrete', piege, True, self.agent_id)
        self.assertEqual(self.appel('GET', f'/api/arretes/{projet}/file', jeton=self.agent)[0], 404)
        self.assertEqual(self.appel('GET', f'/api/arretes/{projet}/file', jeton=self.admin)[0], 200)

    def test_l_administration_ne_voit_pas_les_assistants_personnels_des_agents(self):
        assistant = self.appel('POST', '/api/assistants', {'title': 'Note privée', 'instructions': 'Consigne privée.'}, self.agent)[1]['id']
        liste = self.appel('GET', '/api/admin/assistants', jeton=self.admin)[1]
        self.assertNotIn(assistant, [a['id'] for a in liste])
        self.assertNotIn('Consigne privée', json.dumps(liste, ensure_ascii=False))
        self.assertEqual(self.appel('POST', f'/api/admin/assistants/{assistant}', {'actif': False}, self.admin)[0], 404)
        self.assertEqual(self.appel('DELETE', f'/api/admin/assistants/{assistant}', jeton=self.admin)[0], 404)
        self.assertIn('synthese', [a['id'] for a in liste])
