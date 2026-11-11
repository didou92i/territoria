# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Serveur IA local (LM Studio ou compatible OpenAI) ; secrets conservés côté serveur.

Option choisie à l'installation (TERRITORIA_IA_EN_LIGNE=mistral) : l'API de Mistral, à adresse fixe, au lieu du local.
Sur le seul serveur de démonstration (TERRITORIA_DEMONSTRATION=1), OpenRouter et Mistral, à adresse fixe."""
import ipaddress
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler,ProxyHandler
from urllib.error import HTTPError,URLError

from config import REGLAGES

PROVIDER='lmstudio'
HOTES_LOCAUX=('localhost','host.docker.internal','host.containers.internal')
MESSAGE_HOTE='Seul un serveur IA local ou du réseau interne est accepté.'
MESSAGE_URL='Indiquez une URL de serveur valide, sans identifiant dans l’adresse.'
MESSAGE_FOURNISSEUR='Seul le fournisseur « LM Studio ou serveur local compatible » est accepté.'
OPENROUTER='openrouter'
URL_OPENROUTER='https://openrouter.ai/api/v1'
PREFERENCE_OPENROUTER={'data_collection':'deny'}  # écarte les fournisseurs qui conservent ou réutilisent les données
TITRE_OPENROUTER='TerritorIA (demonstration)'  # en-tête X-Title : ASCII, comme tout en-tête HTTP sûr
MESSAGE_CLE_ABSENTE='Clé OpenRouter absente : saisissez-la dans Administration › Intelligence artificielle.'
MESSAGE_SANS_SORTIES='Ce modèle ne garantit pas les réponses structurées dont TerritorIA a besoin.'
MESSAGE_MODELE_A_CHOISIR='Choisissez un modèle de rédaction dans Administration › Intelligence artificielle (liste « Modèles disponibles »).'
MESSAGE_MODELE_INCONNU='Modèle inconnu du fournisseur : choisissez-en un dans Administration › Intelligence artificielle.'
MISTRAL='mistral'
URL_MISTRAL='https://api.mistral.ai/v1'
MODELE_MISTRAL='mistral-medium-latest'  # recette d'OpenRouter : mistral-small invente des dates, refusées par les garde-fous des arrêtés
PLONGEMENTS_MISTRAL='mistral-embed'
MESSAGE_CLE_MISTRAL_ABSENTE="Clé Mistral absente : relancez l'étape mistral de l'installation, ou saisissez-la dans Administration › Intelligence artificielle."
MESSAGE_CLE_MISTRAL_REFUSEE='Clé Mistral refusée : remplacez-la dans Administration › Intelligence artificielle.'
MESSAGE_SEUL_MISTRAL='Seule l’API de Mistral, choisie à l’installation, est acceptée.'
EN_LIGNE={OPENROUTER:URL_OPENROUTER,MISTRAL:URL_MISTRAL}  # fournisseurs en ligne, chacun à son adresse fixe
_DERNIER_ECHEC={'instant':0.0,'message':''}  # dernière erreur du fournisseur, pour la montrer au lieu d'un message générique


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def fournisseurs_permis():
    """Le local par défaut ; Mistral seul quand l'installation l'a choisi ; les trois sur le serveur de démonstration."""
    if REGLAGES.demonstration:return [PROVIDER,OPENROUTER,MISTRAL]
    if REGLAGES.ia_en_ligne==MISTRAL:return [MISTRAL]
    return [PROVIDER]


def _defaut():
    """Mistral quand l'installation l'a choisi ; OpenRouter, en attente de sa clé, sur le serveur de démonstration (il n'a pas
    de LM Studio) ; sinon le serveur local des réglages."""
    if fournisseurs_permis()==[MISTRAL]:return {'provider':MISTRAL,'base_url':URL_MISTRAL,'model':MODELE_MISTRAL,'api_key':''}
    if REGLAGES.demonstration:return {'provider':OPENROUTER,'base_url':URL_OPENROUTER,'model':'','api_key':''}
    return _defaut_local()


def _defaut_local():
    """Défaut tiré des réglages, normalisé et contrôlé comme une valeur enregistrée ; refusé tel quel s'il est invalide."""
    try:return validate({'provider':PROVIDER,'base_url':REGLAGES.llm_url,'model':REGLAGES.modele,'api_key':''})
    except ValueError as erreur:
        raise ValueError(f'Réglages IA par défaut inutilisables (TERRITORIA_LLM_URL, TERRITORIA_MODELE) : {erreur}') from None


def read(data_dir):
    """Réglages enregistrés ; tout fichier inutilisable, ou d'un fournisseur que l'installation ne permet plus, cède la
    place au défaut.

    Un défaut invalide (réglage de l'opérateur) lève une erreur au lieu de retomber sur autre chose."""
    defaut=_defaut()
    try:
        value=json.loads((Path(data_dir)/'configuration'/'ia.json').read_text())
        if not isinstance(value,dict) or value.get('provider',defaut['provider']) not in fournisseurs_permis():return defaut
        return validate({**defaut,**value})
    except (FileNotFoundError,ValueError):return defaut


def oublier_fournisseur_retire(data_dir):
    """Au démarrage : les réglages d'un fournisseur en ligne que l'installation ne permet plus (Mistral retiré par l'étape
    lmstudio) sont effacés, clé comprise. Une clé saisie dans l'écran ne masque donc jamais celle d'une nouvelle étape
    mistral. Les réglages locaux, seulement ignorés quand Mistral est choisi, resservent au retour au local."""
    fichier=Path(data_dir)/'configuration'/'ia.json'
    try:valeur=json.loads(fichier.read_text())
    except (OSError,ValueError):return
    if isinstance(valeur,dict) and valeur.get('provider') in EN_LIGNE and valeur['provider'] not in fournisseurs_permis():
        fichier.unlink(missing_ok=True)


def _hote_autorise(hote):
    """Machine locale, réseau interne ou hôte des réglages ; jamais une adresse link-local (métadonnées cloud)."""
    interne=urlsplit(REGLAGES.llm_url).hostname
    try:adresse=ipaddress.ip_address(hote)
    except ValueError:return hote in HOTES_LOCAUX or hote==interne
    if adresse.version==6 and adresse.ipv4_mapped:adresse=adresse.ipv4_mapped
    if adresse.is_link_local:return False
    return adresse.is_private or hote==interne


def _modele_et_cle(value,vide_permis=False):
    model=value.get('model','');key=value.get('api_key','')
    if not isinstance(model,str) or (not model.strip() and not vide_permis) or len(model)>200 or any(ord(x)<32 for x in model):
        raise ValueError('Indiquez l’identifiant exact du modèle.')
    if not isinstance(key,str) or len(key)>512 or any(ord(x)<32 for x in key):
        raise ValueError('Clé API invalide.')
    return model.strip(),key


def validate(value):
    fournisseur=value.get('provider') if isinstance(value,dict) else None
    if fournisseur in EN_LIGNE and fournisseur in fournisseurs_permis():
        model,key=_modele_et_cle(value,vide_permis=True)  # adresse fixe ; modèle vide : « à choisir » dans la liste
        return {'provider':fournisseur,'base_url':EN_LIGNE[fournisseur],'model':model,'api_key':key}
    if fournisseur!=PROVIDER or PROVIDER not in fournisseurs_permis():
        raise ValueError(MESSAGE_SEUL_MISTRAL if fournisseurs_permis()==[MISTRAL] else MESSAGE_FOURNISSEUR)
    _,key=_modele_et_cle(value)
    model=value['model']
    url=value.get('base_url','')
    if not isinstance(url,str):raise ValueError(MESSAGE_URL)
    url=url.strip().rstrip('/')
    try:
        parsed=urlsplit(url)
        parsed.port  # lève ValueError quand le port n’est pas un nombre
    except ValueError:raise ValueError(MESSAGE_URL) from None
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(MESSAGE_URL)
    if not _hote_autorise(parsed.hostname):raise ValueError(MESSAGE_HOTE)
    if not parsed.path.rstrip('/').endswith('/v1'):url+='/v1'
    return {'provider':PROVIDER,'base_url':url,'model':model.strip(),'api_key':key}


def _cle(config):
    """Clé saisie dans l'interface, sinon, pour Mistral, celle rangée à l'installation (TERRITORIA_MISTRAL_CLE)."""
    if config['api_key']:return config['api_key']
    return REGLAGES.mistral_cle if config.get('provider')==MISTRAL else ''


def public(data_dir):
    config=read(data_dir)
    valeur={k:v for k,v in config.items() if k!='api_key'}|{'key_configured':bool(_cle(config))}
    if fournisseurs_permis()!=[PROVIDER]:valeur['fournisseurs']=fournisseurs_permis()
    return valeur


def save(data_dir,value):
    if not isinstance(value,dict):raise ValueError('Réglages IA invalides.')
    actuel=read(data_dir)
    config={**actuel,**{k:value[k] for k in ('provider','base_url','model') if k in value}}
    if config.get('provider')!=actuel['provider']:
        config['api_key']=''  # une clé ne suit jamais un autre fournisseur
        if config.get('provider')==PROVIDER:  # retour au local : l'adresse et le modèle en ligne n'y existent pas
            defaut=_defaut_local()
            if config.get('base_url')==actuel['base_url']:config['base_url']=defaut['base_url']
            if config.get('model')==actuel['model']:config['model']=defaut['model']
        elif config.get('provider')==MISTRAL and config.get('model')==actuel['model']:config['model']=MODELE_MISTRAL
    cle=value.get('api_key')
    if isinstance(cle,str) and cle.strip():config['api_key']=cle.strip()  # copier-coller : espaces et retour à la ligne
    elif cle:config['api_key']=cle
    if value.get('clear_key'):config['api_key']=''
    config=validate(config)
    # Premier passage à OpenRouter : la liste des modèles n'est lisible qu'avec la clé enregistrée, le modèle se choisit ensuite.
    if config['provider']==OPENROUTER and config['api_key'] and config['model']:
        if actuel['provider']==OPENROUTER:_exiger_sorties_structurees(config)
        elif not _modele_connu(config):config['model']=''  # passage à OpenRouter : le modèle local n'y existe pas, il est à choisir
    root=Path(data_dir)/'configuration';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=root/'.ia.tmp';temp.write_text(json.dumps(config,ensure_ascii=False));os.chmod(temp,0o600)
    os.replace(temp,root/'ia.json')
    return public(data_dir)


def _request(config,path,payload=None,timeout=600):
    headers={'Accept':'application/json','Content-Type':'application/json'}
    if _cle(config):headers['Authorization']='Bearer '+_cle(config)
    if config.get('provider')==OPENROUTER:headers['X-Title']=TITRE_OPENROUTER
    request=Request(config['base_url']+path,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,headers=headers)
    try:
        # Jamais par un proxy (le prompt et les extraits resteraient lisibles par lui), jamais de redirection.
        with build_opener(ProxyHandler({}),NoRedirect).open(request,timeout=timeout) as response:
            raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('Réponse du fournisseur trop volumineuse.')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('Réponse du fournisseur invalide.')
        return value
    except HTTPError as exc:
        try:corps=exc.read(4096) if exc.fp else b''
        except OSError:corps=b''
        if exc.code==400 and b'valid model' in corps:raise RuntimeError(MESSAGE_MODELE_INCONNU) from None
        fournisseur=config.get('provider')
        reason={401:MESSAGE_CLE_MISTRAL_REFUSEE if fournisseur==MISTRAL else 'Clé API refusée.',
                402:{OPENROUTER:'Crédits OpenRouter épuisés.',MISTRAL:'Crédits ou quota Mistral épuisés.'}.get(fournisseur,'Crédits du fournisseur IA épuisés.'),
                403:'Accès au modèle refusé.',404:'Adresse ou modèle introuvable.',429:'Limite du fournisseur atteinte.'}.get(exc.code,'Le fournisseur IA a refusé la requête.')
        raison=_raison_mistral(corps) if fournisseur==MISTRAL and exc.code not in (401,402) else ''
        raise RuntimeError(f'{reason} Raison donnée par Mistral : {raison}' if raison else reason) from None
    except (URLError,TimeoutError,OSError):
        raise RuntimeError('Serveur IA injoignable. Vérifiez que le serveur est lancé et que l’adresse est correcte.') from None
    except (json.JSONDecodeError,UnicodeDecodeError):
        raise RuntimeError('Le fournisseur n’a pas renvoyé de JSON valide.') from None


def _raison_mistral(corps):
    """Raison courte d'un refus de Mistral (« message », ou le premier detail[].msg d'une erreur de validation), sans
    caractère de contrôle et bornée : la première collectivité doit pouvoir lire pourquoi l'API refuse une requête."""
    try:valeur=json.loads(corps)
    except (ValueError,UnicodeDecodeError):return ''
    raison=valeur.get('message',valeur.get('detail')) if isinstance(valeur,dict) else None
    if isinstance(raison,dict):raison=raison.get('detail',raison.get('message'))
    if isinstance(raison,list) and raison and isinstance(raison[0],dict):raison=raison[0].get('msg')
    if not isinstance(raison,str):return ''
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in raison).split())[:200]


GARDE=None  # appelée avant chaque demande à l'IA (web.py : plafond des visiteurs de la démonstration) ; lève pour refuser


def chat(data_dir,payload):
    config=require_configured(data_dir)
    if GARDE:GARDE()
    request=payload.get('request',payload) if isinstance(payload,dict) else None
    if not isinstance(request,dict) or not isinstance(request.get('messages'),list):raise ValueError('Requête IA invalide.')
    if request.get('model')!=config['model']:raise ValueError('Les réglages IA ont changé pendant la rédaction. Recommencez votre demande.')
    supplied=payload.get('provider') if isinstance(payload,dict) else None
    if supplied and supplied!=config['provider']:raise ValueError('Le fournisseur IA a changé. Recommencez votre demande.')
    # Liste fermée : aucune URL, clé ou décision de routage issue du modèle.
    body={k:request[k] for k in ('model','messages','temperature','max_tokens','response_format') if k in request}
    if config['provider']==OPENROUTER:body['provider']=PREFERENCE_OPENROUTER
    return _request(config,'/chat/completions',body)


def require_configured(data_dir):
    """Un serveur local n’exige pas de clé : la configuration lue est directement utilisable ; un fournisseur en ligne, si."""
    config=read(data_dir)
    if config['provider']==OPENROUTER and not config['api_key']:raise RuntimeError(MESSAGE_CLE_ABSENTE)
    if config['provider']==MISTRAL and not _cle(config):raise RuntimeError(MESSAGE_CLE_MISTRAL_ABSENTE)
    if config['provider'] in EN_LIGNE and not config['model']:raise RuntimeError(MESSAGE_MODELE_A_CHOISIR)
    return config


def retenir_cause(message):
    """Raison d'un échec d'une route appelée par n8n (/api/llm/chat et les étapes des arrêtés, web.py) : l'appel passé par
    n8n la montre ensuite au lieu d'un message générique, n8n ne transmettant pas le détail. Les sondes (état, crédits,
    indexation) n'écrivent jamais ici."""
    _DERNIER_ECHEC.update(instant=time.monotonic(),message=message)


def cause_recente(depuis):
    """Dernière erreur du fournisseur survenue depuis l'instant `depuis` (time.monotonic()), sinon None."""
    return _DERNIER_ECHEC['message'] if _DERNIER_ECHEC['instant']>=depuis else None


def _modele_connu(config):
    try:return config['model'] in {m['id'] for m in _modeles_openrouter(config)}
    except (RuntimeError,ValueError):return True  # liste illisible : le modèle saisi est gardé, l'état de l'IA le dira


def plongements(data_dir,modele,textes):
    """Vecteurs des textes (POST /embeddings) ; avec OpenRouter, sa préférence sur les données ; Mistral n'en a pas."""
    config=require_configured(data_dir)
    corps={'model':modele,'input':textes}
    if config['provider']==OPENROUTER:corps['provider']=PREFERENCE_OPENROUTER
    return _request(config,'/embeddings',corps,timeout=120)


def modeles_plongements(data_dir):
    """Modèles de plongements d'OpenRouter (GET /embeddings/models) ou de Mistral (GET /models, ceux dont le nom l'évoque)."""
    config=require_configured(data_dir)
    if config['provider']==MISTRAL:
        data=_request(config,'/models',timeout=12).get('data',[])
        return [{'id':m['id'],'charge':True} for m in data if isinstance(m,dict) and isinstance(m.get('id'),str) and 'embed' in m['id']]
    data=_request(config,'/embeddings/models',timeout=12).get('data',[])
    return [{'id':m['id'],'charge':True} for m in data if isinstance(m,dict) and isinstance(m.get('id'),str)]


def _modeles_openrouter(config):
    """Modèles d'OpenRouter qui tiennent un schéma JSON strict (arrêtés, délibérations, séances en dépendent)."""
    data=_request(config,'/models?supported_parameters=structured_outputs',timeout=12).get('data',[])
    return [m for m in data if isinstance(m,dict) and isinstance(m.get('id'),str)
            and 'structured_outputs' in (m.get('supported_parameters') or [])]


def _exiger_sorties_structurees(config):
    try:modeles={m['id'] for m in _modeles_openrouter(config)}
    except (RuntimeError,ValueError):return  # OpenRouter injoignable : l'état de l'IA le signalera
    if config['model'] not in modeles:raise ValueError(MESSAGE_SANS_SORTIES)


def _credits(config):
    try:data=_request(config,'/key',timeout=12).get('data')
    except (RuntimeError,ValueError):return None
    if not isinstance(data,dict):return None
    return {'limite':data.get('limit'),'utilise':data.get('usage'),'restant':data.get('limit_remaining')}


def _modeles_mistral(config):
    """Modèles de Mistral faits pour la conversation (les modèles de plongements, d'OCR ou de modération sont écartés)."""
    data=_request(config,'/models',timeout=12).get('data',[])
    return [m for m in data if isinstance(m,dict) and isinstance(m.get('id'),str)
            and isinstance(m.get('capabilities'),dict) and m['capabilities'].get('completion_chat') is True]


def etat_modeles(data_dir):
    """Modèles et modèle actif : ceux de LM Studio, ceux de Mistral, ou, en démonstration, ceux d'OpenRouter avec les
    crédits restants."""
    config=read(data_dir)
    if config['provider'] not in EN_LIGNE:return etat_lmstudio(data_dir)
    if not _cle(config):
        return {'modeles':[],'actif':None,'credits':None,
                'erreur':MESSAGE_CLE_MISTRAL_ABSENTE if config['provider']==MISTRAL else MESSAGE_CLE_ABSENTE}
    try:
        if config['provider']==MISTRAL:
            modeles=[{'id':m['id'],'charge':True,'contexte':m.get('max_context_length'),'contexte_max':m.get('max_context_length'),
                      'quantification':None} for m in _modeles_mistral(config)]
        else:
            modeles=[{'id':m['id'],'charge':True,'contexte':m.get('context_length'),'contexte_max':m.get('context_length'),
                      'quantification':None} for m in _modeles_openrouter(config)]
    except (RuntimeError,ValueError) as erreur:return {'modeles':[],'actif':None,'credits':None,'erreur':str(erreur)}
    credits=_credits(config) if config['provider']==OPENROUTER else None  # Mistral n'expose pas de solde par l'API
    return {'modeles':modeles,'actif':next((m for m in modeles if m['id']==config['model']),None),'credits':credits}


def etat_lmstudio(data_dir):
    """Modèles de LM Studio (API `/api/v0`, propre à LM Studio) avec leur contexte chargé ; None pour un autre serveur."""
    config=read(data_dir)
    racine=config['base_url'][:-3] if config['base_url'].endswith('/v1') else config['base_url']
    try:data=_request({**config,'base_url':racine},'/api/v0/models',timeout=5)
    except (RuntimeError,ValueError):return None
    modeles=[{'id':m['id'],'charge':m.get('state')=='loaded','contexte':m.get('loaded_context_length'),
              'contexte_max':m.get('max_context_length'),'quantification':m.get('quantization')}
             for m in data.get('data',[]) if isinstance(m,dict) and m.get('type') in ('llm','vlm') and isinstance(m.get('id'),str)]
    return {'modeles':modeles,'actif':next((m for m in modeles if m['id']==config['model']),None)}


def test(data_dir):
    config=require_configured(data_dir)
    data=_request(config,'/models',timeout=12)
    found=any(x.get('id')==config['model'] for x in data.get('data',[]) if isinstance(x,dict))
    return {'reachable':True,'model_available':found,'provider':config['provider'],'model':config['model']}
