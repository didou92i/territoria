# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Cohérence des documents d'installation (AGENTS.md, CLAUDE.md, README.md, .gitattributes) avec les scripts.

Ces documents décrivent ce que le code fait vraiment : une étape citée qui n'existe pas, un script ou une option qui a
changé de nom, un code d'échec qui n'est plus produit, et c'est l'assistant (ou le technicien) qui se trompe. Les listes
de référence sont lues dans les scripts eux-mêmes, jamais recopiées ici.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import e2e
import outils

RACINE = Path(__file__).resolve().parents[1]
INSTALL = RACINE / 'install'
AGENTS = RACINE / 'AGENTS.md'
README = RACINE / 'README.md'
CLAUDE = RACINE / 'CLAUDE.md'
GITATTRIBUTES = RACINE / '.gitattributes'

# Ordre du déroulé de la spécification (§4.2) ; sauvegarde-auto est facultative.
ETAPES = ('moteur', 'lmstudio', 'mistral', 'secrets', 'legifrance', 'demarrer', 'n8n', 'demo', 'verifier', 'raccourci',
          'sauvegarde-auto')
# Scripts d'exploitation (commun.* est une bibliothèque, jamais lancée seule).
SCRIPTS = ('diagnostic', 'installer', 'demarrer', 'arreter', 'sauvegarder', 'restaurer', 'mettre-a-jour')
# Codes que les consignes de la tâche imposent de documenter.
CODES_IMPOSES = ('reconnexion_requise', 'journaux_lmstudio_introuvables', 'lmstudio_expose_reseau',
                 'lmstudio_dossier_introuvable', 'moteur_paquet_indisponible', 'etape_inconnue', 'modifications_locales',
                 'restauration_interrompue', 'reprise_interrompue', 'pilote_actif', 'mise_a_jour_annulee')


def lire(chemin):
    return chemin.read_text(encoding='utf-8')


def etapes_du_script_sh():
    """Étapes acceptées par installer.sh : la ligne `moteur | lmstudio | … ) ;;` du `case "$ETAPE"`."""
    trouve = re.search(r'^\s+((?:[a-z0-9-]+ \| )+[a-z0-9-]+)\) ;;$', lire(INSTALL / 'installer.sh'), re.M)
    assert trouve, 'liste des étapes introuvable dans installer.sh'
    return tuple(trouve.group(1).split(' | '))


def etapes_du_script_ps1():
    """Étapes acceptées par installer.ps1 : le tableau de `-cin @(…)` du `switch` de principal."""
    trouve = re.search(r"\$_ -cin @\(([^)]*)\)", lire(INSTALL / 'installer.ps1'))
    assert trouve, 'liste des étapes introuvable dans installer.ps1'
    return tuple(re.findall(r"'([a-z0-9-]+)'", trouve.group(1)))


def sections(texte):
    """Sections `## titre` d'un document : liste de (titre, corps), dans l'ordre."""
    morceaux = re.split(r'^## (.+)$', texte, flags=re.M)
    return list(zip(morceaux[1::2], morceaux[2::2]))


def a_plat(texte):
    """Texte sur une seule ligne : les phrases des documents sont coupées à 120 colonnes."""
    return ' '.join(texte.split())


def section(texte, debut):
    for titre, corps in sections(texte):
        if titre.startswith(debut):
            return corps
    raise AssertionError(f'section « {debut} » absente')


def lignes_de_commande(texte):
    """Lignes qui citent un script d'installation, avec les options de cette ligne."""
    for ligne in texte.splitlines():
        for script in re.finditer(r'install[/\\]([A-Za-z0-9-]+)\.(sh|ps1)', ligne):
            yield ligne, script.group(1), script.group(2)


# Dossiers d'état d'éditeurs et d'assistants IA : créés dans le dépôt par l'outil qui l'ouvre, ils ne doivent pas bloquer
# install/mettre-a-jour.sh (qui refuse de démarrer sur un dépôt modifié). Un fichier qui guide l'assistant reste suivi.
IGNORES = ('.claude/settings.local.json', '.codex/config.toml', '.cursor/rules/regle.mdc', '.vscode/settings.json',
           '.idea/workspace.xml', '.continue/config.json', '.aider.chat.history.md', '.aider.tags.cache.v3/cache.db')
SUIVIS = ('AGENTS.md', 'CLAUDE.md', 'README.md', '.gitattributes', '.gitignore', '.github/copilot-instructions.md',
          '.github/workflows/ci.yml', 'install/installer.sh')


def git(*arguments, cwd=RACINE):
    # Sans le fichier d'exclusions personnel de la machine qui lance les tests : seul le .gitignore du dépôt compte.
    return subprocess.run(['git', '-c', f'core.excludesFile={os.devnull}', *arguments], cwd=cwd, capture_output=True,
                          text=True, timeout=60)


class SourcesDeReferenceTests(unittest.TestCase):
    """Garde-fou : si l'extraction des listes échouait en silence, les tests suivants passeraient pour rien."""

    def test_les_deux_installateurs_acceptent_les_memes_etapes_dans_l_ordre_de_la_specification(self):
        self.assertEqual(etapes_du_script_sh(), ETAPES)
        self.assertEqual(etapes_du_script_ps1(), ETAPES)


class GitignoreTests(unittest.TestCase):
    def test_les_dossiers_d_etat_des_editeurs_et_assistants_sont_ignores(self):
        for chemin in IGNORES:
            with self.subTest(chemin=chemin):
                self.assertEqual(git('check-ignore', '--no-index', '-q', chemin).returncode, 0)

    def test_les_fichiers_du_depot_qui_guident_l_assistant_ne_sont_pas_ignores(self):
        for chemin in SUIVIS:
            with self.subTest(chemin=chemin):
                self.assertEqual(git('check-ignore', '--no-index', '-q', chemin).returncode, 1)

    def test_agents_et_claude_sont_suivis(self):
        for chemin in ('AGENTS.md', 'CLAUDE.md'):
            with self.subTest(chemin=chemin):
                self.assertEqual(git('ls-files', '--error-unmatch', chemin).returncode, 0, 'à ajouter à git')

    def test_ces_dossiers_ne_rendent_pas_le_depot_modifie_mais_un_autre_fichier_oui(self):
        # La condition de refus de mettre-a-jour.sh : `git status --porcelain` non vide.
        with tempfile.TemporaryDirectory() as dossier:
            depot = Path(dossier)
            self.assertEqual(git('init', '-q', cwd=depot).returncode, 0)
            shutil.copy2(RACINE / '.gitignore', depot / '.gitignore')
            self.assertEqual(git('add', '.gitignore', cwd=depot).returncode, 0)
            for chemin in IGNORES:
                (depot / chemin).parent.mkdir(parents=True, exist_ok=True)
                (depot / chemin).write_text('essai', encoding='utf-8')
            self.assertEqual(git('status', '--porcelain', cwd=depot).stdout.strip(), 'A  .gitignore')
            (depot / 'notes.txt').write_text('essai', encoding='utf-8')
            self.assertIn('?? notes.txt', git('status', '--porcelain', cwd=depot).stdout)


class DocumentsPresentsTests(unittest.TestCase):
    def test_les_quatre_fichiers_existent(self):
        for chemin in (AGENTS, README, CLAUDE, GITATTRIBUTES):
            with self.subTest(chemin=chemin.name):
                self.assertTrue(chemin.is_file())

    def test_claude_importe_agents(self):
        lignes = [ligne.strip() for ligne in lire(CLAUDE).splitlines() if ligne.strip()]
        self.assertIn('@AGENTS.md', lignes)  # syntaxe d'import de Claude Code : seule sur sa ligne
        self.assertLessEqual(len(lignes), 3, 'CLAUDE.md renvoie à AGENTS.md et ne dit rien d\'autre qui puisse le contredire')

    def test_gitattributes_garde_lf_et_binaire_pour_la_roue(self):
        lignes = [ligne.strip() for ligne in lire(GITATTRIBUTES).splitlines() if ligne.strip() and not ligne.startswith('#')]
        self.assertIn('* text=auto eol=lf', lignes)
        self.assertIn('*.whl binary', lignes)


class EtapesCiteesTests(unittest.TestCase):
    def test_chaque_etape_citee_existe_dans_les_deux_installateurs(self):
        acceptees = set(etapes_du_script_sh()) & set(etapes_du_script_ps1())
        for chemin in (AGENTS, README):
            citees = re.findall(r'--etape ([A-Za-z0-9_-]+)', lire(chemin))
            self.assertTrue(citees, chemin.name)
            for nom in citees:
                with self.subTest(document=chemin.name, etape=nom):
                    self.assertIn(nom, acceptees)

    def test_chaque_etape_est_decrite_avec_sa_commande_sh_et_powershell(self):
        for chemin in (AGENTS, README):
            texte = lire(chemin)
            for nom in ETAPES:
                with self.subTest(document=chemin.name, etape=nom):
                    self.assertRegex(texte, rf'install/installer\.sh --etape {re.escape(nom)}(?![A-Za-z0-9-])')
                    self.assertRegex(texte, rf'install\\installer\.ps1 --etape {re.escape(nom)}(?![A-Za-z0-9-])')

    def test_les_etapes_sont_dans_l_ordre_du_deroule(self):
        for chemin in (AGENTS, README):
            citees = re.findall(r'install/installer\.sh --etape ([a-z0-9-]+)', lire(chemin))
            premieres = list(dict.fromkeys(citees))
            self.assertEqual([nom for nom in premieres if nom in ETAPES][:len(ETAPES)], list(ETAPES), chemin.name)


class ScriptsCitesTests(unittest.TestCase):
    def test_chaque_script_cite_existe_dans_sa_version(self):
        for chemin in (AGENTS, README):
            citees = list(lignes_de_commande(lire(chemin)))
            self.assertTrue(citees, chemin.name)
            for _, nom, extension in citees:
                with self.subTest(document=chemin.name, script=f'{nom}.{extension}'):
                    self.assertTrue((INSTALL / f'{nom}.{extension}').is_file())

    def test_chaque_script_d_exploitation_est_cite_dans_les_deux_versions(self):
        for chemin in (AGENTS, README):
            citees = {(nom, extension) for _, nom, extension in lignes_de_commande(lire(chemin))}
            for nom in SCRIPTS:
                for extension in ('sh', 'ps1'):
                    with self.subTest(document=chemin.name, script=f'{nom}.{extension}'):
                        self.assertIn((nom, extension), citees)

    def test_chaque_option_citee_avec_un_script_existe_dans_ce_script(self):
        for chemin in (AGENTS, README):
            for ligne, nom, extension in lignes_de_commande(lire(chemin)):
                source = lire(INSTALL / f'{nom}.{extension}')
                for option in re.findall(r'(?<![\w-])(--[a-z]+(?:-[a-z]+)*)', ligne):
                    with self.subTest(document=chemin.name, script=f'{nom}.{extension}', option=option):
                        self.assertIn(f"'{option}'" if extension == 'ps1' else f'{option}', source)

    def test_chaque_commande_outils_citee_existe(self):
        parseur = outils.construire_parseur()
        for chemin in (AGENTS, README):
            citees = re.findall(r'python -m outils ([a-z0-9-]+)', lire(chemin))
            for commande in citees:
                with self.subTest(document=chemin.name, commande=commande):
                    self.assertIn(commande, outils.COMMANDES)
        reprise = [ligne for ligne in lire(AGENTS).splitlines() if 'outils reprendre-pilote' in ligne]
        self.assertTrue(reprise)
        for ligne in reprise:  # les options de `outils` suivent son nom ; celles de `compose run` le précèdent
            for option in re.findall(r'(?<![\w-])(--[a-z]+(?:-[a-z]+)*)', ligne.split('reprendre-pilote', 1)[1]):
                with self.subTest(option=option):
                    self.assertTrue(parseur.parse_args(['reprendre-pilote', '/pilote', option]).forcer)

    def test_options_des_scripts_exclues_du_poste_de_travail_reel(self):
        # --strict est réservé à la fausse IA de la CI : l'installation réelle ne le passe jamais.
        for chemin in (AGENTS, README):
            for ligne in lire(chemin).splitlines():
                if '--strict' in ligne:
                    with self.subTest(document=chemin.name, ligne=ligne):
                        self.assertRegex(ligne.lower(), r'jamais|ne pas|sans')


class ReglesAbsoluesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texte = lire(AGENTS)

    def test_les_regles_absolues_viennent_avant_tout_le_reste(self):
        titres = [titre for titre, _ in sections(self.texte)]
        self.assertTrue(titres[0].startswith('Règles absolues'), titres)

    def test_les_interdictions_citent_territoria_donnees_et_les_autres_sources_sensibles(self):
        regles = a_plat(section(self.texte, 'Règles absolues'))
        for terme in ('territoria-donnees', 'server-logs', 'compose logs', 'n8n-admin.txt', 'sauvegarde.motdepasse',
                      'docker inspect', '127.0.0.1:8878', 'data/'):
            with self.subTest(terme=terme):
                self.assertIn(terme, regles)

    def test_les_regles_du_paragraphe_4_3_de_la_specification(self):
        regles = a_plat(section(self.texte, 'Règles absolues')).lower()
        for fragment in ('accord explicite', 'élévation de droits', 'jamais de secret', 'saisis par l\'utilisateur',
                         'masquée', 'echec', 'dernière ligne', 'rapport'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, regles)


class CodesDEchecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texte = lire(AGENTS)
        cls.sources = '\n'.join(lire(p) for p in sorted(INSTALL.glob('*.sh')) + sorted(INSTALL.glob('*.ps1')))
        cls.sources += '\n' + lire(RACINE / 'app' / 'outils.py') + '\n' + lire(RACINE / 'app' / 'e2e.py')

    def codes_du_tableau(self):
        corps = section(self.texte, 'Codes d\'échec')
        codes = []
        for ligne in corps.splitlines():
            if ligne.startswith('| `'):
                codes.extend(re.findall(r'`([a-z][a-z0-9_]*)`', ligne.split('|')[1]))
        return codes

    def test_les_codes_imposes_figurent_au_tableau(self):
        codes = self.codes_du_tableau()
        for code in CODES_IMPOSES:
            with self.subTest(code=code):
                self.assertIn(code, codes)

    def test_chaque_code_du_tableau_est_produit_par_le_code(self):
        codes = self.codes_du_tableau()
        self.assertGreater(len(codes), 20)
        for code in codes:
            with self.subTest(code=code):
                self.assertRegex(self.sources, rf'(?<![a-z_]){re.escape(code)}(?![a-z_])')


class ContenuImposeTests(unittest.TestCase):
    """Points de contrôle des revues précédentes : chacun est une phrase précise, pas un mot courant."""

    @classmethod
    def setUpClass(cls):
        cls.agents = lire(AGENTS)
        cls.readme = lire(README)

    def test_verifier_liste_les_codes_reellement_emis_et_ne_relance_pas_les_controles_de_securite(self):
        # outils.py garde seulement [a-z_] du nom du contrôle : `recherche_page2` est émis `recherche_page`.
        emis = [re.sub(r'[^a-z_]', '', nom) for nom, _ in e2e.CONTROLES]
        etape = a_plat(self.agents.split('### Étape 8')[1].split('### Étape 9')[0])
        for code in emis:
            with self.subTest(code=code):
                self.assertRegex(etape, rf'`{code}`')
        self.assertNotIn('recherche_page2', self.agents)
        # Seuls reponse_sourcee et arrete_word se relancent (une fois) ; les contrôles de sécurité et le nettoyage, jamais.
        lignes = {ligne.split('|')[1].strip(): ligne for ligne in section(self.agents, 'Codes d\'échec').splitlines()
                  if ligne.startswith('| `')}
        for premiere in lignes:
            if '`origine_tierce_refusee`' in premiere:
                ligne = lignes[premiere]
                for code in ('origine_tierce_refusee', 'interne_sans_jeton_refuse', 'nettoyage'):
                    self.assertIn(f'`{code}`', premiere)
                self.assertIn('immédiatement', ligne)
                self.assertIn('ne relancez jamais', ligne)
                break
        else:
            self.fail('ligne des contrôles de sécurité absente du tableau')
        for premiere, ligne in lignes.items():
            if '`reponse_sourcee`' in premiere:
                self.assertIn('une seule fois', ligne)
                self.assertNotIn('`nettoyage`', premiere)
        self.assertIn('Ne relancez **jamais**', etape)
        self.assertIn('rendez compte immédiatement', etape)

    def test_tableau_avec_une_ligne_pour_tout_autre_code(self):
        corps = section(self.agents, 'Codes d\'échec')
        self.assertRegex(corps, r'(?m)^\| tout autre code \|')

    def test_la_ligne_rapport_est_decrite_comme_nombres_et_oui_non(self):
        # RAPPORT porte aussi `reference_conforme=oui|non` : « que des nombres » seul serait faux.
        for texte in (self.agents, self.readme):
            plat = a_plat(texte)
            self.assertNotRegex(plat, r'que des nombres(?! et)')
            self.assertNotIn('nombres seulement', plat)
            self.assertIn('`oui`/`non`', plat)

    def test_les_suppressions_n_utilisent_pas_de_chemin_relatif_depuis_le_depot(self):
        # territoria-donnees/ est à côté du dépôt : `rm territoria-donnees/…` lancé depuis la racine du dépôt échoue.
        for nom, texte in (('AGENTS.md', self.agents), ('README.md', self.readme)):
            for commande in re.findall(r'`[^`\n]*`', texte):
                if re.search(r'\brm\b|Remove-Item', commande):
                    with self.subTest(document=nom, commande=commande):
                        self.assertNotRegex(commande, r'(?<![./\\])territoria-donnees')
            plat = a_plat(texte)
            self.assertIn('<chemin affiché>', plat)
            self.assertIn('suffixe `-2`', plat)
            self.assertIn('../territoria-donnees/echec-', plat)
            self.assertIn('../territoria-donnees/mises-a-jour/', plat)

    def test_la_reprise_de_mise_a_jour_interrompue_est_lancee_par_l_utilisateur(self):
        corps = a_plat(section(self.agents, 'Mise à jour'))
        self.assertIn("C'est l'utilisateur qui les lance, une à une, dans son terminal", corps)
        self.assertNotIn("ne les lancez qu'avec l'accord", corps)

    def test_mises_a_jour_les_dossiers_d_assistant_sont_ignores_et_le_dit(self):
        for texte in (self.agents, self.readme):
            plat = a_plat(texte)
            for dossier in ('.claude/', '.codex/', '.cursor/', '.vscode/', '.idea/', '.continue/', '.aider*'):
                self.assertIn(f'`{dossier}`', plat)
            self.assertIn('.gitignore', plat)
        self.assertNotIn('.git/info/exclude', self.agents)  # plus de contournement après coup pour ces dossiers

    def test_avertissement_de_licence_docker_desktop_produit_par_le_diagnostic(self):
        ligne = 'avertissement=licence_docker_desktop_payante_pour_les_administrations'
        self.assertIn(ligne, self.agents)
        for script in ('diagnostic.sh', 'diagnostic.ps1'):
            self.assertIn(ligne, lire(INSTALL / script))
        paragraphe = next(bloc for bloc in self.agents.split('\n\n') if ligne in bloc)
        self.assertIn('Podman', paragraphe)  # l'avertissement et la recommandation vont ensemble

    def test_groupe_docker_annonce_avant_l_etape_moteur(self):
        corps = a_plat(section(self.agents, 'Déroulé'))
        self.assertIn('groupe `docker`', corps)
        self.assertIn('droits équivalents à ceux d\'administrateur', corps)

    def test_verifier_sans_strict_et_demo_seulement_sans_bibliotheque_reelle(self):
        corps = a_plat(section(self.agents, 'Déroulé'))
        self.assertIn('--strict', corps)
        self.assertIn('bibliotheque_etrangere', corps)

    def test_windows_blocage_des_scripts_sans_desactiver_la_securite(self):
        for texte in (self.agents, self.readme):
            self.assertIn('Unblock-File', texte)
            self.assertIn('-ExecutionPolicy Bypass', texte)
            self.assertIn('Zone.Identifier', texte)
            self.assertIn('Set-ExecutionPolicy', texte)  # cité pour être déconseillé
            self.assertRegex(texte, r'(?i)jamais[^\n]*Set-ExecutionPolicy|Set-ExecutionPolicy[^\n]*jamais')

    def test_reprise_du_pilote(self):
        corps = a_plat(section(self.agents, 'Reprise du pilote'))
        for fragment in ('Mac du porteur de projet', 'stop territoria', 'python -m outils reprendre-pilote /pilote',
                         ':/pilote:ro', 'RAPPORT', 'pilote_actif', '-wal', '-journal', '--forcer', 'sauvegard',
                         'reprise_interrompue', '.reprise-', 'install/demarrer.sh', 'territoria-donnees/territoria/'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, corps)
        self.assertNotIn('--user', corps, 'l\'import écrit dans /donnees avec l\'identifiant du service (10001), pas en root')

    def test_la_commande_de_reprise_est_la_meme_dans_le_readme(self):
        for fragment in ('python -m outils reprendre-pilote /pilote', ':/pilote:ro', 'stop territoria'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.readme)

    def test_mise_a_jour(self):
        corps = a_plat(section(self.agents, 'Mise à jour'))
        for fragment in ('--version', 'origin/', 'territoria-donnees/mises-a-jour/', 'echec-', 'sudo rm -rf',
                         'restauration_interrompue', 'importe dans n8n les credentials et les workflows',
                         'rien à relancer après une mise à jour', 'modifications_locales', 'mise_a_jour_annulee',
                         'Mise à jour annulée', 'une trentaine de minutes', 'chemins complets déjà protégés'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, corps)
        # La mise à jour importe elle-même les workflows : plus aucune consigne de relancer l'étape n8n après elle.
        for fragment in ('--etape n8n', 'git diff --stat'):
            with self.subTest(absent=fragment):
                self.assertNotIn(fragment, corps)
                self.assertNotIn(fragment, a_plat(section(self.readme, 'Mise à jour')))

    def test_verifier_long_lance_en_arriere_plan_et_jamais_conclu_sur_un_delai_d_outil(self):
        # Jusqu'à 16 min par demande au modèle, rejouée au plus une fois : l'assistant ne doit pas conclure à un échec
        # parce que son propre outil a coupé la commande.
        for nom, texte in (('AGENTS.md', a_plat(self.agents)), ('README.md', a_plat(self.readme))):
            with self.subTest(document=nom):
                self.assertIn('trentaine de minutes', texte)
                self.assertIn('arrière-plan', texte)
                self.assertIn('ETAPE', texte)
        self.assertIn('Ne concluez jamais à un échec parce que votre outil a dépassé son propre délai', a_plat(self.agents))

    def test_les_tests_et_les_documents_de_reference_sont_indiques_dans_le_readme(self):
        for fragment in ('PYTHONPATH=app', 'unittest discover -s tests', 'docs/acces-lmstudio.md', 'AGENTS.md'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.readme)
        self.assertTrue((RACINE / 'docs/acces-lmstudio.md').is_file())
        self.assertNotIn('docs/superpowers', self.readme)  # documents de conception : ils ne font pas partie de la distribution
        for dossier in ('app/', 'conteneurs/', 'install/', 'n8n/workflows/', 'tests/', 'docs/', 'vendor/'):
            with self.subTest(dossier=dossier):
                self.assertIn(dossier, self.readme)
                self.assertTrue((RACINE / dossier).is_dir(), dossier)

    def test_server_logs_n_est_jamais_cree_avec_mkdir_p_ni_force(self):
        # `mkdir -p` ou `-Force` créeraient le dossier de LM Studio lui-même s'il n'existe pas : l'étape lmstudio le
        # prendrait alors pour le vrai, écrirait ses réglages là où LM Studio ne les lit pas et ne contrôlerait aucun journal.
        for nom, texte in (('AGENTS.md', self.agents), ('README.md', self.readme)):
            blocs = [bloc for bloc in re.split(r'\n\s*\n', texte) if 'server-logs' in bloc]
            self.assertTrue(blocs, nom)
            for bloc in blocs:
                with self.subTest(document=nom, debut=bloc[:40]):
                    self.assertNotIn('mkdir -p', bloc)
                    self.assertNotIn('-Force', bloc)
        for texte in (self.agents, self.readme):
            self.assertIn('Ne créez jamais le dossier de LM Studio lui-même', ' '.join(texte.split()))

    def test_les_exemples_de_mise_a_jour_citent_la_version_courante(self):
        source = (RACINE / 'app' / 'territoria' / '__init__.py').read_text(encoding='utf-8')
        version = re.search(r"^VERSION = '([^']+)'", source, re.M).group(1)
        for nom, texte in (('AGENTS.md', self.agents), ('README.md', self.readme)):
            with self.subTest(document=nom):
                self.assertEqual(set(re.findall(r'\bv\d+\.\d+\.\d+\b', texte)), {'v' + version})

    def test_le_guide_nomme_les_prerequis_que_l_installation_exige(self):
        guide = (RACINE / 'docs' / 'guide-debutant.md').read_text(encoding='utf-8')
        self.assertIn('homebrew_absent', (RACINE / 'install' / 'installer.sh').read_text(encoding='utf-8'))
        installateur = (RACINE / 'install' / 'installer.sh').read_text(encoding='utf-8')
        self.assertIn('phase compose_absent', installateur)
        # Tant que l'installation peut s'arrêter sur ces codes, le guide dit quoi installer et comment repartir.
        for fragment in ('Homebrew', '`homebrew_absent`', '`winget_absent`', '`compose_absent`'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, guide)

    def test_aucun_chemin_personnel_ni_secret_dans_les_documents(self):
        for texte in (self.agents, self.readme):
            self.assertNotRegex(texte, r'/Users/[a-z]')  # dossier personnel d'un Mac ; les exemples s'écrivent avec ~ ou <vous>
            self.assertNotRegex(texte, r'(?i)\b(?:password|secret|token)\s*=\s*\S+')


    def test_aucune_trace_du_poste_ni_du_compte_d_hebergement_dans_docs(self):
        # Le dépôt devient public : ni chemin du poste de l'auteur, ni identifiant du compte d'hébergement.
        for chemin in sorted((RACINE / 'docs').rglob('*.md')):
            texte = chemin.read_text(encoding='utf-8')
            self.assertNotIn('/Users/', texte, chemin.name)
            # Identifiant de compte d'un hébergement mutualisé : quatre lettres puis quatre chiffres, en majuscules ou non,
            # seul ou en préfixe d'un nom de base (« xxxx0000_base »).
            self.assertNotRegex(texte, r'(?i)(?<![a-z0-9])[a-z]{4}\d{4}(?![a-z0-9])', chemin.name)

if __name__ == '__main__':
    unittest.main()
