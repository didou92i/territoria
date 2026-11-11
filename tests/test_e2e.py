# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import re
import traceback
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from docx import Document
from pypdf import PdfReader

import e2e

HOTE = '127.0.0.1:8878'
ORIGINE = 'http://' + HOTE
JETON = 'jeton-du-faux-serveur'
SECRET = 'CONTENU-CONFIDENTIEL-A-NE-JAMAIS-REPETER'
ARTICLES = [{'number': 1, 'title': 'Prescriptions'}, {'number': 2, 'title': 'Mise en œuvre'},
            {'number': 3, 'title': 'Contrôle et sanctions'}]
NOMS = ['espace', 'import_pdf', 'recherche_page2', 'sans_source_sans_ia', 'reponse_sourcee', 'arrete_word',
        'origine_tierce_refusee', 'interne_sans_jeton_refuse', 'nettoyage']


def word(titres):
    document = Document()
    for numero, titre in enumerate(titres, 1):
        document.add_paragraph(f'Article {numero} : {titre}')
    sortie = io.BytesIO()
    document.save(sortie)
    return sortie.getvalue()


class FauxTerritoria:
    """Joue le rôle de l'application derrière `e2e._envoyer` : mêmes refus d'accès, mêmes formes de réponse.

    `intercepter(methode, chemin, corps, entetes)` peut imposer une réponse (statut, objet) ou lever une exception."""

    def __init__(self):
        self.requetes = []  # (méthode, chemin avec requête, en-têtes, corps)
        self.espaces, self.documents, self.conversations = {}, set(), {}  # espaces : identifiant -> titre
        self.espace_du_document = {}
        self.supprimes = []
        self.intercepter = None

    def __call__(self, cible, methode, chemin, corps, entetes):
        self.requetes.append((methode, chemin, dict(entetes), corps))
        if self.intercepter:
            imposee = self.intercepter(methode, chemin, corps, entetes)
            if imposee is not None:
                return self.reponse(*imposee)
        return self.reponse(*self.traiter(methode, chemin, corps, entetes))

    @staticmethod
    def reponse(statut, objet, type_contenu='application/json'):
        brut = objet if isinstance(objet, bytes) else json.dumps(objet).encode()
        return statut, brut, {'content-type': type_contenu}

    def traiter(self, methode, chemin, corps, entetes):
        chemin_seul, _, requete = chemin.partition('?')
        if chemin_seul in ('/api/verify', '/api/llm/chat'):
            return (403, {'error': 'Accès refusé.'}) if entetes.get('X-Territoria-Service-Token') != JETON else (400, {})
        if entetes.get('Host') != HOTE or (methode in ('POST', 'DELETE') and entetes.get('Origin') != ORIGINE):
            return 403, {'error': 'Accès refusé.'}
        donnees = json.loads(corps) if corps and entetes.get('Content-Type') == 'application/json' else None
        if (methode, chemin_seul) == ('POST', '/api/spaces'):
            if not donnees.get('title'):
                return 400, {'error': 'Indiquez le nom de l’espace.'}
            ident = uuid.uuid4().hex
            self.espaces[ident] = donnees['title']
            return 201, {'id': ident}
        if (methode, chemin_seul) == ('GET', '/api/spaces'):
            return 200, [{'id': i, 'title': t, 'description': '', 'count': list(self.espace_du_document.values()).count(i)}
                         for i, t in self.espaces.items()]
        if methode == 'POST' and chemin_seul.startswith('/api/upload/'):
            ident = uuid.uuid4().hex
            self.documents.add(ident)
            self.espace_du_document[ident] = chemin_seul.rsplit('/', 1)[-1]
            return 201, {'id': ident}
        if (methode, chemin_seul) == ('GET', '/api/search'):
            parametres = parse_qs(requete)
            ici = sorted(i for i, e in self.espace_du_document.items() if e == parametres['space'][0])
            return 200, [{'id': i, 'page': 2} for i in ici] if 'archives' in parametres['q'][0] else []
        if (methode, chemin_seul) == ('POST', '/api/chat'):
            return self.discuter(donnees['question'], donnees['space'])
        arrete = re.fullmatch(r'/api/arretes/([0-9a-f]{32})/file', chemin_seul)
        if methode == 'GET' and arrete:
            if arrete.group(1) not in self.conversations.values():
                return 404, {'error': 'Projet introuvable.'}
            return 200, word([a['title'] for a in ARTICLES]), e2e.TYPE_WORD
        suppression = re.fullmatch(r'/api/(conversations|documents)/([0-9a-f]{32})', chemin_seul)
        if methode == 'DELETE' and suppression:
            ensemble = self.conversations if suppression.group(1) == 'conversations' else self.documents
            if suppression.group(2) not in ensemble:
                return 404, {'error': 'Introuvable.'}
            (ensemble.pop(suppression.group(2)) if isinstance(ensemble, dict) else ensemble.remove(suppression.group(2)))
            self.espace_du_document.pop(suppression.group(2), None)
            self.supprimes.append(suppression.group(2))
            return 200, {'supprime': True}
        return 404, {'error': 'Introuvable.'}

    def discuter(self, question, espace):
        conversation, arrete = uuid.uuid4().hex, uuid.uuid4().hex
        reponse = {'conversation': conversation, 'answer': 'Je n’ai trouvé aucun passage.', 'sources': [],
                   'artifact': {}, 'mode': 'sources', 'model_used': False}
        if question == e2e.QUESTION_ARRETE:
            reponse.update(model_used=True, answer='Votre projet d’arrêté est rédigé.', artifact={
                'kind': 'arrete', 'id': arrete, 'download': f'/api/arretes/{arrete}/file', 'document': {'articles': ARTICLES}})
        elif question == e2e.QUESTION_SOURCEE:
            reponse.update(model_used=True, answer='Elles ouvrent le mardi matin [1].',
                           sources=[{'id': i, 'page': 2} for i, e in sorted(self.espace_du_document.items()) if e == espace])
        self.conversations[conversation] = arrete
        return 200, reponse

    def restes(self):
        return {'conversations': set(self.conversations), 'documents': set(self.documents)}


class E2eBase(unittest.TestCase):
    def setUp(self):
        self.faux = FauxTerritoria()
        patcher = patch.object(e2e, '_envoyer', self.faux)
        patcher.start()
        self.addCleanup(patcher.stop)
        pause = patch.object(e2e, 'PAUSE_REESSAI', 0)  # aucun vrai délai avant un nouvel essai
        pause.start()
        self.addCleanup(pause.stop)

    def titres(self):
        return sorted(self.faux.espaces.values())

    def lancer(self, strict=True):
        return e2e.executer('http://127.0.0.1:8878', ORIGINE, strict=strict)

    def echec(self):
        with self.assertRaises(AssertionError) as contexte:
            self.lancer()
        return contexte.exception


class ParcoursReussiTests(E2eBase):
    def test_renvoie_les_neuf_controles_dans_l_ordre(self):
        self.assertEqual(self.lancer(), NOMS)

    def test_signature_et_valeurs_par_defaut(self):
        import inspect
        parametres = inspect.signature(e2e.executer).parameters
        self.assertEqual(list(parametres), ['cible', 'origine', 'strict', 'jeton'])
        self.assertEqual(parametres['cible'].default, 'http://127.0.0.1:8878')
        self.assertEqual(parametres['origine'].default, e2e.REGLAGES.origine)
        self.assertIs(parametres['strict'].default, False)  # strict est réservé à la CI avec la fausse IA

    def test_ne_laisse_rien_derriere_lui(self):
        self.lancer()
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})
        # trois conversations (sans source, sourcée, arrêté) et un document ; un second DELETE leur répond 404
        suppressions = [r for r in self.faux.requetes if r[0] == 'DELETE']
        self.assertEqual(len(self.faux.supprimes), 4)
        self.assertEqual(len(suppressions), 8)

    def test_envoie_le_host_de_l_origine_et_l_origine_sur_post_et_delete(self):
        self.lancer()
        fautives = 0
        for methode, chemin, entetes, corps in self.faux.requetes:
            if chemin == '/api/verify' or entetes['Host'] != HOTE or (chemin == '/api/spaces' and corps and b'"title": ""' in corps):
                fautives += 1  # requêtes volontairement fautives des contrôles de refus
                continue
            self.assertEqual(entetes['Host'], HOTE, chemin)
            if methode in ('POST', 'DELETE'):
                self.assertEqual(entetes.get('Origin'), ORIGINE, chemin)
            else:
                self.assertNotIn('Origin', entetes, chemin)
        self.assertEqual(fautives, 5)  # 2 vers /api/verify, 2 POST sans bonne origine, 1 GET avec un autre hôte

    def test_les_contrôles_de_refus_envoient_bien_des_requetes_fautives(self):
        self.lancer()
        spaces = [r for r in self.faux.requetes if (r[0], r[1]) in (('POST', '/api/spaces'), ('GET', '/api/spaces'))]
        self.assertTrue(any(r[2].get('Origin') == e2e.ORIGINE_TIERCE for r in spaces))
        self.assertTrue(any(r[0] == 'POST' and 'Origin' not in r[2] for r in spaces))
        self.assertTrue(any(r[0] == 'GET' and r[2]['Host'] != HOTE for r in spaces))
        verify = [r for r in self.faux.requetes if r[1] == '/api/verify']
        self.assertEqual(len(verify), 2)
        self.assertTrue(all(r[2].get('X-Territoria-Service-Token') != JETON for r in verify))

    def test_le_pdf_importe_a_deux_pages_et_seule_la_page_2_repond_a_la_recherche(self):
        pdf = e2e._pdf_deux_pages()
        pages = [page.extract_text() or '' for page in PdfReader(io.BytesIO(pdf)).pages]
        self.assertEqual(len(pages), 2)
        mots = [m for m in e2e.RECHERCHE.split()]
        self.assertFalse(any(m[:6] in pages[0].lower() for m in mots))
        self.assertTrue(all(m in pages[1].lower() for m in mots))
        self.lancer()
        televersement = next(r for r in self.faux.requetes if r[1].startswith('/api/upload/'))
        self.assertEqual(televersement[3][:5], b'%PDF-')
        self.assertEqual(parse_qs(urlsplit(televersement[1]).query), {'name': ['essai-bout-en-bout.pdf']})


class EchecTests(E2eBase):
    """Chaque contrôle peut échouer : l'erreur ne porte que son nom et tout ce qui existe est supprimé."""

    def intercepter(self, predicat, reponse):
        def regle(methode, chemin, corps, entetes):
            donnees = json.loads(corps) if corps and entetes.get('Content-Type') == 'application/json' else {}
            return reponse(self.faux) if predicat(methode, chemin, donnees, entetes) else None
        self.faux.intercepter = regle

    def verifier_echec(self, nom):
        erreur = self.echec()
        self.assertIs(type(erreur), AssertionError)
        self.assertEqual((erreur.args, str(erreur)), ((nom,), nom))
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()}, 'tout est nettoyé')
        return erreur

    def test_espace(self):
        self.intercepter(lambda m, c, d, e: (m, c) == ('POST', '/api/spaces') and d.get('title'), lambda f: (500, {'error': SECRET}))
        self.verifier_echec('espace')

    def test_import_pdf(self):
        self.intercepter(lambda m, c, d, e: c.startswith('/api/upload/'), lambda f: (400, {'error': SECRET}))
        self.verifier_echec('import_pdf')

    def test_recherche_page2(self):
        self.intercepter(lambda m, c, d, e: c.startswith('/api/search'), lambda f: (200, [{'id': sorted(f.documents)[0], 'page': 1}]))
        self.verifier_echec('recherche_page2')
        self.assertEqual(len(self.faux.supprimes), 1)  # le document déjà importé

    def test_sans_source_sans_ia(self):
        self.intercepter(lambda m, c, d, e: d.get('question') == e2e.QUESTION_SANS_SOURCE,
                         lambda f: (200, {'conversation': uuid.uuid4().hex, 'answer': SECRET, 'sources': [], 'model_used': True}))
        self.verifier_echec('sans_source_sans_ia')

    def test_reponse_sourcee_sans_reference(self):
        self.intercepter(lambda m, c, d, e: d.get('question') == e2e.QUESTION_SOURCEE,
                         lambda f: (200, {'conversation': uuid.uuid4().hex, 'answer': SECRET, 'model_used': True,
                                          'sources': [{'id': sorted(f.documents)[0], 'page': 2}]}))
        self.verifier_echec('reponse_sourcee')

    def test_reponse_sourcee_page_incorrecte(self):
        self.intercepter(lambda m, c, d, e: d.get('question') == e2e.QUESTION_SOURCEE,
                         lambda f: (200, {'conversation': uuid.uuid4().hex, 'answer': '[1]', 'model_used': True,
                                          'sources': [{'id': sorted(f.documents)[0], 'page': 1}]}))
        self.verifier_echec('reponse_sourcee')

    def test_arrete_word_artefact_absent(self):
        self.intercepter(lambda m, c, d, e: d.get('question') == e2e.QUESTION_ARRETE,
                         lambda f: (200, {'conversation': uuid.uuid4().hex, 'answer': SECRET, 'artifact': {}, 'model_used': True}))
        self.verifier_echec('arrete_word')

    def test_arrete_word_titre_absent_du_word(self):
        self.intercepter(lambda m, c, d, e: m == 'GET' and c.startswith('/api/arretes/'),
                         lambda f: (200, word(['Prescriptions']), e2e.TYPE_WORD))
        self.verifier_echec('arrete_word')

    def test_arrete_word_telechargement_refuse(self):
        self.intercepter(lambda m, c, d, e: m == 'GET' and c.startswith('/api/arretes/'), lambda f: (404, {}))
        self.verifier_echec('arrete_word')

    def test_arrete_word_fichier_qui_n_est_pas_un_word(self):
        self.intercepter(lambda m, c, d, e: m == 'GET' and c.startswith('/api/arretes/'),
                         lambda f: (200, SECRET.encode(), e2e.TYPE_WORD))
        self.verifier_echec('arrete_word')

    def test_origine_tierce_acceptee(self):
        self.intercepter(lambda m, c, d, e: e.get('Origin') == e2e.ORIGINE_TIERCE, lambda f: (400, {'error': SECRET}))
        self.verifier_echec('origine_tierce_refusee')

    def test_hote_tiers_accepte(self):
        self.intercepter(lambda m, c, d, e: e.get('Host') == 'hote-tiers.invalide', lambda f: (200, []))
        self.verifier_echec('origine_tierce_refusee')

    def test_interne_sans_jeton_accepte(self):
        self.intercepter(lambda m, c, d, e: c == '/api/verify', lambda f: (200, {'request': SECRET}))
        self.verifier_echec('interne_sans_jeton_refuse')

    def test_nettoyage_second_delete_qui_reussit(self):
        vus = set()

        def deuxieme_delete(m, c, d, e):
            if m != 'DELETE':
                return False
            deja, _ = c in vus, vus.add(c)
            return deja
        self.intercepter(deuxieme_delete, lambda f: (200, {'supprime': True}))
        self.verifier_echec('nettoyage')

    def test_nettoyage_document_encore_trouvable_apres_suppression(self):
        self.intercepter(lambda m, c, d, e: c.startswith('/api/search') and not self.faux.documents,
                         lambda f: (200, [{'id': 'x', 'page': 2}]))
        self.verifier_echec('nettoyage')

    def test_nettoyage_word_encore_telechargeable_apres_suppression(self):
        self.intercepter(lambda m, c, d, e: m == 'GET' and c.startswith('/api/arretes/') and not self.faux.conversations,
                         lambda f: (200, word(['Prescriptions']), e2e.TYPE_WORD))
        self.verifier_echec('nettoyage')

    def test_nettoyage_continue_apres_une_suppression_en_echec(self):
        premiere = {}

        def echoue_une_fois(m, c, d, e):
            if m == 'DELETE' and c.startswith('/api/conversations/') and 'ident' not in premiere:
                premiere['ident'] = c
                return True
            return False
        self.intercepter(echoue_une_fois, lambda f: (500, {'error': SECRET}))
        erreur = self.echec()
        self.assertEqual(erreur.args, ('nettoyage',))
        # la conversation dont le DELETE a échoué est reprise par le nettoyage de secours ; tout le reste est parti
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})


class EspaceReutiliseTests(E2eBase):
    """R15 : l'espace d'essai ne se supprime pas, donc il est réutilisé d'une exécution à l'autre."""

    def test_deux_executions_de_suite_laissent_un_seul_espace(self):
        self.assertEqual(self.lancer(), NOMS)
        self.assertEqual(self.titres(), [e2e.TITRE_ESPACE])
        premier = next(iter(self.faux.espaces))
        creations = [r for r in self.faux.requetes if (r[0], r[1]) == ('POST', '/api/spaces') and b'Essai' in r[3]]
        self.assertEqual(len(creations), 1)
        self.assertEqual(self.lancer(), NOMS)
        self.assertEqual(self.titres(), [e2e.TITRE_ESPACE])
        self.assertEqual(list(self.faux.espaces), [premier])  # c'est bien le même espace
        creations = [r for r in self.faux.requetes if (r[0], r[1]) == ('POST', '/api/spaces') and b'Essai' in r[3]]
        self.assertEqual(len(creations), 1, 'aucune seconde création')
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_trois_executions_dont_une_en_echec_laissent_un_seul_espace(self):
        self.lancer()
        self.faux.intercepter = lambda m, c, corps, e: (200, []) if c.startswith('/api/search') else None
        self.assertEqual(self.echec().args, ('recherche_page2',))
        self.faux.intercepter = None
        self.lancer()
        self.assertEqual(self.titres(), [e2e.TITRE_ESPACE])

    def test_le_document_importe_va_dans_l_espace_reutilise(self):
        existant = uuid.uuid4().hex
        self.faux.espaces[existant] = e2e.TITRE_ESPACE
        self.lancer()
        televersement = next(r for r in self.faux.requetes if r[1].startswith('/api/upload/'))
        self.assertEqual(urlsplit(televersement[1]).path, f'/api/upload/{existant}')
        self.assertEqual(self.titres(), [e2e.TITRE_ESPACE])

    def test_seul_le_titre_exact_est_reutilise(self):
        for titre in ('Essai de bout en bout 2', 'essai de bout en bout', 'Mon espace', ''):
            self.faux.espaces[uuid.uuid4().hex] = titre
        self.lancer()
        self.assertEqual(self.titres().count(e2e.TITRE_ESPACE), 1)
        self.assertEqual(len(self.faux.espaces), 5)

    def test_un_espace_du_meme_titre_qui_contient_des_documents_n_est_pas_reutilise(self):
        # reste d'une exécution interrompue (ou espace de l'utilisateur) : ses documents fausseraient la recherche
        occupe, document = uuid.uuid4().hex, uuid.uuid4().hex
        self.faux.espaces[occupe] = e2e.TITRE_ESPACE
        self.faux.documents.add(document)
        self.faux.espace_du_document[document] = occupe
        self.assertEqual(self.lancer(), NOMS)
        self.assertEqual(self.titres(), [e2e.TITRE_ESPACE] * 2)
        self.assertEqual(self.faux.documents, {document}, 'le document étranger n\'est pas touché')
        self.lancer()  # le nouvel espace, vide, est réutilisé
        self.assertEqual(len(self.faux.espaces), 2)

    def test_liste_des_espaces_illisible(self):
        self.faux.intercepter = lambda m, c, corps, e: (500, {'error': SECRET}) if (m, c) == ('GET', '/api/spaces') and e['Host'] == HOTE else None
        erreur = self.echec()
        self.assertEqual(erreur.args, ('espace',))
        self.assertEqual(self.faux.espaces, {})


class ModeStrictTests(E2eBase):
    """R16 : hors mode strict, un vrai modèle peut oublier la citation ou rendre un JSON refusé une fois."""

    def intercepter_question(self, question, reponses):
        """Impose les réponses données, une par appel portant cette question, puis laisse passer le faux serveur."""
        restantes = list(reponses)

        def regle(methode, chemin, corps, entetes):
            if chemin == '/api/chat' and json.loads(corps)['question'] == question and restantes:
                return restantes.pop(0)
        self.faux.intercepter = regle

    def appels(self, question):
        return [r for r in self.faux.requetes if r[1] == '/api/chat' and json.loads(r[3])['question'] == question]

    def test_strict_est_faux_par_defaut(self):
        self.assertEqual(e2e.executer('http://127.0.0.1:8878', ORIGINE), NOMS)

    def test_une_reponse_sans_citation_passe_hors_strict_et_echoue_en_strict(self):
        original = self.faux.discuter

        def discuter(question, espace):
            statut, reponse = original(question, espace)
            if question == e2e.QUESTION_SOURCEE:
                reponse['answer'] = 'La réponse générée ne comporte pas de références exploitables.'
            return statut, reponse
        self.faux.discuter = discuter
        self.assertEqual(self.lancer(strict=False), NOMS)
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})
        erreur = self.echec()  # strict
        self.assertEqual(erreur.args, ('reponse_sourcee',))
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_hors_strict_la_source_et_le_passage_par_le_modele_restent_exiges(self):
        cas = {
            'modele non utilisé': lambda f: (200, {'conversation': uuid.uuid4().hex, 'model_used': False, 'answer': '[1]',
                                                   'sources': [{'id': sorted(f.documents)[0], 'page': 2}]}),
            'mauvaise page': lambda f: (200, {'conversation': uuid.uuid4().hex, 'model_used': True, 'answer': '[1]',
                                              'sources': [{'id': sorted(f.documents)[0], 'page': 1}]}),
            'mauvais document': lambda f: (200, {'conversation': uuid.uuid4().hex, 'model_used': True, 'answer': '[1]',
                                                 'sources': [{'id': uuid.uuid4().hex, 'page': 2}]}),
            'aucune source': lambda f: (200, {'conversation': uuid.uuid4().hex, 'model_used': True, 'answer': '[1]', 'sources': []}),
            'erreur 500': lambda f: (500, {'error': SECRET}),
            'erreur 400': lambda f: (400, {'error': SECRET}),
        }
        for nom, reponse in cas.items():
            with self.subTest(cas=nom):
                self.faux = FauxTerritoria()
                with patch.object(e2e, '_envoyer', self.faux):
                    self.faux.intercepter = (lambda rep: lambda m, c, corps, e: rep(self.faux) if (
                        c == '/api/chat' and json.loads(corps)['question'] == e2e.QUESTION_SOURCEE) else None)(reponse)
                    with self.assertRaises(AssertionError) as contexte:
                        self.lancer(strict=False)
                self.assertEqual(contexte.exception.args, ('reponse_sourcee',))
                self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_un_502_puis_la_reussite_passe_dans_les_deux_modes_pour_la_reponse_sourcee(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                self.faux = FauxTerritoria()
                with patch.object(e2e, '_envoyer', self.faux):
                    self.intercepter_question(e2e.QUESTION_SOURCEE, [(502, {'error': SECRET})])
                    self.assertEqual(self.lancer(strict=strict), NOMS)
                self.assertEqual(len(self.appels(e2e.QUESTION_SOURCEE)), 2)
                self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_deux_502_de_suite_font_echouer_la_reponse_sourcee_sans_troisieme_essai(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                self.faux = FauxTerritoria()
                with patch.object(e2e, '_envoyer', self.faux):
                    self.intercepter_question(e2e.QUESTION_SOURCEE, [(502, {'error': SECRET})] * 5)
                    with self.assertRaises(AssertionError) as contexte:
                        self.lancer(strict=strict)
                self.assertEqual(contexte.exception.args, ('reponse_sourcee',))
                self.assertEqual(len(self.appels(e2e.QUESTION_SOURCEE)), 2)

    def test_un_autre_code_d_erreur_n_est_pas_rejoue(self):
        self.intercepter_question(e2e.QUESTION_SOURCEE, [(500, {'error': SECRET})] * 5)
        self.assertEqual(self.echec().args, ('reponse_sourcee',))
        self.assertEqual(len(self.appels(e2e.QUESTION_SOURCEE)), 1)

    def test_arrete_un_502_puis_la_reussite_passe_hors_strict(self):
        self.intercepter_question(e2e.QUESTION_ARRETE, [(502, {'error': SECRET})])
        self.assertEqual(self.lancer(strict=False), NOMS)
        self.assertEqual(len(self.appels(e2e.QUESTION_ARRETE)), 2)
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_arrete_un_502_echoue_en_strict_sans_nouvel_essai(self):
        self.intercepter_question(e2e.QUESTION_ARRETE, [(502, {'error': SECRET})])
        erreur = self.echec()
        self.assertEqual(erreur.args, ('arrete_word',))
        self.assertEqual(len(self.appels(e2e.QUESTION_ARRETE)), 1)
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_arrete_deux_502_echouent_hors_strict(self):
        self.intercepter_question(e2e.QUESTION_ARRETE, [(502, {'error': SECRET})] * 5)
        with self.assertRaises(AssertionError) as contexte:
            self.lancer(strict=False)
        self.assertEqual(contexte.exception.args, ('arrete_word',))
        self.assertEqual(len(self.appels(e2e.QUESTION_ARRETE)), 2)
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_arrete_garde_toutes_ses_assertions_hors_strict(self):
        for nom, intercepteur in {
            'artefact absent': lambda m, c, corps, e: (200, {'conversation': uuid.uuid4().hex, 'artifact': {}, 'model_used': True})
            if c == '/api/chat' and e2e.QUESTION_ARRETE.encode() in corps else None,
            'titre absent du Word': lambda m, c, corps, e: (200, word(['Prescriptions']), e2e.TYPE_WORD)
            if m == 'GET' and c.startswith('/api/arretes/') else None,
            'Word non téléchargeable': lambda m, c, corps, e: (404, {}) if m == 'GET' and c.startswith('/api/arretes/') else None,
        }.items():
            with self.subTest(cas=nom):
                self.faux = FauxTerritoria()
                with patch.object(e2e, '_envoyer', self.faux):
                    self.faux.intercepter = intercepteur
                    with self.assertRaises(AssertionError) as contexte:
                        self.lancer(strict=False)
                self.assertEqual(contexte.exception.args, ('arrete_word',))

    def test_la_pause_avant_le_nouvel_essai_est_observee(self):
        self.intercepter_question(e2e.QUESTION_SOURCEE, [(502, {})])
        with patch.object(e2e.time, 'sleep') as dormir, patch.object(e2e, 'PAUSE_REESSAI', 2.5):
            self.lancer()
        dormir.assert_called_once_with(2.5)


class AucunContenuTests(E2eBase):
    def verifier_propre(self, erreur):
        texte = ''.join(traceback.format_exception(erreur)) + repr(erreur) + str(erreur)
        self.assertNotIn(SECRET, texte)
        self.assertIsNone(erreur.__context__)
        self.assertIsNone(erreur.__cause__)

    def test_message_du_serveur_jamais_repris(self):
        self.faux.intercepter = lambda m, c, corps, e: (500, {'error': SECRET}) if c == '/api/spaces' and corps and b'Essai' in corps else None
        erreur = self.echec()
        self.assertEqual(erreur.args, ('espace',))
        self.verifier_propre(erreur)

    def test_exception_reseau_jamais_reprise(self):
        def injoignable(m, c, corps, e):
            raise ConnectionRefusedError(SECRET)
        self.faux.intercepter = injoignable
        erreur = self.echec()
        self.assertEqual(erreur.args, ('espace',))
        self.verifier_propre(erreur)

    def test_reponse_qui_n_est_pas_du_json(self):
        self.faux.intercepter = lambda m, c, corps, e: (200, SECRET.encode()) if c.startswith('/api/search') else None
        erreur = self.echec()
        self.assertEqual(erreur.args, ('recherche_page2',))
        self.verifier_propre(erreur)
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_les_noms_de_controle(self):
        self.assertEqual([nom for nom, _ in e2e.CONTROLES], NOMS)


class NettoyageAvantPropagationTests(E2eBase):
    def test_nettoie_quand_une_exception_inattendue_traverse_un_controle(self):
        def panne_au_chat_arrete(methode, chemin, corps, entetes):
            if chemin == '/api/chat' and e2e.QUESTION_ARRETE.encode() in corps:
                raise RuntimeError(SECRET)
        self.faux.intercepter = panne_au_chat_arrete
        erreur = self.echec()
        self.assertEqual(erreur.args, ('arrete_word',))
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})
        self.assertEqual(len(self.faux.supprimes), 3)  # le document et les deux conversations déjà créées

    def test_nettoie_aussi_a_l_interruption_clavier(self):
        def interruption(methode, chemin, corps, entetes):
            if chemin == '/api/chat' and e2e.QUESTION_SOURCEE.encode() in corps:
                raise KeyboardInterrupt
        self.faux.intercepter = interruption
        with self.assertRaises(KeyboardInterrupt):
            self.lancer()
        self.assertEqual(self.faux.restes(), {'conversations': set(), 'documents': set()})

    def test_un_nettoyage_qui_echoue_ne_masque_pas_l_echec_d_origine(self):
        def tout_echoue(methode, chemin, corps, entetes):
            if methode == 'DELETE':
                raise OSError(SECRET)
            if chemin.startswith('/api/search'):
                return 200, []
        self.faux.intercepter = tout_echoue
        erreur = self.echec()
        self.assertEqual(erreur.args, ('recherche_page2',))



class DelaisTests(unittest.TestCase):
    """Le client n'abandonne jamais avant la chaîne qu'il vérifie : POST /api/chat (réponse sourcée et rédaction de
    l'arrêté) attend le délai de Territoria pour n8n plus une marge ; les autres requêtes gardent un délai court."""

    class Connexion:
        delais = []

        def __init__(self, hote, port, timeout):
            type(self).delais.append(timeout)

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            class Reponse:
                status = 200

                def read(self):
                    return b'{}'

                def getheaders(self):
                    return []
            return Reponse()

        def close(self):
            pass

    def delai(self, methode, chemin):
        self.Connexion.delais.clear()
        with patch.object(e2e.http.client, 'HTTPConnection', self.Connexion):
            e2e._envoyer('http://127.0.0.1:8878', methode, chemin, b'{}', {})
        return self.Connexion.delais[0]

    def test_chat_attend_toute_la_chaine_n8n(self):
        self.assertEqual(self.delai('POST', '/api/chat'), e2e.REGLAGES.delai_n8n + 30)
        with patch.object(e2e, 'REGLAGES', replace(e2e.REGLAGES, delai_n8n=1234)):
            self.assertEqual(self.delai('POST', '/api/chat'), 1264)

    def test_chat_plus_long_que_les_noeuds_ia_et_le_delai_de_territoria(self):
        # 960 s par défaut (TERRITORIA_DELAI_N8N) ; les nœuds IA des workflows attendent 600 s.
        self.assertGreater(self.delai('POST', '/api/chat'), max(e2e.REGLAGES.delai_n8n, 600))

    def test_autres_requetes_delai_court(self):
        for methode, chemin in (('GET', '/api/spaces'), ('POST', '/api/upload/x?name=a.pdf'), ('GET', '/api/search?q=x'),
                                ('DELETE', '/api/conversations/' + 'a' * 32), ('GET', '/api/arretes/' + 'a' * 32 + '/file'),
                                ('POST', '/api/chatx')):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.delai(methode, chemin), 30)


if __name__ == '__main__':
    unittest.main()
