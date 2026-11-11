# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import llm_backend
import server
from territoria import arretes_delegations as de
from tests.test_comptes import Base

FONCTION = ('Rédige un arrêté de délégation de fonction à M. Paul Lambert, deuxième adjoint, pour les finances, le budget et '
            'les marchés publics, à compter du 1er décembre 2026. Commune de Villeneuve-sur-Exemple.')
SIGNATURE = ('Rédige un arrêté de délégation de signature à Mme Nadia Fontaine, directrice générale des services, pour les bons '
             'de commande et les courriers administratifs. Commune de Villeneuve-sur-Exemple.')
ETAT_CIVIL = ('Rédige un arrêté de délégation des fonctions d’officier d’état civil à Mme Julie Perrin, rédactrice territoriale, '
              'à compter du 1er janvier 2027. Commune de Villeneuve-sur-Exemple.')


def completion(objet='Délégation de fonction à M. Paul Lambert',
               considerants=('Considérant la nécessité d’assurer le bon fonctionnement de l’administration communale,',),
               decisions=('M. Paul Lambert, deuxième adjoint, est délégué, sous la surveillance et la responsabilité du maire, pour les finances, le budget et les marchés publics, à compter du 1er décembre 2026.',)):
    contenu = json.dumps({'objet': objet, 'considerants': list(considerants), 'decisions': list(decisions)}, ensure_ascii=False)
    return {'choices': [{'message': {'content': contenu}}]}


class ThemesTests(unittest.TestCase):
    def test_la_copie_livree_est_complete(self):
        self.assertEqual(set(de.textes()), {'L2122-18', 'L2122-19', 'L2122-20', 'R2122-8', 'R2122-10', 'L2122-32', 'L2131-1', 'L2131-2', 'R421-1'})

    def test_themes(self):
        for texte, attendu in ((FONCTION, 'delegation_fonction'), (SIGNATURE, 'delegation_signature'), (ETAT_CIVIL, 'delegation_etat_civil'),
                               ('délégation de signature à M. Roy pour la légalisation des signatures et la certification conforme des copies', 'delegation_certification')):
            with self.subTest(texte=texte):
                self.assertEqual(de.theme(texte)['nom'], attendu)
        self.assertIsNone(de.theme('arrêté de nomination stagiaire de Mme Durand'))

    def test_reconnaissance(self):
        self.assertTrue(de.est_demande(FONCTION))
        self.assertFalse(de.est_demande('Rédige une délibération portant délégations du conseil municipal au maire'))  # c'est une délibération
        self.assertFalse(de.est_demande('Qu’est-ce qu’une délégation de signature ?'))
        donnees, question = de.preparer('Rédige un arrêté de délégation de fonction', None, '')
        self.assertEqual(question, de.CLARIFICATION)


class DocumentTests(unittest.TestCase):
    def assembler(self, demande, **genere):
        donnees = {'contexte': demande, 'collectivite': 'Commune de Villeneuve-sur-Exemple'}
        return de.assembler(donnees, de.lire(completion(**genere), donnees), de.textes())

    def test_delegation_de_fonction(self):
        document = self.assembler(FONCTION)
        self.assertEqual(document['kind'], 'arrete_delegation')
        self.assertEqual(document['visas'][0], 'Vu le code général des collectivités territoriales, notamment ses articles L2122-18 et L2122-20,')
        self.assertEqual([a['role'] for a in document['articles']], ['decision', 'signature', 'duree', 'publicite', 'recours', 'execution'])
        self.assertIn('tant qu’elle n’est pas rapportée', document['articles'][2]['texte'])
        self.assertTrue(any('précises' in p for p in document['points']))

    def test_etat_civil_hors_mariage_et_transmis_au_procureur(self):
        document = self.assembler(ETAT_CIVIL, decisions=('Mme Julie Perrin, rédactrice territoriale, reçoit délégation des fonctions d’officier d’état civil à compter du 1er janvier 2027.',))
        self.assertIn('L2122-32 et R2122-10', document['visas'][0])
        textes = ' '.join(a['texte'] for a in document['articles'])
        self.assertIn('célébration des mariages', textes)
        self.assertIn('procureur de la République', textes)

    def test_signature_avec_mention(self):
        document = self.assembler(SIGNATURE, decisions=('Délégation de signature est donnée à Mme Nadia Fontaine, directrice générale des services, pour les bons de commande et les courriers administratifs.',))
        self.assertIn('L2122-19 et L2122-20', document['visas'][0])
        self.assertIn('Pour le maire et par délégation', ' '.join(a['texte'] for a in document['articles']))


class ConversationTests(Base):
    def test_projet_complet_avec_word(self):
        with patch.object(llm_backend, 'chat', return_value=completion()):
            statut, reponse = self.appel('POST', '/api/chat', {'question': FONCTION, 'mode': 'libre'})
        self.assertEqual(statut, 200)
        self.assertEqual(reponse['artifact']['kind'], 'arrete_delegation')
        self.assertEqual([s['article'] for s in reponse['artifact']['legal']['sources']], ['L2122-18', 'L2122-20', 'L2131-1', 'L2131-2', 'R421-1'])
        from docx import Document
        texte = '\n'.join(p.text for p in Document(io.BytesIO(self.appel('GET', reponse['artifact']['download'])[1])).paragraphs)
        self.assertIn('M. Paul Lambert, deuxième adjoint, est délégué', texte)
        self.assertEqual(self.appel('POST', f"/api/actes/{reponse['artifact']['id']}/numero", {})[1]['titre'][:9], 'Arrêté n°')


if __name__ == '__main__':
    unittest.main()
