# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import re
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
GUIDE = RACINE / 'docs' / 'guide-debutant.md'


def etapes_sh() -> set[str]:
    return set(re.findall(r'^\s+([a-z0-9-]+)\)\s*etape_', (RACINE / 'install/installer.sh').read_text(encoding='utf-8'), re.M))


def etapes_ps1() -> set[str]:
    texte = (RACINE / 'install/installer.ps1').read_text(encoding='utf-8-sig')
    return set(re.findall(r"'([a-z0-9-]+)'", re.search(r'-cin @\(([^)]*)\)', texte).group(1)))


class GuideDebutantTests(unittest.TestCase):
    """Le guide ne doit pas dériver des scripts : chaque commande citée existe, et aucune étape n'est oubliée."""

    def setUp(self):
        self.guide = GUIDE.read_text(encoding='utf-8')

    def test_les_deux_scripts_ont_les_memes_etapes(self):
        self.assertEqual(etapes_sh(), etapes_ps1())

    def test_chaque_etape_existe_et_toutes_sont_decrites(self):
        citees = set(re.findall(r'--etape ([a-z0-9-]+)', self.guide))
        self.assertEqual(citees, etapes_sh())

    def test_chaque_etape_a_sa_commande_windows_et_macos(self):
        for etape in etapes_sh():
            self.assertIn(f'install/installer.sh --etape {etape}', self.guide)
            self.assertIn(f'install\\installer.ps1 --etape {etape}', self.guide)

    def test_scripts_cites_existent(self):
        scripts = set(re.findall(r'install[/\\]([a-z-]+\.(?:sh|ps1))', self.guide))
        self.assertTrue(scripts)
        for script in scripts:
            self.assertTrue((RACINE / 'install' / script).is_file(), script)

    def test_codes_et_cles_cites_existent(self):
        # Codes d'échec et clés du diagnostic : ceux des scripts, ou du tableau « Codes d'échec » d'AGENTS.md.
        reference = ''.join(f.read_text(encoding='utf-8-sig') for f in sorted((RACINE / 'install').iterdir())
                            if f.suffix in ('.sh', '.ps1'))
        reference += (RACINE / 'AGENTS.md').read_text(encoding='utf-8')
        codes = set(re.findall(r'`([a-z]+(?:_[a-z0-9]+)+)`', self.guide))
        self.assertTrue(codes)
        for code in codes:
            self.assertIn(code, reference, code)

    def section(self, titre: str) -> str:
        debut = self.guide.index(titre)
        fin = re.compile(r'^#{2,3} ', re.M).search(self.guide, debut + 1)
        return self.guide[debut:fin.start() if fin else None]

    def test_etape_4_la_reponse_non_coupe_tous_les_connecteurs(self):
        # « n » enregistre le mode hors ligne, qui coupe Légifrance et data.gouv.fr : Administration › Connecteurs ne peut
        # alors plus rien activer. Le guide le dit, avec le moyen d'en sortir (relancer l'étape et répondre « o »).
        sh = (RACINE / 'install/installer.sh').read_text(encoding='utf-8')
        ps1 = (RACINE / 'install/installer.ps1').read_text(encoding='utf-8-sig')
        self.assertRegex(sh, r'if ! repondu_oui "\$reponse"; then\s+ecrire_variable "\$S/compose.env" TERRITORIA_HORS_LIGNE 1')
        self.assertRegex(ps1, r"if \(-not \(repondu_oui \$reponse\)\) \{\s+ecrire_variable \$composeEnv 'TERRITORIA_HORS_LIGNE' '1'")
        etape = self.section('### Étape 4.')
        for attendu in ('mode hors ligne', 'data.gouv.fr', 'relancez cette étape'):
            self.assertIn(attendu, etape)

    def test_legifrance_s_active_dans_l_administration(self):
        # Seul le connecteur activé dans Administration › Connecteurs, avec ses identifiants PISTE, consulte Légifrance
        # (legal_sources.refresh, connecteurs.environnement_legifrance) ; data.gouv.fr s'y active aussi, sans compte.
        app = (RACINE / 'app/public/app.js').read_text(encoding='utf-8')
        for libelle in ('Activer le connecteur Légifrance', 'Activer le connecteur data.gouv.fr'):
            self.assertIn(libelle, app)
            self.assertIn(libelle, self.section('## 6.'))
        self.assertIn('Activer le connecteur Légifrance', self.section('### Étape 4.'))

    def test_etape_4_pose_la_question_des_scripts_sans_saisie_piste(self):
        # L'étape 4 ne règle plus que le mode hors ligne : la même question dans les deux scripts, le guide, le README et
        # AGENTS.md ; l'ancienne question, qui demandait les identifiants PISTE, a disparu partout.
        question = 'Autoriser les connecteurs publics (Légifrance, data.gouv.fr)'
        ancienne = "Configurer l'accès à Légifrance par PISTE"
        documents = {'installer.sh': (RACINE / 'install/installer.sh').read_text(encoding='utf-8'),
                     'installer.ps1': (RACINE / 'install/installer.ps1').read_text(encoding='utf-8-sig'),
                     'guide, étape 4': self.section('### Étape 4.'),
                     'README': (RACINE / 'README.md').read_text(encoding='utf-8'),
                     'AGENTS.md': (RACINE / 'AGENTS.md').read_text(encoding='utf-8')}
        for nom, texte in documents.items():
            with self.subTest(document=nom):
                self.assertIn(question, texte)
                self.assertNotIn(ancienne, texte)
        self.assertNotIn('collez-les', self.section('### Étape 4.'))


class GuideDemonstrationTests(unittest.TestCase):
    """docs/demonstration.md (serveur de démonstration) : commandes, options et étapes citées existent."""

    def test_guide_demonstration_cite_des_commandes_existantes(self):
        guide = (RACINE / 'docs' / 'demonstration.md').read_text(encoding='utf-8')
        script = (RACINE / 'install' / 'demonstration.sh').read_text(encoding='utf-8')
        for option in ('--domaine', '--premier-administrateur', '--nom'):
            self.assertIn(f'install/demonstration.sh {option}' if option != '--nom' else option, guide)
            self.assertIn(f'{option})', script)
        self.assertTrue(set(re.findall(r'--etape ([a-z0-9-]+)', guide)) <= etapes_sh())
        for script_cite in set(re.findall(r'install/([a-z-]+\.sh)', guide)):
            self.assertTrue((RACINE / 'install' / script_cite).is_file(), script_cite)
        for mention in ('plafond', 'données fictives', 'OpenRouter', 'Administration › Intelligence artificielle'):
            self.assertIn(mention, guide)


class GuideMistralTests(unittest.TestCase):
    """docs/mistral.md : ce qu'un DPO doit savoir avant que la collectivité choisisse l'API de Mistral."""

    def test_guide_mistral(self):
        guide = (RACINE / 'docs' / 'mistral.md').read_text(encoding='utf-8')
        for attendu in ('--etape mistral', '--etape lmstudio', 'registre', 'DPO', 'contrat de sous-traitance',
                        'zéro conservation', 'https://api.mistral.ai/v1', 'Ne partent jamais'):
            self.assertIn(attendu, guide)
        self.assertTrue(set(re.findall(r'--etape ([a-z0-9-]+)', guide)) <= etapes_sh())

    def test_promesse_du_readme(self):
        readme = (RACINE / 'README.md').read_text(encoding='utf-8')
        self.assertIn("100 % local par défaut. Option : l'API de Mistral, choisie à l'installation, en connaissance de cause.", readme)


if __name__ == '__main__':
    unittest.main()
