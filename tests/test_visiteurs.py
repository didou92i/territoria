# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Démonstration publique : ce qu'un compte « visiteur » ne peut ni voir, ni remplir, ni laisser derrière lui."""
import contextlib
import io
import json
import sqlite3
import sys
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import llm_backend
import server
import web
from territoria import comptes, publication, seances, semantique
from tests.test_comptes import MDP_ADMIN, NAVIGATEUR, Base
from tests.test_publication_web import docx

BINAIRE = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}


class VisiteursTests(Base):
    def setUp(self):
        super().setUp()
        self.demonstration(True)
        comptes.creer_premier_administrateur(server.DB, {'identifiant': 'admin', 'nom': 'Alice Admin', 'mot_de_passe': MDP_ADMIN})
        self.admin = self.connexion('admin', MDP_ADMIN)

    def demonstration(self, active):
        for module in (web, server):
            correctif = patch.object(module, 'REGLAGES', replace(module.REGLAGES, demonstration=active))
            correctif.start()
            self.addCleanup(correctif.stop)

    def visiteur(self):
        statut, corps = self.appel('POST', '/api/session/visiteur', {})
        self.assertEqual(statut, 200, corps)
        return corps['utilisateur']['id'], self.jeton_de(self.derniere)

    def espace(self, jeton, titre='Essai'):
        statut, corps = self.appel('POST', '/api/spaces', {'title': titre}, jeton)
        self.assertEqual(statut, 201, corps)
        return corps['id']

    def deposer(self, jeton, numero):
        return self.appel('POST', f'/api/publications/depot?nom=a.docx&type=arrete&numero={numero}&date_acte=2026-10-14&objet=Essai',
                          docx('Arrêté d’essai'), jeton, BINAIRE)

    def ensemble(self, appel):
        """Deux appels lancés en même temps ; rend leurs résultats, triés."""
        resultats = []
        fils = [threading.Thread(target=lambda: resultats.append(appel())) for _ in range(2)]
        for fil in fils:
            fil.start()
        for fil in fils:
            fil.join(40)
        return sorted(resultats)

    @contextlib.contextmanager
    def rendez_vous(self, ecriture):
        """Les deux fils qui arrivent à cette écriture (début de la requête SQL) s'y attendent, une seconde au plus.

        Si le plafond est contrôlé hors de la transaction d'écriture, ou dans une transaction que rien ne réserve
        (BEGIN IMMEDIATE), les deux fils franchissent le contrôle, se retrouvent ici et écrivent tous les deux : le plafond
        est dépassé. S'il est bien tenu, le second attend la fin de la transaction du premier, qui repart seul après une
        seconde, et trouve alors le plafond atteint."""
        barriere, deja, connecter = threading.Barrier(2), threading.local(), sqlite3.connect

        class Connexion(sqlite3.Connection):
            def execute(self, sql, *parametres):
                if sql.lstrip().startswith(ecriture) and not getattr(deja, 'fait', False):
                    deja.fait = True
                    with contextlib.suppress(threading.BrokenBarrierError):
                        barriere.wait(1)
                return super().execute(sql, *parametres)
        with patch.object(sqlite3, 'connect', lambda chemin, *a, **k: connecter(chemin, *a, factory=Connexion, **k)):
            yield

    def compter(self, requete, *valeurs):
        with server.db() as c:
            return c.execute(requete, valeurs).fetchone()[0]

    # --- Cloisonnement ---------------------------------------------------------------------------------------------

    def test_annuaire_vide_pour_un_visiteur(self):
        _, jeton = self.visiteur()
        self.visiteur()
        self.assertEqual(self.appel('GET', '/api/annuaire', jeton=jeton), (200, {'utilisateurs': [], 'services': []}))
        self.assertIn('Alice Admin', [u['nom'] for u in self.appel('GET', '/api/annuaire', jeton=self.admin)[1]['utilisateurs']])

    def test_visiteur_ne_partage_rien(self):
        _, jeton = self.visiteur()
        _, autre = self.visiteur()
        espace = self.espace(jeton)
        statut, corps = self.appel('POST', f'/api/partages/espace/{espace}', {'cible': 'tous', 'niveau': 'edition'}, jeton)
        self.assertEqual((statut, corps['error']), (403, web.PARTAGE_VISITEUR))
        self.assertNotIn(espace, [e['id'] for e in self.appel('GET', '/api/spaces', jeton=autre)[1]])
        self.assertEqual(self.compter('SELECT COUNT(*) FROM partages'), 0)

    def test_l_administration_partage_toujours(self):
        _, jeton = self.visiteur()
        espace = self.espace(self.admin, 'Corpus de démonstration')
        self.assertEqual(self.appel('POST', f'/api/partages/espace/{espace}', {'cible': 'tous', 'niveau': 'lecture'}, self.admin)[0], 200)
        self.assertIn(espace, [e['id'] for e in self.appel('GET', '/api/spaces', jeton=jeton)[1]])

    def test_visiteur_importe_dans_ses_espaces_seulement(self):
        _, jeton = self.visiteur()
        commun = self.espace(self.admin, 'Espace commun')
        self.appel('POST', f'/api/partages/espace/{commun}', {'cible': 'tous', 'niveau': 'contribution'}, self.admin)
        statut, corps = self.televerser(jeton, commun)
        self.assertEqual((statut, corps['error']), (403, web.IMPORT_VISITEUR))
        self.assertEqual(self.televerser(jeton, self.espace(jeton))[0], 201)

    # --- Place sur le disque ---------------------------------------------------------------------------------------

    def test_plafond_de_documents_par_visiteur(self):
        _, jeton = self.visiteur()
        espace, second = self.espace(jeton), self.espace(jeton, 'Second')
        with patch.object(web, 'DOCUMENTS_PAR_VISITEUR', 2):
            reponses = [self.televerser(jeton, espace), self.televerser(jeton, second), self.televerser(jeton, espace)]
        self.assertEqual([s for s, _ in reponses], [201, 201, 429])
        self.assertEqual(reponses[2][1]['error'], web.MESSAGE_DOCUMENTS_VISITEUR)

    def test_document_de_visiteur_limite_en_taille(self):
        _, jeton = self.visiteur()
        espace = self.espace(jeton)
        avant = self.compter('SELECT COUNT(*) FROM documents')
        with patch.object(web, 'TAILLE_VISITEUR', 20):
            statut, corps = self.televerser(jeton, espace, 'x' * 21)
            self.assertEqual((statut, corps['error']), (413, web.MESSAGE_TAILLE_VISITEUR))
            self.assertEqual(self.deposer(jeton, 'A1')[0], 413)
            self.assertEqual(self.televerser(self.admin, self.espace(self.admin), 'x' * 21)[0], 201)  # l'administration : 20 Mo
        self.assertEqual(self.compter('SELECT COUNT(*) FROM documents'), avant + 1)

    def test_plafond_de_documents_pour_l_ensemble_des_visiteurs(self):
        _, premier = self.visiteur()
        _, second = self.visiteur()
        with patch.object(web, 'DOCUMENTS_VISITEURS_MAX', 1):
            self.assertEqual(self.televerser(premier, self.espace(premier))[0], 201)
            statut, corps = self.televerser(second, self.espace(second))
            self.assertEqual((statut, corps['error']), (429, web.MESSAGE_STOCKAGE_VISITEURS))
            self.assertEqual(self.televerser(self.admin, self.espace(self.admin))[0], 201)

    def test_plafond_d_actes_deposes_par_visiteur(self):
        _, jeton = self.visiteur()
        with patch.object(web, 'DOCUMENTS_PAR_VISITEUR', 2):
            reponses = [self.deposer(jeton, numero) for numero in ('A1', 'A2', 'A3')]
        self.assertEqual([s for s, _ in reponses], [201, 201, 429])
        self.assertEqual(reponses[2][1]['error'], web.MESSAGE_DOCUMENTS_VISITEUR)

    def test_texte_extrait_borne_pour_un_visiteur(self):
        # Un fichier de quelques kilo-octets peut contenir des millions de caractères une fois ouvert : le poids ne suffit pas.
        _, jeton = self.visiteur()
        espace = self.espace(jeton)
        avant = self.compter('SELECT COUNT(*) FROM documents'), self.compter('SELECT COUNT(*) FROM chunks'), len(list(server.FILES.iterdir()))
        with patch.object(web, 'TEXTE_VISITEUR', 30):
            statut, corps = self.televerser(jeton, espace, 'x' * 31)
            self.assertEqual((statut, corps['error']), (400, web.MESSAGE_TEXTE_VISITEUR))
            self.assertEqual(self.televerser(self.admin, self.espace(self.admin), 'x' * 31)[0], 201)  # l'administration : sans cette limite
        self.assertEqual((self.compter('SELECT COUNT(*) FROM documents'), len(list(server.FILES.iterdir()))), (avant[0] + 1, avant[2] + 1))
        self.assertEqual(self.televerser(jeton, espace)[0], 201)

    def test_word_demesure_refuse_avant_lecture_pour_un_visiteur(self):
        _, jeton = self.visiteur()
        bombe = docx('a' * (6 * 1024 * 1024))  # quelques kilo-octets compressés, 6 Mo de XML à lire en mémoire
        self.assertLess(len(bombe), web.TAILLE_VISITEUR)
        with patch.object(server, 'pages_importees', side_effect=AssertionError('le document ne doit pas être ouvert')):
            statut, corps = self.appel('POST', f'/api/upload/{self.espace(jeton)}?name=gros.docx', bombe, jeton, BINAIRE)
        self.assertEqual((statut, corps['error']), (400, web.MESSAGE_TEXTE_VISITEUR))

    def test_word_demesure_refuse_quel_que_soit_l_habillage_du_nom(self):
        # L'import retire du nom les espaces et fins de ligne qui l'entourent avant d'en lire l'extension : le contrôle doit
        # voir le même nom, sinon « gros.docx » suivi d'un espace serait ouvert comme un Word sans être contrôlé.
        _, jeton = self.visiteur()
        espace = self.espace(jeton)
        bombe = docx('a' * (6 * 1024 * 1024))
        for nom in ('gros.DOCX', 'gros.docx%20', 'gros.docx%09', 'gros.docx%0A', 'gros.docx%C2%A0', 'dossier/gros.docx%20'):
            with self.subTest(nom=nom), \
                    patch.object(server, 'pages_importees', side_effect=AssertionError('le document ne doit pas être ouvert')):
                statut, corps = self.appel('POST', f'/api/upload/{espace}?name={nom}', bombe, jeton, BINAIRE)
                self.assertEqual((statut, corps.get('error')), (400, web.MESSAGE_TEXTE_VISITEUR))
        self.assertEqual(self.compter('SELECT COUNT(*) FROM documents WHERE space_id=?', espace), 0)

    def test_plafonds_d_espaces_d_assistants_et_de_messages_par_visiteur(self):
        _, jeton = self.visiteur()
        assistant = {'title': 'Essai', 'description': 'd', 'instructions': 'Répondre brièvement.'}
        with patch.object(web, 'ESPACES_PAR_VISITEUR', 2), patch.object(web, 'ASSISTANTS_PAR_VISITEUR', 1), \
                patch.object(web, 'MESSAGES_PAR_VISITEUR', 4):
            espaces = [self.appel('POST', '/api/spaces', {'title': f'Espace {i}'}, jeton) for i in range(3)]
            assistants = [self.appel('POST', '/api/assistants', dict(assistant, title=f'Essai {i}'), jeton) for i in range(2)]
            # Une question « avec sources » sans passage trouvé ne passe pas par l'IA : elle n'était comptée nulle part.
            questions = [self.appel('POST', '/api/chat', {'question': 'zzzqqq', 'mode': 'sources'}, jeton) for _ in range(3)]
        self.assertEqual([s for s, _ in espaces], [201, 201, 429])
        self.assertEqual([s for s, _ in assistants], [201, 429])
        self.assertEqual([s for s, _ in questions], [200, 200, 429])
        for refus in (espaces[2], assistants[1], questions[2]):
            self.assertEqual(refus[1]['error'], web.MESSAGE_ESSAI_VISITEUR)
        self.assertEqual(self.appel('POST', '/api/spaces', {'title': 'Autre'}, self.admin)[0], 201)

    def test_seances_reservees_aux_comptes_meme_avec_la_transcription(self):
        # Démonstration complète (avec conteneurs) : une séance accepte 1 Gio d'audio, bien au-delà de ce qu'un visiteur dépose.
        _, jeton = self.visiteur()
        seance = 'a' * 32
        for methode, chemin, corps in (('GET', '/api/seances', None), ('POST', '/api/seances', {'type': 'conseil'}),
                                       ('POST', f'/api/seances/{seance}/audio?rang=0&format=webm', b'x'), ('DELETE', f'/api/seances/{seance}', None)):
            with self.subTest(methode=methode, chemin=chemin):
                statut, reponse = self.appel(methode, chemin, corps, jeton, BINAIRE if isinstance(corps, bytes) else None)
                self.assertEqual((statut, reponse['error']), (403, web.SEANCES_VISITEUR))
        self.assertIs(self.appel('GET', '/api/session', jeton=jeton)[1]['seances'], False)
        self.assertNotIn('seances', self.appel('GET', '/api/session', jeton=self.admin)[1])
        self.assertEqual(self.appel('GET', '/api/seances', jeton=self.admin)[0], 200)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM seances'), 0)

    def test_routes_qui_ecrivent_hors_des_plafonds_fermees_aux_visiteurs(self):
        _, jeton = self.visiteur()
        espace = self.espace(jeton)
        conversation = server.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True, self.compter('SELECT id FROM utilisateurs WHERE identifiant LIKE ?', 'visiteur-%'))
        message = self.compter("SELECT id FROM messages WHERE conversation_id=? AND role='assistant'", conversation['conversation'])
        for chemin, corps in (('/api/connecteurs/datagouv/import', {'espace': espace, 'jeu': 'abcdefgh', 'ressource': 'abcdefgh'}),
                              (f'/api/messages/{message}/signalement', {'commentaire': 'texte saisi par le visiteur'}),
                              ('/api/arretes/generer', {'titre': 'x'})):
            with self.subTest(chemin=chemin), patch.object(server, 'draft_arrete', side_effect=AssertionError('fermé aux visiteurs')):
                statut, reponse = self.appel('POST', chemin, corps, jeton)
                self.assertEqual((statut, reponse['error']), (403, web.RESERVE_AUX_COMPTES))
        self.assertEqual(self.compter('SELECT COUNT(*) FROM signalements'), 0)
        self.assertIs(self.appel('GET', '/api/connecteurs', jeton=jeton)[1]['datagouv'], False)

    # --- Plafonds tenus quand deux demandes arrivent ensemble -------------------------------------------------------

    def test_plafonds_de_documents_et_d_actes_reverifies_a_l_ecriture(self):
        # Deux envois peuvent franchir ensemble le premier contrôle : le second est refusé au moment d'écrire, sans rien laisser.
        _, jeton = self.visiteur()
        espace = self.espace(jeton)
        with patch.object(web, 'DOCUMENTS_PAR_VISITEUR', 1):
            self.assertEqual((self.televerser(jeton, espace)[0], self.deposer(jeton, 'A1')[0]), (201, 201))
            fichiers = len(list(server.FILES.iterdir()))
            dossiers = sorted((server.DATA / 'publication').iterdir())
            with patch.object(web, '_place_visiteur', return_value=None):  # comme si le premier contrôle venait de passer
                for statut, corps in (self.televerser(jeton, espace), self.deposer(jeton, 'A2')):
                    self.assertEqual((statut, corps['error']), (429, web.MESSAGE_DOCUMENTS_VISITEUR))
        _, autre = self.visiteur()
        ailleurs = self.espace(autre)
        with patch.object(web, 'DOCUMENTS_VISITEURS_MAX', 1), patch.object(web, '_place_visiteur', return_value=None):
            for statut, corps in (self.televerser(autre, ailleurs), self.deposer(autre, 'A1')):
                self.assertEqual((statut, corps['error']), (429, web.MESSAGE_STOCKAGE_VISITEURS))
        self.assertEqual(len(list(server.FILES.iterdir())), fichiers)
        self.assertEqual(sorted((server.DATA / 'publication').iterdir()), dossiers)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM publications'), 1)

    def test_plafonds_de_documents_et_d_actes_tenus_quand_deux_envois_arrivent_ensemble(self):
        _, jeton = self.visiteur()
        espace, numeros = self.espace(jeton), ['A1', 'A2']
        with patch.object(web, 'DOCUMENTS_PAR_VISITEUR', 1):
            with self.rendez_vous('INSERT INTO documents'):
                self.assertEqual(self.ensemble(lambda: self.televerser(jeton, espace)[0]), [201, 429])
            with self.rendez_vous('INSERT INTO publications'):
                self.assertEqual(self.ensemble(lambda: self.deposer(jeton, numeros.pop())[0]), [201, 429])

    def test_acte_d_un_visiteur_borne_en_texte(self):
        # Même limite que pour un document importé : le poids du fichier ne dit rien du texte qu'il contient.
        _, jeton = self.visiteur()
        with patch.object(web, 'TEXTE_VISITEUR', 10):
            statut, corps = self.deposer(jeton, 'A1')
            self.assertEqual((statut, corps['error']), (400, web.MESSAGE_TEXTE_VISITEUR))
            self.assertEqual(self.deposer(self.admin, 'A1')[0], 201)  # l'administration : sans cette limite
        self.assertEqual(self.compter('SELECT COUNT(*) FROM publications'), 1)
        self.assertEqual(len(list((server.DATA / 'publication').iterdir())), 1)  # le seul dossier : celui de l'acte admis

    def test_enregistrement_en_echec_ne_laisse_pas_de_projet_d_acte(self):
        dossier = server.DATA / 'arretes'
        dossier.mkdir(exist_ok=True)

        def projet(ident):
            for extension in ('.docx', '.json'):
                (dossier / (ident + extension)).write_bytes(b'x')
            return {'kind': 'arrete', 'id': ident}

        def enregistrer(artifact):
            return server.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', artifact, True, '')
        occupee = patch.object(server, 'now', side_effect=sqlite3.OperationalError('database is locked'))
        with occupee, self.assertRaises(sqlite3.OperationalError):
            enregistrer(projet('d' * 32))
        self.assertEqual(list(dossier.iterdir()), [])  # rattaché à aucun message : plus rien ne l'aurait effacé
        enregistrer(projet('e' * 32))
        with occupee, self.assertRaises(sqlite3.OperationalError):
            enregistrer({'kind': 'arrete', 'id': 'e' * 32})
        self.assertEqual(len(list(dossier.iterdir())), 2)  # déjà cité par un message : on n'y touche pas

    def test_sans_transcription_le_visiteur_lit_que_les_seances_n_existent_pas_ici(self):
        # « S'essaie avec un compte » serait faux sur un serveur sans transcription.
        _, jeton = self.visiteur()
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, orchestration='interne')):
            for methode, chemin, corps in (('GET', '/api/seances', None), ('POST', '/api/seances', {'type': 'conseil'}),
                                           ('DELETE', '/api/seances/' + 'a' * 32, None)):
                with self.subTest(methode=methode, chemin=chemin):
                    self.assertEqual(self.appel(methode, chemin, corps, jeton), (404, web.SEANCES_INDISPONIBLES))

    def test_plafonds_d_espaces_et_d_assistants_tenus_quand_deux_creations_arrivent_ensemble(self):
        _, jeton = self.visiteur()
        assistant = {'title': 'Essai', 'description': 'd', 'instructions': 'Répondre brièvement.'}
        for chemin, corps, plafond, ecriture in (('/api/spaces', {'title': 'Essai'}, 'ESPACES_PAR_VISITEUR', 'INSERT INTO spaces'),
                                                 ('/api/assistants', assistant, 'ASSISTANTS_PAR_VISITEUR', 'INSERT INTO assistants')):
            with self.subTest(chemin=chemin), patch.object(web, plafond, 1), self.rendez_vous(ecriture):
                self.assertEqual(self.ensemble(lambda: self.appel('POST', chemin, corps, jeton)[0]), [201, 429])

    def test_plafond_de_messages_tenu_quand_deux_questions_arrivent_ensemble(self):
        ident, jeton = self.visiteur()
        with patch.object(web, 'MESSAGES_PAR_VISITEUR', 2), self.rendez_vous('INSERT OR IGNORE INTO conversations'):
            statuts = self.ensemble(lambda: self.appel('POST', '/api/chat', {'question': 'zzzqqq', 'mode': 'sources'}, jeton)[0])
        self.assertEqual(statuts, [200, 429])
        self.assertEqual(self.compter('SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.proprietaire=?', ident), 2)

    def test_message_refuse_a_l_ecriture_ne_laisse_pas_de_projet_d_acte(self):
        ident, _ = self.visiteur()
        projet, dossier = 'b' * 32, server.DATA / 'arretes'
        dossier.mkdir(exist_ok=True)
        for extension in ('.docx', '.json'):
            (dossier / (projet + extension)).write_bytes(b'x')
        web._requete.compte = comptes.utilisateur(server.DB, ident)
        self.addCleanup(setattr, web._requete, 'compte', None)
        with patch.object(web, 'MESSAGES_PAR_VISITEUR', 0), self.assertRaises(web.LimiteAtteinte):
            server.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {'kind': 'arrete', 'id': projet}, True, ident)
        self.assertEqual(list(dossier.iterdir()), [])
        self.assertEqual(self.compter('SELECT COUNT(*) FROM conversations WHERE proprietaire=?', ident), 0)

    def test_plafonds_de_demandes_a_l_ia_et_de_visiteurs_tenus_quand_deux_arrivent_ensemble(self):
        ident, _ = self.visiteur()

        def tenter(action):
            def essai():
                try:
                    action()
                    return 'accepté'
                except PermissionError:
                    return 'refusé'
            return essai
        with patch.object(comptes, 'ESSAIS_PAR_VISITEUR', 1), self.rendez_vous('INSERT INTO essais_ia'):
            self.assertEqual(self.ensemble(tenter(lambda: comptes.compter_essai_ia(server.DB, ident))), ['accepté', 'refusé'])
        with patch.object(comptes, 'VISITEURS_PAR_HEURE', 2), self.rendez_vous('INSERT INTO utilisateurs'):
            self.assertEqual(self.ensemble(tenter(lambda: comptes.creer_visiteur(server.DB))), ['accepté', 'refusé'])

    def test_demandes_a_l_ia_comptees_aussi_quand_n8n_orchestre(self):
        # Avec n8n, le fournisseur est appelé par un retour de n8n, hors de la requête du visiteur : la demande se compte au départ.
        ident, jeton = self.visiteur()
        llm_backend.save(server.DATA, {'provider': 'lmstudio'})
        reponse = json.dumps({'choices': [{'message': {'content': 'Réponse.'}}]}).encode()
        with patch.object(server, 'ouvrir_sans_proxy', side_effect=lambda *a, **k: io.BytesIO(reponse)), patch.object(comptes, 'ESSAIS_PAR_VISITEUR', 1):
            statuts = [self.appel('POST', '/api/chat', {'question': 'Bonjour', 'mode': 'libre'}, jeton)[0] for _ in range(2)]
        self.assertEqual(statuts, [200, 429])
        self.assertEqual(self.compter('SELECT COUNT(*) FROM essais_ia WHERE utilisateur_id=?', ident), 1)

    def test_publication_depuis_le_registre_comptee_avec_les_actes_deposes(self):
        _, jeton = self.visiteur()
        with patch.object(web, 'DOCUMENTS_PAR_VISITEUR', 1):
            self.assertEqual(self.deposer(jeton, 'A1')[0], 201)
            with patch.object(web.publication, 'entree_du_registre', return_value={'acte_id': 'c' * 32, 'type': 'arrete', 'numero': 'A2', 'attribue_le': '2026-10-14T10:00:00', 'objet': 'Essai'}), \
                    patch.object(web.comptes, 'projet_de', return_value=True), patch.object(web.publication, 'paragraphes_du_registre', return_value=['Arrêté d’essai']):
                statut, corps = self.appel('POST', '/api/publications', {'registre': 'A2'}, jeton)
        self.assertEqual((statut, corps['error']), (429, web.MESSAGE_DOCUMENTS_VISITEUR))

    # --- Qui est un visiteur, et ce que sa recherche envoie ---------------------------------------------------------

    def test_les_identifiants_en_visiteur_sont_reserves(self):
        # Un compte nommé « visiteur-… » serait traité comme un essai : plafonné, puis effacé au bout de 24 heures.
        compte = {'identifiant': 'Visiteur-accueil', 'nom': 'Accueil', 'role': 'referent'}
        statut, corps = self.appel('POST', '/api/admin/utilisateurs', compte, self.admin)
        self.assertEqual((statut, corps['error']), (400, comptes.IDENTIFIANT_RESERVE))
        with self.assertRaisesRegex(ValueError, 'réservés'):
            comptes.creer_premier_administrateur(server.DB, {'identifiant': 'visiteur-chef', 'nom': 'Chef', 'mot_de_passe': MDP_ADMIN})
        self.assertEqual(self.appel('POST', '/api/admin/utilisateurs', dict(compte, identifiant='visiteuse'), self.admin)[0], 201)

    def test_la_recherche_d_un_visiteur_n_envoie_rien_au_service_d_ia(self):
        # La question d'une recherche par le sens part chez le fournisseur d'IA, sans compter dans les 15 demandes d'un visiteur.
        _, jeton = self.visiteur()
        commun = self.espace(self.admin, 'Corpus de démonstration')
        self.televerser(self.admin, commun)
        self.appel('POST', f'/api/partages/espace/{commun}', {'cible': 'tous', 'niveau': 'lecture'}, self.admin)
        with patch.object(semantique, 'similarites', return_value={}) as sens:
            statut, trouves = self.appel('GET', '/api/search?q=archives+mardi', jeton=jeton)
            self.assertEqual((statut, [x['title'] for x in trouves]), (200, ['note.txt']))  # par les mots, sur le serveur
            sens.assert_not_called()
            self.appel('POST', '/api/chat', {'question': 'zzzqqq', 'mode': 'sources'}, jeton)
            sens.assert_not_called()
            self.appel('GET', '/api/search?q=archives+mardi', jeton=self.admin)
            sens.assert_called_once()

    def test_l_avancement_de_l_index_ignore_les_documents_des_visiteurs(self):
        _, jeton = self.visiteur()
        self.televerser(jeton, self.espace(jeton), 'Le marché de Noël ouvre le premier samedi de décembre.')
        with patch.object(semantique, 'modele', return_value='faux-modele'), \
                patch.object(semantique, 'plonger', lambda donnees, nom, textes, question=False: [semantique.array('f', [1.0]) for _ in textes]):
            while semantique.indexer(server.DB, server.DATA, 'faux-modele', sans_visiteurs=True):
                pass
            with patch.object(semantique, 'REGLAGES', replace(semantique.REGLAGES, demonstration=True)):
                etat = semantique.etat(server.DB, server.DATA)
            ailleurs = semantique.etat(server.DB, server.DATA)
        self.assertGreater(etat['extraits'], 0)
        self.assertEqual(etat['indexes'], etat['extraits'])  # sinon l'administration verrait une indexation jamais finie
        self.assertEqual(ailleurs['extraits'], etat['extraits'] + 1)

    # --- Effacement au bout de 24 heures ---------------------------------------------------------------------------

    def test_visiteur_et_ses_contenus_effaces_apres_24_heures(self):
        ident, jeton = self.visiteur()
        espace = self.espace(jeton)
        document = self.televerser(jeton, espace)[1]['id']
        fichier = server.FILES / (document + '.txt')
        conversation = server.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True, ident)['conversation']
        acte = self.deposer(jeton, 'A1')[1]['id']
        dossier = publication.dossier(server.DATA, acte)
        self.assertTrue(fichier.is_file() and dossier.is_dir())
        maintenant = datetime.now(timezone.utc)
        self.assertEqual(server.purger_visiteurs(maintenant + timedelta(hours=23)), 0)
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=jeton)[0], 200)
        self.assertEqual(server.purger_visiteurs(maintenant + timedelta(hours=25)), 1)
        for requete, valeur in (('SELECT COUNT(*) FROM utilisateurs WHERE id=?', ident), ('SELECT COUNT(*) FROM sessions WHERE utilisateur_id=?', ident),
                                ('SELECT COUNT(*) FROM spaces WHERE id=?', espace), ('SELECT COUNT(*) FROM documents WHERE id=?', document),
                                ('SELECT COUNT(*) FROM chunks WHERE doc_id=?', document), ('SELECT COUNT(*) FROM conversations WHERE id=?', conversation),
                                ('SELECT COUNT(*) FROM publications WHERE id=?', acte)):
            with self.subTest(requete=requete):
                self.assertEqual(self.compter(requete, valeur), 0)
        self.assertFalse(fichier.exists() or dossier.exists())
        self.assertEqual(self.appel('GET', '/api/spaces', jeton=jeton)[0], 401)

    def test_l_effacement_epargne_les_autres_comptes(self):
        espace = self.espace(self.admin, 'Corpus')
        self.televerser(self.admin, espace)
        ancien, _ = self.visiteur()
        plus_tard = datetime.now(timezone.utc) + timedelta(hours=25)
        with patch.object(comptes, '_maintenant', return_value=plus_tard - timedelta(hours=1)):
            recent, jeton = self.visiteur()
        self.assertEqual(server.purger_visiteurs(plus_tard), 1)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', ancien), 0)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', recent), 1)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM documents WHERE space_id=?', espace), 1)
        self.assertEqual(self.compter("SELECT COUNT(*) FROM utilisateurs WHERE identifiant='admin'"), 1)

    def test_la_conservation_efface_les_visiteurs(self):
        ident, _ = self.visiteur()
        server.appliquer_conservation(datetime.now(timezone.utc) + timedelta(hours=25))
        self.assertEqual(self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', ident), 0)

    def test_l_effacement_emporte_seance_assistant_partages_et_objet_du_registre(self):
        ident, jeton = self.visiteur()
        commun = self.espace(self.admin, 'Commun')
        self.appel('POST', '/api/assistants', {'title': 'Essai', 'description': 'd', 'instructions': 'Répondre brièvement.'}, jeton)
        assistant = self.compter('SELECT id FROM assistants WHERE custom=1 AND proprietaire=?', ident)
        seance = seances.creer(server.DB, ident, {'type': 'conseil', 'titre': 'Conseil municipal', 'date': '2026-10-14'})
        with server.db() as c:
            c.execute("INSERT INTO partages(ressource, ressource_id, cible, cible_id, niveau) VALUES ('espace', ?, 'utilisateur', ?, 'lecture')", (commun, ident))
            c.execute("INSERT INTO partages(ressource, ressource_id, cible, cible_id, niveau) VALUES ('assistant', ?, 'tous', '', 'lecture')", (assistant,))
            c.execute("INSERT INTO registre_actes(type, annee, sequence, numero, acte_id, objet, attribue_le, attribue_par) "
                      "VALUES ('arrete', 2026, 1, 'A2026-001', ?, 'Objet saisi par le visiteur', ?, ?)", ('c' * 32, server.now(), ident))
        self.assertEqual(server.purger_visiteurs(datetime.now(timezone.utc) + timedelta(hours=25)), 1)
        for requete, valeur in (('SELECT COUNT(*) FROM seances WHERE id=?', seance), ('SELECT COUNT(*) FROM assistants WHERE id=?', assistant),
                                ('SELECT COUNT(*) FROM partages WHERE cible_id=?', ident), ('SELECT COUNT(*) FROM partages WHERE ressource_id=?', assistant)):
            with self.subTest(requete=requete):
                self.assertEqual(self.compter(requete, valeur), 0)
        with server.db() as c:  # le numéro reste (pas de trou dans la suite), l'objet saisi part
            self.assertEqual(tuple(c.execute("SELECT numero, objet FROM registre_actes WHERE acte_id=?", ('c' * 32,)).fetchone()), ('A2026-001', server.OBJET_EFFACE))
        self.assertEqual(self.compter('SELECT COUNT(*) FROM spaces WHERE id=?', commun), 1)

    def test_un_dossier_d_acte_impossible_a_effacer_est_repris_au_passage_suivant(self):
        ident, jeton = self.visiteur()
        espace = self.espace(jeton)
        acte = self.deposer(jeton, 'A1')[1]['id']
        dossier, plus_tard = publication.dossier(server.DATA, acte), datetime.now(timezone.utc) + timedelta(hours=25)
        with patch.object(server.shutil, 'rmtree', side_effect=OSError('disque occupé')):
            self.assertEqual(server.purger_visiteurs(plus_tard), 0)
        # Ce qui pouvait partir est parti : seuls restent l'acte au dossier récalcitrant et le compte qui y mène.
        self.assertEqual((self.compter('SELECT COUNT(*) FROM spaces WHERE id=?', espace), self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', ident)), (0, 1))
        # La ligne reste tant que le dossier est là : sans elle, plus rien ne mènerait à ce dossier.
        self.assertEqual((self.compter('SELECT COUNT(*) FROM publications WHERE id=?', acte), dossier.is_dir()), (1, True))
        self.assertEqual(server.purger_visiteurs(plus_tard), 1)
        self.assertEqual((self.compter('SELECT COUNT(*) FROM publications WHERE id=?', acte), dossier.exists()), (0, False))

    def test_les_demandes_a_l_ia_comptees_sont_effacees_apres_24_heures(self):
        ident, _ = self.visiteur()
        comptes.compter_essai_ia(server.DB, ident)
        maintenant = datetime.now(timezone.utc)
        server.purger_visiteurs(maintenant + timedelta(hours=23))
        self.assertEqual(self.compter('SELECT COUNT(*) FROM essais_ia'), 1)  # encore dans le plafond du jour
        server.purger_visiteurs(maintenant + timedelta(hours=25))
        self.assertEqual(self.compter('SELECT COUNT(*) FROM essais_ia'), 0)

    def test_un_effacement_en_echec_n_arrete_ni_les_suivants_ni_le_demarrage(self):
        # Plusieurs processus servent l'application : deux peuvent lancer l'effacement en même temps.
        premier, _ = self.visiteur()
        second, _ = self.visiteur()
        plus_tard = datetime.now(timezone.utc) + timedelta(hours=25)
        comptes.supprimer_visiteur(server.DB, 'compte-deja-efface-par-un-autre-processus')  # sans erreur
        vrai = comptes.supprimer_visiteur

        def capricieux(base, ident):
            if ident == premier:
                raise RuntimeError('base occupée')
            vrai(base, ident)
        with patch.object(comptes, 'supprimer_visiteur', capricieux):
            self.assertEqual(server.purger_visiteurs(plus_tard), 1)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', second), 0)
        self.assertEqual(server.purger_visiteurs(plus_tard), 1)  # le premier part au passage suivant
        self.assertEqual(self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', premier), 0)

    def test_hors_demonstration_aucun_compte_n_est_efface(self):
        # Une collectivité peut avoir nommé un vrai compte « visiteur-… » : il ne doit jamais disparaître.
        ident, _ = self.visiteur()
        self.demonstration(False)
        self.assertEqual(server.purger_visiteurs(datetime.now(timezone.utc) + timedelta(days=30)), 0)
        self.assertEqual(self.compter('SELECT COUNT(*) FROM utilisateurs WHERE id=?', ident), 1)

    # --- Recherche par le sens -------------------------------------------------------------------------------------

    def test_documents_des_visiteurs_sans_vecteurs(self):
        # Les vecteurs se calculent chez le fournisseur d'IA, sans plafond par visiteur : seuls ceux de l'administration y partent.
        _, jeton = self.visiteur()
        self.televerser(jeton, self.espace(jeton), 'Le marché de Noël ouvre le premier samedi de décembre.')
        self.televerser(self.admin, self.espace(self.admin), 'Les archives municipales ouvrent le mardi.')
        envoyes = []

        def plonger(donnees, nom, textes, question=False):
            envoyes.extend(textes)
            return [semantique.array('f', [1.0]) for _ in textes]
        with patch.object(semantique, 'plonger', plonger):
            while semantique.indexer(server.DB, server.DATA, 'faux-modele', sans_visiteurs=True):
                pass
        self.assertIn('Les archives municipales ouvrent le mardi.', envoyes)
        self.assertNotIn('Le marché de Noël ouvre le premier samedi de décembre.', envoyes)
        self.assertEqual(semantique.indexer(server.DB, server.DATA, 'faux-modele', sans_visiteurs=True), 0)

    def test_l_indexation_en_arriere_plan_laisse_les_visiteurs_de_cote_en_demonstration(self):
        for active in (True, False):
            with self.subTest(demonstration=active), patch.object(semantique, 'modele', return_value='faux-modele'), \
                    patch.object(semantique, 'REGLAGES', replace(semantique.REGLAGES, demonstration=active)), \
                    patch.object(semantique, 'indexer', return_value=0) as indexer:
                semantique._boucle(server.DB, server.DATA)
                indexer.assert_called_once_with(server.DB, server.DATA, 'faux-modele', sans_visiteurs=active)



class AvertissementsTests(Base):
    """Avant d'entrer, le visiteur lit qu'il s'agit d'une démonstration, où partent ses textes et combien de temps ils restent."""
    def demonstration(self, **reglages):
        return patch.object(web, 'REGLAGES', replace(web.REGLAGES, demonstration=True, **reglages))

    def test_la_session_nomme_le_fournisseur_d_ia_et_la_version(self):
        import llm_backend
        import territoria
        with self.demonstration():
            for fournisseur, nom in (('openrouter', 'OpenRouter'), ('mistral', 'Mistral'), ('lmstudio', '')):
                with self.subTest(fournisseur=fournisseur), patch.object(llm_backend, 'read', return_value={'provider': fournisseur}):
                    session = self.appel('GET', '/api/session')[1]
                    self.assertEqual((session['fournisseur'], session['version']), (nom, territoria.VERSION))
            with patch.object(llm_backend, 'read', side_effect=ValueError('réglages illisibles')):
                self.assertEqual(self.appel('GET', '/api/session')[1]['fournisseur'], '')
        self.assertEqual(set(self.appel('GET', '/api/session')[1]), {'mode', 'utilisateur', 'roles'})  # ailleurs : inchangé

    def test_https_impose_et_pages_hors_des_moteurs_de_recherche(self):
        with self.demonstration(origine='https://demo.exemple.fr'):
            for chemin in ('/', '/api/session', '/absent'):
                with self.subTest(chemin=chemin):
                    entetes = dict(web.handle('GET', chemin, {'Host': 'demo.exemple.fr'}, b'').headers)
                    self.assertEqual(entetes['Strict-Transport-Security'], 'max-age=31536000')
                    self.assertEqual(entetes['X-Robots-Tag'], 'noindex, nofollow')
        entetes = dict(web.handle('GET', '/', NAVIGATEUR, b'').headers)  # poste d'une collectivité, en http sur 127.0.0.1
        self.assertNotIn('Strict-Transport-Security', entetes)
        self.assertNotIn('X-Robots-Tag', entetes)
        # Hors démonstration, même en HTTPS : l'en-tête vaudrait pour tous les services du même nom d'hôte, sur tous ses ports.
        with patch.object(web, 'REGLAGES', replace(web.REGLAGES, origine='https://territoria.mairie.exemple')):
            self.assertNotIn('Strict-Transport-Security', dict(web.handle('GET', '/', {'Host': 'territoria.mairie.exemple'}, b'').headers))

    def test_l_ecran_de_connexion_porte_les_avertissements(self):
        public = Path(__file__).resolve().parents[1] / 'app' / 'public'
        script = (public / 'app.js').read_text(encoding='utf-8')
        avis = script[script.index('function avisDemonstration('):][:1500]
        for fragment in ('aucune donnée réelle', 'service d’IA en ligne', 'session.fournisseur', '24 heures',
                         'https://territoria.ovh/#mentions', 'session.version'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, avis)
        self.assertEqual(script.count('${avisDemonstration()}'), 2)  # écran de connexion et écran « en préparation »
        self.assertIn('.auth-avis{', (public / 'styles.css').read_text(encoding='utf-8'))
        bandeau = (public / 'index.html').read_text(encoding='utf-8')
        bandeau = bandeau[bandeau.index('id="bandeau-demonstration"'):][:600]
        for fragment in ('aucune donnée réelle', 'id="bandeau-fournisseur"', '24 heures'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, bandeau)
        self.assertIn("$('#bandeau-fournisseur').textContent", script)


if __name__ == '__main__':
    unittest.main()
