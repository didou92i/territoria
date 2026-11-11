# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import hashlib
import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arretes
from docx import Document


class ArreteTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'modeles').mkdir()
        self.reference = self.root / 'modeles' / 'reference-test.pdf'
        self.reference.write_bytes(b'%PDF-fictif')
        self.digest = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        self.manifest = self.root / 'modeles' / 'manifest.json'
        self.manifest.write_text(json.dumps({'reference': 'reference-test.pdf', 'sha256': self.digest, 'trame': 'trame-test'}))
        self.legal_patch=patch('arretes.legal_sources.select',return_value={'as_of':'','sources':[],'status':'incomplet'})
        self.legal_patch.start()
        self.input = {
            'collectivite':'Commune de Testville',
            'objet':'Stationnement rue des Lilas',
            'contexte':'Interdire le stationnement rue des Lilas pour sécuriser les piétons.',
        }
        self.output={
            'objet':'Stationnement rue des Lilas',
            'considerant':'Considérant que la sécurité des piétons doit être assurée rue des Lilas,',
            'mesures':['Le stationnement est interdit rue des Lilas.'],
        }
        self.completion = {'choices':[{'message':{'content':json.dumps(self.output,ensure_ascii=False)}}]}

    def tearDown(self):
        self.legal_patch.stop()
        self.tmp.cleanup()

    def test_reference_change_blocks_generation(self):
        self.assertEqual(arretes.prepare(self.input,self.root,'local')['reference_sha256'],self.digest)
        self.reference.write_bytes(b'%PDF-modifie')
        with self.assertRaises(ValueError):
            arretes.prepare(self.input,self.root,'local')

    def test_reference_is_read_from_manifest(self):
        self.assertEqual(arretes.reference(self.root),{'reference':'reference-test.pdf','sha256':self.digest,'trame':'trame-test'})
        self.assertEqual(arretes.verify_reference(self.root),self.digest)
        self.assertEqual(arretes.prepare(self.input,self.root,'local')['template'],'trame-test')

    def test_missing_manifest_means_missing_reference(self):
        self.manifest.unlink()
        for call in (arretes.reference,arretes.verify_reference):
            with self.subTest(call=call.__name__),self.assertRaisesRegex(ValueError,'^Le modèle de référence est introuvable\\.$'):
                call(self.root)

    def test_missing_pdf_means_missing_reference(self):
        self.reference.unlink()
        with self.assertRaisesRegex(ValueError,'^Le modèle de référence est introuvable\\.$'):
            arretes.verify_reference(self.root)

    def test_modified_reference_keeps_its_message(self):
        self.reference.write_bytes(b'%PDF-modifie')
        with self.assertRaisesRegex(ValueError,'^Le modèle de référence a été modifié ; vérifiez le fichier original\\.$'):
            arretes.verify_reference(self.root)

    def test_invalid_manifest_is_refused(self):
        good={'reference':'reference-test.pdf','sha256':self.digest,'trame':'trame-test'}
        bad=['[]','pas du json',json.dumps({k:v for k,v in good.items() if k!='trame'}),
             json.dumps({**good,'sha256':'abc'}),json.dumps({**good,'trame':''}),json.dumps({**good,'reference':3}),
             json.dumps({**good,'reference':'../modeles/reference-test.pdf'}),json.dumps({**good,'reference':'/etc/passwd'}),
             json.dumps({**good,'reference':'dossier\\fichier.pdf'}),json.dumps({**good,'reference':'.cache'}),json.dumps({**good,'reference':'reference-test.txt'})]
        for text in bad:
            self.manifest.write_text(text)
            with self.subTest(manifest=text),self.assertRaises(ValueError):
                arretes.verify_reference(self.root)

    def test_pilot_manifest_is_accepted_unchanged(self):
        self.manifest.write_text(json.dumps({'reference':'reference-stationnement-2023-13.pdf',
            'sha256':'41bda5d664a888019342c539a8e81692338bfd265d89c8df6806ca856b50d190','trame':'stationnement-2023-13'}))
        self.assertEqual(arretes.reference(self.root)['trame'],'stationnement-2023-13')

    def test_finish_refuses_another_template(self):
        prepared=arretes.prepare(self.input,self.root,'local')
        prepared['template']='autre-trame'
        with self.assertRaisesRegex(ValueError,'La référence au modèle est invalide'):
            arretes.finish({'prepared':prepared,'completion':self.completion},self.root,'local')

    def test_model_receives_only_facts_not_legal_visas(self):
        self.input['visas'] = 'Référence fournie à contrôler'
        prepared = arretes.prepare(self.input,self.root,'local')
        sent = prepared['request']['messages'][-1]['content']
        self.assertIn('rue des Lilas',sent)
        self.assertNotIn('Référence fournie à contrôler',sent)
        self.assertEqual(prepared['data']['visas'],'Référence fournie à contrôler')

    def test_rejects_invented_number_and_legal_reference(self):
        for text in ('Le stationnement est interdit à partir du 20 mars 2030.',
                     'Conformément à l’article R417-10, le stationnement est interdit.'):
            content={**self.output,'mesures':[text]}
            bad={'choices':[{'message':{'content':json.dumps(content)}}]}
            with self.subTest(text=text), self.assertRaises(ValueError):
                arretes.read_completion(bad,self.input)

    def test_draft_reproduces_structure_without_old_identity_or_signature(self):
        prepared=arretes.prepare(self.input,self.root,'local')
        result=arretes.finish({'prepared':prepared,'completion':self.completion},self.root,'local')
        path=self.root/'arretes'/(result['id']+'.docx')
        doc=Document(path)
        text='\n'.join(p.text for p in doc.paragraphs)
        self.assertAlmostEqual(doc.sections[0].page_width.cm,21.0,places=1)
        self.assertIn('Considérant que la sécurité des piétons',text)
        positions=[text.index('Article '+str(i)+' :') for i in range(1,7)]
        self.assertEqual(positions,sorted(positions))
        self.assertIn('Références juridiques spécifiques à identifier et vérifier',text)
        self.assertIn('Signature à apposer par l’autorité compétente',text)
        self.assertIn('Publicité et entrée en vigueur',text)
        self.assertIn('Voies et délais de recours',text)
        self.assertEqual(len(result['document']['articles']),6)
        for article in result['document']['articles']:
            self.assertIn(article['texte'].split('\n')[0],text)
        # L'identité du projet est celle de la demande, et aucune autre : rien ne vient du modèle de référence.
        communes={nom.casefold() for nom in re.findall(r"(?i:\b(?:commune|ville|mairie|maire))\s+(?i:de\s+|d['’]\s*)([A-ZÀ-Ý][\wÀ-ÿ'’-]*)",text)}
        self.assertEqual(communes,{'testville'})
        with zipfile.ZipFile(path) as package:
            self.assertFalse(any('media/' in part for part in package.namelist()))

    def test_textes_du_cache_hors_ligne_dates_dans_les_vigilances_et_l_artefact(self):
        text='Texte de L2212-1'
        cache={'as_of':'2026-01-01','status':'cache_date','sources':[{
            'code':'Code général des collectivités territoriales','article':'L2212-1','as_of':'2026-01-01','text':text,
            'sha256':hashlib.sha256(text.encode()).hexdigest(),
            'url':'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006390149'}]}
        with patch('arretes.legal_sources.select',return_value=cache):
            prepared=arretes.prepare(self.input,self.root,'local')
            result=arretes.finish({'prepared':prepared,'completion':self.completion},self.root,'local')
        self.assertEqual(result['vigilances'][0],
                         'Textes juridiques issus du cache du 01/01/2026 : vérifier qu’ils sont toujours en vigueur.')
        self.assertEqual((result['legal']['status'],result['legal']['as_of']),('cache_date','2026-01-01'))
        self.assertEqual(result['legal']['sources'][0]['as_of'],'2026-01-01')
        self.assertIn('Vu l’article L2212-1 du code général des collectivités territoriales,',result['document']['visas'])
        # L'artefact enregistré (relu par l'interface) garde la même vigilance.
        saved=json.loads((self.root/'arretes'/(result['id']+'.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['vigilances'][0],result['vigilances'][0])

    def test_sans_cache_ancien_aucune_vigilance_de_cache(self):
        prepared=arretes.prepare(self.input,self.root,'local')
        result=arretes.finish({'prepared':prepared,'completion':self.completion},self.root,'local')
        self.assertFalse(any('cache' in v for v in result['vigilances']))

    def test_missing_mandatory_input_is_rejected(self):
        with self.assertRaises(ValueError):
            arretes.prepare({'collectivite':'Testville'},self.root,'local')

    def test_rejects_invalid_measures(self):
        content={**self.output,'mesures':[{'texte':'une mesure'}]}
        bad={'choices':[{'message':{'content':json.dumps(content)}}]}
        with self.assertRaises(ValueError):
            arretes.read_completion(bad,self.input)

    def test_context_alone_can_start_a_project(self):
        prepared=arretes.prepare({'contexte':'Limiter le démarchage à domicile.'},self.root,'local')
        self.assertEqual(prepared['data']['collectivite'],'')
        self.assertEqual(prepared['data']['objet'],'')

    def test_unrequested_prohibition_is_not_silently_added(self):
        result=arretes.read_completion(self.completion,{'contexte':'Réglementer le stationnement.','objet':'','destinataires':''})
        self.assertIn('Préciser la mesure exacte',result['articles'][0]['texte'])
        self.assertTrue(result['_vigilances'])

    def test_unprovided_facts_are_flagged_and_old_recipients_are_not_reused(self):
        content={**self.output,'considerant':'Considérant les plaintes des riverains et la fluidité de la circulation.'}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        result=arretes.read_completion(completion,{'contexte':'Interdire le stationnement pour la sécurité des piétons.','objet':'','destinataires':''})
        self.assertNotIn('plaintes',result['considerant'])
        self.assertIn('motif',result['_vigilances'][0].lower())
        self.assertNotIn('Villeparisis',result['destinataires'])

    def test_exception_is_not_lost_or_split_from_the_prohibition(self):
        content={**self.output,'mesures':['Le démarchage à domicile est interdit.','Sauf autorisation de la commune.']}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        facts={'contexte':'Interdire le démarchage à domicile sauf autorisation de la commune.','objet':'','destinataires':''}
        result=arretes.read_completion(completion,facts)
        self.assertEqual(result['articles'][0]['role'],'mesure')
        self.assertIn('sauf autorisation',result['articles'][0]['texte'])
        self.assertEqual(sum(a['role']=='mesure' for a in result['articles']),1)
        self.assertTrue(result['_vigilances'])

    def test_une_mesure_de_signalisation_ne_double_pas_la_mise_en_oeuvre(self):
        content={**self.output,'mesures':['Le stationnement est interdit rue des Lilas.',
                                          'La signalisation routière est mise en place pour indiquer cette interdiction.']}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        result=arretes.read_completion(completion,{'contexte':'Interdire le stationnement rue des Lilas.','objet':'','destinataires':''})
        self.assertEqual([a['texte'] for a in result['articles'] if a['role']=='mesure'],['Le stationnement est interdit rue des Lilas.'])
        self.assertEqual(sum(a['role']=='mise_en_oeuvre' for a in result['articles']),1)

    def test_une_mesure_de_police_du_moteur_general_a_ses_points_de_vigilance(self):
        content={**self.output,'mesures':['Le stationnement est interdit place du Marché le 14 juin 2026.']}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        facts={'contexte':'Interdire le stationnement place du Marché pour la brocante du 14 juin 2026.','objet':'','destinataires':''}
        vigilances=' '.join(arretes.read_completion(completion,facts)['_vigilances'])
        self.assertIn('proportionnée',vigilances)
        self.assertIn('signalisation',vigilances)

    def test_un_acte_hors_police_n_a_ni_sanctions_ni_mise_en_oeuvre_de_voirie(self):
        content={**self.output,'mesures':['Mme Claire Dubois est nommée adjoint administratif territorial stagiaire.']}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        facts={'contexte':'Nommer Mme Claire Dubois adjoint administratif territorial stagiaire.','objet':'','destinataires':''}
        result=arretes.read_completion(completion,facts)
        self.assertEqual([a['role'] for a in result['articles']],['mesure'])
        self.assertIn('sans trame dédiée',' '.join(result['_vigilances']))
        self.assertNotIn('contrôle',result['destinataires'])

    def test_une_consigne_apres_un_arrete_le_complete_et_une_question_reste_une_question(self):
        import arrete_conversation
        precedent={'kind':'arrete','inputs':{'contexte':'Marché de Noël place du Test.'}}
        self.assertTrue(arrete_conversation.is_drafting('Il faut autoriser les chalets et interdire la circulation de 8 h à 22 h.',precedent))
        self.assertFalse(arrete_conversation.is_drafting('Quels textes autorisent le maire à interdire la circulation ?',precedent))
        self.assertFalse(arrete_conversation.is_drafting('Il faut autoriser les chalets.',None))

    def test_le_maire_et_le_lieu_de_signature_nomment_la_commune(self):
        import arrete_document
        self.assertEqual(arrete_document._commune('Commune de Démoville'),('Le Maire de la commune de Démoville','Démoville'))
        self.assertEqual(arrete_document._commune('Ville d’Anvers-sur-Oise'),('Le Maire de la ville d’Anvers-sur-Oise','Anvers-sur-Oise'))
        self.assertEqual(arrete_document._commune('Mairie de Testville'),('Le Maire de Testville','Testville'))
        self.assertEqual(arrete_document._commune('Communauté de communes'),('Le Maire',''))
        generated={'objet':'Objet','considerant':'Considérant le motif,','destinataires':'Services','articles':[{'role':'mesure','texte':'Règle.'}]}
        document=arrete_document.assemble({'collectivite':'Commune de Démoville'},generated)
        self.assertEqual((document['autorite'],document['lieu_date']),('Le Maire de la commune de Démoville','Fait à Démoville, le [date de signature]'))
        document=arrete_document.assemble({'collectivite':'Communauté d’agglomération'},generated)
        self.assertEqual(document['lieu_date'],'[Lieu et date de signature à compléter]')

    def test_period_and_hours_stay_with_the_same_prescription(self):
        content={**self.output,'mesures':['Le stationnement est interdit rue des Lilas.',
            'Cette interdiction de stationnement s’applique le 10 janvier 2027.',
            'L’interdiction de stationnement s’applique de 00 h 00 à 23 h 59.']}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        facts={**self.input,'contexte':'Interdire le stationnement le 10 janvier 2027 de 00 h 00 à 23 h 59.'}
        result=arretes.read_completion(completion,facts)
        self.assertEqual(sum(a['role']=='mesure' for a in result['articles']),1)
        self.assertIn('23 h 59',result['articles'][0]['texte'])

    def test_factual_motivation_never_repeats_the_chat_question(self):
        content={**self.output,'considerant':'Considérant la fluidité de la circulation.'}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        result=arretes.read_completion(completion,{**self.input,'contexte':'Est-ce que tu peux interdire le stationnement pour un déménagement ?'})
        self.assertIn('déménagement',result['considerant'])
        self.assertNotIn('Est-ce',result['considerant']);self.assertNotIn('fluidité',result['considerant'])

    def test_equivalent_numeric_dates_and_midnight_are_accepted(self):
        content={**self.output,'mesures':['Le stationnement est interdit le 10/01/2027 à 00 h 00.']}
        completion={'choices':[{'message':{'content':json.dumps(content)}}]}
        result=arretes.read_completion(completion,{**self.input,'contexte':'Interdire le stationnement le 10 janvier 2027 à minuit.'})
        self.assertIn('00 h 00',result['articles'][0]['texte'])

    def test_explicit_follow_up_resolves_midnight_warning(self):
        facts={'contexte':'Du 10 au 15 janvier de minuit à minuit.\n\nPrécision la plus récente, prioritaire :\nDu 10 à 00 h 00 au 15 à 23 h 59.'}
        self.assertFalse(arretes.time_is_ambiguous(facts))
        self.assertTrue(arretes.time_is_ambiguous({'contexte':'Du 10 au 15 de minuit à minuit.'}))


if __name__=='__main__':
    unittest.main()
