# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Recherche par le sens dans les documents des espaces : plongements calculés par le modèle local de LM Studio.

Chaque extrait reçoit un vecteur (normalisé, stocké en float32 dans `chunk_vecteurs`) ; la question aussi, et leur produit
scalaire mesure la proximité de sens. Par défaut tout reste sur le poste : le modèle de plongements tourne dans LM Studio,
comme le modèle de rédaction ; avec l'option Mistral, c'est `mistral-embed`, chez Mistral. Sans modèle disponible, la
recherche par mots-clés continue seule.

L'indexation tourne dans un fil d'arrière-plan lancé par le serveur (`activer`), jamais pendant une requête.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from array import array
from pathlib import Path

import journal
import llm_backend
from config import REGLAGES

from .base.connexion import ouvrir

LOT = 16
PLONGEMENTS_PREFERES = ('mistralai/mistral-embed-2312', 'mistral-embed', 'openai/text-embedding-3-small')
TEXTE_MAX = 1800  # caractères envoyés par extrait : le modèle de plongements lit au plus 2 048 jetons
PAUSE_ECHEC = 60  # secondes sans appel au modèle après un échec
_etat = {'actif': False, 'fil': None, 'echec': 0.0}
_verrou = threading.Lock()


def _fichier(donnees: Path) -> Path:
    return Path(donnees) / 'configuration' / 'recherche.json'


def reglage(donnees: Path) -> str:
    """Modèle de plongements choisi par l'administration ('' : choix automatique).

    Le choix vaut pour le fournisseur d'IA qui l'a vu (un fichier ancien, sans fournisseur : le local). Un passage à Mistral
    ou au local par l'installation, sans l'écran, le remet donc en automatique au lieu d'envoyer un identifiant inconnu."""
    try:
        valeurs = json.loads(_fichier(donnees).read_text(encoding='utf-8'))
        valeur = valeurs.get('modele_plongements', '')
        if valeurs.get('fournisseur', llm_backend.PROVIDER) != llm_backend.read(donnees)['provider']:
            return ''
        return valeur if isinstance(valeur, str) else ''
    except (OSError, ValueError, AttributeError):
        return ''


def enregistrer(donnees: Path, modele) -> None:
    if not isinstance(modele, str) or len(modele) > 200 or any(ord(x) < 32 for x in modele):
        raise ValueError('Modèle de plongements invalide.')
    dossier = _fichier(donnees).parent
    dossier.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporaire = dossier / '.recherche.tmp'
    fournisseur = llm_backend.read(donnees)['provider']
    temporaire.write_text(json.dumps({'modele_plongements': modele.strip(), 'fournisseur': fournisseur}), encoding='utf-8')
    os.chmod(temporaire, 0o600)
    os.replace(temporaire, _fichier(donnees))


def disponibles(donnees: Path) -> list[dict]:
    """Modèles de plongements connus de LM Studio (API /api/v0) ; à défaut, ceux du catalogue dont le nom l'évoque."""
    config = llm_backend.read(donnees)
    if config['provider'] in llm_backend.EN_LIGNE:  # OpenRouter ou Mistral : leur liste, sans l'API /api/v0 de LM Studio
        try:
            return llm_backend.modeles_plongements(donnees)
        except (RuntimeError, ValueError):
            return []
    racine = config['base_url'][:-3] if config['base_url'].endswith('/v1') else config['base_url']
    try:
        liste = llm_backend._request({**config, 'base_url': racine}, '/api/v0/models', timeout=5).get('data', [])
        return [{'id': m['id'], 'charge': m.get('state') == 'loaded'} for m in liste
                if isinstance(m, dict) and m.get('type') == 'embeddings' and isinstance(m.get('id'), str)]
    except (RuntimeError, ValueError):
        pass
    try:
        liste = llm_backend._request(config, '/models', timeout=5).get('data', [])
        return [{'id': m['id'], 'charge': True} for m in liste if isinstance(m, dict) and 'embed' in str(m.get('id', '')).lower()]
    except (RuntimeError, ValueError):
        return []


def modele(donnees: Path) -> str:
    """Modèle utilisé : celui choisi, sinon le premier modèle de plongements chargé dans LM Studio ('' si aucun)."""
    choisi = reglage(donnees)
    if choisi:
        return choisi
    liste = disponibles(donnees)
    ids = {m['id'] for m in liste}
    # Catalogues en ligne (ordre alphabétique, modèles d'images compris) : un modèle de texte connu d'abord.
    prefere = next((m for m in PLONGEMENTS_PREFERES if m in ids), '')
    return prefere or next((m['id'] for m in liste if m['charge']), liste[0]['id'] if liste else '')


def _prefixe(nom: str, question: bool) -> str:
    """Préfixes recommandés par les familles de modèles connues (EmbeddingGemma, Nomic) ; aucun sinon."""
    nom = nom.lower()
    if 'gemma' in nom:
        return 'task: search result | query: ' if question else 'title: none | text: '
    if 'nomic' in nom:
        return 'search_query: ' if question else 'search_document: '
    return ''


def plonger(donnees: Path, nom: str, textes: list[str], question=False) -> list[array]:
    reponse = llm_backend.plongements(donnees, nom, [_prefixe(nom, question) + t[:TEXTE_MAX] for t in textes])
    donnees_vecteurs = sorted(reponse.get('data', []), key=lambda x: x.get('index', 0))
    if len(donnees_vecteurs) != len(textes):
        raise RuntimeError('Réponse du modèle de plongements incomplète.')
    vecteurs = []
    for element in donnees_vecteurs:
        valeurs = element.get('embedding')
        if not isinstance(valeurs, list) or not valeurs:
            raise RuntimeError('Réponse du modèle de plongements invalide.')
        norme = math.sqrt(sum(v * v for v in valeurs)) or 1.0
        vecteurs.append(array('f', (v / norme for v in valeurs)))
    return vecteurs


# Démonstration publique : les extraits des documents d'un visiteur ne partent pas chez le fournisseur d'IA pour être mis en
# vecteurs (aucun plafond par visiteur sur ces appels) ; ses documents se cherchent par mots-clés.
_DE_VISITEURS = ("ch.doc_id IN (SELECT d.id FROM documents d JOIN spaces s ON s.id=d.space_id JOIN utilisateurs u ON u.id=s.proprietaire "
                 "WHERE u.identifiant LIKE 'visiteur-%')")


def indexer(base: Path, donnees: Path, nom: str, limite=LOT, sans_visiteurs=False) -> int:
    """Calcule les vecteurs d'au plus `limite` extraits qui n'en ont pas pour ce modèle ; rend leur nombre."""
    filtre = f' AND NOT {_DE_VISITEURS}' if sans_visiteurs else ''
    with ouvrir(base) as c:
        lignes = c.execute('SELECT ch.id, ch.content FROM chunks ch LEFT JOIN chunk_vecteurs v ON v.chunk_id=ch.id AND v.modele=? '
                           f'WHERE v.chunk_id IS NULL{filtre} ORDER BY ch.id LIMIT ?', (nom, limite)).fetchall()
    if not lignes:
        return 0
    vecteurs = plonger(donnees, nom, [l['content'] for l in lignes])
    with ouvrir(base) as c:
        c.executemany('INSERT OR REPLACE INTO chunk_vecteurs(chunk_id, modele, vecteur) SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM chunks WHERE id=?)',
                      [(l['id'], nom, v.tobytes(), l['id']) for l, v in zip(lignes, vecteurs)])
    return len(lignes)


def etat(base: Path, donnees: Path) -> dict:
    nom = modele(donnees)
    # En démonstration, les extraits des visiteurs ne sont jamais indexés : les compter afficherait une indexation sans fin.
    filtre = f' WHERE NOT {_DE_VISITEURS}' if REGLAGES.demonstration else ''
    with ouvrir(base) as c:
        total = c.execute('SELECT COUNT(*) FROM chunks ch' + filtre).fetchone()[0]
        faits = c.execute('SELECT COUNT(*) FROM chunk_vecteurs WHERE modele=?', (nom,)).fetchone()[0] if nom else 0
    return {'modele': nom, 'choisi': reglage(donnees), 'extraits': total, 'indexes': faits,
            'en_cours': bool(_etat['fil'] and _etat['fil'].is_alive())}


def _boucle(base: Path, donnees: Path) -> None:
    try:
        nom = modele(donnees)
        while nom and indexer(base, donnees, nom, sans_visiteurs=REGLAGES.demonstration):
            pass
    except Exception as erreur:  # modèle absent ou arrêté : on réessaiera au prochain import ou redémarrage
        _etat['echec'] = time.monotonic()
        journal.evenement('semantique:indexation', 'echec', code=type(erreur).__name__.lower())


def activer() -> None:
    """Autorise l'indexation en arrière-plan (appelé par le serveur ; les tests n'appellent pas le modèle)."""
    _etat['actif'] = True


def lancer(base: Path, donnees: Path) -> None:
    if not _etat['actif']:
        return
    with _verrou:
        if _etat['fil'] and _etat['fil'].is_alive():
            return
        _etat['fil'] = threading.Thread(target=_boucle, args=(base, donnees), daemon=True, name='indexation-semantique')
        _etat['fil'].start()


def similarites(base: Path, donnees: Path, question: str, identifiants) -> dict[int, float]:
    """Proximité de sens (cosinus) entre la question et les extraits indexés ; vide sans modèle ou sans index."""
    identifiants = list(identifiants)
    if not identifiants or time.monotonic() - _etat['echec'] < PAUSE_ECHEC:
        return {}
    with ouvrir(base) as c:
        if not c.execute('SELECT 1 FROM chunk_vecteurs LIMIT 1').fetchone():
            return {}
    try:
        nom = modele(donnees)
        if not nom:
            return {}
        requete = plonger(donnees, nom, [question], question=True)[0]
    except (RuntimeError, ValueError):
        _etat['echec'] = time.monotonic()
        return {}
    resultat = {}
    with ouvrir(base) as c:
        for debut in range(0, len(identifiants), 900):
            tranche = identifiants[debut:debut + 900]
            for ligne in c.execute('SELECT chunk_id, vecteur FROM chunk_vecteurs WHERE modele=? AND chunk_id IN (' + ','.join('?' * len(tranche)) + ')',
                                   (nom, *tranche)):
                vecteur = array('f')
                vecteur.frombytes(ligne['vecteur'])
                if len(vecteur) == len(requete):
                    resultat[ligne['chunk_id']] = sum(a * b for a, b in zip(requete, vecteur))
    return resultat
