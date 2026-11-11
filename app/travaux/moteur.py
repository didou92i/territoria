# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Moteur du conteneur de travaux : conversion (ffmpeg), transcription (faster-whisper) et séparation des voix (sherpa-onnx).

Tout tourne sur le processeur, sans réseau. Les bibliothèques lourdes ne sont importées qu'au moment du calcul : le reste du
paquet (protocole, service, mesures) reste utilisable et testable sans elles. Les empreintes de locuteur calculées par
sherpa-onnx ne vivent qu'en mémoire, le temps de regrouper les tours de parole : rien n'en est écrit.
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import wave
from pathlib import Path
from typing import Callable

from . import modeles

# La séparation des voix reçoit tout le signal et compare toutes les empreintes deux à deux : mesuré au banc (tâche 4),
# 2,25 Go à 2 h, 2,88 Go à 2 h 28 pour un conteneur plafonné à 3 Go. Au-delà de 2 heures, la séance est refusée.
DUREE_MAX_S = 2 * 3600
FREQUENCE = 16000
MANIFESTE = Path('/app/modeles-audio.json')
# Distance au-delà de laquelle deux voix forment deux groupes. Mesuré au banc (tâche 4), 3 voix : sur 72 s, 3 groupes de
# 0,6 à 0,7 et 2 à 0,8 ; sur 25 min, 9 groupes à 0,6 et 6 à 0,7 (justesse 0,92). On reste sous le seuil où deux voix se
# confondent : deux groupes pour une même personne se fusionnent à la relecture, deux personnes confondues non.
SEUIL_REGROUPEMENT = 0.7
# faster-whisper calcule le spectre de tout le signal qu'on lui donne d'un coup (plus d'1 Go par heure d'audio) : on lui
# donne des morceaux d'environ 10 minutes, coupés au moment le plus calme de leurs 30 dernières secondes. La mémoire de la
# transcription ne dépend plus de la durée de la séance ; celle de la séparation des voix, qui reçoit tout le signal, si.
MORCEAU_S = 600
MARGE_S = 30
TRAME_S = 0.1
BLOC = 60 * FREQUENCE  # échantillons lus à la fois


class ErreurTraitement(Exception):
    """Échec attendu d'un traitement ; `code` est l'un des codes du protocole (illisible, trop_long, memoire, interne)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def convertir(source: Path, destination: Path, duree_max_s: int = DUREE_MAX_S) -> float:
    """Piste mono 16 kHz PCM 16 bits ; rend la durée en secondes."""
    try:
        sonde = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type:format=duration',
                                '-of', 'json', str(source)], capture_output=True, text=True, timeout=120)
        infos = json.loads(sonde.stdout or '{}')
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ErreurTraitement('illisible') from exc
    if sonde.returncode != 0 or not infos.get('streams'):
        raise ErreurTraitement('illisible')
    try:
        duree = float((infos.get('format') or {}).get('duration') or 0)
    except ValueError:
        duree = 0.0
    if duree > duree_max_s:
        raise ErreurTraitement('trop_long')
    conversion = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source), '-vn', '-ac', '1', '-ar', str(FREQUENCE),
                                 '-c:a', 'pcm_s16le', str(destination)], capture_output=True, timeout=4 * 3600)
    if conversion.returncode != 0 or not Path(destination).is_file():
        # ffprobe y a vu une piste audio : le fichier est bon, la conversion a échoué pour une autre raison (disque plein…).
        raise ErreurTraitement('interne')
    with wave.open(str(destination), 'rb') as piste:
        reelle = piste.getnframes() / piste.getframerate()
    if reelle > duree_max_s:
        raise ErreurTraitement('trop_long')
    return reelle


def points_de_coupe(energies: list[float], trame_s: float, morceau_s: float = MORCEAU_S, marge_s: float = MARGE_S) -> list[float]:
    """Instants (en secondes) où couper l'audio en morceaux d'au plus `morceau_s` : dans les `marge_s` dernières secondes
    de chaque morceau, au centre de la demi-seconde la plus calme (énergie moyenne par trame de `trame_s`)."""
    largeur = max(1, round(0.5 / trame_s))
    total = len(energies) * trame_s
    coupes, debut = [], 0.0
    while total - debut > morceau_s:
        premiere = max(0, round((debut + morceau_s - marge_s) / trame_s))
        derniere = round((debut + morceau_s) / trame_s) - largeur
        scores = {i: sum(energies[i:i + largeur]) for i in range(premiere, derniere + 1)}
        plus_calme = min(scores.values())
        calmes = [i for i, score in scores.items() if score == plus_calme]
        debut = (calmes[len(calmes) // 2] + largeur / 2) * trame_s  # à égalité (silence complet) : au milieu du silence
        coupes.append(debut)
    return coupes


def _duree(wav: Path) -> float:
    with wave.open(str(wav), 'rb') as piste:
        return piste.getnframes() / piste.getframerate()


def _energies(wav: Path, trame_s: float = TRAME_S) -> list[float]:
    """Énergie moyenne de chaque trame, lue par blocs (jamais tout le signal en mémoire)."""
    import numpy as np
    pas = round(trame_s * FREQUENCE)
    energies = []
    with wave.open(str(wav), 'rb') as piste:
        while brut := piste.readframes(BLOC - BLOC % pas):
            bloc = np.frombuffer(brut, dtype=np.int16).astype(np.float32)
            entieres = len(bloc) // pas * pas
            energies.extend((bloc[:entieres].reshape(-1, pas) ** 2).mean(axis=1).tolist())
            if entieres < len(bloc):
                energies.append(float((bloc[entieres:] ** 2).mean()))
    return energies


def _lire_morceau(wav: Path, debut_s: float, fin_s: float | None = None):
    """Signal flottant (−1 à 1) entre deux instants, converti par blocs : une seule copie du morceau en mémoire."""
    import numpy as np
    with wave.open(str(wav), 'rb') as piste:
        total = piste.getnframes()
        premier = min(total, round(debut_s * FREQUENCE))
        dernier = total if fin_s is None else min(total, round(fin_s * FREQUENCE))
        signal = np.empty(max(0, dernier - premier), dtype=np.float32)
        piste.setpos(premier)
        position = 0
        while position < len(signal):
            bloc = np.frombuffer(piste.readframes(min(BLOC, len(signal) - position)), dtype=np.int16)
            if not len(bloc):
                break
            signal[position:position + len(bloc)] = bloc
            position += len(bloc)
    signal = signal[:position]
    signal *= 1 / 32768.0
    return signal


def _rendre_memoire() -> None:
    """Rend au système la mémoire libérée que glibc garde pour plus tard : sans cela, les poids pleine précision lus au
    chargement du modèle (plus d'1 Go, libérés une fois compressés en int8) resteraient comptés au conteneur."""
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):  # hors de Linux (tests sur le poste) : rien à rendre
        pass


def _lire_wav(wav: Path):
    return _lire_morceau(wav, 0.0)


class Moteur:
    def __init__(self, dossier_modeles: Path, modele: str, fils: int, manifeste: Path = MANIFESTE):
        self.dossier = Path(dossier_modeles)
        self.modele = modele
        self.fils = max(1, int(fils))
        self.manifeste = Path(manifeste)

    def modeles_ok(self) -> bool:
        """Fichiers du modèle et de la séparation présents, à la taille du manifeste (sans tout relire : appelé souvent)."""
        try:
            manifeste = modeles.lire_manifeste(self.manifeste)
            fichiers = [f for nom in (self.modele, 'segmentation', 'empreintes') for f in manifeste['modeles'][nom]['fichiers']]
        except (OSError, ValueError, KeyError):
            return False
        for fichier in fichiers:
            chemin = self.dossier / fichier['chemin']
            if not chemin.is_file() or (isinstance(fichier.get('taille'), int) and chemin.stat().st_size != fichier['taille']):
                return False
        return True

    def _chemin(self, nom: str) -> Path:
        manifeste = modeles.lire_manifeste(self.manifeste)
        return self.dossier / manifeste['modeles'][nom]['fichiers'][0]['chemin']

    def transcrire(self, wav: Path, progres: Callable[[float], None]) -> list[dict]:
        from faster_whisper import WhisperModel
        duree = _duree(wav) or 1.0
        bornes = [0.0] + points_de_coupe(_energies(wav), TRAME_S) + [duree]
        modele = WhisperModel(str(self.dossier / self.modele), device='cpu', compute_type='int8', cpu_threads=self.fils)
        _rendre_memoire()
        segments = []
        for debut, fin in zip(bornes, bornes[1:]):
            # Le signal déjà lu, et non le chemin : faster-whisper 1.2.1 décoderait le fichier avec une option que PyAV 19
            # refuse. Réglages mesurés au banc (tâche 4) : sans le texte précédent comme contexte, plus de texte inventé sur
            # les silences et 3 fois plus vite ; recherche gloutonne et température nulle bornent la durée.
            morceaux, infos = modele.transcribe(_lire_morceau(wav, debut, fin), language='fr', vad_filter=True, beam_size=1,
                                                condition_on_previous_text=False, temperature=0.0)
            for morceau in morceaux:
                texte = morceau.text.strip()
                if texte and morceau.start < infos.duration:  # commencé après la fin du morceau : forcément inventé
                    segments.append({'debut': round(debut + morceau.start, 2), 'fin': round(debut + morceau.end, 2), 'texte': texte})
                progres(min(100.0, 100.0 * (debut + morceau.end) / duree))
            progres(min(100.0, 100.0 * fin / duree))
            _rendre_memoire()
        del modele
        _rendre_memoire()
        progres(100.0)
        return segments

    def separer(self, wav: Path, progres: Callable[[float], None] | None = None) -> list[dict]:
        import sherpa_onnx
        configuration = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(self._chemin('segmentation'))),
                num_threads=self.fils),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self._chemin('empreintes')), num_threads=self.fils),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=SEUIL_REGROUPEMENT),
            min_duration_on=0.3, min_duration_off=0.5)
        if not configuration.validate():
            raise ErreurTraitement('interne')
        separation = sherpa_onnx.OfflineSpeakerDiarization(configuration)
        def suivre(faits: int, total: int) -> int:
            if progres and total:
                progres(100.0 * faits / total)
            return 0  # continuer

        tours = separation.process(_lire_wav(wav), callback=suivre).sort_by_start_time()
        return [{'debut': round(t.start, 2), 'fin': round(t.end, 2), 'groupe': int(t.speaker)} for t in tours]
