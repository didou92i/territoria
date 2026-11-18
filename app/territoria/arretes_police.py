# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Projets d'arrêtés de police temporaires du maire, rédigés dans la conversation : circulation pendant des travaux
(route barrée, déviation, alternat, limitation de vitesse) et occupation du domaine public par permis de stationnement
(terrasse, échafaudage, benne, étalage), et ouverture d'un débit de boissons temporaire
(buvette d'une association ou d'une fête publique).

Même partage que pour les délibérations (voir `deliberations`) : le modèle local ne rédige que l'objet, les considérants et
les mesures, en gardant lieux, dates et exceptions. Les visas (copie datée `ressources/arretes-police-reference.json`,
remplacée chaque jour par la version Légifrance quand le connecteur est actif, et les références de la signalisation
temporaire) et les articles de mise en œuvre sont fixes. Le stationnement, les marchés et la police générale restent au
moteur des arrêtés de police (`arretes`).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import deliberations, trames
from .deliberations import ReferenceInventee, _plain  # noqa: F401 (réexportés)

REFERENCE = Path(__file__).resolve().parent / 'ressources' / 'arretes-police-reference.json'
SOURCES_FIXES = ('L2131-1', 'L2131-2', 'R421-1')
RECOURS = ('Le présent arrêté peut faire l’objet d’un recours devant le tribunal administratif territorialement compétent dans un '
           'délai de deux mois à compter de sa {depart}. Le tribunal peut être saisi par l’application Télérecours citoyens '
           'accessible sur le site www.telerecours.fr.')
DEMANDE = 'Vu, le cas échéant, la demande présentée par [à compléter] le [à compléter],'
REGLE_SIGNALISATION = ('conformément à l’instruction interministérielle sur la signalisation routière '
                       '(huitième partie, signalisation temporaire). Les prescriptions du présent arrêté sont applicables '
                       'dès la mise en place de cette signalisation.')
# Thèmes, du plus précis au plus général : une demande qui touche la circulation (rue barrée près d'un échafaudage)
# relève d'abord de l'arrêté de circulation.
THEMES = (
    {'nom': 'circulation_travaux',
     'motif': r'\b(deviations?|alternats?|barree?s?|fermeture\s+(?:de\s+la\s+|du\s+)?(?:rue|route|voie|chemin|pont)|circulation\s+'
              r'(?:interdite|alternee|restreinte)|limitation\s+de\s+(?:la\s+)?vitesse)\b|\btravaux\b.{0,200}\bcirculation\b|'
              r'\bcirculation\b.{0,200}\btravaux\b',
     'articles': ('L2212-1', 'L2212-2', 'L2213-1', 'R411-8', 'R411-25', 'R417-10'),
     'complements': ('Vu l’arrêté interministériel du 24 novembre 1967 modifié relatif à la signalisation des routes et des autoroutes,',
                     'Vu l’instruction interministérielle sur la signalisation routière, huitième partie (signalisation temporaire), '
                     'approuvée par l’arrêté du 6 novembre 1992 modifié,', DEMANDE),
     'consigne': 'Un considérant explique que la mesure est nécessaire à la sécurité et à la commodité du passage pendant les '
                 'travaux. Pour la circulation pendant des travaux, une décision fixe la mesure principale (circulation interdite, alternée ou '
                 'limitée) avec la voie, le tronçon exact (numéros, intersections) et la période (dates et horaires) tels que donnés ; '
                 'une voie « barrée » a sa circulation interdite. Les exceptions données (riverains, secours, véhicules du chantier) '
                 'figurent dans la même décision que la règle. Si la demande les donne, une décision décrit la déviation et une autre '
                 'interdit le stationnement, considéré comme gênant, sur la section concernée.',
     'fixes': (('signalisation', 'Signalisation',
                'La signalisation temporaire est mise en place, entretenue et retirée par [l’entreprise chargée des travaux ou les '
                'services techniques, à préciser], ' + REGLE_SIGNALISATION),
               ('controle', 'Contrôle et sanctions',
                'Les infractions au présent arrêté sont constatées par les agents légalement habilités et poursuivies conformément aux '
                'lois et règlements en vigueur.'),
               ('publicite', 'Publicité et entrée en vigueur',
                'Le présent arrêté est publié et affiché en mairie et aux extrémités de la section de voie concernée. Il est exécutoire '
                'dès sa publication.'),
               ('recours', 'Voies et délais de recours', RECOURS.format(depart='publication')),
               ('execution', 'Exécution',
                'Le directeur général des services, le responsable de la police municipale le cas échéant, et le commandant de la '
                'brigade de gendarmerie ou le commissaire de police territorialement compétent sont chargés, chacun en ce qui le '
                'concerne, de l’exécution du présent arrêté, dont copie est adressée au service départemental d’incendie et de '
                'secours et, le cas échéant, au demandeur.')),
     'certification': '{titre} certifie sous sa responsabilité le caractère exécutoire du présent arrêté, publié le [date].',
     'points': ('Hors agglomération, la police de la circulation sur une route départementale relève du président du conseil '
                'départemental ; sur une route classée à grande circulation, l’arrêté du maire est pris après avis du préfet '
                '(article L2213-1 du CGCT, article R411-8 du code de la route).',
                'Les mesures ne sont opposables aux usagers qu’une fois la signalisation en place (article R411-25 du code de la route).',
                'Vérifier que la déviation est praticable et signalée ; prévenir les secours, les transports scolaires et la collecte '
                'des déchets.',
                'Arrêté de police relatif à la circulation : il n’a pas à être transmis au représentant de l’État (article L2131-2 du '
                'CGCT) ; il est exécutoire dès sa publication.')},
    {'nom': 'debit_boissons',
     'motif': r'\b(debits?\s+de\s+boissons?|buvettes?|bar\s+(?:temporaire|associatif))\b|\bvente\s+d.?\s*(?:alcool|boissons)\b',
     'articles': ('L3321-1', 'L3334-2'),
     'complements': ('Vu la demande présentée par [à compléter] le [à compléter],',),
     'consigne': 'Le considérant expose la demande et la manifestation. Pour un débit de boissons temporaire, une décision autorise '
                 'le bénéficiaire désigné tel que donné (association, comité, organisateur) à ouvrir un débit de boissons temporaire, '
                 'à l’occasion de la manifestation donnée, au lieu, à la date et aux horaires donnés. Ne mentionne pas les boissons '
                 'autorisées : elles font l’objet d’un article séparé.',
     'fixes': (('boissons', 'Boissons autorisées',
                'Seules peuvent être vendues ou offertes, sous quelque forme que ce soit, les boissons des groupes un et trois : '
                'boissons sans alcool, boissons fermentées non distillées (vin, bière, cidre…) et vins doux naturels.'),
               ('obligations', 'Obligations du bénéficiaire',
                'Le bénéficiaire respecte la réglementation relative à la protection des mineurs et à la répression de l’ivresse '
                'publique : la vente et l’offre de boissons alcooliques aux mineurs sont interdites. Il assure le bon ordre aux abords '
                'du débit pendant toute la durée de la manifestation.'),
               ('precarite', 'Caractère de l’autorisation',
                'La présente autorisation est personnelle et ne vaut que pour la manifestation, le lieu et les horaires indiqués. '
                'Elle peut être retirée à tout moment pour un motif d’ordre public.'),
               ('recours', 'Voies et délais de recours', RECOURS.format(depart='notification au bénéficiaire ou de sa publication')),
               ('execution', 'Exécution',
                'Le directeur général des services, le responsable de la police municipale le cas échéant, et le commandant de la '
                'brigade de gendarmerie ou le commissaire de police territorialement compétent sont chargés, chacun en ce qui le '
                'concerne, de l’exécution du présent arrêté, qui sera notifié au bénéficiaire.')),
     'certification': '{titre} certifie sous sa responsabilité le caractère exécutoire du présent arrêté, notifié au bénéficiaire le [date].',
     'points': ('Une association peut obtenir cinq autorisations par an au plus (article L3334-2 du code de la santé publique) : '
                'vérifier le nombre déjà accordé cette année.',
                'Dans un stade, un gymnase ou un établissement sportif, la vente de boissons du groupe trois relève d’une dérogation '
                'propre, de quarante-huit heures au plus (article L3335-4 du code de la santé publique).',
                'Vérifier les zones protégées fixées par arrêté préfectoral autour de certains établissements (article L3335-1 du '
                'code de la santé publique).',
                'Autorisation d’un débit temporaire pour une manifestation associative : elle n’a pas à être transmise au '
                'représentant de l’État (article L2131-2 du CGCT).')},
    {'nom': 'occupation',
     'motif': r'\b(terrasses?|echafaudages?|bennes?|etalages?|palissades?|monte.?meubles?|nacelles?|grues?|food.?trucks?|'
              r'camion.?(?:restaurant|pizza)|permis\s+de\s+stationnement|occupation\s+(?:temporaire\s+)?du\s+domaine\s+public|'
              r'depots?\s+de\s+materiaux)\b',
     'articles': ('L2213-6', 'L2122-1', 'L2122-2', 'L2122-3', 'L2125-1'),
     'complements': ('Vu la délibération du [à compléter] fixant les tarifs d’occupation du domaine public,',
                     'Vu la demande présentée par [à compléter] le [à compléter],'),
     'consigne': 'Le considérant expose la demande du bénéficiaire, sans évoquer de nécessité de police. Pour une occupation du '
                 'domaine public, une décision autorise le bénéficiaire désigné tel que donné à installer '
                 'l’objet de la demande (terrasse, échafaudage, benne…) à l’emplacement exact, avec sa surface ou ses dimensions, '
                 'pour la période et les horaires donnés. Si la demande en fixe, une autre décision reprend les conditions '
                 'particulières (passage laissé libre, horaires de livraison…).',
     'fixes': (('conditions', 'Conditions d’occupation',
                'Le bénéficiaire maintient en permanence un cheminement piéton libre et sécurisé, signale l’installation de jour comme '
                'de nuit et remet les lieux en état à la fin de l’occupation. Il est responsable des dommages causés aux tiers et au '
                'domaine public du fait de l’occupation.'),
               ('redevance', 'Redevance',
                'L’occupation donne lieu au paiement d’une redevance selon les tarifs fixés par délibération du conseil municipal, '
                'sauf dans les cas de gratuité prévus par la loi.'),
               ('precarite', 'Caractère de l’autorisation',
                'La présente autorisation est personnelle, précaire et révocable. Elle peut être retirée à tout moment pour un motif '
                'd’intérêt général, sans indemnité. Elle doit être présentée à toute réquisition des agents habilités.'),
               ('recours', 'Voies et délais de recours', RECOURS.format(depart='notification au bénéficiaire ou de sa publication')),
               ('execution', 'Exécution',
                'Le directeur général des services et le responsable de la police municipale le cas échéant sont chargés, chacun en ce '
                'qui le concerne, de l’exécution du présent arrêté, qui sera notifié au bénéficiaire.')),
     'certification': '{titre} certifie sous sa responsabilité le caractère exécutoire du présent arrêté, notifié au bénéficiaire le [date].',
     'points': ('Permis de stationnement (occupation sans emprise) : si l’installation modifie l’assiette du domaine (ancrage, '
                'scellement), il faut une permission de voirie.',
                'L’autorisation ne doit entraîner aucune gêne pour la circulation ni pour la liberté du commerce (article L2213-6 du '
                'CGCT) : vérifier la largeur du cheminement piéton laissé libre.',
                'Redevance : appliquer le tarif de la délibération ; une occupation liée à l’exécution de travaux peut être gratuite '
                'dans les cas prévus (article L2125-1 du code général de la propriété des personnes publiques).',
                'Vérifier auprès de la préfecture si l’autorisation doit lui être transmise : les décisions de police relatives à la '
                'circulation et au stationnement en sont exclues (article L2131-2 du CGCT).')},
)
CLARIFICATION = ('Pour rédiger l’arrêté, précisez la voie et le tronçon concernés, les dates (et les horaires) et, pour une occupation '
                 'du domaine public, le bénéficiaire, la nature de l’installation et son emplacement.')
DATE = re.compile(r'\b\d{1,2}(?:er)?\s+(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\b')


FAMILLE = trames.Famille(
    kind='arrete_police', reference=REFERENCE, nom_cache='arretes-police.json', themes=THEMES, clarification=CLARIFICATION,
    complet=lambda contexte: bool(DATE.search(_plain(contexte))),
    introduction='Tu prépares en français un projet d’arrêté de police temporaire du maire, pas un acte signé.',
    considerants='qui exposent le motif concret donné (des travaux, une demande d’installation ou d’autorisation), sans fait non fourni',
    decisions='decisions est une liste de une à cinq dispositions complètes, rédigées comme des règles effectives au présent, sans '
              'numéro d’article.',
    fidelite='Reprends exactement les voies, numéros, intersections, dates, horaires, surfaces et noms donnés, et chaque valeur de '
             'valeurs_donnees ; ne prolonge jamais la période. Écris [à compléter] pour une information absente : n’invente aucun '
             'lieu, horaire, date, nombre, montant ni bénéficiaire.',
    hors_redaction='la signalisation, ni le contrôle, ni la publicité, ni les voies de recours, ni l’exécution, ni la signature',
    role_decision='mesure',
    point_intercommunal='Ces mesures relèvent des pouvoirs de police du maire : vérifier la compétence de l’établissement (transfert '
                        'prévu à l’article L5211-9-2 du CGCT).')
textes, theme, est_demande, requete = FAMILLE.textes, FAMILLE.theme, FAMILLE.est_demande, FAMILLE.requete

_OPERATIONS = {
    'pose': r'\b(?:pos(?:e|ent|er|ee?s?)|install\w*|mi(?:se?s?|s) en place|met(?:tre|tent)? en place)\b',
    'entretien': r'\b(?:entretien\w*|entreten\w*|maintien\w*|mainten\w*)\b',
    'retrait': r'\b(?:retir\w*|retrait|enlev\w*|depos\w*)\b',
}
_AGENT = r"(?:l[’']entreprise|la soci[ée]t[ée]|les services techniques|le service (?:technique|voirie))"
_VERBE = r"(?:qui\s+(?:les?\s+)?)?(?:(?:est|sont)\s+charg[ée]e?s?\s+de|pos\w*|install\w*|met\w*|entreti\w*|retir\w*|enl[èe]v\w*|assur\w*)"
_ACTIF = re.compile(rf"(?P<agent>{_AGENT}[^,;:.!?]{{0,120}}?)\s+(?P<action>{_VERBE}\b.*)$", re.I)
_PASSIF = re.compile(r"(?:\bsignalisation|\bpanneaux?)\b(?P<action>[^;:.!?]{0,160}?)\bpar\s+(?P<agent>[^,;:.!?]{1,120})$", re.I)
_LIBELLE = re.compile(r"(?:ex[ée]cutant|responsable) de la signalisation\s*:\s*([^,;.!?]{1,120})$", re.I)


def _attributions(question: str, precedentes: dict) -> dict:
    attributions = dict(precedentes)
    for phrase in re.split(r'[;.!?\n]', question):
        simple = _plain(phrase)
        if not re.search(r'\b(?:signalisation|panneaux?)\b', simple):
            continue
        if re.search(r"\b(?:ne|non|pas|si|pourrait|peut-etre)\b|\bn[’']", simple):
            if re.search(r"\b(?:ne|n[’']).{0,60}\b(?:pas|plus)\b", simple):
                for cle, agent in list(attributions.items()):
                    if _plain(agent) in simple and re.search(_OPERATIONS[cle], simple):
                        del attributions[cle]
            continue
        libelle = _LIBELLE.search(phrase)
        if libelle:
            attributions = dict.fromkeys(_OPERATIONS, libelle[1].strip())
            continue
        actif = _ACTIF.search(phrase)
        action = actif or _PASSIF.search(phrase)
        if not action:
            continue
        agent = action['agent'].strip()
        # Un second acteur impose de préciser la répartition.
        if actif and re.search(_AGENT, action['action'], re.I):
            continue
        verbes = _plain(action['action'] if actif else phrase[:action.start('agent')])
        operations = [cle for cle, motif in _OPERATIONS.items() if re.search(motif, verbes)]
        if not operations and re.search(r'\bassur\w*\s+la signalisation|\bsignalisation\s+(?:est|sera)\s+assuree\b', verbes):
            operations = list(_OPERATIONS)
        for cle in operations:
            attributions[cle] = agent[0].lower() + agent[1:] if re.match(r"^(?:l[’']|la |le |les )", agent, re.I) else agent
    return attributions


def preparer(question, precedent, collectivite_enregistree):
    donnees, clarification = FAMILLE.preparer(question, precedent, collectivite_enregistree)
    anciennes = (precedent or {}).get('inputs', {}) if (precedent or {}).get('kind', '').startswith(FAMILLE.kind) else {}
    attributions = anciennes.get('executants_signalisation')
    if attributions is None:
        attributions = _attributions(anciennes.get('contexte', ''), {})
    if donnees.get('decisions_precedentes'):
        donnees['derniere_precision'] = question
        if not donnees['contexte'].endswith(question):
            donnees['contexte'] += '\n\nPrécision la plus récente, prioritaire :\n' + question
    donnees['executants_signalisation'] = _attributions(question, attributions)
    return donnees, clarification


# Une révision ciblée peut changer les valeurs annoncées dans les prescriptions existantes.
_CHAMPS = {
    'horaire': (r'horair|heure|\d\s*h', re.compile(r'\b\d{1,2}\s*(?:h(?:eures?)?\b(?:\s*\d{1,2}\b)?|h\d{2}\b|:\d{2}\b)', re.I)),
    'date': (r'date|p[ée]riode|dur[ée]e|prolong|d[ée]cal|jour|semaine|mois', re.compile(
        r'\b\d{1,2}(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)(?:\s+\d{4})?'
        r'|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{1,2}(?=\s+au\s+\d)|\b\d+\s+(?:jours?|semaines?|mois)\b', re.I)),
    'montant': (r'montant|redevance|tarif|euros?|€', deliberations._MONTANT),
    'voie': (r'\b(?:lieu|voie|tronçon|itin[ée]raire|d[ée]viation|rue|avenue|boulevard|quai|impasse|place|chemin|route)', re.compile(
        r'\b(?i:rue|avenue|place|boulevard|quai|impasse|chemin|route|allée)\s+'
        r"(?:(?i:de la|du|des|de l[’']|de)\s*)?[A-ZÀ-ÖØ-Þ][\w’'‑-]*(?:\s+[A-ZÀ-ÖØ-Þ][\w’'‑-]*)*")),
}


def _valeurs_champ(nom, motif, texte):
    if nom == 'montant':
        return deliberations._montants(texte)
    return [re.sub(r'\s+', '', m[0].casefold()) for m in motif.finditer(texte)]


def _revision_fidele(question, precedentes, nouvelles):
    champs = {nom: motif for nom, (declencheur, motif) in _CHAMPS.items()
              if re.search(declencheur, question, re.I) or motif.search(question)}
    ajout = bool(re.search(r'\b(?:ajout|inser|inclu|rajout)\w*', _plain(question)))
    if not champs and not ajout and 'signalisation' not in _plain(question):
        return True
    ancien, nouveau = '\n'.join(precedentes), '\n'.join(nouvelles)
    autorisees = {nom: set(_valeurs_champ(nom, motif, question)) for nom, motif in champs.items()}
    for nom, motif in champs.items():
        avant, apres = set(_valeurs_champ(nom, motif, ancien)), set(_valeurs_champ(nom, motif, nouveau))
        demande = autorisees[nom]
        if not (apres - avant <= demande and demande - avant <= apres):
            return False

    def correspond(ancienne, nouvelle):
        for nom, motif in champs.items():
            if nom == 'voie' and 'deviation' in _plain(question) and 'deviation' not in _plain(ancienne):
                continue
            avant = _valeurs_champ(nom, motif, ancienne)
            apres = _valeurs_champ(nom, motif, nouvelle)
            if any(a != b and b not in autorisees[nom] for a, b in zip(avant, apres)):
                return False
            ancienne = motif.sub('{' + nom + '}', ancienne)
            nouvelle = motif.sub('{' + nom + '}', nouvelle)
        return ' '.join(ancienne.casefold().split()) == ' '.join(nouvelle.casefold().split())

    if not ajout:
        return len(precedentes) == len(nouvelles) and all(correspond(a,b) for a,b in zip(precedentes, nouvelles))
    # Une nouvelle disposition peut être intercalée entre deux anciennes, conservées dans le même ordre.
    restantes = iter(nouvelles)
    return all(any(correspond(attendue, ligne) for ligne in restantes) for attendue in precedentes)


def lire(completion, donnees):
    resultat = deliberations.lire(completion, donnees)
    precision = donnees.get('derniere_precision', '')
    if precision and not _revision_fidele(precision, donnees.get('decisions_precedentes', []), resultat['decisions']):
        raise deliberations.ReponseInvalide('La révision change une prescription hors de la correction demandée ou omet la nouvelle valeur. '
                                            'Le projet précédent et son Word restent disponibles.')
    return resultat


def assembler(donnees, genere, sources, logo=None):
    document = FAMILLE.assembler(donnees, genere, sources, logo)
    acteurs = donnees.get('executants_signalisation') or {}
    for article in document['articles']:
        if article['role'] != 'signalisation' or not acteurs:
            continue
        obligations = 'La pose est assurée par {pose}. L’entretien est assuré par {entretien}. Le retrait est assuré par {retrait}.'
        clauses = obligations.format_map({cle: acteurs.get(cle, '[à préciser]') for cle in _OPERATIONS})
        article['texte'] = clauses + ' La signalisation temporaire est réalisée ' + REGLE_SIGNALISATION
    return document
