# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from travaux import banc  # noqa: E402

REFERENCE = {'texte': 'le conseil municipal délibère', 'tours': [{'debut': 0.0, 'fin': 2.0, 'groupe': 0},
                                                                   {'debut': 2.5, 'fin': 4.0, 'groupe': 1}]}


class FauxMoteur:
    modele = 'faux'

    def transcrire(self, wav, progres):
        progres(100.0)
        return [{'debut': 0.0, 'fin': 2.0, 'texte': 'le conseil'}, {'debut': 2.5, 'fin': 4.0, 'texte': 'municipal délibère'}]

    def separer(self, wav):
        return [{'debut': 0.0, 'fin': 2.0, 'groupe': 7}, {'debut': 2.5, 'fin': 4.0, 'groupe': 3}]


class BancTests(unittest.TestCase):
    def test_mesurer_rend_les_mesures_et_la_duree_de_chaque_etape(self):
        temps = iter([100.0, 102.0, 122.0, 130.0])
        resultat = banc.mesurer(Path('seance.wav'), REFERENCE, FauxMoteur(), convertir=lambda source, destination: 60.0,
                                horloge=lambda: next(temps))
        self.assertEqual(resultat, {'duree_audio_s': 60.0, 'duree_traitement_s': 30.0, 'facteur': 0.5, 'taux_erreur': 0.0,
                                    'justesse_intervenants': 1.0, 'intervenants': 2,
                                    'etapes_s': {'conversion': 2.0, 'transcription': 20.0, 'separation': 8.0}})

    def test_plus_de_six_intervenants_ne_fait_pas_echouer_la_mesure(self):
        class SeptVoix(FauxMoteur):
            def transcrire(self, wav, progres):
                return [{'debut': float(i), 'fin': i + 0.9, 'texte': 'mot'} for i in range(7)]

            def separer(self, wav):
                return [{'debut': float(i), 'fin': i + 0.9, 'groupe': i} for i in range(7)]

        resultat = banc.mesurer(Path('seance.wav'), REFERENCE, SeptVoix(), convertir=lambda source, destination: 7.0)
        self.assertIsNone(resultat['justesse_intervenants'])
        self.assertEqual(resultat['intervenants'], 7)

    def test_le_wav_intermediaire_va_dans_le_dossier_de_travail_et_disparait(self):
        vus = []

        def convertir(source, destination):
            vus.append(Path(destination))
            Path(destination).write_bytes(b'RIFF')
            return 4.0

        with banc.tempfile.TemporaryDirectory() as travail:
            banc.mesurer(Path('seance.wav'), REFERENCE, FauxMoteur(), convertir=convertir, travail=Path(travail))
            self.assertEqual(vus[0].parent.parent, Path(travail))  # comme le service : sur le disque, pas dans /tmp
            self.assertFalse(vus[0].exists())

    def test_ligne_de_commande_ecrit_une_ligne_json(self):
        with mock.patch.object(banc, 'mesurer', return_value={'facteur': 0.5}) as mesurer, \
                mock.patch.object(Path, 'read_text', return_value=json.dumps(REFERENCE)), redirect_stdout(io.StringIO()) as sortie:
            code = banc.main(['--audio', 'a.wav', '--reference', 'r.json', '--modele', 'medium', '--modeles', '/m', '--fils', '3'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie.getvalue()), {'facteur': 0.5})
        moteur = mesurer.call_args.args[2]
        self.assertEqual((moteur.modele, moteur.fils, str(moteur.dossier)), ('medium', 3, '/m'))


if __name__ == '__main__':
    unittest.main()
