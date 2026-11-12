# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Articles de Légifrance livrés avec l'application, versés dans l'espace « Fonds publics ».

Les copies datées de `ressources/*-reference.json` servent déjà aux visas des actes. Versées dans le fonds public, elles
donnent aux questions avec sources de quoi répondre dès l'installation, hors ligne compris. `fonds-publics-reference.json`
ajoute les articles qui ne servent à aucun visa (fonctionnement du conseil municipal).

Un article est un document, d'identifiant stable. Ce qui a été versé est noté dans `parametres` : un article supprimé par
la collectivité ne revient pas, et une copie livrée plus récente remplace l'ancienne tant que le document est resté celui
qui avait été versé.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path

RESSOURCES = Path(__file__).resolve().parent / 'ressources'
ESPACE = 'public'
PREFIXE = 'legifrance-'  # identifiants réservés : ceux des documents importés sont tirés au hasard
CLE = 'fonds_publics_verses'
_URL = re.compile(r'https://www\.legifrance\.gouv\.fr/codes/article_lc/LEGIARTI\d+')
_ARTICLE = re.compile(r'[A-Z*]{0,2}\d[\w-]*(?: [A-Z]{1,2})?')


def _valide(source) -> bool:
    if not isinstance(source, dict) or source.get('status') != 'ok':
        return False
    code, article, texte, jour = (source.get(cle) for cle in ('code', 'article', 'text', 'as_of'))
    if not all(isinstance(valeur, str) and valeur.strip() for valeur in (code, article, texte, jour)):
        return False
    try:
        date.fromisoformat(jour)
    except ValueError:
        return False
    return bool(_ARTICLE.fullmatch(article) and _URL.fullmatch(str(source.get('url', '')))
                and hashlib.sha256(texte.encode()).hexdigest() == source.get('sha256'))


@lru_cache(maxsize=4)
def articles(dossier: Path = RESSOURCES) -> tuple[dict, ...]:
    """Les articles livrés dont l'empreinte est juste, un par code et numéro (la copie la plus récente), triés."""
    retenus: dict[tuple[str, str], dict] = {}
    for fichier in sorted(dossier.glob('*-reference.json')):
        try:
            sources = json.loads(fichier.read_text(encoding='utf-8')).get('sources')
        except (OSError, ValueError, AttributeError):
            continue
        for source in sources if isinstance(sources, list) else ():
            if not _valide(source):
                continue
            cle = (source['code'], source['article'])
            if cle not in retenus or source['as_of'] > retenus[cle]['as_of']:
                retenus[cle] = source
    return tuple(retenus[cle] for cle in sorted(retenus))


def identifiant(code: str, article: str) -> str:
    sans_accent = ''.join(c for c in unicodedata.normalize('NFKD', f'{code} {article}'.casefold()) if not unicodedata.combining(c))
    return PREFIXE + re.sub(r'[^a-z0-9]+', '-', sans_accent).strip('-')


def _contenu(source: dict) -> bytes:
    """Le fichier du document : titre, date de la copie, puis le texte sans le numéro que la copie livrée porte en tête."""
    texte = source['text'].strip()
    if texte.startswith(source['article'] + '\n'):
        texte = texte[len(source['article']):].strip()
    jour = date.fromisoformat(source['as_of']).strftime('%d/%m/%Y')
    return (f"{source['code']} — Article {source['article']}\nCopie du {jour} (Légifrance) : vérifiez que le texte est "
            f"toujours en vigueur.\n\n{texte}\n").encode('utf-8')


def verser(c, fichiers: Path, maintenant: str, decouper) -> int:
    """Verse dans le fonds public les articles livrés qui n'y ont jamais été versés, et remplace ceux dont la copie livrée
    a changé. `c` : connexion ouverte sur la base ; `decouper` : découpage d'un texte en extraits. Renvoie le nombre de
    documents écrits."""
    ligne = c.execute('SELECT valeur FROM parametres WHERE cle=?', (CLE,)).fetchone()
    try:
        verses = json.loads(ligne['valeur']) if ligne else {}
    except ValueError:
        verses = {}
    if not isinstance(verses, dict):
        verses = {}
    ecrits = 0
    for source in articles():
        ident = identifiant(source['code'], source['article'])
        corps = _contenu(source)
        empreinte = hashlib.sha256(corps).hexdigest()
        if verses.get(ident) == empreinte:
            continue
        existant = c.execute('SELECT sha256 FROM documents WHERE id=?', (ident,)).fetchone()
        if ident in verses:  # copie livrée plus récente : seulement si le document est resté celui qui avait été versé
            if not existant or existant['sha256'] != verses[ident]:
                continue
            c.execute('DELETE FROM chunk_vecteurs WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)', (ident,))
            c.execute('DELETE FROM chunks WHERE doc_id=?', (ident,))
            c.execute('DELETE FROM documents WHERE id=?', (ident,))
        elif existant:
            continue
        chemin = fichiers / f'{ident}.txt'
        chemin.write_bytes(corps)
        os.chmod(chemin, 0o600)
        c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)',
                  (ident, ESPACE, f"{source['code']} — article {source['article']}", chemin.name, empreinte, source['url'],
                   source['as_of'], maintenant))
        c.executemany('INSERT INTO chunks(doc_id,page,content) VALUES (?,?,?)',
                      [(ident, None, extrait) for extrait in decouper(corps.decode('utf-8'))])
        verses[ident] = empreinte
        ecrits += 1
    if ecrits:
        c.execute('INSERT INTO parametres(cle,valeur,modifie) VALUES (?,?,?) '
                  'ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur,modifie=excluded.modifie',
                  (CLE, json.dumps(verses, ensure_ascii=False, sort_keys=True), maintenant))
    return ecrits
