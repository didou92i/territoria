# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

import outils
import sauvegarde

FICHIERS_ATTENDUS = {
    'territoria/territoire.sqlite3', 'territoria/documents/piece.txt', 'n8n/database.sqlite', 'n8n/config',
    'secrets/territoria.env', 'secrets/n8n.env', 'secrets/compose.env',
}
DOSSIERS_ATTENDUS = {'territoria', 'territoria/documents', 'territoria/vide', 'n8n', 'secrets'}


def mode(chemin):
    return stat.S_IMODE(os.lstat(chemin).st_mode)


def arborescence(racine):
    """{chemin relatif: 'dossier' ou empreinte SHA-256} de tout ce qui se trouve sous `racine`."""
    racine = Path(racine)
    return {p.relative_to(racine).as_posix(): 'dossier' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
            for p in racine.rglob('*')}


def contenu_base(chemin):
    connexion = sqlite3.connect(f'{Path(chemin).resolve().as_uri()}?mode=ro', uri=True)
    try:
        return list(connexion.iterdump())
    finally:
        connexion.close()


def dechiffrer_selon_le_format(archive, mot_de_passe):
    """Déchiffre une archive `.tsv1` en suivant la seule description du format, sans le code de `sauvegarde`."""
    donnees = Path(archive).read_bytes()
    assert donnees[:6] == b'TRSV1\n'
    sel = donnees[6:22]
    aead = AESGCM(Scrypt(salt=sel, length=32, n=2 ** 15, r=8, p=1).derive(mot_de_passe.encode('utf-8')))
    position, index, blocs = 22, 0, []
    while position < len(donnees):
        longueur = int.from_bytes(donnees[position:position + 4], 'big')
        chiffre = donnees[position + 4:position + 4 + longueur]
        position += 4 + longueur
        drapeau = b'F' if position == len(donnees) else b'C'
        blocs.append(aead.decrypt(bytes(4) + index.to_bytes(8, 'big'), chiffre, b'TRSV1\n' + sel + drapeau))
        index += 1
    return blocs


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.src = self.base / 'territoria-donnees'
        self.dest = self.base / 'sauvegardes'
        self.cible = self.base / 'cible'
        self.dest.mkdir()
        self.cible.mkdir()
        territoria = self.src / 'territoria'
        (territoria / 'documents').mkdir(parents=True)
        (territoria / 'vide').mkdir()
        base = sqlite3.connect(territoria / 'territoire.sqlite3')
        base.execute('create table spaces(id integer primary key, title text)')
        base.execute("insert into spaces(title) values ('Espace fictif')")
        base.commit()
        base.close()
        (territoria / 'documents' / 'piece.txt').write_text('Pièce fictive', encoding='utf-8')
        os.chmod(territoria / 'documents' / 'piece.txt', 0o640)
        for exclu in ('server.log', 'cache.sqlite3-wal', 'cache.sqlite3-shm', 'cache.sqlite3-journal'):
            (territoria / exclu).write_text('exclu', encoding='utf-8')
        n8n = self.src / 'n8n'
        (n8n / 'nodes').mkdir(parents=True)
        base = sqlite3.connect(n8n / 'database.sqlite')
        base.execute('create table workflow_entity(id text, name text)')
        base.execute("insert into workflow_entity values ('TerritoriaConv01', 'Conversation')")
        base.commit()
        base.close()
        (n8n / 'config').write_text('{"encryptionKey": "cle-fictive"}', encoding='utf-8')
        (n8n / 'n8nEventLog.log').write_text('evenements', encoding='utf-8')
        (n8n / 'nodes' / 'package.json').write_text('{}', encoding='utf-8')
        secrets = self.src / 'secrets'
        (secrets / 'provision').mkdir(parents=True)
        os.chmod(secrets, 0o700)
        for nom, texte in (('territoria.env', 'TERRITORIA_SERVICE_TOKEN=fictif\n'), ('n8n.env', 'N8N_ENCRYPTION_KEY=fictive\n'),
                           ('compose.env', 'TERRITORIA_HORS_LIGNE=1\n')):
            (secrets / nom).write_text(texte, encoding='utf-8')
            os.chmod(secrets / nom, 0o600)
        (secrets / 'provision' / 'credentials.json').write_text('[]', encoding='utf-8')

    def creer(self, mot_de_passe='m', maintenant=datetime(2026, 11, 2)):
        return sauvegarde.creer(self.src, self.dest, mot_de_passe, maintenant)


class AllerRetourTests(Base):
    def test_aller_retour_empreintes_identiques(self):
        a = sauvegarde.creer(self.src, self.dest, 'mdp-essai', datetime(2026, 11, 2, 12, 30))
        self.assertEqual(sauvegarde.verifier(a, 'mdp-essai')['conforme'], True)

    def test_verifier_compte_les_fichiers_du_manifeste(self):
        a = self.creer()
        self.assertEqual(sauvegarde.verifier(a, 'm'), {'fichiers': len(FICHIERS_ATTENDUS), 'conforme': True})

    def test_restauration_identique_a_la_source(self):
        a = self.creer()
        sauvegarde.restaurer(a, self.cible, 'm')
        restaure, source = arborescence(self.cible), arborescence(self.src)
        self.assertEqual({c for c, v in restaure.items() if v != 'dossier'}, FICHIERS_ATTENDUS)
        self.assertEqual({c for c, v in restaure.items() if v == 'dossier'}, DOSSIERS_ATTENDUS)
        for chemin in FICHIERS_ATTENDUS - {'territoria/territoire.sqlite3', 'n8n/database.sqlite'}:
            self.assertEqual(restaure[chemin], source[chemin], chemin)
        for chemin in ('territoria/territoire.sqlite3', 'n8n/database.sqlite'):
            self.assertEqual(contenu_base(self.cible / chemin), contenu_base(self.src / chemin), chemin)

    def test_droits_conserves(self):
        sauvegarde.restaurer(self.creer(), self.cible, 'm')
        self.assertEqual(mode(self.cible / 'secrets'), 0o700)
        self.assertEqual(mode(self.cible / 'secrets' / 'n8n.env'), 0o600)
        self.assertEqual(mode(self.cible / 'territoria' / 'documents' / 'piece.txt'), 0o640)

    def test_ligne_presente_seulement_dans_le_wal_est_sauvegardee(self):
        c = sqlite3.connect(self.src / 'territoria' / 'territoire.sqlite3'); c.execute('pragma journal_mode=wal')
        c.execute("create table t(x)"); c.execute("insert into t values ('wal')"); c.commit()  # connexion laissée ouverte : pas de checkpoint
        self.addCleanup(c.close)
        self.assertGreater((self.src / 'territoria' / 'territoire.sqlite3-wal').stat().st_size, 0)
        a = sauvegarde.creer(self.src, self.dest, 'm', datetime(2026, 11, 2)); sauvegarde.restaurer(a, self.cible, 'm')
        self.assertEqual(sqlite3.connect(self.cible / 'territoria' / 'territoire.sqlite3').execute('select x from t').fetchone(), ('wal',))

    def test_base_en_wal_sur_un_support_en_lecture_seule(self):
        """Cas du conteneur de sauvegarde : territoria-donnees monté en lecture seule pendant que n8n écrit."""
        dossier = self.src / 'n8n'
        c = sqlite3.connect(dossier / 'database.sqlite'); c.execute('pragma journal_mode=wal')
        c.execute("insert into workflow_entity values ('TerritoriaArre01', 'Arrêtés')"); c.commit()
        self.addCleanup(c.close)
        lecture_seule = [dossier / 'database.sqlite', dossier / 'database.sqlite-wal', dossier / 'database.sqlite-shm', dossier]
        for chemin in lecture_seule:
            os.chmod(chemin, 0o555 if chemin.is_dir() else 0o444)
        self.addCleanup(lambda: [os.chmod(p, 0o755 if p.is_dir() else 0o644) for p in lecture_seule])
        sauvegarde.restaurer(self.creer(), self.cible, 'm')
        lignes = sqlite3.connect(self.cible / 'n8n' / 'database.sqlite').execute('select id from workflow_entity').fetchall()
        self.assertEqual(lignes, [('TerritoriaConv01',), ('TerritoriaArre01',)])

    def test_base_en_wal_arretee_sur_un_support_en_lecture_seule(self):
        """Services arrêtés : base restée en mode WAL, sans fichier -wal, dans un dossier où rien ne peut être créé."""
        dossier = self.src / 'n8n'
        c = sqlite3.connect(dossier / 'database.sqlite'); c.execute('pragma journal_mode=wal')
        c.execute("insert into workflow_entity values ('TerritoriaArre01', 'Arrêtés')"); c.commit(); c.close()
        self.assertFalse((dossier / 'database.sqlite-wal').exists())
        os.chmod(dossier, 0o555)
        self.addCleanup(os.chmod, dossier, 0o755)
        sauvegarde.restaurer(self.creer(), self.cible, 'm')
        lignes = sqlite3.connect(self.cible / 'n8n' / 'database.sqlite').execute('select id from workflow_entity').fetchall()
        self.assertEqual(lignes, [('TerritoriaConv01',), ('TerritoriaArre01',)])

    def test_mot_de_passe_de_sauvegarde_exclu(self):
        (self.src / 'secrets' / 'sauvegarde.motdepasse').write_text('m')
        a = sauvegarde.creer(self.src, self.dest, 'm', datetime(2026, 11, 2)); sauvegarde.restaurer(a, self.cible, 'm')
        self.assertFalse((self.cible / 'secrets' / 'sauvegarde.motdepasse').exists())

    def test_exclusions(self):
        sauvegarde.restaurer(self.creer(), self.cible, 'm')
        restaure = arborescence(self.cible)
        for exclu in ('territoria/server.log', 'territoria/cache.sqlite3-wal', 'territoria/cache.sqlite3-shm',
                      'territoria/cache.sqlite3-journal', 'n8n/n8nEventLog.log', 'n8n/nodes', 'secrets/provision'):
            self.assertNotIn(exclu, restaure)
        self.assertFalse((self.cible / 'MANIFESTE.json').exists())  # le manifeste sert à la vérification, pas aux données

    def test_l_audio_n_entre_pas_dans_la_sauvegarde(self):
        audio = self.src / 'territoria' / 'audio' / ('a' * 32)
        audio.mkdir(parents=True)
        (audio / 'source.mp3').write_bytes(b'enregistrement de la seance')
        sauvegarde.restaurer(self.creer(), self.cible, 'm')
        restaure = arborescence(self.cible)
        self.assertFalse(any(chemin.startswith('territoria/audio') for chemin in restaure))
        self.assertTrue(any(chemin.startswith('territoria/documents/') for chemin in restaure))

    def test_les_liens_symboliques_de_la_source_ne_sont_pas_suivis(self):
        (self.src / 'territoria' / 'documents' / 'lien').symlink_to(self.src / 'secrets' / 'n8n.env')
        (self.src / 'territoria' / 'dossier-lien').symlink_to(self.src / 'secrets', target_is_directory=True)
        sauvegarde.restaurer(self.creer(), self.cible, 'm')
        self.assertFalse(os.path.lexists(self.cible / 'territoria' / 'documents' / 'lien'))
        self.assertFalse(os.path.lexists(self.cible / 'territoria' / 'dossier-lien'))

    def test_source_sans_n8n(self):
        for chemin in sorted((self.src / 'n8n').rglob('*'), reverse=True):
            chemin.rmdir() if chemin.is_dir() else chemin.unlink()
        (self.src / 'n8n').rmdir()
        a = self.creer()
        self.assertEqual(sauvegarde.verifier(a, 'm')['conforme'], True)
        sauvegarde.restaurer(a, self.cible, 'm')
        self.assertFalse((self.cible / 'n8n').exists())

    def test_source_invalide(self):
        with self.assertRaises(ValueError):
            sauvegarde.creer(self.base / 'ailleurs', self.dest, 'm', datetime(2026, 11, 2))
        self.assertEqual(list(self.dest.iterdir()), [])


class ArchiveTests(Base):
    def test_nom_droits_et_en_tete(self):
        a = sauvegarde.creer(self.src, self.dest, 'm', datetime(2026, 11, 2, 12, 30, 5))
        self.assertEqual(a, self.dest / 'territoria-20261102-123005.tsv1')
        self.assertEqual(mode(a), 0o600)
        self.assertEqual(a.read_bytes()[:6], b'TRSV1\n')
        self.assertEqual(list(self.dest.iterdir()), [a])  # aucun fichier provisoire laissé

    def test_format_conforme_a_la_description(self):
        self.assertEqual(sauvegarde.TAILLE_BLOC, 4 * 1024 * 1024)
        with patch.object(sauvegarde, 'TAILLE_BLOC', 1024):
            a = self.creer('mot de passe accentué é')
        blocs = dechiffrer_selon_le_format(a, 'mot de passe accentué é')
        self.assertGreater(len(blocs), 3)
        self.assertTrue(all(len(b) == 1024 for b in blocs[:-1]))
        self.assertLessEqual(len(blocs[-1]), 1024)
        with tarfile.open(fileobj=io.BytesIO(b''.join(blocs))) as tar:
            membres = {m.name: m for m in tar.getmembers()}
            manifeste = json.loads(tar.extractfile('MANIFESTE.json').read())
            self.assertEqual(set(manifeste), FICHIERS_ATTENDUS)
            for chemin, empreinte in manifeste.items():
                self.assertTrue(membres[chemin].isreg(), chemin)
                self.assertEqual(hashlib.sha256(tar.extractfile(chemin).read()).hexdigest(), empreinte, chemin)
        self.assertEqual(set(membres) - set(manifeste) - {'MANIFESTE.json'}, DOSSIERS_ATTENDUS)

    def test_sel_different_a_chaque_archive(self):
        a = self.creer(maintenant=datetime(2026, 11, 2, 1))
        b = self.creer(maintenant=datetime(2026, 11, 2, 2))
        self.assertNotEqual(a.read_bytes()[6:22], b.read_bytes()[6:22])

    def test_archive_existante_non_remplacee(self):
        a = self.creer()
        avant = a.read_bytes()
        with self.assertRaises(FileExistsError):
            self.creer()
        self.assertEqual(a.read_bytes(), avant)
        self.assertEqual(list(self.dest.iterdir()), [a])

    def test_echec_pendant_la_creation_ne_laisse_rien(self):
        with patch.object(sauvegarde, '_copier_base', side_effect=sqlite3.OperationalError('base occupée')):
            with self.assertRaises(sqlite3.OperationalError):
                self.creer()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_echec_a_la_fin_de_l_ecriture_ne_laisse_rien(self):
        with patch.object(sauvegarde._EcritureChiffree, 'terminer', side_effect=OSError('disque plein')):
            with self.assertRaises(OSError):
                self.creer()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_copies_provisoires_des_bases_supprimees(self):
        temporaire = self.base / 'tmp'
        temporaire.mkdir()
        with patch.object(tempfile, 'tempdir', str(temporaire)):
            self.creer()
            with patch.object(sauvegarde, '_EcritureChiffree', side_effect=OSError('x')), self.assertRaises(OSError):
                self.creer(maintenant=datetime(2026, 11, 3))
        self.assertEqual(list(temporaire.iterdir()), [])

    def test_proprietaire_de_l_archive_en_root(self):
        st = self.dest.stat()
        with patch.object(os, 'geteuid', return_value=0), patch.object(os, 'fchown') as fchown:
            self.creer()
        fchown.assert_called_once()
        self.assertEqual(fchown.call_args.args[1:], (st.st_uid, st.st_gid))


class ArchiveAltereeTests(Base):
    def test_mauvais_mot_de_passe_rien_ecrit(self):
        a = sauvegarde.creer(self.src, self.dest, 'm', datetime(2026, 11, 2))
        with self.assertRaisesRegex(ValueError, 'illisible'): sauvegarde.restaurer(a, self.cible, 'autre')
        self.assertEqual(list(self.cible.iterdir()), [])

    def test_archive_tronquee(self):
        a = sauvegarde.creer(self.src, self.dest, 'm', datetime(2026, 11, 2)); a.write_bytes(a.read_bytes()[:-100])
        with self.assertRaisesRegex(ValueError, 'illisible|incomplète'): sauvegarde.verifier(a, 'm')
        with self.assertRaisesRegex(ValueError, 'illisible|incomplète'): sauvegarde.restaurer(a, self.cible, 'm')
        self.assertEqual(list(self.cible.iterdir()), [])

    def test_bloc_final_retire(self):
        with patch.object(sauvegarde, 'TAILLE_BLOC', 1024):
            a = self.creer()
            donnees = a.read_bytes()
            position = 22
            while True:  # coupe juste avant le bloc final : il ne reste que des blocs C intacts
                longueur = int.from_bytes(donnees[position:position + 4], 'big')
                if position + 4 + longueur == len(donnees):
                    break
                position += 4 + longueur
            a.write_bytes(donnees[:position])
            with self.assertRaisesRegex(ValueError, '^Archive incomplète\\.$'):
                sauvegarde.verifier(a, 'm')
            with self.assertRaisesRegex(ValueError, '^Archive incomplète\\.$'):
                sauvegarde.restaurer(a, self.cible, 'm')
        self.assertEqual(list(self.cible.iterdir()), [])

    def test_messages_exacts(self):
        a = self.creer()
        with self.assertRaisesRegex(ValueError, '^Archive illisible : mot de passe incorrect ou fichier altéré\\.$'):
            sauvegarde.verifier(a, 'autre')

    def test_octet_altere(self):
        a = self.creer()
        donnees = bytearray(a.read_bytes())
        for position in (3, 10, 25, len(donnees) // 2, len(donnees) - 1):  # en-tête, sel, longueur, corps, étiquette
            with self.subTest(position=position):
                altere = bytearray(donnees)
                altere[position] ^= 0x01
                a.write_bytes(bytes(altere))
                with self.assertRaisesRegex(ValueError, 'illisible|incomplète'):
                    sauvegarde.restaurer(a, self.cible, 'm')
                self.assertEqual(list(self.cible.iterdir()), [])

    def test_donnees_ajoutees_apres_le_bloc_final(self):
        a = self.creer()
        a.write_bytes(a.read_bytes() + b'\x00\x00\x00\x10' + bytes(16))
        with self.assertRaisesRegex(ValueError, 'illisible'):
            sauvegarde.verifier(a, 'm')

    def test_blocs_permutes(self):
        with patch.object(sauvegarde, 'TAILLE_BLOC', 1024):
            a = self.creer()
            donnees = a.read_bytes()
            taille = 4 + 1024 + 16
            premier, second = donnees[22:22 + taille], donnees[22 + taille:22 + 2 * taille]
            a.write_bytes(donnees[:22] + second + premier + donnees[22 + 2 * taille:])
            with self.assertRaisesRegex(ValueError, 'illisible'):
                sauvegarde.verifier(a, 'm')

    def test_fichier_vide_ou_etranger(self):
        a = self.dest / 'territoria-20261102-000000.tsv1'
        for contenu, motif in ((b'', 'incomplète'), (b'TRSV1\n' + bytes(5), 'incomplète'), (b'PK\x03\x04' + bytes(64), 'illisible')):
            with self.subTest(contenu=contenu[:6]):
                a.write_bytes(contenu)
                with self.assertRaisesRegex(ValueError, motif):
                    sauvegarde.verifier(a, 'm')

    def test_dossiers_temporaires_prives_et_supprimes(self):
        temporaire = self.base / 'tmp'
        temporaire.mkdir()
        a = self.creer()
        modes = []
        mkdtemp = tempfile.mkdtemp

        def espion(*args, **kwargs):
            chemin = mkdtemp(*args, **kwargs)
            modes.append(mode(chemin))
            return chemin

        with patch.object(tempfile, 'tempdir', str(temporaire)), patch.object(tempfile, 'mkdtemp', side_effect=espion):
            sauvegarde.verifier(a, 'm')
            with self.assertRaises(ValueError):
                sauvegarde.verifier(a, 'autre')
            sauvegarde.restaurer(a, self.cible, 'm')
        self.assertEqual(modes, [0o700] * 3)
        self.assertEqual(list(temporaire.iterdir()), [])
        self.assertEqual(sorted(p.name for p in self.cible.iterdir()), ['n8n', 'secrets', 'territoria'])


class ArchiveForgeeTests(Base):
    """Archives chiffrées avec le bon mot de passe mais au contenu hostile : rien ne doit sortir de la cible."""

    def forger(self, *membres, manifeste=True):
        tampon = io.BytesIO()
        empreintes = {}
        with tarfile.open(fileobj=tampon, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for info, donnees in membres:
                if donnees is not None:
                    info.size = len(donnees)
                    if info.isreg():
                        empreintes[info.name] = hashlib.sha256(donnees).hexdigest()
                tar.addfile(info, io.BytesIO(donnees) if donnees is not None else None)
            if manifeste:
                corps = json.dumps(empreintes).encode()
                info = tarfile.TarInfo('MANIFESTE.json')
                info.size = len(corps)
                tar.addfile(info, io.BytesIO(corps))
        archive = self.dest / 'territoria-20261102-000000.tsv1'
        with open(archive, 'wb') as fichier:
            chiffre = sauvegarde._EcritureChiffree(fichier, 'm')
            chiffre.write(tampon.getvalue())
            chiffre.terminer()
        return archive

    @staticmethod
    def membre(nom, type_=tarfile.REGTYPE, lien='', mode_=0o644):
        info = tarfile.TarInfo(nom)
        info.type, info.linkname, info.mode = type_, lien, mode_
        return info

    def test_archive_forgee_valide_restauree(self):
        archive = self.forger((self.membre('territoria', tarfile.DIRTYPE, mode_=0o755), None),
                              (self.membre('territoria/a.txt'), b'a'))
        self.assertEqual(sauvegarde.verifier(archive, 'm'), {'fichiers': 1, 'conforme': True})
        sauvegarde.restaurer(archive, self.cible, 'm')
        self.assertEqual((self.cible / 'territoria' / 'a.txt').read_bytes(), b'a')

    def test_elements_refuses(self):
        cas = {
            'remontee': (self.membre('../evade.txt'), b'x'),
            'remontee interne': (self.membre('territoria/../../evade.txt'), b'x'),
            'absolu': (self.membre(str(self.base / 'evade.txt')), b'x'),
            'lien symbolique': (self.membre('territoria/lien', tarfile.SYMTYPE, str(self.base / 'evade.txt')), None),
            'lien relatif': (self.membre('territoria/lien', tarfile.SYMTYPE, '../../evade.txt'), None),
            'lien physique': (self.membre('territoria/dur', tarfile.LNKTYPE, 'territoria/a.txt'), None),
            'tube': (self.membre('territoria/tube', tarfile.FIFOTYPE), None),
            'peripherique': (self.membre('territoria/peripherique', tarfile.CHRTYPE), None),
            'racine inconnue': (self.membre('autre/a.txt'), b'x'),
            'fichier a la racine': (self.membre('evade.txt'), b'x'),
            'mot de passe': (self.membre('secrets/sauvegarde.motdepasse'), b'x'),
            'journal sqlite': (self.membre('territoria/territoire.sqlite3-wal'), b'x'),
            'point': (self.membre('territoria/./a.txt'), b'x'),
        }
        for nom, membre in cas.items():
            with self.subTest(nom):
                archive = self.forger((self.membre('territoria', tarfile.DIRTYPE), None),
                                      (self.membre('territoria/a.txt'), b'a'), membre)
                with self.assertRaisesRegex(ValueError, 'non conforme'):
                    sauvegarde.restaurer(archive, self.cible, 'm')
                with self.assertRaisesRegex(ValueError, 'non conforme'):
                    sauvegarde.verifier(archive, 'm')
                self.assertEqual(list(self.cible.iterdir()), [])
                self.assertFalse(os.path.lexists(self.base / 'evade.txt'))
                self.assertFalse(os.path.lexists(self.base.parent / 'evade.txt'))

    def test_doublon_refuse(self):
        archive = self.forger((self.membre('territoria/a.txt'), b'a'), (self.membre('territoria/a.txt'), b'b'))
        with self.assertRaisesRegex(ValueError, 'non conforme'):
            sauvegarde.restaurer(archive, self.cible, 'm')
        self.assertEqual(list(self.cible.iterdir()), [])

    def test_bits_speciaux_retires(self):
        archive = self.forger((self.membre('territoria', tarfile.DIRTYPE, mode_=0o1777), None),
                              (self.membre('territoria/a.txt', mode_=0o6777), b'a'))
        sauvegarde.restaurer(archive, self.cible, 'm')
        self.assertEqual(mode(self.cible / 'territoria'), 0o755)
        self.assertEqual(mode(self.cible / 'territoria' / 'a.txt'), 0o755)

    def test_manifeste_absent_ou_faux(self):
        for nom, archive in (
                ('absent', lambda: self.forger((self.membre('territoria/a.txt'), b'a'), manifeste=False)),
                ('faux', lambda: self.forger((self.membre('territoria/a.txt'), b'a'),
                                             (self.membre('MANIFESTE.json'), json.dumps({'territoria/a.txt': '0' * 64}).encode()),
                                             manifeste=False)),
                ('incomplet', lambda: self.forger((self.membre('territoria/a.txt'), b'a'),
                                                  (self.membre('MANIFESTE.json'), b'{}'), manifeste=False))):
            with self.subTest(nom):
                chemin = archive()
                self.assertFalse(sauvegarde.verifier(chemin, 'm')['conforme'])
                with self.assertRaisesRegex(ValueError, 'non conforme'):
                    sauvegarde.restaurer(chemin, self.cible, 'm')
                self.assertEqual(list(self.cible.iterdir()), [])

    def test_base_corrompue_non_conforme(self):
        archive = self.forger((self.membre('territoria', tarfile.DIRTYPE), None),
                              (self.membre('territoria/territoire.sqlite3'), b'SQLite format 3\x00' + bytes(100)))
        self.assertFalse(sauvegarde.verifier(archive, 'm')['conforme'])
        with self.assertRaisesRegex(ValueError, 'non conforme'):
            sauvegarde.restaurer(archive, self.cible, 'm')
        self.assertEqual(list(self.cible.iterdir()), [])


class RestaurationTests(Base):
    def test_cible_non_vide_refusee(self):
        a = self.creer()
        (self.cible / 'existant.txt').write_text('garde', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'vide'):
            sauvegarde.restaurer(a, self.cible, 'm')
        self.assertEqual(arborescence(self.cible), {'existant.txt': hashlib.sha256(b'garde').hexdigest()})

    def test_cible_absente_creee(self):
        cible = self.base / 'nouvelle'
        sauvegarde.restaurer(self.creer(), cible, 'm')
        self.assertTrue((cible / 'territoria' / 'territoire.sqlite3').is_file())

    def test_cible_absente_non_creee_en_cas_d_echec(self):
        cible = self.base / 'nouvelle'
        with self.assertRaises(ValueError):
            sauvegarde.restaurer(self.creer(), cible, 'autre')
        self.assertFalse(cible.exists())

    def test_forcer_remplace_les_donnees_et_garde_le_reste(self):
        a = self.creer()
        (self.cible / 'territoria' / 'ancien').mkdir(parents=True)
        (self.cible / 'territoria' / 'ancien' / 'perime.txt').write_text('ancien', encoding='utf-8')
        (self.cible / 'territoria' / 'territoire.sqlite3-wal').write_bytes(b'ancien journal')
        (self.cible / 'n8n').mkdir()
        (self.cible / 'n8n' / 'database.sqlite-shm').write_bytes(b'ancien')
        (self.cible / 'secrets').mkdir()
        (self.cible / 'secrets' / 'sauvegarde.motdepasse').write_text('garde', encoding='utf-8')
        os.chmod(self.cible / 'secrets' / 'sauvegarde.motdepasse', 0o600)
        os.utime(self.cible / 'secrets' / 'sauvegarde.motdepasse', (1700000000, 1700000000))
        (self.cible / 'secrets' / 'n8n.env').write_text('ancienne valeur', encoding='utf-8')
        (self.cible / 'echec-20261101').mkdir()
        sauvegarde.restaurer(a, self.cible, 'm', forcer=True)
        conserve = self.cible / 'secrets' / 'sauvegarde.motdepasse'
        self.assertEqual((mode(conserve), int(conserve.stat().st_mtime)), (0o600, 1700000000))
        restaure = arborescence(self.cible)
        source = arborescence(self.src)
        self.assertNotIn('territoria/ancien', restaure)
        self.assertNotIn('territoria/territoire.sqlite3-wal', restaure)
        self.assertNotIn('n8n/database.sqlite-shm', restaure)
        self.assertEqual(restaure['secrets/n8n.env'], source['secrets/n8n.env'])
        self.assertEqual((self.cible / 'secrets' / 'sauvegarde.motdepasse').read_text(encoding='utf-8'), 'garde')
        self.assertIn('echec-20261101', restaure)
        self.assertEqual(sorted(p.name for p in self.cible.iterdir()), ['echec-20261101', 'n8n', 'secrets', 'territoria'])

    def test_proprietaires_retablis_en_root(self):
        a = self.creer()
        st = (self.src / 'secrets' / 'n8n.env').stat()
        with patch.object(os, 'geteuid', return_value=0), patch.object(os, 'chown') as chown:
            sauvegarde.restaurer(a, self.cible, 'm')
        # Appliqués dans le dossier temporaire, avant que quoi que ce soit ne soit déplacé dans la cible.
        relatifs = [Path(appel.args[0]).relative_to(self.cible).parts for appel in chown.call_args_list]
        self.assertTrue(all(parties[0].startswith('.restauration-') for parties in relatifs))
        chemins = {'/'.join(parties[1:]): appel.args[1:] for parties, appel in zip(relatifs, chown.call_args_list)}
        self.assertEqual(set(chemins), FICHIERS_ATTENDUS | DOSSIERS_ATTENDUS)
        self.assertEqual(chemins['secrets/n8n.env'], (st.st_uid, st.st_gid))

    def test_proprietaire_des_secrets_conserves_en_root(self):
        a = self.creer()
        (self.cible / 'secrets').mkdir()
        conserve = self.cible / 'secrets' / 'sauvegarde.motdepasse'
        conserve.write_text('garde', encoding='utf-8')
        st = conserve.stat()
        with patch.object(os, 'geteuid', return_value=0), patch.object(os, 'chown') as chown:
            sauvegarde.restaurer(a, self.cible, 'm', forcer=True)
        appels = {Path(appel.args[0]).name: appel.args[1:] for appel in chown.call_args_list}
        self.assertEqual(appels['sauvegarde.motdepasse'], (st.st_uid, st.st_gid))

    def test_sans_root_aucun_changement_de_proprietaire(self):
        with patch.object(os, 'geteuid', return_value=1000), patch.object(os, 'chown') as chown:
            sauvegarde.restaurer(self.creer(), self.cible, 'm')
        chown.assert_not_called()


class RestaurationForceeTransactionnelleTests(Base):
    """restaurer(forcer=True) : quelle que soit l'étape qui échoue, territoria/, n8n/ et secrets/ de la cible restent
    identiques (mêmes fichiers, mêmes contenus, mêmes droits) et l'erreur est levée."""

    def setUp(self):
        super().setUp()
        self.archive = self.creer()
        self.preparer_cible()

    def preparer_cible(self):
        shutil.rmtree(self.cible)
        self.cible.mkdir()
        shutil.copytree(self.src / 'territoria', self.cible / 'territoria')
        (self.cible / 'territoria' / 'documents' / 'piece.txt').write_text('ancienne pièce', encoding='utf-8')
        (self.cible / 'territoria' / 'ancien.txt').write_text('ancien', encoding='utf-8')
        n8n = self.cible / 'n8n'
        n8n.mkdir()
        (n8n / 'config').write_text('ancienne clé', encoding='utf-8')
        (n8n / 'database.sqlite-wal').write_bytes(b'ancien journal')
        secrets = self.cible / 'secrets'
        (secrets / 'provision').mkdir(parents=True)
        os.chmod(secrets, 0o700)
        for nom, texte in (('n8n.env', 'ancienne valeur'), ('sauvegarde.motdepasse', 'garde'), ('ancien.env', 'X=1')):
            (secrets / nom).write_text(texte, encoding='utf-8')
            os.chmod(secrets / nom, 0o600)
        (secrets / 'provision' / 'credentials.json').write_text('[]', encoding='utf-8')
        (self.cible / 'echec-20261101').mkdir()

    def etat(self):
        """{chemin: (empreinte ou 'dossier', droits)} de tout ce que contient la cible, éléments cachés compris."""
        return {chemin: (valeur, mode(self.cible / chemin)) for chemin, valeur in arborescence(self.cible).items()}

    def echec_a_l_appel(self, rang, *autres):
        """Remplace `_deplacer` : les appels numéro `rang` (et `autres`) échouent, les autres se font."""
        deplacer, appels = sauvegarde._deplacer, []

        def remplacant(source, destination):
            appels.append((source, destination))
            if len(appels) == rang or len(appels) in autres:
                raise OSError('déplacement refusé')
            deplacer(source, destination)

        return patch.object(sauvegarde, '_deplacer', side_effect=remplacant), appels

    def test_reussite_puis_reference(self):
        appels = []
        deplacer = sauvegarde._deplacer
        with patch.object(sauvegarde, '_deplacer', side_effect=lambda s, d: (appels.append(d), deplacer(s, d))):
            sauvegarde.restaurer(self.archive, self.cible, 'm', forcer=True)
        self.assertEqual(len(appels), 6)  # ancien puis nouveau, pour territoria/, n8n/ et secrets/
        self.assertEqual(sorted(p.name for p in self.cible.iterdir()), ['echec-20261101', 'n8n', 'secrets', 'territoria'])

    def test_echec_de_chaque_deplacement(self):
        for rang in range(1, 7):
            with self.subTest(rang=rang):
                self.preparer_cible()
                avant = self.etat()
                remplacement, _ = self.echec_a_l_appel(rang)
                with remplacement, self.assertRaisesRegex(OSError, 'déplacement refusé'):
                    sauvegarde.restaurer(self.archive, self.cible, 'm', forcer=True)
                self.assertEqual(self.etat(), avant)

    def test_echec_des_droits_et_des_dates(self):
        utime = os.utime

        def refuser(chemin, *args, **kwargs):
            if Path(chemin).name == 'config':
                raise PermissionError('dates refusées')
            return utime(chemin, *args, **kwargs)

        avant = self.etat()
        with patch.object(os, 'utime', side_effect=refuser), self.assertRaises(PermissionError):
            sauvegarde.restaurer(self.archive, self.cible, 'm', forcer=True)
        self.assertEqual(self.etat(), avant)
        chmod = os.chmod
        with patch.object(os, 'chmod', side_effect=lambda c, m, **k: (_ for _ in ()).throw(PermissionError('x'))
                          if Path(c).name == 'territoria.env' else chmod(c, m, **k)), self.assertRaises(PermissionError):
            sauvegarde.restaurer(self.archive, self.cible, 'm', forcer=True)
        self.assertEqual(self.etat(), avant)

    def test_echec_pendant_la_fusion_de_secrets(self):
        avant = self.etat()
        for fonction in ('copy2', 'copytree'):
            with self.subTest(fonction), patch.object(shutil, fonction, side_effect=OSError('copie refusée')), \
                    self.assertRaisesRegex(OSError, 'copie refusée'):
                sauvegarde.restaurer(self.archive, self.cible, 'm', forcer=True)
            self.assertEqual(self.etat(), avant)

    def test_cible_vide_reste_vide(self):
        shutil.rmtree(self.cible)
        self.cible.mkdir()
        remplacement, _ = self.echec_a_l_appel(3)  # territoria/ et n8n/ déjà en place
        with remplacement, self.assertRaises(OSError):
            sauvegarde.restaurer(self.archive, self.cible, 'm')
        self.assertEqual(list(self.cible.iterdir()), [])

    def test_retour_arriere_impossible_ne_detruit_rien(self):
        # 4 : nouveau n8n/ refusé ; 5 : le retour de l'ancien n8n/ échoue aussi. Les données précédentes restent sur le
        # disque (dossier temporaire conservé) et l'erreur le dit.
        remplacement, _ = self.echec_a_l_appel(4, 5)
        with remplacement, self.assertRaises(sauvegarde.RestaurationInterrompue) as contexte:
            sauvegarde.restaurer(self.archive, self.cible, 'm', forcer=True)
        self.assertIsInstance(contexte.exception.__cause__, OSError)
        anciens = {p.read_text(encoding='utf-8') for p in self.cible.rglob('config')}
        self.assertIn('ancienne clé', anciens)
        self.assertEqual((self.cible / 'territoria' / 'documents' / 'piece.txt').read_text(encoding='utf-8'), 'ancienne pièce')
        self.assertEqual((self.cible / 'secrets' / 'sauvegarde.motdepasse').read_text(encoding='utf-8'), 'garde')

    def test_code_d_echec_de_la_commande(self):
        remplacement, _ = self.echec_a_l_appel(2)
        with remplacement:
            self.assertEqual(executer('restaurer', '--archive', str(self.archive), '--cible', str(self.cible), '--forcer',
                                      entree=b'm\n'), (1, 'ETAPE restaurer : ECHEC fichier_inaccessible\n'))
        remplacement, _ = self.echec_a_l_appel(4, 5)
        with remplacement:
            self.assertEqual(executer('restaurer', '--archive', str(self.archive), '--cible', str(self.cible), '--forcer',
                                      entree=b'm\n'), (1, 'ETAPE restaurer : ECHEC restauration_interrompue\n'))


class ElagageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = Path(self._tmp.name)

    def archive(self, moment):
        chemin = self.dest / f'territoria-{moment:%Y%m%d-%H%M%S}.tsv1'
        chemin.write_bytes(b'')
        return chemin

    def une_par_jour(self, debut, fin):
        jour = debut
        while jour <= fin:
            self.archive(datetime(jour.year, jour.month, jour.day, 12, 30))
            jour += timedelta(days=1)

    def test_elagage_7_jours_4_semaines(self):
        # une archive par jour du 1er septembre au 2 novembre 2026 → 7 quotidiennes + 4 hebdomadaires distinctes conservées
        self.une_par_jour(date(2026, 9, 1), date(2026, 11, 2))
        self.assertEqual(len(list(self.dest.glob('*.tsv1'))) - len(sauvegarde.elaguer(self.dest, date(2026, 11, 2))), 11)

    def test_archives_conservees(self):
        self.une_par_jour(date(2026, 9, 1), date(2026, 11, 2))
        supprimees = sauvegarde.elaguer(self.dest, date(2026, 11, 2))
        restantes = sorted(p.name[11:19] for p in self.dest.iterdir())
        # 2 novembre 2026 : lundi de la semaine ISO 45 ; les dimanches 25, 18, 11 et 4 octobre ferment les semaines 43 à 40.
        self.assertEqual(restantes, ['20261004', '20261011', '20261018', '20261025', '20261027', '20261028', '20261029',
                                     '20261030', '20261031', '20261101', '20261102'])
        self.assertEqual(len(supprimees), 63 - 11)
        self.assertTrue(all(not p.exists() for p in supprimees))
        self.assertEqual(supprimees, sorted(supprimees))

    def test_plus_recente_de_chaque_jour(self):
        matin = self.archive(datetime(2026, 11, 2, 8))
        midi = self.archive(datetime(2026, 11, 2, 12, 30))
        self.assertEqual(sauvegarde.elaguer(self.dest, date(2026, 11, 2)), [matin])
        self.assertTrue(midi.exists())

    def test_sauvegardes_interrompues_depuis_longtemps(self):
        self.une_par_jour(date(2025, 1, 1), date(2025, 3, 1))
        sauvegarde.elaguer(self.dest, date(2026, 11, 2))
        self.assertEqual(len(list(self.dest.iterdir())), 11)  # jamais tout effacer : les plus récentes restent

    def test_archive_datee_dans_le_futur_conservee(self):
        future = self.archive(datetime(2026, 11, 5, 12, 30))
        self.une_par_jour(date(2026, 10, 1), date(2026, 11, 2))
        sauvegarde.elaguer(self.dest, date(2026, 11, 2))
        self.assertTrue(future.exists())
        self.assertEqual(len(list(self.dest.iterdir())), 12)

    def test_seules_les_archives_sont_touchees(self):
        self.une_par_jour(date(2026, 9, 1), date(2026, 11, 2))
        autres = ['notes.txt', 'territoria-20260901-123000.tsv1.tmp', '.territoria-20260901-123000.tsv1.x.tmp',
                  'territoria-20261399-000000.tsv1', 'territoria-20260901-1230.tsv1', 'Territoria-20250101-123000.tsv1',
                  'autre-20260901-123000.tsv1', 'territoria-20260901-123000.tsv1.bak']
        for nom in autres:
            (self.dest / nom).write_bytes(b'')
        (self.dest / 'territoria-20260902-120000.tsv1x').mkdir()
        dossier = self.dest / 'territoria-20260903-120000.tsv1'
        dossier.mkdir()
        (self.dest / 'sous-dossier').mkdir()
        imbriquee = self.dest / 'sous-dossier' / 'territoria-20260901-120000.tsv1'
        imbriquee.write_bytes(b'')
        supprimees = sauvegarde.elaguer(self.dest, date(2026, 11, 2))
        for nom in autres:
            self.assertTrue((self.dest / nom).exists(), nom)
        self.assertTrue(dossier.is_dir())
        self.assertTrue(imbriquee.exists())
        self.assertTrue(all(p.parent == self.dest and sauvegarde.NOM_ARCHIVE.fullmatch(p.name) for p in supprimees))

    def test_dossier_vide(self):
        self.assertEqual(sauvegarde.elaguer(self.dest, date(2026, 11, 2)), [])


def executer(*argv, entree=b''):
    """Exécute `outils.main` avec `entree` sur l'entrée standard ; renvoie (code de sortie, texte imprimé)."""
    sortie = io.StringIO()
    with patch.object(sys, 'stdin', io.TextIOWrapper(io.BytesIO(entree), encoding='utf-8')), \
            contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(io.StringIO()):
        code = outils.main(list(argv))
    return code, sortie.getvalue()


class OutilsTests(Base):
    def sauvegarder(self, *options, entree=b'mdp-essai\n'):
        return executer('sauvegarder', '--source', str(self.src), '--destination', str(self.dest), *options, entree=entree)

    def archive_unique(self):
        archives = list(self.dest.iterdir())
        self.assertEqual(len(archives), 1)
        return archives[0]

    def test_options_par_defaut(self):
        parseur = outils.construire_parseur()
        args = parseur.parse_args(['sauvegarder'])
        self.assertEqual((args.source, args.destination, args.depuis_fichier), (Path('/source'), Path('/destination'), None))
        args = parseur.parse_args(['verifier-sauvegarde'])
        self.assertEqual((args.archive, args.depuis_fichier), (Path('/destination'), None))
        args = parseur.parse_args(['restaurer'])
        self.assertEqual((args.archive, args.cible, args.forcer, args.depuis_fichier), (Path('/destination'), Path('/cible'), False, None))
        self.assertEqual(parseur.parse_args(['elaguer']).destination, Path('/destination'))

    def test_sauvegarder_lit_le_mot_de_passe_sur_l_entree_standard(self):
        self.assertEqual(self.sauvegarder(), (0, 'ETAPE sauvegarder : OK\n'))
        self.assertEqual(sauvegarde.verifier(self.archive_unique(), 'mdp-essai')['conforme'], True)

    def test_une_seule_fin_de_ligne_retiree(self):
        for entree, mot in ((b'mdp\n\n', 'mdp\n'), (b'mdp\r\n', 'mdp'), (b' mdp ', ' mdp ')):
            with self.subTest(entree=entree):
                for archive in self.dest.iterdir():
                    archive.unlink()
                self.assertEqual(self.sauvegarder(entree=entree)[0], 0)
                self.assertTrue(sauvegarde.verifier(self.archive_unique(), mot)['conforme'])

    def test_mot_de_passe_depuis_un_fichier(self):
        fichier = self.src / 'secrets' / 'sauvegarde.motdepasse'
        fichier.write_text('mdp-fichier\n', encoding='utf-8')
        self.assertEqual(self.sauvegarder('--depuis-fichier', str(fichier), entree=b'ignore\n'), (0, 'ETAPE sauvegarder : OK\n'))
        self.assertTrue(sauvegarde.verifier(self.archive_unique(), 'mdp-fichier')['conforme'])

    def test_saisie_masquee_dans_un_terminal(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        sortie, erreurs = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', Terminal()), patch('getpass.getpass', return_value='mdp-masque') as getpass, \
                contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreurs):
            code = outils.main(['sauvegarder', '--source', str(self.src), '--destination', str(self.dest)])
        self.assertEqual((code, sortie.getvalue()), (0, 'ETAPE sauvegarder : OK\n'))
        getpass.assert_called_once()
        self.assertIs(getpass.call_args.kwargs.get('stream'), erreurs)  # invite hors de la sortie standard
        self.assertTrue(sauvegarde.verifier(self.archive_unique(), 'mdp-masque')['conforme'])

    def test_mot_de_passe_absent(self):
        self.assertEqual(self.sauvegarder(entree=b''), (1, 'ETAPE sauvegarder : ECHEC mot_de_passe_absent\n'))
        self.assertEqual(self.sauvegarder(entree=b'\n'), (1, 'ETAPE sauvegarder : ECHEC mot_de_passe_absent\n'))
        self.assertEqual(self.sauvegarder('--depuis-fichier', str(self.base / 'absent')),
                         (1, 'ETAPE sauvegarder : ECHEC mot_de_passe_absent\n'))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_mot_de_passe_illisible(self):
        self.assertEqual(self.sauvegarder(entree=b'\xff\xfe\n'), (1, 'ETAPE sauvegarder : ECHEC mot_de_passe_illisible\n'))
        self.assertEqual(self.sauvegarder('--depuis-fichier', str(self.src / 'secrets')),
                         (1, 'ETAPE sauvegarder : ECHEC mot_de_passe_illisible\n'))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_aucun_mot_de_passe_en_argument(self):
        self.assertEqual(self.sauvegarder('--mot-de-passe', 'x'), (1, 'ETAPE sauvegarder : ECHEC arguments_invalides\n'))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_le_mot_de_passe_n_apparait_jamais(self):
        sortie = io.StringIO()
        erreurs = io.StringIO()
        with patch.object(sys, 'stdin', io.TextIOWrapper(io.BytesIO(b'mdp-tres-confidentiel\n'), encoding='utf-8')), \
                contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreurs):
            outils.main(['sauvegarder', '--source', str(self.src), '--destination', str(self.dest)])
            outils.main(['verifier-sauvegarde', '--archive', str(self.dest)])  # entrée déjà lue : échec
        self.assertNotIn('confidentiel', sortie.getvalue() + erreurs.getvalue())
        archive = self.archive_unique()
        self.assertNotIn(b'confidentiel', archive.read_bytes())

    def test_source_et_destination(self):
        self.assertEqual(executer('sauvegarder', '--source', str(self.base / 'rien'), '--destination', str(self.dest), entree=b'm\n'),
                         (1, 'ETAPE sauvegarder : ECHEC source_invalide\n'))
        self.assertEqual(executer('sauvegarder', '--source', str(self.src), '--destination', str(self.base / 'rien'), entree=b'm\n'),
                         (1, 'ETAPE sauvegarder : ECHEC destination_absente\n'))
        self.assertFalse((self.base / 'rien').exists())

    def test_base_illisible(self):
        (self.src / 'territoria' / 'territoire.sqlite3').write_bytes(b'pas une base' * 100)
        self.assertEqual(self.sauvegarder(), (1, 'ETAPE sauvegarder : ECHEC base_illisible\n'))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_archive_existante(self):
        with patch.object(outils, 'datetime') as horloge:
            horloge.now.return_value = datetime(2026, 11, 2, 12, 30)
            self.assertEqual(self.sauvegarder()[0], 0)
            self.assertEqual(self.sauvegarder(), (1, 'ETAPE sauvegarder : ECHEC archive_existante\n'))

    def test_verifier_sauvegarde(self):
        self.sauvegarder()
        archive = self.archive_unique()
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(archive), entree=b'mdp-essai\n'),
                         (0, 'ETAPE verifier-sauvegarde : OK\n'))
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(archive), entree=b'autre\n'),
                         (1, 'ETAPE verifier-sauvegarde : ECHEC archive_illisible\n'))
        archive.write_bytes(archive.read_bytes()[:-100])
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(archive), entree=b'mdp-essai\n'),
                         (1, 'ETAPE verifier-sauvegarde : ECHEC archive_incomplete\n'))

    def test_verifier_sauvegarde_choisit_la_plus_recente_du_dossier(self):
        with patch.object(outils, 'datetime') as horloge:
            horloge.now.return_value = datetime(2026, 11, 1, 12, 30)
            self.sauvegarder(entree=b'ancien\n')
            horloge.now.return_value = datetime(2026, 11, 2, 12, 30)
            self.sauvegarder(entree=b'recent\n')
        (self.dest / 'notes.txt').write_text('x', encoding='utf-8')
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(self.dest), entree=b'recent\n'),
                         (0, 'ETAPE verifier-sauvegarde : OK\n'))
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(self.dest), entree=b'ancien\n'),
                         (1, 'ETAPE verifier-sauvegarde : ECHEC archive_illisible\n'))

    def test_verifier_sauvegarde_sans_archive(self):
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(self.dest), entree=b'm\n'),
                         (1, 'ETAPE verifier-sauvegarde : ECHEC archive_absente\n'))
        self.assertEqual(executer('verifier-sauvegarde', '--archive', str(self.dest / 'x.tsv1'), entree=b'm\n'),
                         (1, 'ETAPE verifier-sauvegarde : ECHEC archive_absente\n'))

    def test_restaurer(self):
        self.sauvegarder()
        archive = str(self.archive_unique())
        self.assertEqual(executer('restaurer', '--archive', archive, '--cible', str(self.cible), entree=b'autre\n'),
                         (1, 'ETAPE restaurer : ECHEC archive_illisible\n'))
        self.assertEqual(list(self.cible.iterdir()), [])
        self.assertEqual(executer('restaurer', '--archive', archive, '--cible', str(self.cible), entree=b'mdp-essai\n'),
                         (0, 'ETAPE restaurer : OK\n'))
        self.assertTrue((self.cible / 'territoria' / 'territoire.sqlite3').is_file())
        # Cible désormais non vide : refus avant même de lire le mot de passe.
        self.assertEqual(executer('restaurer', '--archive', archive, '--cible', str(self.cible), entree=b''),
                         (1, 'ETAPE restaurer : ECHEC cible_non_vide\n'))
        self.assertEqual(executer('restaurer', '--archive', archive, '--cible', str(self.cible), '--forcer', entree=b'mdp-essai\n'),
                         (0, 'ETAPE restaurer : OK\n'))

    def test_elaguer(self):
        jour = date(2026, 9, 1)
        while jour <= date(2026, 10, 30):
            (self.dest / f'territoria-{jour:%Y%m%d}-123000.tsv1').write_bytes(b'')
            jour += timedelta(days=1)
        with patch.object(outils, 'date') as calendrier:
            calendrier.today.return_value = date(2026, 10, 30)
            self.assertEqual(executer('elaguer', '--destination', str(self.dest)), (0, 'ETAPE elaguer : OK\n'))
        self.assertEqual(len(list(self.dest.iterdir())), 11)
        self.assertEqual(executer('elaguer', '--destination', str(self.base / 'rien')), (1, 'ETAPE elaguer : ECHEC destination_absente\n'))

    def test_une_seule_ligne_par_commande(self):
        for argv in (('sauvegarder', '--source', str(self.src), '--destination', str(self.dest)),
                     ('verifier-sauvegarde', '--archive', str(self.dest)),
                     ('restaurer', '--archive', str(self.dest), '--cible', str(self.cible)),
                     ('elaguer', '--destination', str(self.dest))):
            with self.subTest(argv[0]):
                _, texte = executer(*argv, entree=b'mdp-essai\n')
                self.assertRegex(texte, rf'\AETAPE {argv[0]} : (OK|ECHEC [a-z_]+)\n\Z')


if __name__ == '__main__':
    unittest.main()
