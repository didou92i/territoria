# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arretes
import corpus_arretes
import demo
import legal_sources

IDENTIFIANTS = ['demo-stationnement-0001-exemple-fictif', 'demo-vitesse-0002-exemple-fictif', 'demo-marche-0003-exemple-fictif']
LEGAL_VIDE = {'as_of': '', 'sources': [], 'status': 'incomplet'}


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / 'donnees'
        self.data.mkdir()

    def installer(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return demo.installer(self.data)

    def manifeste_bibliotheque(self):
        return json.loads((self.data / 'bibliotheque-arretes' / 'manifest.json').read_text(encoding='utf-8'))

    def index(self):
        return json.loads((self.data / 'bibliotheque-arretes' / 'index.json').read_text(encoding='utf-8'))

    def test_demo_permet_l_analyse(self):
        self.assertTrue(self.installer())
        self.assertEqual(len(arretes.verify_reference(self.data)), 64)
        self.assertTrue(arretes.analyze({'contexte': 'Interdire le stationnement rue des Essais', 'collectivite': 'Commune de Démoville'}, self.data)['examples'])

    def test_demo_ne_remplace_pas_un_corpus_existant(self):
        (self.data / 'modeles').mkdir(parents=True)
        (self.data / 'modeles' / 'manifest.json').write_text('{}')
        self.assertFalse(self.installer())
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ['modeles'])
        self.assertEqual(sorted(p.name for p in (self.data / 'modeles').iterdir()), ['manifest.json'])

    def test_demo_ne_remplace_pas_une_bibliotheque_existante(self):
        racine = self.data / 'bibliotheque-arretes'
        racine.mkdir()
        existants = [json.dumps({'files': [{'id': 'arrete-reel-0001-xxxxxxxxxxxx', 'title': 'Autre'}]}),
                     json.dumps({'files': [IDENTIFIANTS[0], 'texte']}), json.dumps({'files': None}), '[]', 'pas du json']
        for original in existants:
            with self.subTest(manifeste=original):
                (racine / 'manifest.json').write_text(original, encoding='utf-8')
                self.assertFalse(self.installer())
                self.assertEqual((racine / 'manifest.json').read_text(encoding='utf-8'), original)
                self.assertFalse((self.data / 'modeles').exists())

    def test_une_installation_interrompue_recommence(self):
        with patch.object(corpus_arretes, 'build_index', side_effect=OSError('disque plein')), self.assertRaises(OSError):
            self.installer()
        self.assertFalse((self.data / 'modeles' / 'manifest.json').exists())
        self.assertTrue(self.installer())
        self.assertEqual(len(arretes.verify_reference(self.data)), 64)

    def test_indexation_incomplete_n_installe_pas_de_modele(self):
        with patch.object(corpus_arretes, 'build_index', return_value={'documents': 2, 'pages': 2, 'errors': [{'id': 'x'}]}), \
                self.assertRaisesRegex(RuntimeError, 'indexés'):
            self.installer()
        self.assertFalse((self.data / 'modeles' / 'manifest.json').exists())

    def test_installation_idempotente(self):
        self.assertTrue(self.installer())
        avant = {p.relative_to(self.data): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        self.assertFalse(self.installer())
        apres = {p.relative_to(self.data): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        self.assertEqual(avant, apres)

    def test_manifeste_de_reference_au_format_attendu(self):
        self.installer()
        manifeste = json.loads((self.data / 'modeles' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(set(manifeste), {'reference', 'sha256', 'trame'})
        self.assertEqual(manifeste['reference'], 'reference-demo.pdf')
        self.assertEqual(manifeste['trame'], 'demo-stationnement')
        pdf = self.data / 'modeles' / 'reference-demo.pdf'
        self.assertEqual(manifeste['sha256'], hashlib.sha256(pdf.read_bytes()).hexdigest())
        texte = ' '.join(page.extract_text() for page in PdfReader(str(pdf)).pages)
        self.assertIn('Commune de Démoville', texte)
        self.assertIn('rue des Essais', texte)

    def test_manifeste_de_bibliotheque_comme_celui_du_pilote(self):
        self.installer()
        fichiers = self.manifeste_bibliotheque()['files']
        self.assertEqual([f['id'] for f in fichiers], IDENTIFIANTS)
        for fichier in fichiers:
            with self.subTest(id=fichier['id']):
                self.assertEqual(set(fichier), {'id', 'title', 'filename', 'sha256', 'size', 'url', 'modified'})
                self.assertRegex(fichier['id'], r'[\w-]{20,80}')
                self.assertEqual(fichier['filename'], fichier['id'] + '.pdf')
                self.assertEqual(fichier['url'], '')
                brut = (self.data / 'bibliotheque-arretes' / 'pdf' / fichier['filename']).read_bytes()
                self.assertEqual(fichier['sha256'], hashlib.sha256(brut).hexdigest())
                self.assertEqual(fichier['size'], len(brut))
                self.assertTrue(fichier['title'] and fichier['modified'])

    def test_exemples_indexes_sans_ocr(self):
        self.installer()
        index = self.index()
        self.assertEqual(index['errors'], [])
        self.assertEqual(sorted(d['id'] for d in index['documents']), sorted(IDENTIFIANTS))
        for doc in index['documents']:
            for page in doc['pages']:
                with self.subTest(id=doc['id'], page=page['page']):
                    self.assertEqual(page['method'], 'texte')
                    self.assertGreater(len(page['text']), 80)

    def test_pas_d_ocr_lance_a_l_installation(self):
        with patch.object(corpus_arretes, '_ocr_page', side_effect=AssertionError('OCR inattendu')):
            self.assertTrue(self.installer())

    def test_chaque_exemple_a_sa_categorie(self):
        self.installer()
        attendu = {IDENTIFIANTS[0]: 'circulation', IDENTIFIANTS[1]: 'circulation', IDENTIFIANTS[2]: 'marche'}
        for doc in self.index()['documents']:
            with self.subTest(id=doc['id']):
                texte = doc['title'] + ' ' + ' '.join(p['text'] for p in doc['pages'])
                self.assertEqual(legal_sources.classify(texte), attendu[doc['id']])

    def test_la_recherche_trouve_l_exemple_du_sujet(self):
        self.installer()
        for requete, ident in (('Interdire le stationnement rue des Essais', IDENTIFIANTS[0]),
                               ('Limiter la vitesse avenue de l’Exemple', IDENTIFIANTS[1]),
                               ('Réorganiser le marché communal du samedi', IDENTIFIANTS[2])):
            with self.subTest(requete=requete):
                exemples = corpus_arretes.search_examples(self.data, requete, limit=3)
                self.assertEqual([e['id'] for e in exemples][:1], [ident])
                self.assertTrue(corpus_arretes.verify_examples(self.data, exemples))

    def test_le_marche_n_est_pas_propose_pour_un_stationnement(self):
        self.installer()
        exemples = corpus_arretes.search_examples(self.data, 'Interdire le stationnement rue des Essais', limit=3)
        self.assertNotIn(IDENTIFIANTS[2], [e['id'] for e in exemples])

    def test_reperes_de_structure_realistes(self):
        self.installer()
        exemples = corpus_arretes.search_examples(self.data, 'Interdire le stationnement rue des Essais', limit=1)
        reperes = corpus_arretes.structure_hints(self.data, exemples)
        self.assertEqual(len(reperes), 1)
        self.assertGreaterEqual(reperes[0]['nombre_articles_reperes'], 4)
        self.assertEqual(set(reperes[0]['roles']), {'motivation', 'signalisation', 'periodes_et_horaires',
                                                      'exceptions_ou_autorisations', 'controle_et_sanctions',
                                                      'diffusion_et_execution'})

    def test_structure_d_un_arrete(self):
        self.installer()
        for doc in self.index()['documents']:
            texte = '\n'.join(p['text'] for p in doc['pages'])
            with self.subTest(id=doc['id']):
                self.assertRegex(texte, r'(?m)^Vu ')
                self.assertRegex(texte, r'(?m)^Considérant ')
                self.assertIn('ARRÊTE', texte)
                self.assertRegex(texte, r'Article 1\b')
                self.assertRegex(texte, r'Article 2\b')
                self.assertRegex(texte, r'(?i)exécution')
                self.assertIn('fictif', texte.casefold())
                self.assertIn('Démoville', texte)

    def test_donnees_fictives_uniquement(self):
        self.installer()
        for pdf in list((self.data / 'bibliotheque-arretes' / 'pdf').glob('*.pdf')) + [self.data / 'modeles' / 'reference-demo.pdf']:
            brut = ' '.join(page.extract_text() for page in PdfReader(str(pdf)).pages)
            texte = brut.casefold()
            with self.subTest(pdf=pdf.name):
                for reel in ('@', 'http'):
                    self.assertNotIn(reel, texte)
                # Une seule collectivité, inventée : toute autre commune nommée serait une donnée réelle. Le nom propre se
                # reconnaît à sa majuscule (« commune de plus de 3 500 habitants » n'en est pas un), après « de » ou « d' »,
                # même à la ligne suivante.
                noms = re.findall(r"(?i:\b(?:commune|ville|mairie|maire))\s+(?i:de\s+|d['’]\s*)([A-ZÀ-Ý][\wÀ-ÿ'’-]*)", brut)
                self.assertLessEqual({nom.casefold() for nom in noms}, {'démoville'})

    def test_la_chaine_de_preparation_accepte_le_corpus(self):
        self.installer()
        with patch('arretes.legal_sources.select', return_value=LEGAL_VIDE):
            entree = {'contexte': 'Interdire le stationnement rue des Essais', 'collectivite': 'Commune de Démoville'}
            analyse = arretes.analyze(entree, self.data)
            self.assertEqual(analyse['template'], 'demo-stationnement')
            self.assertEqual(analyse['category'], 'circulation')
            self.assertEqual(analyse['examples'][0]['id'], IDENTIFIANTS[0])
            enrichi = arretes.enrich({'data': analyse['data'], 'examples': analyse['examples'],
                                      'reference_sha256': analyse['reference_sha256']}, self.data)
            self.assertEqual(enrichi['analysis']['examples'], analyse['examples'])
            prepare = arretes.prepare(entree, self.data, 'local')
            envoi = json.loads(prepare['request']['messages'][-1]['content'])
            self.assertTrue(envoi['trames_exemples'])


class OcrPageTests(unittest.TestCase):
    def test_tesseract_sur_un_fil_et_image_effacee_apres_lecture(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'page-3.png'

            def lancer(commande, **options):
                if commande[0] == corpus_arretes.PDFTOPPM:
                    image.write_bytes(b'png')
                return SimpleNamespace(stdout='Texte lu.'.encode())
            with patch.object(corpus_arretes.subprocess, 'run', side_effect=lancer) as run:
                self.assertEqual(corpus_arretes._ocr_page('scan.pdf', 3, tmp), 'Texte lu.')
            self.assertFalse(image.exists())  # pas d'images qui s'accumulent en mémoire (/tmp) pendant un long scan
        self.assertEqual(run.call_args_list[1].kwargs['env']['OMP_THREAD_LIMIT'], '1')  # plusieurs pages lues à la fois

