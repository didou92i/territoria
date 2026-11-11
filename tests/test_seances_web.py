# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import llm_backend  # noqa: E402
import server  # noqa: E402
from territoria import administration, seances  # noqa: E402
from tests.test_comptes import NAVIGATEUR, Base  # noqa: E402
from travaux import protocole  # noqa: E402

OCTETS = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
TITRE = 'Conseil très confidentiel du 3 novembre'
NOM = 'Mme Arlette Confidentielle'
POINT = 'Point budgétaire très confidentiel'
TENEUR = 'Teneur très confidentielle du débat.'


class SeancesWebTests(Base):
    def _creer(self, jeton=None):
        statut, corps = self.appel('POST', '/api/seances', {'type': 'conseil', 'titre': TITRE, 'date': '2026-11-03', 'lieu': 'Mairie'}, jeton)
        self.assertEqual(statut, 201, corps)
        return corps['id']

    def _envoyer(self, ident, jeton=None, morceaux=(b'0123456789' * 2, b'abcdefghij' * 2)):
        for rang, morceau in enumerate(morceaux):
            statut, corps = self.appel('POST', f'/api/seances/{ident}/audio?rang={rang}&format=mp3', morceau, jeton, OCTETS)
            self.assertEqual((statut, corps), (200, {'rang_suivant': rang + 1}))

    def _resultat(self, ident):
        protocole.ecrire_json(server.DATA / 'audio' / ident / protocole.RESULTAT, {
            'version': 1, 'duree_s': 12.0, 'modele': 'large-v3-turbo', 'duree_traitement_s': 4.0,
            'segments': [{'debut': 0.0, 'fin': 3.0, 'intervenant': 'Intervenant 1', 'texte': 'La séance est ouverte.'},
                         {'debut': 4.0, 'fin': 6.0, 'intervenant': 'Intervenant 2', 'texte': 'Merci.'}]})

    def test_parcours_import_releve_relecture_validation_export(self):
        ident = self._creer()
        statut, liste = self.appel('GET', '/api/seances')
        self.assertEqual((statut, [s['id'] for s in liste['seances']]), (200, [ident]))
        self._envoyer(ident)
        statut, _ = self.appel('POST', f'/api/seances/{ident}/audio/fin', {})
        self.assertEqual(statut, 200)
        demande = protocole.lire_json(server.DATA / 'audio' / ident / protocole.DEMANDE)
        self.assertEqual((demande['source'], demande['modele']), ('source.mp3', 'large-v3-turbo'))
        self._resultat(ident)
        statut, seance = self.appel('GET', f'/api/seances/{ident}')
        self.assertEqual((statut, seance['etat'], len(seance['segments']), seance['audio']), (200, 'relecture', 2, True))
        statut, _ = self.appel('POST', f'/api/seances/{ident}/intervenants', {'etiquette': 'Intervenant 1', 'nom': NOM})
        self.assertEqual(statut, 200)
        segment = seance['segments'][1]['id']
        statut, _ = self.appel('POST', f'/api/seances/{ident}/segments/{segment}', {'texte': 'Merci beaucoup.'})
        self.assertEqual(statut, 200)
        statut, corps = self.appel('GET', f'/api/seances/{ident}/audio')
        self.assertEqual((statut, corps), (200, b'0123456789' * 2 + b'abcdefghij' * 2))
        self.assertTrue(dict(self.derniere.headers)['Content-Type'].startswith('audio/'))
        statut, _ = self.appel('POST', f'/api/seances/{ident}/valider', {})
        self.assertEqual(statut, 200)
        self.assertEqual(self.appel('GET', f'/api/seances/{ident}/audio')[0], 404)
        statut, texte = self.appel('GET', f'/api/seances/{ident}/export?format=txt&heures=0')
        self.assertEqual(statut, 200)
        self.assertIn(f'{NOM} : La séance est ouverte.', texte.decode('utf-8'))
        self.assertIn('Merci beaucoup.', texte.decode('utf-8'))
        self.assertIn('attachment; filename="transcription.txt"', dict(self.derniere.headers)['Content-Disposition'])
        statut, word = self.appel('GET', f'/api/seances/{ident}/export?format=docx&heures=1')
        self.assertEqual((statut, word[:2]), (200, b'PK'))
        statut, _ = self.appel('DELETE', f'/api/seances/{ident}')
        self.assertEqual(statut, 200)
        self.assertEqual(self.appel('GET', f'/api/seances/{ident}')[0], 404)

    def test_morceau_en_avance_409(self):
        ident = self._creer()
        self._envoyer(ident, morceaux=(b'debut',))
        statut, corps = self.appel('POST', f'/api/seances/{ident}/audio?rang=4&format=mp3', b'suite', None, OCTETS)
        self.assertEqual((statut, corps['rang_suivant']), (409, 1))
        statut, _ = self.appel('POST', f'/api/seances/{ident}/audio?rang=1&format=exe', b'suite', None, OCTETS)
        self.assertEqual(statut, 400)

    def test_reessayer_apres_echec(self):
        ident = self._creer()
        self._envoyer(ident)
        self.appel('POST', f'/api/seances/{ident}/audio/fin', {})
        protocole.ecrire_json(server.DATA / 'audio' / ident / protocole.ERREUR, {'code': 'memoire'})
        self.assertEqual(self.appel('GET', f'/api/seances/{ident}')[1]['etat'], 'echec')
        statut, _ = self.appel('POST', f'/api/seances/{ident}/reessayer', {})
        self.assertEqual((statut, self.appel('GET', f'/api/seances/{ident}')[1]['etat']), (200, 'attente'))

    def test_audio_par_plages(self):
        ident = self._creer()
        self._envoyer(ident)
        statut, corps = self.appel('GET', f'/api/seances/{ident}/audio', entetes=NAVIGATEUR | {'Range': 'bytes=0-9'})
        entetes = dict(self.derniere.headers)
        self.assertEqual((statut, corps, entetes['Content-Range'], entetes['Accept-Ranges']), (206, b'0123456789', 'bytes 0-9/40', 'bytes'))
        statut, corps = self.appel('GET', f'/api/seances/{ident}/audio', entetes=NAVIGATEUR | {'Range': 'bytes=35-'})
        self.assertEqual((statut, corps, dict(self.derniere.headers)['Content-Range']), (206, b'fghij', 'bytes 35-39/40'))
        statut, _ = self.appel('GET', f'/api/seances/{ident}/audio', entetes=NAVIGATEUR | {'Range': 'bytes=50-60'})
        self.assertEqual((statut, dict(self.derniere.headers)['Content-Range']), (416, 'bytes */40'))

    def test_seance_d_un_autre_compte_introuvable(self):
        admin = self.premier_admin()
        _, alice = self.creer(admin, 'alice')
        _, carla = self.creer(admin, 'carla')
        ident = self._creer(alice)
        self._envoyer(ident, alice)
        for methode, chemin, donnees in (('GET', f'/api/seances/{ident}', None), ('GET', f'/api/seances/{ident}/audio', None),
                                         ('POST', f'/api/seances/{ident}/valider', {}), ('POST', f'/api/seances/{ident}/audio/fin', {}),
                                         ('GET', f'/api/seances/{ident}/export?format=txt', None), ('DELETE', f'/api/seances/{ident}', None)):
            with self.subTest(methode=methode, chemin=chemin):
                self.assertEqual(self.appel(methode, chemin, donnees, carla)[0], 404)
        self.assertEqual(self.appel('GET', '/api/seances', None, carla)[1]['seances'], [])
        self.assertEqual(self.appel('GET', f'/api/seances/{ident}', None, alice)[0], 200)

    def _validee(self, jeton=None):
        ident = self._creer(jeton)
        self._envoyer(ident, jeton)
        self.appel('POST', f'/api/seances/{ident}/audio/fin', {}, jeton)
        self._resultat(ident)
        self.appel('GET', f'/api/seances/{ident}', None, jeton)
        self.assertEqual(self.appel('POST', f'/api/seances/{ident}/valider', {}, jeton)[0], 200)
        return ident

    def _modele(self, *reponses):
        mock.patch.object(llm_backend, 'require_configured', return_value={'model': 'gemma'}).start()
        self.addCleanup(mock.patch.stopall)
        return mock.patch.object(llm_backend, 'chat', side_effect=[
            {'choices': [{'message': {'content': json.dumps(r, ensure_ascii=False)}}]} for r in reponses])

    def _pv_saisi(self, jeton=None):
        ident = self._validee(jeton)
        statut, corps = self.appel('POST', f'/api/seances/{ident}/details', {'president': 'M. Martin', 'membres_en_exercice': 19}, jeton)
        self.assertEqual((statut, corps['details']['president']), (200, 'M. Martin'))
        points = [{'titre': POINT, 'deliberation_numero': 'D2026-052', 'vote': {'mode': 'unanimite'}, 'debut': 0.0}]
        statut, corps = self.appel('POST', f'/api/seances/{ident}/points', {'points': points}, jeton)
        self.assertEqual((statut, [p['titre'] for p in corps['points']]), (200, [POINT]))
        return ident

    def test_parcours_pv_complet(self):
        from docx import Document
        ident = self._pv_saisi()
        statut, corps = self.appel('POST', f'/api/seances/{ident}/rediger', {'document': 'proces_verbal'})
        self.assertEqual((statut, corps), (200, {'etat': 'redaction'}))
        self.assertEqual(self.appel('GET', f'/api/seances/{ident}')[1]['progression'], 'redaction 0 1')
        with self._modele({'paragraphes': [TENEUR]}):
            seances.rediger_en_attente(server.DB, server.DATA)
        self.assertEqual(self.appel('GET', f'/api/seances/{ident}')[1]['etat'], 'prete')
        statut, octets = self.appel('GET', f'/api/seances/{ident}/export?format=docx&document=proces_verbal')
        self.assertEqual(statut, 200)
        self.assertIn('filename="proces-verbal.docx"', dict(self.derniere.headers)['Content-Disposition'])
        texte = '\n'.join(p.text for p in Document(io.BytesIO(octets)).paragraphs)
        for attendu in (TENEUR, "Adopté à l'unanimité.", 'M. Martin'):
            self.assertIn(attendu, texte)
        statut, octets = self.appel('GET', f'/api/seances/{ident}/export?format=docx&document=liste_deliberations')
        self.assertEqual(statut, 200)
        self.assertIn('n° D2026-052', '\n'.join(p.text for p in Document(io.BytesIO(octets)).paragraphs))

    def test_rediger_repond_sans_attendre(self):
        ident = self._pv_saisi()
        with self._modele() as chat:
            statut, _ = self.appel('POST', f'/api/seances/{ident}/rediger', {'document': 'proces_verbal'})
        self.assertEqual((statut, chat.call_count), (200, 0))

    def test_ordre_du_jour_propose(self):
        ident = self._validee()
        statut, corps = self.appel('GET', f'/api/seances/{ident}/ordre-du-jour')
        self.assertEqual((statut, corps), (200, {'points': []}))

    def test_saisie_et_documents_refuses_hors_etat(self):
        ident = self._creer()
        for methode, chemin, donnees in (('GET', f'/api/seances/{ident}/ordre-du-jour', None),
                                         ('POST', f'/api/seances/{ident}/details', {'president': 'M. Martin'}),
                                         ('POST', f'/api/seances/{ident}/rediger', {'document': 'compte_rendu'}),
                                         ('GET', f'/api/seances/{ident}/export?format=docx&document=proces_verbal', None)):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.appel(methode, chemin, donnees)[0], 400)

    def test_routes_d_une_seance_d_un_autre_compte_introuvables(self):
        admin = self.premier_admin()
        _, alice = self.creer(admin, 'alice')
        _, carla = self.creer(admin, 'carla')
        ident = self._pv_saisi(alice)
        for methode, chemin, donnees in (('GET', f'/api/seances/{ident}/ordre-du-jour', None),
                                         ('POST', f'/api/seances/{ident}/details', {'president': 'M. Martin'}),
                                         ('POST', f'/api/seances/{ident}/points', {'points': []}),
                                         ('POST', f'/api/seances/{ident}/rediger', {'document': 'proces_verbal'}),
                                         ('GET', f'/api/seances/{ident}/export?format=docx&document=proces_verbal', None)):
            with self.subTest(methode=methode, chemin=chemin):
                self.assertEqual(self.appel(methode, chemin, donnees, carla)[0], 404)

    def test_journal_sans_contenu(self):
        sortie = io.StringIO()
        with redirect_stderr(sortie):
            ident = self._creer()
            self._envoyer(ident)
            self.appel('POST', f'/api/seances/{ident}/audio/fin', {})
            self._resultat(ident)
            self.appel('GET', f'/api/seances/{ident}')
            self.appel('POST', f'/api/seances/{ident}/intervenants', {'etiquette': 'Intervenant 1', 'nom': NOM})
            self.appel('POST', f'/api/seances/{ident}/valider', {})
            self.appel('POST', f'/api/seances/{ident}/points', {'points': [{'titre': POINT, 'debut': 0.0}]})
            self.appel('POST', f'/api/seances/{ident}/rediger', {'document': 'proces_verbal'})
            with self._modele({'paragraphes': [TENEUR]}):
                seances.rediger_en_attente(server.DB, server.DATA)
            self.appel('GET', f'/api/seances/{ident}/export?format=docx&document=proces_verbal')
            self.appel('DELETE', f'/api/seances/{ident}')
        lignes = administration.activite(server.DB)
        self.assertEqual([l['action'] for l in reversed(lignes)][-5:],
                         ['seance_creee', 'transcription_lancee', 'transcription_validee', 'seance_redigee', 'seance_supprimee'])
        tout = json.dumps(lignes, ensure_ascii=False) + sortie.getvalue()
        for interdit in (TITRE, NOM, 'La séance est ouverte', ident, POINT, TENEUR):
            self.assertNotIn(interdit, tout)
        self.assertIn('/api/seances/:id/audio', sortie.getvalue())
        self.assertIn('/api/seances/:id/rediger', sortie.getvalue())


if __name__ == '__main__':
    unittest.main()
