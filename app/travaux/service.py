# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Service du conteneur de travaux : traite les séances déposées dans `/audio`, une à la fois, dans l'ordre d'arrivée.

Pour chaque séance : conversion, transcription, séparation des voix, attribution des intervenants, puis `resultat.json`
(ou `erreur.json` avec un code du protocole). Une séance interrompue par un redémarrage est reprise depuis le début,
puisqu'elle n'a toujours ni résultat ni erreur. Un battement toutes les 30 s, même pendant un long traitement, dit à
Territoria que le service tourne et si ses modèles sont présents. Les journaux ne disent que l'identifiant de la séance,
la durée et le code d'erreur : jamais le texte transcrit ni les intervenants.

    python -m travaux.service   (modèle : TERRITORIA_TRANSCRIPTION_MODELE, défaut large-v3-turbo)
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import moteur as moteur_
from .attribution import attribuer
from .protocole import BATTEMENT, CODES, DEMANDE, ERREUR, PROGRES, RESULTAT, VERSION, ecrire_json, en_attente, lire_json

journal = logging.getLogger('territoria.travaux')
BATTEMENT_S = 30


class _Disparu(Exception):
    """Le dossier de la séance a été supprimé pendant le traitement (séance supprimée dans Territoria)."""


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _ecrire(dossier: Path, nom: str, donnees: dict) -> None:
    if not dossier.is_dir():
        raise _Disparu
    try:
        ecrire_json(dossier / nom, donnees)
    except FileNotFoundError as exc:  # dossier supprimé entre la vérification et l'écriture : jamais recréé
        raise _Disparu from exc


# Part de chaque étape dans la durée totale, mesurée au banc (transcription 0,46 et séparation des voix 0,63 fois la durée
# de l'audio) : sert à estimer le temps restant affiché dans Territoria.
ETAPES = {'conversion': (0.0, 0.02), 'transcription': (0.02, 0.42), 'intervenants': (0.44, 0.56)}
# Marque posée pendant un traitement : plafond de mémoire atteint, le noyau tue le processus sans qu'aucune exception ne
# soit levée. Une séance trouvée avec cette marque a été interrompue ; à la deuxième interruption, elle passe en échec
# « memoire » au lieu de bloquer indéfiniment la file.
TENTATIVE = '.tentative'
TENTATIVES_MAX = 2


def _progres(dossier: Path, etape: str, pourcentage: float, debut: float | None = None) -> None:
    donnees = {'etape': etape, 'pourcentage': int(pourcentage), 'maj': _maintenant()}
    depart, part = ETAPES[etape]
    fait = depart + part * min(100.0, max(0.0, pourcentage)) / 100
    if debut is not None and fait >= 0.05:
        donnees['reste_s'] = int((time.monotonic() - debut) * (1 - fait) / fait)
    _ecrire(dossier, PROGRES, donnees)


def _nettoyer(dossier: Path) -> None:
    """Pistes laissées par un traitement interrompu (le nettoyage d'un dossier temporaire n'a pas lieu après un arrêt brutal)."""
    for reste in dossier.glob('.piste-*'):
        shutil.rmtree(reste, ignore_errors=True)


def traiter(dossier: Path, moteur, convertir: Callable = moteur_.convertir) -> None:
    dossier = Path(dossier)
    debut = time.monotonic()
    battu = [debut]

    def vivant() -> None:  # le calcul peut garder la main longtemps : le battement part aussi d'ici
        if time.monotonic() - battu[0] >= BATTEMENT_S:
            battu[0] = time.monotonic()
            try:
                battre(dossier.parent, moteur)
            except OSError:
                pass

    try:
        _nettoyer(dossier)
        tentatives = (lire_json(dossier / TENTATIVE) or {}).get('nombre', 0)
        if isinstance(tentatives, int) and tentatives >= TENTATIVES_MAX:
            raise MemoryError('traitement interrompu deux fois')
        _ecrire(dossier, TENTATIVE, {'nombre': (tentatives if isinstance(tentatives, int) else 0) + 1})
        demande = lire_json(dossier / DEMANDE) or {}
        nom = str(demande.get('source') or '')
        source = dossier / nom
        if not nom or Path(nom).name != nom or not source.is_file():  # jamais de fichier hors du dossier de la séance
            raise moteur_.ErreurTraitement('illisible')

        def suivi(etape: str):
            dernier = [-1]

            def suivre(pourcentage: float) -> None:
                vivant()
                if int(pourcentage) != dernier[0]:  # une écriture par point de pourcentage, pas par segment
                    dernier[0] = int(pourcentage)
                    _progres(dossier, etape, pourcentage, debut)
            return suivre

        # Piste intermédiaire sur le disque, dans le dossier de la séance : /tmp est en mémoire et la compterait (jusqu'à
        # 230 Mo pour 2 h) dans le plafond de 3 Go du conteneur. Effacée à la fin, même si la séance est supprimée entre-temps.
        with tempfile.TemporaryDirectory(dir=dossier, prefix='.piste-', ignore_cleanup_errors=True) as tmp:
            wav = Path(tmp) / 'piste.wav'
            _progres(dossier, 'conversion', 0)
            duree = convertir(source, wav)
            transcription = suivi('transcription')
            transcription(0)
            transcrits = moteur.transcrire(wav, transcription)
            intervenants = suivi('intervenants')
            intervenants(0)
            tours = moteur.separer(wav, intervenants)
        segments = [{'debut': s['debut'], 'fin': s['fin'], 'intervenant': s['intervenant'], 'texte': s['texte']}
                    for s in attribuer(transcrits, tours)]
        _progres(dossier, 'intervenants', 100)
        traitement = round(time.monotonic() - debut, 1)
        _ecrire(dossier, RESULTAT, {'version': VERSION, 'duree_s': round(float(duree), 1), 'modele': moteur.modele,
                                    'duree_traitement_s': float(traitement), 'segments': segments})
        journal.info('séance %s transcrite en %.0f s', dossier.name, traitement)
    except _Disparu:
        journal.info('séance %s supprimée pendant le traitement', dossier.name)
    except Exception as exc:
        if isinstance(exc, MemoryError):
            code = 'memoire'
        elif isinstance(exc, moteur_.ErreurTraitement) and exc.code in CODES:
            code = exc.code
        else:
            code = 'interne'
        journal.warning('séance %s en échec : %s (%s)', dossier.name, code, type(exc).__name__)
        try:
            _ecrire(dossier, ERREUR, {'code': code})
        except (_Disparu, OSError):  # disque plein : le service continue, la séance sera reprise
            journal.warning('séance %s : erreur impossible à écrire', dossier.name)
    finally:
        if (dossier / RESULTAT).exists() or (dossier / ERREUR).exists():  # traitement mené à son terme, d'une façon ou d'une autre
            (dossier / TENTATIVE).unlink(missing_ok=True)


def battre(racine: Path, moteur) -> None:
    ecrire_json(Path(racine) / BATTEMENT, {'maj': _maintenant(), 'modele': moteur.modele, 'modeles_ok': bool(moteur.modeles_ok())})


def _battre_sans_cesse(racine: Path, moteur, arret: threading.Event) -> None:
    while True:
        try:
            battre(racine, moteur)
        except OSError:
            journal.warning('battement impossible à écrire')
        if arret.wait(BATTEMENT_S):
            return


def boucle(racine: Path, fabrique_moteur: Callable[[], object], attente_s: float = 5, tours: int | None = None,
           convertir: Callable = moteur_.convertir) -> None:
    racine = Path(racine)
    moteur = fabrique_moteur()
    arret = threading.Event()
    battement = threading.Thread(target=_battre_sans_cesse, args=(racine, moteur, arret), daemon=True)
    battement.start()
    try:
        fait = 0
        while tours is None or fait < tours:
            fait += 1
            attente = en_attente(racine) if moteur.modeles_ok() else []  # sans modèles, les séances attendent
            if attente:
                traiter(attente[0], moteur, convertir)
            else:
                time.sleep(attente_s)
    finally:
        arret.set()
        battement.join(timeout=5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', stream=sys.stderr)
    modele = os.environ.get('TERRITORIA_TRANSCRIPTION_MODELE', 'large-v3-turbo')
    fils = max(1, (os.cpu_count() or 2) - 2)
    journal.info('service de transcription démarré (modèle %s, %d fils)', modele, fils)
    boucle(Path('/audio'), lambda: moteur_.Moteur(Path('/modeles'), modele, fils))
    return 0


if __name__ == '__main__':
    sys.exit(main())
