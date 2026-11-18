# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Parité entre la relecture, les enregistrements intermédiaires et les trois fichiers à publier."""
import io
import shutil
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from tests import test_publication_web as parcours
from territoria import anonymisation


class InterfacePublicationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js requis pour les gestionnaires du navigateur')
    def test_gestionnaires_interface(self):
        racine = Path(__file__).resolve().parents[1]
        run = subprocess.run(['node', '--test', 'app/frontend/tests/publication.test.cjs'], cwd=racine,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_plages_texte_unicode_espaces_casse_et_chevauchements(self):
        texte = '🌳 Petitville, petit, Petit. Éva  du Port & Fils, Léa.'
        def p(texte, coche=True, categorie='manuel'):
            return {'texte': texte, 'coche': coche, 'categorie': categorie}
        passages = [p('Petit', categorie='nom'), p('Éva du', False), p('du Port'), p('Port & Fils')]
        zones = anonymisation.zones_relecture(texte, passages)
        self.assertEqual([(texte[a:b], etat) for a, b, etat in zones],
                         [('Petit', True), ('Éva  ', False), ('du Port & Fils', True)])
        self.assertEqual(anonymisation.masquer(texte, passages),
                         '🌳 Petitville, petit, [nom masqué]. Éva  [donnée masquée], Léa.')

    def test_objet_omis_puis_masque_dans_pdf_index_et_csv(self):
        api = parcours.PublicationWebTests('test_parcours_complet_referent')
        self.addCleanup(api.doCleanups)
        api.setUp()
        objet = 'Mandat pour Kalune Testive'
        statut, acte = api.deposer(api.referent, parcours.docx('La décision vaut pour Petit et pour Petitville.'),
                                   objet=objet, numero='D-07')
        self.assertEqual(statut, 201)
        chemin = '/api/publications/' + acte['id']
        with patch.object(parcours.web.llm_backend, 'read', return_value={'provider':'mistral'}):
            statut, _ = api.appel('POST', chemin + '/reperer', {}, api.referent)
        self.assertEqual(statut, 200)
        self.assertEqual(api.appel('GET', chemin, jeton=api.referent)[1]['zones_relecture'], [[], []])
        passages = [{'texte':'Kalune Testive', 'origine':'manuel', 'categorie':'manuel', 'coche':True},
                    {'texte':'Petit', 'origine':'manuel', 'categorie':'manuel', 'coche':True}]
        statut, relecture = api.appel('POST', chemin + '/passages', {'passages': passages}, api.referent)
        self.assertEqual(statut, 200)
        for bloc, zones, attendu in zip([objet, relecture['paragraphes'][0]], relecture['zones_relecture'],
                                       ['Kalune Testive', 'Petit']):
            self.assertEqual([bloc[a:b] for a, b, coche in zones if coche], [attendu])
        passages[0]['coche'] = False
        self.assertFalse(api.appel('POST', chemin + '/passages', {'passages':passages}, api.referent)[1]['zones_relecture'][0][0][2])
        passages[0]['coche'] = True
        api.appel('POST', chemin + '/passages', {'passages':passages}, api.referent)
        self.assertEqual(api.appel('POST', chemin + '/valider', {}, api.referent)[0], 200)
        statut, contenu = api.appel('POST', '/api/publications/dossier',
                                    {'ids':[acte['id']], 'date':parcours.aujourdhui()}, api.referent)
        self.assertEqual(statut, 200)
        with zipfile.ZipFile(io.BytesIO(contenu)) as dossier:
            pdf = PdfReader(io.BytesIO(dossier.read('D-07.pdf'))).pages[0].extract_text()
            self.assertIn('Petitville', pdf)
            for texte in [pdf, dossier.read('index.html').decode(), dossier.read('deliberations.csv').decode()]:
                self.assertNotIn('Kalune Testive', texte)
                self.assertIn('[donnée masquée]', texte)
