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
import llm_backend
import server
import web
from territoria import administration
from territoria.base import migrer

ORIGINE = 'http://127.0.0.1:9001'
NAVIGATEUR = {'Host': '127.0.0.1:9001', 'Origin': ORIGINE, 'Content-Type': 'application/json'}


class Base(unittest.TestCase):
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
        redirection = redirect_stderr(io.StringIO())
        redirection.__enter__()
        self.addCleanup(redirection.__exit__, None, None, None)

    def appel(self, methode, chemin, donnees=None, entetes=NAVIGATEUR):
        corps = b'' if donnees is None else (donnees if isinstance(donnees, bytes) else json.dumps(donnees).encode())
        reponse = web.handle(methode, chemin, entetes, corps)
        return reponse.status, json.loads(reponse.body)

    def activite(self):
        with sqlite3.connect(server.DB) as c:
            c.row_factory = sqlite3.Row
            return [dict(x) for x in c.execute('SELECT * FROM activite ORDER BY id')]


class ReglesTests(Base):
    def test_regles_par_defaut_puis_enregistrees(self):
        self.assertEqual(self.appel('GET', '/api/regles'), (200, administration.REGLES_DEFAUT))
        statut, regles = self.appel('POST', '/api/admin/regles', {'import_autorise': False, 'conservation_jours': 90})
        self.assertEqual((statut, regles['import_autorise'], regles['conservation_jours']), (200, False, 90))
        self.assertTrue(regles['assistants_perso_autorises'])
        self.assertEqual(self.appel('GET', '/api/regles')[1], regles)

    def test_valeurs_invalides_refusees(self):
        for donnees in ({'import_autorise': 'non'}, {'conservation_jours': 45}, {'conservation_jours': True}, []):
            with self.subTest(donnees=donnees):
                self.assertEqual(self.appel('POST', '/api/admin/regles', donnees)[0], 400)
        self.assertEqual(self.appel('GET', '/api/regles')[1], administration.REGLES_DEFAUT)

    def test_import_desactive_refuse_cote_serveur(self):
        self.appel('POST', '/api/admin/regles', {'import_autorise': False})
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        statut, corps = self.appel('POST', '/api/upload/interne?name=note.txt', b'Les archives ouvrent le mardi.', entetes)
        self.assertEqual(statut, 403)
        self.assertIn('désactivé', corps['error'])
        self.appel('POST', '/api/admin/regles', {'import_autorise': True})
        self.assertEqual(self.appel('POST', '/api/upload/interne?name=note.txt', b'Les archives ouvrent le mardi.', entetes)[0], 201)

    def test_depot_et_retrait_du_papier_a_en_tete(self):
        from tests.test_branding import papier_a_en_tete
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        self.assertEqual(self.appel('POST', '/api/identite/gabarit', papier_a_en_tete(), entetes), (201, {'gabarit': True}))
        self.assertTrue(self.appel('GET', '/api/identite')[1]['gabarit'])
        statut, corps = self.appel('POST', '/api/identite/gabarit', b'pas un docx', entetes)
        self.assertEqual(statut, 400)
        self.assertIn('.docx', corps['error'])
        self.assertTrue(self.appel('GET', '/api/identite')[1]['gabarit'])  # un mauvais fichier ne remplace pas le bon
        self.assertEqual(self.appel('POST', '/api/identite/gabarit/supprimer', {}), (200, {'gabarit': False}))
        self.assertEqual(administration.activite(server.DB, 5)[0]['action'], 'identite_modifiee')

    def test_assistants_personnels_desactives_refuses_cote_serveur(self):
        self.appel('POST', '/api/admin/regles', {'assistants_perso_autorises': False})
        assistant = {'title': 'Note RH', 'instructions': 'Rédige une note RH.'}
        self.assertEqual(self.appel('POST', '/api/assistants', assistant)[0], 403)
        self.appel('POST', '/api/admin/regles', {'assistants_perso_autorises': True})
        self.assertEqual(self.appel('POST', '/api/assistants', assistant)[0], 201)


class AssistantsTests(Base):
    def test_assistant_desactive_masque_et_refuse_en_conversation(self):
        statut, assistant = self.appel('POST', '/api/admin/assistants/synthese', {'actif': False})
        self.assertEqual((statut, assistant['actif']), (200, False))
        self.assertNotIn('synthese', [a['id'] for a in self.appel('GET', '/api/assistants')[1]])
        self.assertIn('synthese', [a['id'] for a in self.appel('GET', '/api/admin/assistants')[1]])
        with patch.object(server, 'call_n8n') as modele:
            statut, corps = self.appel('POST', '/api/chat', {'question': 'Synthèse ?', 'mode': 'libre', 'assistant': 'synthese'})
        modele.assert_not_called()
        self.assertEqual(statut, 400)
        self.assertIn('désactivé', corps['error'])

    def test_modification_puis_retour_a_l_origine(self):
        nouveau = {'title': 'Synthèse courte', 'description': 'En dix lignes.', 'instructions': 'Dix lignes au plus.'}
        statut, assistant = self.appel('POST', '/api/admin/assistants/synthese', nouveau)
        self.assertEqual((statut, assistant['title'], assistant['modifie']), (200, 'Synthèse courte', True))
        statut, assistant = self.appel('POST', '/api/admin/assistants/synthese/retablir', {})
        origine = next(m for m in server.TEMPLATES if m[0] == 'synthese')
        self.assertEqual((statut, assistant['title'], assistant['instructions'], assistant['modifie']),
                         (200, origine[1], origine[3], False))

    def test_fonction_arrete_ni_desactivable_ni_modifiable(self):
        self.assertEqual(self.appel('POST', '/api/admin/assistants/arrete', {'actif': False})[0], 403)
        self.assertEqual(self.appel('POST', '/api/admin/assistants/arrete', {'title': 'x', 'instructions': 'y'})[0], 403)
        self.assertTrue(next(a for a in self.appel('GET', '/api/admin/assistants')[1] if a['id'] == 'arrete')['integre'])

    def test_suppression_reservee_aux_assistants_personnalises(self):
        self.assertEqual(self.appel('DELETE', '/api/admin/assistants/synthese')[0], 403)
        ident = self.appel('POST', '/api/assistants', {'title': 'Note RH', 'instructions': 'Rédige une note RH.'})[1]['id']
        self.assertEqual(self.appel('DELETE', f'/api/admin/assistants/{ident}')[0], 200)
        self.assertEqual(self.appel('DELETE', f'/api/admin/assistants/{ident}')[0], 404)
        self.assertEqual(self.appel('POST', '/api/admin/assistants/inconnu', {'actif': False})[0], 404)

    def test_textes_vides_ou_trop_longs_refuses(self):
        for donnees in ({'title': '', 'description': '', 'instructions': 'x'}, {'title': 'x', 'instructions': 'y' * 6001},
                        {'actif': 'non'}):
            with self.subTest(donnees=donnees):
                self.assertEqual(self.appel('POST', '/api/admin/assistants/synthese', donnees)[0], 400)


class ActiviteTests(Base):
    def test_question_notee_sans_son_contenu(self):
        with patch.object(server, 'call_n8n', return_value='Brouillon confidentiel.'):
            self.appel('POST', '/api/chat', {'question': 'Question très confidentielle', 'mode': 'libre', 'assistant': 'redaction'})
        ligne = self.activite()[-1]
        self.assertEqual((ligne['action'], ligne['objet'], ligne['detail'], ligne['statut']), ('question', 'redaction', 'libre', 'ok'))
        self.assertIsInstance(ligne['duree_ms'], int)
        self.assertNotIn('confidentiel', json.dumps(self.activite(), ensure_ascii=False))

    def test_echec_du_modele_note_en_erreur(self):
        with patch.object(server, 'call_n8n', side_effect=RuntimeError('Serveur IA injoignable.')):
            self.assertEqual(self.appel('POST', '/api/chat', {'question': 'Bonjour', 'mode': 'libre'})[0], 502)
        self.assertEqual((self.activite()[-1]['action'], self.activite()[-1]['statut']), ('question', 'erreur'))

    def test_actions_d_ecriture_notees_avec_libelle(self):
        self.appel('POST', '/api/spaces', {'title': 'Urbanisme'})
        self.appel('POST', '/api/admin/regles', {'conservation_jours': 30})
        statut, lignes = self.appel('GET', '/api/admin/activite')
        self.assertEqual(statut, 200)
        self.assertEqual([x['action'] for x in lignes[:2]], ['regles_modifiees', 'espace_cree'])
        self.assertEqual(lignes[0]['libelle'], 'Règles d’usage modifiées')
        self.assertNotIn('Urbanisme', json.dumps(lignes, ensure_ascii=False))

    def test_pagination(self):
        for _ in range(120):
            administration.noter(server.DB, 'espace_cree')
        premiere = self.appel('GET', '/api/admin/activite')[1]
        suite = self.appel('GET', f"/api/admin/activite?avant={premiere[-1]['id']}")[1]
        self.assertEqual((len(premiere), len(suite)), (100, 20))

    def test_action_inconnue_refusee_et_code_invalide_neutralise(self):
        with self.assertRaises(ValueError):
            administration.noter(server.DB, 'action_libre')
        administration.noter(server.DB, 'espace_cree', objet='Nom avec espaces et accents é')
        self.assertEqual(self.activite()[-1]['objet'], 'invalide')

    def test_seance_redigee_notee(self):
        administration.noter(server.DB, 'seance_redigee', 'conseil', 'proces_verbal', duree_ms=1200)
        ligne = self.activite()[-1]
        self.assertEqual((ligne['action'], ligne['objet'], ligne['detail']), ('seance_redigee', 'conseil', 'proces_verbal'))


class StatistiquesTests(Base):
    def test_comptes_par_jour_par_assistant_et_par_mode(self):
        maintenant = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        with sqlite3.connect(server.DB) as c:
            for quand, action, objet, detail, duree in [
                ('2026-11-01T09:00:00+00:00', 'question', 'redaction', 'libre', 3000),
                ('2026-11-01T10:00:00+00:00', 'question', '', 'sources', 5000),
                ('2026-10-31T10:00:00+00:00', 'arrete', 'arrete', 'sources', 40000),
                ('2026-09-01T10:00:00+00:00', 'question', 'redaction', 'libre', 1000),  # hors période
            ]:
                c.execute('INSERT INTO activite(quand,action,objet,detail,duree_ms) VALUES (?,?,?,?,?)', (quand, action, objet, detail, duree))
        stats = administration.statistiques(server.DB, 30, maintenant)
        self.assertEqual(len(stats['par_jour']), 30)
        self.assertEqual(stats['par_jour'][-1], {'jour': '2026-11-01', 'questions': 2, 'arretes': 0})
        self.assertEqual(stats['par_jour'][-2], {'jour': '2026-10-31', 'questions': 0, 'arretes': 1})
        self.assertEqual((stats['totaux']['demandes'], stats['totaux']['duree_moyenne_ms'], stats['totaux']['duree_max_ms']),
                         (3, 16000, 40000))
        self.assertEqual(stats['par_assistant'], [{'assistant': 'arrete', 'nombre': 1}, {'assistant': 'redaction', 'nombre': 1}])
        self.assertEqual(stats['par_mode'], {'sources': 1, 'libre': 1})

    def test_route_des_statistiques(self):
        statut, stats = self.appel('GET', '/api/admin/statistiques')
        self.assertEqual((statut, stats['jours']), (200, 30))
        self.assertIsNone(stats['totaux']['duree_moyenne_ms'])  # rien de mesuré : pas de « 0 ms » trompeur
        self.assertEqual(stats['totaux']['demandes'], 0)


class ConservationTests(Base):
    def conversation(self, ident, il_y_a_jours):
        moment = (datetime.now(timezone.utc) - timedelta(days=il_y_a_jours)).isoformat(timespec='seconds')
        with server.db() as c:
            c.execute('INSERT INTO conversations(id,title,created,updated) VALUES (?,?,?,?)', (ident, 'Titre', moment, moment))
            c.execute('INSERT INTO messages(conversation_id,role,text,created) VALUES (?,?,?,?)', (ident, 'user', 'Texte', moment))

    def identifiants(self):
        with server.db() as c:
            return {r['id'] for r in c.execute('SELECT id FROM conversations')}

    def test_sans_limite_rien_n_est_supprime(self):
        self.conversation('a' * 32, 400)
        self.assertEqual(server.appliquer_conservation(), 0)
        self.assertEqual(self.identifiants(), {'a' * 32})

    def test_duree_raccourcie_appliquee_tout_de_suite(self):
        self.conversation('a' * 32, 100)
        self.conversation('b' * 32, 10)
        self.appel('POST', '/api/admin/regles', {'conservation_jours': 90})
        self.assertEqual(self.identifiants(), {'b' * 32})
        purge = [x for x in self.activite() if x['action'] == 'conversations_purgees']
        self.assertEqual(purge[-1]['detail'], '1')

    def test_activite_de_plus_d_un_an_effacee(self):
        with sqlite3.connect(server.DB) as c:
            c.execute("INSERT INTO activite(quand,action) VALUES ('2020-01-01T00:00:00+00:00','question')")
        server.appliquer_conservation()
        self.assertNotIn('2020-01-01T00:00:00+00:00', [x['quand'] for x in self.activite()])


class MigrationTests(unittest.TestCase):
    def test_questions_passees_reprises_dans_l_activite_sans_contenu(self):
        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier) / 'base.sqlite3'
            c = sqlite3.connect(base)
            with c:
                c.execute('CREATE TABLE conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)')
                c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, "
                          "sources TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL, artifact TEXT NOT NULL DEFAULT '{}')")
                c.execute("INSERT INTO conversations VALUES ('c1','Titre secret','2026-10-01','2026-10-01')")
                c.executemany('INSERT INTO messages(id,conversation_id,role,text,created,artifact) VALUES (?,?,?,?,?,?)', [
                    (1, 'c1', 'user', 'Question secrète', '2026-10-01T10:00:00+00:00', '{}'),
                    (2, 'c1', 'assistant', 'Réponse', '2026-10-01T10:00:05+00:00', '{}'),
                    (3, 'c1', 'user', 'Rédige un arrêté', '2026-10-02T10:00:00+00:00', '{}'),
                    (4, 'c1', 'assistant', 'Projet', '2026-10-02T10:01:00+00:00', '{"kind": "arrete"}')])
            c.close()
            migrer.appliquer(base)
            c = sqlite3.connect(base)
            lignes = c.execute('SELECT * FROM activite ORDER BY id').fetchall()
            c.close()
        self.assertEqual([(x[1], x[3], x[5]) for x in lignes],
                         [('2026-10-01T10:00:00+00:00', 'question', 'sources'), ('2026-10-02T10:00:00+00:00', 'arrete', 'sources')])
        self.assertNotIn('secr', json.dumps(lignes, ensure_ascii=False))


class SanteTests(Base):
    def test_sante_rassemble_volumes_services_et_ia(self):
        ia = {'modeles': [{'id': 'm', 'charge': True, 'contexte': 8192}], 'actif': {'id': 'm', 'charge': True, 'contexte': 8192}}
        with patch.object(llm_backend, 'etat_lmstudio', return_value=ia), patch.object(web, '_statut', return_value={'lm': True, 'n8n': True}):
            statut, sante = self.appel('GET', '/api/admin/sante')
        self.assertEqual(statut, 200)
        self.assertEqual(sante['comptes']['documents'], 1)  # l'article du CGFP livré au démarrage
        self.assertEqual(sante['version_base'], 15)
        self.assertEqual((sante['services'], sante['ia']['actif']['contexte']), ({'lm': True, 'n8n': True}, 8192))
        self.assertGreater(sante['disque']['total'], 0)

    def test_sante_transcription_active_puis_absente(self):
        from datetime import datetime, timedelta, timezone
        from territoria import seances
        from travaux import protocole
        transcription = lambda: administration.sante(server.DATA, server.DB)['transcription']
        self.assertEqual(transcription(), {'actif': False, 'maj': None, 'modele': None, 'modeles_ok': None, 'en_attente': 0})
        (server.DATA / 'audio').mkdir()
        battement = server.DATA / 'audio' / protocole.BATTEMENT
        recent = datetime.now(timezone.utc).isoformat(timespec='seconds')
        protocole.ecrire_json(battement, {'maj': recent, 'modele': 'large-v3-turbo', 'modeles_ok': True})
        ident = seances.creer(server.DB, '', {'type': 'reunion', 'titre': 'Bureau', 'date': '2026-11-03'})
        seances.ajouter_morceau(server.DB, server.DATA, ident, '', 0, b'audio', 'mp3')
        seances.terminer_envoi(server.DB, server.DATA, ident, '', 'large-v3-turbo')
        self.assertEqual(transcription(), {'actif': True, 'maj': recent, 'modele': 'large-v3-turbo', 'modeles_ok': True, 'en_attente': 1})
        vieux = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(timespec='seconds')
        protocole.ecrire_json(battement, {'maj': vieux, 'modele': 'large-v3-turbo', 'modeles_ok': False})
        self.assertEqual((transcription()['actif'], transcription()['modeles_ok']), (False, False))

    def test_etat_lmstudio_lit_le_contexte_charge(self):
        reponse = {'data': [{'id': 'gemma', 'type': 'vlm', 'state': 'loaded', 'loaded_context_length': 8192, 'max_context_length': 262144, 'quantization': '4bit'},
                            {'id': 'plongements', 'type': 'embeddings', 'state': 'loaded'}]}
        with patch.object(llm_backend, 'read', return_value={'provider': 'lmstudio', 'base_url': 'http://hote:1234/v1', 'model': 'gemma', 'api_key': ''}), \
                patch.object(llm_backend, '_request', return_value=reponse) as requete:
            etat = llm_backend.etat_lmstudio(server.DATA)
        self.assertEqual(requete.call_args.args[0]['base_url'], 'http://hote:1234')
        self.assertEqual([m['id'] for m in etat['modeles']], ['gemma'])
        self.assertEqual((etat['actif']['charge'], etat['actif']['contexte']), (True, 8192))
        with patch.object(llm_backend, '_request', side_effect=RuntimeError('injoignable')):
            self.assertIsNone(llm_backend.etat_lmstudio(server.DATA))


class TypeDeContenuTests(Base):
    def test_ecriture_refusee_avec_un_type_qu_une_page_tierce_peut_envoyer(self):
        for type_contenu in (None, 'text/plain', 'text/plain;charset=UTF-8', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x'):
            with self.subTest(type_contenu=type_contenu):
                entetes = {k: v for k, v in NAVIGATEUR.items() if k != 'Content-Type'}
                if type_contenu:
                    entetes['Content-Type'] = type_contenu
                self.assertEqual(self.appel('POST', '/api/admin/regles', {'import_autorise': False}, entetes)[0], 415)
        self.assertTrue(self.appel('GET', '/api/regles')[1]['import_autorise'])


if __name__ == '__main__':
    unittest.main()


class BibliothequeTests(Base):
    def test_chaque_assistant_fourni_a_une_rubrique_et_des_consignes(self):
        identifiants = [m[0] for m in server.TEMPLATES]
        self.assertEqual(len(identifiants), len(set(identifiants)))
        self.assertEqual(set(identifiants), set(server.CATEGORIE))
        for ident, titre, description, consignes in server.TEMPLATES:
            with self.subTest(assistant=ident):
                self.assertTrue(titre and description and consignes)
                self.assertLessEqual(len(consignes), 6000)

    def test_la_bibliotheque_donne_la_rubrique(self):
        self.appel('POST', '/api/assistants', {'title': 'Note RH', 'instructions': 'Rédige une note.'})
        rubriques = {a['id']: a['categorie'] for a in self.appel('GET', '/api/assistants')[1]}
        self.assertEqual((rubriques['courriel'], rubriques['discours'], rubriques['arrete']), ('Rédaction', 'Communication', 'Actes et juridique'))
        self.assertIn('Vos assistants', rubriques.values())
        self.assertEqual(len([r for r in rubriques.values() if r != 'Vos assistants']), len(server.TEMPLATES))


class MiseAJourAssistantsTests(Base):
    def test_consignes_ameliorees_portees_sauf_si_l_administration_les_a_modifiees(self):
        self.appel('POST', '/api/admin/assistants/synthese', {'title': 'Synthèse maison', 'description': '', 'instructions': 'Ma consigne.'})
        nouvelles = [(i, t, d, c + ' Version 2.') if i in ('synthese', 'resume') else (i, t, d, c) for i, t, d, c in server.TEMPLATES]
        with patch.object(server, 'TEMPLATES', nouvelles):
            server.mettre_a_jour_assistants()
            liste = {a['id']: a for a in administration.assistants(server.DB, nouvelles)}
        self.assertTrue(liste['resume']['instructions'].endswith('Version 2.'))
        self.assertFalse(liste['resume']['modifie'])
        self.assertEqual(liste['synthese']['instructions'], 'Ma consigne.')  # choix de l'administration conservé
        self.assertTrue(liste['synthese']['modifie'])

    def test_rien_ne_change_quand_les_consignes_sont_a_jour(self):
        avant = administration.assistants(server.DB, server.TEMPLATES)
        server.mettre_a_jour_assistants()
        self.assertEqual(administration.assistants(server.DB, server.TEMPLATES), avant)
