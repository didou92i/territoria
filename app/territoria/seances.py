# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Séances (conseil municipal ou réunion) : création, envoi de l'audio par morceaux, relevé de la transcription.

L'audio d'une séance vit dans `territoria-donnees/territoria/audio/<id>/`, partagé avec le conteneur de travaux selon le
protocole par fichiers (`travaux.protocole`) : Territoria y assemble `source.<format>` morceau par morceau (suivi dans
`envoi.json`), puis dépose `demande.json` ; il relève ensuite `progres.json`, `resultat.json` ou `erreur.json` et copie le
résultat dans la base. Une séance n'est visible que de son auteur : toute autre lecture répond comme une séance inconnue.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from travaux import protocole

from . import deliberations, registre, seances_pv
from .base.connexion import ouvrir

MORCEAU_MAX = 8 * 1024 * 1024
TOTAL_MAX = 1024 ** 3
ABANDON_JOURS = 7
FORMATS = frozenset({'webm', 'ogg', 'mp3', 'm4a', 'mp4', 'wav', 'aac', 'flac', 'mov'})
TYPES = ('conseil', 'reunion')
ENVOI = 'envoi.json'
_IDENT = re.compile(r'[0-9a-f]{32}')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_HEURE = re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d')
_envois = threading.Lock()  # un morceau à la fois : deux envois simultanés ne s'entrelacent pas dans le fichier


class Conflit(Exception):
    """Morceau arrivé en avance : le navigateur doit reprendre à `rang_suivant`."""

    def __init__(self, rang_suivant: int):
        super().__init__(f'rang attendu : {rang_suivant}')
        self.rang_suivant = rang_suivant


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def dossier_audio(donnees: Path, ident: str) -> Path:
    if not _IDENT.fullmatch(str(ident)):
        raise ValueError('identifiant de séance invalide')
    return Path(donnees) / 'audio' / ident


def _dossier_fiable(dossier: Path) -> bool:
    """Vrai si le dossier audio est absent ou un vrai dossier : le conteneur de travaux, qui écrit aussi dans `audio/`,
    pourrait le remplacer par un lien vers la base ou les documents."""
    try:
        return stat.S_ISDIR(os.lstat(dossier).st_mode)
    except FileNotFoundError:
        return True


def _ordinaire(chemin: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(chemin).st_mode)
    except OSError:
        return False


def _effacer_dossier(dossier: Path) -> None:
    if os.path.islink(dossier):
        os.unlink(dossier)
    else:
        shutil.rmtree(dossier, ignore_errors=True)


def _texte(donnees: dict, cle: str, minimum: int, maximum: int) -> str:
    valeur = donnees.get(cle)
    valeur = '' if valeur is None else valeur
    if not isinstance(valeur, str) or not minimum <= len(valeur.strip()) <= maximum:
        raise ValueError(f'{cle} : entre {minimum} et {maximum} caractères')
    return valeur.strip()


def creer(base: Path, proprietaire: str, donnees: dict) -> str:
    if donnees.get('type') not in TYPES:
        raise ValueError('type : conseil ou reunion')
    titre, lieu = _texte(donnees, 'titre', 1, 200), _texte(donnees, 'lieu', 0, 200)
    date, heure = donnees.get('date'), donnees.get('heure') or None
    try:
        if not isinstance(date, str) or not _DATE.fullmatch(date):
            raise ValueError
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise ValueError('date : AAAA-MM-JJ') from None
    if heure is not None and (not isinstance(heure, str) or not _HEURE.fullmatch(heure)):
        raise ValueError('heure : HH:MM')
    ident, maintenant = secrets.token_hex(16), _maintenant()
    with ouvrir(base) as c:
        c.execute('INSERT INTO seances(id, proprietaire, type, titre, date, heure, lieu, etat, cree, modifie) '
                  "VALUES (?, ?, ?, ?, ?, ?, ?, 'creee', ?, ?)",
                  (ident, proprietaire, donnees['type'], titre, date, heure, lieu, maintenant, maintenant))
    return ident


def lister(base: Path, proprietaire: str) -> list[dict]:
    with ouvrir(base) as c:
        lignes = c.execute('SELECT id, type, titre, date, etat, progression, cree FROM seances WHERE proprietaire=? '
                           'ORDER BY cree DESC, rowid DESC', (proprietaire,)).fetchall()
    return [dict(ligne) for ligne in lignes]


def _seance(c, ident: str, proprietaire: str):
    ligne = c.execute('SELECT * FROM seances WHERE id=? AND proprietaire=?', (ident, proprietaire)).fetchone()
    if ligne is None:
        raise LookupError('séance inconnue')
    return ligne


def _audio_present(donnees: Path, ident: str) -> bool:
    dossier = dossier_audio(donnees, ident)
    if not _dossier_fiable(dossier) or not dossier.exists():
        return False
    return any(p.name.startswith('source.') and _ordinaire(p) for p in dossier.iterdir())


def _json(texte, defaut):
    try:
        valeur = json.loads(texte) if texte else defaut
    except ValueError:
        return defaut
    return valeur if isinstance(valeur, type(defaut)) else defaut


def _points(c, ident: str) -> list[dict]:
    """Points de l'ordre du jour ; `a_jour` : teneur rédigée d'après les passages actuels du point (même empreinte)."""
    lignes = c.execute('SELECT id, rang, titre, deliberation_numero, rapporteur, rapport, vote, scrutin_particulier, debut, '
                       'teneur, erreur, empreinte FROM seance_points WHERE seance_id=? ORDER BY rang, id', (ident,)).fetchall()
    points = [dict(ligne, vote=_json(ligne['vote'], {}) or None, scrutin_particulier=bool(ligne['scrutin_particulier']),
                   teneur=_json(ligne['teneur'], []) or None) for ligne in lignes]
    if points:
        segments = [dict(s) for s in c.execute('SELECT debut, etiquette, texte FROM seance_segments WHERE seance_id=? '
                                               'ORDER BY debut, id', (ident,))]
        passages = seances_pv.passages_par_point(segments, points, _intervenants(c, ident))
        for point, extraits in zip(points, passages):
            point['a_jour'] = bool(point['teneur']) and point['empreinte'] == seances_pv.empreinte(extraits)
    return points


def lire(base: Path, ident: str, proprietaire: str, donnees: Path | None = None) -> dict:
    with ouvrir(base) as c:
        seance = dict(_seance(c, ident, proprietaire))
        intervenants = c.execute(
            'SELECT i.etiquette, i.nom FROM seance_intervenants i LEFT JOIN (SELECT etiquette, MIN(debut) AS premier '
            'FROM seance_segments WHERE seance_id=? GROUP BY etiquette) s ON s.etiquette = i.etiquette '
            'WHERE i.seance_id=? ORDER BY s.premier IS NULL, s.premier, i.etiquette', (ident, ident)).fetchall()
        segments = c.execute('SELECT id, debut, fin, etiquette, texte, corrige FROM seance_segments WHERE seance_id=? '
                             'ORDER BY debut, id', (ident,)).fetchall()
        seance['points'] = _points(c, ident)
    seance.pop('proprietaire')
    seance['details'] = _json(seance['details'], {})
    seance['redaction'] = _json(seance['redaction'], {})
    seance['pv_a_jour'] = bool(seance['points']) and all(point['a_jour'] for point in seance['points'])
    seance['intervenants'] = [dict(i) for i in intervenants]
    seance['segments'] = [dict(s, corrige=bool(s['corrige'])) for s in segments]
    seance['audio'] = _audio_present(Path(base).parent if donnees is None else donnees, ident)
    return seance


def ajouter_morceau(base: Path, donnees: Path, ident: str, proprietaire: str, rang: int, octets: bytes, format_: str) -> int:
    if format_ not in FORMATS:
        raise ValueError('format audio non pris en charge')
    if not 0 < len(octets) <= MORCEAU_MAX:
        raise ValueError('morceau vide ou trop gros')
    if not isinstance(rang, int) or rang < 0:
        raise ValueError('rang invalide')
    with _envois, ouvrir(base) as c:
        seance = _seance(c, ident, proprietaire)
        if seance['etat'] not in ('creee', 'reception'):
            raise ValueError("l'envoi de cette séance est terminé")
        dossier = dossier_audio(donnees, ident)
        if not _dossier_fiable(dossier):
            raise ValueError('dossier audio invalide')
        envoi = protocole.lire_json(dossier / ENVOI) or {'rang_suivant': 0, 'format': None, 'octets': 0}
        if envoi['format'] not in (None, format_):
            raise ValueError("format différent du début de l'envoi")
        if rang < envoi['rang_suivant']:
            return envoi['rang_suivant']  # renvoi d'un morceau déjà reçu : ignoré
        if rang > envoi['rang_suivant']:
            raise Conflit(envoi['rang_suivant'])
        if envoi['octets'] + len(octets) > TOTAL_MAX:
            raise ValueError('enregistrement trop volumineux')
        dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = dossier / f'source.{format_}'
        try:  # jamais à travers un lien ni dans un tube : seulement un fichier ordinaire
            descripteur = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        except OSError:
            raise ValueError('fichier audio invalide') from None
        try:
            if not stat.S_ISREG(os.fstat(descripteur).st_mode):
                raise ValueError('fichier audio invalide')
            os.ftruncate(descripteur, envoi['octets'])  # écarte un morceau à moitié écrit avant une coupure
            os.lseek(descripteur, envoi['octets'], os.SEEK_SET)
            reste = memoryview(octets)
            while reste:  # une écriture peut être partielle
                reste = reste[os.write(descripteur, reste):]
            os.fsync(descripteur)
        finally:
            os.close(descripteur)
        suivant = rang + 1
        protocole.ecrire_json(dossier / ENVOI, {'rang_suivant': suivant, 'format': format_, 'octets': envoi['octets'] + len(octets),
                                                'maj': _maintenant()})
        c.execute("UPDATE seances SET etat='reception', modifie=? WHERE id=?", (_maintenant(), ident))
    return suivant


def terminer_envoi(base: Path, donnees: Path, ident: str, proprietaire: str, modele: str) -> None:
    with _envois, ouvrir(base) as c:
        seance = _seance(c, ident, proprietaire)
        dossier = dossier_audio(donnees, ident)
        envoi = protocole.lire_json(dossier / ENVOI) if _dossier_fiable(dossier) else None
        if seance['etat'] != 'reception' or not envoi or not envoi.get('octets'):
            raise ValueError("aucun audio reçu pour cette séance")
        protocole.ecrire_json(dossier / protocole.DEMANDE, {'version': protocole.VERSION, 'langue': 'fr', 'modele': modele,
                                                            'source': f"source.{envoi['format']}"})
        c.execute("UPDATE seances SET etat='attente', erreur=NULL, progression=NULL, modifie=? WHERE id=?", (_maintenant(), ident))


def _segments_valides(resultat: dict) -> list[dict] | None:
    segments = resultat.get('segments')
    if not isinstance(segments, list):
        return None
    valides = []
    for s in segments:
        if (isinstance(s, dict) and isinstance(s.get('debut'), (int, float)) and isinstance(s.get('fin'), (int, float))
                and isinstance(s.get('intervenant'), str) and isinstance(s.get('texte'), str)):
            valides.append(s)
    return valides


def relever(base: Path, donnees: Path) -> int:
    """Copie dans la base ce que le conteneur de travaux a écrit ; rend le nombre de séances qui ont changé."""
    changees = 0
    with ouvrir(base) as c:
        lignes = c.execute("SELECT id, etat, progression FROM seances WHERE etat IN ('attente', 'transcription')").fetchall()
    for ligne in lignes:
        ident = ligne['id']
        dossier = dossier_audio(donnees, ident)
        if not _dossier_fiable(dossier):
            continue
        resultat = protocole.lire_json(dossier / protocole.RESULTAT, protocole.TAILLE_RESULTAT)
        segments = _segments_valides(resultat) if resultat else None
        erreur = protocole.lire_json(dossier / protocole.ERREUR)
        progres = protocole.lire_json(dossier / protocole.PROGRES)
        maintenant = _maintenant()
        with ouvrir(base) as c:
            if segments is not None:
                etiquettes = list(dict.fromkeys(s['intervenant'] for s in segments))  # dans l'ordre d'apparition
                c.execute('DELETE FROM seance_segments WHERE seance_id=?', (ident,))
                c.execute('DELETE FROM seance_intervenants WHERE seance_id=?', (ident,))
                c.executemany("INSERT INTO seance_intervenants(seance_id, etiquette, nom) VALUES (?, ?, '')",
                              [(ident, e) for e in etiquettes])
                c.executemany('INSERT INTO seance_segments(seance_id, debut, fin, etiquette, texte) VALUES (?, ?, ?, ?, ?)',
                              [(ident, float(s['debut']), float(s['fin']), s['intervenant'], s['texte']) for s in segments])
                duree = resultat.get('duree_s')
                c.execute("UPDATE seances SET etat='relecture', progression=NULL, erreur=NULL, duree_s=?, modifie=? WHERE id=?",
                          (float(duree) if isinstance(duree, (int, float)) else None, maintenant, ident))
            elif erreur and erreur.get('code') in protocole.CODES:
                c.execute("UPDATE seances SET etat='echec', erreur=?, progression=NULL, modifie=? WHERE id=?",
                          (erreur['code'], maintenant, ident))
                if erreur['code'] == 'illisible':  # rien à en tirer : l'audio est effacé, un nouvel import reste possible
                    _effacer_dossier(dossier)
            elif progres and isinstance(progres.get('etape'), str) and isinstance(progres.get('pourcentage'), (int, float)):
                progression = f"{progres['etape']} {int(progres['pourcentage'])}"
                if isinstance(progres.get('reste_s'), int) and progres['reste_s'] >= 0:  # temps restant estimé
                    progression += f" {progres['reste_s']}"
                if (ligne['etat'], ligne['progression']) == ('transcription', progression):
                    continue
                c.execute("UPDATE seances SET etat='transcription', progression=?, modifie=? WHERE id=?", (progression, maintenant, ident))
            else:
                continue
        changees += 1
    return changees


# --- Relecture, validation, suppression, texte intégral -------------------------------------------------------------

def _intervenants(c, ident: str) -> dict:
    return {ligne['etiquette']: ligne['nom'] for ligne in
            c.execute('SELECT etiquette, nom FROM seance_intervenants WHERE seance_id=?', (ident,))}


def _echec_de_redaction(seance) -> bool:
    """Échec de la rédaction seulement : la transcription validée reste entière et utilisable."""
    return seance['etat'] == 'echec' and seance['erreur'] == 'redaction'


def _a_relire(c, ident: str, proprietaire: str):
    seance = _seance(c, ident, proprietaire)
    if seance['etat'] not in ('relecture', 'validee', 'prete') and not _echec_de_redaction(seance):
        raise ValueError("cette séance n'a pas encore de transcription")
    return seance


def nommer_intervenant(base: Path, ident: str, proprietaire: str, etiquette: str, nom: str) -> None:
    nom = nom.strip() if isinstance(nom, str) else ''
    if not 1 <= len(nom) <= 80 or any(ord(car) < 32 or ord(car) == 127 for car in nom):
        raise ValueError('nom : entre 1 et 80 caractères, sans caractère de contrôle')
    with ouvrir(base) as c:
        _a_relire(c, ident, proprietaire)
        intervenants = _intervenants(c, ident)
        if etiquette not in intervenants:
            raise ValueError('intervenant inconnu')
        if any(autre != etiquette and existant.casefold() == nom.casefold() for autre, existant in intervenants.items()):
            raise ValueError('Ce nom est déjà donné à un autre intervenant : fusionnez-les.')
        c.execute('UPDATE seance_intervenants SET nom=? WHERE seance_id=? AND etiquette=?', (nom, ident, etiquette))
        c.execute('UPDATE seances SET modifie=? WHERE id=?', (_maintenant(), ident))


def fusionner_intervenants(base: Path, ident: str, proprietaire: str, source: str, cible: str) -> None:
    with ouvrir(base) as c:
        _a_relire(c, ident, proprietaire)
        intervenants = _intervenants(c, ident)
        if source == cible or source not in intervenants or cible not in intervenants:
            raise ValueError('intervenants à fusionner invalides')
        c.execute('UPDATE seance_segments SET etiquette=? WHERE seance_id=? AND etiquette=?', (cible, ident, source))
        c.execute('DELETE FROM seance_intervenants WHERE seance_id=? AND etiquette=?', (ident, source))
        c.execute('UPDATE seances SET modifie=? WHERE id=?', (_maintenant(), ident))


def modifier_segment(base: Path, ident: str, proprietaire: str, segment_id: int, texte: str | None = None,
                     etiquette: str | None = None) -> None:
    with ouvrir(base) as c:
        _a_relire(c, ident, proprietaire)
        if c.execute('SELECT 1 FROM seance_segments WHERE id=? AND seance_id=?', (segment_id, ident)).fetchone() is None:
            raise LookupError('passage inconnu')
        if texte is not None:
            texte = texte.strip() if isinstance(texte, str) else ''
            if not 1 <= len(texte) <= 5000:
                raise ValueError('texte : entre 1 et 5 000 caractères')
            c.execute('UPDATE seance_segments SET texte=?, corrige=1 WHERE id=?', (texte, segment_id))
        if etiquette is not None:
            if etiquette not in _intervenants(c, ident):
                raise ValueError('intervenant inconnu')
            c.execute('UPDATE seance_segments SET etiquette=?, corrige=1 WHERE id=?', (etiquette, segment_id))
        c.execute('UPDATE seances SET modifie=? WHERE id=?', (_maintenant(), ident))


# --- Procès-verbal : mentions et ordre du jour saisis par l'agent (lot 3) -------------------------------------------

CLES_DETAILS = ('president', 'secretaires', 'membres_en_exercice', 'presents', 'representes', 'absents', 'heure_cloture',
                'arrete_le')
CLES_POINT = ('titre', 'deliberation_numero', 'rapporteur', 'rapport', 'vote', 'scrutin_particulier', 'debut')
POINTS_MAX = 100
_NOM_MAX = 120


def _nom(valeur, cle: str) -> str:
    if (not isinstance(valeur, str) or not 1 <= len(valeur.strip()) <= _NOM_MAX
            or any(ord(car) < 32 or ord(car) == 127 for car in valeur)):
        raise ValueError(f'{cle} : un nom de 1 à {_NOM_MAX} caractères, sans caractère de contrôle')
    return valeur.strip()


def _noms(valeur, cle: str) -> list[str]:
    if not isinstance(valeur, list) or len(valeur) > 200:
        raise ValueError(f'{cle} : une liste de noms')
    return [_nom(nom, cle) for nom in valeur]


def _entier(valeur, cle: str, minimum: int = 0) -> int:
    if isinstance(valeur, bool) or not isinstance(valeur, int) or not minimum <= valeur <= 100000:
        raise ValueError(f'{cle} : un nombre entier')
    return valeur


def _modifiable(c, ident: str, proprietaire: str):
    """Séance de conseil à la transcription validée, hors rédaction en cours."""
    seance = _seance(c, ident, proprietaire)
    if seance['type'] != 'conseil':
        raise ValueError('Le procès-verbal ne concerne que les séances de conseil.')
    if not (seance['etat'] in ('validee', 'prete') or _echec_de_redaction(seance)):
        raise ValueError("Validez d'abord la transcription ; pendant une rédaction, attendez qu'elle se termine.")
    return seance


def _details_valides(details) -> dict:
    if not isinstance(details, dict) or set(details) - set(CLES_DETAILS):
        raise ValueError('mentions : ' + ', '.join(CLES_DETAILS))
    propres = {}
    for cle, valeur in details.items():
        if cle == 'president':
            propres[cle] = _nom(valeur, cle)
        elif cle in ('secretaires', 'presents', 'absents'):
            propres[cle] = _noms(valeur, cle)
        elif cle == 'membres_en_exercice':
            propres[cle] = _entier(valeur, cle, 1)
        elif cle == 'representes':
            if not isinstance(valeur, list) or len(valeur) > 200:
                raise ValueError('representes : une liste de pouvoirs')
            if any(not isinstance(pouvoir, dict) or set(pouvoir) != {'mandant', 'mandataire'} for pouvoir in valeur):
                raise ValueError('representes : mandant et mandataire')
            propres[cle] = [{'mandant': _nom(pouvoir['mandant'], 'mandant'),
                             'mandataire': _nom(pouvoir['mandataire'], 'mandataire')} for pouvoir in valeur]
        elif cle == 'heure_cloture':
            if not isinstance(valeur, str) or not _HEURE.fullmatch(valeur):
                raise ValueError('heure_cloture : HH:MM')
            propres[cle] = valeur
        else:
            try:
                if not isinstance(valeur, str) or not _DATE.fullmatch(valeur):
                    raise ValueError
                datetime.strptime(valeur, '%Y-%m-%d')
            except ValueError:
                raise ValueError('arrete_le : AAAA-MM-JJ') from None
            propres[cle] = valeur
    return propres


def enregistrer_details(base: Path, ident: str, proprietaire: str, details: dict) -> dict:
    """Mentions obligatoires du procès-verbal (L2121-15) saisies par l'agent ; remplacent la saisie précédente."""
    propres = _details_valides(details)
    with ouvrir(base) as c:
        _modifiable(c, ident, proprietaire)
        for ligne in c.execute('SELECT vote FROM seance_points WHERE seance_id=?', (ident,)).fetchall():
            _vote_valide(_json(ligne['vote'], {}) or None, propres.get('membres_en_exercice'))  # votes déjà saisis
        c.execute('UPDATE seances SET details=?, modifie=? WHERE id=?', (json.dumps(propres, ensure_ascii=False), _maintenant(), ident))
    return propres


def _vote_valide(vote, membres: int | None) -> dict | None:
    if vote is None:
        return None
    if not isinstance(vote, dict):
        raise ValueError('vote : unanimité ou décompte')
    if vote.get('mode') == 'unanimite' and set(vote) == {'mode'}:
        return {'mode': 'unanimite'}
    decompte = {'mode', 'pour', 'contre', 'abstentions'}
    if vote.get('mode') != 'decompte' or not decompte <= set(vote) <= decompte | {'public'}:
        raise ValueError('vote : unanimité, ou décompte des voix pour, contre et des abstentions')
    propre = {'mode': 'decompte', **{cle: _entier(vote[cle], cle) for cle in ('pour', 'contre', 'abstentions')}}
    total = propre['pour'] + propre['contre'] + propre['abstentions']
    if total < 1:
        raise ValueError('vote : au moins une voix')
    if membres and total > membres:
        raise ValueError('Plus de voix que de membres en exercice.')
    if 'public' in vote:
        public = vote['public']
        if not isinstance(public, dict) or set(public) - {'pour', 'contre', 'abstention'}:
            raise ValueError('scrutin public : noms des votants pour, contre et des abstentions')
        propre['public'] = {cle: _noms(noms, cle) for cle, noms in public.items()}
    return propre


def _point_valide(point, membres: int | None) -> dict:
    if not isinstance(point, dict) or set(point) - set(CLES_POINT):
        raise ValueError('point : ' + ', '.join(CLES_POINT))
    numero = point.get('deliberation_numero') or None
    if numero is not None and (not isinstance(numero, str) or not 1 <= len(numero.strip()) <= 40):
        raise ValueError('deliberation_numero : 40 caractères au plus')
    rapporteur = point.get('rapporteur') or None
    rapport = point.get('rapport') or None
    if rapport is not None and (not isinstance(rapport, str) or len(rapport.strip()) > 300
                                or any(ord(car) < 32 or ord(car) == 127 for car in rapport)):
        raise ValueError('rapport : 300 caractères au plus, sans caractère de contrôle')
    scrutin = point.get('scrutin_particulier', False)
    if not isinstance(scrutin, bool):
        raise ValueError('scrutin_particulier : oui ou non')
    debut = point.get('debut')
    if debut is not None and (isinstance(debut, bool) or not isinstance(debut, (int, float)) or debut < 0):
        raise ValueError('debut : un nombre de secondes')
    return {'titre': _texte(point, 'titre', 1, 300), 'deliberation_numero': numero.strip() if numero else None,
            'rapporteur': _nom(rapporteur, 'rapporteur') if rapporteur else None, 'rapport': rapport.strip() if rapport else None,
            'vote': _vote_valide(point.get('vote'), membres),
            'scrutin_particulier': scrutin, 'debut': float(debut) if debut is not None else None}


def enregistrer_points(base: Path, ident: str, proprietaire: str, points: list[dict]) -> list[dict]:
    """Ordre du jour : remplace les points ; chaque point garde la teneur déjà rédigée à son rang, qui reste valable tant que
    ses passages n'ont pas changé (empreinte : voir `_points`)."""
    if not isinstance(points, list) or len(points) > POINTS_MAX:
        raise ValueError(f'ordre du jour : {POINTS_MAX} points au plus')
    with ouvrir(base) as c:
        seance = _modifiable(c, ident, proprietaire)
        membres = _json(seance['details'], {}).get('membres_en_exercice')
        propres = [_point_valide(point, membres) for point in points]
        anciens = {l['rang']: (l['teneur'], l['erreur'], l['empreinte']) for l in
                   c.execute('SELECT rang, teneur, erreur, empreinte FROM seance_points WHERE seance_id=?', (ident,))}
        c.execute('DELETE FROM seance_points WHERE seance_id=?', (ident,))
        for rang, point in enumerate(propres, 1):
            teneur, erreur, empreinte = anciens.get(rang, (None, None, None))
            c.execute('INSERT INTO seance_points(seance_id, rang, titre, deliberation_numero, rapporteur, rapport, vote, '
                      'scrutin_particulier, debut, teneur, erreur, empreinte) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      (ident, rang, point['titre'], point['deliberation_numero'], point['rapporteur'], point['rapport'],
                       json.dumps(point['vote'], ensure_ascii=False) if point['vote'] else None, int(point['scrutin_particulier']),
                       point['debut'], teneur, erreur, empreinte))
        c.execute('UPDATE seances SET modifie=? WHERE id=?', (_maintenant(), ident))
        return _points(c, ident)


def proposer_ordre_du_jour(base: Path, donnees: Path, ident: str, proprietaire: str) -> list[dict]:
    """Points proposés : délibérations du registre à la date de la séance, avec leur début repéré dans la transcription."""
    with ouvrir(base) as c:
        seance = _modifiable(c, ident, proprietaire)
        segments = [dict(s) for s in c.execute('SELECT id, debut, fin, etiquette, texte FROM seance_segments WHERE seance_id=? '
                                               'ORDER BY debut, id', (ident,))]
    points = [{'rang': rang, 'titre': d['objet'], 'deliberation_numero': d['numero']}
              for rang, d in enumerate(registre.deliberations_du(base, donnees, seance['date']), 1)]
    return [dict(point, debut=debut) for point, debut in zip(points, seances_pv.proposer_debuts(segments, points))]


# --- Rédaction : compte rendu et procès-verbal (lot 3) ----------------------------------------------------------------

DOCUMENTS = ('compte_rendu', 'proces_verbal')
_redaction = threading.Lock()  # une séance rédigée à la fois, par le fil comme par un appel direct


def demander_redaction(base: Path, ident: str, proprietaire: str, document: str) -> None:
    """Met la séance en file de rédaction ; le fil d'arrière-plan s'en charge, aucune requête n'attend le modèle. Pour un
    procès-verbal, seuls les points sans teneur à jour sont rédigés (noms, passages ou débuts changés depuis)."""
    if document not in DOCUMENTS:
        raise ValueError('document : compte_rendu ou proces_verbal')
    with ouvrir(base) as c:
        seance = _seance(c, ident, proprietaire)
        if seance['etat'] not in ('validee', 'prete') and not _echec_de_redaction(seance):
            raise ValueError("Validez d'abord la transcription ; une rédaction est peut-être déjà en cours.")
        total = 1
        if document == 'proces_verbal':
            if seance['type'] != 'conseil':
                raise ValueError('Le procès-verbal ne concerne que les séances de conseil.')
            total = c.execute('SELECT COUNT(*) FROM seance_points WHERE seance_id=?', (ident,)).fetchone()[0]
            if not total:
                raise ValueError("Ajoutez au moins un point à l'ordre du jour.")
            c.execute('UPDATE seance_points SET erreur=NULL WHERE seance_id=?', (ident,))
        redaction = _json(seance['redaction'], {})
        vigilances = redaction.get('vigilances') if isinstance(redaction.get('vigilances'), dict) else {}
        redaction = {'document': document, 'compte_rendu': redaction.get('compte_rendu') if document == 'proces_verbal' else None,
                     'proces_verbal': bool(redaction.get('proces_verbal')) and document == 'compte_rendu',
                     'vigilances': dict(vigilances, **{document: []}), 'duree_s': None}
        c.execute("UPDATE seances SET etat='redaction', erreur=NULL, progression=?, redaction=?, modifie=? WHERE id=?",
                  (f'redaction 0 {total}', json.dumps(redaction, ensure_ascii=False), _maintenant(), ident))


def rediger_en_attente(base: Path, donnees: Path) -> int:
    """Rédige la plus ancienne séance en attente de rédaction ; rend 1 si une séance a été traitée, sinon 0."""
    if not _redaction.acquire(blocking=False):
        return 0
    try:
        with ouvrir(base) as c:
            ligne = c.execute("SELECT * FROM seances WHERE etat='redaction' ORDER BY modifie, rowid LIMIT 1").fetchone()
        if ligne is None:
            return 0
        try:
            _rediger(base, donnees, dict(ligne))
        except Exception as erreur:  # imprévu : la séance passe en échec de rédaction au lieu de bloquer la file
            import journal
            journal.evenement('seances:redaction', 'echec', code=type(erreur).__name__.lower())
            with ouvrir(base) as c:
                c.execute("UPDATE seances SET etat='echec', erreur='redaction', progression=NULL WHERE id=? AND etat='redaction'",
                          (ligne['id'],))
        return 1
    finally:
        _redaction.release()


def _existe(c, ident: str) -> bool:
    return c.execute("SELECT 1 FROM seances WHERE id=? AND etat='redaction'", (ident,)).fetchone() is not None


def _rediger(base: Path, donnees: Path, seance: dict) -> None:
    from . import administration, seances_redaction
    ident, debut = seance['id'], time.monotonic()
    redaction = _json(seance['redaction'], {})
    document = redaction.get('document') if redaction.get('document') in DOCUMENTS else 'compte_rendu'
    with ouvrir(base) as c:
        noms = _intervenants(c, ident)
        segments = [dict(s) for s in c.execute('SELECT id, debut, fin, etiquette, texte FROM seance_segments WHERE seance_id=? '
                                               'ORDER BY debut, id', (ident,))]
        points = _points(c, ident)
    vigilances = list((redaction.get('vigilances') or {}).get(document) or [])
    reussi = True
    if document == 'compte_rendu':
        passages = seances_pv.passages_par_point(segments, [{'debut': float('-inf')}], noms)[0]
        try:
            resultat = seances_redaction.rediger_compte_rendu(donnees, passages)
            vigilances += [v for v in resultat.pop('vigilances') if v not in vigilances]
            redaction['compte_rendu'] = resultat
        except seances_redaction.EchecRedaction:
            reussi = False
    else:
        passages = seances_pv.passages_par_point(segments, points, noms)
        total, faits = len(points), sum(1 for point in points if point['a_jour'])
        with ouvrir(base) as c:  # reprise : la progression repart des points réellement rédigés
            c.execute("UPDATE seances SET progression=? WHERE id=? AND etat='redaction'", (f'redaction {faits} {total}', ident))
        for point, extraits in zip(points, passages):
            if point['a_jour']:
                continue  # déjà rédigé d'après ces passages (reprise, relance des points en échec, nouvelle demande)
            teneur, erreur = [deliberations.A_COMPLETER], None
            if extraits:
                try:
                    resultat = seances_redaction.rediger_teneur(donnees, extraits)
                    teneur = resultat['paragraphes']
                    vigilances += [v for v in resultat['vigilances'] if v not in vigilances]
                except seances_redaction.EchecRedaction as echec:
                    teneur, erreur, reussi = None, echec.code, False
            faits += 1
            with ouvrir(base) as c:
                if not _existe(c, ident):
                    return  # séance supprimée pendant la rédaction : rien n'est réécrit
                c.execute('UPDATE seance_points SET teneur=?, erreur=?, empreinte=? WHERE id=? AND seance_id=?',
                          (json.dumps(teneur, ensure_ascii=False) if teneur else None, erreur,
                           seances_pv.empreinte(extraits) if teneur else None, point['id'], ident))
                c.execute('UPDATE seances SET progression=? WHERE id=?', (f'redaction {faits} {total}', ident))
        redaction['proces_verbal'] = reussi
    redaction['vigilances'] = dict(redaction.get('vigilances') or {}, **{document: vigilances})
    redaction['duree_s'] = round(time.monotonic() - debut, 1)
    with ouvrir(base) as c:
        if not _existe(c, ident):
            return
        c.execute("UPDATE seances SET etat=?, erreur=?, progression=NULL, redaction=? WHERE id=?",
                  ('prete' if reussi else 'echec', None if reussi else 'redaction', json.dumps(redaction, ensure_ascii=False), ident))
    if reussi:
        administration.noter(base, 'seance_redigee', seance['type'], document, duree_ms=int(redaction['duree_s'] * 1000))


def valider(base: Path, donnees: Path, ident: str, proprietaire: str) -> None:
    """Transcription validée : le texte reste, l'audio est effacé (il ne sert plus qu'à la relecture)."""
    with ouvrir(base) as c:
        if _seance(c, ident, proprietaire)['etat'] != 'relecture':
            raise ValueError("seule une transcription à relire peut être validée")
        c.execute("UPDATE seances SET etat='validee', modifie=? WHERE id=?", (_maintenant(), ident))
    _effacer_dossier(dossier_audio(donnees, ident))


def reessayer(base: Path, donnees: Path, ident: str, proprietaire: str) -> None:
    with _envois, ouvrir(base) as c:
        seance = _seance(c, ident, proprietaire)
        if seance['etat'] != 'echec':
            raise ValueError("seule une séance en échec peut être relancée")
        if seance['erreur'] == 'redaction':  # la transcription reste : seuls les points sans teneur sont rédigés de nouveau
            total, faits = c.execute('SELECT COUNT(*), COUNT(teneur) FROM seance_points WHERE seance_id=?', (ident,)).fetchone()
            redaction = _json(seance['redaction'], {})
            progression = f"redaction {faits} {total}" if redaction.get('document') == 'proces_verbal' else 'redaction 0 1'
            c.execute("UPDATE seances SET etat='redaction', erreur=NULL, progression=?, modifie=? WHERE id=?",
                      (progression, _maintenant(), ident))
            return
        dossier = dossier_audio(donnees, ident)
        envoi = protocole.lire_json(dossier / ENVOI) or {}
        source = dossier / f"source.{envoi.get('format')}"
        if seance['erreur'] != 'illisible' and envoi.get('format') and _dossier_fiable(dossier) and _ordinaire(source):
            for nom in (protocole.ERREUR, protocole.PROGRES, protocole.RESULTAT):
                (dossier / nom).unlink(missing_ok=True)
            ancienne = protocole.lire_json(dossier / protocole.DEMANDE) or {}
            protocole.ecrire_json(dossier / protocole.DEMANDE, {'version': protocole.VERSION, 'langue': 'fr',
                                                                'modele': ancienne.get('modele') or 'large-v3-turbo',
                                                                'source': source.name})
            c.execute("UPDATE seances SET etat='attente', erreur=NULL, progression=NULL, modifie=? WHERE id=?", (_maintenant(), ident))
        else:  # rien de réutilisable : on repart d'un nouvel envoi
            _effacer_dossier(dossier)
            c.execute("UPDATE seances SET etat='creee', erreur=NULL, progression=NULL, modifie=? WHERE id=?", (_maintenant(), ident))


def supprimer(base: Path, donnees: Path, ident: str, proprietaire: str) -> None:
    """Efface la séance (lignes réécrites à zéro, `secure_delete`), son audio et ses documents. L'appelant purge ensuite
    le journal WAL, comme pour les conversations."""
    c = ouvrir(base)
    try:
        c.execute('PRAGMA secure_delete=ON')
        with c:
            _seance(c, ident, proprietaire)
            for table in ('seance_segments', 'seance_intervenants', 'seance_points'):
                c.execute(f'DELETE FROM {table} WHERE seance_id=?', (ident,))
            c.execute('DELETE FROM seances WHERE id=?', (ident,))
    finally:
        c.close()
    _effacer_dossier(dossier_audio(donnees, ident))
    shutil.rmtree(Path(donnees) / 'seances' / ident, ignore_errors=True)


def purger_abandons(base: Path, donnees: Path, maintenant: datetime | None = None) -> int:
    """Envois interrompus depuis plus de 7 jours : audio reçu effacé, la séance revient à « créée »."""
    maintenant = maintenant or datetime.now(timezone.utc)
    purgees = 0
    with ouvrir(base) as c:
        lignes = c.execute("SELECT id, modifie FROM seances WHERE etat='reception'").fetchall()
    for ligne in lignes:
        dossier = dossier_audio(donnees, ligne['id'])
        envoi = protocole.lire_json(dossier / ENVOI) or {}
        try:
            dernier = datetime.fromisoformat(str(envoi.get('maj') or ligne['modifie']))
        except ValueError:
            dernier = datetime.fromisoformat(ligne['modifie'])
        if dernier.tzinfo is None:
            dernier = dernier.replace(tzinfo=timezone.utc)
        if (maintenant - dernier).total_seconds() <= ABANDON_JOURS * 86400:
            continue
        _effacer_dossier(dossier)
        with ouvrir(base) as c:
            c.execute("UPDATE seances SET etat='creee', modifie=? WHERE id=? AND etat='reception'", (_maintenant(), ligne['id']))
        purgees += 1
    return purgees


def _heure(secondes: float) -> str:
    secondes = int(secondes)
    return f'{secondes // 3600:02d}:{secondes % 3600 // 60:02d}:{secondes % 60:02d}'


def _paragraphes(seance: dict, heures: bool) -> list[str]:
    noms = {i['etiquette']: i['nom'] or i['etiquette'] for i in seance['intervenants']}
    paragraphes, groupe = [], None
    for segment in seance['segments']:
        if groupe and groupe['etiquette'] == segment['etiquette']:
            groupe['textes'].append(segment['texte'])
            continue
        groupe = {'etiquette': segment['etiquette'], 'debut': segment['debut'], 'textes': [segment['texte']]}
        paragraphes.append(groupe)
    return [(f"[{_heure(g['debut'])}] " if heures else '') + f"{noms.get(g['etiquette'], g['etiquette'])} : " + ' '.join(g['textes'])
            for g in paragraphes]


def texte_integral(base: Path, ident: str, proprietaire: str, heures: bool) -> str:
    paragraphes = _paragraphes(lire(base, ident, proprietaire), heures)
    return '\n\n'.join(paragraphes) if paragraphes else 'Aucune parole détectée.'


def word_integral(base: Path, donnees: Path, ident: str, proprietaire: str, heures: bool) -> Path:
    """Texte intégral en Word, sur le papier à en-tête de la collectivité s'il est déposé."""
    import arrete_document
    import branding
    from docx.shared import Pt

    seance = lire(base, ident, proprietaire, donnees)
    doc = arrete_document.document_vierge(donnees, branding.profile(donnees).get('collectivite') or '', 'TRANSCRIPTION')
    try:
        doc.add_paragraph(seance['titre'], 'Title')
    except KeyError:  # papier à en-tête sans style « Titre »
        titre = doc.add_paragraph().add_run(seance['titre'])
        titre.bold, titre.font.size = True, Pt(16)
    date = datetime.strptime(seance['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
    doc.add_paragraph(' — '.join(x for x in (f"Séance du {date}" + (f" à {seance['heure']}" if seance['heure'] else ''), seance['lieu']) if x))
    noms = [i['nom'] or i['etiquette'] for i in seance['intervenants']]
    if noms:
        doc.add_paragraph('Intervenants : ' + ', '.join(noms))
    paragraphes = _paragraphes(seance, heures) or ['Aucune parole détectée.']
    for paragraphe in paragraphes:
        doc.add_paragraph(paragraphe)
    doc.core_properties.title, doc.core_properties.author = seance['titre'], ''
    dossier = Path(donnees) / 'seances' / ident
    dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
    chemin = dossier / 'transcription.docx'
    doc.save(str(chemin))
    os.chmod(chemin, 0o600)
    return chemin


DOCUMENTS_WORD = {'compte_rendu': ('compte-rendu.docx', 'COMPTE RENDU', seances_pv.contenu_compte_rendu),
                  'proces_verbal': ('proces-verbal.docx', 'PROCÈS-VERBAL', seances_pv.contenu_proces_verbal),
                  'liste_deliberations': ('liste-deliberations.docx', 'LISTE DES DÉLIBÉRATIONS', seances_pv.contenu_liste_deliberations)}


def word_document(base: Path, donnees: Path, ident: str, proprietaire: str, document: str) -> Path:
    """Compte rendu, procès-verbal ou liste des délibérations en Word, sur le papier à en-tête s'il est déposé ; seulement
    une fois le document rédigé (compte rendu après un compte rendu, procès-verbal et liste après un procès-verbal)."""
    import arrete_document
    import branding
    from docx.shared import Pt

    if document not in DOCUMENTS_WORD:
        raise ValueError('document : compte_rendu, proces_verbal ou liste_deliberations')
    seance = lire(base, ident, proprietaire, donnees)
    if document == 'compte_rendu':
        if not seance['redaction'].get('compte_rendu') or seance['etat'] == 'redaction':
            raise ValueError("Ce document n'est pas encore rédigé.")
    elif not seance['pv_a_jour'] or seance['etat'] == 'redaction':
        raise ValueError("Le procès-verbal n'est plus à jour ou pas encore rédigé : relancez sa rédaction ; seuls les points "
                         "modifiés seront refaits.")
    nom, mention, contenu = DOCUMENTS_WORD[document]
    doc = arrete_document.document_vierge(donnees, branding.profile(donnees).get('collectivite') or '', mention)
    for genre, texte in contenu(seance):
        style = {'titre': 'Title', 'section': 'Heading 2', 'liste': 'List Bullet'}.get(genre)
        try:
            doc.add_paragraph(texte, style)
        except KeyError:  # papier à en-tête sans ces styles : mise en forme directe
            paragraphe = doc.add_paragraph()
            run = paragraphe.add_run(('• ' if genre == 'liste' else '') + texte)
            if genre in ('titre', 'section'):
                run.bold, run.font.size = True, Pt(16 if genre == 'titre' else 12)
    doc.core_properties.title, doc.core_properties.author = seance['titre'], ''
    dossier = Path(donnees) / 'seances' / ident
    dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
    chemin = dossier / nom
    doc.save(str(chemin))
    os.chmod(chemin, 0o600)
    return chemin


def source_audio(base: Path, donnees: Path, ident: str, proprietaire: str) -> Path:
    """Fichier audio reçu d'une séance de ce compte ; `LookupError` pour une séance inconnue ou sans audio."""
    with ouvrir(base) as c:
        _seance(c, ident, proprietaire)
    dossier = dossier_audio(donnees, ident)
    envoi = (protocole.lire_json(dossier / ENVOI) if _dossier_fiable(dossier) else None) or {}
    source = dossier / f"source.{envoi.get('format')}"
    if envoi.get('format') not in FORMATS or not _ordinaire(source):
        raise LookupError('aucun audio')
    return source


# --- Relevé en arrière-plan -------------------------------------------------------------------------------------------

RELEVE_S = 10
PURGE_S = 3600
REDACTION_S = 2
_fond = {'actif': False, 'fil': None, 'redaction': None}
_verrou_fond = threading.Lock()


def activer() -> None:
    """Autorise le relevé en arrière-plan (appelé par le serveur seulement : les tests relèvent eux-mêmes)."""
    _fond['actif'] = True


def _boucle(base: Path, donnees: Path) -> None:
    import journal
    derniere_purge = 0.0
    while True:
        try:
            relever(base, donnees)
            if time.monotonic() - derniere_purge >= PURGE_S:
                purger_abandons(base, donnees)
                derniere_purge = time.monotonic()
        except Exception as erreur:  # le fil ne s'arrête jamais : on le dit au journal, sans contenu, et on recommence
            journal.evenement('seances:releve', 'echec', code=type(erreur).__name__.lower())
        time.sleep(RELEVE_S)


def _boucle_redaction(base: Path, donnees: Path) -> None:
    import journal
    while True:
        try:
            rediger_en_attente(base, donnees)
        except Exception as erreur:  # même règle que le relevé : le fil continue, le journal le dit sans contenu
            journal.evenement('seances:redaction', 'echec', code=type(erreur).__name__.lower())
        time.sleep(REDACTION_S)


def lancer(base: Path, donnees: Path) -> None:
    if not _fond['actif']:
        return
    with _verrou_fond:
        if not (_fond['fil'] and _fond['fil'].is_alive()):
            _fond['fil'] = threading.Thread(target=_boucle, args=(base, donnees), daemon=True, name='releve-seances')
            _fond['fil'].start()
        if not (_fond['redaction'] and _fond['redaction'].is_alive()):
            _fond['redaction'] = threading.Thread(target=_boucle_redaction, args=(base, donnees), daemon=True,
                                                  name='redaction-seances')
            _fond['redaction'].start()
