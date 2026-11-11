# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Repérage des données personnelles avant publication, et masquage."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from territoria import anonymisation, publication


def trouves(texte, categorie):
    return [p['texte'] for p in anonymisation.regles(texte) if p['categorie'] == categorie]


def ia(personnes):
    """IA simulée : rend la réponse de llm_backend.chat pour la liste de personnes donnée."""
    vus = []

    def demander(requete):
        vus.append(requete)
        return {'choices': [{'message': {'content': json.dumps({'personnes': personnes})}}]}
    return demander, vus


class ReglesTests(unittest.TestCase):
    def test_courriel(self):
        self.assertEqual(trouves('Écrire à jean.martin@exemple.fr avant lundi.', 'courriel'), ['jean.martin@exemple.fr'])
        self.assertEqual(trouves('Le site mairie.fr est à jour.', 'courriel'), [])

    def test_telephone(self):
        texte = 'Joindre le 06 12 34 56 78, le +33 6 12 34 56 78 ou le 01.23.45.67.89.'
        self.assertEqual(trouves(texte, 'telephone'), ['06 12 34 56 78', '+33 6 12 34 56 78', '01.23.45.67.89'])
        self.assertEqual(trouves('Vu la délibération n° 2026-0412 du 3 mars 2026 ; 150 000 euros.', 'telephone'), [])

    def test_adresse(self):
        self.assertEqual(trouves('demeurant 12 rue des Lilas à Démoville', 'adresse'), ['12 rue des Lilas'])
        self.assertEqual(trouves('au 3 bis, avenue Jean-Jaurès, 2e étage', 'adresse'), ['3 bis, avenue Jean-Jaurès'])
        self.assertEqual(trouves('Article 2 : la rue est fermée.', 'adresse'), [])

    def test_naissance(self):
        self.assertEqual(trouves('M. Martin, né le 3 mars 1980 à Lyon', 'naissance'), ['né le 3 mars 1980'])
        self.assertEqual(trouves('Mme Durand, née le 03/03/1980', 'naissance'), ['née le 03/03/1980'])
        self.assertEqual(trouves('Délibération prise le 3 mars 2026', 'naissance'), [])

    def test_nir(self):
        self.assertEqual(trouves('n° de sécurité sociale 1 80 03 75 116 004 23', 'nir'), ['1 80 03 75 116 004 23'])
        self.assertEqual(trouves('SIRET 21860194600013', 'nir'), [])

    def test_iban(self):
        self.assertEqual(trouves('Virement sur FR76 3000 6000 0112 3456 7890 189.', 'iban'), ['FR76 3000 6000 0112 3456 7890 189'])

    def test_immatriculation(self):
        self.assertEqual(trouves('le véhicule AB-123-CD est enlevé', 'immatriculation'), ['AB-123-CD'])
        self.assertEqual(trouves('Arrêté n° 2026-042', 'immatriculation'), [])

    def test_regles_cochees(self):
        self.assertTrue(all(p['coche'] and p['origine'] == 'regle' for p in anonymisation.regles('Tél. 06 12 34 56 78')))

    def test_categories_alignees_sur_la_table(self):
        self.assertEqual(set(anonymisation.LIBELLES), set(publication.CATEGORIES))


class IATests(unittest.TestCase):
    TEXTE = 'Vu la demande de M. Jean Martin ; sur le rapport de Mme Claire Dubois, maire adjointe.'

    def test_ia_nom_absent_ecarte(self):
        demander, _ = ia([{'texte': 'Jean Martin', 'role': 'particulier'}, {'texte': 'Paul Inventé', 'role': 'particulier'}])
        self.assertEqual([p['texte'] for p in anonymisation.noms_ia(self.TEXTE, demander)], ['Jean Martin', 'Martin'])

    def test_ia_elu_decoche(self):
        demander, vus = ia([{'texte': 'Jean Martin', 'role': 'particulier'}, {'texte': 'Claire Dubois', 'role': 'elu'}])
        passages = {p['texte']: p for p in anonymisation.noms_ia(self.TEXTE, demander)}
        self.assertTrue(passages['Jean Martin']['coche'])
        self.assertFalse(passages['Claire Dubois']['coche'])
        self.assertEqual(passages['Jean Martin']['origine'], 'ia')
        self.assertEqual(vus[0]['response_format']['json_schema']['strict'], True)

    def test_ia_indisponible_avertit(self):
        def panne(requete):
            raise RuntimeError('Serveur IA injoignable.')
        passages, avertissement = anonymisation.reperer('Tél. 06 12 34 56 78, M. Jean Martin', panne)
        self.assertEqual([p['categorie'] for p in passages], ['telephone'])
        self.assertEqual(avertissement, 'Les noms n’ont pas été repérés automatiquement : relisez l’acte en entier.')

    def test_reperer_dedoublonne(self):
        demander, _ = ia([{'texte': 'Jean Martin', 'role': 'particulier'}, {'texte': 'Jean Martin', 'role': 'particulier'}])
        passages, avertissement = anonymisation.reperer(self.TEXTE, demander)
        self.assertEqual([p['texte'] for p in passages], ['Jean Martin', 'Martin'])
        self.assertIsNone(avertissement)


class MasquerTests(unittest.TestCase):
    def test_masquer_toutes_les_occurrences_sans_casse(self):
        # Review Focus 2 : un nom écrit autrement plus bas est masqué aussi.
        texte = 'M. Jean Martin demande… Article 2 : M. JEAN MARTIN est informé. Copie à jean martin.'
        masque = anonymisation.masquer(texte, [{'texte': 'Jean Martin', 'categorie': 'nom', 'origine': 'ia', 'coche': True}])
        self.assertEqual(masque, 'M. [nom masqué] demande… Article 2 : M. [nom masqué] est informé. Copie à [nom masqué].')

    def test_masquer_chevauchements_fusionnes(self):
        # Review Focus 1 : un nom et une adresse qui se touchent ne laissent aucun morceau visible.
        texte = 'Vu la demande de Jean Martin, 12 rue des Lilas ; accord.'
        passages = [{'texte': 'Martin, 12 rue', 'categorie': 'manuel', 'origine': 'manuel', 'coche': True},
                    {'texte': '12 rue des Lilas', 'categorie': 'adresse', 'origine': 'regle', 'coche': True},
                    {'texte': 'Jean Martin', 'categorie': 'nom', 'origine': 'ia', 'coche': True}]
        masque = anonymisation.masquer(texte, passages)
        for morceau in ('Jean', 'Martin', '12', 'Lilas'):
            self.assertNotIn(morceau, masque)
        self.assertTrue(masque.startswith('Vu la demande de ['))
        self.assertTrue(masque.endswith('] ; accord.'))

    def test_decoche_non_masque(self):
        texte = 'Sur le rapport de Claire Dubois.'
        self.assertEqual(anonymisation.masquer(texte, [{'texte': 'Claire Dubois', 'categorie': 'nom', 'origine': 'ia', 'coche': False}]), texte)


class RelectureFinaleTests(unittest.TestCase):
    """Relecture finale de la branche : ce qui laissait passer une donnée cochée, ou masquait du texte public."""
    def test_adresse_ne_traverse_pas_les_blocs(self):
        # C1 : objet puis premier paragraphe, joints par un retour à la ligne.
        texte = 'Cession à M. Jean Martin, 12 rue des Lilas\nLe Conseil municipal,\ndemeurant 3 avenue Foch\nVu le code,'
        self.assertEqual(trouves(texte, 'adresse'), ['12 rue des Lilas', '3 avenue Foch'])

    def test_adresse_sans_personne_proposee_decochee(self):
        # I10 : l'adresse qui fait l'objet d'un arrêté de police reste visible par défaut.
        proposes = {p['texte']: p['coche'] for p in anonymisation.regles(
            'Le stationnement est interdit au droit du 4 rue de la République. Vu la demande de Mme Durand, demeurant 12 rue des Lilas.')}
        self.assertEqual(proposes, {'4 rue de la République': False, '12 rue des Lilas': True})

    def test_masquage_par_mots_entiers(self):
        # I1 : « Martin » ne coupe ni « Martine » ni « Martinez », « Mai » ne masque pas le mois.
        passages = [{'texte': 'Martin', 'categorie': 'nom', 'origine': 'ia', 'coche': True},
                    {'texte': 'Mai', 'categorie': 'nom', 'origine': 'ia', 'coche': True}]
        masque = anonymisation.masquer('M. Martin, Mme Martine Martinez ; séance du 3 mai 2026 ; Mme MAI.', passages)
        self.assertEqual(masque, 'M. [nom masqué], Mme Martine Martinez ; séance du 3 mai 2026 ; Mme [nom masqué].')

    def test_nom_seul_en_minuscules_non_masque(self):
        passages = [{'texte': 'Petit', 'categorie': 'nom', 'origine': 'ia', 'coche': True}]
        self.assertEqual(anonymisation.masquer('M. PETIT entretient le petit patrimoine.', passages),
                         'M. [nom masqué] entretient le petit patrimoine.')

    def test_ia_ajoute_le_nom_de_famille_seul(self):
        # I2 : « Jean Martin » proposé par l'IA ; « M. MARTIN » plus bas doit être masqué aussi.
        texte = 'Vu la demande de M. Jean Martin. Article 2 : M. MARTIN est informé.'
        demander, _ = ia([{'texte': 'Jean Martin', 'role': 'particulier'}])
        passages, _ = anonymisation.reperer(texte, demander)
        self.assertNotIn('MARTIN', anonymisation.masquer(texte, passages))

    def test_ia_nom_avec_espacement_different_garde(self):
        # M2 : espace double ou insécable dans le texte, nom rendu avec une espace simple.
        demander, _ = ia([{'texte': 'Jean Martin', 'role': 'particulier'}])
        self.assertEqual([p['texte'] for p in anonymisation.noms_ia('Vu la demande de M. Jean\u00a0 Martin.', demander)][:1], ['Jean Martin'])

    def test_occurrences(self):
        self.assertEqual(anonymisation.occurrences('Jean  Martin et JEAN MARTIN', {'texte': 'Jean Martin', 'categorie': 'nom'}), 2)
        self.assertEqual(anonymisation.occurrences('Jean Martin', {'texte': 'Lilas\nLe Conseil', 'categorie': 'adresse'}), 0)


class VerificationFinaleTests(unittest.TestCase):
    """Dernière passe de vérification avant la démonstration publique."""
    def test_longue_suite_sans_arobase_reste_rapide(self):
        # Le motif du courriel était quadratique : un acte déposé d'un seul long mot figeait le serveur des heures.
        debut = time.perf_counter()
        anonymisation.regles('a' * 100_000)
        self.assertLess(time.perf_counter() - debut, 1.0)
        self.assertEqual(trouves('Écrire (jean.martin@exemple.fr) avant lundi.', 'courriel'), ['jean.martin@exemple.fr'])

    def test_ia_indisponible_regles_appliquees_une_seule_fois(self):
        def panne(requete):
            raise RuntimeError('Serveur IA injoignable.')
        with patch.object(anonymisation, 'regles', wraps=anonymisation.regles) as espion:
            anonymisation.reperer('Tél. 06 12 34 56 78', panne)
        self.assertEqual(espion.call_count, 1)


if __name__ == '__main__':
    unittest.main()
