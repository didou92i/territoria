# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import llm_backend
import server
from territoria import deliberations as d
from tests.test_comptes import Base


def completion(objet='Attribution d’une subvention exceptionnelle au comité des fêtes',
               considerants=('Considérant la demande du comité des fêtes pour l’organisation du marché de Noël,',),
               decisions=('Le conseil municipal décide d’attribuer une subvention exceptionnelle de 1 500 € au comité des fêtes.',
                          'Les crédits sont inscrits au budget de l’exercice, chapitre [à compléter].')):
    contenu = json.dumps({'objet': objet, 'considerants': list(considerants), 'decisions': list(decisions)}, ensure_ascii=False)
    return {'choices': [{'message': {'content': contenu}}]}


DEMANDE = ('Rédige une délibération pour attribuer une subvention exceptionnelle de 1 500 € au comité des fêtes '
           'pour le marché de Noël. Séance du 15 décembre 2026.')


class TextesEtThemesTests(unittest.TestCase):
    def test_la_copie_livree_est_complete_et_verifiee(self):
        textes = d.textes()
        self.assertEqual(set(textes), {'L2121-17', 'L2121-20', 'L2121-23', 'L2121-29', 'L1611-4', 'L2241-1', 'L1612-11',
                                       'L313-1', 'R531-52', 'L2131-1', 'R421-1', 'L2122-22', 'L2122-23'})
        self.assertTrue(textes['L2121-23']['text'].endswith('signées par le maire et le ou les secrétaires de séance.'))

    def test_un_texte_altere_est_ecarte(self):
        donnees = json.loads(d.REFERENCE.read_text(encoding='utf-8'))
        donnees['sources'][0]['text'] += ' Phrase ajoutée.'
        with tempfile.TemporaryDirectory() as dossier:
            copie = Path(dossier) / 'ref.json'
            copie.write_text(json.dumps(donnees, ensure_ascii=False), encoding='utf-8')
            with patch.object(d, 'REFERENCE', copie):
                self.assertNotIn(donnees['sources'][0]['article'], d.textes())
                self.assertEqual(len(d.textes()), 12)

    def test_themes(self):
        for texte, attendu in (('subvention de 800 € au club de football', 'subvention'),
                               ('création d’un poste d’adjoint technique à 35 h', 'emploi'),
                               ('modifier le tableau des emplois', 'emploi'),
                               ('tarifs de la restauration scolaire', 'restauration'),
                               ('cession de la parcelle AB 12 à un riverain', 'immobilier'),
                               ('décision modificative n°2 du budget', 'budget'),
                               ('dénomination de la nouvelle place du marché', 'general'),
                               ('délégations du conseil municipal au maire, dont les demandes de subventions', 'delegation'),
                               ('délégation d’attributions au maire', 'delegation')):
            with self.subTest(texte=texte):
                self.assertEqual(d.theme(texte)[0], attendu)

    def test_reconnaissance_d_une_demande(self):
        self.assertTrue(d.est_demande(DEMANDE))
        self.assertTrue(d.est_demande('Délibération portant création d’un emploi'))
        self.assertTrue(d.est_demande('Le comité demande 500 €', assistant='deliberation'))
        self.assertFalse(d.est_demande('Que dit la délibération du 12 mars ?'))
        self.assertFalse(d.est_demande('Rédige un arrêté de stationnement'))
        self.assertTrue(d.est_demande('Remplace le montant par 2 000 €', {'kind': 'deliberation', 'inputs': {}}))
        self.assertFalse(d.est_demande('Pourquoi ce montant ?', {'kind': 'deliberation', 'inputs': {}}))
        self.assertTrue(d.est_demande('Pour le comité des fêtes', {'kind': 'deliberation_clarification', 'inputs': {}}))


class PreparationTests(unittest.TestCase):
    def test_demande_trop_vague_et_suivi_cumule(self):
        donnees, question = d.preparer('Rédige une délibération', None, 'Commune de Testville')
        self.assertEqual(question, d.CLARIFICATION)
        donnees, question = d.preparer('Une subvention de 500 € au club de judo',
                                       {'kind': 'deliberation_clarification', 'inputs': donnees}, 'Commune de Testville')
        self.assertEqual(question, '')
        self.assertIn('Rédige une délibération', donnees['contexte'])
        self.assertIn('club de judo', donnees['contexte'])
        self.assertEqual(donnees['collectivite'], 'Commune de Testville')

    def test_date_de_seance_et_collectivite_citee(self):
        donnees, _ = d.preparer('Délibération de la commune de Senlis pour une subvention de 300 €, séance du 3 février 2027', None, '')
        self.assertEqual((donnees['seance'], donnees['collectivite']), ('3 février 2027', 'Commune de Senlis'))
        self.assertEqual(d.collectivite('Subvention de 300 €. Commune de Senlis.'), 'Commune de Senlis')  # majuscule en tête
        self.assertEqual(d.collectivite('Pour la commune de Senlis : subvention de 300 €'), 'Commune de Senlis')


class LectureTests(unittest.TestCase):
    donnees = {'contexte': DEMANDE}

    def test_reponse_conforme(self):
        genere = d.lire(completion(), self.donnees)
        self.assertIn('1 500 €', genere['decisions'][0])
        self.assertEqual(genere['vigilances'], [])

    def test_nombre_invente_remplace(self):
        genere = d.lire(completion(decisions=('Le conseil municipal attribue 2 500 € au comité, versés en 3 fois.',)), self.donnees)
        self.assertEqual(genere['decisions'][0], 'Le conseil municipal attribue [à compléter] € au comité, versés en [à compléter] fois.')
        self.assertEqual(len(genere['vigilances']), 1)

    def test_reference_juridique_refusee(self):
        for texte in ('Vu l’article L2121-29 du CGCT, le conseil décide.', 'Conformément au code général des collectivités territoriales,',
                      'En application de la loi n° 2000-321,'):
            with self.subTest(texte=texte), self.assertRaises(d.ReferenceInventee):
                d.lire(completion(considerants=(texte,)), self.donnees)

    def test_structure_invalide(self):
        for contenu in ('pas du json', json.dumps({'objet': 'x'}), json.dumps({'objet': 'x', 'considerants': [], 'decisions': ['y']})):
            with self.subTest(contenu=contenu), self.assertRaises(ValueError):
                d.lire({'choices': [{'message': {'content': contenu}}]}, self.donnees)


class AssemblageTests(unittest.TestCase):
    def test_chiffres_non_fournis_ne_sont_pas_deduits(self):
        def document(question):
            donnees, _ = d.preparer(question, None, 'Commune de Testville')
            return d.assembler(donnees, d.lire(completion(), donnees), d.textes())

        # Les chiffres de l'objet et ceux d'un montant n'indiquent ni voix ni présence.
        doc = document(DEMANDE + ' Aide de 23 euros pour 2 ateliers de 3 heures. Le conseil délègue 3 pouvoirs de police au maire.')
        self.assertNotRegex(doc['vote'], r'\d')
        effectifs = next(p for p in doc['preambule'] if 'Membres en exercice' in p)
        self.assertNotRegex(effectifs, r'\d')

        # Une seule présence connue ne suffit pas pour déterminer votants et voix.
        doc = document(DEMANDE + ' 22 membres présents.')
        effectifs = next(p for p in doc['preambule'] if 'Membres en exercice' in p)
        self.assertRegex(effectifs.lower(), r'présents\s*:\s*22\b|\b22\s+(?:membres\s+)?présents\b')
        self.assertNotRegex(effectifs.lower(), r'(?:membres en exercice|votants)\s*:\s*\d')
        self.assertNotRegex(doc['vote'], r'\d')

    def test_ancien_projet_vote_partiel_et_changement_de_mode(self):
        donnees = {'contexte': DEMANDE + ' 29 membres en exercice. Présents : 22. Vote : Pour : 23.',
                   'seance': '15 décembre 2026'}

        def modifier(question):
            nonlocal donnees
            precedent = {'kind': 'deliberation', 'inputs': donnees}
            donnees, _ = d.preparer(question, precedent, 'Commune de Testville')
            return d.assembler(donnees, d.lire(completion(), donnees), d.textes())

        acte = modifier('Modifie le montant de la subvention.')
        self.assertRegex('\n'.join(acte['preambule']).lower(), r'présents\s*:\s*22\b|\b22\s+présents\b')
        self.assertRegex(acte['vote'].lower(), r'\b23\s+(?:voix\s+)?pour\b|\bpour\s*:\s*23\b')

        corrige = modifier('Corrige le vote : pour : 21.')
        self.assertRegex(corrige['vote'].lower(), r'\b21\s+(?:voix\s+)?pour\b|\bpour\s*:\s*21\b')
        self.assertEqual(re.findall(r'\d+', corrige['vote']), ['21'])
        unanimite = modifier('Corrige le vote : à l’unanimité.')
        self.assertIn('unanimité', unanimite['vote'].lower())
        self.assertNotRegex(unanimite['vote'], r'\d')

        decompte = modifier('Corrige les effectifs et le vote : présents : 3. Vote : 2 pour, 1 contre.')
        self.assertRegex(decompte['vote'].lower(), r'\b2\s+(?:voix\s+)?pour\b|\bpour\s*:\s*2\b')
        self.assertRegex(decompte['vote'].lower(), r'\b1\s+(?:voix\s+)?contre\b|\bcontre\s*:\s*1\b')
        self.assertNotIn('unanimité', decompte['vote'].lower())
        self.assertNotIn('quorum étant atteint', '\n'.join(decompte['preambule']).lower())

    def test_corrections_et_mentions_etrangeres_au_scrutin(self):
        cas = (('Vote : 23\u00a0pour/0\u202fcontre/2 abstentions.',
                'Remplace les 2 abstentions par 1 et les 23 voix pour par 24.', ['24', '0', '1']),
               ('Vote : 23 pour, 0 contre, 2 abstentions.',
                'Cette délibération n’est pas adoptée à l’unanimité.', ['23', '0', '2']))
        for initial, modification, valeurs in cas:
            with self.subTest(modification=modification):
                donnees, _ = d.preparer(DEMANDE + ' ' + initial, None, 'Commune de Testville')
                donnees, _ = d.preparer(modification, {'kind': 'deliberation', 'inputs': donnees}, '')
                acte = d.assembler(donnees, d.lire(completion(), donnees), d.textes())
                self.assertEqual(re.findall(r'\d+', acte['vote']), valeurs)
        with self.subTest(motif='fréquentation d’un événement'):
            donnees, _ = d.preparer(DEMANDE + ' Le marché a accueilli 2 000 présents à la dernière édition.', None, '')
            acte = d.assembler(donnees, d.lire(completion(), donnees), d.textes())
            effectifs = next(p for p in acte['preambule'] if 'Membres en exercice' in p)
            self.assertNotRegex(effectifs, r'\d')

    def test_document_d_une_subvention(self):
        donnees, _ = d.preparer(DEMANDE, None, 'Commune de Testville')
        doc = d.assembler(donnees, d.lire(completion(), donnees), d.textes())
        self.assertEqual(doc['visas'][0], 'Vu le code général des collectivités territoriales, notamment ses articles L2121-29 et L1611-4,')
        self.assertIn('Séance du 15 décembre 2026', doc['preambule'])
        self.assertEqual((doc['autorite'], doc['decision']), ('Le conseil municipal', 'DÉCIDE'))
        self.assertEqual([a['role'] for a in doc['articles']], ['decision', 'decision', 'execution', 'publicite', 'recours'])
        self.assertIn('Télérecours citoyens', doc['articles'][-1]['texte'])
        self.assertEqual(doc['signatures'], ['Le Maire, [nom et signature]', 'Le secrétaire de séance, [nom et signature]'])
        self.assertEqual(doc['lieu_date'], 'Fait et délibéré à Testville, les jour, mois et an susdits.')

    def test_emploi_vise_le_code_de_la_fonction_publique(self):
        donnees, _ = d.preparer('Délibération pour créer un poste d’adjoint administratif à 35 heures', None, 'Commune de Testville')
        doc = d.assembler(donnees, d.lire(completion(decisions=('Le conseil municipal décide de créer un emploi.',)), donnees), d.textes())
        self.assertIn('Vu le code général de la fonction publique, notamment son article L313-1,', doc['visas'])
        self.assertTrue(any('L313-1' in p for p in doc['points']))

    def test_etablissement_intercommunal(self):
        donnees = {'contexte': 'Délibération pour attribuer une subvention de 1 500 €', 'collectivite': 'Communauté de communes du Val'}
        doc = d.assembler(donnees, d.lire(completion(), donnees), d.textes())
        self.assertEqual(doc['autorite'], 'Le conseil communautaire')
        self.assertTrue(doc['articles'][-3]['texte'].startswith('Le Président'))
        self.assertTrue(any('L5211-1' in p for p in doc['points']))


class ConversationTests(Base):
    def test_vote_et_effectifs_de_la_demande_jusqu_au_word(self):
        from docx import Document
        from io import BytesIO

        demande = (DEMANDE + ' Convocation du 9 décembre 2026. '
                   '29 membres en exercice, 22 présents, 3 pouvoirs et 25 votants. Vote : 23 pour, 0 contre, 2 abstentions.')
        tours = ((demande, 29, 23, 0),
                 ('Modifiez le montant de la subvention à 2 000 euros.', 29, 23, 0),
                 ('Corrigez les données de séance : 30 membres en exercice. Vote : 22 pour, 1 contre, 2 abstentions.', 30, 22, 1))
        payload = {'assistant': 'deliberation', 'mode': 'libre'}
        with patch.object(llm_backend, 'chat', return_value=completion()):
            for question, effectif, pour, contre in tours:
                statut, reponse = self.appel('POST', '/api/chat', {**payload, 'question': question})
                self.assertEqual(statut, 200)
                payload['conversation'] = reponse['conversation']
                artefact = reponse['artifact']
                self.assertEqual(artefact['kind'], 'deliberation')
                acte = artefact['document']
                statut, word = self.appel('GET', artefact['download'])
                self.assertEqual(statut, 200)
                paragraphes = [p.text for p in Document(BytesIO(word)).paragraphs]
                with self.subTest(question=question):
                    entete = '\n'.join(acte['preambule'])
                    for libelle, valeur in (('membres en exercice', effectif), ('présents', 22), ('pouvoirs', 3), ('votants', 25)):
                        self.assertRegex(entete.lower(), rf'\b{libelle}\s*:\s*{valeur}\b|\b{valeur}\s+{libelle}\b')
                    self.assertRegex(entete.lower(), r'convocation\s*:\s*9 décembre 2026')
                    for voix, sens in ((pour, 'pour'), (contre, 'contre'), (2, 'abstentions')):
                        self.assertRegex(acte['vote'].lower(), rf'\b{voix}\s+(?:voix\s+)?{sens}\b|\b{sens}\s*:\s*{voix}\b')
                    for ligne in [*acte['preambule'], acte['vote']]:
                        self.assertIn(ligne, paragraphes)

    def test_projet_complet_dans_la_conversation_avec_word(self):
        with patch.object(llm_backend, 'chat', return_value=completion()) as modele:
            statut, reponse = self.appel('POST', '/api/chat', {'question': DEMANDE, 'mode': 'libre'})
        self.assertEqual(statut, 200)
        self.assertEqual(modele.call_args.args[1]['response_format']['type'], 'json_schema')
        artefact = reponse['artifact']
        self.assertEqual(artefact['kind'], 'deliberation')
        self.assertEqual([s['article'] for s in artefact['legal']['sources']], ['L2121-29', 'L1611-4', 'L2121-17', 'L2121-20', 'L2121-23', 'L2131-1', 'R421-1'])
        self.assertEqual(reponse['sources'][0]['kind'], 'legifrance')
        statut, word = self.appel('GET', artefact['download'])
        self.assertEqual(statut, 200)
        from docx import Document
        import io
        texte = '\n'.join(p.text for p in Document(io.BytesIO(word)).paragraphs)
        for attendu in ('DÉCIDE', 'Le secrétaire de séance, [nom et signature]', 'Vu le code général des collectivités territoriales',
                        'Séance du 15 décembre 2026', 'Certifiée exécutoire'):
            self.assertIn(attendu, texte)
        from territoria import administration
        self.assertEqual(administration.activite(server.DB, 5)[0]['action'], 'deliberation')  # avant la lecture du Word

    def test_reference_inventee_rejouee_une_fois_puis_refusee(self):
        mauvaise = completion(considerants=('Vu l’article L2121-29 du CGCT,',))
        with patch.object(llm_backend, 'chat', side_effect=[mauvaise, completion()]) as modele:
            self.assertEqual(self.appel('POST', '/api/chat', {'question': DEMANDE, 'mode': 'libre'})[0], 200)
        self.assertEqual(modele.call_count, 2)
        with patch.object(llm_backend, 'chat', side_effect=[mauvaise, mauvaise]):
            statut, corps = self.appel('POST', '/api/chat', {'question': DEMANDE, 'mode': 'libre'})
        self.assertEqual(statut, 400)
        self.assertIn('référence juridique', corps['error'])

    def test_demande_vague_puis_precision(self):
        with patch.object(llm_backend, 'chat', return_value=completion()) as modele:
            premiere = self.appel('POST', '/api/chat', {'question': 'Rédige une délibération', 'mode': 'libre'})[1]
            self.assertEqual((premiere['answer'], premiere['artifact']['kind']), (d.CLARIFICATION, 'deliberation_clarification'))
            modele.assert_not_called()
            seconde = self.appel('POST', '/api/chat', {'question': 'Subvention de 1 500 € au comité des fêtes', 'mode': 'libre',
                                                     'conversation': premiere['conversation']})[1]
        self.assertEqual(seconde['artifact']['kind'], 'deliberation')
        self.assertIn('Rédige une délibération', seconde['artifact']['inputs']['contexte'])

    def test_supprimer_la_conversation_efface_le_word(self):
        with patch.object(llm_backend, 'chat', return_value=completion()):
            reponse = self.appel('POST', '/api/chat', {'question': DEMANDE, 'mode': 'libre'})[1]
        fichier = server.DATA / 'arretes' / (reponse['artifact']['id'] + '.docx')
        self.assertTrue(fichier.exists())
        self.appel('DELETE', f"/api/conversations/{reponse['conversation']}")
        self.assertFalse(fichier.exists())


if __name__ == '__main__':
    unittest.main()


class ValeursDonneesTests(unittest.TestCase):
    def test_valeurs_a_reprendre(self):
        self.assertEqual(d.valeurs_donnees('Subvention de 1 500 € pour Noël 2026, séance du 15 décembre 2026.'), ['1 500 €', '15 décembre 2026'])
        self.assertEqual(d.valeurs_donnees('Poste à 35 heures à compter du 1er janvier 2027'), ['35 heures', '1er janvier 2027'])
        self.assertEqual(d.valeurs_donnees('Parcelle AB 112 de 850 m² au prix de 12 000 euros'), ['AB 112', '850 m²', '12 000 euros'])
        self.assertEqual(d.valeurs_donnees('3,20 € le repas enfant et 5,50 € le repas adulte'), ['3,20 €', '5,50 €'])


class RelectureTests(Base):
    def test_questions_sur_une_deliberation_existante_ne_redigent_rien(self):
        for question in ('Que fait la délibération du 12 mars ?', 'Fais une synthèse de la délibération jointe',
                         'Résume la délibération sur les tarifs', 'Quelle délibération fixe les tarifs ?'):
            with self.subTest(question=question):
                self.assertFalse(d.est_demande(question))
        for demande in ('Fais une délibération pour la subvention du club', 'Prépare un projet de délibération sur les tarifs',
                        'Délibération fixant les tarifs de la cantine', 'Rédige une délibération pour dénommer la place Jean-Moulin'):
            with self.subTest(demande=demande):
                self.assertTrue(d.est_demande(demande))

    def test_un_objet_court_ne_declenche_pas_de_question(self):
        self.assertEqual(d.preparer('Rédige une délibération pour dénommer la nouvelle place Jean-Moulin', None, 'Commune de T')[1], '')
        self.assertEqual(d.preparer('Rédige-moi une délibération stp', None, 'Commune de T')[1], d.CLARIFICATION)

    def test_word_nomme_selon_l_acte(self):
        with patch.object(llm_backend, 'chat', return_value=completion()):
            reponse = self.appel('POST', '/api/chat', {'question': DEMANDE, 'mode': 'libre'})[1]
        self.appel('GET', reponse['artifact']['download'])
        self.assertIn('filename="projet-deliberation.docx"', dict(self.derniere.headers)['Content-Disposition'])

    def test_l_assistant_de_deliberation_est_une_fonction_integree(self):
        self.assertEqual(self.appel('POST', '/api/admin/assistants/deliberation', {'actif': False})[0], 403)
        assistant = next(a for a in self.appel('GET', '/api/admin/assistants')[1] if a['id'] == 'deliberation')
        self.assertTrue(assistant['integre'])


class DelegationsTests(unittest.TestCase):
    def test_le_modele_recoit_toute_la_liste_des_matieres_delegables(self):
        demande = 'Rédige une délibération de délégations au maire : marchés jusqu’à 90 000 € HT, actions en justice, régies comptables.'
        payload = d.requete({'collectivite': 'Commune de Testville', 'contexte': demande}, 'modele', d.textes())
        extraits = {t['article']: t['extrait'] for t in json.loads(payload['messages'][1]['content'])['textes_applicables']}
        self.assertIn('intenter au nom de la commune', extraits['L2122-22'])  # 16°, bien au-delà des 500 premiers caractères
        self.assertLessEqual(len(extraits['L2122-23']), 500)
        self.assertIn('reprend la formulation de l’alinéa', payload['messages'][0]['content'])
