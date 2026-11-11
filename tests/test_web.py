# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import hashlib
import http.server
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from wsgiref.util import setup_testing_defaults

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / 'app'))
import config
import llm_backend
import server
import web
import journal
import serve

ORIGINE = 'http://127.0.0.1:9001'
NAVIGATEUR = {'Host': '127.0.0.1:9001', 'Origin': ORIGINE, 'Content-Type': 'application/json'}
IA_LIEN_LOCAL = config.charger({'TERRITORIA_LLM_URL': 'http://169.254.169.254/v1'})


def lignes_journal(texte):
    return [json.loads(ligne) for ligne in texte.splitlines()]


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        donnees = Path(self.tmp.name)
        for cible, nom, valeur in [
            (web, 'REGLAGES', replace(config.REGLAGES, origine=ORIGINE, service_token='jeton-test')),
            (server, 'DATA', donnees), (server, 'FILES', donnees / 'documents'), (server, 'DB', donnees / 'db.sqlite'),
        ]:
            correctif = patch.object(cible, nom, valeur)
            correctif.start()
            self.addCleanup(correctif.stop)
        server.init()
        self.err = io.StringIO()
        redirection = redirect_stderr(self.err)
        redirection.__enter__()
        self.addCleanup(redirection.__exit__, None, None, None)

    def corps(self, reponse):
        return json.loads(reponse.body)

    def entetes(self, reponse):
        return {cle.lower(): valeur for cle, valeur in reponse.headers}

    def wsgi(self, methode, chemin, corps=b'', **environ):
        env = {}
        setup_testing_defaults(env)
        env.update(REQUEST_METHOD=methode, PATH_INFO=chemin, HTTP_HOST='127.0.0.1:9001', HTTP_ORIGIN=ORIGINE,
                   CONTENT_TYPE='application/json', **{'wsgi.input': io.BytesIO(corps)})
        if corps:
            env['CONTENT_LENGTH'] = str(len(corps))
        env.update(environ)
        recu = {}

        def start_response(statut, entetes):
            recu['statut'], recu['entetes'] = statut, entetes

        brut = b''.join(web.application(env, start_response))
        return recu['statut'], dict(recu['entetes']), brut

    # Règles d'accès (brief)
    def test_healthz_sans_controle(self):
        self.assertEqual(web.handle('GET', '/healthz', {}, b'').status, 200)

    def test_chemin_prive_sans_jeton_refuse(self):
        self.assertEqual(web.handle('POST', '/api/verify', {'Host': '127.0.0.1:9001', 'Origin': 'http://127.0.0.1:9001'}, b'{}').status, 403)

    def test_chemin_prive_avec_jeton_accepte_sans_host(self):
        r = web.handle('POST', '/api/verify', {'Host': 'territoria:8878', 'X-Territoria-Service-Token': 'jeton-test'}, b'{}')
        self.assertEqual(r.status, 400)  # accepté par le contrôle d'accès, refusé par la validation métier

    def test_origine_suit_le_port_publie(self):
        h = {'Host': '127.0.0.1:9001', 'Origin': 'http://127.0.0.1:9001', 'Content-Type': 'application/json'}
        self.assertEqual(web.handle('POST', '/api/spaces', h, b'{"title":"Essai"}').status, 201)

    def test_origine_tierce_refusee(self):
        self.assertEqual(web.handle('POST', '/api/spaces', {'Host': '127.0.0.1:9001', 'Origin': 'https://exemple.invalid'}, b'{"title":"x"}').status, 403)

    def test_journal_sans_contenu(self):
        with patch.object(server, 'call_n8n', return_value='Probité [1].'), redirect_stderr(io.StringIO()) as err:
            web.handle('POST', '/api/chat', NAVIGATEUR, json.dumps({'question': 'Question secrète probité dignité', 'space': 'public'}).encode())
        self.assertNotIn('secrète', err.getvalue())
        for ligne in err.getvalue().splitlines(): self.assertLessEqual(set(json.loads(ligne)), {'t', 'etape', 'statut', 'duree_ms', 'code'})

    def test_wsgi_relaie_statut_et_corps(self):
        env = {}
        setup_testing_defaults(env)
        env.update(REQUEST_METHOD='GET', PATH_INFO='/healthz')
        recu = []
        corps = b''.join(web.application(env, lambda statut, entetes: recu.append((statut, entetes))))
        self.assertEqual(recu[0][0], '200 OK')
        self.assertEqual(json.loads(corps), {'ready': True})
        self.assertIn(('Content-Length', str(len(corps))), recu[0][1])

    # Jeton de service
    def test_chemin_prive_refuse_quand_aucun_jeton_configure(self):
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, service_token='')):
            for jeton in ('', 'jeton-test'):
                with self.subTest(jeton=jeton):
                    self.assertEqual(web.handle('POST', '/api/verify', {'X-Territoria-Service-Token': jeton}, b'{}').status, 403)

    def test_chemin_prive_mauvais_jeton_refuse(self):
        for jeton in ('jeton-tes', 'jeton-teste', 'jéton-test'):
            with self.subTest(jeton=jeton):
                r = web.handle('POST', '/api/llm/chat', {'X-Territoria-Service-Token': jeton}, b'{}')
                self.assertEqual((r.status, self.corps(r)), (403, {'error': 'Accès refusé.'}))

    def test_tous_les_chemins_prives_exigent_le_jeton(self):
        self.assertEqual(web.PRIVEES, frozenset({'/api/verify', '/api/llm/chat', '/api/arretes/analyser',
                                                 '/api/arretes/sources', '/api/arretes/preparer', '/api/arretes/finaliser'}))
        for chemin in web.PRIVEES:
            with self.subTest(chemin=chemin):
                self.assertEqual(web.handle('POST', chemin, NAVIGATEUR, b'{}').status, 403)

    def test_noms_d_entetes_sans_casse(self):
        self.assertEqual(web.handle('POST', '/api/spaces', {'host': '127.0.0.1:9001', 'ORIGIN': ORIGINE, 'CONTENT-TYPE': 'application/json'}, b'{"title":"Essai"}').status, 201)
        self.assertEqual(web.handle('POST', '/api/verify', {'x-territoria-service-token': 'jeton-test'}, b'{}').status, 400)

    # Host et Origin
    def test_hote_inconnu_refuse(self):
        for hote in ('exemple.invalid:9001', '127.0.0.1:8878', '127.0.0.1', ''):
            with self.subTest(hote=hote):
                r = web.handle('GET', '/api/spaces', {'Host': hote}, b'')
                self.assertEqual((r.status, self.corps(r)), (403, {'error': 'Accès refusé.'}))

    def test_variante_localhost_acceptee(self):
        self.assertEqual(web.handle('GET', '/api/spaces', {'Host': 'localhost:9001'}, b'').status, 200)
        h = {'Host': 'localhost:9001', 'Origin': 'http://localhost:9001', 'Content-Type': 'application/json'}
        self.assertEqual(web.handle('POST', '/api/spaces', h, b'{"title":"Essai"}').status, 201)

    def test_lecture_sans_origin_acceptee_ecriture_refusee(self):
        self.assertEqual(web.handle('GET', '/api/spaces', {'Host': '127.0.0.1:9001'}, b'').status, 200)
        self.assertEqual(web.handle('POST', '/api/spaces', {'Host': '127.0.0.1:9001'}, b'{"title":"x"}').status, 403)

    def test_suppression_controle_l_origine(self):
        self.assertEqual(web.handle('DELETE', '/api/spaces', {'Host': '127.0.0.1:9001'}, b'').status, 403)

    def test_healthz_en_lecture_seulement(self):
        self.assertEqual(web.handle('POST', '/healthz', {}, b'{}').status, 403)
        self.assertEqual(web.handle('POST', '/healthz', NAVIGATEUR, b'{}').status, 404)

    # Routes reprises
    def test_routes_llm_v1_supprimees(self):
        self.assertEqual(web.handle('GET', '/api/llm/v1/models', NAVIGATEUR, b'').status, 404)
        self.assertEqual(web.handle('POST', '/api/llm/v1/chat/completions', NAVIGATEUR, b'{"messages":[]}').status, 404)

    def test_entetes_de_securite(self):
        r = web.handle('GET', '/api/spaces', NAVIGATEUR, b'')
        h = self.entetes(r)
        self.assertEqual(h['content-type'], 'application/json; charset=utf-8')
        self.assertEqual((h['x-content-type-options'], h['cache-control']), ('nosniff', 'no-store'))
        self.assertIn("default-src 'self'", h['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", h['content-security-policy'])
        self.assertEqual(h['content-length'], str(len(r.body)))
        self.assertEqual({x['id'] for x in self.corps(r)}, {'public', 'interne'})

    def test_page_d_accueil_et_traversee(self):
        r = web.handle('GET', '/', NAVIGATEUR, b'')
        self.assertEqual((r.status, self.entetes(r)['content-type']), (200, 'text/html; charset=utf-8'))
        self.assertEqual(web.handle('GET', '/../server.py', NAVIGATEUR, b'').status, 404)

    def test_document_original_et_integrite(self):
        r = web.handle('GET', '/api/documents/cgfp-l121-1/file', NAVIGATEUR, b'')
        self.assertEqual((r.status, self.entetes(r)['content-disposition']), (200, 'attachment'))
        (server.FILES / 'cgfp-l121-1.txt').write_text('Texte modifié.', encoding='utf-8')
        self.assertEqual(web.handle('GET', '/api/documents/cgfp-l121-1/file', NAVIGATEUR, b'').status, 409)

    def test_modele_de_reference_lu_dans_le_manifeste(self):
        modeles = server.DATA / 'modeles'
        modeles.mkdir()
        contenu = b'%PDF-reference-fictive'
        (modeles / 'reference-test.pdf').write_bytes(contenu)
        manifeste = {'reference': 'reference-test.pdf', 'sha256': hashlib.sha256(contenu).hexdigest(), 'trame': 'trame-test'}
        (modeles / 'manifest.json').write_text(json.dumps(manifeste), encoding='utf-8')
        r = web.handle('GET', '/api/arretes/modele', NAVIGATEUR, b'')
        self.assertEqual((r.status, r.body, self.entetes(r)['content-type']), (200, contenu, 'application/pdf'))
        (modeles / 'manifest.json').unlink()
        self.assertEqual(web.handle('GET', '/api/arretes/modele', NAVIGATEUR, b'').status, 500)

    def test_limites_de_taille(self):
        self.assertEqual(web.handle('POST', '/api/spaces', NAVIGATEUR, b'').status, 413)
        r = web.handle('POST', '/api/spaces', NAVIGATEUR, b' ' * 200001)
        self.assertEqual((r.status, self.corps(r)), (413, {'error': 'Requête trop volumineuse.'}))

    def test_televersement_dans_un_espace(self):
        r = web.handle('POST', '/api/upload/interne?name=note.txt', NAVIGATEUR, 'Les archives ouvrent le mardi.'.encode())
        self.assertEqual(r.status, 201)
        self.assertEqual((self.corps(r)['pages_ocr'], self.corps(r)['pages_sans_texte']), (0, 0))  # annoncés à l'agent
        self.assertTrue(server.search('archives mardi', 'interne'))

    def test_erreur_du_workflow_en_502(self):
        with patch.object(server, 'call_n8n', side_effect=RuntimeError('Le workflow ne répond pas.')):
            r = web.handle('POST', '/api/chat', NAVIGATEUR, json.dumps({'question': 'probité dignité', 'space': 'public'}).encode())
        self.assertEqual((r.status, self.corps(r)), (502, {'error': 'Le workflow ne répond pas.'}))

    # Réglages IA invalides
    def test_statut_signale_des_reglages_ia_invalides(self):
        with patch.object(llm_backend, 'REGLAGES', IA_LIEN_LOCAL), patch.object(server, 'ouvrir_sans_proxy', side_effect=URLError('hors ligne')):
            r = web.handle('GET', '/api/status', NAVIGATEUR, b'')
        corps = self.corps(r)
        self.assertEqual((r.status, corps['lm'], corps['n8n']), (200, False, False))
        self.assertIn('TERRITORIA_LLM_URL', corps['erreur'])
        self.assertNotIn('hosted', corps)

    def test_reglages_ia_invalides_en_400(self):
        with patch.object(llm_backend, 'REGLAGES', IA_LIEN_LOCAL):
            r = web.handle('GET', '/api/ia', NAVIGATEUR, b'')
        self.assertEqual(r.status, 400)
        self.assertIn('TERRITORIA_LLM_URL', self.corps(r)['error'])

    # Journal
    def test_journal_une_ligne_par_requete_avec_route_normalisee(self):
        ident = 'a' * 32
        web.handle('GET', f'/api/conversations/{ident}', NAVIGATEUR, b'')
        web.handle('GET', '/api/search?q=confidentiel&space=all', NAVIGATEUR, b'')
        web.handle('GET', '/api/identite/logo/logo-secret.png', NAVIGATEUR, b'')
        web.handle('GET', '/page-confidentielle', NAVIGATEUR, b'')
        lignes = lignes_journal(self.err.getvalue())
        self.assertEqual([(x['etape'], x['statut']) for x in lignes],
                         [('get:/api/conversations/:id', '404'), ('get:/api/search', '200'),
                          ('get:/api/identite/logo/:id', '500'), ('get:/static', '404')])
        for ligne in lignes:
            self.assertEqual(set(ligne), {'t', 'etape', 'statut', 'duree_ms', 'code'})
            self.assertIsInstance(ligne['duree_ms'], int)
        for fragment in (ident, 'confidentiel', 'secret'):
            self.assertNotIn(fragment, self.err.getvalue())

    def test_journal_code_d_erreur_sans_message(self):
        web.handle('POST', '/api/spaces', NAVIGATEUR, b'{"title":""}')
        web.handle('POST', '/api/arretes/generer', NAVIGATEUR, b'{"titre": "Arr\xc3\xaat\xc3\xa9 confidentiel"')
        lignes = lignes_journal(self.err.getvalue())
        self.assertEqual([(x['etape'], x['statut'], x['code']) for x in lignes],
                         [('post:/api/spaces', '400', 'valueerror'), ('post:/api/arretes/generer', '400', 'jsondecodeerror')])
        self.assertNotIn('Indiquez', self.err.getvalue())
        self.assertNotIn('confidentiel', self.err.getvalue())

    def test_journal_remplace_les_valeurs_hors_motif(self):
        with redirect_stderr(io.StringIO()) as err:
            journal.evenement('get:/api/spaces', '200', 12)
            journal.evenement('Question secrète', '200', code='abc\n')
            journal.evenement('a' * 61, 'OK', duree_ms=None, code='valueerror')
        lignes = lignes_journal(err.getvalue())
        self.assertEqual([(x['etape'], x['statut'], x['duree_ms'], x['code']) for x in lignes],
                         [('get:/api/spaces', '200', 12, None), ('invalide', '200', None, 'invalide'),
                          ('invalide', 'invalide', None, 'valueerror')])
        for ligne in lignes:
            self.assertEqual(list(ligne), ['t', 'etape', 'statut', 'duree_ms', 'code'])
            self.assertRegex(ligne['t'], r'^[a-z0-9_.:/-]{1,60}$')

    # WSGI
    def test_wsgi_transmet_les_entetes_et_le_corps(self):
        statut, _, brut = self.wsgi('POST', '/api/verify', b'{}', HTTP_X_TERRITORIA_SERVICE_TOKEN='jeton-test')
        self.assertEqual((statut, json.loads(brut)['error']), ('400 Bad Request', 'Données de vérification invalides.'))
        statut, _, brut = self.wsgi('POST', '/api/spaces', b'{"title":"Essai"}')
        self.assertEqual(statut, '201 Created')
        statut, _, brut = self.wsgi('GET', '/api/search', QUERY_STRING='q=probit%C3%A9+dignit%C3%A9&space=public')
        self.assertEqual((statut, json.loads(brut)[0]['id']), ('200 OK', 'cgfp-l121-1'))

    def test_wsgi_longueur_invalide(self):
        for longueur in ('abc', '-1', '1.5', '٣'):
            with self.subTest(longueur=longueur):
                statut, _, brut = self.wsgi('POST', '/api/spaces', CONTENT_LENGTH=longueur)
                self.assertEqual(statut, '400 Bad Request')
                self.assertEqual(json.loads(brut), {'error': 'Requête invalide.'})

    def test_wsgi_longueur_excessive_refusee_sans_lecture(self):
        class EntreeInterdite:
            def read(self, *args):
                raise AssertionError('le corps ne doit pas être lu')

        statut, _, brut = self.wsgi('POST', '/api/upload/interne', CONTENT_LENGTH=str(server.MAX_UPLOAD + 1),
                                    **{'wsgi.input': EntreeInterdite()})
        self.assertTrue(statut.startswith('413 '))
        self.assertEqual(json.loads(brut), {'error': 'Fichier trop volumineux ou requête vide (maximum 20 Mo).'})

    def test_wsgi_longueur_absente_ou_vide(self):
        for environ in ({}, {'CONTENT_LENGTH': ''}):
            with self.subTest(environ=environ):
                self.assertEqual(self.wsgi('GET', '/api/spaces', **environ)[0], '200 OK')
                self.assertTrue(self.wsgi('POST', '/api/spaces', **environ)[0].startswith('413 '))

    def test_wsgi_erreur_inattendue_en_500(self):
        with patch.object(web, 'handle', side_effect=KeyError('x')):
            statut, entetes, brut = self.wsgi('GET', '/api/spaces')
        self.assertEqual((statut, json.loads(brut)), ('500 Internal Server Error', {'error': 'Erreur locale pendant le traitement.'}))
        self.assertEqual(entetes['Cache-Control'], 'no-store')
        self.assertEqual(lignes_journal(self.err.getvalue())[-1]['code'], 'keyerror')


class AppelsN8nTests(unittest.TestCase):
    def test_appels_n8n_portent_le_jeton_webhook_et_le_delai(self):
        envois = []

        class Reponse(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False

        def faux_urlopen(requete, timeout):
            envois.append((requete.get_header('X-territoria-webhook-token'), timeout))
            if requete.full_url == server.N8N:
                return Reponse(json.dumps({'choices': [{'message': {'content': 'Réponse [1].'}}]}).encode())
            return Reponse(json.dumps({'id': 'b' * 32}).encode())

        reglages = replace(config.REGLAGES, webhook_token='jeton-webhook', delai_n8n=42)
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'REGLAGES', reglages), \
                patch.object(server, 'DATA', Path(tmp)), patch.object(server, 'ouvrir_sans_proxy', faux_urlopen), \
                patch.object(server.arretes, 'verify_reference'), patch.object(server.arretes, 'clean_input', return_value={}):
            self.assertEqual(server.call_n8n([{'role': 'user', 'content': 'q'}], 'libre', []), 'Réponse [1].')
            self.assertEqual(server.draft_arrete({})['id'], 'b' * 32)
        self.assertEqual(envois, [('jeton-webhook', 42), ('jeton-webhook', 42)])


class _Cible(http.server.BaseHTTPRequestHandler):
    """Faux n8n et faux serveur IA : chaque chemin reçu est noté, la réponse JSON dépend du chemin."""
    recus = []

    def _repondre(self):
        type(self).recus.append(self.path)
        longueur = int(self.headers.get('Content-Length') or 0)
        if longueur:
            self.rfile.read(longueur)
        corps = {'/conversation': {'choices': [{'message': {'content': 'Réponse [1].'}}]},
                 '/arrete': {'id': 'c' * 32}, '/healthz': {'status': 'ok'},
                 '/v1/models': {'data': [{'id': 'modele-local'}]}}.get(self.path, {})
        donnees = json.dumps(corps).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    do_GET = do_POST = _repondre

    def log_message(self, *args):
        pass


class _Proxy(http.server.BaseHTTPRequestHandler):
    """Proxy de la collectivité : il ne doit jamais rien recevoir. Il note la requête et répond 502."""
    recus = []

    def _refuser(self):
        type(self).recus.append(self.requestline)
        self.send_error(502)

    do_GET = do_POST = do_CONNECT = _refuser

    def log_message(self, *args):
        pass


class SansProxyTests(unittest.TestCase):
    """Les appels internes (n8n, serveur IA, état de n8n) transportent la question, les extraits et le prompt : ils ne
    passent jamais par un proxy, même quand http_proxy est défini (Podman copie par défaut les variables de proxy de la
    machine dans les conteneurs, Docker celles de sa configuration). Un faux proxy, défini comme proxy HTTP, ne doit
    rien recevoir ; la fausse cible, joignable en direct, reçoit chaque requête."""

    @classmethod
    def setUpClass(cls):
        cls.serveurs = []
        for classe in (_Cible, _Proxy):
            serveur = http.server.ThreadingHTTPServer(('127.0.0.1', 0), classe)
            threading.Thread(target=serveur.serve_forever, daemon=True).start()
            cls.serveurs.append(serveur)
        cls.cible = 'http://127.0.0.1:%d' % cls.serveurs[0].server_address[1]
        cls.proxy = 'http://127.0.0.1:%d' % cls.serveurs[1].server_address[1]

    @classmethod
    def tearDownClass(cls):
        for serveur in cls.serveurs:
            serveur.shutdown()
            serveur.server_close()

    def setUp(self):
        _Cible.recus.clear()
        _Proxy.recus.clear()
        environ = {k: v for k, v in os.environ.items() if k.lower() not in ('no_proxy', 'http_proxy', 'https_proxy', 'all_proxy')}
        environ.update(http_proxy=self.proxy, HTTP_PROXY=self.proxy, https_proxy=self.proxy, HTTPS_PROXY=self.proxy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        donnees = Path(self.tmp.name)
        (donnees / 'configuration').mkdir()
        (donnees / 'configuration' / 'ia.json').write_text(json.dumps(
            {'provider': 'lmstudio', 'base_url': self.cible + '/v1', 'model': 'modele-local', 'api_key': ''}))
        reglages = replace(config.REGLAGES, webhook_token='jeton-webhook', delai_n8n=10)
        for correctif in (patch.dict(os.environ, environ, clear=True),
                          # Opérateur global de urllib remis à zéro : un appel par urlopen relirait les variables de proxy.
                          patch.object(urllib.request, '_opener', None),
                          patch.object(server, 'REGLAGES', reglages), patch.object(server, 'DATA', donnees),
                          patch.object(server, 'N8N', self.cible + '/conversation'),
                          patch.object(server, 'N8N_ARRETE', self.cible + '/arrete'),
                          patch.object(server, 'N8N_STATUS', self.cible + '/healthz')):
            correctif.start()
            self.addCleanup(correctif.stop)

    def verifier_direct(self, chemins):
        self.assertEqual(_Proxy.recus, [], 'une requête interne est passée par le proxy')
        self.assertEqual(_Cible.recus, chemins)

    def test_le_proxy_est_bien_celui_de_l_environnement(self):
        # Garde-fou : sans le contournement, urllib enverrait ces requêtes au faux proxy (sinon ce test ne prouve rien).
        self.assertEqual(urllib.request.getproxies().get('http'), self.proxy)
        self.assertFalse(urllib.request.proxy_bypass('127.0.0.1'))

    def test_webhook_de_conversation_sans_proxy(self):
        self.assertEqual(server.call_n8n([{'role': 'user', 'content': 'question'}], 'libre', []), 'Réponse [1].')
        self.verifier_direct(['/conversation'])

    def test_webhook_des_arretes_sans_proxy(self):
        with patch.object(server.arretes, 'verify_reference'), patch.object(server.arretes, 'clean_input', return_value={}):
            self.assertEqual(server.draft_arrete({})['id'], 'c' * 32)
        self.verifier_direct(['/arrete'])

    def test_serveur_ia_sans_proxy(self):
        config_ia = {'base_url': self.cible + '/v1', 'api_key': ''}
        self.assertEqual(llm_backend._request(config_ia, '/models', timeout=10)['data'][0]['id'], 'modele-local')
        self.verifier_direct(['/v1/models'])

    def test_etat_de_n8n_et_de_l_ia_sans_proxy(self):
        etats = web._statut()
        self.assertEqual((etats['n8n'], etats['lm']), (True, True))
        self.verifier_direct(['/healthz', '/v1/models'])


class DemarrageTests(unittest.TestCase):
    def test_url_ia_link_local_arrete_le_demarrage(self):
        with patch.object(llm_backend, 'REGLAGES', IA_LIEN_LOCAL), patch('waitress.serve') as servir, \
                patch.object(server, 'init') as initialiser:
            with self.assertRaises(SystemExit) as erreur:
                serve.main()
        self.assertIn('TERRITORIA_LLM_URL', str(erreur.exception.code))
        servir.assert_not_called()
        initialiser.assert_not_called()

    def environnement(self, donnees, **variables):
        env = {k: v for k, v in os.environ.items() if not k.startswith('TERRITORIA_')}
        env.update(PYTHONPATH=str(RACINE / 'app'), TERRITORIA_DONNEES=donnees, **variables)
        return env

    def test_reglage_invalide_message_francais_sans_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            fini = subprocess.run([sys.executable, '-m', 'serve'], cwd=tmp, env=self.environnement(tmp, TERRITORIA_PORT='http'),
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(fini.returncode, 1)
        self.assertIn('TERRITORIA_PORT doit être un nombre entier.', fini.stderr)
        self.assertNotIn('Traceback', fini.stderr)

    def test_waitress_borne_le_corps_a_la_limite_de_l_application(self):
        with patch('waitress.serve') as servir, patch.object(server, 'init'), patch.object(serve.os, 'umask'), \
                patch('territoria.semantique.activer'), patch('territoria.semantique.lancer'), redirect_stdout(io.StringIO()):
            serve.main()  # sans indexation en arrière-plan : elle tournerait sur les bases des tests suivants
        servir.assert_called_once()
        self.assertIs(servir.call_args.args[0], web.application)
        # waitress refuse dès que Content-Length >= max_request_body_size : + 1 garde les 20 Mo exacts acceptés.
        self.assertEqual(servir.call_args.kwargs, {'host': config.REGLAGES.ecoute, 'port': config.REGLAGES.port, 'threads': 8,
                                                   'max_request_body_size': server.MAX_UPLOAD + 1})

    def premiere_ligne(self, port, longueur, corps):
        with socket.create_connection(('127.0.0.1', port), timeout=30) as connexion:
            connexion.sendall(f'POST /api/upload/interne HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                              f'Content-Length: {longueur}\r\n\r\n'.encode())
            if corps:
                connexion.sendall(b'x' * longueur)
            recu = b''
            while b'\r\n' not in recu:
                bloc = connexion.recv(4096)
                if not bloc:
                    break
                recu += bloc
        return recu.split(b'\r\n', 1)[0].decode()

    def test_corps_trop_grand_refuse_par_waitress_avant_l_application(self):
        with socket.socket() as libre:
            libre.bind(('127.0.0.1', 0))
            port = libre.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            processus = subprocess.Popen([sys.executable, '-m', 'serve'], cwd=tmp, env=self.environnement(tmp, TERRITORIA_PORT=str(port)),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(150):
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=1):
                            break
                    except OSError:
                        time.sleep(0.1)
                trop_grand = self.premiere_ligne(port, server.MAX_UPLOAD + 1, corps=False)
                limite_exacte = self.premiere_ligne(port, server.MAX_UPLOAD, corps=True)
            finally:
                processus.terminate()
                _, err = processus.communicate(timeout=30)
        self.assertTrue(trop_grand.startswith('HTTP/1.1 413 '), trop_grand)
        self.assertEqual(limite_exacte, 'HTTP/1.1 403 Forbidden')  # reçu en entier, puis refusé faute d'Origin
        # Le journal prouve que l'application n'a vu que la seconde requête.
        self.assertEqual([(x['etape'], x['statut']) for x in lignes_journal(err)], [('post:/api/upload/:id', '403')])


if __name__ == '__main__':
    unittest.main()
