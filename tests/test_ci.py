# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
WORKFLOW = RACINE / '.github' / 'workflows' / 'ci.yml'
SCRIPT = RACINE / 'tests' / 'integration.sh'
COMMUN = RACINE / 'install' / 'commun.sh'
CLES_DIAGNOSTIC = ('systeme', 'architecture', 'memoire_go', 'gpu', 'disque_libre_go', 'port_8878', 'moteur',
                   'docker_desktop', 'lms', 'chiffrement_disque', 'palier_modele')


def fonction(texte, nom):
    """Corps d'une fonction shell `nom() {` … `}` (accolade fermante en début de ligne)."""
    trouve = re.search(rf'^{re.escape(nom)}\(\) \{{\n(.*?)^\}}', texte, re.M | re.S)
    assert trouve, nom
    return trouve.group(1)


def bloc(texte, nom):
    """Texte du job `nom` d'un workflow (jusqu'au job suivant, de même indentation)."""
    trouve = re.search(rf'^  {re.escape(nom)}:\n(.*?)(?=^  \S|\Z)', texte, re.M | re.S)
    assert trouve, nom
    return trouve.group(1)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texte = WORKFLOW.read_text(encoding='utf-8')

    def test_jobs_tests_et_integration_sur_ubuntu_scripts_en_matrice(self):
        jobs = re.findall(r'^  ([a-z_-]+):\n(?:    #[^\n]*\n)*    runs-on: ([^\n]+)$', self.texte, re.M)
        self.assertEqual(jobs, [('tests', 'ubuntu-latest'), ('integration', 'ubuntu-latest'),
                                ('scripts', '${{ matrix.os }}')])

    def etapes_du_job(self, nom):
        """Étapes d'un job : liste de blocs de texte, chacun commençant par « - »."""
        job = bloc(self.texte, nom)
        return re.split(r'(?m)^      - ', job.split('    steps:\n', 1)[1])[1:]

    def test_job_scripts_sur_macos_et_windows(self):
        job = bloc(self.texte, 'scripts')
        self.assertRegex(job, r'os: \[[^\]]*macos-latest[^\]]*\]')
        self.assertRegex(job, r'os: \[[^\]]*windows-latest[^\]]*\]')
        self.assertIn('fail-fast: false', job)
        # chaque étape, hors la récupération du dépôt, est réservée à un système
        for etape in self.etapes_du_job('scripts')[1:]:
            self.assertRegex(etape, r"if: runner\.os == '(macOS|Windows)'", etape)

    def test_etapes_macos(self):
        etapes = [e for e in self.etapes_du_job('scripts') if "runner.os == 'macOS'" in e]
        job = '\n'.join(etapes)
        self.assertEqual(len(etapes), 3)
        # shellcheck n'est pas garanti sur l'image macOS : installé au besoin, puis lancé sur tous les scripts .sh.
        self.assertIn('command -v shellcheck >/dev/null || brew install shellcheck', job)
        self.assertIn('shellcheck install/*.sh', job)
        self.assertIn('bash install/diagnostic.sh', job)
        for cle in CLES_DIAGNOSTIC:
            self.assertIn(cle, job)
        self.assertIn('bash install/installer.sh --etape moteur --simulation', job)
        self.assertIn('"ETAPE moteur : OK"', job)
        self.assertLess(job.index('shellcheck install'), job.index('diagnostic.sh'))
        self.assertNotIn('.ps1', job)

    def test_etapes_windows(self):
        etapes = [e for e in self.etapes_du_job('scripts') if "runner.os == 'Windows'" in e]
        job = '\n'.join(etapes)
        self.assertEqual(len(etapes), 5)
        # PSScriptAnalyzer : erreurs seulement, version épinglée comme dans les essais (tests/test_install_ps1.py)
        self.assertIn('Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Scope CurrentUser -Force', job)
        self.assertIn('Invoke-ScriptAnalyzer -Path install -Severity Error', job)
        # syntaxe de chaque script avec Windows PowerShell 5.1, celui des postes de la collectivité
        analyse = next(e for e in etapes if 'Parser]::ParseFile' in e)
        self.assertIn('shell: powershell', analyse)
        self.assertIn('Get-ChildItem install -Filter *.ps1', analyse)
        # diagnostic : les 11 clés, dans l'ordre, avec Windows PowerShell 5.1 et PowerShell 7
        diagnostic = next(e for e in etapes if 'install/diagnostic.ps1' in e)
        for cle in CLES_DIAGNOSTIC:
            self.assertIn(f"'{cle}'", diagnostic)
        self.assertIn("foreach ($ps in 'powershell', 'pwsh')", diagnostic)
        self.assertIn("'actif', 'inactif', 'inconnu'", diagnostic)  # manage-bde s'exécute (runner élevé) : pas « inconnu » seul
        # étape moteur en simulation, puis toutes les étapes
        self.assertIn('install/installer.ps1 --etape moteur --simulation', job)
        self.assertIn("'ETAPE moteur : OK'", job)
        toutes = next(e for e in etapes if 'Simulation de toutes les étapes' in e)
        for etape in ('moteur', 'lmstudio', 'secrets', 'legifrance', 'demarrer', 'n8n', 'demo', 'verifier', 'raccourci',
                      'sauvegarde-auto'):
            self.assertIn(f"'{etape}'", toutes)
        for script in ('demarrer', 'arreter', 'sauvegarder', 'restaurer', 'mettre-a-jour'):
            self.assertIn(f"'{script}'", toutes)
        self.assertIn('TERRITORIA_DONNEES_HOTE', toutes)
        self.assertNotIn('bash ', job)

    def test_job_tests(self):
        job = bloc(self.texte, 'tests')
        self.assertIn("python-version: '3.12'", job)
        self.assertIn('pip install -r requirements.lock', job)
        self.assertIn('sha256sum -c SHA256SUMS', job)
        self.assertIn('pip install --no-deps vendor/droit_territorial-0.4.0-py3-none-any.whl', job)
        self.assertIn('PYTHONPATH=app python -m unittest discover -s tests -q', job)
        self.assertRegex(job, r'npm ci[^\n]*\n\s+working-directory: app/frontend')
        self.assertRegex(job, r'npm run test:ui\n\s+working-directory: app/frontend')
        # la roue et le verrou passent avant les tests, npm ci avant test:ui
        self.assertLess(job.index('requirements.lock'), job.index('unittest'))
        self.assertLess(job.index('npm ci'), job.index('test:ui'))

    def test_job_integration_appelle_le_script(self):
        job = bloc(self.texte, 'integration')
        self.assertRegex(job, r'run: (bash )?tests/integration\.sh')
        self.assertNotIn('docker compose', job)  # tout le parcours est dans le script, identique en local
        # La version ratée de la mise à jour attend ATTENTE_MAX (300 s) avant le retour automatique.
        self.assertGreaterEqual(int(re.search(r'timeout-minutes: (\d+)', job).group(1)), 45)

    def test_actions_epinglees_par_empreinte_de_commit(self):
        utilisees = re.findall(r'uses: (\S+)', self.texte)
        self.assertTrue(utilisees)
        for action in utilisees:
            self.assertRegex(action, r'^actions/[\w-]+@[0-9a-f]{40}$', action)

    def test_permissions_minimales(self):
        self.assertRegex(self.texte, re.compile(r'^permissions:\n  contents: read$', re.M))


class ScriptIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texte = SCRIPT.read_text(encoding='utf-8')

    def test_executable_et_syntaxe_valide(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertTrue(self.texte.startswith('#!/usr/bin/env bash\n'))
        bash = shutil.which('bash')
        if bash:
            self.assertEqual(subprocess.run([bash, '-n', str(SCRIPT)], capture_output=True).returncode, 0)

    def test_mode_strict_et_nettoyage_sur_sortie(self):
        self.assertIn('\nset -euo pipefail\n', self.texte)
        self.assertRegex(self.texte, re.compile(r'^trap nettoyer EXIT$', re.M))
        nettoyage = fonction(self.texte, 'nettoyer')
        self.assertIn('rm -rf "$S/provision"', nettoyage)  # jetons en clair
        self.assertIn('compose down -v --remove-orphans', nettoyage)
        self.assertIn('supprimer_dossier "$DONNEES"', nettoyage)
        self.assertIn('supprimer_dossier "$SAUVEGARDES"', nettoyage)

    def test_dossier_temporaire_et_projet_dedie(self):
        self.assertIn('PROJET="territoria-ci"', self.texte)
        self.assertRegex(self.texte, r'DONNEES="\$\(mktemp -d ')
        self.assertRegex(self.texte, r'SAUVEGARDES="\$\(mktemp -d ')
        self.assertIn('export TERRITORIA_DONNEES_HOTE="$DONNEES"', self.texte)
        # exporté avant de sourcer commun.sh, qui en dérive S (dossier des secrets)
        self.assertLess(self.texte.index('export TERRITORIA_DONNEES_HOTE="$DONNEES"'),
                        self.texte.index('. "$DEPOT/install/commun.sh"'))
        self.assertNotRegex(self.texte, r'-p territoria[ "]')  # jamais le projet d'une installation réelle
        self.assertNotRegex(self.texte, r'/territoria-donnees')

    def test_ne_touche_que_son_propre_projet(self):
        self.assertIn('--filter "label=com.docker.compose.project=$PROJET"', self.texte)  # docker ps limité au projet
        self.assertNotRegex(self.texte, r'docker (rm|stop|kill|restart|exec|rmi|system|volume|container|network (rm|prune))\b')
        # Aucun appel Compose direct : tout passe par compose() de install/commun.sh, qui impose le projet PROJET…
        self.assertEqual(re.findall(r'docker compose (-p \S+|version)', self.texte), ['version'])
        commun = COMMUN.read_text(encoding='utf-8')
        self.assertIn('lancer "$MOTEUR" compose -p "$PROJET" --env-file "$S/compose.env" "${FICHIERS_COMPOSE[@]}" "$@"',
                      fonction(commun, 'compose'))
        # … remplacé par le projet dédié juste après avoir sourcé commun.sh, avant tout appel.
        principal = self.texte[self.texte.index('. "$DEPOT/install/commun.sh"'):]
        self.assertLess(principal.index('PROJET="territoria-ci"'), principal.index('preparer_dossiers'))
        # Ses projets, et aucun autre, que le nom soit entre guillemets ou non. Dans le clone d'essai de la mise à jour, le
        # projet par défaut d'une installation (« territoria ») n'apparaît que pour être remplacé, avant tout appel.
        remplacement = "sed -e 's/^PROJET=territoria$/PROJET=territoria-ci-maj/'"
        self.assertIn(remplacement, self.texte)
        noms = set(re.findall(r'''\bPROJET=["']?([\w.-]+)''', self.texte.replace('^PROJET=territoria$', '')))
        self.assertEqual(noms, {'territoria-ci', 'territoria-ci-maj'})

    def test_les_deux_fichiers_compose(self):
        self.assertIn('FICHIERS_COMPOSE+=(-f "$RACINE/conteneurs/compose.ci.yaml")', self.texte)
        commun = COMMUN.read_text(encoding='utf-8')
        self.assertIn('FICHIERS_COMPOSE=(-f "$RACINE/conteneurs/compose.yaml")', commun)
        self.assertIn('--env-file "$S/compose.env"', fonction(commun, 'compose'))

    def test_sequences_partagees_avec_les_scripts_d_installation(self):
        self.assertIn('. "$DEPOT/install/commun.sh"', self.texte)
        for appel in ('preparer_dossiers', 'outils_jetable secrets', 'provisionner_n8n', 'outils_jetable proprietaire-n8n',
                      'outils_service demo', 'attendre_sain n8n', 'sauvegarde_creer "$SAUVEGARDES" auto',
                      'sauvegarde_elaguer "$SAUVEGARDES"', 'sauvegarde_restaurer "$SAUVEGARDES" forcer auto'):
            self.assertIn(appel, self.texte)
        # Plus aucune copie locale des séquences de conteneurs.
        for copie in ('outils_secrets()', 'outils_dans_le_service()', 'ponctuel()', 'attendre_sain()', 'exiger_etape()',
                      'import:credentials', 'publish:workflow', 'chown 10001'):
            self.assertNotIn(copie, self.texte)

    def test_proprietaires_des_dossiers_controles(self):
        self.assertIn('[ "$proprietaires" = "/d/territoria 10001:10001 /d/n8n 1000:1000 /d/caddy 10002:10002 700 " ]', self.texte)
        principal = self.texte[self.texte.index('docker compose version >/dev/null'):]
        self.assertLess(principal.index('preparer_dossiers'), principal.index('[ "$proprietaires" ='))

    def test_ordre_du_parcours(self):
        ordre = ['preparer_dossiers', 'compose build territoria', 'outils_jetable secrets', 'provisionner_n8n',
                 'test ! -e "$S/provision"', 'compose up -d', 'outils_jetable proprietaire-n8n', 'outils_service demo',
                 'outils_service verifier', 'compose logs --no-color territoria n8n', 'docker network inspect',
                 'sauvegarde_restaurer']
        principal = self.texte[self.texte.index('docker compose version >/dev/null'):]  # après les définitions de fonctions
        positions = [principal.index(etape) for etape in ordre]
        self.assertEqual(positions, sorted(positions))

    def test_provisionnement_de_n8n_dans_commun(self):
        provision = fonction(COMMUN.read_text(encoding='utf-8'), 'provisionner_n8n')
        ordre = ['outils_jetable provision-n8n', 'compose stop n8n', 'import:credentials', 'import:workflow',
                 'publish:workflow --id=TerritoriaConv01', 'publish:workflow --id=TerritoriaArre01', 'rm -rf "$S/provision"']
        positions = [provision.index(etape) for etape in ordre]
        self.assertEqual(positions, sorted(positions))
        # Piège dans un sous-shell : provision/ (jetons en clair) supprimé même en cas d'échec, sans remplacer le piège de
        # l'appelant (nettoyer dans integration.sh, terminer dans les scripts d'installation).
        self.assertRegex(provision, r'\(\n\s+trap nettoyer_provision EXIT\n')
        self.assertIn('rm -rf "$S/provision"', fonction(COMMUN.read_text(encoding='utf-8'), 'nettoyer_provision'))

    def test_verifier_est_lance_en_strict_puis_sans_et_ne_laisse_qu_un_espace(self):
        self.assertEqual(self.texte.count('outils_service verifier --strict'), 2)
        self.assertEqual(len(re.findall(r'^outils_service verifier$', self.texte, re.M)), 1)  # mode des installations
        self.assertIn('[ "$espaces" = "1" ]', self.texte)

    def test_sauvegarde_pendant_que_les_services_tournent(self):
        principal = self.texte[self.texte.index('docker compose version >/dev/null'):]
        ordre = [r'^outils_service verifier$', 'sauvegarde_creer', 'outils verifier-sauvegarde', 'outils restaurer',
                 'list:workflow', 'export:credentials', 'sauvegarde_elaguer', 'compose logs --no-color territoria n8n',
                 'sauvegarde_restaurer']
        positions = [re.search(etape if etape.startswith('^') else re.escape(etape), principal, re.M).start() for etape in ordre]
        self.assertEqual(positions, sorted(positions))  # après le parcours, services démarrés : le WAL est réel
        self.assertIn('[ "$wal" -ge 1 ]', principal)
        commun = COMMUN.read_text(encoding='utf-8')
        ponctuel = fonction(commun, 'ponctuel')
        for option in ('--user 0:0', '--network none', '--read-only', '--tmpfs /tmp:mode=1777'):
            self.assertIn(option, ponctuel)
        # sauvegarde_creer contrôle la destination puis écrit l'archive (sauvegarde_ecrire, partagé avec la mise à jour).
        self.assertIn('verifier_destination "$destination"\n    sauvegarde_ecrire "$destination" "$mode"',
                      fonction(commun, 'sauvegarde_creer'))
        creer = fonction(commun, 'sauvegarde_ecrire')
        self.assertIn('-v "$TERRITORIA_DONNEES_HOTE:/source:ro" -v "$destination:/destination"', creer)  # source en lecture seule
        self.assertIn('--depuis-fichier /source/secrets/sauvegarde.motdepasse', creer)
        for commande in ('verifier-sauvegarde', 'restaurer'):
            self.assertIn(f'exiger_etape {commande} ', principal)
        self.assertEqual(principal.count('<"$S/sauvegarde.motdepasse"'), 2)  # vérification et restauration : entrée standard
        self.assertIn('[ "$restaure" = "1 compose.env n8n-admin.txt n8n.env territoria.env" ]', principal)
        # Restauration forcée de l'installation : services arrêtés, conteneur ponctuel en root, mot de passe sur l'entrée standard.
        restaurer = fonction(commun, 'sauvegarde_restaurer')
        self.assertLess(restaurer.index('compose stop'), restaurer.index('python -m outils restaurer'))
        self.assertIn('<"$FICHIER_MOT_DE_PASSE"', restaurer)
        # Restauration éprouvée sans aucun conteneur créé : l'image est retrouvée par son nom de construction.
        self.assertLess(principal.index('compose down --remove-orphans\nsauvegarde_restaurer'), principal.index('sauvegarde_restaurer'))
        image = fonction(commun, 'image_territoria')
        self.assertIn('compose images -q territoria', image)
        self.assertIn('"$MOTEUR" image inspect --format \'{{.Id}}\' "$nom"', image)
        self.assertIn('"$PROJET-territoria"', image)

    def test_mise_a_jour_ratee_dans_un_clone_temporaire(self):
        # R25 : le script de mise à jour (qui fait git checkout) ne tourne que dans un clone temporaire.
        section = self.texte[self.texte.index('etape "Mise à jour ratée'):]
        self.assertIn('MAJ="$(mktemp -d ', section)
        self.assertIn('git clone --quiet --no-hardlinks "$DEPOT" "$CLONE"', section)
        self.assertIn('env -u TERRITORIA_DONNEES_HOTE bash "$CLONE/install/mettre-a-jour.sh" --version essai-echec', section)
        self.assertNotIn('$DEPOT/install/mettre-a-jour', self.texte)
        # Le dépôt de travail n'est que lu : ni commit, ni étiquette, ni changement de version.
        self.assertNotRegex(self.texte, r'git -C "\$DEPOT" (commit|tag [^-]|checkout|reset|switch|add|fetch|stash)')
        self.assertIn('git_essai tag essai-echec', section)
        self.assertIn('[ "$(etat_du_depot)" = "$depot_avant" ]', section)
        self.assertIn('[ -z "$(git -C "$DEPOT" tag -l essai-echec)" ]', section)
        # Câblage du clone : projet dédié, surcharge de la CI, jamais LM Studio (installé sur les postes de développement).
        self.assertIn('s/^PROJET=territoria$/PROJET=territoria-ci-maj/', section)
        self.assertIn('compose.ci.yaml', section)
        self.assertIn('chemin_lms() { :; }', section)
        self.assertIn('[ "$cablage" = "territoria-ci-maj|', section)
        # Version ratée (/healthz en 500), résultats attendus, données intactes, echec-* privé, nettoyage du projet.
        self.assertIn("return _json(500, {'ready': False}), None", section)
        for attendu in ('[ "$code_maj" = "1" ]', '"ETAPE mettre-a-jour : ECHEC mise_a_jour_annulee"',
                        '"Mise à jour annulée : version précédente rétablie."', '[ "$(git -C "$CLONE" rev-parse HEAD)" = "$ORIGINE_MAJ" ]',
                        'dans_le_clone outils_service verifier', '[ "$donnees_apres" = "$donnees_avant" ]',
                        "-name 'echec-*' -type d -perm 700", '[ "$marqueur_echec" = "1" ]',
                        'dans_le_clone compose down -v --remove-orphans --rmi local'):
            self.assertIn(attendu, section)
        nettoyage = fonction(self.texte, 'nettoyer')
        self.assertIn('dans_le_clone compose down -v --remove-orphans --rmi local', nettoyage)
        self.assertIn('supprimer_dossier "$MAJ"', nettoyage)

    def test_controle_des_journaux(self):
        for motif in ('archives municipales fictives', 'rue des Essais'):
            self.assertIn(f"-e '{motif}'", self.texte)
        self.assertIn('[ "$fuites" = "0" ]', self.texte)

    def test_aucun_secret_en_argument_ni_affiche(self):
        self.assertNotRegex(self.texte, r'(?i)(token|jeton|password|mot[ _-]de[ _-]passe|encryption)[A-Za-z_]*=')
        self.assertNotIn('cat "$S/', self.texte)


if __name__ == '__main__':
    unittest.main()
