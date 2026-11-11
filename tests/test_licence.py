# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import subprocess
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
LICENCE = 'SPDX-License-Identifier: EUPL-1.2'
AUTEUR = 'SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)'
SUFFIXES = {'.py', '.sh', '.ps1', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.yaml', '.yml', '.Dockerfile'}
NOMS = {'Caddyfile'}
TIERS = 'app/frontend/src/vendor/'  # composants tiers : ils gardent leur propre licence


def sources_suivies() -> list[str]:
    try:
        sortie = subprocess.run(['git', 'ls-files'], cwd=RACINE, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise unittest.SkipTest('dépôt git indisponible') from None
    return [f for f in sortie.splitlines()
            if (Path(f).suffix in SUFFIXES or Path(f).name in NOMS) and not f.startswith(TIERS)]


class LicenceTests(unittest.TestCase):
    def test_chaque_source_porte_la_licence_et_l_auteur(self):
        # Traçabilité : un fichier copié seul garde son origine et sa licence (EUPL 1.2, article 5).
        sans = []
        for chemin in sources_suivies():
            tete = (RACINE / chemin).read_text(encoding='utf-8-sig').splitlines()[:6]
            if not any(LICENCE in ligne for ligne in tete) or not any(AUTEUR in ligne for ligne in tete):
                sans.append(chemin)
        self.assertEqual(sans, [])

    def test_composants_tiers_sans_en_tete(self):
        for chemin in (RACINE / TIERS).rglob('*'):
            if chemin.is_file() and chemin.suffix in SUFFIXES:
                self.assertNotIn(AUTEUR, chemin.read_text(encoding='utf-8'), chemin)

    def test_texte_officiel_de_l_eupl(self):
        # LICENSE : le texte anglais, celui que les forges reconnaissent ; EUPL-1.2-fr.txt : le texte français, de même valeur.
        anglais = (RACINE / 'LICENSE').read_text(encoding='utf-8')
        self.assertTrue(anglais.lstrip().startswith('EUROPEAN UNION PUBLIC LICENCE v. 1.2'))
        self.assertIn('European Union Public Licence (EUPL) v. 1.1, v. 1.2', anglais)
        francais = (RACINE / 'EUPL-1.2-fr.txt').read_text(encoding='utf-8')
        self.assertTrue(francais.startswith("LICENCE PUBLIQUE DE L'UNION EUROPÉENNE v. 1.2"))
        self.assertIn('Licence publique de l\'Union européenne (EUPL) v. 1.1, v. 1.2', francais)
        for nom in ('README.md', 'NOTICE.md'):  # les deux textes sont annoncés au lecteur
            with self.subTest(document=nom):
                document = (RACINE / nom).read_text(encoding='utf-8')
                self.assertIn('(LICENSE)', document)
                self.assertIn('(EUPL-1.2-fr.txt)', document)

    def test_mention_d_auteur(self):
        notice = (RACINE / 'NOTICE.md').read_text(encoding='utf-8')
        for attendu in ('TerritorIA', 'Rhiad AZZABI', 'SIREN 989 227 731', 'EUPL 1.2', 'article 5'):
            self.assertIn(attendu, notice)


if __name__ == '__main__':
    unittest.main()
