# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import reprise_pilote
import server
from territoria import fonds_publics

CGCT = 'Code général des collectivités territoriales'
RESSOURCES = Path(__file__).resolve().parents[1] / 'app' / 'territoria' / 'ressources'


class ReferencesLivreesTests(unittest.TestCase):
    def test_chaque_article_livre_est_retenu_une_fois(self):
        livres = set()
        for fichier in sorted(RESSOURCES.glob('*-reference.json')):
            for source in json.loads(fichier.read_text(encoding='utf-8'))['sources']:
                self.assertEqual(hashlib.sha256(source['text'].encode()).hexdigest(), source['sha256'], (fichier.name, source['article']))
                self.assertRegex(source['url'], r'^https://www\.legifrance\.gouv\.fr/codes/article_lc/LEGIARTI\d+$')
                livres.add((source['code'], source['article']))
        retenus = [(a['code'], a['article']) for a in fonds_publics.articles()]
        self.assertEqual(len(retenus), len(set(retenus)))
        self.assertEqual(set(retenus), livres)

    def test_le_fonctionnement_du_conseil_municipal_est_livre(self):
        livres = {a['article'] for a in fonds_publics.articles() if a['code'] == CGCT}
        self.assertLessEqual({'L2121-7', 'L2121-8', 'L2121-9', 'L2121-10', 'L2121-11', 'L2121-12', 'L2121-15', 'L2121-18', 'L2121-25'}, livres)

    def test_deux_copies_d_un_article_la_plus_recente_est_retenue(self):
        with tempfile.TemporaryDirectory() as tmp:
            for nom, date, texte in (('a-reference.json', '2026-10-29', 'L1\n\nAncien texte.'), ('b-reference.json', '2026-11-01', 'L1\n\nNouveau texte.'),
                                     ('c-reference.json', '2026-10-30', 'L1\n\nTexte intermédiaire.')):
                source = {'code': 'Code', 'article': 'L1', 'status': 'ok', 'as_of': date, 'text': texte,
                          'sha256': hashlib.sha256(texte.encode()).hexdigest(), 'url': 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000000000001'}
                (Path(tmp) / nom).write_text(json.dumps({'as_of': date, 'sources': [source]}), encoding='utf-8')
            self.assertEqual([a['text'] for a in fonds_publics.articles(Path(tmp))], ['L1\n\nNouveau texte.'])

    def test_une_copie_alteree_ou_illisible_est_ecartee(self):
        with tempfile.TemporaryDirectory() as tmp:
            bonne = {'code': 'Code', 'article': 'L1', 'status': 'ok', 'as_of': '2026-11-01', 'text': 'L1\n\nTexte.',
                     'sha256': hashlib.sha256('L1\n\nTexte.'.encode()).hexdigest(), 'url': 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000000000001'}
            mauvaises = [{**bonne, 'article': 'L2', 'sha256': '0' * 64}, {**bonne, 'article': 'L3', 'url': 'https://exemple.invalid/L3'},
                         {**bonne, 'article': 'L4', 'status': 'erreur'}, {**bonne, 'article': '../L5'}, 'pas un article']
            (Path(tmp) / 'a-reference.json').write_text(json.dumps({'sources': [bonne] + mauvaises}), encoding='utf-8')
            (Path(tmp) / 'b-reference.json').write_text('{ illisible', encoding='utf-8')
            (Path(tmp) / 'c-reference.json').write_text(json.dumps({'categories': {}}), encoding='utf-8')
            self.assertEqual([a['article'] for a in fonds_publics.articles(Path(tmp))], ['L1'])


class VersementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        donnees = Path(self.tmp.name)
        for nom, valeur in (('DATA', donnees), ('FILES', donnees / 'documents'), ('DB', donnees / 'db.sqlite')):
            correctif = patch.object(server, nom, valeur)
            correctif.start()
            self.addCleanup(correctif.stop)
        server.init()

    def documents(self):
        with server.db() as c:
            return {ligne['id']: dict(ligne) for ligne in c.execute("SELECT * FROM documents WHERE space_id='public'")}

    def test_une_installation_neuve_recoit_les_articles_livres(self):
        documents = self.documents()
        attendus = {fonds_publics.identifiant(a['code'], a['article']) for a in fonds_publics.articles()}
        self.assertGreaterEqual(len(attendus), 59)
        self.assertEqual(set(documents), attendus | {'cgfp-l121-1'})
        convocation = documents[fonds_publics.identifiant(CGCT, 'L2121-12')]
        self.assertEqual(convocation['name'], f'{CGCT} — article L2121-12')
        self.assertEqual(convocation['source_url'], 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000031069083')
        self.assertEqual(convocation['captured'], '2026-11-12')
        fichier = server.FILES / convocation['filename']
        self.assertEqual(hashlib.sha256(fichier.read_bytes()).hexdigest(), convocation['sha256'])
        self.assertEqual(fichier.stat().st_mode & 0o777, 0o600)
        texte = fichier.read_text(encoding='utf-8')
        self.assertTrue(texte.startswith(f'{CGCT} — Article L2121-12\nCopie du 12/11/2026 (Légifrance)'), texte[:120])
        self.assertIn('Le délai de convocation est fixé à cinq jours francs.', texte)
        self.assertEqual(texte.count('L2121-12'), 1)  # le numéro en tête de la copie livrée n'est pas répété

    def test_une_question_sur_la_convocation_trouve_sa_source(self):
        resultats = server.search('Quel est le délai de convocation du conseil municipal dans une commune de 3 500 habitants et plus ?', 'public')
        self.assertIn(f'{CGCT} — article L2121-12', [r['title'] for r in resultats])
        self.assertEqual(server.search('dignité intégrité probité')[0]['id'], 'cgfp-l121-1')

    def test_un_second_demarrage_ne_change_rien(self):
        avant = self.documents()
        with server.db() as c:
            extraits = c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        server.init()
        self.assertEqual(self.documents(), avant)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0], extraits)

    def test_un_article_supprime_ne_revient_pas(self):
        ident = fonds_publics.identifiant(CGCT, 'L2121-12')
        self.assertTrue(server.supprimer_document(ident))
        server.init()
        self.assertNotIn(ident, self.documents())

    def test_une_copie_livree_plus_recente_remplace_l_ancienne(self):
        ident = fonds_publics.identifiant(CGCT, 'L2121-12')
        nouveau = 'L2121-12\n\nLe délai de convocation est fixé à sept jours francs.'
        livres = [dict(a, text=nouveau, sha256=hashlib.sha256(nouveau.encode()).hexdigest(), as_of='2027-01-15')
                  if a['article'] == 'L2121-12' else a for a in fonds_publics.articles()]
        with patch.object(fonds_publics, 'articles', return_value=livres):
            server.init()
        document = self.documents()[ident]
        self.assertEqual(document['captured'], '2027-01-15')
        texte = (server.FILES / document['filename']).read_text(encoding='utf-8')
        self.assertIn('sept jours francs', texte)
        self.assertEqual(hashlib.sha256(texte.encode()).hexdigest(), document['sha256'])
        with server.db() as c:
            extraits = [ligne['content'] for ligne in c.execute('SELECT content FROM chunks WHERE doc_id=?', (ident,))]
        self.assertTrue(extraits and all('cinq jours francs' not in e for e in extraits), extraits)
        self.assertTrue(any('sept jours francs' in e for e in extraits))

    def test_un_document_remplace_par_la_collectivite_n_est_pas_ecrase(self):
        ident = fonds_publics.identifiant(CGCT, 'L2121-12')
        with server.db() as c:
            c.execute('UPDATE documents SET sha256=? WHERE id=?', ('a' * 64, ident))
        nouveau = 'L2121-12\n\nLe délai de convocation est fixé à sept jours francs.'
        livres = [dict(a, text=nouveau, sha256=hashlib.sha256(nouveau.encode()).hexdigest()) if a['article'] == 'L2121-12' else a
                  for a in fonds_publics.articles()]
        with patch.object(fonds_publics, 'articles', return_value=livres):
            server.init()
        self.assertEqual(self.documents()[ident]['sha256'], 'a' * 64)

    def test_la_reprise_du_pilote_voit_toujours_une_base_neuve(self):
        self.assertFalse(reprise_pilote._base_avec_donnees(server.DB))
        server.add_doc('interne', 'note.txt', 'Une note de la collectivité.'.encode())
        self.assertTrue(reprise_pilote._base_avec_donnees(server.DB))


if __name__ == '__main__':
    unittest.main()
