# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import outils
import reprise_pilote
import server
from territoria.base import connexion, migrer

# La base du pilote : les six tables sans les colonnes que `m0001_socle` ajoute (figée ici, indépendamment de la migration).
TABLES_PILOTE = (
    'CREATE TABLE spaces(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, created TEXT NOT NULL)',
    "CREATE TABLE documents(id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id), name TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', captured TEXT NOT NULL, created TEXT NOT NULL)",
    'CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id), page INTEGER, content TEXT NOT NULL)',
    'CREATE TABLE assistants(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, instructions TEXT NOT NULL, custom INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)',
    "CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, text TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL)",
)
TABLES_ATTENDUES = {'spaces', 'documents', 'chunks', 'assistants', 'conversations', 'messages', 'schema_migrations',
                    'parametres', 'activite', 'services', 'utilisateurs', 'sessions', 'partages', 'signalements', 'chunk_vecteurs', 'registre_actes',
                    'seances', 'seance_intervenants', 'seance_segments', 'seance_points', 'essais_ia', 'publications'}
TOUTES = ['socle', 'administration', 'comptes', 'retours', 'champs_assistants', 'vecteurs', 'ocr', 'registre', 'seances',
          'redaction', 'rapports_empreintes', 'essais_ia', 'publications', 'publications_relecture', 'publications_retrait']
ANCIENNE_DESCRIPTION = {'public': 'Textes et ressources publics importés sur ce Mac.',
                        'interne': 'Vos documents, rangés et interrogés localement.'}


def creer_base_forme_pilote(chemin):
    """Base du pilote : deux espaces, une conversation de deux messages, aucune colonne ajoutée depuis."""
    connexion_pilote = sqlite3.connect(chemin)
    try:
        with connexion_pilote:
            for creation in TABLES_PILOTE:
                connexion_pilote.execute(creation)
            for ident, titre in (('public', 'Fonds publics'), ('interne', 'Documents internes')):
                connexion_pilote.execute('INSERT INTO spaces VALUES (?,?,?,?)',
                                         (ident, titre, ANCIENNE_DESCRIPTION[ident], '2026-10-01T10:00:00+00:00'))
            connexion_pilote.execute('INSERT INTO conversations VALUES (?,?,?,?)',
                                     ('c1', 'Question sur le stationnement', '2026-10-01T10:00:00+00:00', '2026-10-01T10:05:00+00:00'))
            connexion_pilote.executemany('INSERT INTO messages(conversation_id,role,text,sources,created) VALUES (?,?,?,?,?)',
                                         [('c1', 'user', 'Bonjour', '[]', '2026-10-01T10:00:00+00:00'),
                                          ('c1', 'assistant', 'Réponse', '[]', '2026-10-01T10:05:00+00:00')])
    finally:
        connexion_pilote.close()


@contextlib.contextmanager
def lire(chemin):
    c = sqlite3.connect(chemin)
    try:
        yield c
    finally:
        c.close()


def empreinte(chemin):
    return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()


def initialiser(dossier):
    dossier = Path(dossier)
    with patch.object(server, 'DATA', dossier), patch.object(server, 'FILES', dossier / 'documents'), \
            patch.object(server, 'DB', dossier / 'territoire.sqlite3'):
        server.init()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.racine = Path(self._tmp.name)
        self.base = self.racine / 'territoire.sqlite3'


class MigrationTests(Base):
    def test_base_neuve(self):
        self.assertEqual(migrer.appliquer(self.base), TOUTES)
        with lire(self.base) as c:
            tables = {nom for (nom,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual(tables, TABLES_ATTENDUES)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 15)
            self.assertEqual(c.execute('SELECT version, nom FROM schema_migrations').fetchall(), [(1, 'socle'), (2, 'administration'), (3, 'comptes'), (4, 'retours'), (5, 'champs_assistants'), (6, 'vecteurs'), (7, 'ocr'), (8, 'registre'), (9, 'seances'), (10, 'redaction'), (11, 'rapports_empreintes'), (12, 'essais_ia'), (13, 'publications'), (14, 'publications_relecture'), (15, 'publications_retrait')])
            self.assertTrue(c.execute('SELECT applique_le FROM schema_migrations').fetchone()[0])
            conversations = {ligne[1] for ligne in c.execute('PRAGMA table_info(conversations)')}
            messages = {ligne[1] for ligne in c.execute('PRAGMA table_info(messages)')}
        self.assertTrue({'space_id', 'mode', 'assistant_id'} <= conversations)
        self.assertIn('artifact', messages)

    def test_base_pilote_conservee(self):
        creer_base_forme_pilote(self.base)
        self.assertEqual(migrer.appliquer(self.base), TOUTES)
        with lire(self.base) as c:
            self.assertEqual(c.execute('SELECT id, title, space_id, mode, assistant_id FROM conversations').fetchall(),
                             [('c1', 'Question sur le stationnement', 'all', 'sources', '')])
            self.assertEqual(c.execute('SELECT role, text, artifact FROM messages ORDER BY id').fetchall(),
                             [('user', 'Bonjour', '{}'), ('assistant', 'Réponse', '{}')])
            self.assertEqual(dict(c.execute('SELECT id, description FROM spaces')),
                             {'public': 'Textes et ressources publics importés dans votre espace.',
                              'interne': 'Vos documents, rangés dans votre espace privé.'})
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 15)

    def test_relance_sans_effet(self):
        migrer.appliquer(self.base)
        avant = empreinte(self.base)
        self.assertEqual(migrer.appliquer(self.base), [])
        self.assertEqual(empreinte(self.base), avant)

    def test_version_maximale(self):
        self.assertEqual(migrer.version_maximale(), 15)

    def test_colonnes_de_redaction(self):
        migrer.appliquer(self.base)
        with lire(self.base) as c:
            self.assertIn('redaction', [ligne[1] for ligne in c.execute('PRAGMA table_info(seances)')])
            self.assertIn('erreur', [ligne[1] for ligne in c.execute('PRAGMA table_info(seance_points)')])
            self.assertTrue({'rapport', 'empreinte'} <= {ligne[1] for ligne in c.execute('PRAGMA table_info(seance_points)')})
            index = {nom for (nom,) in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn('seance_points_seance', index)

    def test_m0010_rejouable(self):
        # Une migration doit pouvoir être rejouée (règle de migrations/__init__.py) : aucune colonne en double.
        migrer.appliquer(self.base)
        from territoria.base.migrations import m0010_redaction
        with sqlite3.connect(self.base) as c:
            m0010_redaction.appliquer(c)
            self.assertEqual([ligne[1] for ligne in c.execute('PRAGMA table_info(seances)')].count('redaction'), 1)

    def test_echec_d_une_migration_annule_seulement_celle_ci(self):
        def premiere(c):
            c.execute('CREATE TABLE a(x)')

        def cassee(c):
            c.execute('CREATE TABLE b(x)')
            raise sqlite3.OperationalError('migration cassée')

        migrations = [SimpleNamespace(VERSION=1, NOM='premiere', appliquer=premiere),
                      SimpleNamespace(VERSION=2, NOM='cassee', appliquer=cassee)]
        with patch.object(migrer, '_migrations', return_value=migrations):
            with self.assertRaises(sqlite3.OperationalError):
                migrer.appliquer(self.base)
            with lire(self.base) as c:
                tables = {nom for (nom,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(tables, {'a', 'schema_migrations'})
                self.assertEqual(c.execute('SELECT version, nom FROM schema_migrations').fetchall(), [(1, 'premiere')])
                self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 1)


class EssaiTests(Base):
    def test_essai_laisse_l_original_intact(self):
        creer_base_forme_pilote(self.base)
        avant, fichiers = empreinte(self.base), sorted(os.listdir(self.racine))
        resultat = migrer.essai(self.base)
        self.assertEqual(resultat, {'appliquees': TOUTES, 'integrite': 'ok'})
        self.assertEqual(empreinte(self.base), avant)
        self.assertEqual(sorted(os.listdir(self.racine)), fichiers)  # ni copie, ni journal à côté de l'original
        with lire(self.base) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 0)

    def test_essai_laisse_intacte_une_base_en_wal(self):
        creer_base_forme_pilote(self.base)
        self.assertTrue(connexion.activer_wal(self.base))
        avant = empreinte(self.base)
        self.assertEqual(migrer.essai(self.base), {'appliquees': TOUTES, 'integrite': 'ok'})
        self.assertEqual(empreinte(self.base), avant)  # SQLite peut laisser un -wal vide et un -shm à côté d'une base lue
        with lire(self.base) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 0)

    def test_essai_d_une_base_absente_ne_la_cree_pas(self):
        with self.assertRaises(sqlite3.Error):
            migrer.essai(self.base)
        self.assertFalse(self.base.exists())


class ConnexionTests(Base):
    def test_ouvrir_applique_les_reglages_de_la_base(self):
        c = connexion.ouvrir(self.base)
        try:
            self.assertIs(c.row_factory, sqlite3.Row)
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(c.execute('PRAGMA busy_timeout').fetchone()[0], 20000)
            self.assertEqual(c.execute('PRAGMA synchronous').fetchone()[0], 1)  # NORMAL
        finally:
            c.close()

    def test_wal_actif(self):
        self.assertTrue(connexion.activer_wal(self.base))
        with lire(self.base) as c:
            self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_repli_si_wal_refuse(self):
        requetes = []

        class FausseConnexion:
            def execute(self, requete):
                requetes.append(requete)
                return SimpleNamespace(fetchone=lambda: ('delete',))  # le support refuse le WAL

            def close(self):
                requetes.append('close')

        erreur = io.StringIO()
        with patch.object(connexion, 'ouvrir', return_value=FausseConnexion()), contextlib.redirect_stderr(erreur):
            self.assertFalse(connexion.activer_wal(self.base))
        self.assertEqual(requetes, ['PRAGMA journal_mode=WAL', 'PRAGMA journal_mode=DELETE', 'close'])
        ligne = json.loads(erreur.getvalue())
        self.assertEqual((ligne['etape'], ligne['statut'], ligne['code']), ('base:wal', 'repli', 'wal_indisponible'))


    def test_repli_si_sqlite_leve_une_erreur_au_lieu_de_refuser(self):
        requetes = []

        class FausseConnexion:
            def execute(self, requete):
                requetes.append(requete)
                if requete == 'PRAGMA journal_mode=WAL':
                    raise sqlite3.OperationalError('attempt to write a readonly database')
                return SimpleNamespace(fetchone=lambda: ('delete',))

            def close(self):
                requetes.append('close')

        erreur = io.StringIO()
        with patch.object(connexion, 'ouvrir', return_value=FausseConnexion()), contextlib.redirect_stderr(erreur):
            self.assertFalse(connexion.activer_wal(self.base))
        self.assertEqual(requetes, ['PRAGMA journal_mode=WAL', 'PRAGMA journal_mode=DELETE', 'close'])
        self.assertEqual(json.loads(erreur.getvalue())['code'], 'wal_indisponible')

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, 'root ignore les droits du dossier')
    def test_repli_si_le_dossier_interdit_de_creer_le_wal(self):
        with lire(self.base) as c:
            c.execute('CREATE TABLE t(x)')  # la base existe et reste inscriptible ; seul -wal ne peut pas être créé
        self.racine.chmod(0o500)
        self.addCleanup(self.racine.chmod, 0o700)
        erreur = io.StringIO()
        with contextlib.redirect_stderr(erreur):
            self.assertFalse(connexion.activer_wal(self.base))
        self.assertEqual(json.loads(erreur.getvalue())['code'], 'wal_indisponible')
        self.assertEqual(sorted(os.listdir(self.racine)), ['territoire.sqlite3'])
        with lire(self.base) as c:
            self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'delete')


class ServeurTests(Base):
    def test_init_active_le_wal_et_migre(self):
        initialiser(self.racine)
        with lire(self.base) as c:
            self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 15)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM assistants').fetchone()[0], len(server.TEMPLATES))

    def test_reprise_du_pilote_puis_migration(self):
        pilote, cible = self.racine / 'pilote' / 'data', self.racine / 'donnees'
        pilote.mkdir(parents=True)
        creer_base_forme_pilote(pilote / 'territoire.sqlite3')
        reprise = reprise_pilote.importer(pilote, cible)
        self.assertEqual((reprise['espaces'], reprise['conversations'], reprise['messages']), (2, 1, 2))
        with lire(cible / 'territoire.sqlite3') as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 0)  # la copie est encore celle du pilote

        initialiser(cible)

        with lire(cible / 'territoire.sqlite3') as c:
            comptes = [c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('spaces', 'conversations', 'messages')]
            self.assertEqual(comptes, [2, 1, 2])
            self.assertEqual(c.execute('SELECT version, nom FROM schema_migrations').fetchall(), [(1, 'socle'), (2, 'administration'), (3, 'comptes'), (4, 'retours'), (5, 'champs_assistants'), (6, 'vecteurs'), (7, 'ocr'), (8, 'registre'), (9, 'seances'), (10, 'redaction'), (11, 'rapports_empreintes'), (12, 'essais_ia'), (13, 'publications'), (14, 'publications_relecture'), (15, 'publications_retrait')])
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 15)
            self.assertEqual(c.execute('SELECT DISTINCT artifact FROM messages').fetchall(), [('{}',)])
            self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'wal')


class OutilsMigrerTests(Base):
    def executer(self, *argv):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(io.StringIO()):
            code = outils.main(['migrer', *argv])
        return code, sortie.getvalue()

    def test_valeurs_par_defaut(self):
        args = outils.construire_parseur().parse_args(['migrer'])
        self.assertEqual((args.base, args.essai), (Path('/donnees/territoire.sqlite3'), False))

    def test_migrer(self):
        creer_base_forme_pilote(self.base)
        self.assertEqual(self.executer('--base', str(self.base)), (0, 'ETAPE migrer : OK\n'))
        with lire(self.base) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 15)

    def test_essai_ne_modifie_pas_la_base(self):
        creer_base_forme_pilote(self.base)
        avant = empreinte(self.base)
        self.assertEqual(self.executer('--essai', '--base', str(self.base)), (0, 'ETAPE migrer : OK\n'))
        self.assertEqual(empreinte(self.base), avant)

    def test_base_absente(self):
        for options in ((), ('--essai',)):
            self.assertEqual(self.executer(*options, '--base', str(self.base)), (1, 'ETAPE migrer : ECHEC migration_impossible\n'))
            self.assertFalse(self.base.exists())  # une faute de frappe ne crée pas de base vide

    def test_base_illisible(self):
        self.base.write_bytes(b'ceci n\'est pas une base SQLite' * 20)
        for options in ((), ('--essai',)):
            self.assertEqual(self.executer(*options, '--base', str(self.base)), (1, 'ETAPE migrer : ECHEC migration_impossible\n'))


if __name__ == '__main__':
    unittest.main()
