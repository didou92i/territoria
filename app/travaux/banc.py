# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Banc d'essai de la transcription : vitesse et justesse sur un enregistrement dont on connaît le texte et les tours.

Enchaîne exactement le traitement du service (conversion, transcription, séparation des voix, attribution) et le mesure.
Le WAV intermédiaire va, comme pour le service, sur le disque (par défaut à côté de l'audio mesuré) et disparaît
ensuite ; rien d'autre n'est écrit.

    python -m travaux.banc --audio seance.wav --reference seance.json --modele large-v3-turbo
    → {"duree_audio_s", "duree_traitement_s", "facteur", "taux_erreur", "justesse_intervenants", "intervenants"}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from . import moteur as moteur_
from .attribution import attribuer
from .mesures import justesse_intervenants, taux_erreur_mots


def _justesse(reference: dict, segments: list[dict]) -> float | None:
    """Justesse de la séparation ; `None` au-delà de 6 groupes (appariement trop coûteux), le nombre suffit alors."""
    try:
        return round(justesse_intervenants(reference['tours'], segments), 4)
    except ValueError:
        return None


def mesurer(audio: Path, reference: dict, moteur, convertir=moteur_.convertir, horloge=time.monotonic,
            travail: Path | None = None) -> dict:
    debut = horloge()
    with tempfile.TemporaryDirectory(dir=travail, prefix='.piste-') as tmp:
        wav = Path(tmp) / 'piste.wav'
        duree = convertir(audio, wav)
        converti = horloge()
        transcrits = moteur.transcrire(wav, lambda pourcentage: None)
        transcrit = horloge()
        tours = moteur.separer(wav)
    fin = horloge()
    segments = attribuer(transcrits, tours)
    traitement = fin - debut
    return {'duree_audio_s': round(duree, 1), 'duree_traitement_s': round(traitement, 1),
            'facteur': round(traitement / duree, 3) if duree else 0.0,
            'taux_erreur': round(taux_erreur_mots(reference['texte'], ' '.join(s['texte'] for s in segments)), 4),
            'justesse_intervenants': _justesse(reference, segments),
            'intervenants': len({s['intervenant'] for s in segments}),
            'etapes_s': {'conversion': round(converti - debut, 1), 'transcription': round(transcrit - converti, 1),
                         'separation': round(fin - transcrit, 1)}}


def main(argv: list[str]) -> int:
    parseur = argparse.ArgumentParser(prog='travaux.banc')
    parseur.add_argument('--audio', required=True, type=Path)
    parseur.add_argument('--reference', required=True, type=Path)
    parseur.add_argument('--modele', required=True)
    parseur.add_argument('--modeles', default='/modeles', type=Path)
    parseur.add_argument('--fils', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = parseur.parse_args(argv)
    reference = json.loads(args.reference.read_text(encoding='utf-8'))
    resultat = mesurer(args.audio, reference, moteur_.Moteur(args.modeles, args.modele, args.fils), travail=args.audio.parent)
    print(json.dumps(resultat, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
