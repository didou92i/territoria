# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import config
import llm_backend as llm

LOCAL='http://127.0.0.1:1234/v1'

class ProviderTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def fichier(self):return self.data/'configuration'/'ia.json'
 def ecrire(self,contenu):
  self.fichier().parent.mkdir(parents=True,exist_ok=True)
  self.fichier().write_text(contenu if isinstance(contenu,str) else json.dumps(contenu))
 def save(self,**extra):
  return llm.save(self.data,{'provider':'lmstudio','model':'modele-test','base_url':LOCAL,**extra})

 # Fournisseurs et hôtes acceptés
 def test_openrouter_refuse(self):
  with self.assertRaises(ValueError):llm.validate({'provider':'openrouter','base_url':'https://openrouter.ai/api/v1','model':'x'})
 def test_hote_public_refuse(self):
  with self.assertRaisesRegex(ValueError,'local'):llm.validate({'provider':'lmstudio','base_url':'https://exemple.fr/v1','model':'m'})
 def test_message_hote_refuse(self):
  with self.assertRaises(ValueError) as erreur:llm.validate({'provider':'lmstudio','base_url':'http://8.8.8.8:1234/v1','model':'m'})
  self.assertEqual(str(erreur.exception),'Seul un serveur IA local ou du réseau interne est accepté.')
 def test_hotes_locaux_acceptes(self):
  for url in ('http://127.0.0.1:1234/v1','http://host.containers.internal:1234','http://192.168.1.20:1234/v1',
              'http://localhost:1234','http://[::1]:1234/v1','http://host.docker.internal:1234/v1',
              'http://10.0.0.5:1234','http://172.16.3.4:1234/v1','https://localhost:1234/v1'):
   with self.subTest(url=url):
    self.assertTrue(llm.validate({'provider':'lmstudio','base_url':url,'model':'m'})['base_url'].endswith('/v1'))
 def test_metadonnees_refusees(self):
  with self.assertRaises(ValueError):llm.validate({'provider':'lmstudio','base_url':'http://169.254.169.254/v1','model':'m'})
 def test_adresses_link_local_refusees_meme_deguisees(self):
  for url in ('http://169.254.10.10/v1','http://[fe80::1]:1234/v1','http://[::ffff:169.254.169.254]/v1','http://metadata.google.internal/v1'):
   with self.subTest(url=url),self.assertRaises(ValueError):llm.validate({'provider':'lmstudio','base_url':url,'model':'m'})
 def test_adresses_publiques_refusees(self):
  for url in ('http://8.8.8.8:1234/v1','http://[2001:4860:4860::8888]/v1','http://[::ffff:8.8.8.8]/v1','http://localhost.exemple.fr/v1','http://127.0.0.1.exemple.fr/v1'):
   with self.subTest(url=url),self.assertRaisesRegex(ValueError,'local'):llm.validate({'provider':'lmstudio','base_url':url,'model':'m'})
 def test_hote_des_reglages_accepte(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':'http://ia.mairie.example:1234/v1'})):
   self.assertEqual(llm.validate({'provider':'lmstudio','base_url':'http://ia.mairie.example:8080','model':'m'})['base_url'],'http://ia.mairie.example:8080/v1')
   with self.assertRaisesRegex(ValueError,'local'):llm.validate({'provider':'lmstudio','base_url':'http://autre.example/v1','model':'m'})
 def test_adresse_link_local_refusee_meme_si_elle_est_celle_des_reglages(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':'http://169.254.169.254/v1'})):
   with self.assertRaises(ValueError):llm.validate({'provider':'lmstudio','base_url':'http://169.254.169.254/v1','model':'m'})
 def test_url_malformee_refusee(self):
  for address in ('https://login:secret@localhost','http://127.0.0.1:port/v1','http://127.0.0.1/v1?x=1','ftp://127.0.0.1/v1','127.0.0.1:1234',None):
   with self.subTest(address=address),self.assertRaises(ValueError):self.save(base_url=address)

 # Enregistrement
 def test_keys_never_reach_public_configuration(self):
  result=self.save(api_key='local-secret')
  self.assertNotIn('secret',json.dumps(result));self.assertTrue(result['key_configured'])
  self.assertEqual(self.fichier().stat().st_mode&0o777,0o600)
 def test_configuration_publique_sans_profils(self):
  self.assertEqual(self.save(),{'provider':'lmstudio','base_url':LOCAL,'model':'modele-test','key_configured':False})
  self.assertEqual(llm.public(self.data),{'provider':'lmstudio','base_url':LOCAL,'model':'modele-test','key_configured':False})
 def test_fichier_ecrit_sans_profils(self):
  self.save(api_key='local-secret')
  self.assertEqual(json.loads(self.fichier().read_text()),{'provider':'lmstudio','base_url':LOCAL,'model':'modele-test','api_key':'local-secret'})
 def test_cle_conservee_si_vide_puis_effacee(self):
  self.save(api_key='local-secret');self.save(api_key='')
  self.assertEqual(llm.read(self.data)['api_key'],'local-secret')
  self.save(clear_key=True);self.assertEqual(llm.read(self.data)['api_key'],'')
 def test_enregistrement_refuse_ne_modifie_rien(self):
  self.save()
  avant=self.fichier().read_text()
  for value in ({'provider':'openrouter','model':'x','base_url':'https://openrouter.ai/api/v1'},{'base_url':'https://exemple.fr/v1'},'texte'):
   with self.subTest(value=value),self.assertRaises(ValueError):llm.save(self.data,value)
  self.assertEqual(self.fichier().read_text(),avant)

 # Lecture
 def test_default_model_comes_from_settings(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_MODELE':'modele-reglages'})):
   self.assertEqual(llm.read(self.data)['model'],'modele-reglages')
 def test_default_url_comes_from_settings(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':'http://192.168.1.50:1234/v1'})):
   self.assertEqual(llm.read(self.data)['base_url'],'http://192.168.1.50:1234/v1')
 def test_defaut_normalise_comme_une_valeur_enregistree(self):
  for brut in ('http://host.docker.internal:1234','http://host.docker.internal:1234/','  http://host.docker.internal:1234/v1/ ','http://host.docker.internal:1234/v1'):
   with self.subTest(brut=brut),patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':brut})):
    self.assertEqual(llm.read(self.data)['base_url'],'http://host.docker.internal:1234/v1')
    self.assertEqual(llm.require_configured(self.data)['base_url'],'http://host.docker.internal:1234/v1')
    self.assertEqual(llm.public(self.data)['base_url'],'http://host.docker.internal:1234/v1')
 def test_defaut_sans_v1_atteint_l_adresse_v1_avant_et_apres_enregistrement(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':'http://faux-llm:1234'})):
   with patch.object(llm,'_request',return_value={'data':[]}) as request:
    llm.test(self.data)
   self.assertEqual(request.call_args.args[0]['base_url'],'http://faux-llm:1234/v1')
   llm.save(self.data,{'model':'modele-test'})
   self.assertEqual(llm.read(self.data)['base_url'],'http://faux-llm:1234/v1')
 def test_defaut_link_local_refuse_sans_repli_ni_requete(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':'http://169.254.169.254/latest'})):
   for appel in (llm.read,llm.require_configured,llm.public,llm.test):
    with self.subTest(appel=appel.__name__),patch.object(llm,'build_opener') as reseau,self.assertRaisesRegex(ValueError,'TERRITORIA_LLM_URL'):appel(self.data)
    reseau.assert_not_called()
   with patch.object(llm,'build_opener') as reseau,self.assertRaisesRegex(ValueError,'TERRITORIA_LLM_URL'):
    llm.chat(self.data,{'request':{'model':llm.REGLAGES.modele,'messages':[]}})
   reseau.assert_not_called()
   with self.assertRaisesRegex(ValueError,'TERRITORIA_LLM_URL'):llm.save(self.data,{'model':'m'})
   self.assertFalse(self.fichier().exists())
 def test_defaut_invalide_refuse_meme_avec_un_fichier_enregistre(self):
  self.save()
  for contenu in (None,{'provider':'openrouter','base_url':'https://openrouter.ai/api/v1','model':'x/y'},'{pas du json'):
   if contenu is not None:self.ecrire(contenu)
   for url in ('http://169.254.169.254/v1','ftp://127.0.0.1/v1','http://127.0.0.1:port/v1','127.0.0.1:1234'):
    with self.subTest(contenu=contenu,url=url),patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':url})),self.assertRaisesRegex(ValueError,'TERRITORIA_LLM_URL'):
     llm.read(self.data)
 def test_defaut_avec_hote_interne_du_conteneur_accepte(self):
  with patch.object(llm,'REGLAGES',config.charger({'TERRITORIA_LLM_URL':'http://faux-llm:1234/v1'})):
   self.assertEqual(llm.require_configured(self.data),{'provider':'lmstudio','base_url':'http://faux-llm:1234/v1','model':config.REGLAGES.modele,'api_key':''})
 def test_ancien_fichier_du_pilote_lu_sans_erreur(self):
  self.ecrire({'provider':'lmstudio','base_url':LOCAL,'model':'ancien-modele','api_key':'jeton-local',
               'profiles':{'lmstudio':{'base_url':LOCAL,'model':'ancien-modele','api_key':'jeton-local'},
                           'openrouter':{'base_url':'https://openrouter.ai/api/v1','model':'x/y','api_key':'cle-openrouter'}}})
  self.assertEqual(llm.read(self.data),{'provider':'lmstudio','base_url':LOCAL,'model':'ancien-modele','api_key':'jeton-local'})
  self.assertNotIn('profiles',llm.public(self.data))
 def test_ancien_fichier_openrouter_retombe_sur_le_defaut_local(self):
  self.ecrire({'provider':'openrouter','base_url':'https://openrouter.ai/api/v1','model':'x/y','api_key':'cle-openrouter',
               'profiles':{'openrouter':{'api_key':'cle-openrouter'}}})
  lu=llm.read(self.data)
  self.assertEqual(lu,{'provider':'lmstudio','base_url':config.REGLAGES.llm_url,'model':config.REGLAGES.modele,'api_key':''})
  self.assertNotIn('cle-openrouter',json.dumps(llm.public(self.data)))
  self.assertEqual(llm.require_configured(self.data),lu)
 def test_ancien_fichier_avec_hote_distant_retombe_sur_le_defaut_local(self):
  self.ecrire({'provider':'lmstudio','base_url':'https://mon-mac.example/v1','model':'m','api_key':'jeton'})
  self.assertEqual(llm.read(self.data)['base_url'],config.REGLAGES.llm_url)
  self.assertEqual(llm.read(self.data)['api_key'],'')
 def test_fichier_illisible_retombe_sur_le_defaut_local(self):
  for contenu in ('{pas du json','[]','null'):
   with self.subTest(contenu=contenu):
    self.ecrire(contenu);self.assertEqual(llm.read(self.data)['base_url'],config.REGLAGES.llm_url)
 def test_enregistrer_apres_ancien_fichier_openrouter_reecrit_un_fichier_propre(self):
  self.ecrire({'provider':'openrouter','base_url':'https://openrouter.ai/api/v1','model':'x/y','api_key':'cle-openrouter'})
  self.save()
  self.assertEqual(json.loads(self.fichier().read_text()),{'provider':'lmstudio','base_url':LOCAL,'model':'modele-test','api_key':''})

 # Requêtes
 def test_serveur_local_sans_cle_interroge_directement_le_reseau(self):
  self.save()
  with patch.object(llm,'_request',return_value={'data':[{'id':'modele-test'}]}) as request:
   self.assertEqual(llm.test(self.data),{'reachable':True,'model_available':True,'provider':'lmstudio','model':'modele-test'})
  request.assert_called_once()
 def test_no_fallback_and_no_external_request_controls(self):
  self.save(api_key='local-secret')
  payload={'request':{'model':'modele-test','messages':[{'role':'user','content':'Question'}],'base_url':'https://untrusted.example','provider':{'allow_fallbacks':True}}}
  with patch.object(llm,'_request',return_value={'ok':True}) as request:
   self.assertEqual(llm.chat(self.data,payload),{'ok':True});sent=request.call_args.args[2]
  self.assertEqual(request.call_args.args[0]['base_url'],LOCAL)
  self.assertEqual(set(sent),{'model','messages'})
 def test_provider_or_model_change_blocks_stale_request(self):
  self.save()
  for payload in ({'provider':'openrouter','request':{'model':'modele-test','messages':[]}}, {'request':{'model':'old-model','messages':[]}}):
   with patch.object(llm,'_request') as request,self.assertRaises(ValueError):llm.chat(self.data,payload)
   request.assert_not_called()

class Reponse:
 def __init__(self,corps):self.corps=corps
 def read(self,taille=-1):return self.corps
 def __enter__(self):return self
 def __exit__(self,*args):return False


MODELES={'data':[{'id':'mistral/modele','supported_parameters':['structured_outputs','response_format'],'context_length':32768},
                 {'id':'sans/schema','supported_parameters':['tools']},
                 {'id':'autre/modele','supported_parameters':['structured_outputs'],'context_length':8192}]}


class DemonstrationTests(unittest.TestCase):
 """Serveur de démonstration : OpenRouter à adresse fixe, permis seulement si TERRITORIA_DEMONSTRATION=1."""
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.data=Path(tmp.name)
  reglages=patch.object(llm,'REGLAGES',replace(config.charger({}),demonstration=True));reglages.start();self.addCleanup(reglages.stop)
 def fichier(self):return self.data/'configuration'/'ia.json'
 def local(self):
  """Point de départ « IA locale » (la démonstration commence désormais par OpenRouter)."""
  llm.save(self.data,{'provider':'lmstudio'})
 def test_demonstration_commence_par_openrouter(self):
  # Préparation de la présentation : la démo légère (O2switch) n'a pas de LM Studio ; « Serveur IA injoignable… adresse »
  # ne disait pas quoi faire. Par défaut, OpenRouter attend sa clé, avec un message qui dit où la saisir.
  lu=llm.read(self.data)
  self.assertEqual((lu['provider'],lu['base_url'],lu['model']),('openrouter',llm.URL_OPENROUTER,''))
  with self.assertRaises(RuntimeError) as erreur:llm.require_configured(self.data)
  self.assertEqual(str(erreur.exception),llm.MESSAGE_CLE_ABSENTE)
 def test_demonstration_retour_au_local_depuis_le_defaut(self):
  lu=llm.save(self.data,{'provider':'lmstudio'})
  self.assertEqual((lu['provider'],lu['base_url']),('lmstudio','http://127.0.0.1:1234/v1'))
 def save(self,**extra):
  with patch.object(llm,'_request',return_value=MODELES):
   return llm.save(self.data,{'provider':'openrouter','model':'mistral/modele','api_key':'sk-or-essai',**extra})
 def reseau(self,*corps):
  """Réseau simulé : rend les corps dans l'ordre (une exception est levée) et garde les requêtes envoyées."""
  requetes=[]
  class Ouvreur:
   def open(self,requete,timeout=None):
    requetes.append(requete);corps_=corps[len(requetes)-1]
    if isinstance(corps_,Exception):raise corps_
    return Reponse(corps_ if isinstance(corps_,bytes) else json.dumps(corps_).encode())
  return patch.object(llm,'build_opener',return_value=Ouvreur()),requetes

 def test_openrouter_refuse_hors_demonstration(self):
  with patch.object(llm,'REGLAGES',replace(config.charger({}),demonstration=False)),self.assertRaises(ValueError):
   llm.validate({'provider':'openrouter','base_url':llm.URL_OPENROUTER,'model':'x/y','api_key':'k'})
 def test_openrouter_accepte_en_demonstration_a_adresse_fixe(self):
  resultat=self.save(base_url='https://ailleurs.example/v1')
  self.assertEqual((resultat['provider'],resultat['base_url']),('openrouter','https://openrouter.ai/api/v1'))
 def test_cle_openrouter_jamais_publique(self):
  resultat=self.save()
  self.assertTrue(resultat['key_configured'])
  self.assertNotIn('sk-or-essai',json.dumps(resultat)+json.dumps(llm.public(self.data)))
  self.assertEqual(self.fichier().stat().st_mode&0o777,0o600)
 def test_cle_rognee(self):
  self.save(api_key='  sk-or-essai\n')
  self.assertEqual(json.loads(self.fichier().read_text())['api_key'],'sk-or-essai')
 def test_changer_de_fournisseur_efface_la_cle(self):
  self.save()
  resultat=llm.save(self.data,{'provider':'lmstudio','base_url':LOCAL,'model':'modele-local'})
  self.assertFalse(resultat['key_configured'])
  self.assertEqual(json.loads(self.fichier().read_text())['api_key'],'')
 def test_reglage_openrouter_ignore_hors_demonstration(self):
  self.save()
  with patch.object(llm,'REGLAGES',replace(config.charger({}),demonstration=False)):
   self.assertEqual(llm.read(self.data)['provider'],'lmstudio')
   self.assertNotIn('sk-or-essai',json.dumps(llm.public(self.data)))
 def test_preference_et_titre_dans_chaque_requete(self):
  self.save()
  reseau,requetes=self.reseau({'choices':[{'message':{'content':'Bonjour'}}]})
  with reseau:llm.chat(self.data,{'request':{'model':'mistral/modele','messages':[{'role':'user','content':'Question'}],'provider':{'allow_fallbacks':True}}})
  corps=json.loads(requetes[0].data)
  self.assertEqual(corps['provider'],llm.PREFERENCE_OPENROUTER)
  self.assertEqual(llm.PREFERENCE_OPENROUTER,{'data_collection':'deny'})
  self.assertEqual(requetes[0].full_url,'https://openrouter.ai/api/v1/chat/completions')
  self.assertEqual(requetes[0].get_header('X-title'),'TerritorIA (demonstration)')
  self.assertEqual(requetes[0].get_header('Authorization'),'Bearer sk-or-essai')
 def test_cle_absente_message_clair(self):
  llm.save(self.data,{'provider':'openrouter','model':'mistral/modele'})
  with self.assertRaises(RuntimeError) as erreur:llm.require_configured(self.data)
  self.assertEqual(str(erreur.exception),'Clé OpenRouter absente : saisissez-la dans Administration › Intelligence artificielle.')
 def test_credits_epuises(self):
  self.save()
  reseau,_=self.reseau(HTTPError(llm.URL_OPENROUTER,402,'Payment Required',{},None))
  with reseau,self.assertRaises(RuntimeError) as erreur:
   llm.chat(self.data,{'model':'mistral/modele','messages':[{'role':'user','content':'Question'}]})
  self.assertEqual(str(erreur.exception),'Crédits OpenRouter épuisés.')
 def test_modeles_filtres_sur_sorties_structurees(self):
  self.save()
  reseau,requetes=self.reseau(MODELES,{'data':{'limit':10,'usage':2.5,'limit_remaining':7.5}})
  with reseau:etat=llm.etat_modeles(self.data)
  self.assertEqual([m['id'] for m in etat['modeles']],['mistral/modele','autre/modele'])
  self.assertEqual(etat['actif']['id'],'mistral/modele')
  self.assertEqual(etat['credits'],{'limite':10,'utilise':2.5,'restant':7.5})
  self.assertEqual(requetes[0].full_url,'https://openrouter.ai/api/v1/models?supported_parameters=structured_outputs')
  self.assertEqual(requetes[1].full_url,'https://openrouter.ai/api/v1/key')
 def test_liste_trop_volumineuse(self):
  self.save()
  reseau,_=self.reseau(b' '*(4*1024*1024+1))
  with reseau:etat=llm.etat_modeles(self.data)
  self.assertEqual((etat['modeles'],etat['erreur']),([],'Réponse du fournisseur trop volumineuse.'))
 def test_modele_sans_sorties_structurees_refuse(self):
  self.save()  # OpenRouter déjà réglé : tout modèle choisi ensuite est contrôlé
  with patch.object(llm,'_request',return_value=MODELES),self.assertRaises(ValueError) as erreur:
   llm.save(self.data,{'provider':'openrouter','model':'sans/schema','api_key':'sk-or-essai'})
  self.assertEqual(str(erreur.exception),'Ce modèle ne garantit pas les réponses structurées dont TerritorIA a besoin.')
  with patch.object(llm,'_request',side_effect=RuntimeError('Serveur IA injoignable.')):
   self.assertEqual(llm.save(self.data,{'provider':'openrouter','model':'sans/schema','api_key':'sk-or-essai'})['model'],'sans/schema')
 def test_passage_a_openrouter_avec_le_modele_local_le_met_a_choisir(self):
  # Recette : le modèle local (gemma-4-12b-it-mlx) restait enregistré, OpenRouter refusait chaque demande (400).
  self.local()
  with patch.object(llm,'_request',return_value=MODELES):
   resultat=llm.save(self.data,{'provider':'openrouter','model':'gemma-4-12b-it-mlx','api_key':'sk-or-essai'})
  self.assertEqual((resultat['provider'],resultat['model'],resultat['key_configured']),('openrouter','',True))
  with self.assertRaises(RuntimeError) as erreur:llm.require_configured(self.data)
  self.assertEqual(str(erreur.exception),'Choisissez un modèle de rédaction dans Administration › Intelligence artificielle (liste « Modèles disponibles »).')
 def test_passage_a_openrouter_liste_illisible_garde_le_modele(self):
  with patch.object(llm,'_request',side_effect=RuntimeError('Serveur IA injoignable.')):
   self.assertEqual(llm.save(self.data,{'provider':'openrouter','model':'mistralai/mistral-small-2603','api_key':'sk-or-essai'})['model'],
                    'mistralai/mistral-small-2603')
 def test_modele_inconnu_400(self):
  self.save()
  corps=io.BytesIO(b'{"error":{"message":"gemma-4-12b-it-mlx is not a valid model ID","code":400}}')
  reseau,_=self.reseau(HTTPError(llm.URL_OPENROUTER,400,'Bad Request',{},corps))
  with reseau,self.assertRaises(RuntimeError) as erreur:
   llm.chat(self.data,{'model':'mistral/modele','messages':[{'role':'user','content':'Question'}]})
  self.assertEqual(str(erreur.exception),'Modèle inconnu du fournisseur : choisissez-en un dans Administration › Intelligence artificielle.')
 def test_sans_cause_recente_message_generique(self):
  import server
  self.local()
  with patch.object(server,'DATA',self.data),patch.object(server,'ouvrir_sans_proxy',side_effect=HTTPError('http://n8n:5678',500,'Erreur',{},None)),self.assertRaises(RuntimeError) as erreur:
   server.call_n8n([{'role':'user','content':'Question'}],'libre',[])
  self.assertIn('Le workflow n8n ou le fournisseur IA ne répond pas',str(erreur.exception))
 def test_retour_au_local_depuis_l_ecran(self):
  # Relecture finale : l'écran renvoie l'adresse et le modèle d'OpenRouter avec le fournisseur local ; refusé jusqu'ici.
  self.save()
  resultat=llm.save(self.data,{'provider':'lmstudio','base_url':llm.URL_OPENROUTER,'model':'mistral/modele'})
  defaut=llm._defaut_local()
  self.assertEqual((resultat['provider'],resultat['base_url'],resultat['model'],resultat['key_configured']),
                   ('lmstudio',defaut['base_url'],defaut['model'],False))
 def test_etat_local_inchange(self):
  with patch.object(llm,'REGLAGES',replace(config.charger({}),demonstration=False)),patch.object(llm,'etat_lmstudio',return_value={'modeles':[]}) as local:
   self.assertEqual(llm.etat_modeles(self.data),{'modeles':[]})
  local.assert_called_once()


MODELES_MISTRAL={'data':[{'id':'mistral-small-latest','capabilities':{'completion_chat':True}},
                         {'id':'mistral-embed','capabilities':{'completion_chat':False}},
                         {'id':'mistral-medium-latest','capabilities':{'completion_chat':True}}]}


class MistralTests(unittest.TestCase):
 """Option choisie à l'installation : l'API de Mistral, à adresse fixe, au lieu de l'IA locale."""
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.data=Path(tmp.name)
  self.reglages(ia_en_ligne='mistral',mistral_cle='cle-installation')
 def reglages(self,**valeurs):
  correctif=patch.object(llm,'REGLAGES',replace(config.charger({}),**valeurs));correctif.start();self.addCleanup(correctif.stop)
 def fichier(self):return self.data/'configuration'/'ia.json'
 def reseau(self,*corps):
  return DemonstrationTests.reseau(self,*corps)
 def demande(self):
  return llm.chat(self.data,{'request':{'model':llm.read(self.data)['model'],'messages':[{'role':'user','content':'Question'}],
                                        'response_format':{'type':'json_schema','json_schema':{'name':'x','strict':True,'schema':{}}}}})

 def test_mistral_refuse_sans_option(self):
  self.reglages()
  with self.assertRaises(ValueError):llm.validate({'provider':'mistral','base_url':llm.URL_MISTRAL,'model':'mistral-small-latest'})
 def test_option_active_defaut_mistral(self):
  lu=llm.read(self.data)
  self.assertEqual((lu['provider'],lu['base_url'],lu['model']),('mistral','https://api.mistral.ai/v1','mistral-medium-latest'))
 def test_ancien_reglage_local_ignore(self):
  self.fichier().parent.mkdir(parents=True);self.fichier().write_text(json.dumps({'provider':'lmstudio','base_url':LOCAL,'model':'gemma','api_key':''}))
  self.assertEqual(llm.read(self.data)['provider'],'mistral')
 def test_cle_de_l_installation_utilisee_et_jamais_publique(self):
  reseau,requetes=self.reseau({'choices':[{'message':{'content':'{}'}}]})
  with reseau:self.demande()
  self.assertEqual(requetes[0].get_header('Authorization'),'Bearer cle-installation')
  publique=llm.public(self.data)
  self.assertTrue(publique['key_configured']);self.assertNotIn('cle-installation',json.dumps(publique))
 def test_cle_de_l_interface_prioritaire(self):
  llm.save(self.data,{'provider':'mistral','model':'mistral-small-latest','api_key':'cle-interface'})
  reseau,requetes=self.reseau({'choices':[{'message':{'content':'{}'}}]})
  with reseau:self.demande()
  self.assertEqual(requetes[0].get_header('Authorization'),'Bearer cle-interface')
 def test_corps_sans_preference_ni_titre(self):
  reseau,requetes=self.reseau({'choices':[{'message':{'content':'{}'}}]})
  with reseau:self.demande()
  corps=json.loads(requetes[0].data)
  self.assertNotIn('provider',corps);self.assertIn('response_format',corps)
  self.assertIsNone(requetes[0].get_header('X-title'))
  self.assertEqual(requetes[0].full_url,'https://api.mistral.ai/v1/chat/completions')
 def test_mode_hors_ligne_n_empeche_pas_mistral(self):
  self.reglages(ia_en_ligne='mistral',mistral_cle='cle-installation',hors_ligne=True)
  reseau,requetes=self.reseau({'choices':[{'message':{'content':'{}'}}]})
  with reseau:self.demande()
  self.assertEqual(len(requetes),1)
 def test_cle_absente_message_clair(self):
  self.reglages(ia_en_ligne='mistral')
  with self.assertRaises(RuntimeError) as erreur:llm.require_configured(self.data)
  self.assertEqual(str(erreur.exception),"Clé Mistral absente : relancez l'étape mistral de l'installation, ou saisissez-la dans Administration › Intelligence artificielle.")
 def test_cle_refusee_401(self):
  reseau,_=self.reseau(HTTPError(llm.URL_MISTRAL,401,'Unauthorized',{},None))
  with reseau,self.assertRaises(RuntimeError) as erreur:self.demande()
  self.assertEqual(str(erreur.exception),'Clé Mistral refusée : remplacez-la dans Administration › Intelligence artificielle.')
 def refus(self,code,corps):
  import io
  reseau,_=self.reseau(HTTPError(llm.URL_MISTRAL,code,'Refus',{},io.BytesIO(json.dumps(corps).encode())))
  with reseau,self.assertRaises(RuntimeError) as erreur:self.demande()
  return str(erreur.exception)
 def test_refus_422_donne_la_raison_de_mistral(self):
  # Relecture finale (I4) : la première collectivité doit lire pourquoi l'API réelle refuse une requête.
  corps={'object':'error','message':{'detail':[{'type':'extra_forbidden','loc':['body','x'],'msg':'Extra inputs are not permitted'}]}}
  self.assertEqual(self.refus(422,corps),'Le fournisseur IA a refusé la requête. Raison donnée par Mistral : Extra inputs are not permitted')
 def test_refus_400_raison_nettoyee_et_bornee(self):
  message=self.refus(400,{'object':'error','message':'Schéma\nrefusé '+'x'*500})
  self.assertTrue(message.startswith('Le fournisseur IA a refusé la requête. Raison donnée par Mistral : Schéma refusé x'))
  self.assertNotIn('\n',message);self.assertLess(len(message),300)
 def test_quota_epuise_402(self):
  reseau,_=self.reseau(HTTPError(llm.URL_MISTRAL,402,'Payment Required',{},None))
  with reseau,self.assertRaises(RuntimeError) as erreur:self.demande()
  self.assertEqual(str(erreur.exception),'Crédits ou quota Mistral épuisés.')
 def test_modeles_filtres_sur_la_conversation(self):
  reseau,requetes=self.reseau(MODELES_MISTRAL)
  with reseau:etat=llm.etat_modeles(self.data)
  self.assertEqual([m['id'] for m in etat['modeles']],['mistral-small-latest','mistral-medium-latest'])
  self.assertEqual((etat['actif']['id'],etat['credits']),('mistral-medium-latest',None))
  self.assertEqual(requetes[0].full_url,'https://api.mistral.ai/v1/models')
 def test_fournisseurs_publics_seulement_avec_l_option(self):
  self.assertEqual(llm.public(self.data)['fournisseurs'],['mistral'])
  self.reglages()
  self.assertNotIn('fournisseurs',llm.public(self.data))
  self.reglages(demonstration=True)
  self.assertEqual(llm.public(self.data)['fournisseurs'],['lmstudio','openrouter','mistral'])
 def test_retour_au_local_ignore_mistral(self):
  llm.save(self.data,{'provider':'mistral','model':'mistral-small-latest','api_key':'cle-interface'})
  self.reglages()
  lu=llm.read(self.data)
  self.assertEqual((lu['provider'],lu['api_key']),('lmstudio',''))
 def test_demarrage_efface_le_reglage_mistral_retire(self):
  # Relecture finale (I1) : l'étape lmstudio retire l'option ; au démarrage suivant, la clé saisie dans l'écran disparaît.
  llm.save(self.data,{'provider':'mistral','model':'mistral-medium-latest','api_key':'cle-interface'})
  self.reglages()
  llm.oublier_fournisseur_retire(self.data)
  self.assertFalse(self.fichier().exists())
 def test_nouvelle_cle_d_installation_jamais_masquee_par_l_ancienne_de_l_ecran(self):
  # Installation A, écran B, étape lmstudio et redémarrage, puis étape mistral avec C : les requêtes partent avec C.
  llm.save(self.data,{'provider':'mistral','model':'mistral-medium-latest','api_key':'cle-B'})
  self.reglages();llm.oublier_fournisseur_retire(self.data)
  self.reglages(ia_en_ligne='mistral',mistral_cle='cle-C');llm.oublier_fournisseur_retire(self.data)
  reseau,requetes=self.reseau({'choices':[{'message':{'content':'{}'}}]})
  with reseau:self.demande()
  self.assertEqual(requetes[0].get_header('Authorization'),'Bearer cle-C')
 def test_demarrage_garde_les_reglages_permis_et_locaux(self):
  llm.save(self.data,{'provider':'mistral','model':'mistral-medium-latest','api_key':'cle-interface'})
  llm.oublier_fournisseur_retire(self.data)
  self.assertTrue(self.fichier().exists())
  self.fichier().write_text(json.dumps({'provider':'lmstudio','base_url':LOCAL,'model':'gemma','api_key':''}))
  llm.oublier_fournisseur_retire(self.data)  # ignoré tant que Mistral est choisi, il resservira au retour au local
  self.assertTrue(self.fichier().exists())
 def test_402_local_sans_nom_de_fournisseur(self):
  # Relecture de la démonstration (M5) : un serveur local n'affiche jamais le nom d'un service en ligne.
  self.reglages()
  reseau,_=self.reseau(HTTPError(LOCAL,402,'Payment Required',{},None))
  with reseau,self.assertRaises(RuntimeError) as erreur:llm.chat(self.data,{'request':{'model':llm.read(self.data)['model'],'messages':[]}})
  self.assertNotIn('OpenRouter',str(erreur.exception));self.assertNotIn('Mistral',str(erreur.exception))


class CauseEtapeN8nTests(unittest.TestCase):
 """Une étape appelée par n8n (finaliser, preparer…) qui refuse retient sa raison : l'écran la montre, pas un message générique."""
 def setUp(self):
  from tests.test_comptes import Base
  self.base=Base('setUp');self.base.setUp();self.addCleanup(self.base.doCleanups)
 def appel_interne(self,chemin,corps):
  import web
  entetes={'Content-Type':'application/json','X-Territoria-Service-Token':'jeton-test'}
  return web.handle('POST',chemin,entetes,json.dumps(corps).encode())
 def test_cause_du_fournisseur_remontee_a_travers_n8n(self):
  # Recette : l'écran disait « Le workflow n8n ou le fournisseur IA ne répond pas » au lieu de la cause donnée par OpenRouter.
  import server
  def n8n(requete,timeout=None):
   with patch.object(llm,'_request',side_effect=RuntimeError(llm.MESSAGE_MODELE_INCONNU)):
    self.assertEqual(self.appel_interne('/api/llm/chat',{'request':{'model':llm.read(server.DATA)['model'],'messages':[{'role':'user','content':'Q'}]}}).status,502)
   raise HTTPError('http://n8n:5678',500,'Internal Server Error',{},None)
  with patch.object(server,'ouvrir_sans_proxy',side_effect=n8n),self.assertRaises(RuntimeError) as erreur:
   server.call_n8n([{'role':'user','content':'Q'}],'libre',[])
  self.assertEqual(str(erreur.exception),llm.MESSAGE_MODELE_INCONNU)
 def test_sonde_hors_appel_ne_remplace_pas_la_cause(self):
  # Relecture finale : une sonde (/api/status, indexation, crédits) qui échoue pendant l'appel écrasait la cause,
  # interrupteur éteint compris.
  import server
  def n8n(requete,timeout=None):
   with patch.object(llm,'build_opener') as ouvreur:
    ouvreur.return_value.open.side_effect=HTTPError(llm.URL_OPENROUTER,404,'Not Found',{},None)
    with self.assertRaises(RuntimeError):llm.test(server.DATA)
   raise HTTPError('http://n8n:5678',500,'Erreur',{},None)
  with patch.object(server,'ouvrir_sans_proxy',side_effect=n8n),self.assertRaises(RuntimeError) as erreur:
   server.call_n8n([{'role':'user','content':'Q'}],'libre',[])
  self.assertIn('Le workflow n8n ou le fournisseur IA ne répond pas',str(erreur.exception))
 def test_appels_n8n_simultanes_message_generique(self):
  # Relecture finale : en démonstration à plusieurs, la cause d'un autre appel ne doit jamais s'afficher.
  import server
  def n8n(requete,timeout=None):
   autre=server._ouvrir_appel_n8n()  # un autre utilisateur, au même moment
   self.addCleanup(server._fermer_appel_n8n,autre)
   self.appel_interne('/api/arretes/finaliser',{'prepared':{},'completion':{}})
   raise HTTPError('http://n8n:5678',500,'Erreur',{},None)
  with patch.object(server,'ouvrir_sans_proxy',side_effect=n8n),self.assertRaises(RuntimeError) as erreur:
   server.call_n8n([{'role':'user','content':'Q'}],'libre',[])
  self.assertIn('Le workflow n8n ou le fournisseur IA ne répond pas',str(erreur.exception))
 def test_refus_de_finaliser_retenu(self):
  import time,web
  debut=time.monotonic()
  entetes={'Content-Type':'application/json','X-Territoria-Service-Token':'jeton-test'}
  reponse=web.handle('POST','/api/arretes/finaliser',entetes,json.dumps({'prepared':{},'completion':{}}).encode())
  self.assertEqual(reponse.status,400)
  self.assertEqual(llm.cause_recente(debut),json.loads(reponse.body)['error'])


if __name__=='__main__':unittest.main()
