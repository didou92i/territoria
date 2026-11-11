# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from travaux import protocole, service  # noqa: E402
from travaux.moteur import ErreurTraitement  # noqa: E402

SEGMENTS = [{'debut': 0.0, 'fin': 2.0, 'texte': 'La séance est ouverte.'}, {'debut': 2.5, 'fin': 4.0, 'texte': 'Merci.'}]
TOURS = [{'debut': 0.0, 'fin': 2.2, 'groupe': 5}, {'debut': 2.4, 'fin': 4.0, 'groupe': 2}]


class FauxMoteur:
    modele = 'large-v3-turbo'

    def __init__(self, erreur=None, pendant=None, ok=True, pendant_separation=None):
        self.erreur, self.pendant, self.ok, self.wavs = erreur, pendant, ok, []
        self.pendant_separation = pendant_separation

    def modeles_ok(self):
        return self.ok

    def transcrire(self, wav, progres):
        self.wavs.append(Path(wav))
        if self.pendant:
            self.pendant()
        if self.erreur:
            raise self.erreur
        progres(50.0)
        progres(100.0)
        return list(SEGMENTS)

    def separer(self, wav, progres=None):
        if progres:
            progres(50.0)
        if self.pendant_separation:
            self.pendant_separation()
        if progres:
            progres(100.0)
        return list(TOURS)


def faux_convertir(source, destination):
    Path(destination).write_bytes(b'RIFF')
    return 4.0


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.racine = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.racine, True)

    def _seance(self, nom='a' * 32, date=None):
        dossier = self.racine / nom
        dossier.mkdir()
        (dossier / 'source.mp3').write_bytes(b'audio')
        protocole.ecrire_json(dossier / protocole.DEMANDE, {'version': 1, 'langue': 'fr', 'modele': 'large-v3-turbo', 'source': 'source.mp3'})
        if date is not None:
            os.utime(dossier / protocole.DEMANDE, (date, date))
        return dossier

    def test_resultat_avec_intervenants(self):
        dossier = self._seance()
        service.traiter(dossier, FauxMoteur(), convertir=faux_convertir)
        resultat = protocole.lire_json(dossier / protocole.RESULTAT)
        self.assertEqual((resultat['version'], resultat['duree_s'], resultat['modele']), (1, 4.0, 'large-v3-turbo'))
        self.assertIsInstance(resultat['duree_traitement_s'], float)
        self.assertEqual([(s['intervenant'], s['texte']) for s in resultat['segments']],
                         [('Intervenant 1', 'La séance est ouverte.'), ('Intervenant 2', 'Merci.')])
        progres = protocole.lire_json(dossier / protocole.PROGRES)
        self.assertEqual((progres['etape'], progres['pourcentage']), ('intervenants', 100))
        self.assertIn('maj', progres)
        self.assertEqual(stat.S_IMODE((dossier / protocole.RESULTAT).stat().st_mode), 0o600)

    def test_ordre_d_arrivee(self):
        for nom, date in (('c' * 32, 3_000_000), ('a' * 32, 1_000_000), ('b' * 32, 2_000_000)):
            self._seance(nom, date)
        fini = self._seance('d' * 32, 500_000)
        protocole.ecrire_json(fini / protocole.RESULTAT, {'version': 1})
        echoue = self._seance('e' * 32, 600_000)
        protocole.ecrire_json(echoue / protocole.ERREUR, {'code': 'interne'})
        (self.racine / ('f' * 32)).mkdir()  # envoi en cours : pas encore de demande
        self.assertEqual([d.name for d in protocole.en_attente(self.racine)], ['a' * 32, 'b' * 32, 'c' * 32])

    def test_codes_d_erreur(self):
        for erreur, code in ((MemoryError(), 'memoire'), (ErreurTraitement('trop_long'), 'trop_long'), (KeyError('x'), 'interne')):
            with self.subTest(code=code):
                dossier = self._seance(code.ljust(32, '0'))
                service.traiter(dossier, FauxMoteur(erreur=erreur), convertir=faux_convertir)
                self.assertEqual(protocole.lire_json(dossier / protocole.ERREUR), {'code': code})
                self.assertFalse((dossier / protocole.RESULTAT).exists())

    def test_source_hors_du_dossier_refusee(self):
        dossier = self._seance()
        protocole.ecrire_json(dossier / protocole.DEMANDE, {'version': 1, 'source': '../../etc/passwd'})
        service.traiter(dossier, FauxMoteur(), convertir=faux_convertir)
        self.assertEqual(protocole.lire_json(dossier / protocole.ERREUR), {'code': 'illisible'})

    def test_dossier_supprime_pendant_le_traitement(self):
        dossier = self._seance()
        service.traiter(dossier, FauxMoteur(pendant=lambda: shutil.rmtree(dossier)), convertir=faux_convertir)
        self.assertFalse(dossier.exists())

    def test_reprise_apres_redemarrage(self):
        dossier = self._seance()
        protocole.ecrire_json(dossier / protocole.PROGRES, {'etape': 'transcription', 'pourcentage': 40, 'maj': 'x'})
        self.assertEqual(protocole.en_attente(self.racine), [dossier])
        service.boucle(self.racine, lambda: FauxMoteur(), attente_s=0, tours=1, convertir=faux_convertir)
        self.assertIsNotNone(protocole.lire_json(dossier / protocole.RESULTAT))

    def test_modeles_absents_les_seances_attendent(self):
        dossier = self._seance()
        service.boucle(self.racine, lambda: FauxMoteur(ok=False), attente_s=0, tours=1, convertir=faux_convertir)
        self.assertEqual(protocole.en_attente(self.racine), [dossier])
        self.assertFalse(protocole.lire_json(self.racine / protocole.BATTEMENT)['modeles_ok'])

    def test_battement(self):
        service.battre(self.racine, FauxMoteur())
        battement = protocole.lire_json(self.racine / protocole.BATTEMENT)
        self.assertEqual(set(battement), {'maj', 'modele', 'modeles_ok'})
        self.assertEqual((battement['modele'], battement['modeles_ok']), ('large-v3-turbo', True))
        self.assertRegex(battement['maj'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d')

    def test_aucune_empreinte_ni_wav_ne_reste(self):
        dossier = self._seance()
        moteur = FauxMoteur()
        service.traiter(dossier, moteur, convertir=faux_convertir)
        self.assertEqual(sorted(p.name for p in dossier.iterdir()), ['demande.json', 'progres.json', 'resultat.json', 'source.mp3'])
        # Sur le disque, dans le dossier de la séance, et non dans /tmp : /tmp est en mémoire et compterait dans les 3 Go.
        self.assertEqual(moteur.wavs[0].parent.parent, dossier)
        self.assertFalse(moteur.wavs[0].exists())

    def test_seance_interrompue_deux_fois_passe_en_echec_memoire(self):
        # Sous un plafond de mémoire, le noyau tue le processus sans MemoryError : seule la marque de tentative le révèle.
        dossier = self._seance()
        protocole.ecrire_json(dossier / service.TENTATIVE, {'nombre': 2})
        service.traiter(dossier, FauxMoteur(), convertir=faux_convertir)
        self.assertEqual(protocole.lire_json(dossier / protocole.ERREUR), {'code': 'memoire'})
        self.assertFalse((dossier / service.TENTATIVE).exists())

    def test_seance_interrompue_une_fois_reprise_sans_piste_residuelle(self):
        dossier = self._seance()
        protocole.ecrire_json(dossier / service.TENTATIVE, {'nombre': 1})
        (dossier / '.piste-ancienne').mkdir()
        (dossier / '.piste-ancienne' / 'piste.wav').write_bytes(b'RIFF' * 1000)
        vus = []
        service.traiter(dossier, FauxMoteur(pendant=lambda: vus.append(protocole.lire_json(dossier / service.TENTATIVE))),
                        convertir=faux_convertir)
        self.assertEqual(vus, [{'nombre': 2}])
        self.assertIsNotNone(protocole.lire_json(dossier / protocole.RESULTAT))
        self.assertFalse((dossier / service.TENTATIVE).exists())
        self.assertFalse((dossier / '.piste-ancienne').exists())

    def test_progression_des_intervenants_avec_temps_restant(self):
        dossier = self._seance()
        vus = []
        service.traiter(dossier, FauxMoteur(pendant_separation=lambda: vus.append(protocole.lire_json(dossier / protocole.PROGRES))),
                        convertir=faux_convertir)
        self.assertEqual((vus[0]['etape'], vus[0]['pourcentage']), ('intervenants', 50))
        self.assertIsInstance(vus[0]['reste_s'], int)

    def test_battement_ecrit_pendant_un_long_calcul(self):
        # Le calcul peut garder la main des heures : le battement est aussi écrit depuis les rappels de progression.
        dossier = self._seance()
        with mock.patch.object(service, 'BATTEMENT_S', 0):
            service.traiter(dossier, FauxMoteur(), convertir=faux_convertir)
        self.assertEqual(protocole.lire_json(self.racine / protocole.BATTEMENT)['modele'], 'large-v3-turbo')

    def test_disque_plein_a_l_ecriture_de_l_erreur_ne_fait_pas_tomber_le_service(self):
        dossier = self._seance()
        ecrire = protocole.ecrire_json

        def plein(chemin, donnees):
            if Path(chemin).name == protocole.ERREUR:
                raise OSError(28, 'No space left on device')
            ecrire(chemin, donnees)

        with mock.patch.object(service, 'ecrire_json', plein):
            service.traiter(dossier, FauxMoteur(erreur=KeyError('x')), convertir=faux_convertir)
        self.assertFalse((dossier / protocole.ERREUR).exists())

    def test_lire_json_absent_ou_illisible(self):
        (self.racine / 'casse.json').write_text('{pas du json', encoding='utf-8')
        self.assertIsNone(protocole.lire_json(self.racine / 'casse.json'))
        self.assertIsNone(protocole.lire_json(self.racine / 'absent.json'))

    def test_aucun_contenu_dans_les_journaux(self):
        dossier = self._seance()
        with self.assertLogs('territoria.travaux', level='INFO') as journaux:
            service.traiter(dossier, FauxMoteur(), convertir=faux_convertir)
            service.traiter(self._seance('b' * 32), FauxMoteur(erreur=ValueError('Merci.')), convertir=faux_convertir)
        texte = '\n'.join(journaux.output)
        for interdit in ('séance est ouverte', 'Merci', 'Intervenant'):
            self.assertNotIn(interdit, texte)


if __name__ == '__main__':
    unittest.main()
