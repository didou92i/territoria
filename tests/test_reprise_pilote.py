# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import contextlib
import gc
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen.canvas import Canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arretes
import demo
import outils
import reprise_pilote
import sauvegarde
import server
from territoria import fonds_publics
from config import REGLAGES

REFERENCE = 'reference-stationnement-2023-13.pdf'
SHA_REFERENCE = '41bda5d664a888019342c539a8e81692338bfd265d89c8df6806ca856b50d190'
DOSSIERS = ('documents', 'bibliotheque-arretes', 'modeles', 'ressources-juridiques', 'arretes', 'identite')


def sha(contenu):
    return hashlib.sha256(contenu).hexdigest()


def mode(chemin):
    return stat.S_IMODE(os.lstat(chemin).st_mode)


def empreintes(racine):
    """{chemin relatif: (type, contenu ou lien, droits, date)} de tout l'arbre, sans suivre les liens."""
    racine, resultat = Path(racine), {}
    for dossier, noms, fichiers in os.walk(racine):
        for nom in (*noms, *fichiers):
            chemin = Path(dossier) / nom
            st = os.lstat(chemin)
            if stat.S_ISREG(st.st_mode):
                contenu = sha(chemin.read_bytes())
            else:
                contenu = os.readlink(chemin) if stat.S_ISLNK(st.st_mode) else None
            resultat[chemin.relative_to(racine).as_posix()] = (stat.S_IFMT(st.st_mode), contenu, stat.S_IMODE(st.st_mode), st.st_mtime_ns)
    return resultat


def fichiers(racine):
    return {p.relative_to(racine).as_posix() for p in Path(racine).rglob('*') if p.is_file()}


def pdf(texte):
    sortie = io.BytesIO()
    page = Canvas(sortie)
    page.drawString(72, 700, texte)
    page.save()
    return sortie.getvalue()


def initialiser(dossier, articles_livres=True):
    """Base et documents d'une installation neuve : `server.init()` dans `dossier`. Le pilote, lui, date d'avant le
    versement des articles de Légifrance dans le fonds public (`articles_livres=False`)."""
    dossier = Path(dossier)
    with patch.object(server, 'DATA', dossier), patch.object(server, 'FILES', dossier / 'documents'), \
            patch.object(server, 'DB', dossier / 'territoire.sqlite3'), contextlib.ExitStack() as pile:
        if not articles_livres:
            pile.enter_context(patch.object(fonds_publics, 'articles', return_value=()))
        server.init()
    gc.collect()  # `init()` laisse sa connexion ouverte jusqu'au ramasse-miettes : en WAL, elle garderait un -wal à côté de la base


@contextlib.contextmanager
def base(dossier):
    connexion = sqlite3.connect(Path(dossier) / 'territoire.sqlite3')
    try:
        with connexion:
            yield connexion
    finally:
        connexion.close()


def ajouter_conversation(dossier, titre='Question sur le stationnement', messages=2, espace='all'):
    with base(dossier) as c:
        c.execute('INSERT INTO conversations(id,title,created,updated,space_id,mode,assistant_id) VALUES (?,?,?,?,?,?,?)',
                  ('c1', titre, '2026-10-01T10:00:00+00:00', '2026-10-01T10:05:00+00:00', espace, 'sources', ''))
        for i in range(messages):
            c.execute('INSERT INTO messages(conversation_id,role,text,sources,created,artifact) VALUES (?,?,?,?,?,?)',
                      ('c1', 'user' if i % 2 == 0 else 'assistant', f'Texte {i}', '[]', '2026-10-01T10:00:00+00:00', '{}'))


def ajouter_espace(dossier, ident='mairie', titre='Mairie'):
    with base(dossier) as c:
        c.execute('INSERT INTO spaces(id,title,description,created) VALUES (?,?,?,?)', (ident, titre, 'Espace créé par un test', '2026-10-01T10:00:00+00:00'))


def ajouter_document(dossier, ident='note-interne', espace='interne'):
    """Ligne de `documents` et son fichier (contenu `x`) dans `documents/`."""
    ecrire(Path(dossier) / 'documents' / (ident + '.txt'), b'x')
    with base(dossier) as c:
        c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',
                  (ident, espace, 'Note interne', ident + '.txt', sha(b'x'), '', '2026-10-01', '2026-10-01T10:00:00+00:00'))


def ajouter_assistant(dossier):
    with base(dossier) as c:
        c.execute('INSERT INTO assistants(id,title,description,instructions,custom) VALUES (?,?,?,?,1)', ('perso', 'Mon assistant', 'Description', 'Consigne'))


def ecrire_bibliotheque(dossier, nombre=2):
    racine = Path(dossier) / 'bibliotheque-arretes'
    (racine / 'pdf').mkdir(parents=True)
    entrees = []
    for i in range(nombre):
        brut = pdf(f'Arrêté fictif numéro {i}')
        nom = f'arrete-{i}.pdf'
        (racine / 'pdf' / nom).write_bytes(brut)
        entrees.append({'id': f'arrete-{i}', 'title': f'Arrêté fictif {i}', 'filename': nom, 'sha256': sha(brut),
                        'size': len(brut), 'url': '', 'modified': '2026-01-01T00:00:00+00:00'})
    (racine / 'manifest.json').write_text(json.dumps({'files': entrees}), encoding='utf-8')
    (racine / 'index.json').write_text(json.dumps({'created': '', 'documents': [], 'errors': []}), encoding='utf-8')


def ecrire(chemin, contenu):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu if isinstance(contenu, bytes) else contenu.encode('utf-8'))


def construire_pilote(pilote):
    """Faux dossier data/ du pilote : base `server.init()` avec une conversation, bibliothèque de deux PDF, modèle,
    ressources, arrêtés, identité, archives de déploiement, journal et configuration OpenRouter avec clé."""
    initialiser(pilote, articles_livres=False)
    ajouter_conversation(pilote)
    ajouter_document(pilote)
    ecrire_bibliotheque(pilote)
    ecrire(pilote / 'modeles' / REFERENCE, pdf('Arrêté de référence'))
    ecrire(pilote / 'ressources-juridiques' / 'legifrance.json', '{"as_of": "2026-10-01"}')
    ecrire(pilote / 'arretes' / 'projet-1.docx', b'PK-docx')
    ecrire(pilote / 'arretes' / 'projet-1.json', '{"numero": "1"}')
    ecrire(pilote / 'identite' / 'profil.json', '{"collectivite": "Commune fictive"}')
    ecrire(pilote / 'deployment' / 'secret.txt', 'archive privée')
    ecrire(pilote / 'server.log', 'ligne de journal\n')
    ecrire(pilote / 'configuration' / 'ia.json', json.dumps({
        'provider': 'openrouter', 'api_key': 'sk-or-v1-cle-secrete',
        'profiles': {'openrouter': {'api_key': 'sk-or-v1-cle-secrete', 'model': 'openai/gpt'}}}))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.racine = Path(self._tmp.name)
        self.pilote = self.racine / 'pilote' / 'data'
        self.cible = self.racine / 'donnees'
        construire_pilote(self.pilote)

    def importer(self, **options):
        return reprise_pilote.importer(self.pilote, self.cible, **options)

    def corpus_demo(self, dossier):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(demo.installer(dossier))


class ImportTests(Base):
    def test_comptes_et_empreintes(self):
        r = self.importer()
        self.assertEqual((r['conversations'], r['pdf_conformes']), (1, 2))
        self.assertEqual(r, {'espaces': 2, 'documents': 2, 'conversations': 1, 'messages': 2, 'pdf_conformes': 2,
                             'reference_conforme': False})  # le faux modèle n'a pas l'empreinte du modèle réel

    def test_la_base_est_copiee_a_l_identique(self):
        self.importer()
        self.assertEqual(self.dump(self.pilote / 'territoire.sqlite3'), self.dump(self.cible / 'territoire.sqlite3'))

    def dump(self, chemin):
        connexion = sqlite3.connect(f'{Path(chemin).resolve().as_uri()}?mode=ro&immutable=1', uri=True)
        try:
            return list(connexion.iterdump())
        finally:
            connexion.close()

    def test_les_six_dossiers_sont_copies_octet_pour_octet(self):
        self.importer()
        for nom in DOSSIERS:
            attendu = {p: sha((self.pilote / p).read_bytes()) for p in fichiers(self.pilote) if p.split('/')[0] == nom
                       and p != 'modeles/manifest.json'}
            self.assertTrue(attendu or nom == 'modeles', nom)
            for chemin, empreinte in attendu.items():
                self.assertEqual(sha((self.cible / chemin).read_bytes()), empreinte, chemin)

    def test_deploiement_journal_et_cle_non_importes(self):
        self.importer()
        self.assertFalse((self.cible / 'deployment').exists())
        self.assertFalse((self.cible / 'server.log').exists())
        self.assertNotIn('openrouter', (self.cible / 'configuration' / 'ia.json').read_text())
        self.assertFalse(any('secret' in p or p.endswith('.log') for p in fichiers(self.cible)))
        for chemin in fichiers(self.cible):
            self.assertNotIn(b'sk-or-v1', (self.cible / chemin).read_bytes(), chemin)

    def test_configuration_ia_locale_sans_cle_ni_profils(self):
        self.importer()
        lu = json.loads((self.cible / 'configuration' / 'ia.json').read_text(encoding='utf-8'))
        self.assertEqual(lu, {'provider': 'lmstudio', 'base_url': REGLAGES.llm_url, 'model': REGLAGES.modele, 'api_key': ''})

    def test_manifeste_du_modele_de_reference(self):
        self.importer()
        lu = json.loads((self.cible / 'modeles' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(lu, {'reference': REFERENCE, 'sha256': SHA_REFERENCE, 'trame': 'stationnement-2023-13'})

    def test_reference_conforme_quand_le_pdf_a_l_empreinte_attendue(self):
        with patch.object(reprise_pilote, 'REFERENCE_SHA256', sha((self.pilote / 'modeles' / REFERENCE).read_bytes())):
            r = self.importer()
        self.assertTrue(r['reference_conforme'])
        self.assertEqual(arretes.verify_reference(self.cible), sha((self.pilote / 'modeles' / REFERENCE).read_bytes()))

    def test_reference_modifiee_ou_absente_est_signalee(self):
        (self.pilote / 'modeles' / REFERENCE).unlink()
        self.assertFalse(self.importer()['reference_conforme'])

    def test_pdf_modifie_ou_manquant_non_conforme(self):
        racine = self.pilote / 'bibliotheque-arretes'
        manifeste = json.loads((racine / 'manifest.json').read_text())
        manifeste['files'].append({**manifeste['files'][0], 'id': 'arrete-absent', 'filename': 'arrete-absent.pdf'})
        manifeste['files'].append({**manifeste['files'][0], 'id': 'arrete-chemin', 'filename': '../manifest.json'})
        (racine / 'manifest.json').write_text(json.dumps(manifeste))
        (racine / 'pdf' / 'arrete-1.pdf').write_bytes(b'%PDF altere')
        self.assertEqual(self.importer()['pdf_conformes'], 1)

    def test_pilote_sans_bibliotheque_ni_modele(self):
        for nom in ('bibliotheque-arretes', 'modeles'):
            shutil.rmtree(self.pilote / nom)
        r = self.importer()
        self.assertEqual((r['pdf_conformes'], r['reference_conforme']), (0, False))

    def test_pilote_inchange(self):
        avant = empreintes(self.pilote)
        self.importer()
        self.assertEqual(empreintes(self.pilote), avant)

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, 'root ignore les droits de lecture seule')
    def test_pilote_en_lecture_seule(self):
        connexion = sqlite3.connect(self.pilote / 'territoire.sqlite3')
        connexion.execute('PRAGMA journal_mode=WAL')  # base en WAL, fermée proprement : ni -wal ni -shm ne restent
        connexion.close()
        self.assertEqual([p.name for p in self.pilote.iterdir() if p.name.startswith('territoire.sqlite3-')], [])
        for dossier, noms, fichiers_ in os.walk(self.pilote):
            for nom in fichiers_:
                os.chmod(Path(dossier) / nom, 0o400)
        for dossier, noms, _ in list(os.walk(self.pilote, topdown=False)):
            os.chmod(dossier, 0o500)
        self.addCleanup(lambda: [os.chmod(d, 0o700) for d, _, _ in os.walk(self.pilote)])
        avant = empreintes(self.pilote)
        self.assertEqual(self.importer()['conversations'], 1)
        self.assertEqual(empreintes(self.pilote), avant)

    def test_droits_restrictifs(self):
        self.importer()
        for chemin in Path(self.cible).rglob('*'):
            self.assertEqual(mode(chemin), 0o700 if chemin.is_dir() else 0o600, chemin.relative_to(self.cible))

    def test_aucun_dossier_temporaire_ne_reste(self):
        self.importer()
        self.assertEqual([p.name for p in self.cible.iterdir() if p.name.startswith('.')], [])

    def test_source_sans_base(self):
        (self.pilote / 'territoire.sqlite3').unlink()
        with self.assertRaises(reprise_pilote.SourceInvalide):
            self.importer()
        self.assertFalse(self.cible.exists())

    def test_base_du_pilote_qui_est_un_lien_symbolique(self):
        os.rename(self.pilote / 'territoire.sqlite3', self.racine / 'base-externe.sqlite3')
        os.symlink(self.racine / 'base-externe.sqlite3', self.pilote / 'territoire.sqlite3')
        with self.assertRaises(reprise_pilote.SourceInvalide):
            self.importer()
        self.assertFalse(self.cible.exists())

    def test_source_absente(self):
        with self.assertRaises(ValueError):
            reprise_pilote.importer(self.racine / 'inexistant', self.cible)
        self.assertFalse(self.cible.exists())

    def test_base_du_pilote_illisible(self):
        (self.pilote / 'territoire.sqlite3').write_bytes(b'ce nest pas une base SQLite' * 100)
        with self.assertRaises(reprise_pilote.BaseIllisible) as erreur:
            self.importer()
        self.assertIn('arrêt', str(erreur.exception))
        self.assertFalse(self.cible.exists())

    def test_pilote_actif_journal_sqlite_present(self):
        for suffixe in ('-wal', '-journal'):
            with self.subTest(suffixe=suffixe):
                journal = self.pilote / ('territoire.sqlite3' + suffixe)
                journal.write_bytes(b'pages en attente')
                with self.assertRaises(reprise_pilote.PiloteActif) as erreur:
                    self.importer()
                self.assertIn('arrêt', str(erreur.exception))
                self.assertFalse(self.cible.exists())
                journal.unlink()
        self.assertEqual(self.importer()['conversations'], 1)

    def test_cible_creee_si_absente(self):
        self.assertFalse(self.cible.exists())
        self.importer()
        self.assertTrue((self.cible / 'territoire.sqlite3').is_file())

    def test_cible_qui_n_est_pas_un_dossier(self):
        self.cible.write_text('x')
        with self.assertRaises(OSError):
            self.importer()


class RefusTests(Base):
    """Refus d'une cible qui contient des données de l'utilisateur ; acceptation d'une cible neuve ou de démonstration."""

    def assertRefuse(self):
        def sans_annexes_wal(arbre):  # lire une base en WAL fait créer à SQLite un -wal vide et un -shm : ce ne sont pas des données
            return {chemin: empreinte for chemin, empreinte in arbre.items() if not chemin.endswith(('-wal', '-shm'))}
        avant = sans_annexes_wal(empreintes(self.cible))
        with self.assertRaises(reprise_pilote.CibleNonVide):
            self.importer()
        self.assertEqual(sans_annexes_wal(empreintes(self.cible)), avant)  # rien n'a été modifié, ni laissé en route

    def test_refus_si_base_existante(self):
        self.importer()
        with self.assertRaises(ValueError):
            self.importer()

    def test_base_neuve_acceptee_et_remplacee(self):
        initialiser(self.cible)
        r = self.importer()
        self.assertEqual((r['conversations'], r['documents']), (1, 2))
        with base(self.cible) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM conversations').fetchone(), (1,))

    def test_corpus_de_demonstration_accepte_et_remplace(self):
        initialiser(self.cible)
        self.corpus_demo(self.cible)
        self.assertTrue((self.cible / 'modeles' / demo.REFERENCE).is_file())
        r = self.importer()
        self.assertEqual(r['pdf_conformes'], 2)
        self.assertEqual(sorted(p.name for p in (self.cible / 'bibliotheque-arretes' / 'pdf').iterdir()), ['arrete-0.pdf', 'arrete-1.pdf'])
        self.assertEqual(sorted(p.name for p in (self.cible / 'modeles').iterdir()), ['manifest.json', REFERENCE])
        self.assertEqual(arretes.reference(self.cible)['trame'], 'stationnement-2023-13')
        manifeste = json.loads((self.cible / 'bibliotheque-arretes' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(demo.IDENTIFIANTS & {item['id'] for item in manifeste['files']})

    def test_corpus_de_demonstration_sans_base_accepte(self):
        self.corpus_demo(self.cible)
        self.assertEqual(self.importer()['pdf_conformes'], 2)
        self.assertFalse((self.cible / 'bibliotheque-arretes' / 'pdf' / (sorted(demo.IDENTIFIANTS)[0] + '.pdf')).exists())

    def test_conversation_de_l_utilisateur_refusee(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ma propre question', messages=1)
        self.assertRefuse()

    def test_conversation_de_l_utilisateur_remplacee_avec_forcer(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ma propre question', messages=1)
        r = self.importer(forcer=True)
        self.assertEqual((r['conversations'], r['messages']), (1, 2))
        with base(self.cible) as c:
            self.assertEqual(c.execute('SELECT title FROM conversations').fetchall(), [('Question sur le stationnement',)])

    def test_document_de_l_utilisateur_refuse(self):
        initialiser(self.cible)
        ajouter_document(self.cible)
        self.assertRefuse()
        self.assertEqual(self.importer(forcer=True)['documents'], 2)

    def test_espace_vide_accepte(self):
        initialiser(self.cible)
        ajouter_espace(self.cible)
        self.assertEqual(self.importer()['espaces'], 2)  # la base du pilote remplace celle de la cible

    def test_espace_avec_document_ou_conversation_refuse(self):
        for contenu in (lambda c: ajouter_document(c, 'note-mairie', espace='mairie'),
                        lambda c: ajouter_conversation(c, 'Question', espace='mairie')):
            with self.subTest():
                shutil.rmtree(self.cible, ignore_errors=True)
                initialiser(self.cible)
                ajouter_espace(self.cible)
                contenu(self.cible)
                self.assertRefuse()
                self.assertEqual(self.importer(forcer=True)['espaces'], 2)

    def test_cible_apres_verifier_acceptee_sans_forcer(self):
        """Ce que laisse `verifier` : base neuve, corpus de démonstration et l'espace d'essai vide (les espaces ne se suppriment pas)."""
        initialiser(self.cible)
        self.corpus_demo(self.cible)
        ajouter_espace(self.cible, 'a1b2c3d4', 'Essai de bout en bout')
        r = self.importer()
        self.assertEqual((r['conversations'], r['pdf_conformes']), (1, 2))
        with base(self.cible) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM spaces WHERE title='Essai de bout en bout'").fetchone(), (0,))

    def test_cible_deja_reprise_refusee_apres_verifier(self):
        initialiser(self.cible)
        self.corpus_demo(self.cible)
        ajouter_espace(self.cible, 'a1b2c3d4', 'Essai de bout en bout')
        self.importer()
        self.assertRefuse()

    def test_cible_deja_reprise_sans_conversation_refusee(self):
        with base(self.pilote) as c:  # un pilote sans conversation : seule sa bibliothèque dit que la cible est déjà reprise
            c.execute('DELETE FROM messages')
            c.execute('DELETE FROM conversations')
        self.assertEqual(self.importer()['conversations'], 0)
        with base(self.cible) as c:
            c.execute('DELETE FROM documents WHERE id != ?', ('cgfp-l121-1',))
        self.assertRefuse()

    def test_assistant_personnalise_refuse(self):
        initialiser(self.cible)
        ajouter_assistant(self.cible)
        self.assertRefuse()

    def test_base_de_la_cible_illisible_refusee(self):
        self.cible.mkdir()
        (self.cible / 'territoire.sqlite3').write_bytes(b'pas une base' * 200)
        self.assertRefuse()
        self.assertEqual(self.importer(forcer=True)['conversations'], 1)

    def test_bibliotheque_etrangere_refusee(self):
        self.cible.mkdir()
        ecrire(self.cible / 'bibliotheque-arretes' / 'manifest.json', json.dumps({'files': [{'id': 'arrete-reel-0001-xxxxxxxxxxxx'}]}))
        self.assertRefuse()
        self.assertEqual(self.importer(forcer=True)['pdf_conformes'], 2)

    def test_modele_de_reference_etranger_refuse(self):
        self.cible.mkdir()
        ecrire(self.cible / 'modeles' / 'manifest.json', json.dumps({'reference': 'mien.pdf', 'sha256': 'a' * 64, 'trame': 'ma-trame'}))
        self.assertRefuse()

    def test_manifeste_de_modele_invalide_refuse(self):
        self.cible.mkdir()
        ecrire(self.cible / 'modeles' / 'manifest.json', '{pas du json')
        self.assertRefuse()

    def test_cible_vide_acceptee(self):
        self.cible.mkdir()
        self.assertEqual(self.importer()['conversations'], 1)

    def test_les_fichiers_etrangers_de_la_cible_sont_laisses(self):
        initialiser(self.cible)
        ecrire(self.cible / 'configuration' / 'autre.json', '{}')
        ecrire(self.cible / 'journal.txt', 'journal')
        self.importer()
        self.assertEqual((self.cible / 'configuration' / 'autre.json').read_text(), '{}')
        self.assertEqual((self.cible / 'journal.txt').read_text(), 'journal')

    def test_fichiers_annexes_de_l_ancienne_base_retires(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        for suffixe in ('-wal', '-shm', '-journal'):
            ecrire(self.cible / ('territoire.sqlite3' + suffixe), b'reste')
        self.importer(forcer=True)
        self.assertEqual([p.name for p in self.cible.iterdir() if p.name.startswith('territoire.sqlite3-')], [])

    def test_dossier_absent_du_pilote_laisse_dans_la_cible(self):
        shutil.rmtree(self.pilote / 'identite')
        initialiser(self.cible)
        ecrire(self.cible / 'identite' / 'profil.json', '{"collectivite": "Locale"}')
        self.importer()
        self.assertEqual((self.cible / 'identite' / 'profil.json').read_text(), '{"collectivite": "Locale"}')

    def test_ancienne_configuration_ia_remplacee_avec_forcer(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        ecrire(self.cible / 'configuration' / 'ia.json', json.dumps({'provider': 'lmstudio', 'api_key': 'ancienne-cle'}))
        self.importer(forcer=True)
        self.assertEqual(json.loads((self.cible / 'configuration' / 'ia.json').read_text())['api_key'], '')


def alterer(table):
    """Fait croire à une copie incomplète : supprime une ligne de `table` dans la copie de la base du pilote."""
    original = sauvegarde._sauvegarde_sqlite

    def altere(adresse, copie):
        original(adresse, copie)
        connexion = sqlite3.connect(copie)
        try:
            with connexion:
                connexion.execute(f'DELETE FROM {table} WHERE rowid=(SELECT MIN(rowid) FROM {table})')
        finally:
            connexion.close()
    return patch.object(sauvegarde, '_sauvegarde_sqlite', altere)


class ElementsIgnoresTests(Base):
    """Un élément des six dossiers du pilote qui ne peut pas être copié n'est jamais ignoré en silence."""

    def assertIgnore(self, exiger=False):
        avant = empreintes(self.pilote)
        with self.assertRaises(reprise_pilote.ElementIgnore) as erreur:
            self.importer(exiger_conformite=exiger)
        self.assertFalse(self.cible.exists())
        self.assertEqual(empreintes(self.pilote), avant)
        self.assertEqual(set(erreur.exception.bilan), {'espaces', 'documents', 'conversations', 'messages', 'pdf_conformes',
                                                       'reference_conforme'})
        for nom in ('externe', 'CONTENU-EXTERNE', str(self.racine)):
            self.assertNotIn(nom, str(erreur.exception))
        return erreur.exception

    def externe(self):
        externe = self.racine / 'externe'
        ecrire(externe / 'secret.txt', 'CONTENU-EXTERNE')
        return externe

    def test_lien_symbolique_vers_un_fichier(self):
        os.symlink(self.externe() / 'secret.txt', self.pilote / 'documents' / 'lien.txt')
        self.assertIgnore()

    def test_lien_symbolique_vers_un_dossier(self):
        os.symlink(self.externe(), self.pilote / 'arretes' / 'lien-dossier')
        self.assertIgnore()

    def test_dossier_remplace_par_un_lien_symbolique(self):
        os.rename(self.pilote / 'identite', self.racine / 'identite-reelle')
        os.symlink(self.racine / 'identite-reelle', self.pilote / 'identite')
        self.assertIgnore()

    def test_documents_lien_symbolique_ne_donne_plus_un_rapport_vert(self):
        os.rename(self.pilote / 'documents', self.racine / 'documents-reels')
        os.symlink(self.racine / 'documents-reels', self.pilote / 'documents')
        erreur = self.assertIgnore(exiger=True)
        self.assertEqual(erreur.bilan['documents'], 2)  # les lignes sont comptées, mais leurs fichiers ne seraient pas là

    def test_bibliotheque_lien_symbolique_signale_l_element_et_non_un_inventaire_vide(self):
        os.rename(self.pilote / 'bibliotheque-arretes', self.racine / 'bibliotheque-reelle')
        os.symlink(self.racine / 'bibliotheque-reelle', self.pilote / 'bibliotheque-arretes')
        self.assertEqual(self.assertIgnore(exiger=True).bilan['pdf_conformes'], 0)

    def test_dossier_remplace_par_un_fichier(self):
        shutil.rmtree(self.pilote / 'identite')
        ecrire(self.pilote / 'identite', 'pas un dossier')
        self.assertIgnore()

    def test_fichier_special(self):
        os.mkfifo(self.pilote / 'arretes' / 'tube')
        self.assertIgnore()

    def test_cible_existante_intacte(self):
        os.symlink(self.externe() / 'secret.txt', self.pilote / 'documents' / 'lien.txt')
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        avant = empreintes(self.cible)
        with self.assertRaises(reprise_pilote.ElementIgnore):
            self.importer(forcer=True)
        self.assertEqual(empreintes(self.cible), avant)


class DocumentsTests(Base):
    """Chaque ligne de `documents` doit avoir son fichier, avec la bonne empreinte, dans la copie."""

    def assertNonConformes(self, nombre=1):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        avant = empreintes(self.cible)
        with self.assertRaises(reprise_pilote.DocumentsNonConformes) as erreur:
            self.importer(forcer=True)
        self.assertIn(str(nombre), str(erreur.exception))
        self.assertEqual(erreur.exception.bilan['documents'], 2)
        self.assertEqual(empreintes(self.cible), avant)

    def test_fichier_absent(self):
        (self.pilote / 'documents' / 'note-interne.txt').unlink()
        self.assertNonConformes()

    def test_fichier_modifie(self):
        (self.pilote / 'documents' / 'cgfp-l121-1.txt').write_text('Texte modifié.', encoding='utf-8')
        self.assertNonConformes()

    def test_dossier_documents_absent_de_la_source(self):
        shutil.rmtree(self.pilote / 'documents')
        self.assertNonConformes(nombre=2)

    def test_nom_de_fichier_hors_du_dossier(self):
        ecrire(self.pilote / 'secret.txt', 'x')
        with base(self.pilote) as c:
            c.execute("UPDATE documents SET filename=?, sha256=? WHERE id='note-interne'", ('../secret.txt', sha(b'x')))
        self.assertNonConformes()

    def test_documents_conformes_acceptes(self):
        self.assertEqual(self.importer()['documents'], 2)


class ConformiteTests(Base):
    """`exiger_conformite` : contrôles de la copie avant l'échange ; la cible reste identique en cas d'échec."""

    def cible_a_remplacer(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        self.corpus_demo(self.cible)
        return empreintes(self.cible)

    def reference_attendue(self):
        return patch.object(reprise_pilote, 'REFERENCE_SHA256', sha((self.pilote / 'modeles' / REFERENCE).read_bytes()))

    def test_conformite_exigee_et_satisfaite(self):
        with self.reference_attendue():
            r = self.importer(exiger_conformite=True)
        self.assertEqual((r['pdf_conformes'], r['reference_conforme']), (2, True))

    def test_non_exigee_par_defaut(self):
        self.assertFalse(self.importer()['reference_conforme'])

    def test_reference_non_conforme(self):
        avant = self.cible_a_remplacer()
        with self.assertRaises(reprise_pilote.ReferenceNonConforme) as erreur:
            self.importer(forcer=True, exiger_conformite=True)
        self.assertEqual(erreur.exception.bilan, {'espaces': 2, 'documents': 2, 'conversations': 1, 'messages': 2,
                                                  'pdf_conformes': 2, 'reference_conforme': False})
        self.assertEqual(empreintes(self.cible), avant)

    def test_reference_absente(self):
        (self.pilote / 'modeles' / REFERENCE).unlink()
        avant = self.cible_a_remplacer()
        with self.assertRaises(reprise_pilote.ReferenceNonConforme):
            self.importer(forcer=True, exiger_conformite=True)
        self.assertEqual(empreintes(self.cible), avant)

    def test_pdf_modifie(self):
        (self.pilote / 'bibliotheque-arretes' / 'pdf' / 'arrete-1.pdf').write_bytes(b'%PDF altere')
        avant = self.cible_a_remplacer()
        with self.reference_attendue(), self.assertRaises(reprise_pilote.PdfNonConformes) as erreur:
            self.importer(forcer=True, exiger_conformite=True)
        self.assertEqual((erreur.exception.bilan['pdf_conformes'], erreur.exception.bilan['reference_conforme']), (1, True))
        self.assertEqual(empreintes(self.cible), avant)

    def test_pdf_de_l_inventaire_absent(self):
        (self.pilote / 'bibliotheque-arretes' / 'pdf' / 'arrete-0.pdf').unlink()
        avant = self.cible_a_remplacer()
        with self.reference_attendue(), self.assertRaises(reprise_pilote.PdfNonConformes):
            self.importer(forcer=True, exiger_conformite=True)
        self.assertEqual(empreintes(self.cible), avant)

    def test_pilote_sans_bibliotheque_est_non_conforme(self):
        shutil.rmtree(self.pilote / 'bibliotheque-arretes')  # plus d'inventaire du tout : rien à comparer, donc non conforme
        avant = self.cible_a_remplacer()
        with self.reference_attendue(), self.assertRaises(reprise_pilote.PdfNonConformes) as erreur:
            self.importer(forcer=True, exiger_conformite=True)
        self.assertEqual(erreur.exception.bilan['pdf_conformes'], 0)
        self.assertEqual(empreintes(self.cible), avant)

    def test_inventaire_illisible(self):
        (self.pilote / 'bibliotheque-arretes' / 'manifest.json').write_text('{pas du json', encoding='utf-8')
        with self.reference_attendue(), self.assertRaises(reprise_pilote.PdfNonConformes):
            self.importer(exiger_conformite=True)
        self.assertFalse(self.cible.exists())

    def test_inventaire_vide_conforme(self):
        (self.pilote / 'bibliotheque-arretes' / 'manifest.json').write_text('{"files": []}', encoding='utf-8')
        with self.reference_attendue():
            self.assertEqual(self.importer(exiger_conformite=True)['pdf_conformes'], 0)

    def test_messages_sans_nom_de_fichier(self):
        (self.pilote / 'bibliotheque-arretes' / 'pdf' / 'arrete-1.pdf').write_bytes(b'%PDF altere')
        with self.reference_attendue(), self.assertRaises(reprise_pilote.PdfNonConformes) as erreur:
            self.importer(exiger_conformite=True)
        self.assertNotIn('arrete-', str(erreur.exception))


class ImbricationTests(Base):
    """Source et cible ne se recouvrent jamais : le pilote ne peut pas être modifié, même avec `forcer`."""

    def en_wal(self):
        connexion = sqlite3.connect(self.pilote / 'territoire.sqlite3')
        connexion.execute('PRAGMA journal_mode=WAL')
        connexion.close()

    def assertImbriques(self, cible, *, racine=None):
        racine = racine or self.pilote
        for forcer in (False, True):
            with self.subTest(forcer=forcer):
                avant_pilote, avant = empreintes(racine), empreintes(cible) if cible.exists() else None
                with self.assertRaises(reprise_pilote.CheminsImbriques):
                    reprise_pilote.importer(self.pilote, cible, forcer=forcer)
                self.assertEqual(empreintes(racine), avant_pilote)
                self.assertEqual(empreintes(cible) if cible.exists() else None, avant)

    def test_meme_dossier(self):
        self.en_wal()  # même la lecture de la cible ne doit pas créer de -shm à côté de la base du pilote
        self.assertImbriques(self.pilote)

    def test_meme_dossier_ecrit_autrement(self):
        self.assertImbriques(self.pilote / 'documents' / '..')

    def test_meme_dossier_avec_une_autre_casse(self):
        autre = self.pilote.with_name(self.pilote.name.upper())
        if not autre.exists():
            self.skipTest('système de fichiers sensible à la casse')
        self.assertImbriques(autre)

    def test_cible_dans_la_source_existante(self):
        self.assertImbriques(self.pilote / 'documents')

    def test_cible_dans_la_source_a_creer(self):
        cible = self.pilote / 'nouveau' / 'donnees'
        self.assertImbriques(cible)
        self.assertFalse((self.pilote / 'nouveau').exists())

    def test_source_dans_la_cible(self):
        self.assertImbriques(self.pilote.parent, racine=self.pilote.parent)
        self.assertImbriques(self.racine, racine=self.racine)

    def test_cible_qui_est_un_lien_vers_la_source(self):
        alias = self.racine / 'alias'
        os.symlink(self.pilote, alias)
        self.assertImbriques(alias)

    def test_cible_dans_la_source_par_un_lien(self):
        alias = self.racine / 'alias'
        os.symlink(self.pilote / 'documents', alias)
        self.assertImbriques(alias / 'sous-dossier')

    def test_source_par_un_lien_et_cible_dans_la_source(self):
        alias = self.racine / 'alias'
        os.symlink(self.pilote, alias)
        with self.assertRaises(reprise_pilote.CheminsImbriques):
            reprise_pilote.importer(alias, self.pilote / 'documents', forcer=True)

    def test_dossiers_voisins_acceptes(self):
        self.assertEqual(reprise_pilote.importer(self.pilote, self.racine / 'pilote' / 'donnees')['conversations'], 1)

    def test_prefixe_commun_n_est_pas_un_recouvrement(self):
        voisin = self.pilote.with_name('data-copie')
        self.assertEqual(reprise_pilote.importer(self.pilote, voisin)['conversations'], 1)

    def test_source_absente_reste_une_source_invalide(self):
        with self.assertRaises(reprise_pilote.SourceInvalide):
            reprise_pilote.importer(self.racine / 'inexistant', self.racine / 'autre')


class EcartTests(Base):
    def test_ecart_de_comptage_par_table(self):
        self.cible.mkdir()
        for table in ('spaces', 'documents', 'conversations', 'messages'):
            with self.subTest(table=table), alterer(table), self.assertRaises(ValueError) as erreur:
                self.importer()
            self.assertEqual(str(erreur.exception), f'Écart de comptage : {table}')
            self.assertEqual(list(self.cible.iterdir()), [])  # ni base, ni dossier temporaire

    def test_ecart_laisse_la_cible_existante_intacte(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        avant = empreintes(self.cible)
        with alterer('messages'), self.assertRaises(reprise_pilote.EcartDeComptage):
            self.importer(forcer=True)
        self.assertEqual(empreintes(self.cible), avant)

    def test_cible_creee_puis_retiree_en_cas_d_echec(self):
        with alterer('spaces'), self.assertRaises(ValueError):
            self.importer()
        self.assertFalse(self.cible.exists())


def deplacements(echec_au_rang=None, definitivement=False):
    """Enveloppe `_deplacer` : compte les appels et, au rang demandé, échoue (puis à chaque appel suivant si `definitivement`)."""
    original, appels = reprise_pilote._deplacer, []

    def deplacer(source, destination):
        appels.append(source)
        if echec_au_rang and (len(appels) == echec_au_rang or (definitivement and len(appels) > echec_au_rang)):
            raise OSError('disque plein')
        original(source, destination)
    return patch.object(reprise_pilote, '_deplacer', deplacer), appels


class EchangeTests(Base):
    """L'échange final est réversible : un déplacement qui échoue défait ceux déjà faits."""

    def preparer(self):
        shutil.rmtree(self.cible, ignore_errors=True)
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        self.corpus_demo(self.cible)
        ecrire(self.cible / 'identite' / 'profil.json', '{"collectivite": "Ancienne"}')
        return empreintes(self.cible)

    def test_un_echec_a_n_importe_quel_deplacement_restaure_la_cible(self):
        self.preparer()
        patcher, appels = deplacements()
        with patcher:
            self.importer(forcer=True)
        total = len(appels)
        self.assertGreaterEqual(total, 12)  # base, 6 dossiers (anciens et nouveaux), configuration, annexes
        for rang in range(1, total + 1):
            with self.subTest(rang=rang):
                avant = self.preparer()
                patcher, _ = deplacements(rang)
                with patcher, self.assertRaises(OSError):
                    self.importer(forcer=True)
                self.assertEqual(empreintes(self.cible), avant)

    def test_retour_arriere_impossible_conserve_les_anciennes_donnees(self):
        self.preparer()
        patcher, _ = deplacements(4, definitivement=True)
        with patcher, self.assertRaises(reprise_pilote.RepriseInterrompue) as erreur:
            self.importer(forcer=True)
        dossier = erreur.exception.dossier
        self.assertTrue(dossier.is_dir())
        self.assertEqual(dossier.parent, self.cible)
        self.assertIn(str(dossier), str(erreur.exception))
        self.assertTrue(fichiers(dossier))  # les données précédentes déjà déplacées s'y trouvent encore


RAPPORT = r'RAPPORT reprendre-pilote : espaces=\d+ documents=\d+ conversations=\d+ messages=\d+ pdf_conformes=\d+ reference_conforme=(oui|non)\n'
RAPPORT_PILOTE = ('RAPPORT reprendre-pilote : espaces=2 documents=2 conversations=1 messages=2 pdf_conformes=2 '
                  'reference_conforme=%s\n')


class OutilTests(Base):
    def setUp(self):
        super().setUp()
        for patcher in (patch.object(outils, 'DONNEES', self.cible),
                        # le faux modèle de référence prend l'empreinte attendue : le contrôle de conformité est toujours exigé
                        patch.object(reprise_pilote, 'REFERENCE_SHA256', sha((self.pilote / 'modeles' / REFERENCE).read_bytes()))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def executer(self, *argv):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(io.StringIO()):
            code = outils.main(list(argv))
        return code, sortie.getvalue()

    def reprendre(self, *options):
        return self.executer('reprendre-pilote', str(self.pilote), *options)

    def test_reprend_le_pilote_avec_le_rapport_puis_la_ligne_etape(self):
        self.assertEqual(self.reprendre(), (0, RAPPORT_PILOTE % 'oui' + 'ETAPE reprendre-pilote : OK\n'))
        self.assertTrue((self.cible / 'territoire.sqlite3').is_file())

    def test_la_sortie_est_un_rapport_d_entiers_puis_etape(self):
        _, texte = self.reprendre()
        self.assertRegex(texte, r'\A' + RAPPORT + r'ETAPE reprendre-pilote : OK\n\Z')

    def test_interface(self):
        args = outils.construire_parseur().parse_args(['reprendre-pilote', str(self.pilote)])
        self.assertEqual((args.source, args.forcer), (self.pilote, False))
        self.assertTrue(outils.construire_parseur().parse_args(['reprendre-pilote', str(self.pilote), '--forcer']).forcer)

    def test_source_obligatoire(self):
        self.assertEqual(self.executer('reprendre-pilote'), (1, 'ETAPE reprendre-pilote : ECHEC arguments_invalides\n'))

    def test_refus_puis_forcer(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        self.assertEqual(self.reprendre(), (1, 'ETAPE reprendre-pilote : ECHEC cible_non_vide\n'))
        self.assertEqual(self.reprendre('--forcer'), (0, RAPPORT_PILOTE % 'oui' + 'ETAPE reprendre-pilote : OK\n'))

    def test_source_invalide(self):
        (self.pilote / 'territoire.sqlite3').unlink()
        self.assertEqual(self.reprendre(), (1, 'ETAPE reprendre-pilote : ECHEC source_invalide\n'))

    def test_pilote_actif(self):
        (self.pilote / 'territoire.sqlite3-wal').write_bytes(b'x')
        self.assertEqual(self.reprendre(), (1, 'ETAPE reprendre-pilote : ECHEC pilote_actif\n'))

    def test_chemins_imbriques(self):
        for cible in (self.pilote, self.pilote / 'documents', self.pilote.parent):
            with self.subTest(cible=cible.name), patch.object(outils, 'DONNEES', cible):
                avant = empreintes(self.pilote)
                self.assertEqual(self.reprendre('--forcer'), (1, 'ETAPE reprendre-pilote : ECHEC chemins_imbriques\n'))
                self.assertEqual(empreintes(self.pilote), avant)

    def test_ecart_de_comptage(self):
        with alterer('messages'):
            self.assertEqual(self.reprendre(), (1, 'ETAPE reprendre-pilote : ECHEC ecart_de_comptage\n'))

    def test_cible_inaccessible(self):
        self.cible.write_text('x')
        self.assertEqual(self.reprendre(), (1, 'ETAPE reprendre-pilote : ECHEC fichier_inaccessible\n'))

    def test_reference_non_conforme_garde_la_cible_et_montre_le_rapport(self):
        with patch.object(reprise_pilote, 'REFERENCE_SHA256', SHA_REFERENCE):
            avant = self.cible_existante()
            code, texte = self.reprendre('--forcer')
        self.assertEqual((code, texte), (1, RAPPORT_PILOTE % 'non' + 'ETAPE reprendre-pilote : ECHEC reference_non_conforme\n'))
        self.assertEqual(empreintes(self.cible), avant)

    def test_pdf_non_conformes_garde_la_cible_et_montre_le_rapport(self):
        (self.pilote / 'bibliotheque-arretes' / 'pdf' / 'arrete-1.pdf').write_bytes(b'%PDF altere')
        avant = self.cible_existante()
        self.assertEqual(self.reprendre('--forcer'), (1, 'RAPPORT reprendre-pilote : espaces=2 documents=2 conversations=1 messages=2 '
                                                         'pdf_conformes=1 reference_conforme=oui\n'
                                                         'ETAPE reprendre-pilote : ECHEC pdf_non_conformes\n'))
        self.assertEqual(empreintes(self.cible), avant)

    def test_element_ignore_garde_la_cible_et_montre_le_rapport(self):
        os.symlink(self.racine, self.pilote / 'documents' / 'lien')
        avant = self.cible_existante()
        code, texte = self.reprendre('--forcer')
        self.assertEqual((code, texte), (1, RAPPORT_PILOTE % 'oui' + 'ETAPE reprendre-pilote : ECHEC element_ignore\n'))
        self.assertEqual(empreintes(self.cible), avant)

    def test_document_manquant_garde_la_cible_et_montre_le_rapport(self):
        (self.pilote / 'documents' / 'note-interne.txt').unlink()
        avant = self.cible_existante()
        code, texte = self.reprendre('--forcer')
        self.assertEqual((code, texte), (1, RAPPORT_PILOTE % 'oui' + 'ETAPE reprendre-pilote : ECHEC documents_non_conformes\n'))
        self.assertEqual(empreintes(self.cible), avant)

    def test_echec_de_l_echange_defait_garde_le_rapport(self):
        avant = self.cible_existante()
        patcher, _ = deplacements(1)
        with patcher:
            code, texte = self.reprendre('--forcer')
        self.assertEqual((code, texte), (1, RAPPORT_PILOTE % 'oui' + 'ETAPE reprendre-pilote : ECHEC fichier_inaccessible\n'))
        self.assertEqual(empreintes(self.cible), avant)

    def test_reprise_interrompue_garde_le_rapport(self):
        self.cible_existante()
        patcher, _ = deplacements(4, definitivement=True)
        with patcher:
            code, texte = self.reprendre('--forcer')
        self.assertEqual((code, texte), (1, RAPPORT_PILOTE % 'oui' + 'ETAPE reprendre-pilote : ECHEC reprise_interrompue\n'))
        self.assertTrue([p for p in self.cible.iterdir() if p.name.startswith('.reprise-')])  # données précédentes conservées

    def test_echecs_sans_rapport_n_impriment_qu_une_ligne(self):
        (self.pilote / 'territoire.sqlite3').unlink()
        _, texte = self.reprendre()
        self.assertEqual(texte, 'ETAPE reprendre-pilote : ECHEC source_invalide\n')
        with patch.object(outils, 'DONNEES', self.pilote):
            _, texte = self.reprendre()
        self.assertEqual(texte, 'ETAPE reprendre-pilote : ECHEC chemins_imbriques\n')

    def cible_existante(self):
        initialiser(self.cible)
        ajouter_conversation(self.cible, 'Ancienne')
        return empreintes(self.cible)

    def test_aucun_chemin_ni_contenu_dans_la_sortie(self):
        _, texte = self.reprendre()
        self.assertNotIn(str(self.racine), texte)
        self.assertNotIn('stationnement', texte)
        self.assertEqual(len(texte.splitlines()), 2)


if __name__ == '__main__':
    unittest.main()
