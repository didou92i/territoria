# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Reprise des dates, effectifs et voix explicitement indiqués pour une délibération."""
import re


_DATE = (r"(?:\d{1,2}(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|"
         r"septembre|octobre|novembre|d[ée]cembre)\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})")
_DATES = re.compile(rf"\b(?P<label>s[ée]ance|convocation|convoqu[ée]e?s?)\s*(?:[:=]\s*)?(?:(?:du|le)\s+)?(?P<date>{_DATE})\b", re.I)
_MEMBRES = r"(?:(?:membres?|[ée]lue?s?|conseill(?:ers?|ères?)(?:\s+municip(?:al|aux|ales?))?)\s+)?"
_LIBELLES = {
    'exercice': _MEMBRES + r"en exercice", 'presents': _MEMBRES + r"(?:physiquement\s+)?pr[ée]sente?s?",
    'pouvoirs': r"pouvoirs?|procurations?", 'votants': _MEMBRES + r"votante?s?",
    'pour': r"(?:voix\s+)?pour", 'contre': r"(?:voix\s+)?contre", 'abstentions': r"abstentions?",
}
# Un nombre ne peut pas commencer au milieu d'une date, d'un décimal ou d'un nombre signé.
_NOMBRE = r"(?<![\w+\-])(?<!\d[.,/])(?<!\d[ \u00a0\u202f])(?:\d{1,3}[ \u00a0\u202f]\d{3}|\d{1,4})\b(?![.,/ \u00a0\u202f]\d)"
_COMPTES = {
    cle: re.compile(rf"\b(?:{libelle})\s*[:=]\s*(?P<apres>{_NOMBRE})|"
                    rf"(?P<avant>{_NOMBRE})[^\S\r\n]+(?P<label>{libelle})\b(?!\s*[:=])", re.I)
    for cle, libelle in _LIBELLES.items()
}
_FIN_ITEM = re.compile(r"^[^\S\r\n]*(?:$|[,.;:/)\n]|(?:et|soit|dont|au lieu de)\b|sur\s+\d+\b|"
                       r"\d+\s+(?:voix|pour|contre|abstentions?)\b(?!-))", re.I)
_POUVOIRS_SEANCE = re.compile(r"^\s+(?:re[çc]us?|donn[ée]s?|enregistr[ée]s?|de\s+(?:vote|repr[ée]sentation)|au total)\b", re.I)
_VALEUR_ECARTEE = re.compile(r"\b(?:au lieu de|et non(?: pas)?|plutôt que)\s*$", re.I)
_REMPLACEMENT = re.compile(rf"^\s+par\s+({_NOMBRE})(?=\s*(?:$|[.,;)]|et\b))", re.I)
_UNANIMITE = re.compile(r"\b(?:votes?\s*[:=]?\s*(?:à\s+l[’'])?|(?:adopt[ée]e?|vot[ée]e?)\s+à\s+l[’'])unanimit[ée]\b", re.I)
_VOIX = ('pour', 'contre', 'abstentions')


def relever(texte: str, precedentes: dict | None = None) -> dict:
    """La dernière valeur étiquetée d'un champ remplace celle du projet précédent."""
    mentions = dict(precedentes or {})
    for m in _DATES.finditer(texte):
        cle = 'convocation' if m['label'].lower().startswith('conv') else 'seance'
        mentions[cle] = m['date']

    # Traiter les mentions dans l'ordre pour pouvoir corriger aussi un vote à l'unanimité.
    valeurs = []
    for m in _UNANIMITE.finditer(texte):
        niee = re.search(r"\b(?:pas|plus|jamais)(?:\s+été)?\s*$", texte[:m.start()], re.I)
        valeurs.append((m.start(), 'unanimite', not niee))
    for cle, motif in _COMPTES.items():
        for m in motif.finditer(texte):
            avant, suite = texte[:m.start()], texte[m.end():]
            if _VALEUR_ECARTEE.search(avant):
                continue
            if cle == 'presents' and re.match(r'\s+(?:ou|et)\s+repr[ée]sent', suite, re.I):
                continue
            chiffre = m['avant'] or m['apres']
            remplacement = _REMPLACEMENT.match(suite)
            if remplacement and re.search(r'\bremplac\w*\b[^.!?;\n]*$', avant, re.I):
                chiffre = remplacement[1]
            elif m['label'] and not _FIN_ITEM.match(suite):
                libelle = m['label'].casefold()
                if libelle in ('pour', 'contre') or (cle == 'presents' and ' ' not in libelle):
                    continue  # « 500 pour le club », « 200 présents au festival ».
                if libelle.startswith('pouvoir') and not _POUVOIRS_SEANCE.match(suite):
                    continue  # « délègue 3 pouvoirs de police au maire ».
            valeurs.append((m.start(), cle, int(''.join(chiffre.split()))))
    for _, cle, valeur in sorted(valeurs):
        if cle == 'unanimite' and valeur:
            for voix in _VOIX:
                mentions.pop(voix, None)
        elif cle in _VOIX:
            mentions.pop('unanimite', None)
        mentions[cle] = valeur
    return mentions


def incoherence(mentions: dict) -> str:
    """Seuls des nombres fournis déclenchent une relance. Les blancs et nuls peuvent expliquer un total inférieur."""
    exercice = mentions.get('exercice')
    votants = mentions.get('votants')
    if votants == 0 and mentions.get('unanimite'):
        return 'Le vote est indiqué à l’unanimité avec zéro votant. Précisez le nombre de votants ou le résultat du vote.'
    if exercice is not None:
        for cle, libelle in (('presents', 'présents'), ('votants', 'votants')):
            if mentions.get(cle, 0) > exercice:
                return f"Vous indiquez {mentions[cle]} {libelle} pour {exercice} membres en exercice. Quel effectif faut-il corriger ?"
    decompte = sum(mentions.get(voix, 0) for voix in _VOIX)
    if votants is not None and decompte > votants:
        return f'Le décompte des voix et abstentions atteint {decompte} pour {votants} votants. Précisez le décompte ou le nombre de votants.'
    if exercice is not None and decompte > exercice:
        return f'Le décompte atteint {decompte} pour {exercice} membres en exercice. Quels chiffres faut-il corriger ?'
    if 'presents' in mentions and 'pouvoirs' in mentions:
        representes = mentions['presents'] + mentions['pouvoirs']
        if exercice is not None and representes > exercice:
            return f"Les présents et les pouvoirs totalisent {representes} pour {exercice} membres en exercice. Quels effectifs faut-il corriger ?"
        if max(decompte, votants or 0) > representes:
            return f"Les {mentions['presents']} présents et {mentions['pouvoirs']} pouvoirs représentent au plus {representes} votants. Précisez les effectifs et le vote."
    return ''


def mention_vote(mentions: dict, manquant: str) -> str:
    if mentions.get('unanimite'):
        resultat = 'à l’unanimité'
    elif any(cle in mentions for cle in _VOIX):
        abstentions = mentions.get('abstentions', manquant)
        resultat = (f"par {mentions.get('pour', manquant)} voix pour, {mentions.get('contre', manquant)} voix contre "
                    f"et {abstentions} abstention{'s' if abstentions != 1 else ''}")
    else:
        resultat = '[à l’unanimité / par … voix pour, … voix contre et … abstentions]'
    return f'Après en avoir délibéré, {resultat},'
