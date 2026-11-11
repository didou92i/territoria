# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Projets d'arrêtés du maire portant délégation, rédigés dans la conversation : délégation de fonction à un adjoint ou à un
conseiller municipal (L2122-18 du CGCT), délégation de signature aux responsables des services (L2122-19), délégation de
signature pour la certification et la légalisation (R2122-8), délégation des fonctions d'officier de l'état civil à un
fonctionnaire titulaire (R2122-10). Mécanique commune : `trames` ; visas tirés de `ressources/arretes-delegations-reference.json`.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import trames
from .deliberations import ReferenceInventee, lire  # noqa: F401 (réexportés)

REFERENCE = Path(__file__).resolve().parent / 'ressources' / 'arretes-delegations-reference.json'
SOURCES_FIXES = ('L2131-1', 'L2131-2', 'R421-1')
RECOURS = ('Le présent arrêté peut faire l’objet d’un recours devant le tribunal administratif territorialement compétent dans un '
           'délai de deux mois à compter de sa publication ou de sa notification. Le tribunal peut être saisi par l’application '
           'Télérecours citoyens accessible sur le site www.telerecours.fr.')
EXECUTION = ('execution', 'Exécution', 'Le directeur général des services est chargé de l’exécution du présent arrêté.')
NOTIFICATION = ('Notifié à l’intéressé(e) le [date] — signature :',)
MENTION = ('mention', 'Mention de la délégation', 'Le délégataire fait précéder sa signature de la mention « Pour le maire et par '
           'délégation », suivie de ses prénom, nom et qualité.')
PUBLICITE = ('publicite', 'Publicité', 'Le présent arrêté sera publié, transmis au représentant de l’État dans le département et '
             'notifié à l’intéressé(e).')
CERTIFICATION = '{titre} certifie sous sa responsabilité le caractère exécutoire du présent arrêté, publié le [date], transmis le [date] et notifié le [date].'
PRECISES = 'Les matières déléguées doivent être précises : une délégation générale ou imprécise est illégale.'
# Du plus précis au plus général : « délégation des fonctions d'officier de l'état civil » n'est pas une délégation de fonction
# à un élu, et une délégation de signature pour la légalisation relève de R2122-8.
THEMES = (
    {'nom': 'delegation_etat_civil', 'motif': r'\betat\s+civil\b', 'articles': ('L2122-32', 'R2122-10'), 'complements': (),
     'consigne': 'Pour une délégation d’état civil, une décision délègue au fonctionnaire titulaire désigné tel que donné (civilité, '
                 'prénom, nom, grade) les fonctions d’officier de l’état civil citées dans la demande, à défaut les fonctions d’officier '
                 'de l’état civil, avec la date d’effet donnée. Ne mentionne ni les mariages ni la transmission : ils font l’objet '
                 'd’articles séparés.',
     'fixes': (('limite', 'Limite de la délégation',
                'Cette délégation ne s’étend pas à la célébration des mariages, réservée au maire et aux adjoints. Les actes dressés '
                'dans le cadre des fonctions déléguées comportent la seule signature du fonctionnaire délégué, qui les exerce sous le '
                'contrôle et la responsabilité du maire.'),
               ('transmission', 'Transmission',
                'Le présent arrêté est transmis au préfet ou au sous-préfet et au procureur de la République près le tribunal '
                'judiciaire dans le ressort duquel se trouve la commune ; il est notifié à l’intéressé(e).'),
               ('recours', 'Voies et délais de recours', RECOURS), EXECUTION),
     'signatures': NOTIFICATION,
     'certification': '{titre} certifie sous sa responsabilité le caractère exécutoire du présent arrêté, transmis le [date] et notifié le [date].',
     'points': ('Seul un fonctionnaire titulaire de la commune peut recevoir cette délégation (article R2122-10 du CGCT) : vérifier la '
                'situation de l’agent.',
                'La délégation peut porter sur tout ou partie des fonctions d’officier de l’état civil : préciser celles qui sont déléguées.')},
    {'nom': 'delegation_certification',
     'motif': r'\b(legalisation|certification\s+(?:conforme|de\s+(?:la\s+)?conformite)|copies?\s+conformes?|paraphe|expeditions?)\b',
     'articles': ('R2122-8',), 'complements': (),
     'consigne': 'Pour une délégation de signature de certification, une décision donne délégation de signature à l’agent désigné tel '
                 'que donné pour les seules opérations citées dans la demande (paraphe des registres, délivrance d’expéditions, '
                 'certification conforme, légalisation des signatures, ou certification des pièces justificatives des mandats de '
                 'paiement pour un agent de catégorie A).',
     'fixes': (('condition', 'Condition d’exercice',
                'La présente délégation s’exerce en l’absence ou en cas d’empêchement des adjoints, sous la surveillance et la '
                'responsabilité du maire.'),
               MENTION, ('duree', 'Durée', 'La présente délégation prend fin si elle est rapportée ou si l’intéressé(e) quitte ses fonctions.'),
               PUBLICITE, ('recours', 'Voies et délais de recours', RECOURS), EXECUTION),
     'signatures': NOTIFICATION, 'certification': CERTIFICATION,
     'points': ('La certification des pièces justificatives des mandats de paiement ne peut être déléguée qu’à un fonctionnaire de '
                'catégorie A (article R2122-8 du CGCT).',)},
    {'nom': 'delegation_signature', 'motif': r'\bdelegations?\s+de\s+signature\b|\bsignature\b.{0,40}\bdelegation\b',
     'articles': ('L2122-19', 'L2122-20'), 'complements': (),
     'consigne': 'Pour une délégation de signature, une décision donne délégation de signature, sous la surveillance et la '
                 'responsabilité du maire, à l’agent désigné tel que donné (civilité, prénom, nom, fonction) pour les documents et '
                 'matières cités dans la demande ; si la demande le prévoit, une autre désigne qui signe en cas d’absence ou '
                 'd’empêchement du délégataire.',
     'fixes': (MENTION, ('duree', 'Durée', 'La présente délégation subsiste tant qu’elle n’est pas rapportée ; elle cesse si l’intéressé(e) '
                                          'quitte ses fonctions.'),
               PUBLICITE, ('recours', 'Voies et délais de recours', RECOURS), EXECUTION),
     'signatures': NOTIFICATION, 'certification': CERTIFICATION,
     'points': ('Au titre de l’article L2122-19, seuls le directeur général des services, le directeur général adjoint, le directeur '
                'des services techniques et les responsables de services communaux peuvent recevoir délégation de signature : vérifier '
                'la fonction du délégataire.', PRECISES,
                'La délégation n’est opposable qu’une fois l’arrêté publié et transmis au représentant de l’État.')},
    {'nom': 'delegation_fonction',
     'motif': r'\bdelegations?\s+(?:de\s+|des\s+)?fonctions?\b|\bdelegu\w*\b.{0,60}\b(?:adjoint|conseill\w+)\b|'
              r'\b(?:adjoint|conseill\w+)\b.{0,60}\bdelegu\w*',
     'articles': ('L2122-18', 'L2122-20'),
     'complements': ('Vu le procès-verbal de l’élection du maire et des adjoints du [à compléter],',),
     'consigne': 'Pour une délégation de fonction, une décision dit que l’élu désigné tel que donné (civilité, prénom, nom, qualité '
                 'd’adjoint ou de conseiller municipal) est délégué, sous la surveillance et la responsabilité du maire, pour les seules '
                 'matières données, énoncées précisément, avec la date d’effet si elle est donnée. Ne rédige pas la signature des actes '
                 'ni la durée : elles font l’objet d’articles séparés.',
     'fixes': (('signature', 'Signature',
                'Dans les matières déléguées, l’intéressé(e) signe les actes, arrêtés, décisions et correspondances en faisant précéder sa '
                'signature de la mention de sa qualité et de la délégation.'),
               ('duree', 'Durée', 'La présente délégation subsiste tant qu’elle n’est pas rapportée.'),
               PUBLICITE, ('recours', 'Voies et délais de recours', RECOURS), EXECUTION),
     'signatures': NOTIFICATION, 'certification': CERTIFICATION,
     'points': (PRECISES,
                'Un membre du conseil municipal qui exerce un mandat de député, de sénateur ou de représentant au Parlement européen ne '
                'peut recevoir de délégation, sauf pour les attributions exercées au nom de l’État (article L2122-18 du CGCT).',
                'Si le maire retire ses délégations à un adjoint, le conseil municipal se prononce sur son maintien dans ses fonctions '
                '(article L2122-18 du CGCT).',
                'Conflit d’intérêts : le délégataire s’abstient d’intervenir dans une affaire où il est intéressé ; prévoir un arrêté de '
                'déport le cas échéant.')},
)
CLARIFICATION = ('Pour rédiger l’arrêté de délégation, précisez le délégataire (civilité, prénom, nom, qualité ou fonction), les '
                 'matières ou documents délégués et, si possible, la date d’effet.')

FAMILLE = trames.Famille(
    kind='arrete_delegation', reference=REFERENCE, nom_cache='arretes-delegations.json', themes=THEMES, clarification=CLARIFICATION,
    complet=lambda contexte: bool(re.search(r'\b(M\.|Mme|Madame|Monsieur)\s+[A-ZÀ-Ý]', contexte)),
    introduction='Tu prépares en français un projet d’arrêté du maire portant délégation, pas un acte signé.',
    considerants='qui exposent la nécessité d’assurer le bon fonctionnement de l’administration communale ou l’organisation donnée, '
                 'sans fait non fourni',
    decisions='decisions est une liste de une à trois décisions complètes au présent, qui désignent le délégataire par sa civilité, '
              'son prénom, son nom et sa qualité tels que donnés, sans numéro d’article.',
    fidelite='Reprends exactement les noms, qualités, fonctions, matières déléguées et dates donnés, et chaque valeur de '
             'valeurs_donnees ; ne délègue aucune matière non demandée. Écris [à compléter] pour une information absente : n’invente '
             'aucun nom, qualité, matière ni date.',
    hors_redaction='la mention de la délégation, ni la durée, ni la publicité, ni la transmission, ni les voies de recours, ni '
                   'l’exécution, ni la signature',
    role_decision='decision',
    point_intercommunal='Établissement intercommunal : le président délègue sur le fondement de l’article L5211-9 du CGCT ; adapter '
                        'les visas.')
textes, theme, est_demande, preparer, requete, assembler = (FAMILLE.textes, FAMILLE.theme, FAMILLE.est_demande, FAMILLE.preparer,
                                                             FAMILLE.requete, FAMILLE.assembler)
