# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Scripts d'installation POSIX (install/*.sh) : structure, diagnostic, simulation sans effet, saisies masquées.

Aucun test ne modifie la machine : HOME et TERRITORIA_DONNEES_HOTE pointent vers des dossiers temporaires, et des
commandes factices (docker, podman, lms, brew, launchctl, crontab, sudo…) placées en tête du PATH consignent leurs
arguments au lieu d'agir. Les étapes interactives sont jouées dans un pseudo-terminal.
"""
import fcntl
import json
import os
import platform
import plistlib
import pty
import re
import select
import shutil
import stat
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
INSTALL = RACINE / 'install'
NOMS = ('commun', 'diagnostic', 'installer', 'demarrer', 'arreter', 'sauvegarder', 'restaurer', 'mettre-a-jour',
        'demonstration')  # demonstration.sh : serveur Linux seulement, sans jumeau .ps1
BASH = shutil.which('bash') or '/bin/bash'
CLES = ('systeme', 'architecture', 'memoire_go', 'gpu', 'disque_libre_go', 'port_8878', 'moteur', 'docker_desktop',
        'lms', 'chiffrement_disque', 'palier_modele', 'transcription', 'modeles_audio', 'ia_en_ligne')
ETAPES = ('moteur', 'lmstudio', 'mistral', 'secrets', 'legifrance', 'demarrer', 'n8n', 'demo', 'verifier', 'raccourci',
          'sauvegarde-auto')
FACTICES = ('docker', 'podman', 'lms', 'brew', 'launchctl', 'crontab', 'sudo', 'apt-get', 'dnf', 'plutil', 'open',
            'xdg-open', 'curl', 'gio', 'systemctl', 'osascript')
AVERTISSEMENT = 'avertissement=licence_docker_desktop_payante_pour_les_administrations'
SYSTEME = {'Darwin': 'macos', 'Linux': 'linux'}.get(platform.system(), 'autre')


def etat_du_depot():
    return subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=RACINE,
                          capture_output=True, text=True).stdout


class Bac:
    """Dossiers temporaires d'un essai : HOME vide, données (chemin avec une espace, non créé), commandes factices."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix='territoria-test-install-'))
        self.home = self.base / 'maison'
        self.home.mkdir()
        self.donnees = self.base / 'territoria donnees'
        self.factices = self.base / 'factices'
        self.factices.mkdir()
        self.journal = self.base / 'appels.log'
        for nom in FACTICES:
            chemin = self.factices / nom
            # crontab - : le contenu reçu est consigné aussi ; docker info répond (moteur « en marche »).
            chemin.write_text('#!/bin/sh\n'
                              'printf "%s\\n" "$(basename "$0") $*" >>"$JOURNAL_FACTICES"\n'
                              'if [ "$(basename "$0")" = crontab ] && [ "$1" = - ]; then cat >>"$JOURNAL_FACTICES"; fi\n'
                              'exit 0\n')
            chemin.chmod(0o755)

    def env(self, **autres):
        env = {k: v for k, v in os.environ.items() if not k.startswith('TERRITORIA_')}
        env.update(HOME=str(self.home), TERRITORIA_DONNEES_HOTE=str(self.donnees), JOURNAL_FACTICES=str(self.journal),
                   PATH=f'{self.factices}:/usr/bin:/bin:/usr/sbin:/sbin', LC_ALL='C.UTF-8' if SYSTEME == 'linux' else 'fr_FR.UTF-8')
        env.update(autres)
        return env

    def appels(self):
        return self.journal.read_text() if self.journal.exists() else ''

    def contenu_home(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob('*'))

    def nettoyer(self):
        shutil.rmtree(self.base, ignore_errors=True)


def lancer(args, env, entree=subprocess.DEVNULL, delai=60):
    """Lance un script sans terminal de contrôle (nouvelle session : /dev/tty est inaccessible)."""
    return subprocess.run([BASH, *map(str, args)], env=env, stdin=entree, capture_output=True, text=True,
                          timeout=delai, start_new_session=True, cwd=RACINE)


def derniere_ligne(texte):
    lignes = texte.rstrip('\n').split('\n')
    return lignes[-1] if lignes else ''


def bash_commun(code, env):
    """Exécute du bash après avoir sourcé install/commun.sh."""
    return subprocess.run([BASH, '-c', f'. "{INSTALL}/commun.sh"\n{code}'], env=env, capture_output=True, text=True,
                          timeout=60, start_new_session=True, cwd=RACINE)


def bash_installer(code, env):
    """Exécute du bash après avoir sourcé install/installer.sh (fonctions seulement : aucune étape n'est lancée)."""
    return subprocess.run([BASH, '-c', f'. "{INSTALL}/installer.sh"\n{code}'], env=env, capture_output=True, text=True,
                          timeout=60, start_new_session=True, cwd=RACINE)


def commande_factice(dossier, nom, corps):
    chemin = Path(dossier) / nom
    chemin.write_text('#!/bin/sh\n' + corps)
    chemin.chmod(0o755)
    return chemin


class Terminal:
    """Script joué dans un pseudo-terminal : il le voit comme /dev/tty, comme dans le terminal de l'utilisateur."""

    def __init__(self, args, env):
        self.maitre, self.esclave = pty.openpty()

        def terminal_de_controle():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen([BASH, *map(str, args)], env=env, stdin=self.esclave, stdout=self.esclave,
                                     stderr=self.esclave, start_new_session=True, preexec_fn=terminal_de_controle,
                                     cwd=RACINE, close_fds=True)
        self.sortie = b''

    def _lire(self, delai):
        prets, _, _ = select.select([self.maitre], [], [], delai)
        if prets:
            try:
                morceau = os.read(self.maitre, 65536)
            except OSError:
                return False
            self.sortie += morceau
            return bool(morceau)
        return False

    def attendre(self, texte, delai=30):
        fin = time.time() + delai
        while texte.encode() not in self.sortie:
            if time.time() > fin or (self.proc.poll() is not None and not self._lire(0.2)):
                raise AssertionError(f'invite absente : {texte!r} ; sortie : {self.sortie.decode(errors="replace")}')
            self._lire(0.2)

    def repondre(self, texte, masque=False):
        if masque:  # n'écrire le secret qu'une fois l'écho coupé par `read -s`
            fin = time.time() + 15
            while termios.tcgetattr(self.esclave)[3] & termios.ECHO:
                if time.time() > fin:
                    raise AssertionError('l\'écho du terminal n\'a pas été coupé pour une saisie masquée')
                time.sleep(0.05)
        os.write(self.maitre, texte.encode() + b'\n')

    def finir(self, delai=60):
        fin = time.time() + delai
        while self.proc.poll() is None and time.time() < fin:
            self._lire(0.2)
        if self.proc.poll() is None:
            self.proc.kill()
            raise AssertionError('le script ne s\'est pas terminé')
        while self._lire(0.1):
            pass
        os.close(self.esclave)
        os.close(self.maitre)
        return self.proc.returncode, self.sortie.decode(errors='replace').replace('\r\n', '\n')


class StructureTests(unittest.TestCase):
    def test_scripts_executables_en_mode_strict_et_syntaxe_valide(self):
        for nom in NOMS:
            chemin = INSTALL / f'{nom}.sh'
            with self.subTest(script=nom):
                texte = chemin.read_text(encoding='utf-8')
                self.assertTrue(os.access(chemin, os.X_OK))
                self.assertTrue(texte.startswith('#!/usr/bin/env bash\n'))
                self.assertRegex(texte, re.compile(r'^set -euo pipefail$', re.M))
                self.assertEqual(subprocess.run([BASH, '-n', str(chemin)], capture_output=True).returncode, 0)
                if nom != 'commun':
                    # set -euo pipefail puis commun.sh, avant tout le reste
                    self.assertRegex(texte, r'\nset -euo pipefail\n(#[^\n]*\n)*\. "\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd -P\)/commun\.sh"\n')

    def test_compatibles_avec_le_bash_3_2_de_macos(self):
        for nom in NOMS:
            texte = (INSTALL / f'{nom}.sh').read_text(encoding='utf-8')
            texte = '\n'.join(ligne for ligne in texte.splitlines() if not ligne.lstrip().startswith('#'))
            with self.subTest(script=nom):
                for interdit in ('declare -A', 'mapfile', 'readarray', '<<<', '|&', 'coproc', ',,}', '^^}', 'local -n'):
                    self.assertNotIn(interdit, texte)
                self.assertNotRegex(texte, r'\btimeout ')  # absent de macOS : avec_delai
                self.assertNotRegex(texte, r'\| *grep -[a-zA-Z]*q')  # grep -q derrière un tube avec pipefail

    def test_commun_ne_fait_rien_quand_on_le_source(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        avant = etat_du_depot()
        resultat = bash_commun('printf "%s|%s|%s|%s\\n" "$RACINE" "$PROJET" "$TERRITORIA_DONNEES_HOTE" "$S"', bac.env())
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertEqual(resultat.stdout, f'{RACINE}|territoria|{bac.donnees}|{bac.donnees}/secrets\n')
        self.assertEqual(resultat.stderr, '')
        self.assertFalse(bac.donnees.exists())
        self.assertEqual(bac.contenu_home(), [])
        self.assertEqual(bac.appels(), '')
        self.assertEqual(etat_du_depot(), avant)
        # Par défaut, territoria-donnees est à côté du dépôt.
        env = bac.env()
        del env['TERRITORIA_DONNEES_HOTE']
        resultat = bash_commun('printf "%s" "$TERRITORIA_DONNEES_HOTE"', env)
        self.assertEqual(resultat.stdout, str(RACINE.parent / 'territoria-donnees'))

    def test_donnees_jamais_dans_le_depot(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        resultat = lancer([INSTALL / 'installer.sh', '--etape', 'secrets', '--simulation'],
                          bac.env(TERRITORIA_DONNEES_HOTE=str(RACINE / 'territoria-donnees')))
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE secrets : ECHEC donnees_dans_le_depot')
        self.assertFalse((RACINE / 'territoria-donnees').exists())


class ModelesTests(unittest.TestCase):
    def test_trois_paliers_gemma_4_gguf_q4_k_m(self):
        modeles = json.loads((INSTALL / 'modeles.json').read_text(encoding='utf-8'))
        self.assertEqual(list(modeles), ['petit', 'moyen', 'grand'])
        for palier, identifiant in modeles.items():
            with self.subTest(palier=palier):
                self.assertRegex(identifiant, r'^google/gemma-4-[a-z0-9-]+@q4_k_m$')
        self.assertEqual(len(set(modeles.values())), 3)

    def test_palier_selon_la_memoire(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        resultat = bash_commun('for m in 4 11 12 27 28 64; do printf "%s=%s " "$m" "$(palier_modele "$m")"; done\n'
                               'for p in petit moyen grand; do printf "%s=%s " "$p" "$(modele_du_palier "$p")"; done',
                               bac.env())
        modeles = json.loads((INSTALL / 'modeles.json').read_text(encoding='utf-8'))
        self.assertEqual(resultat.stdout.split(), ['4=petit', '11=petit', '12=moyen', '27=moyen', '28=grand', '64=grand']
                         + [f'{p}={modeles[p]}' for p in ('petit', 'moyen', 'grand')])


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bac = Bac()
        env = cls.bac.env(PATH=os.environ.get('PATH', '/usr/bin:/bin'))  # vraies sondes de la machine, lecture seule
        cls.resultat = lancer([INSTALL / 'diagnostic.sh'], env)

    @classmethod
    def tearDownClass(cls):
        cls.bac.nettoyer()

    def test_treize_cles_une_par_ligne_puis_avertissement_eventuel(self):
        self.assertEqual(self.resultat.returncode, 0, self.resultat.stderr)
        lignes = self.resultat.stdout.splitlines()
        self.assertEqual([ligne.split('=', 1)[0] for ligne in lignes[:len(CLES)]], list(CLES))
        valeurs = dict(ligne.split('=', 1) for ligne in lignes[:len(CLES)])
        self.assertIn(valeurs['transcription'], ('en_marche', 'arrete', 'inconnu'))
        self.assertEqual(valeurs['modeles_audio'], 'absents')  # dossier de données du bac : rien de téléchargé
        suite = lignes[len(CLES):]
        attendu = suite == [AVERTISSEMENT] if valeurs['docker_desktop'] == 'oui' else suite == []
        self.assertTrue(attendu, suite)

    def test_valeurs(self):
        valeurs = dict(ligne.split('=', 1) for ligne in self.resultat.stdout.splitlines()[:len(CLES)])
        formes = {'systeme': r'macos|linux|autre', 'architecture': r'[a-z0-9_]+', 'memoire_go': r'\d+',
                  'gpu': r'[a-z0-9_]+', 'disque_libre_go': r'\d+|inconnu', 'port_8878': r'libre|occupe',
                  'moteur': r'docker|podman|aucun', 'docker_desktop': r'oui|non', 'lms': r'present|absent',
                  'chiffrement_disque': r'actif|inactif|inconnu', 'palier_modele': r'petit|moyen|grand',
                  'ia_en_ligne': r'aucune|mistral'}
        for cle, forme in formes.items():
            self.assertRegex(valeurs[cle], rf'^(?:{forme})$', cle)
        self.assertEqual(valeurs['systeme'], SYSTEME)
        memoire = int(valeurs['memoire_go'])
        self.assertEqual(valeurs['palier_modele'], 'petit' if memoire < 12 else 'moyen' if memoire < 28 else 'grand')

    def test_sans_modification(self):
        self.assertFalse(self.bac.donnees.exists())
        self.assertEqual(self.bac.contenu_home(), [])


class SimulationTests(unittest.TestCase):
    """--simulation : dernière ligne « ETAPE <nom> : OK », aucune commande lancée, rien créé, aucune question."""

    def verifier_sans_effet(self, args, nom):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        voisin = (RACINE.parent / 'territoria-donnees').exists()
        avant = etat_du_depot()
        resultat = lancer(args, bac.env())
        self.assertEqual(resultat.returncode, 0, resultat.stdout + resultat.stderr)
        self.assertEqual(derniere_ligne(resultat.stdout), f'ETAPE {nom} : OK')
        self.assertEqual(sum(1 for ligne in resultat.stdout.splitlines() if ligne.startswith('ETAPE ')), 1)
        for ligne in resultat.stdout.splitlines()[:-1]:
            self.assertRegex(ligne, r'^(\+ |# |\|   )', ligne)  # commandes, explications, contenu des fichiers
        self.assertEqual(bac.appels(), '')  # aucune commande factice appelée : rien n'a été lancé
        self.assertFalse(bac.donnees.exists())
        self.assertEqual(bac.contenu_home(), [])
        self.assertEqual((RACINE.parent / 'territoria-donnees').exists(), voisin)
        self.assertEqual(etat_du_depot(), avant)
        return resultat.stdout

    def test_chaque_etape(self):
        for etape in ETAPES:
            with self.subTest(etape=etape):
                self.verifier_sans_effet([INSTALL / 'installer.sh', '--etape', etape, '--simulation'], etape)

    def test_legifrance_question_sans_identifiants_et_migration(self):
        sortie = self.verifier_sans_effet([INSTALL / 'installer.sh', '--etape', 'legifrance', '--simulation'], 'legifrance')
        self.assertIn('Autoriser les connecteurs publics (Légifrance, data.gouv.fr)', sortie)
        self.assertNotIn('saisie masquée', sortie)
        self.assertRegex(sortie, r'(?m)^\+ retirer PISTE_CLIENT_ID de .*territoria\.env')
        self.assertRegex(sortie, r'(?m)^\+ retirer PISTE_CLIENT_SECRET de .*territoria\.env')
        self.assertRegex(sortie, r'(?m)^\+ écrire TERRITORIA_HORS_LIGNE=0 dans .*compose\.env')
        self.assertRegex(sortie, r'(?m)^\+ écrire TERRITORIA_HORS_LIGNE=1 dans .*compose\.env')

    def test_options_des_etapes(self):
        sortie = self.verifier_sans_effet([INSTALL / 'installer.sh', '--etape', 'raccourci', '--auto', '--simulation'],
                                          'raccourci')
        self.assertIn('fr.territoria.demarrer.plist' if SYSTEME == 'macos' else 'autostart/territoria.desktop', sortie)
        sortie = self.verifier_sans_effet([INSTALL / 'installer.sh', '--etape', 'lmstudio', '--palier', 'grand',
                                           '--simulation'], 'lmstudio')
        self.assertIn('google/gemma-4-26b-a4b@q4_k_m --gguf --yes', sortie)
        self.assertIn('server start --port 1234 --bind', sortie)
        self.assertIn('TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1', sortie)
        self.assertIn('TERRITORIA_MODELE=google/gemma-4-26b-a4b', sortie)
        self.assertIn('marqueur-territoria-', sortie)

    def test_autres_scripts(self):
        self.verifier_sans_effet([INSTALL / 'demarrer.sh', '--ouvrir', '--simulation'], 'demarrer')
        self.verifier_sans_effet([INSTALL / 'arreter.sh', '--simulation'], 'arreter')
        self.verifier_sans_effet([INSTALL / 'sauvegarder.sh', '--destination', '/Volumes/Sauvegardes', '--simulation'],
                                 'sauvegarder')
        sortie = self.verifier_sans_effet([INSTALL / 'sauvegarder.sh', '--destination', '/Volumes/Sauvegardes', '--auto',
                                           '--simulation'], 'sauvegarder')
        self.assertIn('--depuis-fichier /source/secrets/sauvegarde.motdepasse', sortie)
        self.assertIn('python -m outils elaguer', sortie)
        sortie = self.verifier_sans_effet([INSTALL / 'restaurer.sh', '--archive', '/Volumes/S/territoria-20261030-123000.tsv1',
                                           '--forcer', '--simulation'], 'restaurer')
        self.assertIn('--archive /destination/territoria-20261030-123000.tsv1 --forcer', sortie)

    def test_commandes_affichees(self):
        sortie = self.verifier_sans_effet([INSTALL / 'installer.sh', '--etape', 'n8n', '--simulation'], 'n8n')
        # Séquence vérifiée de la tâche 8, n8n arrêté pendant l'import, sur le projet territoria uniquement.
        ordre = ['outils provision-n8n --sortie /donnees-secrets/provision', ' stop n8n', 'import:credentials',
                 'import:workflow --separate', 'publish:workflow --id=TerritoriaConv01',
                 'publish:workflow --id=TerritoriaArre01', 'rm -rf', ' up -d', 'outils proprietaire-n8n']
        positions = [sortie.index(morceau) for morceau in ordre]
        self.assertEqual(positions, sorted(positions))
        for ligne in sortie.splitlines():
            if ' compose ' in ligne:
                self.assertIn(' compose -p territoria --env-file ', ligne)
        sortie = self.verifier_sans_effet([INSTALL / 'installer.sh', '--etape', 'verifier', '--simulation'], 'verifier')
        self.assertRegex(sortie, r'exec -T territoria python -m outils verifier\n')  # jamais --strict en installation réelle


class CorrectifsRevueTests(unittest.TestCase):
    """Tour de corrections 1 : caddy/, dossier de LM Studio, Docker Engine sous Linux (R21), écoute réelle de LM Studio."""

    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)
        self.outils = self.bac.base / 'outils'  # commandes factices propres à un essai, sans celles de Bac
        self.outils.mkdir()

    def env_outils(self, **autres):
        return self.bac.env(PATH=f'{self.outils}:/usr/bin:/bin:/usr/sbin:/sbin', **autres)

    # 1. caddy/ (proxy du mode équipe) : créé en 0700 et donné à l'uid 10002 dans le même conteneur root.
    def test_caddy_cree_et_attribue_a_10002(self):
        sortie = lancer([INSTALL / 'installer.sh', '--etape', 'secrets', '--simulation'], self.bac.env()).stdout
        donnees = str(self.bac.donnees).replace(' ', '\\ ')
        self.assertIn(f'+ mkdir -p -m 700 {donnees}/caddy\n', sortie)
        conteneur = next(ligne for ligne in sortie.splitlines() if '--entrypoint sh' in ligne)
        self.assertIn(f'-v {donnees}/caddy:/caddy', conteneur)
        self.assertIn('chown\\ 10002:10002\\ /caddy\\ \\&\\&\\ chmod\\ 0700\\ /caddy', conteneur)
        self.assertIn('chown\\ 10001:10001\\ /territoria', conteneur)
        self.assertIn('chown\\ 1000:1000\\ /n8n', conteneur)

    # 2. Dossier de LM Studio : ~/.lmstudio-home-pointer, jamais deviné ; journaux introuvables = échec explicite.
    def test_dossier_de_lm_studio_par_le_pointeur(self):
        ailleurs = self.bac.base / 'LM Studio ailleurs'
        (ailleurs / 'bin').mkdir(parents=True)
        commande_factice(ailleurs / 'bin', 'lms', 'exit 0\n')
        (self.bac.home / '.lmstudio-home-pointer').write_text(str(ailleurs))  # sans fin de ligne, comme LM Studio
        (self.bac.home / '.lmstudio').mkdir()  # emplacement par défaut présent, mais le pointeur l'emporte
        resultat = bash_commun('printf "%s|%s" "$(dossier_lmstudio)" "$(chemin_lms)"', self.env_outils())
        self.assertEqual(resultat.stdout, f'{ailleurs}|{ailleurs}/bin/lms')

    def test_dossier_de_lm_studio_jamais_devine(self):
        cas = {'pointeur vers un dossier absent': lambda: (self.bac.home / '.lmstudio-home-pointer').write_text('/nulle/part\n'),
               'aucun emplacement': lambda: (self.bac.home / '.lmstudio-home-pointer').unlink()}
        for nom, preparer in cas.items():
            with self.subTest(cas=nom):
                preparer()
                resultat = bash_installer('debut_etape lmstudio\nexiger_dossier_lmstudio\nregler_journalisation_lms 127.0.0.1',
                                          self.env_outils())
                self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE lmstudio : ECHEC lmstudio_dossier_introuvable')
                self.assertEqual(sorted(p.name for p in self.bac.home.iterdir()),
                                 ['.lmstudio-home-pointer'] if 'pointeur' in nom else [])
        # Défaut documenté de l'installateur : ~/.cache/lm-studio, quand ~/.lmstudio n'existe pas.
        (self.bac.home / '.cache' / 'lm-studio').mkdir(parents=True)
        self.assertEqual(bash_commun('dossier_lmstudio', self.env_outils()).stdout, str(self.bac.home / '.cache' / 'lm-studio'))

    def test_reglages_ecrits_dans_le_dossier_pointe(self):
        ailleurs = self.bac.base / 'lms-maison'
        (ailleurs / '.internal').mkdir(parents=True)
        reglages = ailleurs / '.internal' / 'http-server-config.json'
        reglages.write_text(json.dumps({'port': 1234, 'verbose': True, 'logIncomingTokens': True, 'logSensitiveData': True,
                                        'fileLoggingMode': 'full', 'networkInterface': '0.0.0.0', 'cors': False}))
        (self.bac.home / '.lmstudio-home-pointer').write_text(str(ailleurs))
        resultat = bash_installer('debut_etape lmstudio\nexiger_dossier_lmstudio\nregler_journalisation_lms 127.0.0.1',
                                  self.env_outils())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE lmstudio : OK', resultat.stderr)
        self.assertEqual(json.loads(reglages.read_text()),
                         {'port': 1234, 'verbose': False, 'logIncomingTokens': False, 'logSensitiveData': False,
                          'fileLoggingMode': 'off', 'networkInterface': '127.0.0.1', 'cors': False})
        self.assertFalse((self.bac.home / '.lmstudio').exists())  # aucun fichier égaré dans ~/.lmstudio
        # Dossier confirmé sans réglages : fichier créé avec les valeurs par défaut de LM Studio et l'adresse choisie.
        reglages.unlink()
        resultat = bash_installer('debut_etape lmstudio\nexiger_dossier_lmstudio\nregler_journalisation_lms 172.17.0.1',
                                  self.env_outils())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE lmstudio : OK', resultat.stderr)
        cree = json.loads(reglages.read_text())
        self.assertEqual((cree['networkInterface'], cree['fileLoggingMode'], cree['verbose'], cree['logSensitiveData']),
                         ('172.17.0.1', 'off', False, False))

    def journaux(self, curl):
        ailleurs = self.bac.base / 'lms-maison'
        ailleurs.mkdir(exist_ok=True)
        (self.bac.home / '.lmstudio-home-pointer').write_text(str(ailleurs))
        commande_factice(self.outils, 'curl', curl)
        commande_factice(self.outils, 'sleep', 'exit 0\n')
        return ailleurs, bash_installer('debut_etape lmstudio\nexiger_dossier_lmstudio\n'
                                        'verifier_journaux_lms http://127.0.0.1:1234/v1 google/gemma-4-12b',
                                        self.env_outils(LMS_MAISON=str(ailleurs)))

    def test_journaux_introuvables_apres_la_requete(self):
        _, resultat = self.journaux('exit 0\n')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE lmstudio : ECHEC journaux_lmstudio_introuvables')

    def test_requete_marquee_journalisee_ou_non(self):
        # Faux LM Studio qui journalise le corps de la requête dans son dossier server-logs.
        ailleurs, resultat = self.journaux('mkdir -p "$LMS_MAISON/server-logs/2026-10"\n'
                                           'for a in "$@"; do case "$a" in *marqueur-territoria-*) '
                                           'printf "%s\\n" "$a" >>"$LMS_MAISON/server-logs/2026-10/2026-10-31.1.log";; esac; done\n')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE lmstudio : ECHEC journalisation_active')
        self.assertIn('contenant la requête de test : 1', resultat.stderr)
        self.assertNotIn('Répondez seulement', resultat.stdout + resultat.stderr)  # contenu des journaux jamais affiché
        shutil.rmtree(ailleurs / 'server-logs')
        (ailleurs / 'server-logs').mkdir()
        _, resultat = self.journaux('exit 0\n')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE lmstudio : OK', resultat.stderr)
        self.assertIn('contenant la requête de test : 0', resultat.stderr)

    # 3. R21 : sous Linux sans moteur, Docker Engine des paquets de la distribution, jamais Podman ni script distant.
    def simuler_moteur_linux(self, gestionnaire):
        return bash_installer('activer_simulation\nsysteme() { printf linux; }\nmoteur_present() { printf aucun; }\n'
                              f'gestionnaire_paquets() {{ printf {gestionnaire}; }}\nid() {{ if [ "$1" = -u ]; then echo 1000; '
                              'elif [ "$1" = -un ]; then echo agent; else command id "$@"; fi; }\n'
                              'debut_etape moteur\netape_moteur', self.bac.env())

    def test_linux_sans_moteur_installe_docker_engine(self):
        sortie = self.simuler_moteur_linux('apt').stdout
        ordre = ['+ sudo apt-get update', '+ sudo apt-get install -y docker.io docker-compose-v2',
                 '+ sudo systemctl enable --now docker', '+ sudo usermod -aG docker agent', '+ docker compose version']
        positions = [sortie.index(morceau) for morceau in ordre]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('docker-compose-plugin', sortie)  # repli annoncé, selon apt-cache policy
        self.assertEqual(derniere_ligne(sortie), 'ETAPE moteur : OK')
        sortie = self.simuler_moteur_linux('dnf').stdout
        self.assertIn('+ sudo dnf install -y moby-engine docker-compose', sortie)
        self.assertIn('+ sudo systemctl enable --now docker', sortie)
        sortie = self.simuler_moteur_linux('aucun').stdout
        self.assertIn('moteur_paquet_indisponible', sortie)
        self.assertIn('https://docs.docker.com/engine/install/', sortie)
        for gestionnaire in ('apt', 'dnf', 'aucun'):
            sortie = self.simuler_moteur_linux(gestionnaire).stdout
            self.assertNotIn('podman', sortie)
            self.assertNotRegex(sortie, r'curl[^\n]*\| *(ba)?sh|get\.docker\.com')
        self.assertEqual(self.bac.appels(), '')

    def test_paquet_compose_selon_apt_cache_policy(self):
        politique = ('case "$2" in docker.io) c="${DOCKER_IO:-24.0.7}";; docker-compose-v2) c="${V2:-(none)}";; '
                     'docker-compose-plugin) c="${PLUGIN:-(none)}";; *) c="(none)";; esac\n'
                     'printf "%s:\\n  Installed: (none)\\n  Candidate: %s\\n" "$2" "$c"\n')
        commande_factice(self.outils, 'apt-cache', politique)
        for variables, attendu in (({'V2': '2.24.6'}, 'docker-compose-v2'), ({'PLUGIN': '2.29.1'}, 'docker-compose-plugin'),
                                   ({'V2': '2.24.6', 'PLUGIN': '2.29.1'}, 'docker-compose-v2'), ({}, '')):
            with self.subTest(variables=variables):
                resultat = bash_installer('paquet_compose_apt', self.env_outils(**variables))
                self.assertEqual(resultat.stdout, attendu)
        # Aucun module Compose : arrêt explicite avant toute installation, renvoi à la documentation officielle.
        journal = self.bac.base / 'apt.log'
        for nom in ('sudo', 'systemctl', 'apt-get'):
            commande_factice(self.outils, nom, f'printf "%s\\n" "{nom} $*" >>"{journal}"\nexit 0\n')
        resultat = bash_installer('systeme() { printf linux; }\ngestionnaire_paquets() { printf apt; }\n'
                                  'debut_etape moteur\ninstaller_docker_linux', self.env_outils())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE moteur : ECHEC moteur_paquet_indisponible')
        self.assertIn('https://docs.docker.com/engine/install/', resultat.stderr)
        self.assertNotIn('install -y', journal.read_text() if journal.exists() else '')

    # 4. Écoute réelle de LM Studio (lsof sur macOS, ss sous Linux) : jamais toutes les interfaces ni le réseau local.
    def ecoute(self, systeme, adresses, attendue='127.0.0.1'):
        if systeme == 'macos':
            commande_factice(self.outils, 'lsof', 'set -f\nfor a in $FAUSSES_ADRESSES; do printf "p4242\\nf80\\nn%s:1234\\n" "$a"; done\n')
        else:
            commande_factice(self.outils, 'ss', 'set -f\nprintf "State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n"\n'
                             'printf "LISTEN 0 4096 127.0.0.53%%lo:53 0.0.0.0:*\\n"\n'
                             'for a in $FAUSSES_ADRESSES; do printf "LISTEN 0 511 %s:1234 0.0.0.0:*\\n" "$a"; done\n')
        return bash_commun(f'systeme() {{ printf {systeme}; }}\ndebut_etape demarrer\nverifier_ecoute_lms {attendue}',
                           self.env_outils(FAUSSES_ADRESSES=' '.join(adresses)))

    def test_ecoute_limitee_acceptee(self):
        for systeme, adresses, attendue in (('macos', ['127.0.0.1'], '127.0.0.1'), ('macos', ['127.0.0.1', '[::1]'], '127.0.0.1'),
                                            ('linux', ['172.17.0.1'], '172.17.0.1'), ('linux', ['127.0.0.1'], '127.0.0.1')):
            with self.subTest(systeme=systeme, adresses=adresses):
                resultat = self.ecoute(systeme, adresses, attendue)
                self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : OK', resultat.stderr)

    def test_ecoute_sur_le_reseau_refusee(self):
        for systeme, adresses in (('macos', ['*']), ('macos', ['127.0.0.1', '192.168.0.10']), ('linux', ['0.0.0.0']),
                                  ('linux', ['[::]']), ('linux', ['*']), ('linux', ['192.168.0.10'])):
            with self.subTest(systeme=systeme, adresses=adresses):
                resultat = self.ecoute(systeme, adresses, '172.17.0.1' if systeme == 'linux' else '127.0.0.1')
                self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : ECHEC lmstudio_expose_reseau')
                self.assertIn(f'{adresses[-1]}:1234', resultat.stderr)  # « * » cité tel quel, jamais développé
        resultat = self.ecoute('macos', [])
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : ECHEC lmstudio_injoignable')

    def test_ecoute_controlee_par_lmstudio_et_demarrer(self):
        sortie = lancer([INSTALL / 'installer.sh', '--etape', 'lmstudio', '--simulation'], self.bac.env()).stdout
        self.assertLess(sortie.index('server start --port 1234 --bind'), sortie.index('+ lsof -nP -iTCP:1234 -sTCP:LISTEN')
                        if SYSTEME == 'macos' else sortie.index('+ ss -ltn'))
        self.assertRegex(sortie, r'networkInterface=(127\.0\.0\.1|<passerelle de docker0>)')
        sortie = lancer([INSTALL / 'demarrer.sh', '--simulation'], self.bac.env()).stdout
        self.assertIn('+ lsof -nP -iTCP:1234 -sTCP:LISTEN' if SYSTEME == 'macos' else '+ ss -ltn', sortie)


class ChargementDuModeleTests(unittest.TestCase):
    """Après le démarrage du serveur de LM Studio (demarrer, mise à jour, étape demarrer), le modèle de compose.env est
    chargé s'il ne l'est pas : sinon la première demande le chargerait à la volée et `verifier` dépasserait ses délais."""

    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)
        self.outils = self.bac.base / 'outils'
        self.outils.mkdir()
        self.journal = self.bac.base / 'lms.log'
        # ps --json rend $MODELES_CHARGES ; load rend $CODE_CHARGEMENT (0 par défaut).
        commande_factice(self.outils, 'lms', 'printf "%s\\n" "lms $*" >>"$JOURNAL_LMS"\n'
                         'if [ "$1" = ps ]; then printf "%s\\n" "$MODELES_CHARGES"; fi\n'
                         'if [ "$1" = load ]; then exit "${CODE_CHARGEMENT:-0}"; fi\nexit 0\n')

    def lancer_commun(self, code, compose_env=None, **env):
        if compose_env is not None:
            (self.bac.donnees / 'secrets').mkdir(parents=True)
            (self.bac.donnees / 'secrets' / 'compose.env').write_text(compose_env)
        return bash_commun(f'debut_etape demarrer\n{code}', self.bac.env(
            PATH=f'{self.outils}:/usr/bin:/bin:/usr/sbin:/sbin', JOURNAL_LMS=str(self.journal), **env))

    def appels(self):
        return self.journal.read_text() if self.journal.exists() else ''

    def test_identifiant_du_modele_retrouve_dans_modeles_json(self):
        resultat = bash_commun('printf "%s|%s|%s" "$(modele_de_la_cle google/gemma-4-12b)" '
                               '"$(modele_de_la_cle google/gemma-4-26b-a4b)" "$(modele_de_la_cle autre-modele)"', self.bac.env())
        self.assertEqual(resultat.stdout, 'google/gemma-4-12b@q4_k_m|google/gemma-4-26b-a4b@q4_k_m|autre-modele')

    def test_modele_charge_s_il_ne_l_est_pas(self):
        resultat = self.lancer_commun(f'assurer_modele_charge "{self.outils}/lms"',
                                      "TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1\nTERRITORIA_MODELE=google/gemma-4-12b\n",
                                      MODELES_CHARGES='[]')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : OK', resultat.stderr)
        self.assertEqual(self.appels(), 'lms ps --json\nlms load google/gemma-4-12b@q4_k_m --identifier google/gemma-4-12b --yes\n')

    def test_modele_deja_charge_non_recharge_meme_en_json_indente(self):
        charges = '[\n  {\n    "identifier": "google/gemma-4-12b",\n    "path": "google/gemma-4-12b"\n  }\n]'
        resultat = self.lancer_commun(f'assurer_modele_charge "{self.outils}/lms"', 'TERRITORIA_MODELE=google/gemma-4-12b\n',
                                      MODELES_CHARGES=charges)
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : OK', resultat.stderr)
        self.assertEqual(self.appels(), 'lms ps --json\n')
        # Un autre modèle chargé, dont la clé commence pareil, ne compte pas.
        self.journal.unlink()
        resultat = self.lancer_commun(f'assurer_modele_charge "{self.outils}/lms"',
                                      MODELES_CHARGES='[{"identifier":"google/gemma-4-12b-autre"}]')
        self.assertIn('lms load google/gemma-4-12b@q4_k_m', self.appels())

    def test_sans_modele_dans_compose_env_rien_n_est_charge(self):
        resultat = self.lancer_commun(f'assurer_modele_charge "{self.outils}/lms"', 'TERRITORIA_HORS_LIGNE=1\n')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : OK', resultat.stderr)
        self.assertEqual(self.appels(), '')

    def test_mistral_choisi_lm_studio_n_est_pas_demarre(self):
        # Relecture finale (I2) : un poste passé à Mistral ne relance ni LM Studio ni son modèle, et n'échoue pas sur eux.
        resultat = self.lancer_commun('demarrer_lmstudio', 'TERRITORIA_MODELE=google/gemma-4-12b\nTERRITORIA_IA_EN_LIGNE=mistral\n',
                                      MODELES_CHARGES='[]', CODE_CHARGEMENT='1')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : OK', resultat.stderr)
        self.assertEqual(self.appels(), '')
        self.assertIn('API de Mistral choisie', resultat.stderr)

    def test_chargement_impossible(self):
        resultat = self.lancer_commun(f'assurer_modele_charge "{self.outils}/lms"', 'TERRITORIA_MODELE=modele-absent\n',
                                      MODELES_CHARGES='[]', CODE_CHARGEMENT='1')
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demarrer : ECHEC chargement_impossible')
        self.assertIn('lms load modele-absent --identifier modele-absent --yes', self.appels())

    def test_demarrer_charge_le_modele_apres_le_serveur_et_avant_les_services(self):
        (self.bac.donnees / 'secrets').mkdir(parents=True)
        (self.bac.donnees / 'secrets' / 'compose.env').write_text('TERRITORIA_MODELE=google/gemma-4-e4b\n')
        sortie = lancer([INSTALL / 'demarrer.sh', '--simulation'], self.bac.env()).stdout
        self.assertEqual(derniere_ligne(sortie), 'ETAPE demarrer : OK')
        ordre = ['server start --port 1234', 'lms load google/gemma-4-e4b@q4_k_m --identifier google/gemma-4-e4b --yes',
                 ' up -d']
        positions = [sortie.index(morceau) for morceau in ordre]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.bac.appels(), '')  # simulation : rien n'est lancé


class ArgumentsTests(unittest.TestCase):
    def test_refus(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        cas = [([INSTALL / 'installer.sh'], 'ETAPE installer : ECHEC arguments_invalides'),
               ([INSTALL / 'installer.sh', '--etape'], 'ETAPE installer : ECHEC arguments_invalides'),
               ([INSTALL / 'installer.sh', '--etape', 'inconnue'], 'ETAPE installer : ECHEC etape_inconnue'),
               ([INSTALL / 'installer.sh', '--etape', 'moteur', '--auto'], 'ETAPE moteur : ECHEC arguments_invalides'),
               ([INSTALL / 'installer.sh', '--etape', 'lmstudio', '--palier', 'enorme'], 'ETAPE lmstudio : ECHEC palier_inconnu'),
               ([INSTALL / 'installer.sh', '--etape', 'moteur', '--motdepasse=x'], 'ETAPE installer : ECHEC arguments_invalides'),
               ([INSTALL / 'sauvegarder.sh', '--auto'], 'ETAPE sauvegarder : ECHEC arguments_invalides'),
               ([INSTALL / 'restaurer.sh', '--forcer'], 'ETAPE restaurer : ECHEC arguments_invalides'),
               ([INSTALL / 'demarrer.sh', '--inconnu'], 'ETAPE demarrer : ECHEC arguments_invalides')]
        for args, attendu in cas:
            with self.subTest(args=[str(a).replace(str(INSTALL), 'install') for a in args]):
                resultat = lancer(args, bac.env())
                self.assertEqual(resultat.returncode, 1)
                self.assertEqual(resultat.stdout, attendu + '\n')
                self.assertNotIn('motdepasse', resultat.stdout + resultat.stderr)  # un argument reçu n'est jamais recopié
        self.assertEqual(bac.appels(), '')
        self.assertFalse(bac.donnees.exists())

    def test_etapes_interactives_sans_terminal(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        for etape in ('legifrance', 'sauvegarde-auto'):
            with self.subTest(etape=etape):
                resultat = lancer([INSTALL / 'installer.sh', '--etape', etape], bac.env())
                self.assertEqual(resultat.stdout.splitlines()[-1], f'ETAPE {etape} : ECHEC terminal_requis')
                self.assertEqual(resultat.returncode, 1)
        self.assertFalse(bac.donnees.exists())
        self.assertEqual(bac.contenu_home(), [])


class FonctionsCommunesTests(unittest.TestCase):
    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)
        self.fichier = self.bac.base / 'essai.env'

    def test_ecrire_variable_remplace_ajoute_et_garde_le_reste(self):
        self.fichier.write_text('# commentaire\nA=1\nCLE=ancienne\nB=2\nCLE=doublon\n')
        self.fichier.chmod(0o644)
        resultat = bash_commun(f'ecrire_variable "{self.fichier}" CLE "nouvelle valeur"\n'
                               f'ecrire_variable "{self.fichier}" AJOUT "x=y"\n'
                               f'ecrire_variable "{self.fichier}" SECRET \'a#b c$d\' litteral,secret', self.bac.env())
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertEqual(self.fichier.read_text(), "# commentaire\nA=1\nCLE=nouvelle valeur\nB=2\nAJOUT=x=y\nSECRET='a#b c$d'\n")
        self.assertEqual(stat.S_IMODE(self.fichier.stat().st_mode), 0o600)
        self.assertEqual(resultat.stdout + resultat.stderr, '')  # la valeur n'est jamais affichée
        self.assertEqual(sorted(p.name for p in self.bac.base.iterdir() if p.name.startswith('essai')), ['essai.env'])

    def test_retirer_variable_garde_le_reste_et_n_affiche_rien(self):
        self.fichier.write_text("# commentaire\nTERRITORIA_SERVICE_TOKEN=jeton-essai\nCLE='secret-essai'\nB=2\nCLE=doublon\nCLEF=garde\n")
        self.fichier.chmod(0o644)
        absent = self.bac.base / 'essai-absent.env'
        resultat = bash_commun(f'retirer_variable "{self.fichier}" CLE\nretirer_variable "{self.fichier}" ABSENTE\n'
                               f'retirer_variable "{absent}" CLE', self.bac.env())
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertEqual(self.fichier.read_text(), '# commentaire\nTERRITORIA_SERVICE_TOKEN=jeton-essai\nB=2\nCLEF=garde\n')
        self.assertEqual(stat.S_IMODE(self.fichier.stat().st_mode), 0o600)
        self.assertEqual(resultat.stdout + resultat.stderr, '')  # la valeur n'est jamais affichée
        self.assertEqual(sorted(p.name for p in self.bac.base.iterdir() if p.name.startswith('essai')), ['essai.env'])

    def test_ecrire_variable_refuse_fin_de_ligne_et_apostrophe(self):
        for valeur, options in (('a\\nb', ''), ('a\\rb', ''), ("l'apostrophe", 'litteral')):
            with self.subTest(valeur=valeur):
                resultat = bash_commun(f'debut_etape essai\necrire_variable "{self.fichier}" CLE "$(printf "{valeur}")" {options}',
                                       self.bac.env())
                self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE essai : ECHEC valeur_invalide')
                self.assertFalse(self.fichier.exists())

    def test_exiger_etape_lit_la_derniere_ligne(self):
        cas = [('ETAPE secrets : OK', 'secrets', 0, ''),
               ('RAPPORT reprendre-pilote : espaces=2\nETAPE reprendre-pilote : OK', 'reprendre-pilote', 0, ''),
               ('bruit\nETAPE demo : ECHEC bibliotheque_etrangere', 'demo', 1, 'bibliotheque_etrangere'),
               ('ETAPE demo : OK\nbruit', 'demo', 1, 'sortie_inattendue'),
               ('ETAPE demo : ECHEC Code-Bizarre', 'demo', 1, 'erreur_inattendue')]
        for sortie, commande, code, echec in cas:
            with self.subTest(sortie=sortie):
                resultat = subprocess.run([BASH, '-c', f'. "{INSTALL}/commun.sh"\n'
                                           f'if exiger_etape {commande} "$1"; then echo "rc=0"; else echo "rc=1 $CODE_ECHEC"; fi',
                                           'bash', sortie], env=self.bac.env(), capture_output=True, text=True, timeout=30)
                self.assertEqual(resultat.stdout.strip(), f'rc={code} {echec}'.strip())

    def test_projet_et_fichiers_compose_remplacables(self):
        resultat = bash_commun('activer_simulation\nMOTEUR=docker\ncompose ps\n'
                               'PROJET=territoria-ci\nFICHIERS_COMPOSE+=(-f "$RACINE/conteneurs/compose.ci.yaml")\ncompose ps',
                               self.bac.env())
        lignes = resultat.stdout.splitlines()
        donnees = str(self.bac.donnees).replace(' ', '\\ ')
        self.assertEqual(lignes[0], f'+ docker compose -p territoria --env-file {donnees}/secrets/compose.env '
                                    f'-f {RACINE}/conteneurs/compose.yaml ps')
        self.assertEqual(lignes[1], f'+ docker compose -p territoria-ci --env-file {donnees}/secrets/compose.env '
                                    f'-f {RACINE}/conteneurs/compose.yaml -f {RACINE}/conteneurs/compose.ci.yaml ps')

    def test_adresse_et_url_de_lm_studio(self):
        resultat = bash_commun('activer_simulation\nfor MOTEUR in docker podman; do printf "%s %s\\n" "$(url_lms_conteneurs)" "$(adresse_ecoute_lms)"; done',
                               self.bac.env())
        lignes = resultat.stdout.splitlines()
        self.assertTrue(lignes[0].startswith('http://host.docker.internal:1234/v1 '))
        self.assertTrue(lignes[1].startswith('http://host.containers.internal:1234/v1 '))
        if SYSTEME == 'macos':
            self.assertEqual([ligne.split()[1] for ligne in lignes], ['127.0.0.1', '127.0.0.1'])
        else:  # Linux : passerelle de docker0 avec Docker, boucle locale avec Podman
            self.assertEqual(lignes[1].split()[1], '127.0.0.1')
            self.assertNotEqual(lignes[0].split()[1], '0.0.0.0')


@unittest.skipUnless(SYSTEME in ('macos', 'linux'), 'pseudo-terminal POSIX')
class SaisiesMasqueesTests(unittest.TestCase):
    """Étapes interactives dans un pseudo-terminal : les secrets ne s'affichent pas et finissent en 0600."""

    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)
        self.secrets = self.bac.donnees / 'secrets'

    def installer(self, etape):
        return Terminal([INSTALL / 'installer.sh', '--etape', etape], self.bac.env())

    def test_legifrance_autorise_les_connecteurs_sans_identifiants(self):
        # Les identifiants PISTE se saisissent dans Administration › Connecteurs, seul endroit d'où Légifrance est consulté :
        # l'étape ne les demande plus. Elle ne règle que le mode hors ligne, qui coupe tous les connecteurs.
        terminal = self.installer('legifrance')
        terminal.attendre('Autoriser les connecteurs publics (Légifrance, data.gouv.fr)')
        terminal.attendre('[o/N]')
        terminal.repondre('o')
        code, sortie = terminal.finir()
        self.assertEqual(code, 0, sortie)
        self.assertEqual(derniere_ligne(sortie), 'ETAPE legifrance : OK')
        self.assertNotIn('saisie masquée', sortie)
        self.assertIn('Administration › Connecteurs', sortie)
        self.assertEqual((self.secrets / 'compose.env').read_text(), 'TERRITORIA_HORS_LIGNE=0\n')
        self.assertFalse((self.secrets / 'territoria.env').exists())
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o700)
        # Relancée : la question revient (c'est ainsi qu'on change d'avis).
        terminal = self.installer('legifrance')
        terminal.attendre('[o/N]')
        terminal.repondre('n')
        code, sortie = terminal.finir()
        self.assertEqual(derniere_ligne(sortie), 'ETAPE legifrance : OK')
        self.assertEqual((self.secrets / 'compose.env').read_text(), 'TERRITORIA_HORS_LIGNE=1\n')
        self.assertEqual(self.bac.appels(), '')

    def test_legifrance_retire_les_identifiants_piste_d_une_ancienne_installation(self):
        # L'ancienne étape 4 rangeait PISTE_CLIENT_ID et PISTE_CLIENT_SECRET dans territoria.env, que plus rien ne lit
        # (legal_sources.refresh ne prend que ceux du connecteur). Ils en sont retirés sans être affichés ; le jeton reste.
        self.secrets.mkdir(parents=True, mode=0o700)
        env = self.secrets / 'territoria.env'
        env.write_text("TERRITORIA_SERVICE_TOKEN=jeton-essai-4d1e\nPISTE_CLIENT_ID='identifiant-essai-7f3a'\n"
                       "PISTE_CLIENT_SECRET='secret-essai-91bc'\n")
        env.chmod(0o600)
        (self.secrets / 'compose.env').write_text('TERRITORIA_HORS_LIGNE=0\n')
        terminal = self.installer('legifrance')
        terminal.attendre('[o/N]')
        terminal.repondre('o')
        code, sortie = terminal.finir()
        self.assertEqual(code, 0, sortie)
        self.assertEqual(derniere_ligne(sortie), 'ETAPE legifrance : OK')
        self.assertEqual(env.read_text(), 'TERRITORIA_SERVICE_TOKEN=jeton-essai-4d1e\n')
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        for valeur in ('identifiant-essai-7f3a', 'secret-essai-91bc', 'jeton-essai-4d1e'):
            self.assertNotIn(valeur, sortie)
        self.assertIn('Identifiants PISTE retirés', sortie)
        self.assertEqual((self.secrets / 'compose.env').read_text(), 'TERRITORIA_HORS_LIGNE=0\n')

    def test_legifrance_refuse_mode_hors_ligne(self):
        self.secrets.mkdir(parents=True)
        (self.secrets / 'compose.env').write_text('TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1\nTERRITORIA_HORS_LIGNE=0\n')
        terminal = self.installer('legifrance')
        terminal.attendre('[o/N]')
        terminal.repondre('n')
        code, sortie = terminal.finir()
        self.assertEqual(derniere_ligne(sortie), 'ETAPE legifrance : OK')
        self.assertEqual((self.secrets / 'compose.env').read_text(),
                         'TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1\nTERRITORIA_HORS_LIGNE=1\n')
        self.assertFalse((self.secrets / 'territoria.env').exists())

    def preparer_sauvegarde(self):
        self.secrets.mkdir(parents=True, mode=0o700)
        destination = self.bac.base / 'disque externe'
        destination.mkdir()
        return destination

    def test_sauvegarde_auto(self):
        destination = self.preparer_sauvegarde()
        terminal = self.installer('sauvegarde-auto')
        terminal.attendre('Dossier de destination')
        terminal.repondre(str(destination))
        terminal.attendre('Mot de passe des sauvegardes')
        terminal.repondre('mot-de-passe-essai-5c2e', masque=True)
        terminal.attendre('Confirmez')
        terminal.repondre('mot-de-passe-essai-5c2e', masque=True)
        code, sortie = terminal.finir()
        self.assertEqual(code, 0, sortie)
        self.assertEqual(derniere_ligne(sortie), 'ETAPE sauvegarde-auto : OK')
        self.assertNotIn('mot-de-passe-essai-5c2e', sortie)
        fichier = self.secrets / 'sauvegarde.motdepasse'
        self.assertEqual(fichier.read_text(), 'mot-de-passe-essai-5c2e\n')
        self.assertEqual(stat.S_IMODE(fichier.stat().st_mode), 0o600)
        appels = self.bac.appels()
        self.assertNotIn('mot-de-passe-essai-5c2e', appels)
        sauvegarder = str(INSTALL / 'sauvegarder.sh')
        if SYSTEME == 'macos':
            plist = self.bac.home / 'Library' / 'LaunchAgents' / 'fr.territoria.sauvegarde.plist'
            contenu = plistlib.loads(plist.read_bytes())
            self.assertEqual(contenu['StartCalendarInterval'], {'Hour': 12, 'Minute': 30})
            self.assertEqual(contenu['ProgramArguments'], ['/bin/bash', sauvegarder, '--auto', '--destination', str(destination)])
            self.assertEqual(contenu['EnvironmentVariables']['TERRITORIA_DONNEES_HOTE'], str(self.bac.donnees))
            self.assertIn(f'launchctl bootstrap gui/{os.getuid()} {plist}', appels)
        else:
            self.assertRegex(appels, re.compile(rf"^30 12 \* \* \* PATH='[^']*' TERRITORIA_DONNEES_HOTE='{re.escape(str(self.bac.donnees))}' "
                                                rf"'{re.escape(sauvegarder)}' --auto --destination '{re.escape(str(destination))}' "
                                                r">>'[^']*sauvegarde-auto\.log' 2>&1 # territoria-sauvegarde-auto$", re.M))

    def test_sauvegarde_auto_refuse_saisies_differentes_ou_courtes(self):
        destination = self.preparer_sauvegarde()
        for premier, second, code in (('mot-de-passe-essai-5c2e', 'autre-mot-de-passe-1', 'saisies_differentes'),
                                      ('court', 'court', 'mot_de_passe_trop_court')):
            with self.subTest(code=code):
                terminal = self.installer('sauvegarde-auto')
                terminal.attendre('Dossier de destination')
                terminal.repondre(str(destination))
                terminal.attendre('Mot de passe des sauvegardes')
                terminal.repondre(premier, masque=True)
                terminal.attendre('Confirmez')
                terminal.repondre(second, masque=True)
                _, sortie = terminal.finir()
                self.assertEqual(derniere_ligne(sortie), f'ETAPE sauvegarde-auto : ECHEC {code}')
                self.assertFalse((self.secrets / 'sauvegarde.motdepasse').exists())
        self.assertEqual(self.bac.appels(), '')

    def test_destination_inexistante_refusee(self):
        self.preparer_sauvegarde()
        terminal = self.installer('sauvegarde-auto')
        terminal.attendre('Dossier de destination')
        terminal.repondre(str(self.bac.base / 'disque debranche'))
        _, sortie = terminal.finir()
        self.assertEqual(derniere_ligne(sortie), 'ETAPE sauvegarde-auto : ECHEC destination_absente')
        self.assertFalse((self.bac.base / 'disque debranche').exists())


class DemonstrationTests(unittest.TestCase):
    """install/demonstration.sh : réglages du serveur de démonstration (VPS Linux seulement)."""
    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)
        self.secrets = self.bac.donnees / 'secrets'
        self.systeme('Linux')

    def systeme(self, nom):
        commande_factice(self.bac.factices, 'uname', f'echo {nom}\n')

    def secrets_prets(self, compose_env='TERRITORIA_HORS_LIGNE=0\n'):
        self.secrets.mkdir(parents=True)
        (self.secrets / 'n8n.env').write_text('N8N_ENCRYPTION_KEY=essai\n')
        (self.secrets / 'territoria.env').write_text('TERRITORIA_SERVICE_TOKEN=essai\n')
        (self.secrets / 'compose.env').write_text(compose_env)

    def test_demonstration_domaine_ecrit_les_variables(self):
        self.secrets_prets()
        resultat = lancer([INSTALL / 'demonstration.sh', '--domaine', 'demo.exemple.fr'], self.bac.env())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demonstration : OK', resultat.stderr)
        self.assertEqual((self.secrets / 'compose.env').read_text(),
                         'TERRITORIA_HORS_LIGNE=0\nTERRITORIA_DEMONSTRATION=1\nTERRITORIA_DOMAINE=demo.exemple.fr\n')

    def test_demonstration_domaine_invalide(self):
        self.secrets_prets()
        for domaine in ('exemple', 'Demo.Exemple.fr', 'demo..fr', '-demo.fr', 'demo.fr/x'):
            with self.subTest(domaine=domaine):
                resultat = lancer([INSTALL / 'demonstration.sh', '--domaine', domaine], self.bac.env())
                self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demonstration : ECHEC domaine_invalide')
                self.assertEqual((self.secrets / 'compose.env').read_text(), 'TERRITORIA_HORS_LIGNE=0\n')

    def test_demonstration_refusee_hors_linux(self):
        self.systeme('Darwin')
        self.secrets_prets()
        resultat = lancer([INSTALL / 'demonstration.sh', '--domaine', 'demo.exemple.fr'], self.bac.env())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demonstration : ECHEC systeme_non_pris_en_charge')

    def test_demonstration_simulation_sans_effet(self):
        resultat = lancer([INSTALL / 'demonstration.sh', '--domaine', 'demo.exemple.fr', '--simulation'], self.bac.env())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demonstration : OK', resultat.stderr)
        self.assertFalse(self.bac.donnees.exists())
        self.assertEqual(self.bac.appels(), '')

    def test_premier_administrateur_exige_un_terminal(self):
        self.secrets_prets()
        resultat = lancer([INSTALL / 'demonstration.sh', '--premier-administrateur', 'admin', '--nom', 'Alice Admin'], self.bac.env())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demonstration : ECHEC terminal_requis')

    def test_arguments_incomplets_refuses(self):
        for args in ([], ['--domaine'], ['--premier-administrateur', 'admin'], ['--domaine', 'a.fr', '--premier-administrateur', 'admin', '--nom', 'A']):
            with self.subTest(args=args):
                resultat = lancer([INSTALL / 'demonstration.sh', *args], self.bac.env())
                self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE demonstration : ECHEC arguments_invalides')

    def test_compose_ajoute_le_fichier_de_demonstration(self):
        code = 'activer_simulation; MOTEUR=docker; compose up -d'
        self.secrets_prets('TERRITORIA_DEMONSTRATION=1\nTERRITORIA_DOMAINE=demo.exemple.fr\n')
        self.assertIn('compose.demonstration.yaml', bash_commun(code, self.bac.env()).stdout)
        (self.secrets / 'compose.env').write_text('TERRITORIA_HORS_LIGNE=0\n')
        sortie = bash_commun(code, self.bac.env()).stdout
        self.assertIn('compose.yaml', sortie)
        self.assertNotIn('compose.demonstration.yaml', sortie)


class MistralEtapeTests(unittest.TestCase):
    """Étape mistral : l'API de Mistral au lieu de l'IA locale, choisie dans le terminal, après avertissement."""
    QUESTION = "Utiliser l'API de Mistral au lieu de l'IA locale ? [o/N]"

    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)
        self.secrets = self.bac.donnees / 'secrets'

    def installer(self):
        return Terminal([INSTALL / 'installer.sh', '--etape', 'mistral'], self.bac.env())

    def test_mistral_refus_par_defaut(self):
        terminal = self.installer()
        terminal.attendre(self.QUESTION)
        terminal.repondre('')
        code, sortie = terminal.finir()
        self.assertEqual((code, derniere_ligne(sortie)), (0, 'ETAPE mistral : OK'))
        self.assertIn('registre', sortie)  # l'avertissement est affiché avant la question
        self.assertIn('--etape lmstudio', sortie)
        self.assertFalse(self.secrets.exists())

    def test_mistral_accepte(self):
        terminal = self.installer()
        terminal.attendre(self.QUESTION)
        terminal.repondre('o')
        terminal.attendre('Clé API de Mistral')
        terminal.repondre('k-essai-mistral', masque=True)
        code, sortie = terminal.finir()
        self.assertEqual((code, derniere_ligne(sortie)), (0, 'ETAPE mistral : OK'), sortie)
        self.assertNotIn('k-essai-mistral', sortie)
        env = self.secrets / 'territoria.env'
        self.assertEqual(env.read_text(), "TERRITORIA_MISTRAL_CLE='k-essai-mistral'\n")
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertEqual((self.secrets / 'compose.env').read_text(), 'TERRITORIA_IA_EN_LIGNE=mistral\n')
        # Relancée : rien n'est redemandé ni remplacé.
        code, sortie = self.installer().finir()
        self.assertEqual(derniere_ligne(sortie), 'ETAPE mistral : OK')
        self.assertNotIn('[o/N]', sortie)
        self.assertEqual(env.read_text(), "TERRITORIA_MISTRAL_CLE='k-essai-mistral'\n")

    def test_mistral_cle_vide(self):
        terminal = self.installer()
        terminal.attendre(self.QUESTION)
        terminal.repondre('o')
        terminal.attendre('Clé API de Mistral')
        terminal.repondre('', masque=True)
        code, sortie = terminal.finir()
        self.assertEqual(derniere_ligne(sortie), 'ETAPE mistral : ECHEC saisie_vide')
        self.assertFalse((self.secrets / 'territoria.env').exists())

    def test_mistral_sans_terminal(self):
        resultat = lancer([INSTALL / 'installer.sh', '--etape', 'mistral'], self.bac.env())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE mistral : ECHEC terminal_requis')

    def test_lmstudio_retire_mistral(self):
        self.secrets.mkdir(parents=True)
        (self.secrets / 'compose.env').write_text('TERRITORIA_HORS_LIGNE=1\nTERRITORIA_IA_EN_LIGNE=mistral\n')
        (self.secrets / 'territoria.env').write_text("PISTE_CLIENT_ID='x'\nTERRITORIA_MISTRAL_CLE='k-essai-mistral'\n")
        resultat = bash_installer('retirer_mistral', self.bac.env())
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertEqual((self.secrets / 'compose.env').read_text(), 'TERRITORIA_HORS_LIGNE=1\nTERRITORIA_IA_EN_LIGNE=\n')
        self.assertEqual((self.secrets / 'territoria.env').read_text(), "PISTE_CLIENT_ID='x'\nTERRITORIA_MISTRAL_CLE=''\n")

    def test_diagnostic_indique_l_ia_en_ligne(self):
        self.secrets.mkdir(parents=True)
        (self.secrets / 'compose.env').write_text('TERRITORIA_IA_EN_LIGNE=mistral\n')
        resultat = lancer([INSTALL / 'diagnostic.sh'], self.bac.env())
        self.assertIn('ia_en_ligne=mistral', resultat.stdout.splitlines())


class RaccourciTests(unittest.TestCase):
    """Étape raccourci réelle, dans un HOME temporaire (jamais le vrai bureau)."""

    def test_raccourci_et_demarrage_a_l_ouverture_de_session(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        resultat = lancer([INSTALL / 'installer.sh', '--etape', 'raccourci', '--auto'], bac.env())
        self.assertEqual(derniere_ligne(resultat.stdout), 'ETAPE raccourci : OK', resultat.stderr)
        demarrer = str(INSTALL / 'demarrer.sh')
        if SYSTEME == 'macos':
            commande = bac.home / 'Desktop' / 'Territoria.command'
            self.assertEqual(stat.S_IMODE(commande.stat().st_mode), 0o755)
            self.assertIn(f'exec {demarrer} --ouvrir\n', commande.read_text())
            agent = plistlib.loads((bac.home / 'Library' / 'LaunchAgents' / 'fr.territoria.demarrer.plist').read_bytes())
            self.assertEqual(agent['ProgramArguments'], ['/bin/bash', demarrer])
            self.assertTrue(agent['RunAtLoad'])
            self.assertEqual(bac.appels(), '')  # chargé à la prochaine session : aucun launchctl
        elif SYSTEME == 'linux':
            bureau = bac.home / 'Desktop' / 'territoria.desktop'
            self.assertIn(f'Exec="{demarrer}" --ouvrir\n', bureau.read_text())
            self.assertIn(f'Exec="{demarrer}"\n', (bac.home / '.config' / 'autostart' / 'territoria.desktop').read_text())
        self.assertFalse(bac.donnees.exists())


class HygieneTests(unittest.TestCase):
    """Secrets : saisie masquée, jamais en argument d'un programme, jamais affichés."""

    @classmethod
    def setUpClass(cls):
        cls.textes = {nom: (INSTALL / f'{nom}.sh').read_text(encoding='utf-8') for nom in NOMS}

    def test_saisie_masquee_depuis_le_terminal(self):
        commun = self.textes['commun']
        self.assertIn('IFS= read -r -s __saisie_masquee </dev/tty', commun)
        installer = self.textes['installer']
        self.assertRegex(installer, r'lire_secret [a-z]+ "Mot de passe des sauvegardes \(saisie masquée')
        self.assertNotIn('PISTE_CLIENT_ID (saisie masquée)', installer)  # étape 4 : identifiants dans l'administration
        self.assertNotIn('set -x', ''.join(self.textes.values()))

    def test_secrets_jamais_en_argument(self):
        # Chaque ligne qui manipule une valeur secrète n'utilise que des commandes internes de bash.
        autorise = re.compile(r'^\s*(?:local [^=]*|lire_secret \w+ "[^"]*"|ecrire_variable "[^"]*" \w+ "\$\w+" [\w,]+|'
                              r'ecrire_mot_de_passe "\$motdepasse"|(?:identifiant|secret|motdepasse|confirmation)=""|'
                              r'if \[ .*\]; then echouer .*|exiger_etape [\w-]+ "\$\(printf \'%s\\n\' "\$motdepasse" \| .*|'
                              r'if ! \(umask 077 && printf \'%s\\n\' "\$1" >"\$temporaire".*)$')
        for nom, texte in self.textes.items():
            for ligne in texte.splitlines():
                if re.search(r'\$\{?(secret|identifiant|motdepasse|confirmation)\b', ligne):
                    with self.subTest(script=nom, ligne=ligne.strip()):
                        self.assertRegex(ligne, autorise)

    def test_ecritures_en_0600(self):
        commun = self.textes['commun']
        self.assertIn('umask 077', commun)
        self.assertIn('chmod 600 "$temporaire"', commun)
        self.assertIn('umask 077 && printf', self.textes['installer'])


class DocumentationTests(unittest.TestCase):
    def test_acces_lmstudio(self):
        texte = (RACINE / 'docs' / 'acces-lmstudio.md').read_text(encoding='utf-8')
        for attendu in ('host.docker.internal:1234/v1', 'host.containers.internal:1234/v1', '--bind', 'docker0',
                        'à valider en recette', 'marqueur-territoria', 'fileLoggingMode', 'compose.env'):
            self.assertIn(attendu, texte)
        modeles = json.loads((INSTALL / 'modeles.json').read_text(encoding='utf-8'))
        for identifiant in modeles.values():
            self.assertIn(identifiant, texte)


if __name__ == '__main__':
    unittest.main()


class TranscriptionInstallTests(unittest.TestCase):
    """Image de transcription, modèles téléchargés et vérifiés avant le démarrage, dossiers audio et modèles."""

    def setUp(self):
        self.bac = Bac()
        self.addCleanup(self.bac.nettoyer)

    def test_demarrer_construit_la_transcription_et_telecharge_les_modeles_avant_les_services(self):
        (self.bac.donnees / 'secrets').mkdir(parents=True)
        (self.bac.donnees / 'secrets' / 'compose.env').write_text('TERRITORIA_MODELE=google/gemma-4-e4b\n')
        sortie = lancer([INSTALL / 'installer.sh', '--etape', 'demarrer', '--simulation'], self.bac.env()).stdout
        self.assertEqual(derniere_ligne(sortie), 'ETAPE demarrer : OK')
        ordre = [' build territoria', ' build travaux', ' --profile outils run --rm -T travaux-modeles', ' up -d']
        positions = [sortie.index(morceau) for morceau in ordre]
        self.assertEqual(positions, sorted(positions))

    def test_dossiers_audio_et_modeles_crees_et_confies_aux_services(self):
        sortie = lancer([INSTALL / 'installer.sh', '--etape', 'secrets', '--simulation'], self.bac.env()).stdout
        self.assertEqual(derniere_ligne(sortie), 'ETAPE secrets : OK')
        sortie = sortie.replace('\\ ', ' ').replace('\\&', '&')  # la simulation échappe les espaces et les &
        self.assertIn(f'mkdir -p -m 755 {self.bac.donnees}/modeles-audio', sortie)
        # territoria/audio/ est créé dans le conteneur root : sous Linux, la machine n'entre plus dans territoria/.
        self.assertNotIn(f'{self.bac.donnees}/territoria/audio', sortie)
        self.assertIn('mkdir -p /territoria/audio && chmod 0700 /territoria/audio && chown 10001:10001 /territoria '
                      '/territoria/audio /modeles-audio', sortie)

    def test_echec_du_telechargement_des_modeles_remonte_son_code(self):
        docker = self.bac.factices / 'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "docker $*" >>"$JOURNAL_FACTICES"\n'
                          'case "$*" in *travaux-modeles*) echo "ETAPE modeles-audio : ECHEC empreinte" ;; esac\nexit 0\n')
        resultat = bash_commun('PROJET=territoria; MOTEUR=docker; S="$TERRITORIA_DONNEES_HOTE/secrets"; modeles_audio || '
                               'printf "code=%s\\n" "$CODE_ECHEC"', self.bac.env())
        self.assertIn('code=empreinte', resultat.stdout)
        self.assertIn('build travaux', self.bac.appels())

    def test_modeles_absents_ne_bloquent_ni_l_installation_ni_la_mise_a_jour(self):
        # La transcription est une fonction parmi d'autres : sans les modèles (proxy, coupure), Territoria démarre quand
        # même, la transcription attend (« Modèles absents » dans Santé) et l'étape demarrer relancée réessaie.
        docker = self.bac.factices / 'docker'
        docker.write_text('#!/bin/sh\ncase "$*" in *travaux-modeles*) echo "ETAPE modeles-audio : ECHEC reseau" ;; esac\nexit 0\n')
        resultat = bash_commun('PROJET=territoria; MOTEUR=docker; S="$TERRITORIA_DONNEES_HOTE/secrets"; '
                               'modeles_audio_facultatif && printf "suite\\n"', self.bac.env())
        self.assertIn('suite', resultat.stdout)
        self.assertIn('reseau', resultat.stderr)
        self.assertIn('--etape demarrer', resultat.stderr)
