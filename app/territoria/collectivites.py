# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Identité explicitement nommée dans une demande d'acte."""
import re


COMMUNAL = r'commune|ville|mairie|municipalit[ée]|maire'
INTERCOMMUNAL = r"communaut[ée] (?:de communes|d[’']agglom[ée]ration|urbaine)|m[ée]tropole"
LIAISON = r"(?:de|du|des)[ \t]+|d['’]"
MENTION = re.compile(rf'\b(?P<genre>{INTERCOMMUNAL}|{COMMUNAL})[ \t]+(?P<liaison>{LIAISON})?', re.I)

# Un nom composé peut contenir des traits d'union, des apostrophes et des particules.
# La première minuscule est admise après « commune de » (les demandes peuvent être saisies en minuscules).
LETTRES = r"[^\W\d_]+(?:[-‐‑’'][^\W\d_]+)*"
MAJUSCULE = rf"(?=[A-ZÀ-ÖØ-ÞŒŸ]){LETTRES}"
MINUSCULE = rf"(?=[a-zà-öø-ÿœ]){LETTRES}"
PARTICULE = r"(?i:(?:de la|de|du|des|sur|sous|en|les|le|la|aux)[ \t]+)?(?:[dl]['’])?"
NOM = re.compile(rf"{LETTRES}(?:[ \t]+{PARTICULE}{MAJUSCULE})*")
NOM_MINUSCULE = re.compile(rf"{MINUSCULE}(?:[ \t]+{MINUSCULE})*")
FIN = re.compile(r'\s+(?:qui|dont|portant|accordant|concernant|relati(?:f|ve)|afin|pour|avec|interdisant|'
                 r'autorisant|r[ée]glementant|approuvant|fixant|attribuant|entre|lors|mais|'
                 r'(?:du|au|le)\s+(?=\d|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche))\b', re.I)
NEGATION = re.compile(r"\b(?:pas|plus|ni|non)\s+(?:(?:le|la|du|de|maire|président|présidente)\s+)*$", re.I)
CIBLE = re.compile(rf'\bpar\s+(?=(?:(?:la|le|du|de|président|présidente)\s+)*(?:{INTERCOMMUNAL}|{COMMUNAL})\s)', re.I)


def collectivite(question: str) -> str:
    if re.search(r'\bremplac\w*', question, re.I):
        question = CIBLE.split(question, maxsplit=1)[-1]
    for mention in MENTION.finditer(question):
        genre = mention['genre'].lower()
        liaison = (mention['liaison'] or '').lower()
        suite = question[mention.end():]
        if NEGATION.search(question[:mention.start()]):
            continue
        # Dans « maire de la commune de X », c'est « commune de X » qui porte le nom.
        if re.match(rf"(?:la[ \t]+|l['’])?(?:{COMMUNAL}|{INTERCOMMUNAL})\b[ \t]", suite, re.I):
            continue
        suite = FIN.split(re.split(r'[,;:\n?.!()]', suite, maxsplit=1)[0], maxsplit=1)[0]
        candidat = NOM.match(suite)
        if not candidat:
            continue
        nom = candidat.group()
        if liaison and nom.islower():
            nom = (NOM_MINUSCULE.match(suite) or candidat).group()
        if not liaison and (genre == 'maire' or not nom[0].isupper()):
            continue
        if len(nom) > 100 or nom.casefold() in {'la', 'le', 'les', 'lui', 'de', 'du', 'des'}:
            continue
        if genre == 'maire' and re.match(r"(?:me|vous|nous|bien|lui|ne|se|prendre|signer|faire)\b|[ms]['’]", nom):
            continue  # « demander au maire de bien vouloir signer… »
        if re.fullmatch(COMMUNAL, genre):
            return 'Commune ' + (liaison or 'de ') + nom
        return mention['genre'].capitalize() + ' ' + liaison + nom
    return ''


def lieu_communal(collectivite: str) -> str:
    """Lieu de signature tiré du libellé : « Commune du Val » donne « Le Val »."""
    match = re.fullmatch(rf'\s*(?:commune|ville|mairie)\s+({LIAISON})(.+)', collectivite, re.I)
    if not match:
        return ''
    article = {'du': 'Le ', 'des': 'Les '}.get(match[1].strip().lower(), '')
    return article + match[2].strip()
