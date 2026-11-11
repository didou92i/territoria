# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Récupère des références publiques (code et article), sans transmettre le dossier.

Sans entrée, la liste prédéfinie des arrêtés ; sinon, la liste reçue sur l'entrée standard en JSON
`{"references": [[code, article], …]}`. Les identifiants PISTE arrivent par l'environnement, jamais en argument."""
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ['JT_CREDENTIAL_STORE'] = 'keyring' if sys.platform=='darwin' else 'environment'
os.environ['PISTE_ENV'] = 'production'
os.environ.pop('JT_EVIDENCE_DB', None)

from droit_territorial.service import Service

CATALOG = [
    ('Code général des collectivités territoriales','L2212-1'),
    ('Code général des collectivités territoriales','L2212-2'),
    ('Code général des collectivités territoriales','L2213-1'),
    ('Code général des collectivités territoriales','L2213-2'),
    ('Code général des collectivités territoriales','L2224-18'),
    ('Code de la route','R411-25'),
    ('Code de la route','R417-10'),
    ('Code général des collectivités territoriales','L2131-1'),
    ('Code de justice administrative','R421-1'),
]


def references():
    """Références demandées sur l'entrée standard (au plus 30, code et numéro d'article plausibles) ; sinon le catalogue."""
    brut = '' if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read(65536)
    if not brut.strip():
        return CATALOG
    demande = json.loads(brut).get('references', [])
    valides = [(str(code)[:200], str(article)[:30]) for code, article in demande[:30]
               if re.fullmatch(r"Code [\w'’ -]{3,120}", str(code)) and re.fullmatch(r'(?:[LRDA]\*{0,2})?\d[\d.-]{0,20}', str(article))]
    if not valides:
        raise ValueError('Aucune référence valide')
    return valides


def nettoyer(texte):
    """Retire les espaces que la conversion des pages Légifrance laisse autour des renvois (« L. 2121-12 , », « l' article »).
    Les espaces avant « ; » et « : » restent, selon l'usage français."""
    texte=re.sub(r'[ \t]+(?=[,.)\]])','',texte)
    texte=re.sub(r'(?<=[(\[])[ \t]+','',texte)
    return re.sub(r"(?<=\w['’])[ \t]+(?=\w)",'',texte)


async def main():
    date = datetime.now(ZoneInfo('Europe/Paris')).date()
    demandees = references()
    service = Service()

    async def get_one(code,article):
        try:
            item=await service.legal_version(code,article,date)
            if item.get('status')!='ok' or item.get('temporal_applicability')!='yes' or not item.get('metadata',{}).get('source_complete'):
                return {'code':code,'article':article,'status':'indisponible'}
            parts=[item.get('text','')]
            offset=item.get('next_offset')
            while offset is not None:
                page=await service.fetch(item['source_ref'],date,offset,6000)
                parts.append(page.get('text',''))
                offset=page.get('next_offset')
                if sum(map(len,parts))>100000:
                    raise ValueError('Source trop longue')
            text=nettoyer(''.join(parts))
            if not text:
                raise ValueError('Source vide')
            meta=item['metadata']
            return {'code':code,'article':article,'status':'ok','as_of':date.isoformat(),
                    'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),
                    'url':meta.get('canonical_url',''),'version':meta.get('version'),
                    'retrieved_at':meta.get('retrieved_at'),'id':meta.get('canonical_id')}
        except Exception:
            return {'code':code,'article':article,'status':'indisponible'}

    try:
        results=await asyncio.gather(*(get_one(code,article) for code,article in demandees))
        print(json.dumps({'as_of':date.isoformat(),'sources':results},ensure_ascii=False))
    finally:
        await service.close()


if __name__=='__main__':
    asyncio.run(main())
