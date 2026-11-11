# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Protocole d'échange par fichiers entre Territoria et le conteneur de travaux (version 1).

Pour chaque séance, un dossier `/audio/<id>/` : Territoria y dépose `source.<ext>` puis, en dernier, `demande.json` ; le
conteneur de travaux y écrit `progres.json`, puis `resultat.json` ou `erreur.json`. Le battement `/audio/.travaux-vivant`
dit que le conteneur tourne. Toute écriture est atomique (fichier temporaire puis renommage) et en droits 0600 : un
lecteur ne voit jamais de fichier à moitié écrit.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

VERSION = 1
DEMANDE = 'demande.json'
PROGRES = 'progres.json'
RESULTAT = 'resultat.json'
ERREUR = 'erreur.json'
BATTEMENT = '.travaux-vivant'
CODES = ('illisible', 'trop_long', 'memoire', 'interne')
TAILLE_JSON = 64 * 1024
TAILLE_RESULTAT = 50 * 1024 * 1024  # transcription d'une séance de 2 heures, très largement


def ecrire_json(chemin: Path, donnees: dict) -> None:
    chemin = Path(chemin)
    descripteur, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=f'.{chemin.name}.')
    try:
        os.fchmod(descripteur, 0o600)
        with os.fdopen(descripteur, 'w', encoding='utf-8') as flux:
            json.dump(donnees, flux, ensure_ascii=False)
        os.replace(temporaire, chemin)
    except BaseException:
        Path(temporaire).unlink(missing_ok=True)
        raise


def lire_json(chemin: Path, taille_max: int = TAILLE_JSON) -> dict | None:
    """Contenu d'un fichier JSON ordinaire d'au plus `taille_max` octets, sinon `None` : le dossier est partagé avec le
    conteneur de travaux, qui décode des fichiers non fiables ; un lien, un tube ou un fichier démesuré n'est jamais lu."""
    try:
        descripteur = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(descripteur, 'rb') as flux:
        etat = os.fstat(flux.fileno())
        if not stat.S_ISREG(etat.st_mode) or etat.st_size > taille_max:
            return None
        brut = flux.read(taille_max + 1)
    try:
        donnees = json.loads(brut.decode('utf-8')) if len(brut) <= taille_max else None
    except ValueError:
        return None
    return donnees if isinstance(donnees, dict) else None


def en_attente(racine: Path) -> list[Path]:
    """Séances déposées et pas encore traitées, dans l'ordre d'arrivée (date de `demande.json`)."""
    trouvees = []
    try:
        dossiers = [d for d in Path(racine).iterdir() if d.is_dir()]
    except OSError:
        return []
    for dossier in dossiers:
        try:
            if (dossier / RESULTAT).exists() or (dossier / ERREUR).exists():
                continue
            trouvees.append(((dossier / DEMANDE).stat().st_mtime, dossier.name, dossier))
        except OSError:  # pas encore de demande, ou dossier supprimé entre-temps
            continue
    return [dossier for _, _, dossier in sorted(trouvees)]
