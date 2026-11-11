# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Identité documentaire fournie par l'utilisateur et conservée sur ce Mac."""
import hashlib
import io
import json
import os
import re
from urllib.parse import urlsplit
import uuid
import zipfile
from pathlib import Path
from PIL import Image
from legal_sources import _plain


GABARIT_MAX=5*1024*1024


def profile(data_dir):
    try:
        value=json.loads((Path(data_dir)/'identite'/'profil.json').read_text(encoding='utf-8'))
        result={'collectivite':str(value.get('collectivite',''))[:140],'logo':str(value.get('logo','')),
                'siret':str(value.get('siret','')),'adresse_actes':str(value.get('adresse_actes',''))}
    except (OSError,ValueError,TypeError,AttributeError):
        result={'collectivite':'','logo':'','siret':'','adresse_actes':''}
    return result|{'gabarit':gabarit_file(data_dir) is not None}


def _docx_sain(raw):
    """Vrai document Word, sans macro ni ressource externe chargée à l'ouverture (une image distante trace la lecture) ;
    les liens hypertextes restent permis. Archive bornée en nombre d'entrées et en taille décompressée."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entrees=archive.infolist();noms=archive.namelist()
        if len(entrees)>1000 or sum(e.file_size for e in entrees)>50*1024*1024:
            raise ValueError('archive démesurée')
        if '[Content_Types].xml' not in noms or 'word/document.xml' not in noms:
            raise ValueError('pas un document Word')
        if any(n.lower().endswith('vbaproject.bin') for n in noms) or b'macroEnabled' in archive.read('[Content_Types].xml'):
            raise ValueError('macro')
        for nom in (n for n in noms if n.endswith('.rels')):
            for relation in re.findall(rb'<Relationship\b[^>]*>',archive.read(nom)):
                if re.search(rb'TargetMode\s*=\s*"External"',relation) and not re.search(rb'/hyperlink"',relation):
                    raise ValueError('ressource externe')
    from docx import Document
    Document(io.BytesIO(raw))


def upload_gabarit(data_dir,raw):
    """Papier à en-tête de la collectivité (.docx) : les actes générés reprennent ses en-têtes, pieds de page, marges et police."""
    if not raw or len(raw)>GABARIT_MAX:
        raise ValueError('Choisissez un document Word (.docx) de 5 Mo maximum.')
    try:
        _docx_sain(raw)
    except Exception as exc:
        raise ValueError('Le gabarit doit être un document Word (.docx) valide, sans macro ni contenu externe.') from exc
    root=Path(data_dir)/'identite';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=root/'.gabarit.tmp';temp.write_bytes(raw);os.chmod(temp,0o600);os.replace(temp,root/'gabarit.docx')
    meta=root/'.gabarit-json.tmp';meta.write_text(json.dumps({'sha256':hashlib.sha256(raw).hexdigest()}));os.chmod(meta,0o600)
    os.replace(meta,root/'gabarit.json')


def gabarit_file(data_dir):
    """Chemin du gabarit déposé, s'il est intact (même empreinte qu'au dépôt) ; None sinon."""
    root=Path(data_dir)/'identite'
    try:
        attendu=json.loads((root/'gabarit.json').read_text())['sha256']
        path=root/'gabarit.docx'
        return path if hashlib.sha256(path.read_bytes()).hexdigest()==attendu else None
    except (OSError,ValueError,KeyError,TypeError):
        return None


def supprimer_gabarit(data_dir):
    for nom in ('gabarit.json','gabarit.docx'):
        (Path(data_dir)/'identite'/nom).unlink(missing_ok=True)


def logo_file(data_dir,ident):
    if not re.fullmatch(r'[a-f0-9]{32}',ident):
        raise ValueError('Logo introuvable.')
    root=Path(data_dir)/'identite'
    try:
        meta=json.loads((root/(ident+'.json')).read_text())
        suffix={'image/png':'.png','image/jpeg':'.jpg'}[meta['mime']]
        path=root/(ident+suffix)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['sha256']:
            raise ValueError('Le logo a changé depuis son ajout.')
        return path,meta['mime']
    except (OSError,KeyError,ValueError) as exc:
        raise ValueError('Logo introuvable ou modifié.') from exc


def upload(data_dir,raw):
    if not raw or len(raw)>2*1024*1024:
        raise ValueError('Choisissez un logo PNG ou JPEG de 2 Mo maximum.')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG','JPEG') or image.width*image.height>12000000:
                raise ValueError('Format ou dimensions du logo non pris en charge.')
            image.verify()
            mime='image/png' if image.format=='PNG' else 'image/jpeg'
    except Exception as exc:
        raise ValueError('Choisissez une image PNG ou JPEG valide.') from exc
    root=Path(data_dir)/'identite';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    ident=uuid.uuid4().hex
    path=root/(ident+('.png' if mime=='image/png' else '.jpg'))
    path.write_bytes(raw);os.chmod(path,0o600)
    metadata={'mime':mime,'sha256':hashlib.sha256(raw).hexdigest()}
    meta=root/(ident+'.json');meta.write_text(json.dumps(metadata));os.chmod(meta,0o600)
    return ident


def _siret(valeur):
    """SIRET de la collectivité (CSV national des délibérations) : 14 chiffres et clé de Luhn, espaces retirés ; vide permis."""
    chiffres=''.join(str(valeur).split())
    if not chiffres:return ''
    doubles=[int(c)*(2 if i%2 else 1) for i,c in enumerate(reversed(chiffres))] if chiffres.isdigit() else []
    if len(chiffres)!=14 or sum(d-9 if d>9 else d for d in doubles)%10:  # clé de Luhn
        raise ValueError('SIRET invalide : 14 chiffres attendus.')
    return chiffres


def _adresse_actes(valeur):
    """Adresse des actes publiés sur le site de la collectivité (liens de l'index et DELIB_URL) : https, sans identifiant."""
    adresse=str(valeur).strip()
    if not adresse:return ''
    parts=urlsplit(adresse)
    if len(adresse)>300 or parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Adresse des actes invalide : https://… attendue.')
    return adresse if adresse.endswith('/') else adresse+'/'


def save(data_dir,value):
    name=value.get('collectivite','');logo=value.get('logo','')
    if not isinstance(name,str) or len(name)>140 or not isinstance(logo,str):
        raise ValueError('Identité documentaire invalide.')
    if logo:logo_file(data_dir,logo)
    actuel=profile(data_dir)  # l'écran « Logo et collectivité » n'envoie ni le SIRET ni l'adresse des actes : on les garde
    result={'collectivite':name.strip(),'logo':logo,
            'siret':_siret(value['siret']) if 'siret' in value else actuel['siret'],
            'adresse_actes':_adresse_actes(value['adresse_actes']) if 'adresse_actes' in value else actuel['adresse_actes']}
    root=Path(data_dir)/'identite';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=root/'.profil.tmp';temp.write_text(json.dumps(result,ensure_ascii=False));os.chmod(temp,0o600)
    os.replace(temp,root/'profil.json')
    return result


def municipality(value):
    return ' '.join(re.sub(r'\b(commune|ville|mairie|municipalite|de|du|la|le)\b',' ',_plain(value)).split())


def for_document(data_dir,collectivite):
    identity=profile(data_dir)
    if not identity['logo']:return {}
    if identity['collectivite'] and municipality(identity['collectivite'])!=municipality(collectivite):
        return {}
    path,mime=logo_file(data_dir,identity['logo'])
    return {'id':identity['logo'],'url':'/api/identite/logo/'+identity['logo'],
            'mime':mime,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
