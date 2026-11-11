# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from travaux import moteur
from travaux.attribution import attribuer
from travaux.mesures import justesse_intervenants, taux_erreur_mots

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
FFMPEG = shutil.which('ffmpeg') and shutil.which('ffprobe')
MODELES = Path(os.environ.get('TRAVAUX_MODELES', '/modeles'))
MANIFESTE = Path(__file__).resolve().parents[1] / 'conteneurs' / 'modeles-audio.json'


def sinus(chemin: Path, secondes: int) -> Path:
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:duration={secondes}', str(chemin)], check=True)
    return chemin


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dossier = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_convertir_en_mono_16_khz(self):
        duree = moteur.convertir(sinus(self.dossier / 'son.mp3', 3), self.dossier / 'piste.wav')
        self.assertAlmostEqual(duree, 3.0, delta=0.1)
        import wave
        with wave.open(str(self.dossier / 'piste.wav'), 'rb') as piste:
            self.assertEqual((piste.getnchannels(), piste.getframerate(), piste.getsampwidth()), (1, 16000, 2))

    def test_convertir_refuse_un_fichier_sans_audio(self):
        texte = self.dossier / 'note.mp3'
        texte.write_text('pas un son', encoding='utf-8')
        with self.assertRaises(moteur.ErreurTraitement) as erreur:
            moteur.convertir(texte, self.dossier / 'piste.wav')
        self.assertEqual(erreur.exception.code, 'illisible')

    def test_convertir_refuse_au_dela_de_la_duree_maximale(self):
        with self.assertRaises(moteur.ErreurTraitement) as erreur:
            moteur.convertir(sinus(self.dossier / 'long.wav', 3), self.dossier / 'piste.wav', duree_max_s=1)
        self.assertEqual(erreur.exception.code, 'trop_long')

    def test_modeles_absents(self):
        self.assertFalse(moteur.Moteur(self.dossier, 'large-v3-turbo', 2, MANIFESTE).modeles_ok())


class DureeMaximaleTests(unittest.TestCase):
    def test_duree_maximale_bornee_par_la_memoire_de_la_separation(self):
        # Banc de la tâche 4 : au-delà de 2 heures, la séparation des voix dépasse les 2,5 Go visés pour le conteneur.
        self.assertEqual(moteur.DUREE_MAX_S, 2 * 3600)

    def test_page_seances_annonce_la_duree_maximale(self):
        page = (Path(__file__).resolve().parents[1] / 'app' / 'public' / 'app.js').read_text(encoding='utf-8')
        heures = moteur.DUREE_MAX_S // 3600
        self.assertEqual(page.count(f'dépasse {heures} heures'), 1)
        self.assertEqual(page.count(f'fichier audio ou vidéo de {heures} heures'), 1)
        # L'ancienne durée ne revient pas ; « 24 heures » (durée de l'essai d'un visiteur) n'en est pas une.
        self.assertNotRegex(page, r'(?<!\d)4 heures')


def _faux_whisper(appels, reponses):
    """Faux faster-whisper : note chaque appel (signal, options) et rend, morceau par morceau, les segments prévus."""
    class FauxModele:
        chargements = 0

        def __init__(self, *args, **options):
            FauxModele.chargements += 1

        def transcribe(self, audio, **options):
            appels.append((audio, options))
            segments, duree = reponses[len(appels) - 1]
            return iter(types.SimpleNamespace(start=d, end=f, text=t) for d, f, t in segments), types.SimpleNamespace(duration=duree)
    return FauxModele


class DecoupageTests(unittest.TestCase):
    def test_audio_plus_court_qu_un_morceau_sans_coupe(self):
        self.assertEqual(moteur.points_de_coupe([1.0] * 500, trame_s=1.0, morceau_s=600, marge_s=30), [])

    def test_coupe_au_moment_le_plus_calme_des_dernieres_secondes(self):
        energies = [5.0] * 1500
        energies[580:583] = [0.0, 0.0, 0.0]   # silence vers 581 s, dans les 30 s avant 600 s
        energies[300:303] = [0.0, 0.0, 0.0]   # silence hors de la marge : ignoré
        coupes = moteur.points_de_coupe(energies, trame_s=1.0, morceau_s=600, marge_s=30)
        self.assertAlmostEqual(coupes[0], 581.5)
        self.assertEqual(len(coupes), 2)
        self.assertLessEqual(coupes[1] - coupes[0], 600)   # le morceau suivant part de la coupe
        self.assertGreaterEqual(coupes[1] - coupes[0], 570)

    def test_quatre_heures_restent_en_morceaux_bornes(self):
        coupes = moteur.points_de_coupe([1.0] * 144000, trame_s=0.1, morceau_s=600, marge_s=30)
        bornes = [0.0] + coupes + [14400.0]
        self.assertTrue(all(0 < fin - debut <= 600 for debut, fin in zip(bornes, bornes[1:])))


class ConversionEchouee(unittest.TestCase):
    def test_echec_de_ffmpeg_apres_une_sonde_reussie_n_est_pas_illisible(self):
        # Fichier lisible (ffprobe y voit une piste audio) mais conversion ratée, par exemple disque plein : l'audio est bon
        # et ne doit pas être effacé comme un fichier illisible.
        sonde = types.SimpleNamespace(returncode=0, stdout=json.dumps({'streams': [{'codec_type': 'audio'}], 'format': {'duration': '60'}}))
        rate = types.SimpleNamespace(returncode=1, stdout=b'', stderr=b'No space left on device')
        with mock.patch.object(moteur.subprocess, 'run', side_effect=[sonde, rate]):
            with self.assertRaises(moteur.ErreurTraitement) as erreur:
                moteur.convertir(Path('seance.mp3'), Path('piste.wav'))
        self.assertEqual(erreur.exception.code, 'interne')


class SeparerTests(unittest.TestCase):
    def test_separer_rend_compte_de_sa_progression(self):
        recus = []

        class FauxSeparation:
            def __init__(self, configuration):
                pass

            def process(self, signal, callback=None):
                for fait in (1, 2, 4):
                    callback(fait, 4)
                tours = [types.SimpleNamespace(start=0.0, end=1.0, speaker=0)]
                return types.SimpleNamespace(sort_by_start_time=lambda: tours)

        faux = types.SimpleNamespace(
            OfflineSpeakerDiarizationConfig=lambda **options: types.SimpleNamespace(validate=lambda: True),
            OfflineSpeakerSegmentationModelConfig=lambda **options: None,
            OfflineSpeakerSegmentationPyannoteModelConfig=lambda **options: None,
            SpeakerEmbeddingExtractorConfig=lambda **options: None, FastClusteringConfig=lambda **options: None,
            OfflineSpeakerDiarization=FauxSeparation)
        with mock.patch.dict(sys.modules, {'sherpa_onnx': faux}), mock.patch.object(moteur, '_lire_wav', return_value=object()), \
                mock.patch.object(moteur.Moteur, '_chemin', return_value=Path('/m/x.onnx')):
            tours = moteur.Moteur(Path('/m'), 'large-v3-turbo', 2).separer(Path('piste.wav'), recus.append)
        self.assertEqual(recus, [25.0, 50.0, 100.0])
        self.assertEqual(tours, [{'debut': 0.0, 'fin': 1.0, 'groupe': 0}])


class TranscrireTests(unittest.TestCase):
    def _transcrire(self, reponses, coupes, duree, lus=None):
        appels, progres = [], []
        faux = _faux_whisper(appels, reponses)
        lire = lus.append if lus is not None else (lambda bornes: None)
        with mock.patch.dict(sys.modules, {'faster_whisper': types.SimpleNamespace(WhisperModel=faux)}), \
                mock.patch.object(moteur, '_duree', return_value=duree), \
                mock.patch.object(moteur, '_energies', return_value=[]), \
                mock.patch.object(moteur, 'points_de_coupe', return_value=coupes), \
                mock.patch.object(moteur, '_lire_morceau', side_effect=lambda wav, debut, fin: lire((debut, fin)) or ('signal', debut, fin)), \
                mock.patch.object(moteur, '_rendre_memoire') as rendre:
            segments = moteur.Moteur(Path('/m'), 'large-v3-turbo', 2).transcrire(Path('piste.wav'), progres.append)
        self.rendus = rendre.call_count
        return segments, appels, progres, faux

    def test_transcrire_donne_le_signal_deja_lu_sans_passer_par_pyav(self):
        # faster-whisper 1.2.1 décode un chemin avec une option que PyAV 19 refuse : on lui donne le signal lu.
        segments, appels, _, _ = self._transcrire([([(0.0, 1.0, ' bonjour ')], 1.0)], [], 1.0)
        self.assertEqual(appels[0][0], ('signal', 0.0, 1.0))
        self.assertEqual(segments, [{'debut': 0.0, 'fin': 1.0, 'texte': 'bonjour'}])

    def test_transcrire_sans_contexte_ni_faisceau_et_sans_segment_au_dela_de_la_fin(self):
        # Mesuré au banc : sans le texte précédent comme contexte, plus de texte inventé sur le silence final, et 3 fois
        # plus rapide ; un segment qui commencerait après la fin de l'audio est forcément inventé.
        segments, appels, _, _ = self._transcrire([([(0.0, 1.5, 'bonjour'), (2.0, 9.0, 'inventé')], 2.0)], [], 2.0)
        self.assertEqual({k: appels[0][1][k] for k in ('language', 'vad_filter', 'beam_size', 'condition_on_previous_text', 'temperature')},
                         {'language': 'fr', 'vad_filter': True, 'beam_size': 1, 'condition_on_previous_text': False, 'temperature': 0.0})
        self.assertEqual([s['texte'] for s in segments], ['bonjour'])

    def test_par_morceaux_avec_un_seul_chargement_et_les_temps_de_la_seance(self):
        # faster-whisper calcule le spectre de tout le signal d'un coup (plus d'1 Go par heure) : morceaux d'environ 10 min.
        lus = []
        reponses = [([(1.0, 2.0, 'un')], 590.0), ([(3.0, 4.0, 'deux')], 600.0), ([(0.5, 1.0, 'trois')], 10.0)]
        segments, _, progres, faux = self._transcrire(reponses, [590.0, 1190.0], 1200.0, lus)
        self.assertEqual(lus, [(0.0, 590.0), (590.0, 1190.0), (1190.0, 1200.0)])
        self.assertEqual(faux.chargements, 1)
        self.assertEqual([(s['debut'], s['texte']) for s in segments], [(1.0, 'un'), (593.0, 'deux'), (1190.5, 'trois')])
        self.assertEqual(progres, sorted(progres))
        self.assertEqual(progres[-1], 100.0)

    def test_memoire_rendue_apres_le_chargement_chaque_morceau_et_le_modele(self):
        # glibc garde la mémoire libérée : les poids pleine précision lus au chargement (plus d'1 Go) resteraient pris,
        # puis le modèle lui-même (0,9 Go) pendant la séparation des voix.
        self._transcrire([([], 590.0), ([], 600.0), ([], 10.0)], [590.0, 1190.0], 1200.0)
        self.assertEqual(self.rendus, 1 + 3 + 1)

    def test_rendre_memoire_sans_glibc_ne_fait_rien(self):
        with mock.patch.object(moteur.ctypes, 'CDLL', side_effect=OSError):
            moteur._rendre_memoire()


@unittest.skipUnless(FFMPEG and moteur.Moteur(MODELES, os.environ.get('TERRITORIA_TRANSCRIPTION_MODELE', 'large-v3-turbo'), 1,
                                              MANIFESTE if MANIFESTE.exists() else moteur.MANIFESTE).modeles_ok(),
                     'modèles absents : bout-en-bout lancé dans le conteneur de travaux (tâche 4)')
class BoutEnBoutTests(unittest.TestCase):
    def test_bout_en_bout_trois_voix(self):
        reference = json.loads((FIXTURES / 'seance-3-voix.json').read_text(encoding='utf-8'))
        m = moteur.Moteur(MODELES, os.environ.get('TERRITORIA_TRANSCRIPTION_MODELE', 'large-v3-turbo'), max(1, (os.cpu_count() or 2) - 2),
                          MANIFESTE if MANIFESTE.exists() else moteur.MANIFESTE)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'piste.wav'
            moteur.convertir(FIXTURES / 'seance-3-voix.wav', wav)
            segments = attribuer(m.transcrire(wav, lambda p: None), m.separer(wav))
        self.assertLessEqual(taux_erreur_mots(reference['texte'], ' '.join(s['texte'] for s in segments)), 0.10)
        self.assertGreaterEqual(justesse_intervenants(reference['tours'], segments), 0.90)


if __name__ == '__main__':
    unittest.main()
