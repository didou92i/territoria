# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import socket
import sys
import unittest
from dataclasses import replace
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import config
import server
import web
from territoria import connecteurs as cx
from tests.test_comptes import Base

JEU = {'id': 'abcdef0123456789', 'title': 'Délibérations 2025', 'page': 'https://www.data.gouv.fr/datasets/deliberations-2025',
       'organization': {'name': 'Commune de Testville'}, 'last_update': '2026-01-05T10:00:00', 'license': 'lov2',
       'resources': [{'id': 'ressource-0001', 'title': 'Liste des délibérations', 'format': 'csv', 'filesize': 2048, 'url': 'https://exemple.fr/liste.csv'},
                     {'id': 'ressource-0002', 'title': 'Carte', 'format': 'shp', 'url': 'https://exemple.fr/carte.zip'}]}


def adresses(*ips):
    return lambda hote, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port)) for ip in ips]


class ReglagesTests(Base):
    def test_desactives_par_defaut_puis_actives_et_coupes_hors_ligne(self):
        self.assertFalse(cx.etat(server.DATA, False)['datagouv']['actif'])
        cx.enregistrer(server.DATA, {'datagouv': True})
        self.assertTrue(cx.etat(server.DATA, False)['datagouv']['actif'])
        self.assertFalse(cx.etat(server.DATA, True)['datagouv']['actif'])
        with self.assertRaises(cx.ConnecteurIndisponible):
            cx.exiger(server.DATA, True, 'datagouv')

    def test_le_secret_piste_n_est_jamais_rendu(self):
        cx.enregistrer(server.DATA, {'legifrance': {'client_id': 'mon-identifiant', 'client_secret': 'mon-secret-tres-long'}})
        etat = cx.etat(server.DATA, False)['legifrance']
        self.assertTrue(etat['identifiants'])
        self.assertNotIn('mon-secret-tres-long', str(etat))
        cx.enregistrer(server.DATA, {'legifrance': {'client_id': 'autre-identifiant', 'client_secret': ''}})  # vide : gardé
        self.assertEqual(cx.lire(server.DATA)['legifrance']['client_secret'], 'mon-secret-tres-long')
        cx.enregistrer(server.DATA, {'legifrance': {'effacer': True}})
        self.assertFalse(cx.etat(server.DATA, False)['legifrance']['identifiants'])
        self.assertEqual((cx._fichier(server.DATA).stat().st_mode & 0o777), 0o600)

    def test_reglages_reserves_a_l_administration(self):
        admin = self.premier_admin()
        _, agent = self.creer(admin, 'bob')
        self.assertEqual(self.appel('GET', '/api/admin/connecteurs', jeton=agent)[0], 403)
        self.assertEqual(self.appel('POST', '/api/admin/connecteurs', {'datagouv': True}, agent)[0], 403)
        self.assertEqual(self.appel('POST', '/api/admin/connecteurs', {'datagouv': True}, admin)[0], 200)


class AdressesTests(unittest.TestCase):
    def test_adresses_internes_refusees(self):
        for ip in ('127.0.0.1', '10.0.0.5', '192.168.1.10', '169.254.169.254', '172.16.0.1', '::1', 'fd00::1'):
            with self.subTest(ip=ip), patch.object(socket, 'getaddrinfo', adresses(ip)), self.assertRaises(ValueError):
                cx._adresse_publique('https://piege.exemple/fichier.pdf')
        for url in ('ftp://exemple.fr/a.pdf', 'https://moi:secret@exemple.fr/a.pdf', 'file:///etc/passwd'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                cx._adresse_publique(url)
        with patch.object(socket, 'getaddrinfo', adresses('93.184.216.34')):
            cx._adresse_publique('https://exemple.fr/a.pdf')

    def test_une_redirection_vers_le_reseau_interne_est_refusee(self):
        entetes = Message()
        entetes['Location'] = 'http://interne.local/admin'

        class Ouvreur:
            def open(self, requete, timeout):
                raise HTTPError(requete.full_url, 302, 'Found', entetes, io.BytesIO())

        def resolution(hote, port):
            return adresses('10.1.2.3' if hote == 'interne.local' else '93.184.216.34')(hote, port)
        with patch.object(cx, 'build_opener', return_value=Ouvreur()), patch.object(socket, 'getaddrinfo', resolution), \
                self.assertRaises(ValueError):
            cx.telecharger('https://exemple.fr/fichier.pdf')


class DatagouvTests(unittest.TestCase):
    def test_recherche_resume_les_jeux_et_marque_les_formats_importables(self):
        with patch.object(cx, '_api', return_value={'total': 1, 'data': [JEU]}) as api:
            resultat = cx.rechercher('  délibérations   2025 ')
        self.assertEqual(api.call_args.args[1]['q'], 'délibérations 2025')
        jeu = resultat['jeux'][0]
        self.assertEqual((jeu['organisation'], jeu['mise_a_jour']), ('Commune de Testville', '2026-01-05'))
        self.assertEqual([(r['format'], r['importable']) for r in jeu['ressources']], [('csv', True), ('shp', False)])
        with self.assertRaises(ValueError):
            cx.rechercher('a')

    def test_formats_lisibles_et_formats_techniques_ecartes(self):
        # Test de la démonstration : « VND.OPENXMLFORMATS-OFFICEDOCUMENT.SPREADSHEETML.SHEET » au lieu de XLSX, et des
        # formats du web sémantique (RDF, Turtle, N3…) qui encombraient chaque jeu.
        jeu = dict(JEU, resources=[
            {'id': 'r1', 'title': 'Tableau', 'format': 'vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'url': 'https://exemple.fr/t'},
            {'id': 'r2', 'title': 'Texte', 'format': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'url': 'https://exemple.fr/w'},
            {'id': 'r3', 'title': 'Ancien', 'format': 'vnd.ms-excel', 'url': 'https://exemple.fr/x'},
            {'id': 'r4', 'title': 'Brut', 'format': 'text/plain', 'url': 'https://exemple.fr/b'},
            {'id': 'r5', 'title': 'RDF', 'format': 'rdf+xml', 'url': 'https://exemple.fr/r.rdf'},
            {'id': 'r6', 'title': 'Turtle', 'format': 'turtle', 'url': 'https://exemple.fr/r.ttl'},
            {'id': 'r7', 'title': 'N3', 'format': 'n3', 'url': 'https://exemple.fr/r.n3'},
            {'id': 'r8', 'title': 'LD', 'format': 'ld+json', 'url': 'https://exemple.fr/r.jsonld'},
            {'id': 'r9', 'title': 'Parquet', 'format': 'parquet', 'url': 'https://exemple.fr/r.parquet'},
            {'id': 'r10', 'title': 'Lignes', 'format': 'jsonl', 'url': 'https://exemple.fr/r.jsonl'},
        ])
        with patch.object(cx, '_api', return_value={'total': 1, 'data': [jeu]}):
            ressources = cx.rechercher('subventions')['jeux'][0]['ressources']
        self.assertEqual([(r['format'], r['importable']) for r in ressources],
                         [('xlsx', False), ('docx', True), ('xls', False), ('txt', True)])

    def test_article_legifrance_alineas_a_la_ligne(self):
        # Test de la démonstration : l'article L2122-22 arrivait en un seul bloc, « … mandat : 1° D'arrêter … ; 2° De fixer … ».
        plat = ("L2122-22\n\nLe maire peut, par délégation du conseil municipal, être chargé : 1° D'arrêter l'affectation des "
                "propriétés communales ; 2° De fixer les tarifs des droits de voirie ; 3° De procéder aux emprunts. Le maire "
                "rend compte au 10° alinéa.")
        source = {'code': 'Code général des collectivités territoriales', 'article': 'L2122-22', 'text': plat,
                  'url': 'https://www.legifrance.gouv.fr/x', 'as_of': '2026-11-08', 'version': 'v'}
        with patch.object(cx, 'textes_legifrance', return_value={'L2122-22': source}):
            texte = cx.article_legifrance(server.DATA, False, 'Code général des collectivités territoriales', 'L2122-22')['text']
        self.assertEqual(texte.split('\n')[2:], ['Le maire peut, par délégation du conseil municipal, être chargé :',
                                                  "1° D'arrêter l'affectation des propriétés communales ;",
                                                  '2° De fixer les tarifs des droits de voirie ;',
                                                  '3° De procéder aux emprunts. Le maire rend compte au 10° alinéa.'])

    def test_un_titre_deja_suffixe_csv_ou_json_ne_double_pas_l_extension(self):
        for titre, forme, attendu in (('subventions-2025.csv', 'csv', 'subventions-2025.txt'),
                                      ('Budget.JSON', 'json', 'Budget.txt')):
            jeu = {**JEU, 'resources': [{'id': 'ressource-0001', 'title': titre, 'format': forme, 'url': 'https://exemple.fr/f'}]}
            with self.subTest(titre=titre), patch.object(cx, '_api', return_value=jeu), \
                    patch.object(cx, 'telecharger', return_value=(b'a;b', 'text/plain')):
                self.assertEqual(cx.recuperer('abcdef0123456789', 'ressource-0001')[0], attendu)

    def test_recuperation_relit_l_adresse_sur_data_gouv_et_convertit_le_csv(self):
        with patch.object(cx, '_api', return_value=JEU), patch.object(cx, 'telecharger', return_value=('Numéro;Objet\n1;Budget'.encode('latin-1'), 'text/csv')) as telechargement:
            nom, contenu, page = cx.recuperer('abcdef0123456789', 'ressource-0001')
        self.assertEqual(telechargement.call_args.args[0], 'https://exemple.fr/liste.csv')
        self.assertEqual((nom, page), ('Liste des délibérations.txt', JEU['page']))
        self.assertEqual(contenu.decode('utf-8'), 'Numéro;Objet\n1;Budget')
        with patch.object(cx, '_api', return_value=JEU), self.assertRaises(ValueError):
            cx.recuperer('abcdef0123456789', 'ressource-0002')
        with patch.object(cx, '_api', return_value=JEU), self.assertRaises(LookupError):
            cx.recuperer('abcdef0123456789', 'ressource-9999')
        with self.assertRaises(ValueError):
            cx.recuperer('../../etc', 'ressource-0001')


class ImportTests(Base):
    def setUp(self):
        super().setUp()
        cx.enregistrer(server.DATA, {'datagouv': True})

    def test_recherche_refusee_si_inactif_ou_hors_ligne(self):
        cx.enregistrer(server.DATA, {'datagouv': False})
        self.assertEqual(self.appel('GET', '/api/connecteurs/datagouv?q=budget')[0], 403)
        cx.enregistrer(server.DATA, {'datagouv': True})
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, hors_ligne=True)):
            self.assertEqual(self.appel('GET', '/api/connecteurs/datagouv?q=budget')[0], 403)
            self.assertFalse(self.appel('GET', '/api/connecteurs')[1]['datagouv'])

    def test_import_dans_un_espace_avec_sa_source(self):
        with patch.object(cx, 'recuperer', return_value=('Liste.txt', 'Les délibérations budgétaires de 2025.'.encode(), JEU['page'])):
            statut, corps = self.appel('POST', '/api/connecteurs/datagouv/import', {'jeu': JEU['id'], 'ressource': 'ressource-0001', 'espace': 'interne'})
        self.assertEqual(statut, 201)
        document = next(x for x in self.appel('GET', '/api/documents?space=interne')[1] if x['id'] == corps['id'])
        self.assertEqual(document['source_url'], JEU['page'])
        self.assertEqual(server.search('délibérations budgétaires', 'interne')[0]['url'], JEU['page'])

    def test_import_soumis_aux_droits_et_aux_regles(self):
        admin = self.premier_admin()
        _, agent = self.creer(admin, 'bob')
        demande = {'jeu': JEU['id'], 'ressource': 'ressource-0001', 'espace': 'interne'}
        with patch.object(cx, 'recuperer', return_value=('Liste.txt', b'Texte public.', JEU['page'])) as recuperation:
            self.assertEqual(self.appel('POST', '/api/connecteurs/datagouv/import', demande, agent)[0], 404)  # espace de l'administratrice
            self.appel('POST', '/api/admin/regles', {'import_autorise': False}, admin)
            self.assertEqual(self.appel('POST', '/api/connecteurs/datagouv/import', demande, admin)[0], 403)
        recuperation.assert_not_called()


if __name__ == '__main__':
    unittest.main()


import hashlib
import json
import subprocess
from types import SimpleNamespace

from territoria import deliberations


def source_legifrance(article, texte='Texte officiel.', code='Code général des collectivités territoriales', as_of='2026-11-02'):
    complet = article + '\n\n' + texte
    return {'code': code, 'article': article, 'status': 'ok', 'as_of': as_of, 'text': complet,
            'sha256': hashlib.sha256(complet.encode()).hexdigest(), 'url': 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000000000001'}


class LegifranceTests(Base):
    def activer(self):
        cx.enregistrer(server.DATA, {'legifrance': {'actif': True, 'client_id': 'identifiant-piste', 'client_secret': 'secret-piste-long'}})

    def test_identifiants_transmis_par_l_environnement_seulement(self):
        self.assertEqual(cx.environnement_legifrance(server.DATA, False), {})
        self.activer()
        self.assertEqual(cx.environnement_legifrance(server.DATA, True), {})
        abimee = source_legifrance('L2121-17') | {'sha256': 'faux'}
        sortie = json.dumps({'sources': [source_legifrance('L2121-29'), abimee]}).encode()
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout=sortie)) as lancement:
            textes = cx.textes_legifrance(server.DATA, False, [('Code général des collectivités territoriales', 'L2121-29'),
                                                                ('Code général des collectivités territoriales', 'l 2121-17')])
        self.assertEqual(list(textes), ['L2121-29'])  # texte altéré écarté
        args, kwargs = lancement.call_args
        self.assertNotIn('secret-piste-long', ' '.join(map(str, args[0])))
        self.assertEqual(kwargs['env']['PISTE_CLIENT_SECRET'], 'secret-piste-long')
        self.assertEqual(json.loads(kwargs['input'])['references'][1], ['Code général des collectivités territoriales', 'L2121-17'])

    def test_consultation_et_ajout_a_un_espace(self):
        self.activer()
        reponse = {'L2122-22': source_legifrance('L2122-22', 'Le maire peut, en outre, par délégation du conseil municipal…')}
        with patch.object(cx, 'textes_legifrance', return_value=reponse):
            statut, texte = self.appel('POST', '/api/connecteurs/legifrance/article', {'code': 'Code général des collectivités territoriales', 'article': 'L2122-22'})
            self.assertEqual((statut, texte['article']), (200, 'L2122-22'))
            statut, ajout = self.appel('POST', '/api/connecteurs/legifrance/ajouter', {'code': 'Code général des collectivités territoriales',
                                                                                     'article': 'L2122-22', 'espace': 'public'})
        self.assertEqual(statut, 201)
        document = next(x for x in self.appel('GET', '/api/documents?space=public')[1] if x['id'] == ajout['id'])
        self.assertEqual((document['source_url'], document['captured']), (reponse['L2122-22']['url'], '2026-11-02'))
        with patch.object(cx, 'textes_legifrance', return_value={}):
            self.assertEqual(self.appel('POST', '/api/connecteurs/legifrance/article', {'code': 'Code général des collectivités territoriales', 'article': 'L9999-9'})[0], 404)
        self.assertEqual(self.appel('POST', '/api/connecteurs/legifrance/article', {'code': 'Code inventé', 'article': 'L1'})[0], 400)
        self.assertEqual(self.appel('POST', '/api/connecteurs/legifrance/article', {'code': 'Code civil', 'article': 'n’importe quoi'})[0], 400)

    def test_inactif_ou_sans_identifiants_refuse(self):
        self.assertEqual(self.appel('POST', '/api/connecteurs/legifrance/article', {'code': 'Code civil', 'article': '1240'})[0], 403)
        self.assertEqual(cx._normaliser_article('Article 1240'), '1240')
        self.assertEqual(cx._normaliser_article('l 2122-22'), 'L2122-22')

    def test_test_de_connexion(self):
        self.activer()
        with patch.object(cx, 'textes_legifrance', return_value={'L2121-29': source_legifrance('L2121-29')}):
            self.assertEqual(self.appel('POST', '/api/admin/connecteurs/legifrance/tester', {})[0], 200)
        with patch.object(cx, 'textes_legifrance', return_value={}):
            statut, corps = self.appel('POST', '/api/admin/connecteurs/legifrance/tester', {})
        self.assertEqual(statut, 502)
        self.assertIn('PISTE', corps['error'])

    def test_deliberations_avec_les_textes_du_jour_une_fois_par_jour(self):
        self.activer()
        livree = deliberations.textes()
        fraiches = {a: source_legifrance(a, 'Version du jour.', s['code'], deliberations_jour()) for a, s in livree.items()}
        with patch.object(cx, 'textes_legifrance', return_value=fraiches) as appel:
            premiers = deliberations.textes(server.DATA, False)
            seconds = deliberations.textes(server.DATA, False)
        self.assertEqual(appel.call_count, 1)  # le cache du jour sert ensuite
        self.assertTrue(premiers['L2121-29']['text'].endswith('Version du jour.'))
        self.assertEqual(seconds['L2121-29']['as_of'], deliberations_jour())

    def test_sans_connecteur_la_copie_livree(self):
        self.assertEqual(deliberations.textes(server.DATA, False)['L2121-29']['as_of'], '2026-11-01')


def deliberations_jour():
    import legal_sources
    return legal_sources.today()


class LegifranceVisiteurTests(Base):
    """Démonstration : la consultation passe par le compte PISTE de l'administrateur, sans limite ni cache ; un visiteur
    anonyme ne doit pas pouvoir s'en servir (blocage du compte par PISTE, processus lancés à volonté)."""
    def setUp(self):
        super().setUp()
        from territoria import comptes
        from tests.test_comptes import MDP_ADMIN
        comptes.creer_premier_administrateur(server.DB, {'identifiant': 'admin', 'nom': 'Alice Admin', 'mot_de_passe': MDP_ADMIN})
        self.admin = self.connexion('admin', MDP_ADMIN)
        correctif = patch.object(web, 'REGLAGES', replace(web.REGLAGES, demonstration=True, hors_ligne=False))
        correctif.start()
        self.addCleanup(correctif.stop)
        cx.enregistrer(server.DATA, {'legifrance': {'actif': True, 'client_id': 'identifiant-piste', 'client_secret': 'secret-piste-long'}})
        self.appel('POST', '/api/session/visiteur', {})
        self.visiteur = self.jeton_de(self.derniere)

    def test_le_visiteur_ne_consulte_ni_n_ajoute_rien(self):
        demande = {'code': 'Code général des collectivités territoriales', 'article': 'L2122-22', 'espace': 'interne'}
        with patch.object(cx, 'textes_legifrance') as legifrance:
            for chemin in ('/api/connecteurs/legifrance/article', '/api/connecteurs/legifrance/ajouter'):
                statut, corps = self.appel('POST', chemin, demande, self.visiteur)
                self.assertEqual((statut, corps['error']), (403, 'La consultation de Légifrance est réservée aux comptes de la collectivité.'))
        legifrance.assert_not_called()
        self.assertFalse(self.appel('GET', '/api/connecteurs', jeton=self.visiteur)[1]['legifrance'])  # l'onglet est masqué

    def test_l_administration_consulte_toujours(self):
        reponse = {'L2122-22': source_legifrance('L2122-22', 'Le maire peut, en outre, par délégation du conseil municipal…')}
        with patch.object(cx, 'textes_legifrance', return_value=reponse):
            statut, _ = self.appel('POST', '/api/connecteurs/legifrance/article',
                                   {'code': 'Code général des collectivités territoriales', 'article': 'L2122-22'}, self.admin)
        self.assertEqual(statut, 200)
        self.assertTrue(self.appel('GET', '/api/connecteurs', jeton=self.admin)[1]['legifrance'])


class PontTests(unittest.TestCase):
    def test_le_pont_des_arretes_n_attend_rien_sur_l_entree_standard(self):
        import legal_sources
        with patch.object(legal_sources, 'REGLAGES', replace(legal_sources.REGLAGES, hors_ligne=False)), \
                patch.object(legal_sources.subprocess, 'run', side_effect=subprocess.SubprocessError) as lancement, \
                patch.object(legal_sources, '_read_cache', return_value={'as_of': '', 'sources': []}):
            import tempfile
            with tempfile.TemporaryDirectory() as dossier:
                cx.enregistrer(Path(dossier), {'legifrance': {'actif': True, 'client_id': 'identifiant-piste', 'client_secret': 'secret-piste-long'}})
                legal_sources.refresh(dossier)
        self.assertIs(lancement.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_le_pont_retire_les_espaces_parasites_de_la_conversion(self):
        import os
        with patch.dict(os.environ):  # le pont règle son environnement en s'important
            import legal_bridge
        brut = ("Dans les conditions prévues aux articles L. 2121-10 à L. 2121-12 , ce quorum n'est pas atteint "
                "(article L. 1612-1 ) ; à l' article L. 331-1 et jusqu' à L. 2411-19 . Le bilan :\n\n« 1° Les tarifs ; »")
        self.assertEqual(legal_bridge.nettoyer(brut),
                         "Dans les conditions prévues aux articles L. 2121-10 à L. 2121-12, ce quorum n'est pas atteint "
                         "(article L. 1612-1) ; à l'article L. 331-1 et jusqu'à L. 2411-19. Le bilan :\n\n« 1° Les tarifs ; »")
