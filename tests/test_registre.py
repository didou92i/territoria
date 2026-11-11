# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import legal_sources
import llm_backend
import server
from tests import test_arretes_police as police, test_arretes_rh as rh, test_deliberations as delib
from tests.test_comptes import Base

ANNEE = legal_sources.today()[:4]


class NumerotationTests(Base):
    def rediger(self, module, question, jeton=None):
        with patch.object(llm_backend, 'chat', return_value=module.completion()):
            statut, reponse = self.appel('POST', '/api/chat', {'question': question, 'mode': 'libre'}, jeton)
        self.assertEqual(statut, 200)
        return reponse

    def numeroter(self, reponse, jeton=None):
        return self.appel('POST', f"/api/actes/{reponse['artifact']['id']}/numero", {}, jeton)

    def test_un_numero_attribue_une_fois_et_reporte_partout(self):
        reponse = self.rediger(delib, delib.DEMANDE)
        self.assertIn('[à attribuer]', reponse['artifact']['document']['title'])
        statut, corps = self.numeroter(reponse)
        self.assertEqual((statut, corps['numero']), (200, f'D{ANNEE}-001'))
        self.assertEqual(self.numeroter(reponse)[1]['numero'], f'D{ANNEE}-001')  # un second clic ne consomme rien
        titre = f'Délibération n° D{ANNEE}-001'
        self.assertEqual(corps['titre'], titre)
        messages = self.appel('GET', f"/api/conversations/{reponse['conversation']}")[1]['messages']
        self.assertEqual(messages[-1]['artifact']['document']['title'], titre)
        from docx import Document
        word = Document(io.BytesIO(self.appel('GET', reponse['artifact']['download'])[1]))
        self.assertIn(titre, [p.text for p in word.paragraphs])

    def test_un_compteur_pour_les_arretes_un_autre_pour_les_deliberations(self):
        numeros = [self.numeroter(self.rediger(m, q))[1]['numero']
                   for m, q in ((rh, rh.NOMINATION), (police, police.TRAVAUX), (delib, delib.DEMANDE))]
        self.assertEqual(numeros, [f'{ANNEE}-001', f'{ANNEE}-002', f'D{ANNEE}-001'])

    def test_format_et_numero_de_depart_reglables(self):
        reglage = {'arrete': {'format': 'AM-{annee}-{numero}', 'prochain': 148}}
        statut, corps = self.appel('POST', '/api/admin/numerotation', reglage)
        self.assertEqual((statut, corps['arrete']['prochain']), (200, 148))
        self.assertEqual(self.numeroter(self.rediger(rh, rh.NOMINATION))[1]['numero'], f'AM-{ANNEE}-148')
        self.assertEqual(self.appel('GET', '/api/admin/numerotation')[1]['arrete']['prochain'], 149)
        for mauvais in ({'arrete': {'format': 'AM-{annee}'}}, {'arrete': {'format': '{annee}-{numero}-{nom}'}},
                        {'arrete': {'prochain': 12}}, {'deliberation': {'prochain': 0}}):
            with self.subTest(reglage=mauvais):
                self.assertEqual(self.appel('POST', '/api/admin/numerotation', mauvais)[0], 400)

    def test_registre_et_export(self):
        reponse = self.rediger(police, police.TRAVAUX)
        self.numeroter(reponse)
        self.rediger(rh, rh.NOMINATION)  # non numéroté : absent du registre
        registre = self.appel('GET', '/api/admin/registre')[1]
        self.assertEqual([(a['numero'], a['type'], a['objet']) for a in registre['actes']],
                         [(f'{ANNEE}-001', 'arrete', 'Réglementation de la circulation rue des Lilas pendant des travaux')])
        statut, csv = self.appel('GET', f'/api/admin/registre/csv?annee={ANNEE}')
        self.assertEqual(statut, 200)
        lignes = csv.decode('utf-8-sig').splitlines()
        self.assertEqual(lignes[0], 'Numéro;Type;Date;Objet')
        self.assertTrue(lignes[1].startswith(f'{ANNEE}-001;Arrêté;'))

    def test_droits(self):
        admin = self.premier_admin()
        _, agent = self.creer(admin, 'bob')
        _, autre = self.creer(admin, 'carla')
        reponse = self.rediger(rh, rh.NOMINATION, agent)
        self.assertEqual(self.numeroter(reponse, autre)[0], 404)  # l'acte d'un autre est introuvable
        self.assertEqual(self.numeroter(reponse, agent)[0], 200)
        for route in ('/api/admin/registre', '/api/admin/numerotation', f'/api/admin/registre/csv?annee={ANNEE}'):
            with self.subTest(route=route):
                self.assertEqual(self.appel('GET', route, jeton=agent)[0], 403)
        self.assertEqual(self.appel('POST', '/api/admin/numerotation', {'arrete': {'prochain': 50}}, agent)[0], 403)
        self.assertEqual(self.appel('GET', '/api/admin/registre', jeton=admin)[1]['actes'][0]['numero'], f'{ANNEE}-001')


if __name__ == '__main__':
    unittest.main()


class DeliberationsDuJourTests(unittest.TestCase):
    """Délibérations du registre à la date d'une séance (ordre du jour proposé du procès-verbal, lot 3)."""

    def setUp(self):
        import shutil
        import tempfile
        from territoria.base import migrer
        self.donnees = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.donnees, True)
        self.base = self.donnees / 'territoire.sqlite3'
        migrer.appliquer(self.base)
        (self.donnees / 'arretes').mkdir()

    def acte(self, ident, genre, objet, seance):
        from territoria import registre
        numero = registre.attribuer(self.base, ident, genre, objet)
        (self.donnees / 'arretes' / f'{ident}.json').write_text(json.dumps(
            {'id': ident, 'document': {'kind': genre, 'preambule': ['Extrait du registre des délibérations', f'Séance du {seance}']}}),
            encoding='utf-8')
        return numero

    def test_date_francaise(self):
        from territoria import registre
        for texte, attendu in (('15 décembre 2026', '2026-12-15'), ('1er décembre 2026', '2026-12-01'), ('15/12/2026', '2026-12-15'),
                               ('3 Novembre 2026', '2026-11-03'), ('31 février 2026', None), ('[à compléter]', None), ('', None)):
            with self.subTest(texte=texte):
                self.assertEqual(registre.date_francaise(texte), attendu)

    def test_deliberations_du_jour_seulement(self):
        from territoria import registre
        budget = self.acte('a' * 32, 'deliberation', 'Budget primitif 2027', '3 novembre 2026')
        self.acte('b' * 32, 'deliberation', 'Tarifs de la cantine', '10 novembre 2026')
        self.acte('c' * 32, 'arrete', 'Stationnement', '3 novembre 2026')
        self.assertEqual(registre.deliberations_du(self.base, self.donnees, '2026-11-03'),
                         [{'numero': budget, 'objet': 'Budget primitif 2027'}])

    def test_fichier_d_acte_absent_ignore(self):
        from territoria import registre
        registre.attribuer(self.base, 'd' * 32, 'deliberation', 'Sans fichier')
        (self.donnees / 'arretes' / ('e' * 32 + '.json')).write_text('pas du JSON', encoding='utf-8')
        registre.attribuer(self.base, 'e' * 32, 'deliberation', 'Fichier illisible')
        self.assertEqual(registre.deliberations_du(self.base, self.donnees, '2026-11-03'), [])
