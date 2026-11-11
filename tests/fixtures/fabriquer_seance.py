# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Fabrique un enregistrement fictif de séance (macOS : voix de synthèse `say`, ffmpeg) et sa référence.

Trois voix françaises se répondent ; les répliques sont des phrases d'articles de loi publics (copies de référence de
Territoria). Sortie : un WAV mono 16 kHz et un JSON `{"texte": ..., "tours": [{"debut", "fin", "groupe"}]}`.

    python tests/fixtures/fabriquer_seance.py --repliques 12 --sortie tests/fixtures/seance-3-voix.wav
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import wave
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
VOIX = ('Eddy (Français (France))', 'Flo (Français (France))', 'Grandpa (Français (France))')
SILENCE_S = 0.5
REFERENCES = ('deliberations-reference.json', 'arretes-rh-reference.json', 'arretes-police-reference.json',
              'arretes-delegations-reference.json')


def phrases() -> list[str]:
    vues, resultat = set(), []
    for nom in REFERENCES:
        donnees = json.loads((RACINE / 'app' / 'territoria' / 'ressources' / nom).read_text(encoding='utf-8'))
        for source in donnees['sources']:
            corps = source['text'].split('\n\n', 1)[-1]
            for phrase in re.split(r'(?<=[.;])\s+', corps):
                phrase = re.sub(r'\s+', ' ', phrase).strip()
                if 60 <= len(phrase) <= 220 and phrase not in vues and not re.search(r'\d', phrase):
                    vues.add(phrase)
                    resultat.append(phrase)
    return resultat


def duree(wav: Path) -> float:
    with wave.open(str(wav), 'rb') as piste:
        return piste.getnframes() / piste.getframerate()


def fabriquer(repliques: int, sortie: Path, reference: Path) -> None:
    textes = phrases()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        silence = tmp / 'silence.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anullsrc=r=16000:cl=mono', '-t', str(SILENCE_S),
                        '-c:a', 'pcm_s16le', str(silence)], check=True)
        morceaux, tours, curseur, dits = [], [], 0.0, []
        for i in range(repliques):
            texte, groupe = textes[i % len(textes)], i % len(VOIX)
            aiff, wav, replique = tmp / f'{i}.aiff', tmp / f'{i}.wav', tmp / f'{i}.txt'
            replique.write_text(texte, encoding='utf-8')  # par fichier : une phrase commençant par « - » n'est pas une option
            subprocess.run(['say', '-v', VOIX[groupe], '-o', str(aiff), '-f', str(replique)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(aiff), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], check=True)
            longueur = duree(wav)
            tours.append({'debut': round(curseur, 2), 'fin': round(curseur + longueur, 2), 'groupe': groupe})
            curseur += longueur + SILENCE_S
            morceaux += [wav, silence]
            dits.append(texte)
        liste = tmp / 'liste.txt'
        liste.write_text(''.join(f"file '{m}'\n" for m in morceaux), encoding='utf-8')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(liste), '-c:a', 'pcm_s16le', str(sortie)],
                       check=True)
    reference.write_text(json.dumps({'texte': ' '.join(dits), 'tours': tours}, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    parseur = argparse.ArgumentParser()
    parseur.add_argument('--repliques', type=int, default=12)
    parseur.add_argument('--sortie', type=Path, required=True)
    args = parseur.parse_args()
    fabriquer(args.repliques, args.sortie, args.sortie.with_suffix('.json'))
