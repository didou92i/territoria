# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Dossier de publication : PDF anonymisés, index des actes publiés, CSV national des délibérations (SCDL 2.1.6)."""
import csv
import io
import sys
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from pypdf import PdfReader

from territoria import publication_dossier as dossier



def aujourdhui():
    """Le jour de l'application (heure de Paris), lu au moment de s'en servir. L'horloge de la machine donne la veille ou le
    lendemain une partie de la journée dès qu'elle est réglée sur un autre fuseau, comme en intégration continue."""
    return dossier.jour_courant().isoformat()
PROFIL = {'collectivite': 'Commune de Démoville', 'siret': '21860194600013', 'adresse_actes': 'https://www.demoville.fr/actes/'}
EN_TETE = ('COLL_NOM,COLL_SIRET,DELIB_ID,DELIB_DATE,DELIB_MATIERE_CODE,DELIB_MATIERE_NOM,DELIB_OBJET,BUDGET_ANNEE,BUDGET_NOM,'
           'PREF_ID,PREF_DATE,VOTE_EFFECTIF,VOTE_REEL,VOTE_POUR,VOTE_CONTRE,VOTE_ABSTENTION,DELIB_URL')


def acte(**valeurs):
    return {'id': 'a' * 32, 'type': 'deliberation', 'numero': 'D2026/012', 'date_acte': '2026-10-14',
            'objet': 'Cession d’un terrain à M. Jean Martin', 'matiere_code': '3.2', 'statut': 'valide',
            'votes': {'effectif': 19, 'reel': 18, 'pour': 15, 'contre': 2, 'abstention': 1},
            'paragraphes': ['Le conseil municipal,', 'Vu la demande de M. Jean Martin, 12 rue des Lilas ;', 'DÉCIDE'],
            'passages': [{'texte': 'Jean Martin', 'categorie': 'nom', 'origine': 'ia', 'coche': True},
                         {'texte': '12 rue des Lilas', 'categorie': 'adresse', 'origine': 'regle', 'coche': True}]} | valeurs


def texte_pdf(octets):
    return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(octets)).pages)


class DossierTests(unittest.TestCase):
    def test_nom_fichier(self):
        self.assertEqual(dossier.nom_fichier('D2026/012'), 'D2026-012.pdf')
        self.assertEqual(dossier.nom_fichier('Arrêté n° 42'), 'Arr-t--n--42.pdf')

    def test_pdf_texte_masque_et_mention(self):
        a = acte()
        octets = dossier.pdf(a, dossier.paragraphes_masques(a), PROFIL['collectivite'], '2026-11-03')
        texte = texte_pdf(octets)
        self.assertIn('[nom masqué]', texte)
        self.assertIn('[adresse masquée]', texte)
        self.assertNotIn('Martin', texte)
        self.assertNotIn('Lilas', texte)
        self.assertIn('Acte publié sous forme électronique le 03/11/2026 — document anonymisé (RGPD)', ' '.join(texte.split()))
        self.assertIn('D2026/012', texte)

    def test_pdf_caracteres_typographiques(self):
        # Review Focus 4.
        a = acte(passages=[], paragraphes=['Une œuvre pour l’école, « gratuite », 15 € ; émoji ☺ remplacé.'])
        texte = texte_pdf(dossier.pdf(a, a['paragraphes'], 'Commune', '2026-11-03'))
        for morceau in ('œuvre', 'l’école', '« gratuite »', '15 €'):
            self.assertIn(morceau, texte)

    def test_index_echappe_le_html(self):
        # Review Focus 3.
        html = dossier.index_html([acte(objet='Travaux <script>alert(1)</script> & réseaux', mise_en_ligne='2026-11-03')],
                                  PROFIL['adresse_actes'], PROFIL['collectivite'])
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&amp; réseaux', html)
        self.assertIn('https://www.demoville.fr/actes/D2026-012.pdf', html)

    def test_index_sans_script(self):
        html = dossier.index_html([acte(mise_en_ligne='2026-11-03')], '', 'Commune')
        self.assertNotIn('<script', html.lower())
        self.assertNotIn('http', html.lower().replace('http-equiv', ''))  # aucune ressource extérieure, lien relatif
        self.assertIn('href="D2026-012.pdf"', html)

    def test_csv_colonnes_et_formats(self):
        arrete = acte(id='b' * 32, type='arrete', numero='A2026-001', matiere_code='', votes={})
        donnees = dossier.csv_deliberations([acte(), arrete], PROFIL['collectivite'], PROFIL['siret'], PROFIL['adresse_actes'])
        self.assertFalse(donnees.startswith(b'\xef\xbb\xbf'))
        lignes = list(csv.DictReader(io.StringIO(donnees.decode('utf-8'))))
        self.assertEqual(donnees.decode('utf-8').splitlines()[0], EN_TETE)
        self.assertEqual(len(lignes), 1)  # les arrêtés n'entrent pas dans le CSV des délibérations
        ligne = lignes[0]
        self.assertEqual((ligne['COLL_SIRET'], ligne['DELIB_ID'], ligne['DELIB_DATE']), ('21860194600013', 'D2026/012', '2026-10-14'))
        self.assertEqual((ligne['DELIB_MATIERE_CODE'], ligne['DELIB_MATIERE_NOM']), ('3.2', 'domaine et patrimoine/aliénations'))
        self.assertEqual(ligne['DELIB_OBJET'], 'Cession d’un terrain à M. [nom masqué]')
        self.assertEqual((ligne['VOTE_POUR'], ligne['VOTE_CONTRE'], ligne['VOTE_ABSTENTION']), ('15', '2', '1'))
        self.assertEqual(ligne['DELIB_URL'], 'https://www.demoville.fr/actes/D2026-012.pdf')
        self.assertEqual(ligne['BUDGET_ANNEE'], '')

    def test_dossier_contenu(self):
        deja = acte(id='c' * 32, numero='D2026/001', statut='publie', publie_le='2026-10-01')
        archive, empreintes = dossier.dossier([acte()], [deja], PROFIL, aujourdhui())
        noms = zipfile.ZipFile(io.BytesIO(archive)).namelist()
        self.assertEqual(sorted(noms), ['D2026-012.pdf', 'LISEZ-MOI.txt', 'deliberations.csv', 'index.html'])
        self.assertEqual(list(empreintes), ['a' * 32])
        self.assertEqual(len(empreintes['a' * 32]['empreinte']), 64)
        contenu = zipfile.ZipFile(io.BytesIO(archive))
        self.assertIn('D2026/001', contenu.read('index.html').decode())  # les actes déjà publiés restent dans l'index
        self.assertEqual(len(contenu.read('deliberations.csv').decode().splitlines()), 3)

    def test_les_essais_prennent_le_jour_a_l_horloge_de_l_application(self):
        # Sinon ils échouent une à deux heures par jour sur une machine réglée en temps universel, et à minuit partout.
        horloge_de_la_machine = 'date.' + 'today()'
        for nom in ('test_publication_dossier.py', 'test_publication_web.py'):
            with self.subTest(fichier=nom):
                self.assertNotIn(horloge_de_la_machine, (Path(__file__).parent / nom).read_text(encoding='utf-8'))

    def test_dossier_sans_siret_avec_deliberation_refuse(self):
        with self.assertRaises(ValueError) as erreur:
            dossier.dossier([acte()], [], PROFIL | {'siret': ''}, aujourdhui())
        self.assertEqual(str(erreur.exception), 'Indiquez le SIRET de la collectivité dans Administration › Collectivité.')
        dossier.dossier([acte(type='arrete', matiere_code='')], [], PROFIL | {'siret': ''}, aujourdhui())  # un arrêté seul suffit

    def test_date_passee_refusee(self):
        hier = (dossier.jour_courant() - timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError) as erreur:
            dossier.dossier([acte()], [], PROFIL, hier)
        self.assertEqual(str(erreur.exception), 'La date de mise en ligne ne peut pas être passée.')

    def test_dossier_vide_refuse(self):
        with self.assertRaises(ValueError):
            dossier.dossier([], [], PROFIL, aujourdhui())


class RelectureFinaleDossierTests(unittest.TestCase):
    def test_pdf_deterministe(self):
        # I6 : la même publication donne le même fichier, donc la même empreinte.
        a = acte()
        premier = dossier.pdf(a, dossier.paragraphes_masques(a), 'Commune', '2026-11-03')
        second = dossier.pdf(a, dossier.paragraphes_masques(a), 'Commune', '2026-11-03')
        self.assertEqual(premier, second)

    def test_pdf_espaces_fins_et_ligatures(self):
        # I5 : pas de « ? » pour une espace fine insécable ou une ligature extraite d'un PDF.
        a = acte(passages=[], paragraphes=['Article 1\u202f: école Jules\u202fFerry, modi\ufb01cation ; montant \u2265 10 €.'])
        texte = texte_pdf(dossier.pdf(a, a['paragraphes'], 'Commune', '2026-11-03'))
        self.assertNotIn('?', texte)
        self.assertIn('modification', texte)


if __name__ == '__main__':
    unittest.main()
