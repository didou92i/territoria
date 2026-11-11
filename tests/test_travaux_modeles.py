# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import functools
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from travaux import modeles

CONTENU = b'poids du modele ' * 200


class _Discret(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class ModelesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        racine = Path(self.tmp.name)
        (racine / 'serveur').mkdir()
        (racine / 'serveur' / 'poids.bin').write_bytes(CONTENU)
        self.dossier = racine / 'modeles'
        self.serveur = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(_Discret, directory=str(racine / 'serveur')))
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.serveur.server_address[1]}/poids.bin'

    def tearDown(self):
        self.serveur.shutdown()
        self.serveur.server_close()
        self.tmp.cleanup()

    def manifeste(self, sha256=None, chemin='a/poids.bin', licence='MIT', taille=None):
        return {'version': 1, 'modeles': {'essai': {'licence': licence, 'fichiers': [
            {'chemin': chemin, 'url': self.url, 'taille': len(CONTENU) if taille is None else taille,
             'sha256': sha256 or hashlib.sha256(CONTENU).hexdigest()}]}}}

    def ecrire(self, manifeste):
        chemin = Path(self.tmp.name) / 'manifeste.json'
        chemin.write_text(json.dumps(manifeste), encoding='utf-8')
        return chemin

    def test_telecharge_verifie_et_ne_retelecharge_pas(self):
        m = self.manifeste()
        self.assertEqual(modeles.telecharger(m, self.dossier, ['essai']), ['a/poids.bin'])
        self.assertEqual((self.dossier / 'a' / 'poids.bin').read_bytes(), CONTENU)
        self.assertTrue(modeles.verifier(m, self.dossier, ['essai']))
        self.assertEqual(modeles.telecharger(m, self.dossier, ['essai']), [])  # déjà présent et intact

    def test_fichier_altere_retelecharge(self):
        m = self.manifeste()
        modeles.telecharger(m, self.dossier, ['essai'])
        (self.dossier / 'a' / 'poids.bin').write_bytes(b'abime')
        self.assertFalse(modeles.verifier(m, self.dossier, ['essai']))
        self.assertEqual(modeles.telecharger(m, self.dossier, ['essai']), ['a/poids.bin'])

    def test_empreinte_fausse_refusee_sans_fichier_laisse(self):
        with self.assertRaisesRegex(ValueError, 'empreinte'):
            modeles.telecharger(self.manifeste(sha256='0' * 64), self.dossier, ['essai'])
        self.assertEqual(sorted(p.name for p in self.dossier.rglob('*')), ['a'])  # ni poids.bin ni .partiel

    def test_empreinte_non_renseignee_refusee(self):
        m = self.manifeste()
        m['modeles']['essai']['fichiers'][0]['sha256'] = None
        with self.assertRaisesRegex(ValueError, 'manifeste'):
            modeles.telecharger(m, self.dossier, ['essai'])

    def test_manifeste_refuse_chemin_sortant_licence_inconnue_et_version(self):
        for mauvais in (self.manifeste(chemin='../x'), self.manifeste(chemin='/etc/x'), self.manifeste(licence='Propriétaire'),
                        {'version': 2, 'modeles': {}}):
            with self.subTest(mauvais=mauvais), self.assertRaises(ValueError):
                modeles.lire_manifeste(self.ecrire(mauvais))
        self.assertEqual(modeles.lire_manifeste(self.ecrire(self.manifeste()))['version'], 1)

    def test_manifeste_du_depot(self):
        m = modeles.lire_manifeste(Path(__file__).resolve().parents[1] / 'conteneurs' / 'modeles-audio.json')
        self.assertEqual(set(m['modeles']), {'large-v3-turbo', 'medium', 'segmentation', 'empreintes'})
        self.assertTrue(all(f['url'].startswith('https://') for modele in m['modeles'].values() for f in modele['fichiers']))

    def test_ligne_de_commande_contrat_etape(self):
        commande = [sys.executable, '-m', 'travaux.modeles', 'telecharger', '--manifeste', str(self.ecrire(self.manifeste())),
                    '--dossier', str(self.dossier), '--modeles', 'essai']
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'app'))
        sortie = subprocess.run(commande, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual((sortie.returncode, sortie.stdout.strip()), (0, 'ETAPE modeles-audio : OK'))
        sortie = subprocess.run(commande[:-1] + ['inconnu'], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual((sortie.returncode, sortie.stdout.strip()), (1, 'ETAPE modeles-audio : ECHEC manifeste'))


class _Reponse(io.BytesIO):
    """Réponse factice : statut HTTP et corps, éventuellement coupé (connexion perdue) après `coupure` octets."""

    def __init__(self, corps, status=200, coupure=None):
        super().__init__(corps)
        self.status, self.coupure = status, coupure

    def read(self, taille=-1):
        if self.coupure is not None and self.tell() >= self.coupure:
            raise ConnectionResetError('connexion perdue')
        return super().read(taille if self.coupure is None else min(taille, self.coupure - self.tell()))


class RepriseTests(unittest.TestCase):
    """Reprise d'un téléchargement interrompu (spécification, § 11) : le fichier partiel est gardé et complété."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dossier = Path(self.tmp.name)
        self.m = {'version': 1, 'modeles': {'essai': {'licence': 'MIT', 'fichiers': [
            {'chemin': 'a/poids.bin', 'url': 'https://exemple.invalid/poids.bin', 'taille': len(CONTENU),
             'sha256': hashlib.sha256(CONTENU).hexdigest()}]}}}
        (self.dossier / 'a').mkdir()
        self.partiel = self.dossier / 'a' / 'poids.bin.partiel'

    def test_coupure_reseau_garde_le_fichier_partiel_puis_reprise_par_plage(self):
        demandes = []

        def ouvrir(requete, timeout):
            demandes.append(requete.get_header('Range'))
            if len(demandes) == 1:
                return _Reponse(CONTENU, coupure=10)
            return _Reponse(CONTENU[10:], status=206)

        with self.assertRaises(OSError):
            modeles.telecharger(self.m, self.dossier, ['essai'], ouvrir=ouvrir)
        self.assertEqual(self.partiel.read_bytes(), CONTENU[:10])
        self.assertEqual(modeles.telecharger(self.m, self.dossier, ['essai'], ouvrir=ouvrir), ['a/poids.bin'])
        self.assertEqual(demandes, [None, 'bytes=10-'])
        self.assertEqual((self.dossier / 'a' / 'poids.bin').read_bytes(), CONTENU)
        self.assertFalse(self.partiel.exists())

    def test_serveur_sans_reprise_recommence_au_debut(self):
        self.partiel.write_bytes(CONTENU[:10])
        modeles.telecharger(self.m, self.dossier, ['essai'], ouvrir=lambda requete, timeout: _Reponse(CONTENU, status=200))
        self.assertEqual((self.dossier / 'a' / 'poids.bin').read_bytes(), CONTENU)

    def test_disque_plein_ou_droits_distingues_du_reseau(self):
        import errno
        import urllib.error
        from unittest import mock
        for erreur, code in ((PermissionError(13, 'Permission denied'), 'disque'), (OSError(errno.ENOSPC, 'No space left'), 'disque'),
                             (urllib.error.URLError('injoignable'), 'reseau'), (TimeoutError('délai'), 'reseau')):
            with self.subTest(code=code), mock.patch.object(modeles, 'telecharger', side_effect=erreur), \
                    mock.patch.object(modeles, 'lire_manifeste', return_value=self.m), \
                    mock.patch('sys.stdout', new_callable=io.StringIO) as sortie:
                self.assertEqual(modeles.main(['telecharger', '--manifeste', 'm', '--dossier', 'd', '--modeles', 'essai']), 1)
            self.assertEqual(sortie.getvalue().strip(), f'ETAPE modeles-audio : ECHEC {code}')


if __name__ == '__main__':
    unittest.main()
