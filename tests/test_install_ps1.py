# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Scripts d'installation Windows (install/*.ps1) : structure, compatibilité avec Windows PowerShell 5.1, et vérifications
dans PowerShell 7 (conteneur Docker jetable : pwsh n'est pas installé sur les machines de développement).

Les vérifications du conteneur sont dans tests/verification_ps1.ps1 : analyse syntaxique, collisions de noms, chargement
sans effet, diagnostic, simulation de chaque étape avec de faux winget, lms, docker, podman, schtasks et icacls en tête du
PATH, fonctions de commun.ps1 et installer.ps1 avec des saisies simulées, scripts complets avec un faux Docker. Le conteneur
n'a pas de réseau (--network none) et le dépôt y est monté en lecture seule.

Ces essais ne prouvent ni icacls, ni COM (WScript.Shell), ni schtasks, ni winget, ni Get-NetTCPConnection, ni Windows
PowerShell 5.1 lui-même : voir « À valider sur un vrai Windows » dans le rapport de la tâche 12b. Les tests sans Docker (BOM,
syntaxe réservée à PowerShell 7, correspondance avec les .sh) tournent partout ; les autres sont ignorés sans Docker, sans
l'image ou, pour l'analyseur, sans accès à la galerie PowerShell.
"""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
INSTALL = RACINE / 'install'
NOMS = ('commun', 'diagnostic', 'installer', 'demarrer', 'arreter', 'sauvegarder', 'restaurer', 'mettre-a-jour')
# Image de PowerShell 7.5.0 (Ubuntu 24.04), épinglée par empreinte comme les autres images tierces.
IMAGE = ('mcr.microsoft.com/powershell:7.5-ubuntu-24.04@'
         'sha256:042240d57ec9e47e511033b92625a8d95875ee5860af3015992c248b58a8be81')
PSSA_VERSION = '1.25.0'
# Profil de compatibilité de PSScriptAnalyzer : Windows PowerShell 5.1.17763 (Windows 10 1809 et Windows Server 2019).
PROFIL_51 = 'win-8_x64_10.0.17763.0_5.1.17763.316_x64_4.0.30319.42000_framework'
ISOLATION = ['-e', 'POWERSHELL_TELEMETRY_OPTOUT=1', '-e', 'XDG_CACHE_HOME=/tmp/xdg/cache',
             '-e', 'XDG_DATA_HOME=/tmp/xdg/data', '-e', 'XDG_CONFIG_HOME=/tmp/xdg/config',
             '-e', 'PSModuleAnalysisCachePath=/tmp/xdg/psanalysis']

CAS_ATTENDUS = (
    'analyse_syntaxique', 'aucun_alias_en_collision', 'chargement_sans_effet', 'diagnostic_treize_cles',
    'simulation_etape_moteur', 'simulation_etape_lmstudio', 'simulation_etape_mistral', 'simulation_etape_secrets', 'simulation_etape_legifrance',
    'simulation_etape_demarrer', 'simulation_etape_n8n', 'simulation_etape_demo', 'simulation_etape_verifier',
    'simulation_etape_raccourci', 'simulation_etape_sauvegarde-auto', 'simulation_legifrance_sans_saisie_piste',
    'simulation_moteur_sans_moteur',
    'simulation_demarrer_construit_la_transcription', 'simulation_moteur_docker_reutilise', 'simulation_lmstudio_installe_puis_regle',
    'simulation_lmstudio_podman_et_lms_present', 'simulation_commandes_compose', 'simulation_raccourci_et_planification',
    'simulation_autres_scripts', 'refus_des_arguments', 'etapes_interactives_sans_terminal',
    'donnees_dans_le_depot_refusees', 'citation_des_arguments_windows', 'ecrire_variable', 'retirer_variable',
    'exiger_etape_derniere_ligne',
    'dossier_lmstudio_resolution', 'reglages_du_serveur_lm_studio', 'ecoute_de_lm_studio', 'requete_marquee_et_journaux',
    'modele_servi', 'executer_octets_delai_et_code', 'lancer_detache_et_lancer', 'lancer_detache_entree_standard',
    'sauvegarde_mot_de_passe_par_entree_standard', 'restauration', 'mistral_saisie_masquee', 'lmstudio_retire_mistral', 'mistral_lm_studio_non_demarre', 'legifrance_connecteurs_publics',
    'sauvegarde_auto_mot_de_passe_et_tache', 'raccourcis', 'lmstudio_demarrer_refuse_une_ecoute_sur_le_reseau',
    'dossiers_prives_avant_les_secrets', 'verification_du_dossier_de_donnees_et_de_la_destination',
    'demarrer_et_arreter_reels', 'demo_et_verifier_reprennent_le_code_d_outils',
    'sauvegarder_auto_script_complet_et_journal', 'aucun_secret_ni_asplaintext',
    'simulation_mise_a_jour', 'mise_a_jour_reussie', 'mise_a_jour_ratee_puis_retour',
    'mise_a_jour_restauration_interrompue', 'mise_a_jour_refus_avant_tout_changement',
    'chargement_du_modele_apres_le_demarrage', 'mise_a_jour_consigne_interruption_citee',
)

# Fonctions et codes d'échec de la version POSIX qui n'ont pas d'équivalent sous Windows (gestionnaires de paquets, launchd,
# cron, .desktop, Homebrew, groupe docker…) : tout le reste doit exister dans les .ps1.
FONCTIONS_POSIX_SEULEMENT = {
    'debut_etape', 'terminer',  # remplacés par executer_etape
    'lancer_root', 'gestionnaire_paquets', 'paquet_apt_disponible', 'paquet_compose_apt', 'paquet_dnf_disponible',
    'installer_paquet_linux', 'installer_docker_linux', 'assurer_groupe_docker', 'exiger_homebrew',
    'raccourci_macos', 'raccourci_linux', 'chemin_pour_desktop', 'chemin_tache', 'planifier_macos', 'planifier_linux',
    'podman_sans_root',
}
CODES_POSIX_SEULEMENT = {
    'homebrew_absent', 'python3_absent', 'reconnexion_requise', 'moteur_paquet_indisponible',
    'gestionnaire_de_paquets_absent', 'mise_a_jour_des_paquets_impossible', 'installation_docker_impossible',
    'demarrage_docker_impossible', 'groupe_docker_impossible', 'passerelle_introuvable', 'systeme_non_pris_en_charge',
    'erreur_inattendue', 'interrompu',
}


def code(nom):
    """Texte d'un script sans ses lignes de commentaire."""
    texte = (INSTALL / nom).read_text(encoding='utf-8-sig')
    return '\n'.join(ligne for ligne in texte.splitlines() if not ligne.lstrip().startswith('#'))


def docker_utilisable():
    if shutil.which('docker') is None:
        return False
    try:
        return subprocess.run(['docker', 'info'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def image_disponible():
    try:
        if subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True, timeout=30).returncode == 0:
            return True
        return subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', IMAGE], capture_output=True,
                              timeout=600).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pwsh_dans_conteneur(arguments, reseau, delai):
    commande = ['docker', 'run', '--rm', '--platform', 'linux/amd64']
    if not reseau:
        commande += ['--network', 'none']
    commande += ['-v', f'{RACINE}:/repo:ro', '-w', '/repo', *ISOLATION, IMAGE, 'pwsh', '-NoProfile', *arguments]
    return subprocess.run(commande, capture_output=True, text=True, timeout=delai)


class StructureTests(unittest.TestCase):
    def test_sept_scripts_comme_les_sh(self):
        self.assertEqual(sorted(p.stem for p in INSTALL.glob('*.ps1')), sorted(NOMS))
        for nom in NOMS:
            self.assertTrue((INSTALL / f'{nom}.sh').exists(), nom)

    def test_utf8_avec_bom(self):
        # Windows PowerShell 5.1 lit en ANSI un fichier UTF-8 sans BOM : les accents des messages seraient altérés.
        for nom in NOMS:
            with self.subTest(script=nom):
                octets = (INSTALL / f'{nom}.ps1').read_bytes()
                self.assertTrue(octets.startswith(b'\xef\xbb\xbf'), 'BOM UTF-8 absent')
                octets.decode('utf-8')
                self.assertNotIn(b'\r', octets)  # fins de ligne LF dans le dépôt ; PowerShell accepte les deux

    def test_mode_strict_et_chargement_de_commun(self):
        self.assertIn('$ErrorActionPreference = \'Stop\'', code('commun.ps1'))
        self.assertIn('Set-StrictMode -Version 2.0', code('commun.ps1'))
        for nom in NOMS[1:]:
            with self.subTest(script=nom):
                texte = code(f'{nom}.ps1')
                self.assertIn('$ErrorActionPreference = \'Stop\'', texte)
                self.assertIn("Set-StrictMode -Version 2.0", texte)
                self.assertIn(". (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')", texte)

    def test_rien_de_reserve_a_powershell_7(self):
        interdits = {
            r'\$IsWindows|\$IsLinux|\$IsMacOS': '$IsWindows (absent de la version 5.1)',
            r'-AdditionalChildPath': 'Join-Path à plusieurs éléments',
            r'\.ArgumentList\b': 'ProcessStartInfo.ArgumentList (absent de .NET Framework)',
            r'utf8NoBOM|-AsByteStream|-AsHashtable|-Parallel|-NoEmphasis': 'option réservée à PowerShell 6+',
            r'\?\?|\?\.|\?\[': 'opérateur réservé à PowerShell 7',
            r'ForEach-Object\s+-Parallel': 'ForEach-Object -Parallel',
            r'\bGet-Content\b|\bSet-Content\b|\bOut-File\b|\bAdd-Content\b':
                'lecture ou écriture sans encodage explicite (utiliser System.IO.File et UTF8Encoding)',
            r'-AsPlainText|ConvertTo-SecureString': 'mot de passe converti en clair',
            r'\bInvoke-Expression\b|\biex\b': 'Invoke-Expression',
            r'^\s*(curl|wget)\b': 'alias de Invoke-WebRequest dans Windows PowerShell 5.1',
        }
        for nom in NOMS:
            texte = code(f'{nom}.ps1')
            for motif, explication in interdits.items():
                with self.subTest(script=nom, interdit=explication):
                    self.assertIsNone(re.search(motif, texte, re.M), explication)
            # Join-Path : toujours -Path et -ChildPath (deux éléments au plus)
            for ligne in texte.splitlines():
                if 'Join-Path' in ligne:
                    self.assertRegex(ligne, r'Join-Path -Path .+ -ChildPath ', ligne)

    def test_options_et_valeurs_comparees_en_respectant_la_casse(self):
        # Les « case » de bash distinguent majuscules et minuscules : « --etape Moteur » est refusé. -eq, -in, -contains,
        # -match et -like de PowerShell les ignorent : il faut -ceq, -cin, -ccontains, -cmatch.
        installer = code('installer.ps1')
        principal = re.search(r'^function principal \{.*?^\}', installer, re.M | re.S).group(0)
        ignorants = r'\s-(in|notin|eq|ne|contains|notcontains|match|notmatch|like|notlike)\s'
        self.assertIsNone(re.search(ignorants, principal), 'principal : comparaison insensible à la casse')
        for nom in ('demarrer', 'arreter', 'sauvegarder', 'restaurer', 'mettre-a-jour'):
            for ligne in code(f'{nom}.ps1').splitlines():
                if "'--" in ligne or "'-h'" in ligne:
                    self.assertIsNone(re.search(ignorants, ligne), ligne)

    def test_les_sorties_ne_passent_pas_par_le_pipeline(self):
        for nom in NOMS:
            with self.subTest(script=nom):
                self.assertIsNone(re.search(r'\bWrite-(Output|Host)\b', code(f'{nom}.ps1')))

    def test_pas_de_variable_suivie_de_deux_points_dans_une_chaine(self):
        # "$a:/x" est une erreur d'analyse (variable à portée) : il faut ${a}:/x ou l'opérateur -f.
        for nom in NOMS:
            with self.subTest(script=nom):
                self.assertIsNone(re.search(r'"[^"\n]*\$[A-Za-z_]\w*:[^"\n]*"', code(f'{nom}.ps1')))

    def test_les_fonctions_de_commun_sh_ont_leur_equivalent(self):
        posix = (INSTALL / 'commun.sh').read_text(encoding='utf-8') + (INSTALL / 'installer.sh').read_text(encoding='utf-8')
        fonctions_sh = set(re.findall(r'^([a-z_0-9]+)\(\) \{', posix, re.M))
        windows = ''.join(code(f'{nom}.ps1') for nom in NOMS)
        fonctions_ps = set(re.findall(r'^function ([A-Za-z_0-9]+)\b', windows, re.M))
        absentes = {f for f in fonctions_sh - fonctions_ps if f not in FONCTIONS_POSIX_SEULEMENT}
        self.assertEqual(absentes, set(), 'fonctions des scripts .sh sans équivalent dans les .ps1')

    def test_memes_codes_d_echec_que_les_sh(self):
        posix = ''.join((INSTALL / f'{nom}.sh').read_text(encoding='utf-8') for nom in NOMS)
        windows = ''.join(code(f'{nom}.ps1') for nom in NOMS)
        codes_sh = set(re.findall(r'\b(?:echouer|phase) ([a-z_0-9]+)', posix))
        codes_sh |= set(re.findall(r'refuser_arguments [^\n]*? ([a-z_]+_(?:inconnu|invalides|inconnue))\b', posix))
        manquants = {c for c in codes_sh - CODES_POSIX_SEULEMENT if c not in windows}
        self.assertEqual(manquants, set(), "codes d'échec des .sh absents des .ps1")

    def test_les_dix_etapes_sont_ecrites_litteralement(self):
        texte = code('installer.ps1')
        for etape in ('moteur', 'lmstudio', 'secrets', 'legifrance', 'demarrer', 'n8n', 'demo', 'verifier', 'raccourci',
                      'sauvegarde-auto'):
            self.assertIn(f"'{etape}'", texte)
        for code_arguments in ('arguments_invalides', 'etape_inconnue', 'palier_inconnu', 'terminal_requis'):
            self.assertIn(code_arguments, texte + code('commun.ps1'))

    def test_appels_de_programmes_par_leur_nom_nu(self):
        # Les essais placent des programmes factices dans le PATH : jamais « schtasks.exe », « winget.exe »…
        for nom in NOMS:
            texte = code(f'{nom}.ps1')
            for programme in ('schtasks', 'icacls', 'winget', 'manage-bde', 'docker', 'podman'):
                self.assertNotIn(f'{programme}.exe', texte)

    def test_aucune_commande_dans_le_pipeline_avec_un_mot_de_passe(self):
        # Le mot de passe passe par des octets sur l'entrée standard (executer) ou par un fichier monté.
        for nom in NOMS:
            for ligne in code(f'{nom}.ps1').splitlines():
                if re.search(r'\$(motdepasse|confirmation|secret|identifiant)\b', ligne):
                    self.assertNotRegex(ligne, r'\b(lancer|lancer_tableau|lancer_detache|compose|moteur|afficher_commande|info|sortie)\b', ligne)
                    self.assertNotRegex(ligne, r'\|\s*&|\| *(docker|podman)', ligne)

    def test_documentation_windows(self):
        texte = (RACINE / 'docs' / 'acces-lmstudio.md').read_text(encoding='utf-8')
        for mot in ('installer.ps1', 'winget install --exact --id ElementLabs.LMStudio', 'RedHat.Podman',
                    'Docker.DockerCompose', 'host.containers.internal', 'Get-NetTCPConnection', 'schtasks /Create',
                    '%USERPROFILE%\\.lmstudio-home-pointer', 'ConvertFrom-Json', 'À valider sur un Windows réel',
                    'lmstudio_expose_reseau'):
            self.assertIn(mot, texte)


@unittest.skipUnless(docker_utilisable(), 'Docker est absent ou ne répond pas')
class ConteneurTests(unittest.TestCase):
    """Les .ps1 dans PowerShell 7 (conteneur sans réseau, dépôt en lecture seule)."""

    @classmethod
    def setUpClass(cls):
        if not image_disponible():
            raise unittest.SkipTest(f"image {IMAGE} introuvable et impossible à télécharger")
        resultat = pwsh_dans_conteneur(['-File', '/repo/tests/verification_ps1.ps1'], reseau=False, delai=900)
        cls.resultats = {}
        for ligne in resultat.stdout.splitlines():
            if ligne.startswith('RESULTAT|'):
                morceaux = ligne.split('|', 3)
                cls.resultats[morceaux[1]] = (morceaux[2], morceaux[3] if len(morceaux) > 3 else '')
        cls.sortie = resultat.stdout + resultat.stderr

    def test_tous_les_cas_ont_tourne(self):
        self.assertEqual(sorted(self.resultats), sorted(CAS_ATTENDUS), self.sortie[-2000:])

    def test_chaque_cas(self):
        for nom in CAS_ATTENDUS:
            with self.subTest(cas=nom):
                etat, detail = self.resultats.get(nom, ('ABSENT', ''))
                self.assertEqual(etat, 'OK', detail)


@unittest.skipUnless(docker_utilisable(), 'Docker est absent ou ne répond pas')
class AnalyseurTests(unittest.TestCase):
    """PSScriptAnalyzer (version épinglée) : aucune erreur, et compatibilité avec Windows PowerShell 5.1 (syntaxe, commandes,
    types .NET du profil Windows 10 1809)."""

    PROGRAMME = f'''
$ProgressPreference = 'SilentlyContinue'
try {{
    Install-Module PSScriptAnalyzer -RequiredVersion {PSSA_VERSION} -Scope CurrentUser -Force -Repository PSGallery -ErrorAction Stop
}} catch {{
    Write-Output 'SANS_GALERIE'
    exit 0
}}
$erreurs = @(Invoke-ScriptAnalyzer -Path /repo/install -Severity Error)
Write-Output ('ERREURS|' + $erreurs.Count)
foreach ($e in $erreurs) {{ Write-Output ('  {{0}}:{{1}} {{2}} {{3}}' -f $e.ScriptName, $e.Line, $e.RuleName, $e.Message) }}
$profil = '{PROFIL_51}'
$reglages = @{{
    IncludeRules = @('PSUseCompatibleSyntax', 'PSUseCompatibleCommands', 'PSUseCompatibleTypes')
    Rules = @{{
        PSUseCompatibleSyntax = @{{ Enable = $true; TargetVersions = @('5.1') }}
        PSUseCompatibleCommands = @{{ Enable = $true; TargetProfiles = @($profil) }}
        PSUseCompatibleTypes = @{{ Enable = $true; TargetProfiles = @($profil) }}
    }}
}}
$compat = @(Invoke-ScriptAnalyzer -Path /repo/install -Settings $reglages)
Write-Output ('COMPATIBILITE_51|' + $compat.Count)
foreach ($e in $compat) {{ Write-Output ('  {{0}}:{{1}} {{2}} {{3}}' -f $e.ScriptName, $e.Line, $e.RuleName, $e.Message) }}
'''

    @classmethod
    def setUpClass(cls):
        if not image_disponible():
            raise unittest.SkipTest(f"image {IMAGE} introuvable et impossible à télécharger")
        resultat = pwsh_dans_conteneur(['-Command', cls.PROGRAMME], reseau=True, delai=900)
        cls.sortie = resultat.stdout + resultat.stderr
        if 'SANS_GALERIE' in cls.sortie:
            raise unittest.SkipTest('galerie PowerShell inaccessible : PSScriptAnalyzer ne peut pas être installé')

    def test_aucune_erreur(self):
        self.assertIn('ERREURS|0', self.sortie, self.sortie[-3000:])

    def test_compatible_avec_windows_powershell_5_1(self):
        self.assertIn('COMPATIBILITE_51|0', self.sortie, self.sortie[-3000:])


if __name__ == '__main__':
    unittest.main()
