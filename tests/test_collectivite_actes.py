# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Collectivité et signataire des arrêtés, de la demande à la trame RH."""
import tempfile
import unittest
from pathlib import Path

import arrete_conversation
import arrete_document
import branding
from docx import Document
from territoria import arretes_rh, deliberations


IDENTITE = 'Démoville (commune fictive)'
COMMUNAUTE = 'Communauté de communes du Val-Exemple'
NOMINATION = (f'Rédige un arrêté du président de la {COMMUNAUTE} portant nomination stagiaire '
              'de Mme Claire Exemple au grade d’adjoint administratif territorial au 1er décembre 2026.')


class CollectiviteActesTests(unittest.TestCase):
    def test_extraire_la_commune_du_maire(self):
        self.assertEqual(arrete_conversation.collectivity('Arrêté du maire de Villeneuve-Exemple portant interdiction de stationner.'),
                         'Commune de Villeneuve-Exemple')

    def test_extraire_la_communaute_avec_son_article(self):
        self.assertEqual(arrete_conversation.collectivity(NOMINATION), COMMUNAUTE)

    def test_extraire_la_commune_avant_une_relative(self):
        self.assertEqual(arrete_conversation.collectivity('Commune de Saint-Exemple-les-Bains qui interdit la circulation.'),
                         'Commune de Saint-Exemple-les-Bains')

    def test_extraire_la_commune_avant_un_participe(self):
        self.assertEqual(arrete_conversation.collectivity("Commune de Val-Exemple accordant une autorisation d'occupation temporaire."),
                         'Commune de Val-Exemple')

    def test_la_commune_demandee_prime_sur_le_profil(self):
        demande = 'Rédige un arrêté du maire de Villeneuve-Exemple interdisant le stationnement rue des Tilleuls pour travaux.'
        with tempfile.TemporaryDirectory() as tmp:
            dossier = Path(tmp)
            branding.save(dossier, {'collectivite': IDENTITE})
            donnees, relance = arrete_conversation.prepare(demande, None, dossier)
        self.assertEqual((relance, donnees['collectivite']), ('', 'Commune de Villeneuve-Exemple'))

    def test_la_commune_explicite_reste_reconnue(self):
        self.assertEqual(arrete_conversation.collectivity('Commune de Ville-Exemple-sur-Loire portant interdiction de stationner.'),
                         'Commune de Ville-Exemple-sur-Loire')

    def test_formes_du_nom_dans_les_deux_parcours(self):
        cas = (('Arrêté du maire de la Commune de Val-Exemple accordant une permission.', 'Commune de Val-Exemple'),
               ('Métropole du Val des Sources (EPCI).', 'Métropole du Val des Sources'),
               ('Communauté d’agglomération des Deux Vallées portant nomination.', 'Communauté d’agglomération des Deux Vallées'),
               ('Pour la commune d’Île-Exemple, interdire le stationnement.', 'Commune d’Île-Exemple'),
               ('La ville de testville interdisant le stationnement.', 'Commune de testville'),
               ('Il appartient au maire de prendre les mesures nécessaires.', ''),
               ('Demande au maire de me transmettre le projet.', ''),
               ('Demande au maire de bien vouloir accueillir cette manifestation.', ''),
               ('Arrêté du maire de saint-pierre.', 'Commune de saint-pierre'),
               ('Commune de La Roche d’Exemple portant interdiction.', 'Commune de La Roche d’Exemple'),
               ('Communauté de communes du Pays d’Exemple portant nomination.', 'Communauté de communes du Pays d’Exemple'),
               ('Commune de Saint‑Jean‑Exemple portant interdiction.', 'Commune de Saint‑Jean‑Exemple'),
               ('Commune de la roche bernard pour les travaux.', 'Commune de la roche bernard'),
               ('COMMUNE DE VAL-EXEMPLE DU 12 AU 15 NOVEMBRE.', 'Commune de VAL-EXEMPLE'),
               ('Remplace la commune de Val-Exemple par la commune de Saint-Exemple.', 'Commune de Saint-Exemple'),
               (f"L'autorité n'est pas le maire de la commune de Démoville mais le président de la {COMMUNAUTE}.", COMMUNAUTE),
               ('Les services de la commune préparent cet arrêté.', ''))
        for demande, attendu in cas:
            with self.subTest(demande=demande):
                self.assertEqual(arrete_conversation.collectivity(demande), attendu)
                self.assertEqual(deliberations.collectivite(demande), attendu)

    def test_nom_communal_avec_article_contracte_jusqu_au_lieu_de_signature(self):
        nom = arrete_conversation.collectivity('Arrêté du maire du Val-Exemple portant interdiction de stationner.')
        self.assertEqual(nom, 'Commune du Val-Exemple')
        self.assertEqual(arrete_document._commune(nom), ('Le Maire de la commune du Val-Exemple', 'Le Val-Exemple'))

    def test_la_nomination_et_sa_correction_donnent_le_president(self):
        precedent = {'kind': 'arrete_rh', 'inputs': {'contexte': NOMINATION, 'collectivite': IDENTITE}}
        correction = (f"Modifie ce projet : l'autorité n'est pas le maire de Démoville mais le président de la {COMMUNAUTE}.")
        contenu = {'objet': 'Nomination stagiaire', 'considerants': [], 'decisions': ['Mme Claire Exemple est nommée stagiaire.']}
        for demande, anterieur in ((NOMINATION, None), (correction, precedent)):
            with self.subTest(demande=demande):
                donnees, relance = arretes_rh.preparer(demande, anterieur, IDENTITE)
                self.assertEqual(relance, '')
                acte = arretes_rh.assembler(donnees, contenu, {})
                self.assertEqual((acte['collectivite'], acte['autorite'], acte['signatures'][0]),
                                 (COMMUNAUTE, 'Le Président de la communauté de communes du Val-Exemple',
                                  'Le Président, [nom et signature]'))
                self.assertTrue(acte['certification'].startswith('Le Président certifie'))
                with tempfile.TemporaryDirectory() as tmp:
                    fichier = Path(tmp) / 'projet.docx'
                    arrete_document.create_word(fichier, acte)
                    texte = '\n'.join(p.text for p in Document(fichier).paragraphs)
                self.assertIn('Le Président de la communauté de communes du Val-Exemple', texte)
                self.assertNotIn('Démoville', texte)


if __name__ == '__main__':
    unittest.main()
