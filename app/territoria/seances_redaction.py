# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Rédaction par le modèle local de la teneur des discussions (procès-verbal) et du compte rendu d'une séance.

Le modèle ne reçoit que les passages transcrits ; sa réponse suit un schéma JSON strict et passe des garde-fous : aucun
nombre absent des passages (remplacé par « [à compléter] » et signalé), aucun nom de personne absent des passages (même
traitement), aucune référence juridique qui n'y figure pas (réponse inutilisable, rejouée une fois).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import llm_backend

from .deliberations import A_COMPLETER, MESSAGE_REPONSE_INVALIDE, _nombres_connus, remplacer_nombres_inconnus

VIGILANCE_NOMBRE = 'Nombre absent des passages remplacé par [à compléter].'
VIGILANCE_NOM = 'Nom absent des passages remplacé par [à compléter].'
VIGILANCE_REFERENCE = 'Référence juridique absente des passages remplacée par [à compléter].'
# Heure placée en tête de chaque passage (« [00:12:34] Nom : texte ») : ses chiffres ne sont pas des nombres dits en séance.
_HEURE_PASSAGE = re.compile(r'\[\d{2}:\d{2}:\d{2}\]')
# Référence juridique comparée en entier : article numéroté (tous ses segments), loi, décret ou ordonnance numérotés, code
# général nommé. « La loi ne laisse pas le choix » n'en est pas une.
_REFERENCE = re.compile(r'\b(?:articles?\s+)?[LRD]\s*\.?\s*\d{1,4}(?:\s*[-\u2011\u2013]\s*\d+)+'
                        r'|\b(?:loi|d[ée]cret|ordonnance)\s+n\s*[°o]\s*\.?\s*\d+(?:\s*[-\u2011\u2013]\s*\d+)*'
                        r'|\bcode\s+g[ée]n[ée]ral\s+(?:des|de\s+la|du)\s+\w+(?:\s+\w+)?', re.IGNORECASE)
# Une personne désignée par sa civilité : « M. Durand », « Mme Le Gall », « Monsieur le Maire ».
_PERSONNE = re.compile(r"\b(?:M\.|Mme|Monsieur|Madame)\s+((?:(?:le|la|de|du|des)\s+)?[A-ZÀ-ÖØ-Þ][\w'’-]*"
                       r"(?:\s+[A-ZÀ-ÖØ-Þ][\w'’-]*)?)")


def _sans_accents(texte: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', texte) if not unicodedata.combining(c)).casefold()


def _compact(texte: str) -> str:
    """Forme comparable d'une référence : sans accents, sans « article », espaces, points ni traits d'union."""
    return re.sub(r'article|[\s.\-‑–]', '', _sans_accents(texte))


def nettoyer(texte: str, source: str) -> tuple[str, list[str]]:
    """Garde-fous appliqués à un texte du modèle, `source` étant les passages fournis : référence juridique, nom de personne
    ou nombre absents des passages remplacés par « [à compléter] », chacun signalé par une vigilance."""
    texte = re.sub(r'\*\*|__', '', texte.strip())
    dit = _HEURE_PASSAGE.sub(' ', source)
    citees = {_compact(m.group(0)) for m in _REFERENCE.finditer(dit)}
    vigilances = []

    def reference(m):
        if _compact(m.group(0)) in citees:
            return m.group(0)
        if VIGILANCE_REFERENCE not in vigilances:
            vigilances.append(VIGILANCE_REFERENCE)
        return A_COMPLETER
    texte = _REFERENCE.sub(reference, texte)
    source_simple = _sans_accents(source)

    def personne(m):
        if re.search(r'\b' + re.escape(_sans_accents(m.group(1))) + r'\b', source_simple):
            return m.group(0)
        if VIGILANCE_NOM not in vigilances:
            vigilances.append(VIGILANCE_NOM)
        return A_COMPLETER
    texte = _PERSONNE.sub(personne, texte)
    texte, remplace = remplacer_nombres_inconnus(texte, _nombres_connus(dit))
    if remplace:
        vigilances.insert(0, VIGILANCE_NOMBRE)
    return texte, vigilances


def lire_reponse(completion: dict, cles: tuple[str, ...]) -> dict:
    """Réponse JSON du modèle (clôtures ``` retirées) dont les clés sont exactement `cles` ; sinon ValueError."""
    try:
        contenu = completion['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(MESSAGE_REPONSE_INVALIDE) from exc
    if not isinstance(contenu, str) or len(contenu) > 40000:
        raise ValueError(MESSAGE_REPONSE_INVALIDE)
    contenu = re.sub(r'^```(?:json)?\s*|\s*```$', '', contenu.strip(), flags=re.IGNORECASE)
    try:
        resultat = json.loads(contenu)
    except json.JSONDecodeError as exc:
        raise ValueError('La réponse du modèle ne respecte pas la structure attendue.') from exc
    if not isinstance(resultat, dict) or set(resultat) != set(cles):
        raise ValueError('La réponse du modèle ne respecte pas la structure attendue.')
    return resultat


# --- Appels au modèle --------------------------------------------------------------------------------------------------

TAILLE_MORCEAU = 12000  # caractères : tient dans le contexte réglé de Gemma (12 576 jetons) avec la consigne et la réponse
CONSIGNE_TENEUR = ("Tu rédiges la teneur des discussions d'un point d'une séance du conseil municipal, pour son procès-verbal. "
                   'À partir des seuls passages fournis, résume les échanges en un à huit paragraphes courts, selon la longueur '
                   'des échanges, au style '
                   'indirect, neutres et fidèles (« M. le Maire présente… », « Mme X demande… », « Il lui est répondu… »), sans '
                   'reprendre les passages mot à mot. N\'ajoute aucun fait, nombre, nom '
                   'ni référence juridique absent des passages ; n\'écris ni le vote ni la décision. Réponds uniquement en JSON.')
CONSIGNE_COMPTE_RENDU = ("Tu rédiges le compte rendu d'une réunion à partir des seuls passages fournis : points abordés "
                         '(titre, synthèse de quelques phrases), décisions prises, actions (qui, quoi, échéance seulement si elle est dite, sinon '
                         "chaîne vide). N'ajoute rien qui ne figure pas dans les passages. Réponds uniquement en JSON.")
CONSIGNE_FUSION_COMPTE_RENDU = ('Fusionne ces comptes rendus partiels, rédigés morceau par morceau pour une même réunion, en un '
                                'seul compte rendu, sans rien ajouter. Réponds uniquement en JSON.')
# Ajouté à la consigne quand la première réponse est inutilisable : le plus souvent coupée net à la limite de jetons
# (Gemma paraphrase tout un long point ; constaté à la recette du lot 3).
RAPPEL_BREF = ('Ta réponse précédente était inutilisable (trop longue ou mal formée) : sois plus bref et réponds uniquement en '
               'JSON valide.')
_TEXTE = {'type': 'string'}
PARAGRAPHES_MAX = 40  # la consigne en demande un à huit ; Gemma en rend plus sur un long morceau (recette du lot 3)
SCHEMA_TENEUR = {'type': 'object', 'properties': {'paragraphes': {'type': 'array', 'minItems': 1, 'maxItems': PARAGRAPHES_MAX,
                                                                   'items': _TEXTE}},
                 'required': ['paragraphes'], 'additionalProperties': False}
SCHEMA_COMPTE_RENDU = {'type': 'object', 'properties': {
    'points': {'type': 'array', 'items': {'type': 'object', 'properties': {'titre': _TEXTE, 'synthese': _TEXTE},
                                          'required': ['titre', 'synthese'], 'additionalProperties': False}},
    'decisions': {'type': 'array', 'items': _TEXTE},
    'actions': {'type': 'array', 'items': {'type': 'object', 'properties': {'qui': _TEXTE, 'quoi': _TEXTE, 'echeance': _TEXTE},
                                           'required': ['qui', 'quoi', 'echeance'], 'additionalProperties': False}}},
    'required': ['points', 'decisions', 'actions'], 'additionalProperties': False}
_ELEMENTS_MAX = 40


class EchecRedaction(Exception):
    """Rédaction impossible : `code` vaut « modele » (serveur injoignable ou réglages changés) ou « reponse » (deux réponses
    inutilisables de suite)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def morceaux(passages: list[str], taille: int = TAILLE_MORCEAU) -> list[list[str]]:
    """Passages regroupés en morceaux d'au plus `taille` caractères, sans jamais couper un passage."""
    groupes, courant, longueur = [], [], 0
    for passage in passages:
        if courant and longueur + len(passage) + 1 > taille:
            groupes.append(courant)
            courant, longueur = [], 0
        courant.append(passage)
        longueur += len(passage) + 1
    if courant:
        groupes.append(courant)
    return groupes


def _chaine(valeur, longueur_max: int = 4000, vide: bool = False) -> str:
    if not isinstance(valeur, str) or len(valeur) > longueur_max or (not vide and not valeur.strip()):
        raise ValueError('La réponse du modèle ne respecte pas la structure attendue.')
    return valeur


class _Nettoyage:
    """Garde-fous appliqués à chaque texte d'une réponse ; vigilances rassemblées sans doublon."""

    def __init__(self, source: str):
        self.source, self.vigilances = source, []

    def __call__(self, texte: str) -> str:
        propre, vigilances = nettoyer(texte, self.source)
        self.vigilances += [v for v in vigilances if v not in self.vigilances]
        return propre


def _forme_teneur(reponse: dict, propre: _Nettoyage) -> dict:
    paragraphes = reponse['paragraphes']
    if not isinstance(paragraphes, list) or not 1 <= len(paragraphes) <= PARAGRAPHES_MAX:
        raise ValueError('La réponse du modèle ne respecte pas la structure attendue.')
    return {'paragraphes': [propre(_chaine(p, 12000)) for p in paragraphes]}


def _forme_compte_rendu(reponse: dict, propre: _Nettoyage) -> dict:
    points, decisions, actions = reponse['points'], reponse['decisions'], reponse['actions']
    if not all(isinstance(x, list) and len(x) <= _ELEMENTS_MAX for x in (points, decisions, actions)):
        raise ValueError('La réponse du modèle ne respecte pas la structure attendue.')
    if any(not isinstance(p, dict) or set(p) != {'titre', 'synthese'} for p in points) or \
            any(not isinstance(a, dict) or set(a) != {'qui', 'quoi', 'echeance'} for a in actions):
        raise ValueError('La réponse du modèle ne respecte pas la structure attendue.')
    return {'points': [{'titre': propre(_chaine(p['titre'], 300)), 'synthese': propre(_chaine(p['synthese']))} for p in points],
            'decisions': [propre(_chaine(d)) for d in decisions],
            'actions': [{'qui': propre(_chaine(a['qui'], 300)), 'quoi': propre(_chaine(a['quoi'])),
                         'echeance': propre(_chaine(a['echeance'], 300, vide=True))} for a in actions]}


def _appeler(donnees: Path, consigne: str, contenu: dict, nom_schema: str, schema: dict, forme, source: str,
             max_tokens: int) -> tuple[dict, list[str]]:
    """Un appel au modèle, réponse au schéma strict passée par `forme` et les garde-fous ; une réponse inutilisable est
    rejouée une fois. Rend la réponse nettoyée et ses vigilances."""
    try:
        modele = llm_backend.require_configured(donnees)['model']
    except (RuntimeError, ValueError, KeyError, TypeError):  # réglages IA absents ou invalides
        raise EchecRedaction('modele') from None
    commun = {'model': modele,
              'temperature': 0.1, 'max_tokens': max_tokens,
              'response_format': {'type': 'json_schema', 'json_schema': {'name': nom_schema, 'strict': True, 'schema': schema}}}
    cles = tuple(schema['required'])
    for essai in (1, 2):
        requete = dict(commun, messages=[{'role': 'system', 'content': consigne if essai == 1 else consigne + ' ' + RAPPEL_BREF},
                                         {'role': 'user', 'content': json.dumps(contenu, ensure_ascii=False)}])
        try:
            completion = llm_backend.chat(donnees, requete)
        except (RuntimeError, ValueError):
            raise EchecRedaction('modele') from None
        propre = _Nettoyage(source)
        try:
            return forme(lire_reponse(completion, cles), propre), propre.vigilances
        except ValueError:
            if essai == 2:
                raise EchecRedaction('reponse') from None


def _ajouter(vigilances: list[str], autres: list[str]) -> None:
    vigilances += [v for v in autres if v not in vigilances]


def _grouper(elements: list[dict], taille: int = TAILLE_MORCEAU) -> list[list[dict]]:
    """Résultats partiels regroupés pour la fusion : au plus `taille` caractères par groupe quand c'est possible, et toujours
    au moins deux éléments par groupe, pour que chaque tour de fusion en réduise le nombre."""
    groupes, courant, longueur = [], [], 0
    for element in elements:
        mesure = len(json.dumps(element, ensure_ascii=False))
        if len(courant) >= 2 and longueur + mesure > taille:
            groupes.append(courant)
            courant, longueur = [], 0
        courant.append(element)
        longueur += mesure
    if len(courant) == 1 and groupes:
        groupes[-1].append(courant[0])
    elif courant:
        groupes.append(courant)
    return groupes


def _fusionner(donnees: Path, elements: list[dict], consigne: str, contenu, nom_schema: str, schema: dict, forme, source: str,
               max_tokens: int, vigilances: list[str]) -> dict:
    """Fusion par groupes qui tiennent dans le contexte du modèle, répétée jusqu'à un seul résultat."""
    while len(elements) > 1:
        suivants = []
        for groupe in _grouper(elements):
            resultat, trouvees = _appeler(donnees, consigne, contenu(groupe), nom_schema, schema, forme, source, max_tokens)
            suivants.append(resultat)
            _ajouter(vigilances, trouvees)
        elements = suivants
    return elements[0]


def rediger_teneur(donnees: Path, passages: list[str]) -> dict:
    """Teneur des discussions d'un point : un appel par morceau, paragraphes repris dans l'ordre des morceaux. Pas de fusion
    par le modèle : sur un long point, il recopiait tout en un bloc coupé à la limite de jetons (recette du lot 3)."""
    groupes = morceaux(passages)
    if not groupes:
        return {'paragraphes': [A_COMPLETER], 'vigilances': []}
    source, vigilances, paragraphes = '\n'.join(passages), [], []
    for groupe in groupes:
        teneur, trouvees = _appeler(donnees, CONSIGNE_TENEUR, {'passages': groupe}, 'teneur_point', SCHEMA_TENEUR,
                                    _forme_teneur, source, 3000)
        paragraphes += teneur['paragraphes']
        _ajouter(vigilances, trouvees)
    return {'paragraphes': paragraphes, 'vigilances': vigilances}


def rediger_compte_rendu(donnees: Path, passages: list[str]) -> dict:
    """Compte rendu : un compte rendu partiel par morceau, fusionnés par un dernier appel s'il y a plusieurs morceaux."""
    groupes = morceaux(passages)
    if not groupes:
        return {'points': [], 'decisions': [], 'actions': [], 'vigilances': []}
    source, vigilances, partiels = '\n'.join(passages), [], []
    for groupe in groupes:
        partiel, trouvees = _appeler(donnees, CONSIGNE_COMPTE_RENDU, {'passages': groupe}, 'compte_rendu', SCHEMA_COMPTE_RENDU,
                                     _forme_compte_rendu, source, 3000)
        partiels.append(partiel)
        _ajouter(vigilances, trouvees)
    compte_rendu = _fusionner(donnees, partiels, CONSIGNE_FUSION_COMPTE_RENDU, lambda groupe: {'comptes_rendus': groupe},
                              'compte_rendu', SCHEMA_COMPTE_RENDU, _forme_compte_rendu, source, 3000, vigilances)
    return dict(compte_rendu, vigilances=vigilances)
