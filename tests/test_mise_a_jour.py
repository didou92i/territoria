# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Mise à jour avec retour automatique (install/mettre-a-jour.sh) : séquence, retour arrière, refus, mot de passe.

Chaque essai joue le vrai script dans une copie jetable du dépôt (install/ et conteneurs/ de l'arbre de travail, dans un
dépôt git temporaire cloné d'une « origine » locale), avec un territoria-donnees temporaire à côté. Docker, lms, lsof, ss
et curl sont des commandes factices en tête du PATH : le faux docker consigne chaque appel avec la version extraite du
dépôt d'essai et imite les réponses utiles (moteur en marche, image, santé, archive créée, restauration, déplacement vers
echec-*). Le vrai parcours, avec de vrais conteneurs et une version qui échoue vraiment, est dans tests/integration.sh.
"""
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # test_install.py est à côté
from test_install import BASH, INSTALL, RACINE, SYSTEME, Bac, Terminal, derniere_ligne, etat_du_depot  # noqa: E402

MESSAGE = 'Mise à jour annulée : version précédente rétablie.'
MOT_DE_PASSE = 'motdepasse-essai-tres-long-42'
IDENTITE = {'GIT_AUTHOR_NAME': 'Essai', 'GIT_AUTHOR_EMAIL': 'essai@exemple.invalid', 'GIT_COMMITTER_NAME': 'Essai',
            'GIT_COMMITTER_EMAIL': 'essai@exemple.invalid'}

FAUX_DOCKER = r'''#!/bin/sh
version="$(git -C "$DEPOT_ESSAI" rev-parse HEAD 2>/dev/null)"
printf '%s @%s\n' "docker $*" "$version" >>"$JOURNAL_FACTICES"
tout=" $* "
precedent=""
for mot in "$@"; do
    if [ "$precedent" = -v ]; then
        case "$mot" in
            *:/destination | *:/destination:ro) m_destination="${mot%:/destination*}" ;;
            *:/cible) m_cible="${mot%:/cible}" ;;
            *:/donnees-hote) m_hote="${mot%:/donnees-hote}" ;;
            *:/donnees-secrets) m_secrets="${mot%:/donnees-secrets}" ;;
            *:/provision:ro) m_provision="${mot%:/provision:ro}" ;;
        esac
    fi
    precedent="$mot"
    dernier="$mot"
done
case "$tout" in
    *" run "*" -i "*) { printf '== %s\n' "$*"; cat; printf '\n'; } >>"$ETAT_FACTICE/entrees" ;;
esac
case "$1" in
    info) exit 0 ;;
    network) echo 172.17.0.1; exit 0 ;;
    inspect) echo healthy; exit 0 ;;
    image) cat "$ETAT_FACTICE/image"; exit 0 ;;
esac
case "$tout" in
    *" images -q territoria "*) cat "$ETAT_FACTICE/image" ;;
    *" ps -q "*) echo conteneur ;;
    *" build territoria "*)
        if [ -n "$INTERROMPRE_CONSTRUCTION" ] && [ "$version" != "$(cat "$ETAT_FACTICE/a")" ]; then
            : >"$ETAT_FACTICE/en_construction"
            sleep 60
        fi
        n=$(($(cat "$ETAT_FACTICE/n") + 1))
        echo "$n" >"$ETAT_FACTICE/n"
        echo "sha256:image-$n" >"$ETAT_FACTICE/image"
        ;;
    *" run --rm -T travaux-modeles"*) echo "ETAPE modeles-audio : OK" ;;
    *" python -m outils verifier-sauvegarde "*) echo "ETAPE verifier-sauvegarde : OK" ;;
    *" python -m outils provision-n8n "*)
        (umask 077 && mkdir -p "$m_secrets/provision" && echo '[]' >"$m_secrets/provision/credentials.json" \
            && echo '{}' >"$m_secrets/provision/conversation.json")
        echo "ETAPE provision-n8n : OK"
        ;;
    *" import:workflow "*) ls "$m_provision" >"$ETAT_FACTICE/importes" ;;
    *" publish:workflow "*) if [ -n "$PUBLICATION_EN_ECHEC" ] && [ "$version" != "$(cat "$ETAT_FACTICE/a")" ]; then exit 1; fi ;;
    *" python -m outils sauvegarder "*)
        (umask 077 && : >"$m_destination/territoria-$(date +%Y%m%d-%H%M%S).tsv1")
        echo "ETAPE sauvegarder : OK"
        ;;
    *" python -m outils restaurer "*)
        if [ -n "$RESTAURATION_EN_ECHEC" ]; then
            if [ "$RESTAURATION_EN_ECHEC" = restauration_interrompue ]; then mkdir -p "$m_cible/.restauration-essai"; fi
            echo "ETAPE restaurer : ECHEC $RESTAURATION_EN_ECHEC"
        else
            mkdir -p "$m_cible/territoria" "$m_cible/n8n"
            echo "restauree" >"$m_cible/territoria/territoire.sqlite3"
            echo "ETAPE restaurer : OK"
        fi
        ;;
    *" python -m outils verifier "*)
        if [ "$VERIFICATION_TOUJOURS_EN_ECHEC" = 1 ] || [ "$version" = "$VERIFICATION_EN_ECHEC" ]; then
            echo "ETAPE verifier : ECHEC reponse_sourcee"
        else
            echo "ETAPE verifier : OK"
        fi
        ;;
    *" --entrypoint sh "*)
        for r in territoria n8n; do
            if [ -e "$m_hote/$r" ]; then mv "$m_hote/$r" "$m_hote/$dernier/$r"; fi
        done
        ;;
esac
exit 0
'''
FAUX = {
    'docker': FAUX_DOCKER,
    'lms': '#!/bin/sh\nprintf "%s\\n" "lms $*" >>"$JOURNAL_FACTICES"\n'
           'if [ "$1" = server ] && [ "$2" = status ]; then echo \'{"running":true}\'; fi\nexit 0\n',
    'lsof': '#!/bin/sh\nprintf "p1\\nn127.0.0.1:1234\\n"\n',
    'ss': '#!/bin/sh\nprintf "State Recv-Q Send-Q Local Peer\\nLISTEN 0 4096 127.0.0.1:1234 0.0.0.0:*\\n"\n',
    'curl': '#!/bin/sh\nexit 0\n',
}


def git(*args, cwd, env=None):
    resultat = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, env=env)
    if resultat.returncode != 0:
        raise AssertionError(f'git {" ".join(args)} : {resultat.stderr}')
    return resultat.stdout.strip()


class Essai:
    """Origine git locale (A sur la branche principale, B étiqueté v-b hors branche), installation clonée de l'origine
    (sur A, sans v-b avant `git fetch --tags`), territoria-donnees à côté et commandes factices."""

    def __init__(self, mot_de_passe_enregistre=True, sous_dossier=''):
        self.racine_tmp = Path(tempfile.mkdtemp(prefix='territoria-test-maj-')).resolve()
        self.base = self.racine_tmp / sous_dossier if sous_dossier else self.racine_tmp
        self.base.mkdir(parents=True, exist_ok=True)
        self.home = self.base / 'maison'
        self.home.mkdir()
        self.env_git = {**{k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'TERRITORIA_'))},
                        **IDENTITE, 'HOME': str(self.home), 'GIT_CONFIG_NOSYSTEM': '1'}
        origine = self.base / 'origine'
        origine.mkdir()
        git('-c', 'init.defaultBranch=principale', 'init', '-q', cwd=origine, env=self.env_git)
        for dossier in ('install', 'conteneurs'):
            shutil.copytree(RACINE / dossier, origine / dossier, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(RACINE / '.gitignore', origine / '.gitignore')
        git('add', '-A', cwd=origine, env=self.env_git)
        git('commit', '-q', '-m', 'A', cwd=origine, env=self.env_git)
        self.a = git('rev-parse', 'HEAD', cwd=origine, env=self.env_git)
        git('checkout', '-q', '--detach', cwd=origine, env=self.env_git)
        (origine / 'conteneurs' / 'VERSION').write_text('b\n')
        git('add', '-A', cwd=origine, env=self.env_git)
        git('commit', '-q', '-m', 'B', cwd=origine, env=self.env_git)
        self.b = git('rev-parse', 'HEAD', cwd=origine, env=self.env_git)
        git('tag', 'v-b', cwd=origine, env=self.env_git)
        git('checkout', '-q', 'principale', cwd=origine, env=self.env_git)
        self.depot = self.base / 'territoria'
        git('clone', '-q', str(origine), str(self.depot), cwd=self.base, env=self.env_git)
        self.script = self.depot / 'install' / 'mettre-a-jour.sh'
        self.donnees = self.base / 'territoria-donnees'
        for dossier in ('territoria', 'n8n', 'secrets'):
            (self.donnees / dossier).mkdir(parents=True)
        self.donnees.chmod(0o700)
        (self.donnees / 'territoria' / 'territoire.sqlite3').write_text('avant\n')
        (self.donnees / 'secrets' / 'compose.env').write_text('')
        if mot_de_passe_enregistre:
            (self.donnees / 'secrets' / 'sauvegarde.motdepasse').write_text(MOT_DE_PASSE + '\n')
        self.factices = self.base / 'factices'
        self.factices.mkdir()
        for nom, corps in FAUX.items():
            (self.factices / nom).write_text(corps)
            (self.factices / nom).chmod(0o755)
        self.etat = self.base / 'etat'
        self.etat.mkdir()
        (self.etat / 'n').write_text('1\n')
        (self.etat / 'image').write_text('sha256:image-1\n')
        (self.etat / 'a').write_text(self.a)
        self.journal = self.base / 'appels.log'

    def env(self, **autres):
        env = {**self.env_git, 'PATH': f'{self.factices}:/usr/bin:/bin:/usr/sbin:/sbin',
               'JOURNAL_FACTICES': str(self.journal), 'ETAT_FACTICE': str(self.etat), 'DEPOT_ESSAI': str(self.depot),
               'LC_ALL': 'C.UTF-8' if SYSTEME == 'linux' else 'fr_FR.UTF-8'}
        for cle in IDENTITE:  # le script ne doit rien enregistrer dans le dépôt : aucune identité git disponible
            env.pop(cle)
        env.update(autres)
        return env

    def lancer(self, *args, **env):
        return subprocess.run([BASH, str(self.script), *args], env=self.env(**env), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120, start_new_session=True)

    def head(self):
        return git('rev-parse', 'HEAD', cwd=self.depot, env=self.env_git)

    def branche(self):
        return subprocess.run(['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'], cwd=self.depot, capture_output=True,
                              text=True, env=self.env_git).stdout.strip()

    def appels(self):
        return self.journal.read_text() if self.journal.exists() else ''

    def evenements(self):
        """(étape, version) de chaque appel du faux docker qui compte dans la séquence."""
        motifs = (('verifier-sauvegarde', ' python -m outils verifier-sauvegarde '),
                  ('sauvegarder', ' python -m outils sauvegarder'), ('restaurer', ' python -m outils restaurer '),
                  ('verifier', ' python -m outils verifier @'), ('build', ' build territoria @'), ('up', ' up -d @'),
                  ('stop', ' stop @'), ('proprietaires', 'chown 10001:10001 /territoria '),
                  ('deplacement', ' --entrypoint sh '), ('etiquette', 'docker tag '),
                  ('retrait', ' image rm '), ('provision', ' python -m outils provision-n8n '),
                  ('arret_n8n', ' stop n8n @'), ('credentials', ' import:credentials '), ('workflows', ' import:workflow '),
                  ('publication', ' publish:workflow --id=TerritoriaArre01 '))
        resultat = []
        for ligne in self.appels().splitlines():
            if not ligne.startswith('docker '):
                continue
            version = ligne.rsplit(' @', 1)[1]
            for nom, motif in motifs:
                if motif in ligne + ' ':
                    resultat.append((nom, version))
                    break
        return resultat

    def appel(self, motif):
        lignes = [ligne for ligne in self.appels().splitlines() if motif in ligne]
        assert len(lignes) == 1, (motif, lignes)
        return lignes[0]

    def entrees(self):
        chemin = self.etat / 'entrees'
        return chemin.read_text() if chemin.exists() else ''

    def archives(self):
        dossier = self.donnees / 'mises-a-jour'
        return sorted(p.name for p in dossier.iterdir()) if dossier.exists() else []

    def echecs(self):
        return sorted(p for p in self.donnees.iterdir() if p.name.startswith('echec-'))

    def nettoyer(self):
        shutil.rmtree(self.racine_tmp, ignore_errors=True)


def mode(chemin):
    return stat.S_IMODE(chemin.stat().st_mode)


class MiseAJourTests(unittest.TestCase):
    def essai(self, **options):
        essai = Essai(**options)
        self.addCleanup(essai.nettoyer)
        return essai

    def test_reussie(self):
        e = self.essai()
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : OK\n')
        # v-b n'existait pas dans l'installation : seul `git fetch --tags` l'a fait connaître.
        self.assertEqual(e.head(), e.b)
        self.assertEqual(e.branche(), '')  # version extraite par son commit (HEAD détaché)
        a, b = e.a, e.b
        # Workflows de la nouvelle version importés dans n8n (arrêté) après la construction, avant le redémarrage et le
        # test de bout en bout (spécification, § 4.5, migration) ; jetons en clair de provision/ supprimés ensuite.
        self.assertEqual(e.evenements(), [('sauvegarder', a), ('verifier-sauvegarde', a), ('etiquette', a), ('proprietaires', b), ('build', b),
                                          ('provision', b), ('arret_n8n', b), ('credentials', b), ('workflows', b),
                                          ('publication', b), ('up', b), ('verifier', b), ('retrait', b)])
        self.assertEqual((e.etat / 'importes').read_text(), 'conversation.json\ncredentials.json\n')
        self.assertFalse((e.donnees / 'secrets' / 'provision').exists())
        archive, = e.archives()
        self.assertRegex(archive, r'^territoria-\d{8}-\d{6}\.tsv1$')
        self.assertEqual(mode(e.donnees / 'mises-a-jour'), 0o700)
        sauvegarde = e.appel('python -m outils sauvegarder')
        self.assertIn(f'-v {e.donnees}:/source:ro -v {e.donnees}/mises-a-jour:/destination ', sauvegarde)
        self.assertIn('--depuis-fichier /source/secrets/sauvegarde.motdepasse', sauvegarde)
        self.assertIn(f'--archive /destination/{archive}', e.appel('outils verifier-sauvegarde'))
        self.assertIn(MOT_DE_PASSE, e.entrees())  # vérification : mot de passe sur l'entrée standard
        self.assertNotIn(MOT_DE_PASSE, e.appels() + r.stdout + r.stderr)  # jamais en argument ni affiché
        self.assertNotIn('--strict', e.appels())
        self.assertIn(f'Sauvegarde d\'avant la mise à jour : {e.donnees}/mises-a-jour/{archive}', r.stderr)
        self.assertEqual(e.echecs(), [])

    def test_echec_puis_retour_automatique(self):
        e = self.essai()
        r = e.lancer('--version', 'v-b', VERIFICATION_EN_ECHEC=e.b)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC mise_a_jour_annulee\n')
        self.assertEqual(derniere_ligne(r.stderr), MESSAGE)
        self.assertEqual(e.head(), e.a)
        self.assertEqual(e.branche(), 'principale')  # la branche d'origine est remise, pas un HEAD détaché
        a, b = e.a, e.b
        # Le retour ne réimporte rien : la restauration remet la base n8n d'avant, avec ses anciens workflows.
        self.assertEqual(e.evenements(), [
            ('sauvegarder', a), ('verifier-sauvegarde', a), ('etiquette', a), ('proprietaires', b), ('build', b), ('provision', b),
            ('arret_n8n', b), ('credentials', b), ('workflows', b), ('publication', b), ('up', b), ('verifier', b),
            ('stop', b), ('deplacement', b), ('stop', b), ('restaurer', b), ('build', a), ('up', a), ('verifier', a),
            ('retrait', a)])
        self.assertFalse((e.donnees / 'secrets' / 'provision').exists())
        archive, = e.archives()
        restauration = e.appel('python -m outils restaurer')
        # Restaurée avec l'image de la version qui a fait la sauvegarde (image-1, gardée par une étiquette avant la
        # construction de la nouvelle : le magasin d'images de containerd la supprimerait sinon), pas avec celle qui échoue.
        self.assertIn(f'docker tag sha256:image-1 territoria-territoria:avant-mise-a-jour @{a}', e.appels())
        self.assertIn(f' territoria-territoria:avant-mise-a-jour python -m outils restaurer --archive /destination/{archive} '
                      '--forcer ', restauration + ' ')
        self.assertIn('docker image rm territoria-territoria:avant-mise-a-jour', e.appels())
        self.assertIn(f'-v {e.donnees}/mises-a-jour:/destination:ro -v {e.donnees}:/cible ', restauration)
        self.assertEqual(e.entrees().count(MOT_DE_PASSE), 2)  # vérification et restauration, par l'entrée standard
        # Données de la tentative ratée : déplacées, gardées, privées ; l'installation restaurée les remplace.
        echec, = e.echecs()
        self.assertRegex(echec.name, r'^echec-\d{8}-\d{6}$')
        self.assertEqual(mode(echec), 0o700)
        self.assertEqual(sorted(p.name for p in echec.iterdir()), ['n8n', 'territoria'])
        self.assertEqual((echec / 'territoria' / 'territoire.sqlite3').read_text(), 'avant\n')
        self.assertEqual((e.donnees / 'territoria' / 'territoire.sqlite3').read_text(), 'restauree\n')
        self.assertIn(str(echec), r.stderr)
        self.assertIn('reponse_sourcee', r.stderr)  # cause de l'échec, en code seulement
        deplacement = e.appel(':/donnees-hote ')  # le déplacement, pas l'attribution des dossiers (même --entrypoint sh)
        self.assertIn('--network none --user 0:0', deplacement)
        self.assertIn(f'-v {e.donnees}:/donnees-hote territoria-territoria:avant-mise-a-jour -c ', deplacement)

    def test_dossier_territoria_reserve_au_service(self):
        # Sous Linux, territoria/ appartient à l'uid 10001 en 0700 (l'application le restreint au démarrage) : la machine
        # n'y entre plus. Le dossier audio/, apparu avec la transcription, est créé par le conteneur jetable en root.
        e = self.essai()
        (e.donnees / 'territoria').chmod(0)
        try:
            r = e.lancer('--version', 'v-b')
        finally:
            (e.donnees / 'territoria').chmod(0o755)
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : OK\n', r.stderr)
        self.assertIn('-c mkdir -p /territoria/audio && chmod 0700 /territoria/audio && chown 10001:10001 /territoria '
                      '/territoria/audio /modeles-audio', e.appels())

    def test_provisionnement_rate_puis_retour_automatique(self):
        e = self.essai()
        r = e.lancer('--version', 'v-b', PUBLICATION_EN_ECHEC='1')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC mise_a_jour_annulee\n', r.stderr)
        self.assertIn('(n8n_provisionnement)', r.stderr)
        a, b = e.a, e.b
        evenements = e.evenements()
        # La première publication (TerritoriaConv01) échoue : rien n'est démarré, le retour suit aussitôt.
        self.assertEqual(evenements[evenements.index(('workflows', b)) + 1:], [
            ('stop', b), ('deplacement', b), ('stop', b), ('restaurer', b), ('build', a), ('up', a), ('verifier', a),
            ('retrait', a)])
        self.assertNotIn(('up', b), evenements)
        self.assertFalse((e.donnees / 'secrets' / 'provision').exists())  # jetons en clair supprimés malgré l'échec
        self.assertEqual(e.head(), a)

    def test_modele_de_lm_studio_charge_avant_la_sauvegarde(self):
        # Le modèle est chargé dès le démarrage de LM Studio : le test de bout en bout ne le charge pas à la volée.
        e = self.essai()
        (e.donnees / 'secrets' / 'compose.env').write_text('TERRITORIA_MODELE=google/gemma-4-12b\n')
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : OK\n', r.stderr)
        appels = e.appels()
        chargement = appels.index('lms load google/gemma-4-12b@q4_k_m --identifier google/gemma-4-12b --yes\n')
        self.assertLess(appels.index('lms server'), chargement)
        self.assertLess(chargement, appels.index('python -m outils sauvegarder'))
        self.assertEqual(appels.count('lms load '), 1)

    def test_interruption_donne_trois_commandes_protegees(self):
        # Ctrl-C pendant la construction : aucun retour automatique, et trois commandes à recopier. Le dépôt est dans
        # un dossier avec espaces, accent et apostrophe : chaque chemin doit rester un seul argument pour bash.
        e = Essai(sous_dossier="Mes documents/l'été 2026")
        self.addCleanup(e.nettoyer)
        processus = subprocess.Popen([BASH, str(e.script), '--version', 'v-b'], env=e.env(INTERROMPRE_CONSTRUCTION='1'),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     start_new_session=True)
        try:
            for _ in range(600):
                if (e.etat / 'en_construction').exists():
                    break
                time.sleep(0.1)
            self.assertTrue((e.etat / 'en_construction').exists(), 'construction jamais atteinte')
            os.killpg(processus.pid, signal.SIGINT)
            sortie, erreurs = processus.communicate(timeout=60)
        finally:
            if processus.poll() is None:
                os.killpg(processus.pid, signal.SIGKILL)
                processus.communicate()
        self.assertEqual(sortie, 'ETAPE mettre-a-jour : ECHEC interrompu\n', erreurs)
        archive, = e.archives()
        lignes = erreurs.splitlines()
        debut = lignes.index("Pour revenir en arrière, lancez ces trois commandes l'une après l'autre "
                             "(la restauration demande le mot de passe de la sauvegarde) :")
        commandes = lignes[debut + 1:debut + 4]
        attendus = [['git', '-C', str(e.depot), 'checkout', 'principale'],
                    [str(e.depot / 'install' / 'restaurer.sh'), '--archive', str(e.donnees / 'mises-a-jour' / archive),
                     '--forcer'],
                    [str(e.depot / 'install' / 'demarrer.sh')]]
        for commande, attendu in zip(commandes, attendus):
            with self.subTest(commande=commande):
                self.assertTrue(commande.startswith('  '), commande)
                # bash lui-même découpe la ligne (set -- n'exécute rien) : chaque chemin reste un seul argument.
                mots = subprocess.run([BASH, '-c', 'set -- ' + commande + '\nprintf "%s\\0" "$@"'], capture_output=True,
                                      text=True, timeout=30).stdout.split('\0')[:-1]
                self.assertEqual(mots, attendu)
        self.assertEqual(e.head(), e.b)  # interrompue après le passage à la version : rien n'a été défait
        self.assertEqual(e.echecs(), [])

    def test_restauration_interrompue_arrete_tout(self):
        e = self.essai()
        r = e.lancer('--version', 'v-b', VERIFICATION_EN_ECHEC=e.b, RESTAURATION_EN_ECHEC='restauration_interrompue')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC restauration_interrompue\n')
        # Aucun nouvel essai, aucun retour de version, plus rien après la restauration.
        self.assertEqual(e.evenements()[-1][0], 'restaurer')
        self.assertEqual(e.head(), e.b)
        self.assertNotIn(MESSAGE, r.stderr)
        self.assertIn(str(e.donnees / '.restauration-essai'), r.stderr)
        echec, = e.echecs()
        self.assertIn(str(echec), r.stderr)
        self.assertIn('intervention humaine', r.stderr)
        self.assertIn(e.a, r.stderr)  # version précédente, pour l'intervention

    def test_retour_qui_echoue_a_sa_propre_verification(self):
        e = self.essai()
        r = e.lancer('--version', 'v-b', VERIFICATION_TOUJOURS_EN_ECHEC='1')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC retour_impossible\n')
        self.assertNotIn(MESSAGE, r.stderr)
        self.assertEqual(e.head(), e.a)
        self.assertEqual(e.evenements()[-1], ('verifier', e.a))
        self.assertIn('intervention humaine', r.stderr)
        archive, = e.archives()
        self.assertIn(archive, r.stderr)

    def test_autre_echec_de_la_restauration(self):
        e = self.essai()
        r = e.lancer('--version', 'v-b', VERIFICATION_EN_ECHEC=e.b, RESTAURATION_EN_ECHEC='archive_illisible')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC retour_impossible\n')
        self.assertIn('archive_illisible', r.stderr)
        self.assertEqual(e.evenements()[-1][0], 'restaurer')
        self.assertEqual(e.head(), e.b)

    def test_modifications_locales_refusees(self):
        for modification, ligne in (('suivi', '  install/commun.sh (modifié)'), ('non_suivi', '  réunion.txt (ajouté)')):
            with self.subTest(modification=modification):
                e = self.essai()
                if modification == 'suivi':
                    with open(e.depot / 'install' / 'commun.sh', 'a') as fichier:
                        fichier.write('# modification locale\n')
                else:
                    (e.depot / 'réunion.txt').write_text('x\n')  # accent lisible (core.quotePath=false)
                r = e.lancer('--version', 'v-b')
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC modifications_locales\n')
                self.assertEqual(e.appels(), '')  # aucun conteneur touché
                self.assertEqual(e.head(), e.a)
                self.assertEqual(e.archives(), [])
                # Le message nomme le fichier en cause et dit quoi faire, en français.
                self.assertIn('\n' + ligne + '\n', r.stderr)
                self.assertIn('puis relancez la mise à jour', derniere_ligne(r.stderr))

    def test_fichiers_du_systeme_ignores(self):
        # Finder, l'Explorateur et les partages créent ces fichiers tout seuls : ils ne bloquent pas la mise à jour.
        e = self.essai()
        for chemin in ('.DS_Store', 'install/.DS_Store', 'Thumbs.db', 'desktop.ini', 'install/._mettre-a-jour.sh'):
            (e.depot / chemin).write_text('x\n')
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : OK\n', r.stderr)
        self.assertEqual(e.head(), e.b)

    def test_liste_des_fichiers_limitee_a_dix(self):
        e = self.essai()
        for numero in range(12):
            (e.depot / f'ajout-{numero:02d}.txt').write_text('x\n')
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC modifications_locales\n')
        listes = [ligne for ligne in r.stderr.splitlines() if ligne.startswith('  ajout-')]
        self.assertEqual(listes, [f'  ajout-{numero:02d}.txt (ajouté)' for numero in range(10)])
        self.assertIn('\n  … et 2 autres\n', r.stderr)

    def test_version_introuvable(self):
        e = self.essai()
        r = e.lancer('--version', 'v-inconnue')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC version_introuvable\n')
        self.assertEqual(e.appels(), '')
        self.assertEqual(e.head(), e.a)

    def test_sans_reseau(self):
        e = self.essai()
        git('fetch', '-q', '--tags', cwd=e.depot, env=e.env_git)  # v-b déjà connue localement
        git('remote', 'set-url', 'origin', str(e.base / 'injoignable'), cwd=e.depot, env=e.env_git)
        r = e.lancer('--version', 'v-inconnue')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC recuperation_impossible\n')
        self.assertEqual(e.appels(), '')
        # Récupération impossible mais version connue localement : la mise à jour se fait, avec un avertissement.
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : OK\n', r.stderr)
        self.assertIn('git fetch --tags', r.stderr)
        self.assertEqual(e.head(), e.b)

    def test_arguments(self):
        e = self.essai()
        for args in ([], ['--version'], ['--version', ''], ['--version', '--simulation'], ['--version', '-x'],
                     ['--version', 'v-b', '--inconnu'], ['--version', 'v-b', '--destination']):
            with self.subTest(args=args):
                r = e.lancer(*args)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC arguments_invalides\n')
        self.assertEqual(e.appels(), '')

    def test_destination_choisie(self):
        e = self.essai()
        for destination, code in (('relatif', 'destination_invalide'), (str(e.donnees / 'ailleurs'), 'destination_invalide'),
                                  (str(e.base / 'absente'), 'destination_absente')):
            with self.subTest(destination=destination):
                r = e.lancer('--version', 'v-b', '--destination', destination)
                self.assertEqual(r.stdout, f'ETAPE mettre-a-jour : ECHEC {code}\n')
        self.assertEqual(e.appels(), '')
        disque = e.base / 'disque externe'
        disque.mkdir()
        r = e.lancer('--version', 'v-b', '--destination', str(disque))
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : OK\n', r.stderr)
        self.assertEqual(len(list(disque.glob('territoria-*.tsv1'))), 1)
        self.assertFalse((e.donnees / 'mises-a-jour').exists())

    def test_sans_mot_de_passe_enregistre_ni_terminal(self):
        e = self.essai(mot_de_passe_enregistre=False)
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC terminal_requis\n')
        self.assertEqual(e.appels(), '')
        self.assertEqual(e.head(), e.a)

    def test_installation_absente(self):
        e = self.essai()
        shutil.rmtree(e.donnees / 'territoria')
        r = e.lancer('--version', 'v-b')
        self.assertEqual(r.stdout, 'ETAPE mettre-a-jour : ECHEC installation_absente\n')
        self.assertEqual(e.appels(), '')

    @unittest.skipUnless(SYSTEME in ('macos', 'linux'), 'pseudo-terminal POSIX')
    def test_mot_de_passe_saisi_une_fois_et_garde_pour_le_retour(self):
        e = self.essai(mot_de_passe_enregistre=False)
        terminal = Terminal([e.script, '--version', 'v-b'], e.env(VERIFICATION_EN_ECHEC=e.b))
        terminal.attendre('Mot de passe de la sauvegarde (saisie masquée) : ')
        terminal.repondre(MOT_DE_PASSE, masque=True)
        terminal.attendre('Confirmez le mot de passe : ')
        terminal.repondre(MOT_DE_PASSE, masque=True)
        code, sortie = terminal.finir(delai=120)
        self.assertEqual(code, 1, sortie)
        self.assertEqual(derniere_ligne(sortie), 'ETAPE mettre-a-jour : ECHEC mise_a_jour_annulee')
        self.assertNotIn(MOT_DE_PASSE, sortie)
        self.assertNotIn(MOT_DE_PASSE, e.appels())
        # Sauvegarde, vérification et restauration reçoivent le même mot de passe, par l'entrée standard.
        entrees = e.entrees()
        for commande in ('outils sauvegarder', 'outils verifier-sauvegarde', 'outils restaurer'):
            self.assertRegex(entrees, rf'== [^\n]*{commande}[^\n]*\n{re.escape(MOT_DE_PASSE)}\n')
        self.assertEqual(e.head(), e.a)


class SimulationTests(unittest.TestCase):
    def test_simulation_sans_effet(self):
        bac = Bac()
        self.addCleanup(bac.nettoyer)
        avant = etat_du_depot()
        r = subprocess.run([BASH, str(INSTALL / 'mettre-a-jour.sh'), '--version', 'v2.0', '--simulation'], env=bac.env(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, start_new_session=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(derniere_ligne(r.stdout), 'ETAPE mettre-a-jour : OK')
        for ligne in r.stdout.splitlines()[:-1]:
            self.assertRegex(ligne, r'^(\+ |# )', ligne)
        self.assertEqual(bac.appels(), '')
        self.assertFalse(bac.donnees.exists())
        self.assertEqual(etat_du_depot(), avant)
        # Mise à jour puis retour automatique, dans l'ordre du brief.
        ordre = ['fetch --tags', 'python -m outils sauvegarder', 'outils verifier-sauvegarde', 'checkout --quiet --detach',
                 'compose -p territoria', ' build territoria', ' build travaux', ' --profile outils run --rm -T travaux-modeles',
                 'outils provision-n8n --sortie /donnees-secrets/provision',
                 ' stop n8n', 'import:workflow --separate', 'publish:workflow --id=TerritoriaArre01', ' up -d',
                 'python -m outils verifier\n', ' stop\n',
                 'mkdir -m 700', 'echec-', 'python -m outils restaurer', ' checkout --quiet', ' build territoria', ' up -d',
                 'python -m outils verifier\n']
        position, positions = 0, []
        for morceau in ordre:
            position = r.stdout.index(morceau, position)
            positions.append(position)
        # Dossiers de la transcription créés et confiés aux services avant la construction (installations antérieures).
        brut = r.stdout.replace('\\ ', ' ')
        proprietaires = brut.index('chown 10001:10001 /territoria /territoria/audio /modeles-audio')
        self.assertLess(brut.index('checkout --quiet --detach'), proprietaires)
        self.assertLess(proprietaires, brut.index(' build territoria'))
        self.assertIn('--forcer', r.stdout)
        self.assertIn('/mises-a-jour', r.stdout)
        self.assertNotIn('--strict', r.stdout)


class StructureTests(unittest.TestCase):
    def test_corps_dans_une_fonction(self):
        # git checkout remplace le script pendant qu'il s'exécute : bash ne doit plus avoir à le relire.
        texte = (INSTALL / 'mettre-a-jour.sh').read_text(encoding='utf-8')
        self.assertTrue(texte.endswith('\nprincipal "$@"\nexit $?\n'))
        self.assertIn(f'MESSAGE_ANNULATION="{MESSAGE}"', texte)


if __name__ == '__main__':
    unittest.main()
