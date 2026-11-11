# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import config
import corpus_arretes
import legal_sources
from territoria import connecteurs

REFERENCE_LIVREE=legal_sources.REFERENCE


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)
        # Sans copie livrée par défaut : chaque test décrit son cache ; la copie livrée a ses propres tests.
        reference=patch.object(legal_sources,'REFERENCE',self.data/'reference-absente.json')
        reference.start();self.addCleanup(reference.stop)
        self.root=self.data/'bibliotheque-arretes'
        (self.root/'pdf').mkdir(parents=True)
        self.path=self.root/'pdf'/'example-one.pdf'
        pdf=Canvas(str(self.path))
        pdf.drawString(60,750,'Arrete : demarchage a domicile. Article 1 : portage d information.')
        pdf.save()
        text=PdfReader(str(self.path)).pages[0].extract_text().strip()
        doc={'id':'example-one','filename':self.path.name,'title':'Exemple demarchage domicile.pdf',
             'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
             'pages':[{'page':1,'text':text,'method':'texte'}]}
        (self.root/'index.json').write_text(json.dumps({'documents':[doc]}),encoding='utf-8')
        (self.root/'manifest.json').write_text(json.dumps({'files':[doc]}),encoding='utf-8')

    def tearDown(self):self.tmp.cleanup()

    def test_citation_matches_page_and_original_hash(self):
        refs=corpus_arretes.search_examples(self.data,'demarchage domicile')
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]['page'],1)
        self.assertTrue(corpus_arretes.verify_examples(self.data,refs))
        self.path.write_bytes(b'fichier modifie')
        self.assertFalse(corpus_arretes.verify_examples(self.data,refs))
        self.assertEqual(corpus_arretes.search_examples(self.data,'demarchage domicile'),[])

    def ecrire_cache(self,as_of,articles=('L2212-1',)):
        root=self.data/'ressources-juridiques'
        root.mkdir(exist_ok=True)
        sources=[]
        for article in articles:
            text='Texte de '+article
            sources.append({'status':'ok','code':'Code général des collectivités territoriales','article':article,
                            'as_of':as_of,'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),
                            'url':'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006390149'})
        (root/'legifrance.json').write_text(json.dumps({'as_of':as_of,'sources':sources}))

    def selectionner(self,hors_ligne,jour='2026-10-29',category='police'):
        reglages=config.charger({'TERRITORIA_HORS_LIGNE':'1' if hors_ligne else '0'})
        with patch.object(legal_sources,'REGLAGES',reglages),patch('legal_sources.today',return_value=jour), \
                patch('subprocess.run',side_effect=AssertionError('Légifrance appelé')):
            # Hors ligne, la mise à jour du cache est autorisée : elle ne doit pas lancer le pont juridique.
            return legal_sources.select(self.data,category,'Commune de Testville',allow_refresh=hors_ligne)

    def test_stale_law_is_not_presented_as_current(self):
        self.activer_legifrance()
        self.ecrire_cache('2026-01-01')
        reglages=config.charger({'TERRITORIA_HORS_LIGNE':'0'})
        with patch.object(legal_sources,'REGLAGES',reglages),patch('legal_sources.today',return_value='2026-10-29'):
            result=legal_sources.select(self.data,'police','Commune de Testville',allow_refresh=False)
        self.assertEqual(result['sources'],[])
        self.assertEqual(result['status'],'incomplet')

    def test_hors_ligne_le_cache_ancien_reste_utilisable_avec_sa_date(self):
        # Spécification, § 3.5 : TERRITORIA_HORS_LIGNE=1 coupe Légifrance, les textes en cache restent utilisables avec
        # leur date affichée (statut cache_date, as_of gardé), sans jamais lancer le pont juridique.
        self.ecrire_cache('2026-01-01',('L2212-1','L2212-2','R417-10'))
        result=self.selectionner(hors_ligne=True)
        self.assertEqual(result['status'],'cache_date')
        self.assertEqual(result['as_of'],'2026-01-01')
        self.assertEqual([(s['article'],s['as_of']) for s in result['sources']],[('L2212-1','2026-01-01'),('L2212-2','2026-01-01')])
        self.assertEqual(legal_sources.vigilance_cache(result),
                         'Textes juridiques issus du cache du 01/01/2026 : vérifier qu’ils sont toujours en vigueur.')

    def test_hors_ligne_cache_du_jour_et_cache_vide(self):
        self.assertEqual(self.selectionner(hors_ligne=True)['status'],'incomplet')  # aucun cache : aucun texte
        self.ecrire_cache('2026-10-29',('L2212-1','L2212-2','L2131-1','R421-1'))
        result=self.selectionner(hors_ligne=True)
        self.assertEqual((result['status'],len(result['sources'])),('bases_disponibles',4))
        self.assertEqual(legal_sources.vigilance_cache(result),'')

    def test_en_ligne_inchange_le_cache_ancien_reste_ecarte(self):
        self.activer_legifrance()
        self.ecrire_cache('2026-01-01',('L2212-1','L2212-2','L2131-1','R421-1'))
        result=self.selectionner(hors_ligne=False)
        self.assertEqual((result['status'],result['sources']),('incomplet',[]))
        self.assertEqual(legal_sources.vigilance_cache(result),'')

    def test_unknown_authority_does_not_receive_municipal_visas(self):
        result=legal_sources.select(self.data,'circulation','Communauté d’agglomération',allow_refresh=False)
        self.assertEqual(result['sources'],[])
        self.assertEqual(result['status'],'competence_ou_sujet_inconnu')

    def test_topics_are_not_all_treated_as_stationnement(self):
        self.assertEqual(legal_sources.classify('réglementer un marché forain'),'marche')
        self.assertEqual(legal_sources.classify('interdire le démarchage à domicile'),'police')
        self.assertEqual(legal_sources.classify('stationnement rue des Lilas'),'circulation')
        self.assertEqual(legal_sources.classify('nommer un agent communal'),'indetermine')
        self.assertEqual(legal_sources.classify('préparer un marché public'),'indetermine')
        self.assertEqual(legal_sources.classify('réglementer le démarchage des commerçants'),'police')
        self.assertEqual(legal_sources.classify('un marché de Noël place du Test'),'marche')
        self.assertEqual(legal_sources.classify('le marché hebdomadaire du mercredi'),'marche')


    def test_hors_ligne_ne_lance_pas_legifrance(self):
        with patch.object(legal_sources,'REGLAGES',config.charger({'TERRITORIA_HORS_LIGNE':'1'})),patch('subprocess.run') as run:
            result=legal_sources.refresh(self.data)
        run.assert_not_called()
        self.assertEqual(result,{'as_of':'','sources':[]})

    def activer_legifrance(self,actif=True):
        connecteurs.enregistrer(self.data,{'legifrance':{'actif':actif,'client_id':'faux-id-admin','client_secret':'faux-secret-admin'}})

    def test_en_ligne_connecteur_actif_lance_legifrance_quand_le_cache_est_vide(self):
        self.activer_legifrance()
        with patch.object(legal_sources,'REGLAGES',config.charger({})),patch('subprocess.run',side_effect=OSError) as run:
            result=legal_sources.refresh(self.data)
        run.assert_called_once()
        self.assertEqual(result,{'as_of':'','sources':[]})

    def test_en_ligne_connecteur_inactif_les_identifiants_du_conteneur_ne_partent_pas(self):
        # Connecteurs désactivés par défaut, activés par l'administration : les identifiants PISTE rangés dans
        # l'environnement du conteneur (étape 4 de l'installation) ne suffisent pas à lancer le pont.
        conteneur={'PISTE_CLIENT_ID':'faux-id-conteneur','PISTE_CLIENT_SECRET':'faux-secret-conteneur'}
        for reglage in ('jamais activé','désactivé avec ses identifiants'):
            with self.subTest(reglage=reglage):
                if reglage!='jamais activé':self.activer_legifrance(actif=False)
                with patch.dict(os.environ,conteneur),patch.object(legal_sources,'REGLAGES',config.charger({})), \
                        patch('subprocess.run',side_effect=OSError) as run:
                    result=legal_sources.refresh(self.data)
                run.assert_not_called()
                self.assertEqual(result,{'as_of':'','sources':[]})

    def test_en_ligne_connecteur_actif_le_pont_recoit_ses_identifiants(self):
        self.activer_legifrance()
        with patch.dict(os.environ,{'PISTE_CLIENT_ID':'faux-id-conteneur','PISTE_CLIENT_SECRET':'faux-secret-conteneur'}), \
                patch.object(legal_sources,'REGLAGES',config.charger({})),patch('subprocess.run',side_effect=OSError) as run:
            legal_sources.refresh(self.data)
        run.assert_called_once()
        env=run.call_args.kwargs['env']
        self.assertEqual((env['PISTE_CLIENT_ID'],env['PISTE_CLIENT_SECRET']),('faux-id-admin','faux-secret-admin'))

    def test_copie_livree_complete_et_verifiable(self):
        livree=legal_sources._read_cache(REFERENCE_LIVREE)
        self.assertEqual(sorted(s['article'] for s in livree['sources']),sorted(legal_sources.ALL_ARTICLES))
        self.assertRegex(livree['as_of'],r'^\d{4}-\d{2}-\d{2}$')

    def test_hors_ligne_sans_cache_la_copie_livree_devient_le_cache_date(self):
        with patch.object(legal_sources,'REFERENCE',REFERENCE_LIVREE):
            result=self.selectionner(hors_ligne=True,jour='2026-11-15',category='circulation')
        self.assertEqual(result['status'],'cache_date')
        self.assertEqual(sorted(s['article'] for s in result['sources']),
                         ['L2131-1','L2212-1','L2212-2','L2213-1','L2213-2','R411-25','R421-1'])
        self.assertTrue(legal_sources.vigilance_cache(result).startswith('Textes juridiques issus du cache du '))
        cache=self.data/'ressources-juridiques'/'legifrance.json'
        self.assertEqual(cache.read_bytes(),REFERENCE_LIVREE.read_bytes())
        self.assertEqual(cache.stat().st_mode&0o777,0o600)

    def test_en_ligne_connecteur_actif_la_copie_livree_n_est_pas_utilisee(self):
        # Pont en échec : la copie livrée ne devient pas le cache, les visas attendent la version du jour.
        self.activer_legifrance()
        with patch.object(legal_sources,'REFERENCE',REFERENCE_LIVREE), \
                patch.object(legal_sources,'REGLAGES',config.charger({})),patch('subprocess.run',side_effect=OSError):
            result=legal_sources.refresh(self.data)
        self.assertEqual(result,{'as_of':'','sources':[]})
        self.assertFalse((self.data/'ressources-juridiques'/'legifrance.json').exists())

    def test_en_ligne_connecteur_inactif_la_copie_livree_devient_le_cache_date(self):
        # Mode en ligne (étape 4 : « o »), Légifrance non activé dans Administration › Connecteurs, par exemple pour
        # n'utiliser que data.gouv.fr : les arrêtés gardent les visas de la copie datée, comme hors ligne, sans le pont.
        for reglage in ('jamais activé','désactivé avec ses identifiants'):
            with self.subTest(reglage=reglage):
                if reglage!='jamais activé':self.activer_legifrance(actif=False)
                with patch.object(legal_sources,'REFERENCE',REFERENCE_LIVREE), \
                        patch.object(legal_sources,'REGLAGES',config.charger({'TERRITORIA_HORS_LIGNE':'0'})), \
                        patch('legal_sources.today',return_value='2026-11-15'), \
                        patch('subprocess.run',side_effect=AssertionError('Légifrance appelé')):
                    result=legal_sources.select(self.data,'circulation','Commune de Testville')
                self.assertEqual(result['status'],'cache_date')
                self.assertEqual(sorted(s['article'] for s in result['sources']),
                                 ['L2131-1','L2212-1','L2212-2','L2213-1','L2213-2','R411-25','R421-1'])
                self.assertTrue(legal_sources.vigilance_cache(result).startswith('Textes juridiques issus du cache du '))
                cache=self.data/'ressources-juridiques'/'legifrance.json'
                self.assertEqual(cache.read_bytes(),REFERENCE_LIVREE.read_bytes())

    def test_en_ligne_connecteur_inactif_le_cache_ancien_reste_utilisable_avec_sa_date(self):
        self.ecrire_cache('2026-01-01',('L2212-1','L2212-2','R417-10'))
        with patch.object(legal_sources,'REGLAGES',config.charger({'TERRITORIA_HORS_LIGNE':'0'})), \
                patch('legal_sources.today',return_value='2026-10-29'):
            result=legal_sources.select(self.data,'police','Commune de Testville',allow_refresh=False)
        self.assertEqual(result['status'],'cache_date')
        self.assertEqual([(s['article'],s['as_of']) for s in result['sources']],[('L2212-1','2026-01-01'),('L2212-2','2026-01-01')])

    def test_pont_juridique_utilise_l_interpreteur_courant(self):
        self.assertEqual(legal_sources.BRIDGE_PYTHON,sys.executable)


if __name__=='__main__':unittest.main()
