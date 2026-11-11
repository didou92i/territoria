# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Dossier de publication à déposer sur le site de la collectivité : un PDF anonymisé par acte (format non modifiable,
avec sa date de mise en ligne), un index de tous les actes publiés, et le CSV national des délibérations (schéma SCDL
« Délibérations » 2.1.6). Rien n'est envoyé : l'agent dépose le ZIP lui-même."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import anonymisation, nomenclature

TYPES = {'arrete': 'Arrêté', 'deliberation': 'Délibération'}
COLONNES = ('COLL_NOM', 'COLL_SIRET', 'DELIB_ID', 'DELIB_DATE', 'DELIB_MATIERE_CODE', 'DELIB_MATIERE_NOM', 'DELIB_OBJET',
            'BUDGET_ANNEE', 'BUDGET_NOM', 'PREF_ID', 'PREF_DATE', 'VOTE_EFFECTIF', 'VOTE_REEL', 'VOTE_POUR', 'VOTE_CONTRE',
            'VOTE_ABSTENTION', 'DELIB_URL')
VOTES = {'VOTE_EFFECTIF': 'effectif', 'VOTE_REEL': 'reel', 'VOTE_POUR': 'pour', 'VOTE_CONTRE': 'contre', 'VOTE_ABSTENTION': 'abstention'}
MESSAGE_SIRET = 'Indiquez le SIRET de la collectivité dans Administration › Collectivité.'
MESSAGE_DATE = 'La date de mise en ligne ne peut pas être passée.'
LISEZ_MOI = """Dossier de publication préparé par TerritorIA

1. Déposez les fichiers PDF et index.html dans la rubrique des actes de votre site internet
   (à l'adresse indiquée dans Administration › Collectivité, si vous l'avez renseignée).
2. Déposez deliberations.csv sur votre espace open data, ou sur data.gouv.fr (schéma « Délibérations »).
3. Revenez dans TerritorIA, page Publication, et cliquez « Mis en ligne » pour chaque acte déposé.

Chaque PDF porte la date de mise en ligne choisie : déposez-le ce jour-là, sinon préparez un nouveau dossier.
Les données personnelles cochées à la relecture ont été remplacées par « [nom masqué] », « [adresse masquée] »…
"""


def jour_courant() -> date:
    """Le jour à Paris : celui des collectivités, quel que soit le fuseau de la machine."""
    return datetime.now(ZoneInfo('Europe/Paris')).date()


def _jour(iso: str) -> str:
    return '/'.join(reversed(iso.split('-')))


def nom_fichier(numero: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '-', numero) + '.pdf'


def objet_masque(acte: dict) -> str:
    """L'objet aussi peut citer un particulier : mêmes passages cochés que le texte."""
    return anonymisation.masquer(acte['objet'], acte.get('passages', []))


def paragraphes_masques(acte: dict) -> list[str]:
    return [anonymisation.masquer(p, acte.get('passages', [])) for p in acte['paragraphes']]


def _winansi(texte: str) -> str:
    """Police standard du PDF (Helvetica, codage WinAnsi) : `œ`, `’`, `«`, `€` passent ; espaces fines et ligatures (texte
    extrait d'un PDF) sont normalisées d'abord (NFKC) ; un caractère qui reste hors du codage devient `?`."""
    texte = unicodedata.normalize('NFKC', texte).replace('≥', '>=').replace('≤', '<=')
    return texte.encode('cp1252', errors='replace').decode('cp1252')


def pdf(acte: dict, paragraphes_masques: list[str], collectivite: str, date_mise_en_ligne: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    def texte(valeur: str) -> str:
        return html.escape(_winansi(valeur), quote=False)

    styles = getSampleStyleSheet()
    mention = _winansi(f'Acte publié sous forme électronique le {_jour(date_mise_en_ligne)} — document anonymisé (RGPD)')
    numero = _winansi(f'{TYPES[acte["type"]]} n° {acte["numero"]}')

    def pied(canevas, document):
        canevas.saveState()
        canevas.setFont('Helvetica', 8)
        canevas.drawString(2 * cm, 1.5 * cm, mention)
        canevas.drawRightString(A4[0] - 2 * cm, 1.0 * cm, f'{numero} — page {document.page}')
        canevas.restoreState()

    sortie = io.BytesIO()
    document = SimpleDocTemplate(sortie, pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm, topMargin=2 * cm,
                                 bottomMargin=2.5 * cm, title=numero, author=_winansi(collectivite),
                                 invariant=1)  # ni date de création ni identifiant aléatoire : même acte, même fichier, même empreinte
    contenu = [Paragraph(texte(collectivite), styles['Heading3']), Paragraph(texte(numero), styles['Title']),
               Paragraph(texte(f'du {_jour(acte["date_acte"])}'), styles['Normal']), Spacer(1, 6),
               Paragraph('<b>Objet :</b> ' + texte(objet_masque(acte)), styles['Normal']), Spacer(1, 12)]
    contenu += [Paragraph(texte(p), styles['BodyText']) for p in paragraphes_masques]
    document.build(contenu, onFirstPage=pied, onLaterPages=pied)
    return sortie.getvalue()


def _lien(adresse_actes: str, acte: dict) -> str:
    return (adresse_actes or '') + nom_fichier(acte['numero'])


def index_html(actes: list[dict], adresse_actes: str, collectivite: str = '') -> str:
    """Page autonome (ni script ni ressource extérieure) : les actes publiés, du plus récent au plus ancien."""
    e = html.escape
    lignes = ''.join(
        f'<tr><td>{e(a["numero"])}</td><td>{e(TYPES[a["type"]])}</td><td>{e(_jour(a["date_acte"]))}</td>'
        f'<td>{e(objet_masque(a))}</td><td>{e(_jour(a["mise_en_ligne"]))}</td>'
        f'<td><a href="{e(_lien(adresse_actes, a))}">PDF</a></td></tr>'
        for a in sorted(actes, key=lambda a: (a['date_acte'], a['numero']), reverse=True))
    titre = 'Actes publiés' + (f' — {collectivite}' if collectivite else '')
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1">'
            f'<title>{e(titre)}</title><style>body{{font-family:Arial,sans-serif;margin:2rem;color:#273247}}'
            'table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #dde3ea;padding:.5rem;text-align:left;'
            'vertical-align:top}th{background:#f4f6f8}</style></head><body>'
            f'<h1>{e(titre)}</h1><p>Actes publiés sous forme électronique, anonymisés conformément au RGPD.</p>'
            '<table><thead><tr><th>Numéro</th><th>Type</th><th>Date de l’acte</th><th>Objet</th><th>Mis en ligne le</th>'
            f'<th>Document</th></tr></thead><tbody>{lignes}</tbody></table></body></html>')


def csv_deliberations(actes: list[dict], collectivite: str, siret: str, adresse_actes: str) -> bytes:
    """CSV au schéma « Délibérations » SCDL 2.1.6 : délibérations seulement, virgule, UTF-8 sans BOM."""
    sortie = io.StringIO()
    ecrivain = csv.DictWriter(sortie, fieldnames=COLONNES, lineterminator='\n')
    ecrivain.writeheader()
    for a in sorted((a for a in actes if a['type'] == 'deliberation'), key=lambda a: (a['date_acte'], a['numero'])):
        ligne = dict.fromkeys(COLONNES, '')
        ligne |= {'COLL_NOM': collectivite, 'COLL_SIRET': siret, 'DELIB_ID': a['numero'], 'DELIB_DATE': a['date_acte'],
                  'DELIB_MATIERE_CODE': a['matiere_code'], 'DELIB_MATIERE_NOM': nomenclature.nom_scdl(a['matiere_code']),
                  'DELIB_OBJET': objet_masque(a), 'DELIB_URL': _lien(adresse_actes, a) if adresse_actes else ''}
        for colonne, cle in VOTES.items():
            if cle in (a.get('votes') or {}):
                ligne[colonne] = str(a['votes'][cle])
        ecrivain.writerow(ligne)
    return sortie.getvalue().encode('utf-8')


def dossier(actes_a_publier: list[dict], deja_publies: list[dict], profil: dict,
            date_mise_en_ligne: str) -> tuple[bytes, dict[str, dict]]:
    """ZIP du dossier et, pour chaque nouvel acte, son PDF et l'empreinte SHA-256 de ce PDF ; les actes déjà publiés
    restent dans l'index et le CSV."""
    if not actes_a_publier:
        raise ValueError('Choisissez au moins un acte validé.')
    if date.fromisoformat(date_mise_en_ligne) < jour_courant():
        raise ValueError(MESSAGE_DATE)
    tous = [a | {'mise_en_ligne': date_mise_en_ligne} for a in actes_a_publier] + \
           [a | {'mise_en_ligne': a.get('publie_le') or a.get('mise_en_ligne', '')} for a in deja_publies]
    if any(a['type'] == 'deliberation' for a in tous) and not profil.get('siret'):
        raise ValueError(MESSAGE_SIRET)
    collectivite, adresse = profil.get('collectivite', ''), profil.get('adresse_actes', '')
    empreintes, sortie = {}, io.BytesIO()
    with zipfile.ZipFile(sortie, 'w', zipfile.ZIP_DEFLATED) as archive:
        for a in actes_a_publier:
            octets = pdf(a, paragraphes_masques(a), collectivite, date_mise_en_ligne)
            empreintes[a['id']] = {'empreinte': hashlib.sha256(octets).hexdigest(), 'pdf': octets}
            archive.writestr(nom_fichier(a['numero']), octets)
        archive.writestr('index.html', index_html(tous, adresse, collectivite))
        archive.writestr('deliberations.csv', csv_deliberations(tous, collectivite, profil.get('siret', ''), adresse))
        archive.writestr('LISEZ-MOI.txt', LISEZ_MOI)
    return sortie.getvalue(), empreintes
