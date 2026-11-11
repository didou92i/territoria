# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import importlib.util, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('territoire_server',Path(__file__).resolve().parents[1]/'app'/'server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)

class DocumentarySafetyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); app.DATA=Path(self.tmp.name);app.FILES=app.DATA/'documents';app.DB=app.DATA/'db.sqlite';app.init()
 def tearDown(self):self.tmp.cleanup()
 def test_verified_original_is_excluded_after_tampering(self):
  self.assertEqual(app.search('dignité intégrité probité')[0]['id'],'cgfp-l121-1')
  (app.FILES/'cgfp-l121-1.txt').write_text('Document modifié.',encoding='utf-8')
  self.assertEqual(app.search('dignité intégrité probité'),[])
 def test_space_search_is_isolated(self):
  app.add_doc('interne','reglement.txt','Stationnement des vélos municipaux : accès réservé au personnel.'.encode())
  self.assertTrue(app.search('stationnement vélos','interne'))
  self.assertEqual(app.search('stationnement vélos','public'),[])
 def test_un_passage_qui_ne_partage_qu_un_mot_courant_n_est_pas_une_source(self):
  app.add_doc('interne','teletravail.txt','Le télétravail est autorisé deux jours par semaine ; le mercredi, chaque agent est présent.'.encode())
  app.add_doc('interne','arrete.txt','Le présent arrêté entre en vigueur dès sa publication.'.encode())
  resultats=app.search('Combien de jours de télétravail par semaine faut-il être présent ?','interne')
  self.assertEqual([r['title'] for r in resultats],['teletravail.txt'])
 def test_no_relevant_source_means_no_model_call(self):
  with patch.object(app,'call_n8n') as call:
   answer=app.chat({'question':'photovoltaïque extraterrestre','space':'public','mode':'sources'})
   call.assert_not_called();self.assertFalse(answer['model_used']);self.assertEqual(answer['sources'],[])
 def test_verified_extract_is_passed_to_workflow(self):
  with patch.object(app,'call_n8n',return_value='Le texte mentionne la probité [1].') as call:
   result=app.chat({'question':'Quelles obligations de probité et dignité ?', 'space':'public','mode':'sources'})
   self.assertTrue(result['model_used']);self.assertEqual(result['sources'][0]['id'],'cgfp-l121-1')
   sent=call.call_args.args[0];self.assertIn("dignité, impartialité, intégrité et probité",sent[-1]['content'])
 def test_docx_is_searchable(self):
  from docx import Document
  path=Path(self.tmp.name)/'input.docx';d=Document();d.add_paragraph('La bibliothèque municipale ouvre le samedi matin.');d.save(path)
  app.add_doc('interne','bibliotheque.docx',path.read_bytes())
  self.assertTrue(app.search('bibliothèque samedi','interne'))
 def test_pdf_search_preserves_page_number(self):
  from reportlab.pdfgen.canvas import Canvas
  path=Path(self.tmp.name)/'input.pdf';pdf=Canvas(str(path));pdf.drawString(70,700,'Premier chapitre');pdf.showPage();pdf.drawString(70,700,'Les archives municipales sont ouvertes le mardi.');pdf.save()
  app.add_doc('interne','archives.pdf',path.read_bytes())
  self.assertEqual(app.search('archives municipales mardi','interne')[0]['page'],2)
 def pdf_scanne(self,pages=('',)):
  from reportlab.pdfgen.canvas import Canvas
  path=Path(self.tmp.name)/'scan.pdf';pdf=Canvas(str(path))
  for texte in pages:
   if texte:pdf.drawString(70,700,texte)
   pdf.showPage()
  pdf.save();return path.read_bytes()
 def test_un_pdf_scanne_est_lu_par_ocr_page_par_page(self):
  import corpus_arretes
  contenu=self.pdf_scanne(('','Le règlement intérieur du conseil municipal fixe l’ordre du jour des séances publiques chaque trimestre.'))
  with patch.object(corpus_arretes,'_ocr_page',return_value='Le marché hebdomadaire se tient place de la mairie le samedi matin.') as ocr:
   ident=app.add_doc('interne','marche.pdf',contenu)
  self.assertEqual([appel.args[1] for appel in ocr.call_args_list],[1])  # la page avec du texte n'est pas relue
  resultat=app.search('marché hebdomadaire samedi','interne')[0]
  self.assertEqual((resultat['id'],resultat['page'],resultat['ocr']),(ident,1,True))
  self.assertFalse(app.search('règlement intérieur ordre du jour séances','interne')[0]['ocr'])
  self.assertEqual(app.pages_ocr(ident),1)
 def test_sans_ocr_disponible_un_pdf_scanne_est_refuse_clairement(self):
  import corpus_arretes
  with patch.object(corpus_arretes,'_ocr_page',side_effect=FileNotFoundError('tesseract')):
   with self.assertRaises(ValueError) as erreur:app.add_doc('interne','scan.pdf',self.pdf_scanne())
  self.assertIn('lecture optique (OCR)',str(erreur.exception))
  self.assertEqual(list(app.FILES.glob('*.pdf')),[])
 def test_sans_ocr_un_pdf_qui_a_du_texte_est_importe_quand_meme(self):
  import corpus_arretes
  contenu=self.pdf_scanne(('','Les archives municipales sont ouvertes au public le mardi et le jeudi après-midi.'))
  with patch.object(corpus_arretes,'_ocr_page',side_effect=FileNotFoundError('pdftoppm')):
   ident=app.add_doc('interne','archives.pdf',contenu)
  self.assertEqual(app.search('archives municipales mardi jeudi','interne')[0]['page'],2)
  self.assertEqual((app.pages_ocr(ident),app.pages_sans_texte(ident)),(0,1))  # la page scannée est annoncée non lue
 def test_une_page_illisible_n_empeche_pas_de_lire_les_autres(self):
  import corpus_arretes, subprocess
  def ocr(path,numero,tmp):
   if numero==2:raise subprocess.TimeoutExpired('tesseract',90)
   return 'Le conseil municipal se réunit le premier lundi de chaque mois à la mairie.'
  with patch.object(corpus_arretes,'_ocr_page',side_effect=ocr):
   ident=app.add_doc('interne','pv.pdf',self.pdf_scanne(('','')))
  self.assertEqual((app.pages_ocr(ident),app.pages_sans_texte(ident)),(1,1))
  self.assertEqual(app.search('conseil municipal premier lundi','interne')[0]['page'],1)
 def test_un_pdf_scanne_trop_long_est_refuse(self):
  import corpus_arretes
  with patch.object(app,'OCR_PAGES_MAX',2),patch.object(corpus_arretes,'_ocr_page',return_value='Texte lu.') as ocr:
   with self.assertRaises(ValueError) as erreur:app.add_doc('interne','scan.pdf',self.pdf_scanne(('','','')))
  self.assertIn('découpez',str(erreur.exception));ocr.assert_not_called()
 def test_unreferenced_generated_answer_is_not_presented_as_sourced(self):
  with patch.object(app,'call_n8n',return_value='Affirmation sans citation'):
   result=app.chat({'question':'dignité probité','space':'public','mode':'sources'})
  self.assertNotIn('Affirmation sans citation',result['answer'])
  self.assertIn('aucune référence exploitable',result['answer'])
  self.assertIn('Rédaction libre',result['answer'])
 def test_la_collectivite_enregistree_est_donnee_au_modele(self):
  import branding
  branding.save(app.DATA,{'collectivite':'Commune de Testville','logo':''})
  with patch.object(app,'call_n8n',return_value='Brouillon.') as call:
   app.chat({'question':'Rédige un courriel aux agents.','space':'all','mode':'libre'})
  self.assertIn('Collectivité de l’agent : Commune de Testville.',call.call_args.args[0][0]['content'])
 def test_le_rappel_des_citations_clot_l_invite_avec_sources(self):
  with patch.object(app,'call_n8n',return_value='La probité est exigée [1].') as call:
   app.chat({'question':'dignité probité','space':'public','mode':'sources'})
  self.assertTrue(call.call_args.args[0][-1]['content'].endswith(app.RAPPEL_CITATIONS))
 def test_un_arrete_sans_mesure_exploitable_devient_une_question(self):
  import branding
  branding.save(app.DATA,{'collectivite':'Commune de Testville','logo':''})
  projet={'id':'a'*32,'articles':[{'role':'mesure','texte':'[Préciser la mesure exacte envisagée : interdiction, autorisation ou autre modalité.]'}],'examples':[]}
  (app.DATA/'arretes').mkdir(exist_ok=True);(app.DATA/'arretes'/('a'*32+'.docx')).write_bytes(b'word')
  with patch.object(app,'draft_arrete',return_value=projet) as rediger:
   result=app.chat({'question':'Rédige un arrêté pour un marché de Noël place du Test le samedi 19 décembre 2026 de 10 h à 20 h.','space':'all','mode':'sources'})
   self.assertEqual(result['answer'],app.QUESTION_MESURE)
   self.assertFalse((app.DATA/'arretes'/('a'*32+'.docx')).exists())
   app.chat({'question':'Autoriser l’occupation de la place du Test par les exposants.','space':'all','mode':'sources','conversation':result['conversation']})
  contexte=rediger.call_args.args[0]['contexte']  # la précision rejoint la demande d'origine
  self.assertIn('marché de Noël',contexte);self.assertIn('Autoriser l’occupation',contexte)
 def test_workflow_verification_rejects_changed_document(self):
  sources=app.search('dignité probité','public')
  payload={'mode':'sources','sources':sources,'request':{'model':app.MODEL,'messages':[{'role':'user','content':sources[0]['excerpt']}]}}
  self.assertEqual(app.verify_for_n8n(payload)['verification']['sources_verifiees'],1)
  (app.FILES/'cgfp-l121-1.txt').write_text('Texte modifié.',encoding='utf-8')
  with self.assertRaises(ValueError):app.verify_for_n8n(payload)

 def test_chat_routes_drafting_before_documentary_search(self):
  draft={'id':'a'*32,'download':'/api/arretes/'+'a'*32+'/file','document':{'articles':[]},'examples':[]}
  with patch.object(app,'draft_arrete',return_value=draft) as write,patch.object(app,'call_n8n') as answer:
   result=app.chat({'question':'Rédige un arrêté pour la ville de Senlis interdisant le stationnement rue des Lilas pour un déménagement.','mode':'sources','space':'interne'})
  write.assert_called_once();answer.assert_not_called()
  self.assertEqual(write.call_args.args[0]['collectivite'],'Commune de Senlis')
  self.assertEqual(result['artifact']['kind'],'arrete')
  with app.db() as c:
   self.assertIn('document',c.execute("select artifact from messages where role='assistant'").fetchone()['artifact'])

 def test_vague_draft_request_is_clarified_in_the_conversation(self):
  with patch.object(app,'draft_arrete') as write:
   first=app.chat({'question':'Peux-tu m’aider à rédiger un arrêté ?','mode':'sources'})
   self.assertEqual(first['artifact']['kind'],'arrete_clarification');write.assert_not_called()
   second=app.chat({'question':'Pour la ville de Senlis, interdire le stationnement rue des Lilas pour un déménagement.','mode':'sources','conversation':first['conversation']})
   write.assert_called_once()
  self.assertIn('rue des Lilas',write.call_args.args[0]['contexte'])

 def test_une_demande_d_exemple_d_arrete_redige_un_exemple_fictif(self):
  draft={'id':'a'*32,'download':'/api/arretes/'+'a'*32+'/file','articles':[{'role':'mesure','texte':'Le stationnement est interdit rue des Essais.'}],'examples':[]}
  with patch.object(app,'draft_arrete',return_value=draft) as write,patch.object(app,'call_n8n') as answer:
   result=app.chat({'question':'redige moi un exemple d\'arreté','mode':'sources'})
  write.assert_called_once();answer.assert_not_called()
  self.assertIn('stationnement',write.call_args.args[0]['contexte'])
  self.assertEqual(write.call_args.args[0]['collectivite'],'Commune de Démoville')
  self.assertEqual(result['artifact']['kind'],'arrete')
  self.assertIn('exemple fictif',result['answer'])

 def test_un_nom_fictif_exemple_ne_produit_pas_l_exemple_de_stationnement(self):
  with patch.object(app,'draft_arrete') as write,patch.object(app,'call_n8n') as answer:
   result=app.chat({'question':'Rédige un arrêté accordant un permis de construire à Mme Camille Exemple, rue des Essais, Commune de Démoville','mode':'sources'})
  write.assert_not_called();answer.assert_not_called()
  self.assertEqual(result['artifact']['kind'],'arrete_clarification')
  self.assertNotEqual(result['answer'],app.arrete_conversation.REPONSE_EXEMPLE)

 def test_une_deuxieme_demande_vague_obtient_un_exemple_et_non_la_meme_question(self):
  draft={'id':'a'*32,'download':'/api/arretes/'+'a'*32+'/file','articles':[{'role':'mesure','texte':'Le stationnement est interdit rue des Essais.'}],'examples':[]}
  with patch.object(app,'draft_arrete',return_value=draft) as write:
   first=app.chat({'question':'Peux-tu m’aider à rédiger un arrêté ?','mode':'sources'})
   write.assert_not_called()
   second=app.chat({'question':'je ne sais pas, je veux juste voir','mode':'sources','conversation':first['conversation']})
  write.assert_called_once()
  self.assertNotEqual(second['answer'],first['answer'])
  self.assertIn('exemple fictif',second['answer'])

 def test_la_question_sur_la_commune_n_est_pas_reposee_mot_pour_mot(self):
  with patch.object(app,'draft_arrete') as write:
   first=app.chat({'question':'Rédige un arrêté interdisant le stationnement rue des Lilas pour un déménagement.','mode':'sources'})
   second=app.chat({'question':'aucune idée','mode':'sources','conversation':first['conversation']})
  write.assert_not_called()
  self.assertEqual(second['artifact']['kind'],'arrete_clarification')
  self.assertNotEqual(second['answer'],first['answer'])

 def test_la_question_sur_la_mesure_n_est_pas_reposee_mot_pour_mot(self):
  import branding
  branding.save(app.DATA,{'collectivite':'Commune de Testville','logo':''})
  projet={'id':'a'*32,'articles':[{'role':'mesure','texte':'[Préciser la mesure exacte envisagée : interdiction, autorisation ou autre modalité.]'}],'examples':[]}
  with patch.object(app,'draft_arrete',return_value=projet):
   first=app.chat({'question':'Rédige un arrêté pour un marché de Noël place du Test le samedi 19 décembre 2026 de 10 h à 20 h.','space':'all','mode':'sources'})
   second=app.chat({'question':'Fais au mieux.','space':'all','mode':'sources','conversation':first['conversation']})
  self.assertEqual(first['answer'],app.QUESTION_MESURE)
  self.assertEqual(second['artifact']['kind'],'arrete_clarification')
  self.assertNotEqual(second['answer'],first['answer'])

 def test_follow_up_modification_preserves_previous_draft(self):
  draft={'id':'a'*32,'download':'/api/arretes/'+'a'*32+'/file','document':{'articles':[]},'examples':[]}
  with patch.object(app,'draft_arrete',return_value=draft) as write:
   first=app.chat({'question':'Rédige un arrêté pour la ville de Senlis interdisant le stationnement rue des Lilas le 10 janvier 2027.','mode':'sources'})
   app.chat({'question':'Remplace le 10 janvier par le 11 janvier 2027.','mode':'sources','conversation':first['conversation']})
  inputs=write.call_args.args[0]
  self.assertIn('rue des Lilas',inputs['contexte']);self.assertIn('11 janvier 2027',inputs['contexte'])
  self.assertEqual(inputs['collectivite'],'Commune de Senlis')

if __name__=='__main__':unittest.main()
