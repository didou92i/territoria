# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Corpus de démonstration, entièrement fictif, pour une installation neuve.

Une installation n'a ni le PDF de référence du pilote ni ses exemples d'arrêtés. Ce module crée de quoi
rédiger un arrêté et lancer le test de bout en bout : un modèle de référence et trois exemples de la
« Commune de Démoville », collectivité imaginaire (rues, dates et textes inventés, aucun nom ni aucune signature).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

import corpus_arretes

REFERENCE = 'reference-demo.pdf'
TRAME = 'demo-stationnement'
MENTION = 'Document fictif de démonstration TerritorIA — aucune valeur juridique.'

VISA_COLLECTIVITES = 'Vu le code général des collectivités territoriales, notamment ses articles '
INFRACTIONS = ('Les infractions au présent arrêté seront constatées par procès-verbal et poursuivies conformément '
               'aux lois et règlements en vigueur. Elles exposent leurs auteurs à une contravention.')
PUBLICITE = ('Le présent arrêté sera publié et affiché en mairie. Il peut faire l’objet d’un recours contentieux devant '
             'le tribunal administratif compétent dans un délai de deux mois à compter de sa publication.')
EXECUTION = ('Le directeur général des services et le responsable de la police municipale de Démoville sont chargés, '
             'chacun en ce qui le concerne, de l’exécution du présent arrêté, dont ampliation sera transmise aux '
             'services de secours et aux forces de l’ordre territorialement compétentes.')

# Modèle de référence : le seul dont la trame est vérifiée par arretes.verify_reference().
DOCUMENT_REFERENCE = {
    'titre': 'Arrêté fictif de stationnement — modèle de référence de démonstration',
    'numero': 'ARRÊTÉ MUNICIPAL N° DEMO-2026-001',
    'objet': 'portant réglementation temporaire du stationnement rue des Essais',
    'visas': [VISA_COLLECTIVITES + 'L2212-1, L2212-2 et L2213-2 ;',
              'Vu le code de la route, notamment ses articles R411-25 et R417-10 ;',
              'Vu la demande déposée en mairie par l’entreprise chargée de travaux de ravalement de façade '
              'au n° 12 de la rue des Essais ;'],
    'considerants': ['Considérant que ces travaux imposent l’installation d’un échafaudage et d’une benne sur la '
                     'chaussée de la rue des Essais ;',
                     'Considérant qu’il convient, pour garantir la sécurité des piétons et des ouvriers, de '
                     'réglementer temporairement le stationnement des véhicules dans cette rue ;'],
    'mesures': ['Du lundi 16 mars 2026 à 7 h 00 au vendredi 20 mars 2026 à 18 h 00, le stationnement de tout véhicule '
                'est interdit rue des Essais, entre les n° 10 et 14, des deux côtés de la chaussée.',
                'À l’exception des véhicules de secours, des véhicules de collecte des déchets et de ceux de '
                'l’entreprise chargée des travaux, l’interdiction définie à l’article 1 s’applique à tous les véhicules.',
                'Une signalisation réglementaire (panneaux de stationnement interdit complétés d’un panonceau '
                'indiquant la période et le périmètre) sera mise en place par les services techniques de la '
                'commune, 48 heures au moins avant le début de l’interdiction, puis retirée à son expiration.'],
    'date': 'Fait à Démoville, le 6 mars 2026',
}

# Exemples de la bibliothèque : un par sujet, chacun classé dans sa catégorie par legal_sources.classify().
EXEMPLES = [
    {'id': 'demo-stationnement-0001-exemple-fictif',
     'title': 'Exemple fictif — stationnement pour déménagement rue des Maquettes',
     'titre': 'Arrêté fictif de stationnement — exemple de démonstration',
     'numero': 'ARRÊTÉ MUNICIPAL N° DEMO-2026-014',
     'objet': 'portant interdiction temporaire du stationnement rue des Maquettes à l’occasion d’un déménagement',
     'visas': [VISA_COLLECTIVITES + 'L2212-1, L2212-2 et L2213-2 ;',
               'Vu le code de la route, notamment ses articles R411-25 et R417-10 ;',
               'Vu la demande présentée en mairie par un riverain de la rue des Maquettes ;'],
     'considerants': ['Considérant qu’un déménagement aura lieu au n° 8 de la rue des Maquettes et qu’un camion '
                      'doit pouvoir stationner devant l’immeuble ;',
                      'Considérant qu’il convient de réglementer temporairement le stationnement pour permettre '
                      'ce déménagement sans gêner les autres usagers ;'],
     'mesures': ['Le samedi 4 avril 2026, de 7 h 00 à 19 h 00, le stationnement de tout véhicule est interdit rue des '
                 'Maquettes, devant le n° 8 et sur une longueur de vingt mètres.',
                 'À l’exception du véhicule du déménagement, des véhicules de secours et de collecte des déchets, '
                 'cette interdiction s’applique à tous les véhicules.',
                 'Des panneaux de stationnement interdit seront posés par les services techniques de la commune, '
                 '48 heures au moins avant l’heure d’effet de la mesure, puis retirés.'],
     'date': 'Fait à Démoville, le 27 mars 2026'},
    {'id': 'demo-vitesse-0002-exemple-fictif',
     'title': 'Exemple fictif — limitation de la vitesse à 30 km/h avenue de l’Exemple',
     'titre': 'Arrêté fictif de vitesse — exemple de démonstration',
     'numero': 'ARRÊTÉ MUNICIPAL N° DEMO-2026-021',
     'objet': 'portant limitation de la vitesse à 30 km/h avenue de l’Exemple',
     'visas': [VISA_COLLECTIVITES + 'L2212-1, L2212-2 et L2213-1 ;',
               'Vu le code de la route, notamment ses dispositions relatives aux limitations de vitesse ;',
               'Vu l’avis des services techniques de la commune de Démoville ;'],
     'considerants': ['Considérant que l’avenue de l’Exemple est très fréquentée par des piétons et des cyclistes '
                      'entre la place du Test et la rue des Maquettes ;',
                      'Considérant qu’il convient de réduire la vitesse des véhicules sur ce tronçon de voirie '
                      'pour garantir la sécurité de ces usagers ;'],
     'mesures': ['À compter de la pose de la signalisation, la vitesse maximale autorisée est fixée à 30 km/h de façon '
                 'permanente avenue de l’Exemple, entre la place du Test et la rue des Maquettes, dans les deux sens '
                 'de circulation.',
                 'Par dérogation à l’article 1, les véhicules des services de secours en intervention ne sont pas '
                 'tenus de respecter cette limitation, dans les conditions prévues par le code de la route.',
                 'Des panneaux de limitation de vitesse et de zone 30 seront mis en place aux deux extrémités du '
                 'tronçon par les services techniques de la commune.'],
     'date': 'Fait à Démoville, le 12 mai 2026'},
    {'id': 'demo-marche-0003-exemple-fictif',
     'title': 'Exemple fictif — marché communal du samedi place du Test',
     'titre': 'Arrêté fictif de marché communal — exemple de démonstration',
     'numero': 'ARRÊTÉ MUNICIPAL N° DEMO-2026-032',
     'objet': 'portant déplacement temporaire du marché communal du samedi',
     'visas': [VISA_COLLECTIVITES + 'L2212-1, L2212-2 et L2224-18 ;',
               'Vu le règlement du marché communal de Démoville ;',
               'Vu la demande des commerçants non sédentaires du marché communal ;'],
     'considerants': ['Considérant que des travaux de réfection de la place du Test ne permettent plus d’y installer '
                      'les étals du marché communal du samedi ;',
                      'Considérant qu’il convient d’assurer la continuité de l’activité des commerçants non '
                      'sédentaires dans des conditions de sécurité satisfaisantes ;'],
     'mesures': ['Du samedi 11 avril 2026 au samedi 6 juin 2026, le marché communal du samedi se tient rue des '
                 'Maquettes, de 7 h 00 à 13 h 30, au lieu de la place du Test.',
                 'Sauf autorisation expresse du maire, seuls les commerçants titulaires d’un emplacement au marché '
                 'communal peuvent s’installer sur le nouveau site.',
                 'Des panneaux d’information indiquant le nouveau lieu du marché seront affichés place du Test et '
                 'aux abords de la rue des Maquettes par les services techniques de la commune.'],
     'date': 'Fait à Démoville, le 30 mars 2026'},
]
IDENTIFIANTS = frozenset(exemple['id'] for exemple in EXEMPLES)


def _styles():
    base = {'fontName': 'Helvetica', 'fontSize': 10.5, 'leading': 14.5, 'spaceAfter': 6}
    return {'normal': ParagraphStyle('normal', **base),
            'centre': ParagraphStyle('centre', **{**base, 'alignment': 1, 'fontName': 'Helvetica-Bold', 'fontSize': 12}),
            'mention': ParagraphStyle('mention', **{**base, 'alignment': 1, 'fontSize': 8.5, 'textColor': HexColor('#555555')}),
            'titre': ParagraphStyle('titre', **{**base, 'alignment': 1, 'fontName': 'Helvetica-Bold', 'fontSize': 13,
                                                'spaceBefore': 10}),
            'decision': ParagraphStyle('decision', **{**base, 'alignment': 1, 'fontName': 'Helvetica-Bold',
                                                      'spaceBefore': 8, 'spaceAfter': 8})}


def _pdf(document):
    """Construit le PDF d'un arrêté fictif. `invariant` rend les octets reproductibles d'une installation à l'autre."""
    styles = _styles()
    histoire = [Paragraph('COMMUNE DE DÉMOVILLE', styles['centre']),
                Paragraph('Collectivité imaginaire — exemple de démonstration', styles['mention']),
                Paragraph(escape(document['numero']), styles['titre']),
                Paragraph(escape(document['objet']), styles['centre']),
                Spacer(1, 8),
                Paragraph('Le Maire de la Commune de Démoville,', styles['normal'])]
    histoire += [Paragraph(escape(texte), styles['normal']) for texte in document['visas'] + document['considerants']]
    histoire.append(Paragraph('ARRÊTE', styles['decision']))
    articles = document['mesures'] + [INFRACTIONS, PUBLICITE, EXECUTION]
    for numero, texte in enumerate(articles, 1):
        histoire.append(Paragraph('<b>Article %d</b> : %s' % (numero, escape(texte)), styles['normal']))
    histoire += [Spacer(1, 10), Paragraph(escape(document['date']), styles['normal']),
                 Paragraph('Le Maire,', styles['normal']),
                 Paragraph('[Signature fictive : aucun acte réel]', styles['mention'])]

    def pied(canvas, _document):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.drawCentredString(A4[0] / 2, 1.2 * cm, MENTION)
        canvas.restoreState()

    sortie = io.BytesIO()
    SimpleDocTemplate(sortie, pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm, topMargin=2 * cm,
                      bottomMargin=2.2 * cm, title=document['titre'], author='TerritorIA — démonstration',
                      invariant=1).build(histoire, onFirstPage=pied, onLaterPages=pied)
    return sortie.getvalue()


def _ecrire(chemin, contenu):
    temporaire = chemin.with_name('.' + chemin.name + '.tmp')
    temporaire.write_bytes(contenu)
    os.chmod(temporaire, 0o600)
    os.replace(temporaire, chemin)


def _ecrire_json(chemin, donnees):
    _ecrire(chemin, json.dumps(donnees, ensure_ascii=False, indent=2).encode('utf-8'))


def _dossier(chemin):
    chemin.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(chemin, 0o700)


def _bibliotheque_etrangere(manifeste):
    """Vrai si la bibliothèque existante contient autre chose que les exemples de démonstration."""
    if not manifeste.exists():
        return False
    try:
        fichiers = json.loads(manifeste.read_text(encoding='utf-8')).get('files', [])
        return any(not isinstance(item, dict) or item.get('id') not in IDENTIFIANTS for item in fichiers)
    except (OSError, ValueError, AttributeError, TypeError):
        return True


def installer(data_dir):
    """Crée le modèle de référence et les trois exemples fictifs ; renvoie False si un modèle est déjà installé.

    modeles/manifest.json est écrit en dernier : tant que l'indexation n'a pas réussi, une nouvelle exécution recommence.
    """
    data = Path(data_dir)
    modeles = data / 'modeles'
    bibliotheque = data / 'bibliotheque-arretes'
    if (modeles / 'manifest.json').exists() or _bibliotheque_etrangere(bibliotheque / 'manifest.json'):
        return False
    for dossier in (modeles, bibliotheque, bibliotheque / 'pdf'):
        _dossier(dossier)

    reference = _pdf(DOCUMENT_REFERENCE)
    _ecrire(modeles / REFERENCE, reference)
    modifie = datetime.now(timezone.utc).isoformat(timespec='seconds')
    fichiers = []
    for exemple in EXEMPLES:
        brut = _pdf(exemple)
        nom = exemple['id'] + '.pdf'
        _ecrire(bibliotheque / 'pdf' / nom, brut)
        fichiers.append({'id': exemple['id'], 'title': exemple['title'], 'filename': nom,
                         'sha256': hashlib.sha256(brut).hexdigest(), 'size': len(brut), 'url': '', 'modified': modifie})
    _ecrire_json(bibliotheque / 'manifest.json', {'files': fichiers})
    bilan = corpus_arretes.build_index(data)
    if bilan['errors'] or bilan['documents'] != len(fichiers):
        raise RuntimeError('Les exemples de démonstration n’ont pas pu être indexés.')
    _ecrire_json(modeles / 'manifest.json', {'reference': REFERENCE, 'sha256': hashlib.sha256(reference).hexdigest(),
                                             'trame': TRAME})
    return True
