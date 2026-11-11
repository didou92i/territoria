# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import llm_backend  # noqa: E402
from territoria import administration, seances, seances_pv  # noqa: E402
from territoria.base import migrer  # noqa: E402
from territoria.base.connexion import ouvrir  # noqa: E402
from travaux import protocole  # noqa: E402

SEANCE = {'type': 'conseil', 'titre': 'Conseil municipal du 3 novembre', 'date': '2026-11-03', 'heure': '19:00', 'lieu': 'Mairie'}
# Séance de conseil fictive : deux points annoncés, des montants et des noms dans les passages (lot 3).
SEGMENTS_PV = [
    {'debut': 0.0, 'fin': 4.0, 'intervenant': 'Intervenant 1', 'texte': 'La séance est ouverte, le quorum est atteint.'},
    {'debut': 5.0, 'fin': 12.0, 'intervenant': 'Intervenant 1', 'texte': 'Point numéro 1 : budget primitif 2027. Je donne la parole à Mme Durand.'},
    {'debut': 13.0, 'fin': 30.0, 'intervenant': 'Intervenant 2', 'texte': 'Le budget s’équilibre à 3 000 000 euros.'},
    {'debut': 31.0, 'fin': 40.0, 'intervenant': 'Intervenant 3', 'texte': 'Pourquoi une hausse de 2 % ?'},
    {'debut': 41.0, 'fin': 50.0, 'intervenant': 'Intervenant 1', 'texte': 'Nous passons au vote du budget.'},
    {'debut': 60.0, 'fin': 66.0, 'intervenant': 'Intervenant 1', 'texte': 'Point 2, tarifs de la cantine scolaire.'},
    {'debut': 67.0, 'fin': 80.0, 'intervenant': 'Intervenant 2', 'texte': 'Les tarifs restent inchangés.'},
]
DETAILS = {'president': 'M. Martin', 'secretaires': ['Mme Durand'], 'membres_en_exercice': 19,
           'presents': ['M. Martin', 'Mme Durand', 'M. Petit'], 'representes': [{'mandant': 'M. Leroy', 'mandataire': 'M. Martin'}],
           'absents': ['Mme Roux'], 'heure_cloture': '21:30', 'arrete_le': '2026-12-15'}
POINTS = [{'titre': 'Budget primitif 2027', 'deliberation_numero': 'D2026-052', 'rapporteur': 'Mme Durand',
           'vote': {'mode': 'decompte', 'pour': 15, 'contre': 2, 'abstentions': 1}, 'scrutin_particulier': False, 'debut': 5.0},
          {'titre': 'Tarifs de la cantine scolaire', 'deliberation_numero': None, 'rapporteur': None,
           'vote': {'mode': 'unanimite'}, 'scrutin_particulier': True, 'debut': None}]


class Base(unittest.TestCase):
    def setUp(self):
        self.dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dossier, True)
        self.base = self.dossier / 'territoria.db'
        migrer.appliquer(self.base)
        self.donnees = self.dossier

    def _seance(self, proprietaire='alice'):
        return seances.creer(self.base, proprietaire, dict(SEANCE))

    def _envoyee(self):
        ident = self._seance()
        seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'audio', 'mp3')
        seances.terminer_envoi(self.base, self.donnees, ident, 'alice', 'large-v3-turbo')
        return ident

    def _validee(self, type_='conseil', segments=None, valider=True):
        """Séance transcrite (passages de SEGMENTS_PV par défaut), validée sauf demande contraire."""
        ident = seances.creer(self.base, 'alice', dict(SEANCE, type=type_))
        seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'audio', 'mp3')
        seances.terminer_envoi(self.base, self.donnees, ident, 'alice', 'large-v3-turbo')
        protocole.ecrire_json(seances.dossier_audio(self.donnees, ident) / protocole.RESULTAT,
                              {'version': 1, 'duree_s': 80.0, 'modele': 'large-v3-turbo', 'duree_traitement_s': 1.0,
                               'segments': SEGMENTS_PV if segments is None else segments})
        seances.relever(self.base, self.donnees)
        if valider:
            seances.valider(self.base, self.donnees, ident, 'alice')
        return ident


class SeancesTests(Base):
    def test_creer_et_lister_seulement_les_siennes(self):
        ancienne = self._seance()
        recente = seances.creer(self.base, 'alice', dict(SEANCE, titre='Réunion de bureau', type='reunion', date='2026-11-04'))
        self._seance('bob')
        liste = seances.lister(self.base, 'alice')
        self.assertEqual([s['id'] for s in liste], [recente, ancienne])
        self.assertEqual(set(liste[0]), {'id', 'type', 'titre', 'date', 'etat', 'progression', 'cree'})
        self.assertEqual(liste[0]['etat'], 'creee')
        self.assertRegex(ancienne, r'^[0-9a-f]{32}$')
        with self.assertRaises(LookupError):
            seances.lire(self.base, ancienne, 'bob')
        with self.assertRaises(LookupError):
            seances.lire(self.base, 'f' * 32, 'alice')
        lue = seances.lire(self.base, ancienne, 'alice')
        self.assertEqual((lue['titre'], lue['heure'], lue['lieu'], lue['intervenants'], lue['segments'], lue['audio']),
                         ('Conseil municipal du 3 novembre', '19:00', 'Mairie', [], [], False))

    def test_creation_refusee_si_donnees_invalides(self):
        for changement in ({'type': 'fete'}, {'titre': ''}, {'titre': 'x' * 201}, {'date': '03/11/2026'}, {'date': '2026-13-40'},
                           {'heure': '25:00'}, {'lieu': 'x' * 201}):
            with self.subTest(changement=changement), self.assertRaises(ValueError):
                seances.creer(self.base, 'alice', dict(SEANCE, **changement))
        sans_heure = dict(SEANCE)
        del sans_heure['heure'], sans_heure['lieu']
        self.assertEqual(seances.lire(self.base, seances.creer(self.base, 'alice', sans_heure), 'alice')['heure'], None)

    def test_morceaux_dans_l_ordre_sans_doublon(self):
        ident = self._seance()
        rangs = [seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', rang, octets, 'webm')
                 for rang, octets in ((0, b'aa'), (1, b'bb'), (1, b'bb'), (2, b'cc'))]
        self.assertEqual(rangs, [1, 2, 2, 3])
        dossier = seances.dossier_audio(self.donnees, ident)
        self.assertEqual((dossier / 'source.webm').read_bytes(), b'aabbcc')
        envoi = protocole.lire_json(dossier / 'envoi.json')
        self.assertEqual((envoi['rang_suivant'], envoi['format'], envoi['octets']), (3, 'webm', 6))
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'reception')
        self.assertTrue(seances.lire(self.base, ident, 'alice')['audio'])

    def test_rang_en_avance_refuse_avec_rang_attendu(self):
        ident = self._seance()
        seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'a', 'webm')
        seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 1, b'b', 'webm')
        with self.assertRaises(seances.Conflit) as refus:
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 5, b'f', 'webm')
        self.assertEqual(refus.exception.rang_suivant, 2)

    def test_limites(self):
        ident = self._seance()
        with self.assertRaises(ValueError):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'x' * (seances.MORCEAU_MAX + 1), 'webm')
        with self.assertRaises(ValueError):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'', 'webm')
        with self.assertRaises(ValueError):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'x', 'exe')
        with mock.patch.object(seances, 'TOTAL_MAX', 5):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'abc', 'webm')
            with self.assertRaises(ValueError):
                seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 1, b'def', 'webm')
        with self.assertRaises(ValueError):  # changer de format en cours d'envoi
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 1, b'd', 'mp3')
        with self.assertRaises(LookupError):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'bob', 1, b'd', 'webm')

    def test_demande_deposee_atomiquement(self):
        ident = self._envoyee()
        demande = protocole.lire_json(seances.dossier_audio(self.donnees, ident) / protocole.DEMANDE)
        self.assertEqual(demande, {'version': 1, 'langue': 'fr', 'modele': 'large-v3-turbo', 'source': 'source.mp3'})
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'attente')
        with self.assertRaises(ValueError):  # plus de morceau après la demande
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 1, b'x', 'mp3')

    def test_terminer_sans_audio_refuse(self):
        ident = self._seance()
        with self.assertRaises(ValueError):
            seances.terminer_envoi(self.base, self.donnees, ident, 'alice', 'large-v3-turbo')

    def test_releve_progression_puis_resultat(self):
        ident = self._envoyee()
        dossier = seances.dossier_audio(self.donnees, ident)
        protocole.ecrire_json(dossier / protocole.PROGRES, {'etape': 'transcription', 'pourcentage': 40, 'maj': 'x'})
        self.assertEqual(seances.relever(self.base, self.donnees), 1)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['progression']), ('transcription', 'transcription 40'))
        protocole.ecrire_json(dossier / protocole.RESULTAT, {'version': 1, 'duree_s': 75.5, 'modele': 'large-v3-turbo', 'duree_traitement_s': 30.0,
                                                             'segments': [{'debut': 0.0, 'fin': 2.0, 'intervenant': 'Intervenant 1', 'texte': 'Bonsoir.'},
                                                                          {'debut': 2.5, 'fin': 5.0, 'intervenant': 'Intervenant 2', 'texte': 'Merci.'},
                                                                          {'debut': 6.0, 'fin': 8.0, 'intervenant': 'Intervenant 1', 'texte': 'Point un.'}]})
        self.assertEqual(seances.relever(self.base, self.donnees), 1)
        self.assertEqual(seances.relever(self.base, self.donnees), 0)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['duree_s']), ('relecture', 75.5))
        self.assertEqual(lue['intervenants'], [{'etiquette': 'Intervenant 1', 'nom': ''}, {'etiquette': 'Intervenant 2', 'nom': ''}])
        self.assertEqual([(s['debut'], s['etiquette'], s['texte'], s['corrige']) for s in lue['segments']],
                         [(0.0, 'Intervenant 1', 'Bonsoir.', False), (2.5, 'Intervenant 2', 'Merci.', False), (6.0, 'Intervenant 1', 'Point un.', False)])
        self.assertTrue(all(isinstance(s['id'], int) for s in lue['segments']))

    def test_releve_sans_parole(self):
        ident = self._envoyee()
        protocole.ecrire_json(seances.dossier_audio(self.donnees, ident) / protocole.RESULTAT,
                              {'version': 1, 'duree_s': 10.0, 'modele': 'medium', 'duree_traitement_s': 3.0, 'segments': []})
        seances.relever(self.base, self.donnees)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['segments'], lue['intervenants'], lue['erreur']), ('relecture', [], [], None))

    def test_releve_erreur(self):
        garde, efface = self._envoyee(), self._envoyee()
        protocole.ecrire_json(seances.dossier_audio(self.donnees, garde) / protocole.ERREUR, {'code': 'trop_long'})
        protocole.ecrire_json(seances.dossier_audio(self.donnees, efface) / protocole.ERREUR, {'code': 'illisible'})
        self.assertEqual(seances.relever(self.base, self.donnees), 2)
        for ident, code, audio in ((garde, 'trop_long', True), (efface, 'illisible', False)):
            lue = seances.lire(self.base, ident, 'alice')
            self.assertEqual((lue['etat'], lue['erreur'], lue['audio']), ('echec', code, audio))
        self.assertFalse(seances.dossier_audio(self.donnees, efface).exists())

    def test_releve_resultat_illisible_ignore(self):
        ident = self._envoyee()
        (seances.dossier_audio(self.donnees, ident) / protocole.RESULTAT).write_text('{tronqué', encoding='utf-8')
        self.assertEqual(seances.relever(self.base, self.donnees), 0)
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'attente')


if __name__ == '__main__':
    unittest.main()


class RelectureTests(Base):
    SEGMENTS = [{'debut': 0.0, 'fin': 2.0, 'intervenant': 'Intervenant 1', 'texte': 'La séance est ouverte.'},
                {'debut': 2.5, 'fin': 5.0, 'intervenant': 'Intervenant 1', 'texte': 'Premier point.'},
                {'debut': 3725.0, 'fin': 3728.0, 'intervenant': 'Intervenant 2', 'texte': 'Merci.'},
                {'debut': 3730.0, 'fin': 3733.0, 'intervenant': 'Intervenant 3', 'texte': 'Je vote pour.'}]

    def _a_relire(self, segments=None):
        ident = self._envoyee()
        protocole.ecrire_json(seances.dossier_audio(self.donnees, ident) / protocole.RESULTAT,
                              {'version': 1, 'duree_s': 3733.0, 'modele': 'large-v3-turbo', 'duree_traitement_s': 1.0,
                               'segments': self.SEGMENTS if segments is None else segments})
        seances.relever(self.base, self.donnees)
        return ident

    def _en_echec(self, code):
        ident = self._envoyee()
        protocole.ecrire_json(seances.dossier_audio(self.donnees, ident) / protocole.ERREUR, {'code': code})
        seances.relever(self.base, self.donnees)
        return ident

    def test_nommer_une_fois_pour_toute_la_seance(self):
        ident = self._a_relire()
        seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 1', 'M. le Maire')
        texte = seances.texte_integral(self.base, ident, 'alice', heures=False)
        self.assertEqual(texte.split('\n\n'), ['M. le Maire : La séance est ouverte. Premier point.', 'Intervenant 2 : Merci.',
                                               'Intervenant 3 : Je vote pour.'])
        for nom in ('', 'x' * 81, 'M. le\x07Maire'):
            with self.subTest(nom=nom), self.assertRaises(ValueError):
                seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 2', nom)
        with self.assertRaises(LookupError):
            seances.nommer_intervenant(self.base, ident, 'bob', 'Intervenant 2', 'Mme Durand')

    def test_nom_en_double_refuse(self):
        ident = self._a_relire()
        seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 1', 'M. le Maire')
        with self.assertRaisesRegex(ValueError, 'fusionnez'):
            seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 2', 'M. le Maire')
        seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 1', 'M. le Maire')  # renommer à l'identique reste permis

    def test_fusionner(self):
        ident = self._a_relire()
        seances.fusionner_intervenants(self.base, ident, 'alice', 'Intervenant 3', 'Intervenant 2')
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual([i['etiquette'] for i in lue['intervenants']], ['Intervenant 1', 'Intervenant 2'])
        self.assertEqual([s['etiquette'] for s in lue['segments']], ['Intervenant 1', 'Intervenant 1', 'Intervenant 2', 'Intervenant 2'])
        with self.assertRaises(ValueError):
            seances.fusionner_intervenants(self.base, ident, 'alice', 'Intervenant 2', 'Intervenant 2')
        with self.assertRaises(ValueError):
            seances.fusionner_intervenants(self.base, ident, 'alice', 'Intervenant 9', 'Intervenant 1')

    def test_corriger_un_passage(self):
        ident = self._a_relire()
        segment = seances.lire(self.base, ident, 'alice')['segments'][2]
        seances.modifier_segment(self.base, ident, 'alice', segment['id'], texte='Merci, Monsieur le Maire.', etiquette='Intervenant 1')
        corrige = next(s for s in seances.lire(self.base, ident, 'alice')['segments'] if s['id'] == segment['id'])
        self.assertEqual((corrige['texte'], corrige['etiquette'], corrige['corrige']), ('Merci, Monsieur le Maire.', 'Intervenant 1', True))
        for changement in ({'etiquette': 'Intervenant 9'}, {'texte': ''}, {'texte': 'x' * 5001}):
            with self.subTest(changement=changement), self.assertRaises(ValueError):
                seances.modifier_segment(self.base, ident, 'alice', segment['id'], **changement)
        autre = self._a_relire()
        with self.assertRaises(LookupError):  # un passage d'une autre séance
            seances.modifier_segment(self.base, autre, 'alice', segment['id'], texte='x')

    def test_valider_efface_l_audio(self):
        ident = self._a_relire()
        seances.valider(self.base, self.donnees, ident, 'alice')
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['audio']), ('validee', False))
        self.assertFalse(seances.dossier_audio(self.donnees, ident).exists())
        with self.assertRaises(ValueError):
            seances.valider(self.base, self.donnees, ident, 'alice')
        with self.assertRaises(ValueError):
            seances.valider(self.base, self.donnees, self._envoyee(), 'alice')

    def test_reessayer(self):
        memoire = self._en_echec('memoire')
        dossier = seances.dossier_audio(self.donnees, memoire)
        seances.reessayer(self.base, self.donnees, memoire, 'alice')
        self.assertEqual(seances.lire(self.base, memoire, 'alice')['etat'], 'attente')
        self.assertFalse((dossier / protocole.ERREUR).exists())
        self.assertEqual(protocole.lire_json(dossier / protocole.DEMANDE)['source'], 'source.mp3')
        illisible = self._en_echec('illisible')
        seances.reessayer(self.base, self.donnees, illisible, 'alice')
        lue = seances.lire(self.base, illisible, 'alice')
        self.assertEqual((lue['etat'], lue['erreur'], lue['audio']), ('creee', None, False))
        with self.assertRaises(ValueError):
            seances.reessayer(self.base, self.donnees, memoire, 'alice')  # plus en échec

    def test_supprimer_efface_tout(self):
        ident, autre = self._a_relire(), self._a_relire()
        seances.supprimer(self.base, self.donnees, ident, 'alice')
        with self.assertRaises(LookupError):
            seances.lire(self.base, ident, 'alice')
        from territoria.base.connexion import ouvrir
        with ouvrir(self.base) as c:
            for table in ('seance_intervenants', 'seance_segments'):
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table} WHERE seance_id=?', (ident,)).fetchone()[0], 0)
        self.assertFalse(seances.dossier_audio(self.donnees, ident).exists())
        self.assertEqual(len(seances.lire(self.base, autre, 'alice')['segments']), 4)
        with self.assertRaises(LookupError):
            seances.supprimer(self.base, self.donnees, autre, 'bob')

    def test_purger_abandons_apres_7_jours(self):
        from datetime import datetime, timedelta, timezone
        vieille, recente = self._seance(), self._seance()
        for ident in (vieille, recente):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'audio', 'webm')
        maintenant = datetime.now(timezone.utc) + timedelta(days=8)
        envoi = seances.dossier_audio(self.donnees, recente) / seances.ENVOI
        protocole.ecrire_json(envoi, dict(protocole.lire_json(envoi), maj=(maintenant - timedelta(days=6)).isoformat(timespec='seconds')))
        self.assertEqual(seances.purger_abandons(self.base, self.donnees, maintenant), 1)
        self.assertEqual((seances.lire(self.base, vieille, 'alice')['etat'], seances.lire(self.base, vieille, 'alice')['audio']), ('creee', False))
        self.assertEqual(seances.lire(self.base, recente, 'alice')['etat'], 'reception')

    def test_texte_integral_avec_et_sans_heures(self):
        ident = self._a_relire()
        self.assertEqual(seances.texte_integral(self.base, ident, 'alice', heures=True).split('\n\n'),
                         ['[00:00:00] Intervenant 1 : La séance est ouverte. Premier point.', '[01:02:05] Intervenant 2 : Merci.',
                          '[01:02:10] Intervenant 3 : Je vote pour.'])
        vide = self._a_relire(segments=[])
        self.assertEqual(seances.texte_integral(self.base, vide, 'alice', heures=False), 'Aucune parole détectée.')

    def test_word_integral_sur_papier_a_en_tete(self):
        import branding
        from docx import Document
        from tests.test_branding import papier_a_en_tete
        ident = self._a_relire()
        seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 1', 'M. le Maire')
        branding.upload_gabarit(self.donnees, papier_a_en_tete())
        chemin = seances.word_integral(self.base, self.donnees, ident, 'alice', heures=True)
        self.assertEqual(chemin, self.donnees / 'seances' / ident / 'transcription.docx')
        doc = Document(str(chemin))
        self.assertEqual(doc.sections[0].header.paragraphs[0].text, 'Mairie de Testville — 1 place de la Mairie')
        texte = '\n'.join(p.text for p in doc.paragraphs)
        for attendu in ('Conseil municipal du 3 novembre', 'Mairie', 'M. le Maire', '[00:00:00] M. le Maire : La séance est ouverte. Premier point.'):
            self.assertIn(attendu, texte)


class SaisieTests(Base):
    def test_details_et_points_enregistres_puis_relus(self):
        ident = self._validee()
        self.assertEqual(seances.enregistrer_details(self.base, ident, 'alice', dict(DETAILS)), DETAILS)
        points = seances.enregistrer_points(self.base, ident, 'alice', POINTS)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual(lue['details'], DETAILS)
        self.assertEqual([(p['rang'], p['titre'], p['deliberation_numero'], p['rapporteur'], p['vote'], p['scrutin_particulier'],
                           p['debut'], p['teneur'], p['erreur']) for p in lue['points']],
                         [(1, 'Budget primitif 2027', 'D2026-052', 'Mme Durand', POINTS[0]['vote'], False, 5.0, None, None),
                          (2, 'Tarifs de la cantine scolaire', None, None, {'mode': 'unanimite'}, True, None, None, None)])
        self.assertEqual(points, lue['points'])
        self.assertEqual(lue['redaction'], {})
        with self.assertRaises(LookupError):
            seances.enregistrer_details(self.base, ident, 'bob', dict(DETAILS))
        with self.assertRaises(LookupError):
            seances.enregistrer_points(self.base, ident, 'bob', POINTS)

    def test_cle_de_details_inconnue_refusee(self):
        ident = self._validee()
        for details in ({'maire': 'M. Martin'}, {'membres_en_exercice': '19'}, {'membres_en_exercice': 0},
                        {'heure_cloture': '25:00'}, {'arrete_le': '15/12/2026'}, {'presents': 'M. Martin'},
                        {'representes': [{'mandant': 'M. Leroy'}]}, {'president': 'M.\x07Martin'}):
            with self.subTest(details=details), self.assertRaises(ValueError):
                seances.enregistrer_details(self.base, ident, 'alice', details)

    def test_trop_de_voix_refusees(self):
        ident = self._validee()
        seances.enregistrer_details(self.base, ident, 'alice', dict(DETAILS))
        trop = dict(POINTS[0], vote={'mode': 'decompte', 'pour': 15, 'contre': 4, 'abstentions': 1})
        with self.assertRaisesRegex(ValueError, 'Plus de voix que de membres en exercice'):
            seances.enregistrer_points(self.base, ident, 'alice', [trop])
        for vote in ({'mode': 'decompte', 'pour': -1, 'contre': 0, 'abstentions': 0}, {'mode': 'decompte', 'pour': 0, 'contre': 0, 'abstentions': 0},
                     {'mode': 'autre'}, {'mode': 'decompte', 'pour': True, 'contre': 0, 'abstentions': 0}):
            with self.subTest(vote=vote), self.assertRaises(ValueError):
                seances.enregistrer_points(self.base, ident, 'alice', [dict(POINTS[0], vote=vote)])
        with self.assertRaises(ValueError):
            seances.enregistrer_points(self.base, ident, 'alice', [{'titre': ''}])

    def test_saisie_refusee_pour_une_reunion(self):
        ident = self._validee(type_='reunion')
        with self.assertRaises(ValueError):
            seances.enregistrer_details(self.base, ident, 'alice', dict(DETAILS))
        with self.assertRaises(ValueError):
            seances.enregistrer_points(self.base, ident, 'alice', POINTS)

    def test_saisie_refusee_avant_validation(self):
        ident = self._validee(valider=False)
        with self.assertRaises(ValueError):
            seances.enregistrer_details(self.base, ident, 'alice', dict(DETAILS))
        with self.assertRaises(ValueError):
            seances.enregistrer_points(self.base, ident, 'alice', POINTS)

    def test_teneur_gardee_par_rang(self):
        # La teneur suit le rang du point ; son empreinte de passages dit si elle est encore à jour (tâche PV à jour).
        ident = self._validee()
        seances.enregistrer_points(self.base, ident, 'alice', POINTS)
        with ouvrir(self.base) as c:
            c.execute('UPDATE seance_points SET teneur=? WHERE seance_id=?', (json.dumps(['Teneur rédigée.']), ident))
        modifies = [POINTS[0], dict(POINTS[1], titre='Tarifs de la restauration scolaire')]
        points = seances.enregistrer_points(self.base, ident, 'alice', modifies)
        self.assertEqual([p['teneur'] for p in points], [['Teneur rédigée.'], ['Teneur rédigée.']])
        self.assertEqual([p['a_jour'] for p in points], [False, False])  # aucune empreinte : rien n'est garanti

    def test_voix_reverifiees_quand_les_membres_changent(self):
        # Relecture finale : enregistrer les mentions après les votes contournait le contrôle du nombre de voix.
        ident = self._validee()
        seances.enregistrer_points(self.base, ident, 'alice', [POINTS[0]])  # 18 voix, membres non saisis
        with self.assertRaisesRegex(ValueError, 'Plus de voix que de membres en exercice'):
            seances.enregistrer_details(self.base, ident, 'alice', dict(DETAILS, membres_en_exercice=17))

    def test_rapport_du_point(self):
        ident = self._validee()
        points = seances.enregistrer_points(self.base, ident, 'alice', [dict(POINTS[0], rapport='Note de synthèse sur le budget')])
        self.assertEqual(points[0]['rapport'], 'Note de synthèse sur le budget')


class OrdreDuJourTests(Base):
    def test_ordre_du_jour_propose_avec_debuts(self):
        from territoria import registre
        ident = self._validee()
        (self.donnees / 'arretes').mkdir()
        acte = 'a' * 32
        numero = registre.attribuer(self.base, acte, 'deliberation', 'Budget primitif 2027')
        (self.donnees / 'arretes' / f'{acte}.json').write_text(json.dumps(
            {'document': {'kind': 'deliberation', 'preambule': ['Extrait', 'Séance du 3 novembre 2026']}}), encoding='utf-8')
        self.assertEqual(seances.proposer_ordre_du_jour(self.base, self.donnees, ident, 'alice'),
                         [{'rang': 1, 'titre': 'Budget primitif 2027', 'deliberation_numero': numero, 'debut': 5.0}])
        with self.assertRaises(LookupError):
            seances.proposer_ordre_du_jour(self.base, self.donnees, ident, 'bob')


def completion(contenu):
    return {'choices': [{'message': {'content': json.dumps(contenu, ensure_ascii=False)}}]}


TENEUR_1 = {'paragraphes': ['Intervenant 1 présente le budget primitif 2027 et donne la parole à Mme Durand.']}
TENEUR_2 = {'paragraphes': ['Intervenant 2 indique que les tarifs restent inchangés.']}
INUTILISABLE = {'autre': 1}
COMPTE_RENDU = {'points': [{'titre': 'Budget', 'synthese': 'Le budget s’équilibre à 3 000 000 euros.'}],
                'decisions': ['Le budget est présenté.'], 'actions': []}


class AvecModele(Base):
    """Faux modèle de rédaction et séance de conseil prête à rédiger (pas de test propre)."""

    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(llm_backend, 'require_configured', return_value={'model': 'gemma'}).start()

    def modele(self, *reponses):
        return mock.patch.object(llm_backend, 'chat', side_effect=list(reponses))

    def _pv_demande(self, points=None):
        ident = self._validee()
        seances.enregistrer_details(self.base, ident, 'alice', dict(DETAILS))
        seances.enregistrer_points(self.base, ident, 'alice', points or [POINTS[0], dict(POINTS[1], debut=60.0)])
        seances.demander_redaction(self.base, ident, 'alice', 'proces_verbal')
        return ident


class RedactionTests(AvecModele):
    def test_pv_redige_point_par_point_puis_prete(self):
        ident = self._pv_demande()
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'redaction')
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)):
            self.assertEqual(seances.rediger_en_attente(self.base, self.donnees), 1)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['progression'], lue['erreur']), ('prete', None, None))
        self.assertEqual([p['teneur'] for p in lue['points']], [TENEUR_1['paragraphes'], TENEUR_2['paragraphes']])
        self.assertEqual((lue['redaction']['document'], lue['redaction']['proces_verbal']), ('proces_verbal', True))
        self.assertEqual(seances.rediger_en_attente(self.base, self.donnees), 0)

    def test_progression_point_par_point(self):
        ident = self._pv_demande()
        vues = []

        def repondre(donnees, requete):
            vues.append(seances.lire(self.base, ident, 'alice')['progression'])
            return completion(TENEUR_1)
        with mock.patch.object(llm_backend, 'chat', side_effect=repondre):
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(vues, ['redaction 0 2', 'redaction 1 2'])

    def test_point_sans_passages_sans_appel(self):
        ident = self._pv_demande(points=POINTS)  # le second point n'a pas de début
        with self.modele(completion(TENEUR_1)) as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(seances.lire(self.base, ident, 'alice')['points'][1]['teneur'], ['[à compléter]'])

    def test_echec_d_un_point_puis_reessayer_ne_refait_que_lui(self):
        ident = self._pv_demande()
        with self.modele(completion(TENEUR_1), completion(INUTILISABLE), completion(INUTILISABLE)):
            seances.rediger_en_attente(self.base, self.donnees)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['erreur']), ('echec', 'redaction'))
        self.assertEqual([(p['teneur'], p['erreur']) for p in lue['points']], [(TENEUR_1['paragraphes'], None), (None, 'reponse')])
        seances.reessayer(self.base, self.donnees, ident, 'alice')
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'redaction')
        with self.modele(completion(TENEUR_2)) as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(chat.call_count, 1)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], [p['teneur'] for p in lue['points']]), ('prete', [TENEUR_1['paragraphes'], TENEUR_2['paragraphes']]))

    def test_reessayer_apres_redaction_garde_la_transcription(self):
        ident = self._pv_demande()
        with self.modele(RuntimeError('Serveur IA injoignable.'), RuntimeError('Serveur IA injoignable.')):
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(seances.lire(self.base, ident, 'alice')['erreur'], 'redaction')
        seances.reessayer(self.base, self.donnees, ident, 'alice')
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], len(lue['segments'])), ('redaction', len(SEGMENTS_PV)))
        self.assertEqual([p['erreur'] for p in lue['points']], ['modele', 'modele'])

    def test_erreur_inattendue_marque_l_echec(self):
        # Relecture finale : une exception imprévue laissait la séance en rédaction pour toujours et bloquait la file.
        ident = self._pv_demande()
        with mock.patch.object(llm_backend, 'chat', side_effect=KeyError('inattendu')):
            self.assertEqual(seances.rediger_en_attente(self.base, self.donnees), 1)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['erreur'], lue['progression']), ('echec', 'redaction', None))
        self.assertEqual(seances.rediger_en_attente(self.base, self.donnees), 0)

    def test_apres_echec_de_redaction_tout_reste_possible(self):
        # Relecture finale : en échec de rédaction, ni nouvelle demande, ni relecture n'étaient permises (état sans issue).
        ident = self._pv_demande()
        with self.modele(RuntimeError('Serveur IA injoignable.'), RuntimeError('Serveur IA injoignable.')):
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(seances.lire(self.base, ident, 'alice')['erreur'], 'redaction')
        seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 1', 'M. le Maire')
        seances.demander_redaction(self.base, ident, 'alice', 'compte_rendu')
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'redaction')

    def _point_redige(self, ident, rang, teneur):
        """Point rédigé avant un arrêt du serveur : teneur et empreinte de ses passages actuels."""
        lue = seances.lire(self.base, ident, 'alice')
        passages = seances_pv.passages_par_point(lue['segments'], lue['points'], {i['etiquette']: i['nom'] for i in lue['intervenants']})
        with ouvrir(self.base) as c:
            c.execute('UPDATE seance_points SET teneur=?, empreinte=? WHERE seance_id=? AND rang=?',
                      (json.dumps(teneur), seances_pv.empreinte(passages[rang - 1]), ident, rang))

    def test_reprise_apres_redemarrage_sans_refaire_les_points(self):
        ident = self._pv_demande()
        self._point_redige(ident, 1, TENEUR_1['paragraphes'])
        with self.modele(completion(TENEUR_2)) as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(seances.lire(self.base, ident, 'alice')['etat'], 'prete')

    def test_progression_remise_a_jour_a_la_reprise(self):
        # Recette : après un redémarrage, la barre gardait l'ancienne progression jusqu'à la fin du premier point repris.
        ident = self._pv_demande()
        self._point_redige(ident, 1, TENEUR_1['paragraphes'])
        with ouvrir(self.base) as c:
            c.execute("UPDATE seances SET progression='redaction 2 2' WHERE id=?", (ident,))
        vues = []

        def repondre(donnees, requete):
            vues.append(seances.lire(self.base, ident, 'alice')['progression'])
            return completion(TENEUR_2)
        with mock.patch.object(llm_backend, 'chat', side_effect=repondre):
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual(vues, ['redaction 1 2'])

    def test_seance_supprimee_pendant_la_redaction(self):
        ident = self._pv_demande()

        def supprimer_puis_repondre(donnees, requete):
            seances.supprimer(self.base, self.donnees, ident, 'alice')
            return completion(TENEUR_1)
        with mock.patch.object(llm_backend, 'chat', side_effect=supprimer_puis_repondre):
            self.assertEqual(seances.rediger_en_attente(self.base, self.donnees), 1)
        with ouvrir(self.base) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM seance_points').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM seances').fetchone()[0], 0)

    def test_pv_refuse_pour_une_reunion(self):
        ident = self._validee(type_='reunion')
        with self.assertRaises(ValueError):
            seances.demander_redaction(self.base, ident, 'alice', 'proces_verbal')
        seances.demander_redaction(self.base, ident, 'alice', 'compte_rendu')

    def test_demande_refusee(self):
        en_relecture = self._validee(valider=False)
        with self.assertRaises(ValueError):
            seances.demander_redaction(self.base, en_relecture, 'alice', 'compte_rendu')
        sans_points = self._validee()
        with self.assertRaises(ValueError):
            seances.demander_redaction(self.base, sans_points, 'alice', 'proces_verbal')
        with self.assertRaises(ValueError):
            seances.demander_redaction(self.base, sans_points, 'alice', 'autre')
        with self.assertRaises(LookupError):
            seances.demander_redaction(self.base, sans_points, 'bob', 'compte_rendu')
        seances.demander_redaction(self.base, sans_points, 'alice', 'compte_rendu')
        with self.assertRaises(ValueError):  # déjà en cours
            seances.demander_redaction(self.base, sans_points, 'alice', 'compte_rendu')

    def test_compte_rendu_redige(self):
        ident = self._validee(type_='reunion')
        seances.demander_redaction(self.base, ident, 'alice', 'compte_rendu')
        with self.modele(completion(COMPTE_RENDU)) as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['etat'], lue['redaction']['compte_rendu']), ('prete', COMPTE_RENDU))
        passages = json.loads(chat.call_args[0][1]['messages'][1]['content'])['passages']
        self.assertEqual(len(passages), len(SEGMENTS_PV))

    def test_renommage_repris_a_la_redaction_suivante(self):
        ident = self._pv_demande()
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)):
            seances.rediger_en_attente(self.base, self.donnees)
        seances.nommer_intervenant(self.base, ident, 'alice', 'Intervenant 1', 'M. le Maire')  # permis une fois le PV prêt
        seances.demander_redaction(self.base, ident, 'alice', 'proces_verbal')
        self.assertEqual([p['a_jour'] for p in seances.lire(self.base, ident, 'alice')['points']], [False, False])
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)) as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertIn('M. le Maire : Point numéro 1', chat.call_args_list[0][0][1]['messages'][1]['content'])

    def _pv_redige(self):
        ident = self._pv_demande()
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)):
            seances.rediger_en_attente(self.base, self.donnees)
        return ident

    def test_titre_corrige_garde_le_pv_a_jour(self):
        # Relecture finale : corriger un titre vidait la teneur, et le PV restait servi avec [à compléter].
        ident = self._pv_redige()
        seances.enregistrer_points(self.base, ident, 'alice', [dict(POINTS[0], titre='Budget primitif 2027 (corrigé)'),
                                                                dict(POINTS[1], debut=60.0)])
        lue = seances.lire(self.base, ident, 'alice')
        self.assertTrue(lue['pv_a_jour'])
        seances.word_document(self.base, self.donnees, ident, 'alice', 'proces_verbal')

    def test_debut_deplace_rend_le_pv_perime(self):
        ident = self._pv_redige()
        seances.enregistrer_points(self.base, ident, 'alice', [POINTS[0], dict(POINTS[1], debut=41.0)])
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((lue['pv_a_jour'], [p['a_jour'] for p in lue['points']]), (False, [False, False]))
        with self.assertRaisesRegex(ValueError, "n'est plus à jour"):
            seances.word_document(self.base, self.donnees, ident, 'alice', 'proces_verbal')
        seances.demander_redaction(self.base, ident, 'alice', 'proces_verbal')
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)) as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        self.assertEqual((chat.call_count, seances.lire(self.base, ident, 'alice')['pv_a_jour']), (2, True))

    def test_nouvelle_demande_ne_refait_que_les_points_perimes(self):
        ident = self._pv_redige()
        seances.demander_redaction(self.base, ident, 'alice', 'proces_verbal')
        with self.modele() as chat:
            seances.rediger_en_attente(self.base, self.donnees)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual((chat.call_count, lue['etat'], lue['pv_a_jour']), (0, 'prete', True))

    def test_une_seance_a_la_fois(self):
        premiere, seconde = self._validee(), self._validee()
        for ident in (premiere, seconde):
            seances.demander_redaction(self.base, ident, 'alice', 'compte_rendu')
        with self.modele(completion(COMPTE_RENDU)):
            self.assertEqual(seances.rediger_en_attente(self.base, self.donnees), 1)
        etats = {seances.lire(self.base, i, 'alice')['etat'] for i in (premiere, seconde)}
        self.assertEqual(etats, {'prete', 'redaction'})

    def test_teneur_sans_nom_ni_nombre_inventes(self):
        ident = self._pv_demande(points=[POINTS[0]])
        with self.modele(completion({'paragraphes': ['Mme Martin propose 4 000 euros.']})):
            seances.rediger_en_attente(self.base, self.donnees)
        lue = seances.lire(self.base, ident, 'alice')
        self.assertEqual(lue['points'][0]['teneur'], ['[à compléter] propose [à compléter] euros.'])
        self.assertEqual(len(lue['redaction']['vigilances']['proces_verbal']), 2)

    def test_journal_seance_redigee_sans_contenu(self):
        ident = self._pv_demande()
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)):
            seances.rediger_en_attente(self.base, self.donnees)
        ligne = administration.activite(self.base, 5)[0]
        self.assertEqual((ligne['action'], ligne['objet'], ligne['detail']), ('seance_redigee', 'conseil', 'proces_verbal'))
        journal = json.dumps(administration.activite(self.base, 50), ensure_ascii=False)
        for secret in (ident, SEANCE['titre'], 'Budget primitif', 'Mme Durand', 'présente le budget'):
            self.assertNotIn(secret, journal)


class DocumentsTests(AvecModele):
    def _pv_pret(self):
        ident = self._pv_demande()
        with self.modele(completion(TENEUR_1), completion(TENEUR_2)):
            seances.rediger_en_attente(self.base, self.donnees)
        return ident

    def test_word_sur_papier_a_en_tete(self):
        import branding
        from docx import Document
        from tests.test_branding import papier_a_en_tete
        ident = self._pv_pret()
        branding.upload_gabarit(self.donnees, papier_a_en_tete())
        chemin = seances.word_document(self.base, self.donnees, ident, 'alice', 'proces_verbal')
        self.assertEqual(chemin, self.donnees / 'seances' / ident / 'proces-verbal.docx')
        doc = Document(str(chemin))
        self.assertEqual(doc.sections[0].header.paragraphs[0].text, 'Mairie de Testville — 1 place de la Mairie')
        texte = '\n'.join(p.text for p in doc.paragraphs)
        for attendu in ('PROCÈS-VERBAL', 'Procès-verbal de la séance du conseil municipal du 3 novembre 2026', 'M. Martin',
                        TENEUR_1['paragraphes'][0], 'Adopté par 15 voix pour, 2 contre et 1 abstention.'):
            self.assertIn(attendu, texte)
        liste = seances.word_document(self.base, self.donnees, ident, 'alice', 'liste_deliberations')
        self.assertIn('n° D2026-052 — Budget primitif 2027', '\n'.join(p.text for p in Document(str(liste)).paragraphs))

    def test_mention_demonstration_dans_le_pv(self):
        from dataclasses import replace
        from docx import Document
        import arrete_document
        import config
        ident = self._pv_pret()
        with mock.patch.object(arrete_document, 'REGLAGES', replace(config.charger({}), demonstration=True)):
            chemin = seances.word_document(self.base, self.donnees, ident, 'alice', 'proces_verbal')
        pied = '\n'.join(p.text for p in Document(str(chemin)).sections[-1].footer.paragraphs)
        self.assertIn('Document de démonstration — sans valeur juridique', pied)

    def test_fichiers_prives(self):
        import stat
        ident = self._pv_pret()
        for document, nom in (('proces_verbal', 'proces-verbal.docx'), ('liste_deliberations', 'liste-deliberations.docx')):
            chemin = seances.word_document(self.base, self.donnees, ident, 'alice', document)
            self.assertEqual(chemin.name, nom)
            self.assertEqual(stat.S_IMODE(chemin.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(chemin.parent.stat().st_mode), 0o700)

    def test_compte_rendu_en_word(self):
        from docx import Document
        ident = self._validee(type_='reunion')
        seances.demander_redaction(self.base, ident, 'alice', 'compte_rendu')
        with self.modele(completion(COMPTE_RENDU)):
            seances.rediger_en_attente(self.base, self.donnees)
        chemin = seances.word_document(self.base, self.donnees, ident, 'alice', 'compte_rendu')
        self.assertEqual(chemin.name, 'compte-rendu.docx')
        self.assertIn('Budget — Le budget s’équilibre à 3 000 000 euros.', '\n'.join(p.text for p in Document(str(chemin)).paragraphs))

    def test_document_refuse_avant_redaction(self):
        ident = self._validee()
        for document in ('compte_rendu', 'proces_verbal', 'liste_deliberations', 'autre'):
            with self.subTest(document=document), self.assertRaises(ValueError):
                seances.word_document(self.base, self.donnees, ident, 'alice', document)
        pret = self._pv_pret()
        with self.assertRaises(ValueError):  # pas de compte rendu rédigé pour cette séance
            seances.word_document(self.base, self.donnees, pret, 'alice', 'compte_rendu')
        with self.assertRaises(LookupError):
            seances.word_document(self.base, self.donnees, pret, 'bob', 'proces_verbal')


class FichiersDuConteneurTests(Base):
    """Le dossier audio est aussi écrit par le conteneur de travaux, qui décode des fichiers non fiables : Territoria ne
    suit aucun lien et ne lit que des fichiers ordinaires de taille bornée."""

    def setUp(self):
        super().setUp()
        self.secret = self.dossier / 'territoire.sqlite3'
        self.secret.write_bytes(b'BASE CONFIDENTIELLE')

    def test_source_remplacee_par_un_lien_ni_lue_ni_ecrasee(self):
        ident = self._seance()
        seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'debut', 'webm')
        source = seances.dossier_audio(self.donnees, ident) / 'source.webm'
        source.unlink()
        source.symlink_to(self.secret)
        with self.assertRaises(LookupError):
            seances.source_audio(self.base, self.donnees, ident, 'alice')
        with self.assertRaises(ValueError):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 1, b'suite', 'webm')
        self.assertEqual(self.secret.read_bytes(), b'BASE CONFIDENTIELLE')

    def test_dossier_de_seance_remplace_par_un_lien_ignore(self):
        ident = self._envoyee()
        dossier = seances.dossier_audio(self.donnees, ident)
        leurre = self.dossier / 'leurre'
        leurre.mkdir()
        (leurre / 'source.mp3').write_bytes(b'x')
        protocole.ecrire_json(leurre / protocole.RESULTAT, {'version': 1, 'duree_s': 1.0, 'segments': []})
        shutil.rmtree(dossier)
        dossier.symlink_to(leurre, target_is_directory=True)
        self.assertEqual(seances.relever(self.base, self.donnees), 0)
        self.assertFalse(seances.lire(self.base, ident, 'alice')['audio'])
        with self.assertRaises(LookupError):
            seances.source_audio(self.base, self.donnees, ident, 'alice')

    def test_lire_json_refuse_lien_tube_et_fichier_trop_gros(self):
        import os
        (self.dossier / 'lien.json').symlink_to(self.secret)
        os.mkfifo(self.dossier / 'tube.json')
        (self.dossier / 'gros.json').write_text('{"a": "' + 'x' * 70000 + '"}', encoding='utf-8')
        for nom in ('lien.json', 'tube.json', 'gros.json'):
            with self.subTest(nom=nom):
                self.assertIsNone(protocole.lire_json(self.dossier / nom))
        self.assertIsNotNone(protocole.lire_json(self.dossier / 'gros.json', taille_max=100000))

    def test_morceau_ecrit_en_entier_meme_par_ecritures_partielles(self):
        import os
        ident = self._seance()
        vrai = os.write
        with mock.patch.object(seances.os, 'write', side_effect=lambda fd, octets: vrai(fd, octets[:3])):
            seances.ajouter_morceau(self.base, self.donnees, ident, 'alice', 0, b'abcdefghij', 'webm')
        self.assertEqual((seances.dossier_audio(self.donnees, ident) / 'source.webm').read_bytes(), b'abcdefghij')

    def test_temps_restant_releve(self):
        ident = self._envoyee()
        protocole.ecrire_json(seances.dossier_audio(self.donnees, ident) / protocole.PROGRES,
                              {'etape': 'intervenants', 'pourcentage': 30, 'maj': 'x', 'reste_s': 1234})
        seances.relever(self.base, self.donnees)
        self.assertEqual(seances.lire(self.base, ident, 'alice')['progression'], 'intervenants 30 1234')
