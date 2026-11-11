# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Publication des actes : réglages de la collectivité, nomenclature ACTES, table des publications et statuts."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arrete_document
import branding
from territoria import nomenclature, publication
from territoria.base import migrer


class ReglagesTests(unittest.TestCase):
    def setUp(self):
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        self.donnees = Path(dossier.name)

    def test_siret_valide_enregistre(self):
        resultat = branding.save(self.donnees, {'collectivite': 'Ville de Poitiers', 'siret': '218 601 946 00013'})
        self.assertEqual(resultat['siret'], '21860194600013')
        self.assertEqual(branding.profile(self.donnees)['siret'], '21860194600013')

    def test_siret_cle_fausse_refuse(self):
        with self.assertRaises(ValueError) as erreur:
            branding.save(self.donnees, {'collectivite': 'Ville', 'siret': '21860194600014'})
        self.assertEqual(str(erreur.exception), 'SIRET invalide : 14 chiffres attendus.')

    def test_adresse_actes(self):
        resultat = branding.save(self.donnees, {'collectivite': 'Ville', 'adresse_actes': 'https://www.mairie.fr/actes'})
        self.assertEqual(resultat['adresse_actes'], 'https://www.mairie.fr/actes/')
        for mauvaise in ('http://x.fr/actes/', 'https://u:p@x.fr/', 'ftp://x.fr/', 'https://'):
            with self.subTest(adresse=mauvaise), self.assertRaises(ValueError) as erreur:
                branding.save(self.donnees, {'collectivite': 'Ville', 'adresse_actes': mauvaise})
            self.assertEqual(str(erreur.exception), 'Adresse des actes invalide : https://… attendue.')

    def test_profil_ancien_sans_siret(self):
        (self.donnees / 'identite').mkdir()
        (self.donnees / 'identite' / 'profil.json').write_text(json.dumps({'collectivite': 'Ville', 'logo': ''}))
        profil = branding.profile(self.donnees)
        self.assertEqual((profil['siret'], profil['adresse_actes']), ('', ''))

    def test_identite_enregistree_sans_ces_champs_les_garde(self):
        # L'écran « Logo et collectivité » n'envoie que le nom et le logo : le SIRET ne doit pas disparaître.
        branding.save(self.donnees, {'collectivite': 'Ville', 'siret': '21860194600013',
                                     'adresse_actes': 'https://www.mairie.fr/actes/'})
        branding.save(self.donnees, {'collectivite': 'Ville de Poitiers', 'logo': ''})
        profil = branding.profile(self.donnees)
        self.assertEqual((profil['collectivite'], profil['siret'], profil['adresse_actes']),
                         ('Ville de Poitiers', '21860194600013', 'https://www.mairie.fr/actes/'))

    def test_nomenclature(self):
        self.assertEqual(nomenclature.nom('7.1'), 'Décisions budgétaires (B.P., D.M., C.A. …)')
        codes = [m['code'] for m in nomenclature.matieres()]
        self.assertEqual(len(codes), 57)
        self.assertEqual(len(codes), len(set(codes)))
        with self.assertRaises(ValueError) as erreur:
            nomenclature.nom('99.9')
        self.assertEqual(str(erreur.exception), 'Matière inconnue de la nomenclature ACTES.')

    def test_nom_scdl(self):
        # Schéma « Délibérations » 2.1.6 : niveau 1 puis niveau 2, en minuscules accentuées, sans virgule ni parenthèse.
        self.assertEqual(nomenclature.nom_scdl('8.4'), 'domaines de compétences par thèmes/aménagement du territoire')
        self.assertEqual(nomenclature.nom_scdl('7.1'), 'finances locales/décisions budgétaires')
        self.assertEqual(nomenclature.nom_scdl('8.5'), 'domaines de compétences par thèmes/politique de la ville habitat logement')


class PublicationTests(unittest.TestCase):
    """Table des publications : création, lecture, statuts a_relire → valide → publie."""
    def setUp(self):
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        self.donnees = Path(dossier.name)
        self.base = self.donnees / 'territoire.sqlite3'
        migrer.appliquer(self.base)

    def creer(self, **valeurs):
        donnees = {'type': 'deliberation', 'numero': 'D2026-012', 'date_acte': '2026-10-14', 'objet': 'Cession d’un terrain',
                   'paragraphes': ['Le conseil municipal,', 'Vu la demande de M. Jean Martin, 12 rue des Lilas ;'],
                   'source': 'depot', 'matiere_code': '3.2', 'par': 'u1'} | valeurs
        return publication.creer(self.base, self.donnees, **donnees)

    def test_creer_puis_lire(self):
        acte = self.creer()
        lu = publication.lire(self.base, self.donnees, acte['id'])
        self.assertEqual((lu['statut'], lu['numero'], lu['matiere_code']), ('a_relire', 'D2026-012', '3.2'))
        self.assertEqual(lu['paragraphes'][1], 'Vu la demande de M. Jean Martin, 12 rue des Lilas ;')
        self.assertEqual(lu['passages'], [])
        dossier = self.donnees / 'publication' / acte['id']
        self.assertEqual(stat.S_IMODE(dossier.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((dossier / 'texte.json').stat().st_mode), 0o600)

    def test_type_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            self.creer(type='decret')

    def test_deliberation_sans_matiere_refusee(self):
        with self.assertRaises(ValueError) as erreur:
            self.creer(matiere_code='')
        self.assertEqual(str(erreur.exception), 'Choisissez la matière de la délibération (nomenclature ACTES).')
        self.creer(type='arrete', matiere_code='')  # un arrêté n'a pas de matière

    def test_paragraphes_du_registre(self):
        ident = 'a' * 32
        (self.donnees / 'arretes').mkdir()
        document = arrete_document.assemble({'collectivite': 'Commune de Démoville'},
                                            {'objet': 'Stationnement rue des Essais', 'considerant': 'Considérant le déménagement.',
                                             'articles': [], 'destinataires': 'Services compétents'})
        arrete_document.create_word(self.donnees / 'arretes' / f'{ident}.docx', document)
        paragraphes = publication.paragraphes_du_registre(self.donnees, ident)
        self.assertTrue(any('Considérant le déménagement.' in p for p in paragraphes))
        self.assertNotIn('', paragraphes)
        with self.assertRaises(LookupError):
            publication.paragraphes_du_registre(self.donnees, 'b' * 32)

    def test_modifier_apres_validation_repasse_a_relire(self):
        # Review Focus 5 : une validation ne couvre que les passages qu'elle a vus.
        acte = self.creer()
        passage = {'texte': 'Jean Martin', 'categorie': 'nom', 'origine': 'ia', 'coche': True}
        publication.enregistrer_reperage(self.base, acte['id'], [passage], None)
        publication.valider(self.base, self.donnees, acte['id'], 'referent')
        lu = publication.enregistrer_passages(self.base, acte['id'], [passage | {'coche': False}])
        self.assertEqual((lu['statut'], lu['valide_par'], lu['valide_le']), ('a_relire', '', ''))

    def test_publie_ne_se_modifie_plus(self):
        acte = self.creer()
        publication.enregistrer_reperage(self.base, acte['id'], [], None)
        publication.valider(self.base, self.donnees, acte['id'], 'referent')
        publication.enregistrer_dossier(self.base, self.donnees, acte['id'], 'e' * 64, b'%PDF', '2026-11-03')
        publication.marquer_publie(self.base, acte['id'])
        with self.assertRaises(PermissionError) as erreur:
            publication.enregistrer_passages(self.base, acte['id'], [])
        self.assertEqual(str(erreur.exception), 'Cet acte est publié : il ne se modifie plus.')

    def test_marquer_publie_exige_valide(self):
        acte = self.creer()
        with self.assertRaises(ValueError):
            publication.marquer_publie(self.base, acte['id'])

    def test_passage_de_categorie_inconnue_refuse(self):
        acte = self.creer()
        with self.assertRaises(ValueError):
            publication.enregistrer_passages(self.base, acte['id'], [{'texte': 'x', 'categorie': 'autre', 'origine': 'ia', 'coche': True}])

    def test_liste_par_proprietaire(self):
        self.creer(par='u1')
        self.creer(par='u2', numero='D2026-013')
        self.assertEqual([a['numero'] for a in publication.liste(self.base, 'u2')], ['D2026-013'])
        self.assertEqual(len(publication.liste(self.base)), 2)


class RelectureFinalePublicationTests(PublicationTests):
    """Relecture finale : validation sûre, numéros uniques, suppression, date de mise en ligne, Word fidèle."""
    def passage(self, texte='Jean Martin', coche=True):
        return {'texte': texte, 'categorie': 'nom', 'origine': 'ia', 'coche': coche}

    def test_valider_exige_le_reperage(self):
        acte = self.creer()
        with self.assertRaises(ValueError) as erreur:
            publication.valider(self.base, self.donnees, acte['id'], 'referent')
        self.assertEqual(str(erreur.exception), 'Lancez d’abord le repérage des données personnelles.')

    def test_valider_refuse_un_passage_introuvable(self):
        # C1 : un passage coché qui ne figure nulle part ne masquerait rien.
        acte = self.creer()
        publication.enregistrer_reperage(self.base, acte['id'], [self.passage('12 rue des Lilas\nLe Conseil')], None)
        with self.assertRaises(ValueError) as erreur:
            publication.valider(self.base, self.donnees, acte['id'], 'referent')
        self.assertIn('introuvable dans le texte', str(erreur.exception))

    def test_avertissement_conserve(self):
        # I3 : celui qui valide voit que les noms n'ont pas été cherchés.
        acte = self.creer()
        lu = publication.enregistrer_reperage(self.base, acte['id'], [], 'Les noms n’ont pas été repérés automatiquement : relisez l’acte en entier.')
        self.assertTrue(lu['avertissement'].startswith('Les noms n’ont pas été repérés'))
        self.assertTrue(lu['repere_le'])

    def test_numero_en_double_refuse(self):
        # I9 : « D2026/012 » et « D2026-012 » donneraient le même PDF.
        self.creer()
        with self.assertRaises(ValueError) as erreur:
            self.creer(numero='D2026/012')
        self.assertEqual(str(erreur.exception), 'Ce numéro est déjà dans les actes à publier.')

    def test_supprimer(self):
        acte = self.creer()
        publication.supprimer(self.base, self.donnees, acte['id'])
        self.assertEqual(publication.liste(self.base), [])
        self.assertFalse((self.donnees / 'publication' / acte['id']).exists())

    def test_supprimer_un_acte_publie_refuse(self):
        acte = self.publie()
        with self.assertRaises(PermissionError):
            publication.supprimer(self.base, self.donnees, acte['id'])

    def publie(self, date='2026-11-03'):
        acte = self.creer()
        publication.enregistrer_reperage(self.base, acte['id'], [self.passage()], None)
        publication.valider(self.base, self.donnees, acte['id'], 'referent')
        publication.enregistrer_dossier(self.base, self.donnees, acte['id'], 'e' * 64, b'%PDF', date)
        return publication.marquer_publie(self.base, acte['id'])

    def test_retrait_d_un_acte_publie_garde_la_preuve(self):
        acte = self.publie()
        retire = publication.retirer(self.base, acte['id'], '  Publié par erreur : donnée personnelle visible.  ', 'referent')
        self.assertEqual((retire['statut'], retire['retire_motif'], retire['retire_par']),
                         ('retire', 'Publié par erreur : donnée personnelle visible.', 'referent'))
        self.assertTrue(retire['retire_le'])
        self.assertEqual(retire['publie_le'], acte['publie_le'])
        self.assertEqual((self.donnees / 'publication' / acte['id'] / 'publie.pdf').read_bytes(), b'%PDF')

    def test_retrait_exige_un_acte_publie_et_un_motif(self):
        acte = self.publie()
        for motif in ('', '   '):
            with self.assertRaises(ValueError) as erreur:
                publication.retirer(self.base, acte['id'], motif, 'referent')
            self.assertEqual(str(erreur.exception), 'Indiquez le motif du retrait.')
        with self.assertRaises(ValueError):
            publication.retirer(self.base, acte['id'], 'x' * 301, 'referent')
        a_relire = self.creer(numero='D2026-777')
        with self.assertRaises(ValueError) as erreur:
            publication.retirer(self.base, a_relire['id'], 'Motif', 'referent')
        self.assertEqual(str(erreur.exception), 'Seul un acte publié peut être retiré de la publication.')

    def test_un_acte_retire_ne_se_modifie_ni_ne_se_supprime(self):
        acte = self.publie()
        publication.retirer(self.base, acte['id'], 'Motif', 'referent')
        for appel in (lambda: publication.enregistrer_passages(self.base, acte['id'], []),
                      lambda: publication.valider(self.base, self.donnees, acte['id'], 'referent'),
                      lambda: publication.supprimer(self.base, self.donnees, acte['id']),
                      lambda: publication.retirer(self.base, acte['id'], 'Encore', 'referent')):
            with self.assertRaises((PermissionError, ValueError)):
                appel()
        self.assertTrue((self.donnees / 'publication' / acte['id'] / 'publie.pdf').exists())

    def test_mise_en_ligne_a_la_date_du_dossier(self):
        # I7 : la date publiée est celle imprimée sur le PDF, et un PDF est gardé pour la preuve (I6).
        acte = self.publie('2026-11-03')
        self.assertEqual((acte['statut'], acte['publie_le']), ('publie', '2026-11-03'))
        self.assertEqual((self.donnees / 'publication' / acte['id'] / 'publie.pdf').read_bytes(), b'%PDF')

    def test_mise_en_ligne_exige_un_dossier(self):
        acte = self.creer()
        publication.enregistrer_reperage(self.base, acte['id'], [], None)
        publication.valider(self.base, self.donnees, acte['id'], 'referent')
        with self.assertRaises(ValueError) as erreur:
            publication.marquer_publie(self.base, acte['id'])
        self.assertEqual(str(erreur.exception), 'Préparez d’abord le dossier de publication.')

    def test_word_dans_l_ordre_avec_tableaux_imbriques_controles_et_insertions(self):
        # I4 : texte intégral, dans l'ordre du document.
        from docx import Document
        from docx.oxml import parse_xml
        document = Document()
        document.add_paragraph('Avant le tableau.')
        tableau = document.add_table(rows=1, cols=1)
        tableau.cell(0, 0).paragraphs[0].text = 'Cellule'
        tableau.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).paragraphs[0].text = 'Jean Martin (tableau imbriqué)'
        document.add_paragraph('Après le tableau.')
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        document.element.body.append(parse_xml(f'<w:sdt {w}><w:sdtContent><w:p><w:r><w:t>Contrôle de contenu</w:t></w:r></w:p></w:sdtContent></w:sdt>'))
        document.element.body.append(parse_xml(f'<w:p {w}><w:ins w:id="1" w:author="x"><w:r><w:t>Texte inséré</w:t></w:r></w:ins>'
                                               f'<w:del w:id="2" w:author="x"><w:r><w:delText>Texte supprimé</w:delText></w:r></w:del></w:p>'))
        chemin = self.donnees / 'acte.docx'
        document.save(chemin)
        paragraphes = publication.paragraphes_docx(chemin)
        self.assertEqual(paragraphes[0], 'Avant le tableau.')
        self.assertIn('Jean Martin (tableau imbriqué)', paragraphes[1])
        self.assertEqual(paragraphes[2], 'Après le tableau.')
        self.assertIn('Contrôle de contenu', paragraphes)
        self.assertIn('Texte inséré', paragraphes)
        self.assertNotIn('Texte supprimé', ' '.join(paragraphes))

    def test_word_trop_lourd_refuse(self):
        # I8 : un petit fichier qui se décompresse en énorme XML.
        import zipfile
        chemin = self.donnees / 'bombe.docx'
        with zipfile.ZipFile(chemin, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', '<x>' + ' ' * (60 * 1024 * 1024) + '</x>')
        with self.assertRaises(ValueError) as erreur:
            publication.paragraphes_docx(chemin)
        self.assertEqual(str(erreur.exception), 'Fichier Word trop volumineux ou invalide.')

    def test_xml_de_plus_de_5_mo_refuse_avant_lecture(self):
        # Vérification finale : la lecture d'un XML Word coûte environ vingt fois sa taille en mémoire ; un acte réel
        # n'a pas 5 Mo de XML.
        import zipfile
        from unittest.mock import patch
        chemin = self.donnees / 'xml-lourd.docx'
        with zipfile.ZipFile(chemin, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', '<x>' + ' ' * (6 * 1024 * 1024) + '</x>')
        with patch('docx.Document') as lecture, self.assertRaises(ValueError) as erreur:
            publication.paragraphes_docx(chemin)
        self.assertEqual(str(erreur.exception), 'Fichier Word trop volumineux ou invalide.')
        lecture.assert_not_called()

    def test_image_volumineuse_acceptee(self):
        # La borne de 5 Mo ne vise que le XML : une signature scannée de plusieurs Mo reste acceptée.
        import zipfile
        from docx import Document
        chemin = self.donnees / 'avec-image.docx'
        document = Document()
        document.add_paragraph('Le conseil municipal,')
        document.save(str(chemin))
        with zipfile.ZipFile(chemin, 'a', zipfile.ZIP_STORED) as archive:
            archive.writestr('word/media/signature.png', os.urandom(6 * 1024 * 1024))
        self.assertEqual(publication.paragraphes_docx(chemin), ['Le conseil municipal,'])



if __name__ == '__main__':
    unittest.main()
