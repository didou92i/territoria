# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Numérotation des actes et registre : un compteur pour les arrêtés (toutes familles), un autre pour les délibérations,
remis à 1 chaque année (ou au numéro de départ fixé par l'administration). Un numéro est attribué sur demande, quand
l'acte est prêt à signer, jamais à la rédaction : un projet abandonné ne laisse pas de trou. Un numéro n'est jamais réutilisé.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .base.connexion import ouvrir

TYPES = {'arrete': 'Arrêté', 'deliberation': 'Délibération'}
FORMATS_DEFAUT = {'arrete': '{annee}-{numero}', 'deliberation': 'D{annee}-{numero}'}
FORMAT = re.compile(r'(?:[A-Za-z0-9 ./_-]|\{annee\}|\{numero\}){1,40}')


def _annee() -> int:
    return datetime.now(ZoneInfo('Europe/Paris')).year


def _enregistres(c) -> dict:
    ligne = c.execute("SELECT valeur FROM parametres WHERE cle='numerotation'").fetchone()
    try:
        valeur = json.loads(ligne['valeur']) if ligne else {}
        return valeur if isinstance(valeur, dict) else {}
    except ValueError:
        return {}


def _format(enregistres, genre) -> str:
    valeur = (enregistres.get(genre) or {}).get('format')
    return valeur if isinstance(valeur, str) and FORMAT.fullmatch(valeur) and '{numero}' in valeur else FORMATS_DEFAUT[genre]


def _depart(enregistres, genre, annee) -> int:
    valeur = ((enregistres.get(genre) or {}).get('depart') or {}).get(str(annee), 1)
    return valeur if isinstance(valeur, int) and valeur >= 1 else 1


def _prochain(c, enregistres, genre, annee) -> int:
    dernier = c.execute('SELECT MAX(sequence) FROM registre_actes WHERE type=? AND annee=?', (genre, annee)).fetchone()[0] or 0
    return max(dernier + 1, _depart(enregistres, genre, annee))


def reglages(base: Path) -> dict:
    annee = _annee()
    with ouvrir(base) as c:
        enregistres = _enregistres(c)
        return {genre: {'format': _format(enregistres, genre), 'prochain': _prochain(c, enregistres, genre, annee), 'annee': annee}
                for genre in TYPES}


def enregistrer_reglages(base: Path, nouveau) -> dict:
    """Format ({annee} et {numero}, ce dernier sur trois chiffres) et prochain numéro de l'année, par type d'acte."""
    if not isinstance(nouveau, dict) or not nouveau or set(nouveau) - set(TYPES):
        raise ValueError('Réglages de numérotation invalides.')
    annee = _annee()
    with ouvrir(base) as c:
        enregistres = _enregistres(c)
        for genre, valeurs in nouveau.items():
            if not isinstance(valeurs, dict) or set(valeurs) - {'format', 'prochain'}:
                raise ValueError('Réglages de numérotation invalides.')
            courant = dict(enregistres.get(genre) or {})
            if 'format' in valeurs:
                fmt = valeurs['format']
                if not isinstance(fmt, str) or not FORMAT.fullmatch(fmt) or '{numero}' not in fmt:
                    raise ValueError('Le format doit contenir {numero}, et seulement des lettres, chiffres, espaces, « . / _ - », '
                                     '{annee} et {numero} (40 caractères au plus).')
                courant['format'] = fmt
            if 'prochain' in valeurs:
                prochain = valeurs['prochain']
                if isinstance(prochain, bool) or not isinstance(prochain, int) or not 1 <= prochain <= 999999:
                    raise ValueError('Le prochain numéro doit être un entier positif.')
                dernier = c.execute('SELECT MAX(sequence) FROM registre_actes WHERE type=? AND annee=?', (genre, annee)).fetchone()[0] or 0
                if prochain <= dernier:
                    raise ValueError(f'Le numéro {prochain} est déjà passé cette année : le prochain possible est {dernier + 1}.')
                courant['depart'] = {str(annee): prochain}
            enregistres[genre] = courant
        c.execute("INSERT INTO parametres(cle, valeur, modifie) VALUES ('numerotation', ?, ?) "
                  "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur, modifie=excluded.modifie",
                  (json.dumps(enregistres, ensure_ascii=False), datetime.now(ZoneInfo('Europe/Paris')).isoformat(timespec='seconds')))
    return reglages(base)


def attribuer(base: Path, acte_id: str, genre: str, objet: str, utilisateur: str = '') -> str:
    """Numéro de l'acte : celui déjà attribué, sinon le prochain de son type pour l'année (calculé et réservé par une seule
    instruction, donc sans doublon entre deux demandes simultanées)."""
    annee = _annee()
    with ouvrir(base) as c:
        deja = c.execute('SELECT numero FROM registre_actes WHERE acte_id=?', (acte_id,)).fetchone()
        if deja:
            return deja['numero']
        enregistres = _enregistres(c)
        try:
            c.execute("INSERT INTO registre_actes(type, annee, sequence, numero, acte_id, objet, attribue_le, attribue_par) "
                      "SELECT :type, :annee, s, replace(replace(:format, '{annee}', :annee), '{numero}', printf('%03d', s)), "
                      ":acte, :objet, :quand, :par FROM (SELECT MAX(COALESCE(MAX(sequence), 0) + 1, :depart) AS s FROM registre_actes "
                      "WHERE type=:type AND annee=:annee)",
                      {'type': genre, 'annee': annee, 'format': _format(enregistres, genre), 'acte': acte_id, 'objet': objet[:300],
                       'quand': datetime.now(ZoneInfo('Europe/Paris')).isoformat(timespec='seconds'), 'par': utilisateur or '',
                       'depart': _depart(enregistres, genre, annee)})
        except sqlite3.IntegrityError as exc:
            deja = c.execute('SELECT numero FROM registre_actes WHERE acte_id=?', (acte_id,)).fetchone()
            if deja:  # deux demandes simultanées pour le même acte
                return deja['numero']
            raise ValueError('Ce numéro existe déjà dans le registre : ajoutez {annee} au format de numérotation.') from exc
        return c.execute('SELECT numero FROM registre_actes WHERE acte_id=?', (acte_id,)).fetchone()['numero']


def liste(base: Path, annee: int | None = None) -> list[dict]:
    with ouvrir(base) as c:
        lignes = c.execute('SELECT r.numero, r.type, r.annee, r.objet, r.attribue_le, COALESCE(u.nom, \'\') AS par FROM registre_actes r '
                           'LEFT JOIN utilisateurs u ON u.id=r.attribue_par WHERE (? IS NULL OR r.annee=?) '
                           'ORDER BY r.attribue_le DESC, r.id DESC', (annee, annee)).fetchall()
    return [{'numero': l['numero'], 'type': l['type'], 'annee': l['annee'], 'objet': l['objet'], 'date': l['attribue_le'][:10],
             'par': l['par']} for l in lignes]


def csv_annee(base: Path, annee: int) -> bytes:
    sortie = io.StringIO()
    ecrivain = csv.writer(sortie, delimiter=';', lineterminator='\n')
    ecrivain.writerow(['Numéro', 'Type', 'Date', 'Objet'])
    for acte in reversed(liste(base, annee)):  # ordre chronologique
        ecrivain.writerow([acte['numero'], TYPES.get(acte['type'], acte['type']), '/'.join(reversed(acte['date'].split('-'))), acte['objet']])
    return sortie.getvalue().encode('utf-8-sig')  # BOM : accents corrects à l'ouverture dans un tableur


MOIS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'septembre': 9,
        'octobre': 10, 'novembre': 11, 'decembre': 12}
_ACTE = re.compile(r'[0-9a-f]{32}')


def date_francaise(texte: str) -> str | None:
    """« 15 décembre 2026 », « 1er décembre 2026 » ou « 15/12/2026 » → '2026-12-15' ; sinon None."""
    simple = ''.join(c for c in unicodedata.normalize('NFKD', str(texte or '')) if not unicodedata.combining(c)).lower()
    trouve = re.search(r'\b(\d{1,2})(?:er)?\s+([a-z]+)\s+(\d{4})\b', simple)
    if trouve and trouve.group(2) in MOIS:
        jour, mois, annee = int(trouve.group(1)), MOIS[trouve.group(2)], int(trouve.group(3))
    else:
        trouve = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', simple)
        if not trouve:
            return None
        jour, mois, annee = (int(trouve.group(i)) for i in (1, 2, 3))
    try:
        return datetime(annee, mois, jour).strftime('%Y-%m-%d')
    except ValueError:
        return None


def deliberations_du(base: Path, donnees: Path, date_iso: str) -> list[dict]:
    """Délibérations numérotées dont la fiche porte la séance de cette date (« Séance du … » du préambule), dans l'ordre
    des numéros : l'ordre du jour proposé du procès-verbal. Une fiche absente ou illisible est ignorée."""
    with ouvrir(base) as c:
        lignes = c.execute("SELECT numero, acte_id, objet FROM registre_actes WHERE type='deliberation' "
                           'ORDER BY annee, sequence').fetchall()
    trouvees = []
    for ligne in lignes:
        if not _ACTE.fullmatch(ligne['acte_id']):
            continue
        try:
            fiche = json.loads((Path(donnees) / 'arretes' / f"{ligne['acte_id']}.json").read_text(encoding='utf-8'))
            preambule = fiche['document'].get('preambule') or []
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        for texte in preambule:
            seance = re.match(r'\s*s[ée]ance\s+du\s+(.+)', str(texte), re.IGNORECASE)
            if seance and date_francaise(seance.group(1)) == date_iso:
                trouvees.append({'numero': ligne['numero'], 'objet': ligne['objet']})
                break
    return trouvees
