# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / 'app'))
import config
import server as app
import web

ORIGINE = 'http://127.0.0.1:9001'
NAVIGATEUR = {'Host': '127.0.0.1:9001', 'Origin': ORIGINE}
PROJET = 'a' * 32


class SuppressionBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        donnees = Path(self.tmp.name)
        for cible, nom, valeur in [
            (web, 'REGLAGES', replace(config.REGLAGES, origine=ORIGINE, service_token='jeton-test')),
            (app, 'DATA', donnees), (app, 'FILES', donnees / 'documents'), (app, 'DB', donnees / 'db.sqlite'),
        ]:
            correctif = patch.object(cible, nom, valeur)
            correctif.start()
            self.addCleanup(correctif.stop)
        app.init()
        self.err = io.StringIO()
        redirection = redirect_stderr(self.err)
        redirection.__enter__()
        self.addCleanup(redirection.__exit__, None, None, None)

    def conversation_sur_document(self):
        with patch.object(app, 'call_n8n', return_value='Le mardi [1].'):
            return app.chat({'question': 'archives mardi', 'space': 'interne'})['conversation']

    def conversation_avec_projet(self, artefact, fichiers=True):
        racine = app.DATA / 'arretes'
        racine.mkdir(exist_ok=True)
        if fichiers:
            (racine / (PROJET + '.docx')).write_bytes(b'word')
            (racine / (PROJET + '.json')).write_text('{}', encoding='utf-8')
        return app.save_chat('', 'Rédige un arrêté', 'Voici le projet.', [], 'libre', 'all', 'arrete', artefact, True)['conversation']

    def base_contient(self, texte):
        """Cherche le texte dans les octets du fichier SQLite et de son journal WAL, y compris les pages libérées."""
        wal = Path(f'{app.DB}-wal')
        return texte.encode() in app.DB.read_bytes() + (wal.read_bytes() if wal.exists() else b'')

    def compte(self, table, colonne, valeur):
        with app.db() as c:
            return c.execute(f'SELECT COUNT(*) FROM {table} WHERE {colonne}=?', (valeur,)).fetchone()[0]


class SuppressionDocumentTests(SuppressionBase):
    def test_document_supprime_efface_fichier_chunks_et_extraits(self):
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        conv = self.conversation_sur_document()
        self.assertTrue(app.supprimer_document(ident))
        with app.db() as c:
            self.assertIsNone(c.execute('select 1 from chunks where doc_id=?', (ident,)).fetchone())
            self.assertIsNone(c.execute('select 1 from documents where id=?', (ident,)).fetchone())
            sources = c.execute("select sources from messages where role='assistant' and conversation_id=?", (conv,)).fetchone()[0]
        self.assertNotIn('mardi', sources)
        self.assertEqual(list(app.FILES.glob(ident + '*')), [])

    def test_extraits_remplaces_par_des_valeurs_neutres(self):
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        conv = self.conversation_sur_document()
        app.supprimer_document(ident)
        with app.db() as c:
            sources = json.loads(c.execute("select sources from messages where role='assistant' and conversation_id=?", (conv,)).fetchone()[0])
        self.assertEqual(len(sources), 1)
        self.assertEqual((sources[0]['id'], sources[0]['excerpt'], sources[0]['url'], sources[0]['title']),
                         (ident, '', '', 'Document supprimé'))
        self.assertEqual(sources[0]['space'], 'Documents internes')

    def test_les_autres_sources_du_message_sont_conservees(self):
        gardee = app.add_doc('interne', 'garde.txt', 'Les archives ouvrent le mardi.'.encode())
        perdue = app.add_doc('interne', 'perdu.txt', 'Les archives ferment le dimanche.'.encode())
        with patch.object(app, 'call_n8n', return_value='Le mardi [1] et le dimanche [2].'):
            conv = app.chat({'question': 'archives mardi dimanche', 'space': 'interne'})['conversation']
        self.assertTrue(app.supprimer_document(perdue))
        with app.db() as c:
            sources = json.loads(c.execute("select sources from messages where role='assistant' and conversation_id=?", (conv,)).fetchone()[0])
        par_id = {s['id']: s for s in sources}
        self.assertEqual(par_id[perdue]['title'], 'Document supprimé')
        self.assertEqual(par_id[gardee]['title'], 'garde.txt')
        self.assertIn('mardi', par_id[gardee]['excerpt'])
        self.assertTrue((app.FILES / (gardee + '.txt')).is_file())

    def test_document_supprime_n_est_plus_trouve(self):
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        self.assertTrue(app.search('archives mardi', 'interne'))
        app.supprimer_document(ident)
        self.assertEqual(app.search('archives mardi', 'interne'), [])

    def test_contenu_efface_de_la_base_elle_meme(self):
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        self.conversation_sur_document()
        self.assertTrue(self.base_contient('Les archives ouvrent le mardi.'))
        app.supprimer_document(ident)
        self.assertFalse(self.base_contient('Les archives ouvrent le mardi.'))

    def test_document_inconnu(self):
        self.assertFalse(app.supprimer_document('0' * 32))
        self.assertFalse(app.supprimer_document(''))

    def test_seconde_suppression_renvoie_faux(self):
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        self.assertTrue(app.supprimer_document(ident))
        self.assertFalse(app.supprimer_document(ident))


class SuppressionConversationTests(SuppressionBase):
    def test_conversation_supprimee_efface_projet_arrete(self):
        conv = self.conversation_avec_projet({'kind': 'arrete', 'id': PROJET, 'inputs': {}})
        self.assertTrue(app.supprimer_conversation(conv))
        self.assertEqual(list((app.DATA / 'arretes').glob(PROJET + '*')), [])
        self.assertEqual(self.compte('conversations', 'id', conv), 0)
        self.assertEqual(self.compte('messages', 'conversation_id', conv), 0)

    def test_plusieurs_projets_de_la_meme_conversation(self):
        autre = 'b' * 32
        conv = self.conversation_avec_projet({'kind': 'arrete', 'id': PROJET})
        (app.DATA / 'arretes' / (autre + '.docx')).write_bytes(b'word')
        app.save_chat(conv, 'Modifie le premier article', 'Voici la version 2.', [], 'libre', 'all', 'arrete', {'kind': 'arrete', 'id': autre}, True)
        self.assertTrue(app.supprimer_conversation(conv))
        self.assertEqual(list((app.DATA / 'arretes').iterdir()), [])

    def test_identifiant_hors_format_ne_supprime_rien(self):
        fichier = app.DATA / 'secret.docx'
        fichier.write_bytes(b'a conserver')
        for faux in ('../secret', 'a' * 31, 'a' * 33, ''):
            with self.subTest(identifiant=faux):
                conv = self.conversation_avec_projet({'kind': 'arrete', 'id': faux}, fichiers=False)
                self.assertTrue(app.supprimer_conversation(conv))
                self.assertTrue(fichier.is_file())

    def test_seuls_les_artefacts_arrete_sont_concernes(self):
        conv = self.conversation_avec_projet({'kind': 'arrete_clarification', 'id': PROJET})
        self.assertTrue(app.supprimer_conversation(conv))
        self.assertEqual(len(list((app.DATA / 'arretes').glob(PROJET + '*'))), 2)

    def test_artefacts_illisibles_ne_bloquent_pas_la_suppression(self):
        conv = self.conversation_avec_projet({'kind': 'arrete', 'id': PROJET})
        with app.db() as c:
            c.execute("INSERT INTO messages(conversation_id,role,text,created,artifact) VALUES (?,?,?,?,?)", (conv, 'user', 'x', app.now(), 'pas du json'))
            c.execute("INSERT INTO messages(conversation_id,role,text,created,artifact) VALUES (?,?,?,?,?)", (conv, 'user', 'x', app.now(), '["liste"]'))
        self.assertTrue(app.supprimer_conversation(conv))
        self.assertEqual(self.compte('messages', 'conversation_id', conv), 0)
        self.assertEqual(list((app.DATA / 'arretes').glob(PROJET + '*')), [])

    def test_projet_deja_absent_n_empeche_pas_la_suppression(self):
        conv = self.conversation_avec_projet({'kind': 'arrete', 'id': PROJET}, fichiers=False)
        self.assertTrue(app.supprimer_conversation(conv))

    def test_les_autres_conversations_sont_intactes(self):
        gardee = app.save_chat('', 'Question gardée', 'Réponse gardée.', [], 'libre', 'all', '', {}, True)['conversation']
        supprimee = app.save_chat('', 'Question perdue', 'Réponse perdue.', [], 'libre', 'all', '', {}, True)['conversation']
        self.assertTrue(app.supprimer_conversation(supprimee))
        self.assertEqual(self.compte('conversations', 'id', gardee), 1)
        self.assertEqual(self.compte('messages', 'conversation_id', gardee), 2)

    def test_conversation_inconnue(self):
        self.assertFalse(app.supprimer_conversation('0' * 32))

    def test_echec_de_suppression_d_un_fichier_garde_la_conversation(self):
        conv = self.conversation_avec_projet({'kind': 'arrete', 'id': PROJET})
        with patch.object(Path, 'unlink', side_effect=PermissionError('refusé')):
            with self.assertRaises(PermissionError):
                app.supprimer_conversation(conv)
        self.assertEqual(self.compte('conversations', 'id', conv), 1)
        self.assertEqual(self.compte('messages', 'conversation_id', conv), 2)
        self.assertTrue(app.supprimer_conversation(conv))
        self.assertEqual(list((app.DATA / 'arretes').glob(PROJET + '*')), [])

    def test_contenu_efface_de_la_base_elle_meme(self):
        conv = app.save_chat('', 'Question très confidentielle', 'Réponse très confidentielle.', [], 'libre', 'all', '', {}, True)['conversation']
        self.assertTrue(self.base_contient('Question très confidentielle'))
        app.supprimer_conversation(conv)
        self.assertFalse(self.base_contient('Question très confidentielle'))
        self.assertFalse(self.base_contient('Réponse très confidentielle'))


class PurgeJournalTests(SuppressionBase):
    def lecteur_avec_instantane(self):
        """Une connexion qui garde une transaction de lecture ouverte, puis une écriture validée après son instantané."""
        lecteur = app.db()
        self.addCleanup(lecteur.close)
        lecteur.execute('BEGIN')
        lecteur.execute('SELECT COUNT(*) FROM messages').fetchall()
        with app.db() as c:
            c.execute('UPDATE spaces SET title=title')
            c.execute("INSERT INTO spaces(id,title,description,created) VALUES ('autre','Autre','Espace de test','2026-10-01T10:00:00+00:00')")
        return lecteur

    def test_purge_reussie(self):
        self.assertTrue(app.purger_journal())
        self.assertNotIn('wal_occupe', self.err.getvalue())

    def test_purge_incomplete_si_un_lecteur_garde_son_instantane(self):
        lecteur = self.lecteur_avec_instantane()
        with patch.object(app, 'time') as horloge:
            self.assertFalse(app.purger_journal())
        horloge.sleep.assert_called_once_with(0.2)  # un seul nouvel essai, après une courte pause
        ligne = json.loads(self.err.getvalue().strip().splitlines()[-1])
        self.assertEqual((ligne['etape'], ligne['statut'], ligne['code']), ('base:purge', 'incomplete', 'wal_occupe'))
        lecteur.rollback()
        self.assertTrue(app.purger_journal())

    def test_purge_reussit_au_second_essai(self):
        lecteur = self.lecteur_avec_instantane()
        with patch.object(app, 'time') as horloge:
            horloge.sleep.side_effect = lambda _: lecteur.rollback()  # le lecteur termine pendant la pause
            self.assertTrue(app.purger_journal())
        self.assertNotIn('wal_occupe', self.err.getvalue())

    def test_l_effacement_reste_valide_quand_la_purge_est_incomplete(self):
        conv = app.save_chat('', 'Question très confidentielle', 'Réponse très confidentielle.', [], 'libre', 'all', '', {}, True)['conversation']
        lecteur = self.lecteur_avec_instantane()
        with patch.object(app, 'time'):
            self.assertTrue(app.supprimer_conversation(conv))
        self.assertEqual(self.compte('conversations', 'id', conv), 0)
        self.assertIn('wal_occupe', self.err.getvalue())
        lecteur.rollback()


class SuppressionHttpTests(SuppressionBase):
    def corps(self, reponse):
        return json.loads(reponse.body)

    def wsgi(self, methode, chemin, **environ):
        env = {}
        setup_testing_defaults(env)
        env.update(REQUEST_METHOD=methode, PATH_INFO=chemin, HTTP_HOST='127.0.0.1:9001', HTTP_ORIGIN=ORIGINE)
        env.update(environ)
        recu = {}
        corps = b''.join(web.application(env, lambda statut, entetes: recu.update(statut=statut)))
        return recu['statut'], corps

    def test_suppression_d_une_conversation(self):
        conv = app.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True)['conversation']
        r = web.handle('DELETE', f'/api/conversations/{conv}', NAVIGATEUR, b'')
        self.assertEqual((r.status, self.corps(r)), (200, {'supprime': True}))
        self.assertEqual(self.compte('conversations', 'id', conv), 0)
        r = web.handle('DELETE', f'/api/conversations/{conv}', NAVIGATEUR, b'')
        self.assertEqual((r.status, self.corps(r)), (404, {'error': 'Introuvable.'}))
        self.assertEqual(web.handle('GET', f'/api/conversations/{conv}', NAVIGATEUR, b'').status, 404)

    def test_suppression_d_un_document(self):
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        r = web.handle('DELETE', f'/api/documents/{ident}', NAVIGATEUR, b'')
        self.assertEqual((r.status, self.corps(r)), (200, {'supprime': True}))
        self.assertEqual(web.handle('GET', f'/api/documents/{ident}/file', NAVIGATEUR, b'').status, 404)
        r = web.handle('DELETE', f'/api/documents/{ident}', NAVIGATEUR, b'')
        self.assertEqual((r.status, self.corps(r)), (404, {'error': 'Introuvable.'}))
        self.assertNotIn(ident, [d['id'] for d in self.corps(web.handle('GET', '/api/documents', NAVIGATEUR, b''))])

    def test_identifiants_inconnus_ou_mal_formes(self):
        for chemin in ('/api/conversations/' + '0' * 32, '/api/conversations/nimporte-quoi', '/api/documents/inconnu'):
            with self.subTest(chemin=chemin):
                r = web.handle('DELETE', chemin, NAVIGATEUR, b'')
                self.assertEqual((r.status, self.corps(r)), (404, {'error': 'Introuvable.'}))

    def test_origine_tierce_refusee_et_rien_n_est_supprime(self):
        conv = app.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True)['conversation']
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        for entetes in ({'Host': '127.0.0.1:9001', 'Origin': 'https://exemple.invalid'}, {'Host': '127.0.0.1:9001'},
                        {'Host': 'exemple.invalid', 'Origin': ORIGINE}):
            for chemin in (f'/api/conversations/{conv}', f'/api/documents/{ident}'):
                with self.subTest(entetes=entetes, chemin=chemin):
                    r = web.handle('DELETE', chemin, entetes, b'')
                    self.assertEqual((r.status, self.corps(r)), (403, {'error': 'Accès refusé.'}))
        self.assertEqual(self.compte('conversations', 'id', conv), 1)
        self.assertTrue((app.FILES / (ident + '.txt')).is_file())

    def test_autres_chemins_restent_non_autorises_en_suppression(self):
        for chemin in ('/api/spaces', '/api/conversations', '/api/documents', f'/api/documents/{PROJET}/file'):
            with self.subTest(chemin=chemin):
                self.assertEqual(web.handle('DELETE', chemin, NAVIGATEUR, b'').status, 405)

    def test_erreur_locale_en_500_sans_detail(self):
        conv = app.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True)['conversation']
        with patch.object(app, 'supprimer_conversation', side_effect=OSError('/chemin/secret')):
            r = web.handle('DELETE', '/api/conversations/' + conv, NAVIGATEUR, b'')
        self.assertEqual((r.status, self.corps(r)), (500, {'error': 'Erreur locale pendant le traitement.'}))

    def test_journal_normalise_la_route_sans_identifiant(self):
        conv = app.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True)['conversation']
        ident = app.add_doc('interne', 'note.txt', 'Les archives ouvrent le mardi.'.encode())
        with redirect_stderr(io.StringIO()) as err:
            web.handle('DELETE', f'/api/conversations/{conv}', NAVIGATEUR, b'')
            web.handle('DELETE', f'/api/documents/{ident}', NAVIGATEUR, b'')
        lignes = [json.loads(ligne) for ligne in err.getvalue().splitlines()]
        self.assertEqual([x['etape'] for x in lignes], ['delete:/api/conversations/:id', 'delete:/api/documents/:id'])
        self.assertNotIn(conv, err.getvalue())
        self.assertNotIn(ident, err.getvalue())

    def test_wsgi_relaie_la_methode_delete(self):
        conv = app.save_chat('', 'Question', 'Réponse.', [], 'libre', 'all', '', {}, True)['conversation']
        statut, corps = self.wsgi('DELETE', f'/api/conversations/{conv}')
        self.assertEqual((statut, json.loads(corps)), ('200 OK', {'supprime': True}))
        statut, corps = self.wsgi('DELETE', f'/api/conversations/{conv}')
        self.assertEqual((statut, json.loads(corps)), ('404 Not Found', {'error': 'Introuvable.'}))
        statut, _ = self.wsgi('DELETE', f'/api/conversations/{conv}', HTTP_ORIGIN='https://exemple.invalid')
        self.assertEqual(statut, '403 Forbidden')


if __name__ == '__main__':
    unittest.main()
