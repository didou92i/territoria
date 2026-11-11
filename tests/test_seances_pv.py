# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from territoria import seances_pv  # noqa: E402


class VoteTests(unittest.TestCase):
    def test_phrases_de_vote(self):
        cas = [({'mode': 'unanimite'}, "Adopté à l'unanimité."),
               ({'mode': 'decompte', 'pour': 15, 'contre': 2, 'abstentions': 1}, 'Adopté par 15 voix pour, 2 contre et 1 abstention.'),
               ({'mode': 'decompte', 'pour': 15, 'contre': 0, 'abstentions': 2}, 'Adopté par 15 voix pour et 2 abstentions.'),
               ({'mode': 'decompte', 'pour': 3, 'contre': 5, 'abstentions': 0}, 'Rejeté par 3 voix pour et 5 contre.'),
               ({'mode': 'decompte', 'pour': 5, 'contre': 5, 'abstentions': 0}, 'Partage égal des voix (5 pour, 5 contre) : [à compléter].'),
               ({'mode': 'decompte', 'pour': 1, 'contre': 0, 'abstentions': 0}, 'Adopté par 1 voix pour.'),
               (None, '[à compléter]')]
        for vote, phrase in cas:
            with self.subTest(vote=vote):
                self.assertEqual(seances_pv.phrase_vote(vote), phrase)

    def test_scrutin_public_nomme_les_votants(self):
        vote = {'mode': 'decompte', 'pour': 2, 'contre': 1, 'abstentions': 0,
                'public': {'pour': ['A', 'B'], 'contre': ['C'], 'abstention': []}}
        self.assertEqual(seances_pv.phrase_vote(vote), 'Adopté par 2 voix pour et 1 contre. Ont voté pour : A, B. A voté contre : C.')
        vote = {'mode': 'decompte', 'pour': 1, 'contre': 0, 'abstentions': 2, 'public': {'pour': ['A'], 'abstention': ['D', 'E']}}
        self.assertEqual(seances_pv.phrase_vote(vote), 'Adopté par 1 voix pour et 2 abstentions. A voté pour : A. Se sont abstenus : D, E.')


class QuorumTests(unittest.TestCase):
    def test_quorum(self):
        self.assertEqual(seances_pv.quorum({'membres_en_exercice': 19, 'presents': ['x'] * 10}),
                         {'presents': 10, 'membres': 19, 'atteint': True})
        self.assertFalse(seances_pv.quorum({'membres_en_exercice': 19, 'presents': ['x'] * 9})['atteint'])
        self.assertFalse(seances_pv.quorum({'membres_en_exercice': 20, 'presents': ['x'] * 10})['atteint'])
        self.assertEqual(seances_pv.quorum({'presents': ['x']}), {'presents': 1, 'membres': None, 'atteint': None})
        self.assertEqual(seances_pv.quorum({}), {'presents': 0, 'membres': None, 'atteint': None})


SEGMENTS = [
    {'id': 1, 'debut': 0.0, 'fin': 4.0, 'etiquette': 'Intervenant 1', 'texte': 'La séance est ouverte ; la cantine attendra.'},
    {'id': 2, 'debut': 5.0, 'fin': 12.0, 'etiquette': 'Intervenant 1', 'texte': 'Point numéro 1 : budget primitif 2027.'},
    {'id': 3, 'debut': 13.0, 'fin': 30.0, 'etiquette': 'Intervenant 2', 'texte': 'Le budget s’équilibre à 3 000 000 euros.'},
    {'id': 4, 'debut': 60.0, 'fin': 66.0, 'etiquette': 'Intervenant 1', 'texte': 'Point 2, tarifs de la cantine scolaire.'},
    {'id': 5, 'debut': 67.0, 'fin': 80.0, 'etiquette': 'Intervenant 2', 'texte': 'Les tarifs restent inchangés.'},
]
NOMS = {'Intervenant 1': 'M. le Maire', 'Intervenant 2': ''}


class DebutsTests(unittest.TestCase):
    def test_debut_repere_par_numero_de_point(self):
        points = [{'rang': 1, 'titre': 'Budget'}, {'rang': 2, 'titre': 'Cantine'}]
        self.assertEqual(seances_pv.proposer_debuts(SEGMENTS, points), [5.0, 60.0])

    def test_debut_repere_par_les_mots_du_titre(self):
        segments = [dict(s, texte=s['texte'].replace('Point numéro 1 : ', '').replace('Point 2, ', '')) for s in SEGMENTS]
        points = [{'rang': 1, 'titre': 'Vote du budget primitif 2027'}, {'rang': 2, 'titre': 'Tarifs de la cantine scolaire'}]
        self.assertEqual(seances_pv.proposer_debuts(segments, points), [5.0, 60.0])

    def test_debut_repere_par_numero_de_deliberation(self):
        segments = SEGMENTS + [{'id': 6, 'debut': 90.0, 'fin': 95.0, 'etiquette': 'Intervenant 1', 'texte': 'Délibération D2026-052, la voici.'}]
        points = [{'rang': 7, 'titre': 'Autre sujet', 'deliberation_numero': 'D2026-052'}]
        self.assertEqual(seances_pv.proposer_debuts(segments, points), [90.0])

    def test_debut_absent_reste_nul(self):
        points = [{'rang': 1, 'titre': 'Budget'}, {'rang': 5, 'titre': 'Jumelage avec Bielefeld'}, {'rang': 2, 'titre': 'Cantine'}]
        self.assertEqual(seances_pv.proposer_debuts(SEGMENTS, points), [5.0, None, 60.0])

    def test_debuts_apres_le_point_precedent(self):
        # « la cantine » est dite à l'ouverture (0 s), avant le point 1 : le point « cantine » commence après lui.
        points = [{'rang': 1, 'titre': 'Budget primitif'}, {'rang': 3, 'titre': 'Tarifs cantine scolaire'}]
        self.assertEqual(seances_pv.proposer_debuts(SEGMENTS, points), [5.0, 60.0])


class PassagesTests(unittest.TestCase):
    def test_passages_par_point_dans_l_ordre_des_debuts(self):
        points = [{'rang': 1, 'titre': 'Cantine', 'debut': 60.0}, {'rang': 2, 'titre': 'Budget', 'debut': 5.0}]
        self.assertEqual(seances_pv.passages_par_point(SEGMENTS, points, NOMS), [
            ['[00:01:00] M. le Maire : Point 2, tarifs de la cantine scolaire.', '[00:01:07] Intervenant 2 : Les tarifs restent inchangés.'],
            ['[00:00:05] M. le Maire : Point numéro 1 : budget primitif 2027.', '[00:00:13] Intervenant 2 : Le budget s’équilibre à 3 000 000 euros.'],
        ])

    def test_point_sans_debut_sans_passages(self):
        points = [{'rang': 1, 'titre': 'Budget', 'debut': None}, {'rang': 2, 'titre': 'Cantine', 'debut': 60.0}]
        self.assertEqual(seances_pv.passages_par_point(SEGMENTS, points, NOMS)[0], [])


A = '[à compléter]'
VOTE = {'mode': 'decompte', 'pour': 15, 'contre': 2, 'abstentions': 1}
PHRASE = 'Adopté par 15 voix pour, 2 contre et 1 abstention.'


def seance(**autres):
    base = {'titre': 'Conseil municipal du 3 novembre', 'type': 'conseil', 'date': '2026-11-03', 'heure': None, 'lieu': '',
            'details': {}, 'intervenants': [], 'redaction': {},
            'points': [{'rang': 1, 'titre': 'Budget primitif 2027', 'deliberation_numero': None, 'rapporteur': None, 'vote': None,
                        'scrutin_particulier': False, 'debut': None, 'teneur': None, 'erreur': None}]}
    base.update(autres)
    return base


SAISIE = {'heure': '19:00', 'lieu': 'Mairie',
          'details': {'president': 'M. Martin', 'secretaires': ['Mme Durand'], 'membres_en_exercice': 19,
                      'presents': ['M. Martin', 'Mme Durand', 'M. Petit'], 'representes': [{'mandant': 'M. Leroy', 'mandataire': 'M. Martin'}],
                      'absents': [], 'heure_cloture': '21:30', 'arrete_le': '2026-12-15'},
          'points': [{'rang': 1, 'titre': 'Budget primitif 2027', 'deliberation_numero': 'D2026-052', 'rapporteur': 'Mme Durand',
                      'rapport': 'Note de synthèse sur le budget', 'vote': VOTE, 'scrutin_particulier': True, 'debut': 5.0, 'teneur': ['Mme Durand présente le budget.'], 'erreur': None},
                     {'rang': 2, 'titre': 'Questions diverses', 'deliberation_numero': None, 'rapporteur': None, 'vote': None,
                      'scrutin_particulier': False, 'debut': 60.0, 'teneur': ['Aucune question.'], 'erreur': None}]}


def textes(contenu):
    return [texte for _, texte in contenu]


class ProcesVerbalTests(unittest.TestCase):
    def test_toutes_les_mentions_l2121_15(self):
        contenu = seances_pv.contenu_proces_verbal(seance())
        lignes = textes(contenu)
        for mention in seances_pv.MENTIONS_L2121_15:
            with self.subTest(mention=mention):
                self.assertTrue(any(mention in ligne for ligne in lignes), mention)
        apres = {lignes[i]: lignes[i + 1] for i in range(len(lignes) - 1) if contenu[i][0] == 'section'}
        for section in ('Président', 'Secrétaire(s) de séance', 'Membres représentés', 'Quorum'):
            self.assertEqual(apres[section], A, section)
        self.assertEqual(apres['Date et heure de la séance'], f'3 novembre 2026 à {A} — {A}')
        self.assertIn(f'Rapporteur : {A}', lignes)
        self.assertIn(f'Rapport : {A}', lignes)
        self.assertIn(f'Membres en exercice : {A}. Présents : {A}.', lignes)
        self.assertIn(f'Résultat : {A}', lignes)
        self.assertEqual(lignes[-3:], [f'Procès-verbal arrêté lors de la séance du {A}.', 'Le maire', 'Le ou les secrétaires de séance'])

    def test_mentions_saisies_reprises(self):
        lignes = textes(seances_pv.contenu_proces_verbal(seance(**SAISIE)))
        for attendu in ('3 novembre 2026 à 19:00 — Mairie', 'M. Martin', 'Mme Durand', 'M. Leroy (pouvoir à M. Martin)',
                        "Le quorum n'est pas atteint (3 présents sur 19 membres en exercice).", 'Absents : aucun.',
                        'Séance levée à 21:30.', 'Procès-verbal arrêté lors de la séance du 15 décembre 2026.',
                        '1. Budget primitif 2027 (délibération n° D2026-052)', '2. Questions diverses',
                        'Rapport : Note de synthèse sur le budget'):
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, lignes)
        self.assertNotIn(A, lignes)

    def test_adoption_inconnue_ou_partagee_a_completer(self):
        # Relecture finale : une délibération sans vote saisi (ou à partage égal) était déclarée adoptée, ou passée sous silence.
        points = [dict(SAISIE['points'][0], rang=1, deliberation_numero='D1', titre='Sans vote', vote=None),
                  dict(SAISIE['points'][0], rang=2, deliberation_numero='D2', titre='Partage',
                       vote={'mode': 'decompte', 'pour': 5, 'contre': 5, 'abstentions': 0}),
                  dict(SAISIE['points'][0], rang=3, deliberation_numero='D3', titre='Rejet',
                       vote={'mode': 'decompte', 'pour': 2, 'contre': 9, 'abstentions': 0}),
                  dict(SAISIE['points'][0], rang=4, deliberation_numero='D4', titre='Adopté', vote={'mode': 'unanimite'})]
        lignes = textes(seances_pv.contenu_proces_verbal(seance(**dict(SAISIE, points=points))))
        debut, fin = lignes.index('Délibérations adoptées'), lignes.index('Teneur des discussions')
        self.assertEqual(lignes[debut + 1:fin], [f'n° D1 — Sans vote — {A}', f'n° D2 — Partage — {A}', 'n° D4 — Adopté'])

    def test_quorum_sans_presents_inconnu(self):
        self.assertIsNone(seances_pv.quorum({'membres_en_exercice': 19})['atteint'])
        lignes = textes(seances_pv.contenu_proces_verbal(seance(details={'membres_en_exercice': 19})))
        self.assertIn(f'Membres en exercice : 19. Présents : {A}.', lignes)
        self.assertEqual(lignes[lignes.index('Quorum') + 1], A)

    def test_phrases_de_vote_dans_le_pv_et_la_liste(self):
        pv = textes(seances_pv.contenu_proces_verbal(seance(**SAISIE)))
        self.assertIn(f'Résultat : {PHRASE}', pv)
        self.assertIn(f'1. Budget primitif 2027 : {PHRASE}', pv)
        liste = textes(seances_pv.contenu_liste_deliberations(seance(**SAISIE)))
        self.assertIn(f'n° D2026-052 — Budget primitif 2027 — {PHRASE}', liste)

    def test_teneur_du_modele_seulement_dans_sa_section(self):
        lignes = textes(seances_pv.contenu_proces_verbal(seance(**SAISIE)))
        debut, fin = lignes.index('Teneur des discussions'), lignes.index('Demandes de scrutin particulier')
        self.assertEqual([i for i, ligne in enumerate(lignes) if ligne == 'Mme Durand présente le budget.'], [lignes.index('Mme Durand présente le budget.')])
        self.assertTrue(debut < lignes.index('Mme Durand présente le budget.') < fin)
        self.assertIn('1. Budget primitif 2027', lignes[fin:])  # le point qui a demandé un scrutin particulier


class CompteRenduTests(unittest.TestCase):
    def test_compte_rendu_participants_nommes(self):
        compte_rendu = {'points': [{'titre': 'Budget', 'synthese': 'Présentation du budget.'}], 'decisions': ['Le budget est validé.'],
                        'actions': [{'qui': 'Mme Durand', 'quoi': 'Envoyer le document.', 'echeance': ''},
                                    {'qui': 'M. Petit', 'quoi': 'Relancer.', 'echeance': 'vendredi'}]}
        lignes = textes(seances_pv.contenu_compte_rendu(seance(
            type='reunion', heure='10:00', lieu='Salle du conseil', redaction={'compte_rendu': compte_rendu},
            intervenants=[{'etiquette': 'Intervenant 1', 'nom': 'M. le Maire'}, {'etiquette': 'Intervenant 2', 'nom': ''}])))
        self.assertEqual(lignes[0], 'Compte rendu — Conseil municipal du 3 novembre')
        for attendu in ('Réunion du 3 novembre 2026 à 10:00 — Salle du conseil', 'M. le Maire', 'Budget — Présentation du budget.',
                        'Le budget est validé.', 'Mme Durand — Envoyer le document.', 'M. Petit — Relancer. — échéance : vendredi'):
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, lignes)
        self.assertNotIn('Intervenant 2', ' '.join(lignes))


class ListeTests(unittest.TestCase):
    def test_liste_avec_numeros_objets_resultats(self):
        lignes = textes(seances_pv.contenu_liste_deliberations(seance(**SAISIE)))
        self.assertEqual(lignes[0], 'Liste des délibérations examinées — séance du 3 novembre 2026')
        self.assertEqual([l for l in lignes if l.startswith('n° ')], [f'n° D2026-052 — Budget primitif 2027 — {PHRASE}'])
        self.assertEqual(lignes[-1], "Liste à afficher à la mairie et à mettre en ligne sur le site internet de la commune dans un "
                                     "délai d'une semaine (article L2121-25 du CGCT).")


if __name__ == '__main__':
    unittest.main()
