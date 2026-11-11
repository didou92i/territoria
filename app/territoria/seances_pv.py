# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Procès-verbal, compte rendu et liste des délibérations d'une séance : tout ce que le code assemble lui-même.

Fonctions pures, sans base ni modèle : phrase de vote, quorum, repérage du début des points dans la transcription,
passages de chaque point, contenu des documents. Le modèle local ne rédige que la teneur des discussions et la synthèse
du compte rendu (`seances_redaction`) ; ce qui vient de la saisie de l'agent n'est jamais confié au modèle.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .deliberations import A_COMPLETER


def _enumerer(termes: list[str]) -> str:
    return termes[0] if len(termes) == 1 else ', '.join(termes[:-1]) + ' et ' + termes[-1]


def phrase_vote(vote: dict | None) -> str:
    """« Adopté à l'unanimité. », « Adopté par 15 voix pour, 2 contre et 1 abstention. », noms des votants d'un scrutin
    public ; le partage égal reste à compléter (voix prépondérante du président selon le mode de scrutin, L2121-20)."""
    if not vote:
        return A_COMPLETER
    if vote.get('mode') == 'unanimite':
        return "Adopté à l'unanimité."
    pour, contre, abstentions = vote['pour'], vote['contre'], vote['abstentions']
    if pour == contre:
        phrase = f'Partage égal des voix ({pour} pour, {contre} contre) : {A_COMPLETER}.'
    else:
        termes = [f'{pour} voix pour']
        if contre:
            termes.append(f'{contre} contre')
        if abstentions:
            termes.append(f"{abstentions} abstention{'s' if abstentions > 1 else ''}")
        phrase = f"{'Adopté' if pour > contre else 'Rejeté'} par {_enumerer(termes)}."
    public = vote.get('public') or {}
    for cle, un, plusieurs in (('pour', 'A voté pour', 'Ont voté pour'), ('contre', 'A voté contre', 'Ont voté contre'),
                               ('abstention', "S'est abstenu", 'Se sont abstenus')):
        noms = public.get(cle) or []
        if noms:
            phrase += f" {un if len(noms) == 1 else plusieurs} : {', '.join(noms)}."
    return phrase


def quorum(details: dict) -> dict:
    """Quorum (L2121-17) : la majorité des membres en exercice est présente ; inconnu tant que leur nombre n'est pas saisi."""
    presents = len(details.get('presents') or [])
    membres = details.get('membres_en_exercice')
    connu = bool(membres) and 'presents' in details  # présents non saisis : quorum inconnu, jamais « non atteint »
    return {'presents': presents, 'membres': membres, 'atteint': presents * 2 > membres if connu else None}


def _simple(texte: str) -> str:
    sans_accents = ''.join(c for c in unicodedata.normalize('NFKD', str(texte or '')) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', sans_accents.lower()))


def _annonce(texte: str, point: dict) -> bool:
    """Le passage annonce-t-il le point : « point 3 », « point numéro 3 », son numéro de délibération, ou la plupart des
    mots du titre (au moins 60 % des mots de plus de trois lettres, titre d'au moins deux tels mots) ?"""
    simple = _simple(texte)
    if point.get('rang') is not None and re.search(rf"\bpoint (?:numero |n )?{int(point['rang'])}\b", simple):
        return True
    numero = _simple(point.get('deliberation_numero') or '')
    if numero and re.search(rf'\b{re.escape(numero)}\b', simple):
        return True
    mots = {mot for mot in _simple(point.get('titre') or '').split() if len(mot) > 3}
    return len(mots) >= 2 and len(mots & set(simple.split())) >= 0.6 * len(mots)


def proposer_debuts(segments: list[dict], points: list[dict]) -> list[float | None]:
    """Début proposé de chaque point : le premier passage qui l'annonce après le début du point précédent ; sinon None."""
    debuts, apres = [], -1.0
    for point in points:
        debut = next((s['debut'] for s in segments if s['debut'] > apres and _annonce(s['texte'], point)), None)
        debuts.append(debut)
        if debut is not None:
            apres = debut
    return debuts


def empreinte(passages: list[str]) -> str:
    """Empreinte des passages d'un point (noms compris) : la teneur rédigée d'après eux n'est plus à jour s'ils changent."""
    return hashlib.sha256('\n'.join(passages).encode('utf-8')).hexdigest()[:16]


def _heure(secondes: float) -> str:
    secondes = int(secondes)
    return f'{secondes // 3600:02d}:{secondes % 3600 // 60:02d}:{secondes % 60:02d}'


def passages_par_point(segments: list[dict], points: list[dict], noms: dict[str, str]) -> list[list[str]]:
    """Passages de chaque point, « [HH:MM:SS] Nom : texte », du début du point au début suivant (dans l'ordre des débuts) ;
    un point sans début n'a aucun passage."""
    debuts = sorted({p['debut'] for p in points if p.get('debut') is not None})
    resultat = []
    for point in points:
        debut = point.get('debut')
        if debut is None:
            resultat.append([])
            continue
        fin = next((d for d in debuts if d > debut), float('inf'))
        resultat.append([f"[{_heure(s['debut'])}] {noms.get(s['etiquette']) or s['etiquette']} : {s['texte']}"
                         for s in segments if debut <= s['debut'] < fin])
    return resultat


# --- Contenu des documents : suites de (genre, texte), genre dans titre, section, paragraphe, liste ---------------------

MENTIONS_L2121_15 = ('Date et heure de la séance', 'Président', 'Secrétaire(s) de séance', 'Membres présents',
                     'Membres représentés', 'Quorum', 'Ordre du jour', 'Délibérations adoptées', 'Rapporteur', 'Rapport :',
                     'Demandes de scrutin particulier', 'Résultat des scrutins', 'Teneur des discussions', 'Signatures')
MOIS_NOMS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre',
             'décembre')
MENTION_L2121_25 = ("Liste à afficher à la mairie et à mettre en ligne sur le site internet de la commune dans un délai "
                    "d'une semaine (article L2121-25 du CGCT).")


def date_longue(date_iso: str | None) -> str:
    """'2026-11-03' → '3 novembre 2026' ; absente ou illisible : « [à compléter] »."""
    try:
        annee, mois, jour = (int(x) for x in str(date_iso).split('-'))
        return f'{jour} {MOIS_NOMS[mois - 1]} {annee}'
    except (ValueError, IndexError):
        return A_COMPLETER


def _quand(seance: dict) -> str:
    return f"{date_longue(seance.get('date'))} à {seance.get('heure') or A_COMPLETER} — {seance.get('lieu') or A_COMPLETER}"


def _liste_noms(details: dict, cle: str, vide: str) -> str:
    """Noms saisis séparés par des virgules ; clé non saisie : « [à compléter] » ; liste saisie vide : `vide`."""
    if cle not in details:
        return A_COMPLETER
    return ', '.join(details[cle]) or vide


def _adoptee(point: dict) -> bool | None:
    """Adoptée, rejetée, ou inconnue (aucun vote saisi, partage égal) : le PV n'affirme que ce qui a été saisi."""
    vote = point.get('vote')
    if not vote:
        return None
    if vote.get('mode') == 'unanimite':
        return True
    return None if vote['pour'] == vote['contre'] else vote['pour'] > vote['contre']


def contenu_proces_verbal(seance: dict) -> list[tuple[str, str]]:
    """Procès-verbal (L2121-15) : chaque mention figure, saisie ou « [à compléter] » ; seule la teneur vient du modèle."""
    details, points = seance.get('details') or {}, seance.get('points') or []
    etat_quorum = quorum(details)
    if etat_quorum['atteint'] is None:
        phrase_quorum = A_COMPLETER
    else:
        compte = f"({etat_quorum['presents']} présents sur {etat_quorum['membres']} membres en exercice)"
        phrase_quorum = f'Le quorum est atteint {compte}.' if etat_quorum['atteint'] else f"Le quorum n'est pas atteint {compte}."
    representes = details.get('representes')
    contenu = [('titre', f"Procès-verbal de la séance du conseil municipal du {date_longue(seance.get('date'))}"),
               ('section', 'Date et heure de la séance'), ('paragraphe', _quand(seance)),
               ('section', 'Président'), ('paragraphe', details.get('president') or A_COMPLETER),
               ('section', 'Secrétaire(s) de séance'), ('paragraphe', _liste_noms(details, 'secretaires', A_COMPLETER)),
               ('section', 'Membres présents'),
               ('paragraphe', f"Membres en exercice : {details.get('membres_en_exercice') or A_COMPLETER}. "
                              + (f"Présents ({etat_quorum['presents']}) : {_liste_noms(details, 'presents', A_COMPLETER)}."
                                 if details.get('presents') else f'Présents : {A_COMPLETER}.')),
               ('paragraphe', f"Absents : {_liste_noms(details, 'absents', 'aucun')}."),
               ('section', 'Membres représentés'),
               ('paragraphe', A_COMPLETER if representes is None else
                ', '.join(f"{p['mandant']} (pouvoir à {p['mandataire']})" for p in representes) or 'Aucun.'),
               ('section', 'Quorum'), ('paragraphe', phrase_quorum),
               ('section', 'Ordre du jour')]
    contenu += [('liste', f"{p['rang']}. {p['titre']}" + (f" (délibération n° {p['deliberation_numero']})" if p.get('deliberation_numero') else ''))
                for p in points] or [('paragraphe', A_COMPLETER)]
    adoptees = [p for p in points if p.get('deliberation_numero') and _adoptee(p) is not False]
    contenu.append(('section', 'Délibérations adoptées'))
    contenu += [('liste', f"n° {p['deliberation_numero']} — {p['titre']}" + ('' if _adoptee(p) else f' — {A_COMPLETER}'))
                for p in adoptees] or [('paragraphe', 'Aucune délibération numérotée adoptée.')]
    contenu.append(('section', 'Teneur des discussions'))
    for point in points:
        contenu.append(('paragraphe', f"{point['rang']}. {point['titre']}"))
        if point.get('deliberation_numero'):
            contenu.append(('paragraphe', f"Délibération n° {point['deliberation_numero']}"))
        contenu.append(('paragraphe', f"Rapporteur : {point.get('rapporteur') or A_COMPLETER}"))
        contenu.append(('paragraphe', f"Rapport : {point.get('rapport') or A_COMPLETER}"))
        contenu += [('paragraphe', paragraphe) for paragraphe in point.get('teneur') or [A_COMPLETER]]
        contenu.append(('paragraphe', f"Résultat : {phrase_vote(point.get('vote'))}"))
    demandes = [p for p in points if p.get('scrutin_particulier')]
    contenu.append(('section', 'Demandes de scrutin particulier'))
    contenu += [('liste', f"{p['rang']}. {p['titre']}") for p in demandes] or [('paragraphe', 'Aucune demande.')]
    contenu.append(('section', 'Résultat des scrutins'))
    contenu += [('liste', f"{p['rang']}. {p['titre']} : {phrase_vote(p.get('vote'))}") for p in points]
    contenu += [('section', 'Clôture'), ('paragraphe', f"Séance levée à {details.get('heure_cloture') or A_COMPLETER}."),
                ('section', 'Signatures'),
                ('paragraphe', f"Procès-verbal arrêté lors de la séance du {date_longue(details['arrete_le']) if details.get('arrete_le') else A_COMPLETER}."),
                ('paragraphe', 'Le maire'), ('paragraphe', 'Le ou les secrétaires de séance')]
    return contenu


def contenu_compte_rendu(seance: dict) -> list[tuple[str, str]]:
    """Compte rendu : participants (intervenants nommés), points, décisions et actions rédigés par le modèle."""
    compte_rendu = (seance.get('redaction') or {}).get('compte_rendu') or {}
    participants = [i['nom'] for i in seance.get('intervenants') or [] if i.get('nom')]
    contenu = [('titre', f"Compte rendu — {seance.get('titre')}"),
               ('paragraphe', f"Réunion du {_quand(seance)}"),
               ('section', 'Participants'), ('paragraphe', ', '.join(participants) or A_COMPLETER),
               ('section', 'Points abordés')]
    contenu += [('paragraphe', f"{p['titre']} — {p['synthese']}") for p in compte_rendu.get('points') or []] or [('paragraphe', 'Aucun point relevé.')]
    contenu.append(('section', 'Décisions'))
    contenu += [('liste', d) for d in compte_rendu.get('decisions') or []] or [('paragraphe', 'Aucune décision.')]
    contenu.append(('section', 'Actions'))
    contenu += [('liste', f"{a['qui']} — {a['quoi']}" + (f" — échéance : {a['echeance']}" if a.get('echeance') else ''))
                for a in compte_rendu.get('actions') or []] or [('paragraphe', 'Aucune action.')]
    return contenu


def contenu_liste_deliberations(seance: dict) -> list[tuple[str, str]]:
    """Liste des délibérations examinées (L2121-25) : numéro, objet et résultat du vote, assemblés par le code seul."""
    contenu = [('titre', f"Liste des délibérations examinées — séance du {date_longue(seance.get('date'))}")]
    contenu += [('liste', f"n° {p['deliberation_numero']} — {p['titre']} — {phrase_vote(p.get('vote'))}")
                for p in seance.get('points') or [] if p.get('deliberation_numero')] or [('paragraphe', 'Aucune délibération numérotée.')]
    contenu.append(('paragraphe', MENTION_L2121_25))
    return contenu
