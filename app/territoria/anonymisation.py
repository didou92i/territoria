# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Repérage des données personnelles d'un acte avant publication, et masquage.

Deux sources de propositions : des règles sûres (courriel, téléphone, adresse, date de naissance, n° de sécurité sociale,
IBAN, immatriculation) et l'IA pour les noms de personnes, gardés seulement s'ils figurent mot pour mot dans l'acte.
Rien n'est masqué sans la validation d'un agent (publication.py) ; un passage coché l'est à toutes ses occurrences."""
from __future__ import annotations

import json
import re
from typing import Callable

LIBELLES = {'nom': '[nom masqué]', 'adresse': '[adresse masquée]', 'telephone': '[téléphone masqué]',
            'courriel': '[courriel masqué]', 'naissance': '[date de naissance masquée]',
            'nir': '[n° de sécurité sociale masqué]', 'iban': '[IBAN masqué]', 'immatriculation': '[immatriculation masquée]',
            'manuel': '[donnée masquée]'}
AVERTISSEMENT_IA = 'Les noms n’ont pas été repérés automatiquement : relisez l’acte en entier.'
ROLES = ('particulier', 'elu', 'agent')
MORCEAU_IA = 8000  # caractères envoyés par demande à l'IA

_MOIS = r'(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)'
_VOIE = r'(?i:rue|avenue|av\.|boulevard|bd|place|chemin|allée|allee|impasse|route|quai|cours|square|passage|sentier|voie|résidence|residence|lotissement|hameau)'
_MOT_PROPRE = r"[A-ZÀ-ÖØ-Þ][\wÀ-ÿ’'-]*"
REGLES = (
    # Bornes et début de mot : sans elles, une longue suite de lettres sans « @ » coûtait un temps quadratique.
    ('courriel', re.compile(r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}')),
    ('telephone', re.compile(r'(?<![\w.])(?:\+33\s?[1-9]|0[1-9])(?:[\s.-]?\d{2}){4}(?![\w])')),
    # Jamais à travers un retour à la ligne : l'objet et chaque paragraphe sont des blocs distincts.
    ('adresse', re.compile(rf"\b\d{{1,4}}(?:[ \u00a0]?(?i:bis|ter|quater))?,?[ \u00a0]+{_VOIE}[ \u00a0]+"
                           rf"(?:(?i:de[ \u00a0]+la|de[ \u00a0]+l’|de[ \u00a0]+l'|de|du|des|d’|d'|l’|l'|la|le|les)[ \u00a0]*)?"
                           rf"{_MOT_PROPRE}(?:[ \u00a0-]+{_MOT_PROPRE})*")),
    ('naissance', re.compile(rf'(?i)\bnée?\s+le\s+(?:\d{{1,2}}(?:er)?\s+{_MOIS}\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})')),
    ('nir', re.compile(r'(?<![\d])[12]\s?\d{2}\s?(?:0[1-9]|1[0-2]|[2-9]\d)\s?(?:\d{2}|2A|2B)\s?\d{3}\s?\d{3}\s?\d{2}(?![\d])')),
    ('iban', re.compile(r'\bFR\d{2}(?:\s?[0-9A-Z]{4}){5}\s?[0-9A-Z]{3}\b')),
    ('immatriculation', re.compile(r'\b[A-HJ-NP-TV-Z]{2}-\d{3}-[A-HJ-NP-TV-Z]{2}\b')),
)


# Une adresse n'est cochée d'office que si elle suit une personne (« demeurant », « M. ») : celle qui fait l'objet d'un
# arrêté de police (« au droit du 4 rue de la République ») reste visible, sauf décision de l'agent.
_PERSONNE_AVANT = re.compile(r"(?i)(?:demeurant|domicilié|domiciliée|résidant|résidante|habitant|habitante|\bM\.|\bMme\b|monsieur|madame)[^.;:]{0,60}$")


class IAIndisponible(Exception):
    """L'IA n'a pas pu repérer les noms : les règles seules s'appliquent, avec un avertissement."""


def regles(texte: str) -> list[dict]:
    """Passages repérés par les règles, bloc par bloc (une ligne = l'objet ou un paragraphe), dans l'ordre du texte ;
    cochés d'office, sauf une adresse qui ne suit pas une personne."""
    trouves, decalage = [], 0
    for bloc in texte.split('\n'):
        for categorie, motif in REGLES:
            for m in motif.finditer(bloc):
                coche = categorie != 'adresse' or bool(_PERSONNE_AVANT.search(bloc[:m.start()]))
                trouves.append((decalage + m.start(), {'texte': m.group(0).strip(), 'categorie': categorie, 'origine': 'regle',
                                                       'coche': coche}))
        decalage += len(bloc) + 1
    return [p for _, p in sorted(trouves, key=lambda x: x[0])]


def _requete(morceau: str) -> dict:
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['personnes'],
              'properties': {'personnes': {'type': 'array', 'items': {
                  'type': 'object', 'additionalProperties': False, 'required': ['texte', 'role'],
                  'properties': {'texte': {'type': 'string'}, 'role': {'type': 'string', 'enum': list(ROLES)}}}}}}
    consigne = ('Tu relis un acte administratif avant sa publication. Liste les noms de personnes physiques qui y figurent, '
                'recopiés exactement comme dans le texte (prénom et nom tels qu’écrits, sans civilité). Rôle : « elu » pour '
                'un élu (maire, adjoint, conseiller), « agent » pour un agent de la collectivité dans ses fonctions, '
                '« particulier » pour toute autre personne. N’invente aucun nom ; liste vide s’il n’y en a pas. '
                'Ignore les noms de rues, de lieux, d’entreprises et d’institutions.')
    return {'messages': [{'role': 'system', 'content': consigne}, {'role': 'user', 'content': morceau}],
            'temperature': 0, 'max_tokens': 2000,
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'personnes', 'strict': True, 'schema': schema}}}


def noms_ia(texte: str, demander: Callable[[dict], dict]) -> list[dict]:
    """Noms de personnes proposés par l'IA ; un particulier est coché, un élu ou un agent décoché."""
    passages, vus = [], set()
    for debut in range(0, len(texte), MORCEAU_IA):
        try:
            reponse = demander(_requete(texte[debut:debut + MORCEAU_IA]))
            personnes = json.loads(reponse['choices'][0]['message']['content'])['personnes']
        except (RuntimeError, ValueError, KeyError, IndexError, TypeError) as erreur:
            raise IAIndisponible(str(erreur)) from None
        for personne in personnes if isinstance(personnes, list) else []:
            nom = ' '.join(personne.get('texte', '').split()) if isinstance(personne, dict) else ''
            role = personne.get('role') if isinstance(personne, dict) else None
            if len(nom) < 3 or role not in ROLES or nom.lower() in vus or not occurrences(texte, {'texte': nom, 'categorie': 'nom'}):
                continue  # un nom absent du texte est une invention de l'IA
            vus.add(nom.lower())
            passages.append({'texte': nom, 'categorie': 'nom', 'origine': 'ia', 'coche': role == 'particulier'})
            famille = nom.split()[-1] if len(nom.split()) > 1 else ''
            if len(famille) >= 3 and famille.lower() not in vus:  # « M. MARTIN » plus bas, après « Jean Martin »
                vus.add(famille.lower())
                passages.append({'texte': famille, 'categorie': 'nom', 'origine': 'ia', 'coche': role == 'particulier'})
    return passages


def reperer(texte: str, demander: Callable[[dict], dict]) -> tuple[list[dict], str | None]:
    """Règles et IA réunies, sans doublon (même texte, sans tenir compte de la casse), dans l'ordre du texte ; avec
    l'avertissement quand l'IA n'a pas pu repérer les noms."""
    avertissement, par_regles = None, regles(texte)
    try:
        proposes = par_regles + noms_ia(texte, demander)
    except IAIndisponible:
        proposes, avertissement = par_regles, AVERTISSEMENT_IA
    uniques, vus = [], set()
    for passage in proposes:
        if passage['texte'].lower() not in vus:
            vus.add(passage['texte'].lower())
            uniques.append(passage)
    position = {p['texte']: texte.lower().find(p['texte'].lower()) for p in uniques}
    return sorted(uniques, key=lambda p: position[p['texte']]), avertissement


def _motif(passage: str) -> re.Pattern:
    """Toutes les occurrences en mots entiers, sans tenir compte de la casse ni des espaces (texte extrait d'un PDF)."""
    return re.compile(r'(?<!\w)' + r'\s+'.join(re.escape(mot) for mot in passage.split()) + r'(?!\w)', re.IGNORECASE)


def _zones(texte: str, passage: dict):
    """Occurrences d'un passage ; un nom d'un seul mot n'est pris qu'avec sa majuscule (« Petit », pas « le petit
    patrimoine »)."""
    seul_nom = passage.get('categorie') == 'nom' and len(passage.get('texte', '').split()) == 1
    for m in _motif(passage.get('texte', '')).finditer(texte) if passage.get('texte', '').strip() else []:
        if not (seul_nom and m.group(0)[:1].islower()):
            yield m


def occurrences(texte: str, passage: dict) -> int:
    """Nombre d'endroits où le passage serait masqué ; 0 : il ne masquerait rien (à refuser avant validation)."""
    return sum(1 for _ in _zones(texte, passage))


def masquer(texte: str, passages: list[dict]) -> str:
    """Remplace chaque passage coché, partout ; des passages qui se chevauchent sont fusionnés en un seul libellé, celui
    du passage le plus long."""
    zones = []
    for passage in passages:
        if passage.get('coche'):
            for m in _zones(texte, passage):
                zones.append([m.start(), m.end(), len(passage['texte']), passage['categorie']])
    zones.sort()
    fusion = []
    for debut, fin, longueur, categorie in zones:
        if fusion and debut < fusion[-1][1]:
            precedente = fusion[-1]
            precedente[1] = max(precedente[1], fin)
            if longueur > precedente[2]:
                precedente[2], precedente[3] = longueur, categorie
        else:
            fusion.append([debut, fin, longueur, categorie])
    morceaux, curseur = [], 0
    for debut, fin, _, categorie in fusion:
        morceaux += [texte[curseur:debut], LIBELLES.get(categorie, LIBELLES['manuel'])]
        curseur = fin
    return ''.join(morceaux) + texte[curseur:]
