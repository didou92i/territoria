# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Copies datées de références publiques, sans transmission des dossiers."""
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import REGLAGES

BRIDGE_PYTHON=sys.executable
BRIDGE=Path(__file__).resolve().parent/'legal_bridge.py'
ALL_ARTICLES={'L2212-1','L2212-2','L2213-1','L2213-2','L2224-18','R411-25','R417-10','L2131-1','R421-1'}
# Copie datée de ces articles publics (Légifrance), livrée avec l'application pour les installations hors ligne.
REFERENCE=Path(__file__).resolve().parent/'territoria'/'ressources'/'legifrance-reference.json'


def today():
    return datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()


def _plain(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c))


def classify(text):
    text=_plain(text)
    if re.search(r'\bmarche\s+public\b|\bcommande\s+publique\b',text):
        return 'indetermine'
    if re.search(r'\b(forains?|halles?|marche\s+(communal|municipal|forain|hebdomadaire|nocturne|artisanal)|marche\s+de\s+(la\s+commune|noel))\b',text):
        return 'marche'
    if re.search(r'\b(stationnement|stationner|circulation|vitesse|vehicule|voirie|route|chaussees?)\b',text):
        return 'circulation'
    if re.search(r'\b(demarchage|prospection|petards?|feux|barbecue|bruit|nuisances?|poulaillers?|pigeonniers?|animaux|salubrite|dechets|protoxyde|rassemblements?)\b',text):
        return 'police'
    return 'indetermine'


def is_commune(collectivite):
    return bool(re.search(r'\b(commune|ville|mairie|municipalite)\b',_plain(collectivite))) and not bool(re.search(r'\b(communaute|agglomeration|metropole)\b',_plain(collectivite)))


def _read_cache(path):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        valid=[]
        for source in data.get('sources',[]):
            if source.get('status')!='ok' or source.get('article') not in ALL_ARTICLES:
                continue
            text=source.get('text','')
            if not isinstance(text,str) or hashlib.sha256(text.encode()).hexdigest()!=source.get('sha256'):
                continue
            if not re.fullmatch(r'https://www\.legifrance\.gouv\.fr/codes/article_lc/LEGIARTI\d+',source.get('url','')):
                continue
            valid.append(source)
        return {'as_of':data.get('as_of',''),'sources':valid}
    except (OSError,ValueError,TypeError):
        return {'as_of':'','sources':[]}


def _amorcer(path):
    """Légifrance indisponible (hors ligne ou connecteur inactif) et cache vide : la copie datée livrée avec l'application
    devient le cache (visas dès l'installation).

    Ses textes gardent leur date : la sélection les marque cache_date et la rédaction demande de vérifier leur vigueur.
    """
    reference=_read_cache(REFERENCE)
    if not reference['sources']:
        return {'as_of':'','sources':[]}
    try:
        candidate=path.with_name('.legifrance.tmp')
        candidate.write_bytes(REFERENCE.read_bytes())
        os.chmod(candidate,0o600)
        os.replace(candidate,path)
    except OSError:
        pass
    return reference


def _environnement(data_dir):
    """Identifiants PISTE du connecteur Légifrance (Administration › Connecteurs), pour l'environnement du pont seulement ;
    vide hors ligne ou connecteur inactif : Légifrance est alors indisponible, même si le conteneur a des identifiants."""
    from territoria import connecteurs
    return connecteurs.environnement_legifrance(data_dir,REGLAGES.hors_ligne)


def refresh(data_dir):
    root=Path(data_dir)/'ressources-juridiques'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(root,0o700)
    path=root/'legifrance.json'
    cached=_read_cache(path)
    env=_environnement(data_dir)
    if not env:
        return cached if cached['sources'] else _amorcer(path)
    if cached['as_of']==today() and len(cached['sources'])==len(ALL_ARTICLES):
        return cached
    try:
        result=subprocess.run([BRIDGE_PYTHON,str(BRIDGE)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=True,
                              env={**os.environ,**env})
        data=json.loads(result.stdout.decode('utf-8'))
        if data.get('as_of')!=today():
            raise ValueError('Date de collecte incohérente')
        candidate=root/'.legifrance.tmp'
        candidate.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        os.chmod(candidate,0o600)
        verified=_read_cache(candidate)
        if verified['sources']:
            os.replace(candidate,path)
            return verified
        candidate.unlink(missing_ok=True)
    except (OSError,ValueError,subprocess.SubprocessError):
        pass
    return cached


def select(data_dir,category,collectivite,allow_refresh=True,subject=''):
    path=Path(data_dir)/'ressources-juridiques'/'legifrance.json'
    cache=refresh(data_dir) if allow_refresh else _read_cache(path)
    if category=='indetermine' or not is_commune(collectivite):
        return {'as_of':cache['as_of'],'sources':[],'status':'competence_ou_sujet_inconnu'}
    wanted={'L2212-1','L2212-2','L2131-1','R421-1'}
    if category=='circulation':wanted|={'L2213-1','L2213-2','R411-25'}
    if category=='circulation' and re.search(r'\bstationn\w*',_plain(subject)):wanted.add('R417-10')
    if category=='marche':wanted.add('L2224-18')
    jour=today()
    if not _environnement(data_dir):
        # Hors ligne (spécification, § 3.5), ou connecteur Légifrance inactif : les textes déjà en cache restent utilisables,
        # avec leur date (as_of gardé) ; le statut cache_date le signale, et la rédaction demande de vérifier qu'ils sont
        # toujours en vigueur.
        sources=[s for s in cache['sources'] if s['article'] in wanted]
        if any(s.get('as_of')!=jour for s in sources):
            return {'as_of':cache['as_of'],'sources':sources,'status':'cache_date'}
    else:
        sources=[s for s in cache['sources'] if s['article'] in wanted and s.get('as_of')==jour]
    status='bases_disponibles' if len(sources)==len(wanted) and cache['as_of']==jour else 'incomplet'
    return {'as_of':cache['as_of'],'sources':sources,'status':status}


def vigilance_cache(legal):
    """Point de vigilance des textes issus du cache (statut cache_date), daté du plus ancien d'entre eux ; vide sinon."""
    if not isinstance(legal,dict) or legal.get('status')!='cache_date':
        return ''
    dates=sorted(s.get('as_of') for s in legal.get('sources',[]) if isinstance(s.get('as_of'),str) and s.get('as_of'))
    date=dates[0] if dates else str(legal.get('as_of') or '')
    try:
        date=datetime.strptime(date,'%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        date=date or 'date inconnue'
    return f'Textes juridiques issus du cache du {date} : vérifier qu’ils sont toujours en vigueur.'
