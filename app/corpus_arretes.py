# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Bibliothèque locale d'arrêtés : OCR paginé, empreintes et recherche d'exemples."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from config import REGLAGES

PDFTOPPM = REGLAGES.pdftoppm
TESSERACT = REGLAGES.tesseract
STOP = set('arrete arreter article articles commune municipal municipale ville maire mairie code loi relative relatif reglementation interdiction interdire interdit autorisation expresse sauf hormis exception mesure securite vehicule vehicules public publique publics protection territoire habitants au aux en les des une qui que pour dans avec cette ces sur est sont aux par plus sous depuis sera doit etre afin tous toutes tout toute'.split())


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _words(text):
    normalized = unicodedata.normalize('NFKD', text.casefold())
    normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
    return {word.rstrip('s') if len(word) > 5 else word for word in re.findall(r'[a-z]{4,}', normalized) if word not in STOP}


def _ocr_page(path, number, tmp):
    prefix = Path(tmp) / ('page-' + str(number))
    subprocess.run([PDFTOPPM,'-f',str(number),'-l',str(number),'-singlefile','-r','150','-png',str(path),str(prefix)],
                   check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)
    image = Path(str(prefix)+'.png')
    try:
        # Un fil par Tesseract : plusieurs pages sont lues à la fois, ce qui va trois fois plus vite (mesuré sur 8 cœurs).
        result = subprocess.run([TESSERACT,str(image),'stdout','-l','fra','--psm','3'],check=True,stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,timeout=90,env={**os.environ,'OMP_THREAD_LIMIT':'1'})
    finally:
        image.unlink(missing_ok=True)  # /tmp est en mémoire : les images d'un long scan ne s'y accumulent pas
    return result.stdout.decode('utf-8','replace').strip()


def _index_one(item, pdf_dir):
    path = pdf_dir / item['filename']
    if not path.is_file() or _hash(path) != item['sha256']:
        raise ValueError('PDF manquant ou modifié')
    reader = PdfReader(str(path))
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError('PDF protégé ou trop long')
    pages = []
    with tempfile.TemporaryDirectory(prefix='territoire-ocr-') as tmp:
        for number, page in enumerate(reader.pages,1):
            text = (page.extract_text() or '').strip()
            method = 'texte'
            if len(text) < 80:
                text = _ocr_page(path,number,tmp)
                method = 'ocr'
            pages.append({'page':number,'text':text,'method':method})
    return {**item,'pages':pages}


def build_index(data_dir, workers=3):
    root = Path(data_dir) / 'bibliotheque-arretes'
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    items = manifest.get('files',[])
    if not isinstance(items,list):
        raise ValueError('Inventaire invalide.')
    docs, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_index_one,item,root/'pdf'):item for item in items}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                docs.append(future.result())
            except Exception as exc:
                errors.append({'id':item['id'],'title':item['title'],'error':type(exc).__name__})
            print('Indexés :',len(docs),'/',len(items),'— erreurs :',len(errors),flush=True)
    docs.sort(key=lambda item:item['title'])
    index = {'created':datetime.now(timezone.utc).isoformat(timespec='seconds'),
             'documents':docs,'errors':errors}
    temp = root/'.index.tmp'
    temp.write_text(json.dumps(index,ensure_ascii=False),encoding='utf-8')
    os.chmod(temp,0o600)
    os.replace(temp,root/'index.json')
    return {'documents':len(docs),'pages':sum(len(d['pages']) for d in docs),'errors':errors}


def search_examples(data_dir, query, limit=3):
    root = Path(data_dir) / 'bibliotheque-arretes'
    index_path = root/'index.json'
    if not index_path.is_file():
        return []
    words = _words(query)
    query_plain=_plain_for_hints(query)
    if not words:
        return []
    index = json.loads(index_path.read_text(encoding='utf-8'))
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    originals={item['id']:item for item in manifest.get('files',[])}
    ranked=[]
    for doc in index.get('documents',[]):
        try:
            original=originals.get(doc['id'])
            if not original or original.get('sha256')!=doc.get('sha256') or doc['filename']!=doc['id']+'.pdf' or original.get('filename')!=doc['filename']:
                continue
            path = root/'pdf'/doc['filename']
            if not path.is_file() or _hash(path) != doc['sha256']:
                continue
            title_words = _words(doc['title'])
            title_plain=_plain_for_hints(doc['title'])
            # Écarter les régimes spéciaux qui ne sont pas demandés : une
            # interdiction pour déménagement n'est pas une zone bleue ou PL.
            special=(r'\b(?:pls?|tonnes)\b',r'\b(?:camping|caravane)\w*',r'zone\s+bleue',
                     r'\becoles?\b',r'\bpmr\b',r'\b(?:zi|zac)\b',r'\bmarche\b')
            if any(re.search(pattern,title_plain) and not re.search(pattern,query_plain) for pattern in special):
                continue
            for page in doc.get('pages',[]):
                match = words & _words(page['text'])
                title_match = words & title_words
                score = 3*len(title_match) + len(match)
                if score:
                    ranked.append((score,doc,page,bool(title_match)))
        except (OSError, KeyError, TypeError):
            continue
    ranked.sort(key=lambda x:(-x[0],x[1]['title'],x[2]['page']))
    result=[]
    seen=set()
    has_title_match=any(item[3] for item in ranked)
    minimum=ranked[0][0]*0.7 if ranked else 0
    for score,doc,page,title_matched in ranked:
        if score<minimum:continue
        if has_title_match and not title_matched:
            continue
        if doc['id'] in seen:
            continue
        seen.add(doc['id'])
        # L'extrait reste identifiable dans le texte OCR de la page ; l'OCR doit
        # être contrôlé sur l'image et ne constitue pas une preuve juridique.
        text=page['text']
        lines=list(re.finditer(r'.*(?:\n|$)',text))
        target=next((i for i,line in enumerate(lines) if _words(line.group())&words),0)
        start=lines[max(0,target-1)].start() if lines else 0
        end=lines[min(len(lines)-1,target+4)].start() if lines else len(text)
        excerpt=text[start:min(end,start+900)].strip()
        result.append({'id':doc['id'],'title':doc['title'],'page':page['page'],
                       'excerpt':excerpt,'method':page['method'],'sha256':doc['sha256'],
                       'url':doc.get('url','')})
        if len(result)>=limit:
            break
    return result


def verify_examples(data_dir, examples):
    """Contrôle identité, page et texte OCR de chaque exemple sélectionné."""
    root=Path(data_dir)/'bibliotheque-arretes'
    if not examples:
        return True
    index=json.loads((root/'index.json').read_text(encoding='utf-8'))
    docs={doc['id']:doc for doc in index.get('documents',[])}
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    originals={item['id']:item for item in manifest.get('files',[])}
    for ref in examples:
        doc=docs.get(ref.get('id'))
        original=originals.get(ref.get('id'))
        if not doc or not original or doc.get('sha256')!=ref.get('sha256') or original.get('sha256')!=doc.get('sha256') or doc.get('filename')!=doc['id']+'.pdf' or original.get('filename')!=doc.get('filename'):
            return False
        path=root/'pdf'/doc['filename']
        if not path.is_file() or _hash(path)!=doc['sha256']:
            return False
        page=next((p for p in doc['pages'] if p['page']==ref.get('page')),None)
        if not page or not ref.get('excerpt') or ref['excerpt'] not in page['text']:
            return False
        try:
            if page['method']=='ocr':
                with tempfile.TemporaryDirectory(prefix='territoire-verif-ocr-') as temp:
                    observed=_ocr_page(path,page['page'],temp)
            else:
                observed=(PdfReader(str(path)).pages[page['page']-1].extract_text() or '').strip()
        except Exception:
            return False
        if ref['excerpt'] not in observed:
            return False
    return True


def structure_hints(data_dir, examples):
    """Extrait des repères de rédaction génériques, sans faits propres aux exemples."""
    if not examples:
        return []
    root=Path(data_dir)/'bibliotheque-arretes'
    index=json.loads((root/'index.json').read_text(encoding='utf-8'))
    docs={d['id']:d for d in index.get('documents',[])}
    patterns={
        'motivation':r'considerant',
        'signalisation':r'signalisation|panneaux',
        'periodes_et_horaires':r'horaires|heure|permanen|temporaire',
        'exceptions_ou_autorisations':r'derogation|sauf|autorisation|exception',
        'controle_et_sanctions':r'infraction|sanction|contravention|poursuiv',
        'diffusion_et_execution':r'ampliation|transmis|execution|executer',
    }
    out=[]
    for example in examples:
        doc=docs.get(example['id'])
        if not doc:continue
        text=_plain_for_hints(' '.join(p['text'] for p in doc.get('pages',[])))
        roles=[name for name,pattern in patterns.items() if re.search(pattern,text)]
        numbers=set(re.findall(r'\barticle\s*(\d{1,2})\b',text))
        out.append({'roles':roles,'nombre_articles_reperes':len(numbers)})
    return out


def _plain_for_hints(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c))


if __name__ == '__main__':
    import sys
    if len(sys.argv)!=3 or sys.argv[1]!='index':
        raise SystemExit('Usage : corpus_arretes.py index DOSSIER_DATA')
    print(json.dumps(build_index(sys.argv[2]),ensure_ascii=False),flush=True)
