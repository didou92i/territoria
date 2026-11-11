# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import llm_backend  # noqa: E402
from territoria import seances_redaction  # noqa: E402

SOURCE = '[00:00:05] M. le Maire : Je propose 3 000 euros. [00:00:20] Mme Durand : Pourquoi pas 2 500 ?'


def completion(contenu):
    return {'choices': [{'message': {'content': contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False)}}]}


class GardeFousTests(unittest.TestCase):
    def test_nombre_absent_remplace(self):
        texte, vigilances = seances_redaction.nettoyer('M. le Maire propose 4 000 euros.', SOURCE)
        self.assertEqual(texte, 'M. le Maire propose [à compléter] euros.')
        self.assertEqual(vigilances, ['Nombre absent des passages remplacé par [à compléter].'])

    def test_nom_absent_remplace(self):
        texte, vigilances = seances_redaction.nettoyer('Mme Martin demande une précision.', SOURCE)
        self.assertEqual(texte, '[à compléter] demande une précision.')
        self.assertEqual(vigilances, ['Nom absent des passages remplacé par [à compléter].'])

    def test_nom_present_garde(self):
        self.assertEqual(seances_redaction.nettoyer('Mme Durand propose 2 500 euros.', SOURCE),
                         ('Mme Durand propose 2 500 euros.', []))

    def test_reference_juridique_absente_remplacee(self):
        texte, vigilances = seances_redaction.nettoyer("En application de l'article L2121-29, le conseil délibère.", SOURCE)
        self.assertEqual(texte, "En application de l'[à compléter], le conseil délibère.")
        self.assertEqual(vigilances, [seances_redaction.VIGILANCE_REFERENCE])

    def test_nombres_des_heures_non_connus(self):
        # Relecture finale : les heures « [00:12:34] » des passages rendaient « connus » tous les nombres de 0 à 59.
        source = '[00:12:34] M. le Maire : Bonsoir. [00:15:02] Mme Durand : Merci.'
        texte, vigilances = seances_redaction.nettoyer('Il propose 12 logements et 34 places pour 15 associations.', source)
        self.assertEqual(texte, 'Il propose [à compléter] logements et [à compléter] places pour [à compléter] associations.')
        self.assertEqual(vigilances, [seances_redaction.VIGILANCE_NOMBRE])

    def test_phrase_ordinaire_sur_la_loi_gardee(self):
        # Relecture finale : « loi n… » attrapait « la loi ne laisse pas le choix » et faisait échouer le point.
        for phrase in ('Il est répondu que la loi ne laisse pas le choix.', 'La loi nous impose ce choix.'):
            self.assertEqual(seances_redaction.nettoyer(phrase, SOURCE), (phrase, []))

    def test_reference_comparee_en_entier(self):
        source = SOURCE + " [00:01:00] M. le Maire : Selon l'article L. 2121-21, nous votons."
        texte, _ = seances_redaction.nettoyer("Il cite l'article L2121-29.", source)
        self.assertEqual(texte, "Il cite l'[à compléter].")
        texte, _ = seances_redaction.nettoyer('Il cite la loi n° 2019-828.', SOURCE + ' plus loin')
        self.assertEqual(texte, 'Il cite la [à compléter].')

    def test_reference_juridique_citee_en_seance_gardee(self):
        source = SOURCE + " [00:01:00] M. le Maire : Selon l'article L. 2121-29, nous délibérons."
        texte, _ = seances_redaction.nettoyer("M. le Maire rappelle l'article L2121-29.", source)
        self.assertEqual(texte, "M. le Maire rappelle l'article L2121-29.")

    def test_gras_retire(self):
        self.assertEqual(seances_redaction.nettoyer('**Mme Durand** demande __pourquoi__.', SOURCE)[0], 'Mme Durand demande pourquoi.')


class LectureTests(unittest.TestCase):
    def test_cles_inattendues_refusees(self):
        for contenu in ({'paragraphes': ['a'], 'autre': 1}, {}, ['a'], 'pas du JSON'):
            with self.subTest(contenu=contenu), self.assertRaises(ValueError):
                seances_redaction.lire_reponse(completion(contenu), ('paragraphes',))
        with self.assertRaises(ValueError):
            seances_redaction.lire_reponse({'choices': []}, ('paragraphes',))

    def test_cloture_markdown_retiree(self):
        contenu = '```json\n' + json.dumps({'paragraphes': ['Texte.']}) + '\n```'
        self.assertEqual(seances_redaction.lire_reponse(completion(contenu), ('paragraphes',)), {'paragraphes': ['Texte.']})


PASSAGES = ['[00:00:05] M. le Maire : Je présente le budget de 3 000 000 euros.', '[00:00:20] Mme Durand : Pourquoi une hausse de 2 % ?']
TENEUR = {'paragraphes': ['M. le Maire présente le budget de 3 000 000 euros.', 'Mme Durand demande pourquoi une hausse de 2 %.']}
COMPTE_RENDU = {'points': [{'titre': 'Budget', 'synthese': 'M. le Maire présente le budget de 3 000 000 euros.'}],
                'decisions': ['Le budget est présenté.'], 'actions': [{'qui': 'Mme Durand', 'quoi': 'Préciser la hausse de 2 %.', 'echeance': ''}]}


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(llm_backend, 'require_configured', return_value={'model': 'gemma'}).start()

    def modele(self, *reponses):
        return mock.patch.object(llm_backend, 'chat', side_effect=list(reponses))

    def test_un_morceau_un_appel(self):
        with self.modele(completion(TENEUR)) as chat:
            resultat = seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual(resultat, {'paragraphes': TENEUR['paragraphes'], 'vigilances': []})
        self.assertEqual(chat.call_count, 1)

    def test_morceaux_mis_bout_a_bout_sans_fusion(self):
        # Recette : la fusion par le modèle rendait un seul bloc de près de 12 000 caractères, coupé à la limite de jetons
        # sur le plus long point (échec « reponse »). Les paragraphes des morceaux, déjà contrôlés, sont repris dans l'ordre.
        longs = ['[00:00:01] M. le Maire : ' + 'budget ' * 1000, '[00:10:00] Mme Durand : ' + 'question ' * 1000]
        inutilisable = completion('pas du JSON')
        with self.modele(completion({'paragraphes': ['M. le Maire présente le budget.']}),
                         completion({'paragraphes': ['Mme Durand pose 4 questions.', 'Il lui est répondu.']}),
                         inutilisable, inutilisable) as chat:
            resultat = seances_redaction.rediger_teneur(Path('/donnees'), longs)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(resultat, {'paragraphes': ['M. le Maire présente le budget.', 'Mme Durand pose [à compléter] questions.',
                                                    'Il lui est répondu.'],
                                    'vigilances': [seances_redaction.VIGILANCE_NOMBRE]})

    def test_schema_strict_dans_la_requete(self):
        with self.modele(completion(TENEUR)) as chat:
            seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        requete = chat.call_args[0][1]
        self.assertEqual(requete['model'], 'gemma')
        self.assertEqual((requete['temperature'], requete['max_tokens']), (0.1, 3000))
        self.assertEqual(requete['response_format']['type'], 'json_schema')
        self.assertIs(requete['response_format']['json_schema']['strict'], True)
        self.assertEqual(requete['response_format']['json_schema']['name'], 'teneur_point')
        self.assertEqual(requete['messages'][0]['content'], seances_redaction.CONSIGNE_TENEUR)
        self.assertEqual(json.loads(requete['messages'][1]['content']), {'passages': PASSAGES})

    def test_reponse_inutilisable_rejouee_une_fois(self):
        with self.modele(completion('pas du JSON'), completion(TENEUR)) as chat:
            resultat = seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(resultat['paragraphes'], TENEUR['paragraphes'])

    def test_reponse_tronquee_rejouee_en_demandant_plus_bref(self):
        # Recette : sur un point long, Gemma paraphrase tout et s'arrête à la limite de jetons (JSON coupé, « length »).
        tronquee = {'choices': [{'message': {'content': '{"paragraphes": ["M. le Maire présente'}, 'finish_reason': 'length'}]}
        with self.modele(tronquee, completion(TENEUR)) as chat:
            resultat = seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual(resultat['paragraphes'], TENEUR['paragraphes'])
        premiere, seconde = (appel[0][1]['messages'][0]['content'] for appel in chat.call_args_list)
        self.assertNotIn(seances_redaction.RAPPEL_BREF, premiere)
        self.assertEqual(seconde, premiere + ' ' + seances_redaction.RAPPEL_BREF)

    def test_teneur_longue_acceptee(self):
        # Recette : sur un morceau de 12 000 caractères, Gemma rend plus de 12 paragraphes (le schéma n'est pas tenu à la
        # lettre) ; la réponse reste utilisable.
        longue = {'paragraphes': [f'M. le Maire présente le point {n}.' for n in range(1, 21)] + ['Mme Durand répond. ' * 250]}
        source = PASSAGES + ['[00:01:00] M. le Maire : ' + ' '.join(f'point {n}' for n in range(1, 21))]
        with self.modele(completion(longue)) as chat:
            resultat = seances_redaction.rediger_teneur(Path('/donnees'), source)
        self.assertEqual((chat.call_count, len(resultat['paragraphes'])), (1, 21))

    def test_consignes_demandent_la_concision(self):
        self.assertIn('paragraphes courts', seances_redaction.CONSIGNE_TENEUR)
        self.assertIn('sans reprendre les passages mot à mot', seances_redaction.CONSIGNE_TENEUR)
        self.assertIn('un à huit paragraphes courts, selon la longueur des échanges', seances_redaction.CONSIGNE_TENEUR)
        self.assertIn('quelques phrases', seances_redaction.CONSIGNE_COMPTE_RENDU)

    def test_reference_inventee_remplacee_sans_rejeu(self):
        invente = {'paragraphes': ["Vu l'article L2121-29, le conseil délibère."]}
        with self.modele(completion(invente)) as chat:
            resultat = seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual((chat.call_count, resultat['paragraphes']), (1, ["Vu l'[à compléter], le conseil délibère."]))
        self.assertEqual(resultat['vigilances'], [seances_redaction.VIGILANCE_REFERENCE])

    def test_deux_reponses_inutilisables_echec(self):
        with self.modele(completion({'autre': 1}), completion({'paragraphes': []})), \
                self.assertRaises(seances_redaction.EchecRedaction) as echec:
            seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual(echec.exception.code, 'reponse')

    def test_modele_injoignable_echec(self):
        with self.modele(RuntimeError('Serveur IA injoignable.')), self.assertRaises(seances_redaction.EchecRedaction) as echec:
            seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual(echec.exception.code, 'modele')

    def test_reglages_ia_invalides_echec_modele(self):
        with mock.patch.object(llm_backend, 'require_configured', side_effect=ValueError('Réglages IA invalides.')), \
                self.assertRaises(seances_redaction.EchecRedaction) as echec:
            seances_redaction.rediger_teneur(Path('/donnees'), PASSAGES)
        self.assertEqual(echec.exception.code, 'modele')

    def test_fusion_bornee_par_groupes(self):
        # Relecture finale : la fusion était un seul appel sur tous les comptes rendus partiels ; sur une longue séance, il
        # débordait du contexte du modèle. Ils sont fusionnés par groupes qui tiennent dans un morceau, puis de nouveau.
        passages = [f'[00:{n:02d}:00] M. le Maire : ' + 'budget ' * 1500 for n in range(5)]
        partiel = {'points': [{'titre': 'Budget', 'synthese': 'M. le Maire présente le budget. ' * 120}],  # près de 4 000 caractères
                   'decisions': [], 'actions': []}
        final = {'points': [{'titre': 'Budget', 'synthese': 'M. le Maire présente longuement le budget.'}], 'decisions': [], 'actions': []}
        with self.modele(*[completion(partiel)] * 7, completion(final)) as chat:
            resultat = seances_redaction.rediger_compte_rendu(Path('/donnees'), passages)
        self.assertEqual(chat.call_count, 8)  # 5 morceaux, 2 fusions de groupe, 1 fusion finale
        self.assertEqual(resultat, dict(final, vigilances=[]))
        fusions = [len(appel[0][1]['messages'][1]['content']) for appel in chat.call_args_list[5:]]
        self.assertTrue(all(longueur < 2 * seances_redaction.TAILLE_MORCEAU for longueur in fusions), fusions)

    def test_compte_rendu_redige(self):
        with self.modele(completion(COMPTE_RENDU)) as chat:
            resultat = seances_redaction.rediger_compte_rendu(Path('/donnees'), PASSAGES)
        self.assertEqual(resultat, dict(COMPTE_RENDU, vigilances=[]))
        requete = chat.call_args[0][1]
        self.assertEqual((requete['response_format']['json_schema']['name'], requete['max_tokens']), ('compte_rendu', 3000))
        self.assertEqual(requete['messages'][0]['content'], seances_redaction.CONSIGNE_COMPTE_RENDU)

    def test_compte_rendu_nombre_invente_signale(self):
        invente = dict(COMPTE_RENDU, decisions=['Le budget de 4 000 euros est adopté.'])
        with self.modele(completion(invente)):
            resultat = seances_redaction.rediger_compte_rendu(Path('/donnees'), PASSAGES)
        self.assertEqual(resultat['decisions'], ['Le budget de [à compléter] euros est adopté.'])
        self.assertEqual(resultat['vigilances'], [seances_redaction.VIGILANCE_NOMBRE])

    def test_morceaux_sans_couper_un_passage(self):
        a, b, c, d = 'a' * 5000, 'b' * 5000, 'c' * 5000, 'd' * 20000
        self.assertEqual(seances_redaction.morceaux([a, b, c]), [[a, b], [c]])
        self.assertEqual(seances_redaction.morceaux([a, d, b]), [[a], [d], [b]])
        self.assertEqual(seances_redaction.morceaux([]), [])


if __name__ == '__main__':
    unittest.main()
