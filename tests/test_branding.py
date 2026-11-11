# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import io
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import branding
import arrete_document
import config


class BrandingTests(unittest.TestCase):
    def test_uploaded_logo_is_embedded_and_not_transposed_to_another_city(self):
        with tempfile.TemporaryDirectory() as tmp:
            image=Image.new('RGB',(100,30),'navy');raw=io.BytesIO();image.save(raw,format='PNG')
            ident=branding.upload(tmp,raw.getvalue())
            branding.save(tmp,{'collectivite':'Ville de Senlis','logo':ident})
            logo=branding.for_document(tmp,'Commune de Senlis')
            self.assertEqual(logo['id'],ident)
            self.assertEqual(branding.for_document(tmp,'Ville de Démoville'),{})
            document=arrete_document.assemble({'collectivite':'Commune de Senlis'},
                {'objet':'Exemple fictif','considerant':'Considérant les faits fournis.','articles':[],
                 'destinataires':'Services compétents'},data_dir=tmp)
            output=Path(tmp)/'exemple.docx';arrete_document.create_word(output,document,tmp)
            with zipfile.ZipFile(output) as word:
                self.assertTrue(any(n.startswith('word/media/') for n in word.namelist()))
                text=word.read('word/document.xml').decode()
                self.assertNotIn('[LOGO',text)
            path,_=branding.logo_file(tmp,ident);path.write_bytes(b'logo modifie')
            with self.assertRaises(ValueError):branding.logo_file(tmp,ident)

    def test_active_or_invalid_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for raw in (b'<svg onload="alert(1)"></svg>',b'not a png',b'x'*(2*1024*1024+1)):
                with self.assertRaises(ValueError):branding.upload(tmp,raw)


def papier_a_en_tete(**modifs):
    """Papier à en-tête fictif d'une collectivité : en-tête, pied de page, marges et police à elle."""
    from docx import Document
    from docx.shared import Cm, Pt
    doc = Document()
    section = doc.sections[0]
    section.left_margin = section.right_margin = Cm(2.5)
    section.header.paragraphs[0].text = 'Mairie de Testville — 1 place de la Mairie'
    section.footer.paragraphs[0].text = 'www.testville.fr'
    doc.styles['Normal'].font.name = 'Marianne'
    doc.styles['Normal'].font.size = Pt(11)
    doc.add_paragraph('Texte d’exemple à remplacer')
    raw = io.BytesIO(); doc.save(raw)
    contenu = raw.getvalue()
    if modifs:
        entree, sortie = io.BytesIO(contenu), io.BytesIO()
        with zipfile.ZipFile(entree) as source, zipfile.ZipFile(sortie, 'w') as cible:
            for item in source.infolist():
                donnees = source.read(item.filename)
                if item.filename in modifs.get('remplacer', {}):
                    donnees = modifs['remplacer'][item.filename](donnees)
                cible.writestr(item, donnees)
            for nom, donnees in modifs.get('ajouter', {}).items():
                cible.writestr(nom, donnees)
        contenu = sortie.getvalue()
    return contenu


DOCUMENT = {'kind': 'arrete_rh', 'title': 'Arrêté n° [à attribuer]', 'collectivite': 'Commune de Testville',
            'objet': 'Objet : Nomination stagiaire', 'preambule': [], 'autorite': 'Le Maire de la commune de Testville',
            'visas': ['Vu le code général de la fonction publique,'], 'considerants': ['Considérant la vacance de l’emploi,'],
            'vote': '', 'decision': 'ARRÊTE', 'articles': [{'role': 'decision', 'title': '', 'texte': 'Mme X est nommée.', 'number': 1}],
            'lieu_date': 'Fait à Testville, le [date de signature]', 'signatures': ['Le Maire, [nom et signature]'],
            'certification': '', 'signature': '', 'logo': {}}


class GabaritTests(unittest.TestCase):
    def test_les_actes_sortent_sur_le_papier_a_en_tete_de_la_collectivite(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as tmp:
            branding.upload_gabarit(tmp, papier_a_en_tete())
            self.assertTrue(branding.profile(tmp)['gabarit'])
            sortie = Path(tmp) / 'acte.docx'
            arrete_document.create_word(sortie, DOCUMENT, tmp)
            acte = Document(str(sortie))
            section = acte.sections[0]
            self.assertEqual(section.header.paragraphs[0].text, 'Mairie de Testville — 1 place de la Mairie')
            self.assertEqual(section.footer.paragraphs[0].text, 'www.testville.fr')
            self.assertAlmostEqual(section.left_margin.cm, 2.5, places=2)  # Word arrondit au vingtième de point
            self.assertEqual(acte.styles['Normal'].font.name, 'Marianne')
            texte = [p.text for p in acte.paragraphs]
            self.assertNotIn('Texte d’exemple à remplacer', texte)  # le corps du gabarit est remplacé par l'acte
            self.assertIn('PROJET — NON SIGNÉ', texte)
            self.assertIn('ARRÊTE', texte)
            branding.supprimer_gabarit(tmp)
            self.assertFalse(branding.profile(tmp)['gabarit'])
            arrete_document.create_word(sortie, DOCUMENT, tmp)
            self.assertNotEqual(Document(str(sortie)).sections[0].header.paragraphs[0].text, 'Mairie de Testville — 1 place de la Mairie')

    def test_un_gabarit_modifie_apres_depot_est_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            branding.upload_gabarit(tmp, papier_a_en_tete())
            (Path(tmp) / 'identite' / 'gabarit.docx').write_bytes(papier_a_en_tete() + b' ')
            self.assertIsNone(branding.gabarit_file(tmp))

    def test_fichiers_refuses(self):
        externe = lambda xml: xml.replace(b'</Relationships>', b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                                          b'Target="https://exemple.fr/pixel.png" TargetMode="External"/></Relationships>')
        with tempfile.TemporaryDirectory() as tmp:
            for raw in (b'pas un docx', b'x' * (5 * 1024 * 1024 + 1),
                        papier_a_en_tete(ajouter={'word/vbaProject.bin': b'macro'}),
                        papier_a_en_tete(remplacer={'word/_rels/document.xml.rels': externe})):
                with self.subTest(taille=len(raw)), self.assertRaises(ValueError):
                    branding.upload_gabarit(tmp, raw)
            self.assertFalse(branding.profile(tmp)['gabarit'])



def _empreinte(chemin):
    from docx import Document
    doc = Document(str(chemin))
    section = doc.sections[0]
    return {'corps': [(p.style.name, p.text) for p in doc.paragraphs], 'entete': [p.text for p in section.header.paragraphs],
            'pied': [p.text for p in section.footer.paragraphs], 'marges': [round(section.left_margin.cm, 2), round(section.top_margin.cm, 2)]}


CORPS_DOCUMENT = [('Title', 'Arrêté n° [à attribuer]'), ('Normal', 'COMMUNE DE TESTVILLE'), ('Normal', 'Objet : Nomination stagiaire'),
                  ('Normal', 'Le Maire de la commune de Testville,'), ('Normal', 'Vu le code général de la fonction publique,'),
                  ('Normal', 'Considérant la vacance de l’emploi,'), ('Normal', 'ARRÊTE'), ('Normal', 'Article 1'),
                  ('Normal', 'Mme X est nommée.'), ('Normal', 'Fait à Testville, le [date de signature]'),
                  ('Normal', 'Le Maire, [nom et signature]')]


class DocumentViergeTests(unittest.TestCase):
    def test_create_word_inchange_apres_extraction(self):
        # Relevé avant l'extraction de document_vierge (tâche 7 du lot 1 de la transcription).
        with tempfile.TemporaryDirectory() as tmp:
            arrete_document.create_word(Path(tmp) / 'a.docx', DOCUMENT, tmp)
            self.assertEqual(_empreinte(Path(tmp) / 'a.docx'), {
                'corps': CORPS_DOCUMENT, 'entete': ['Commune de Testville', 'PROJET — NON SIGNÉ'],
                'pied': ['LIBERTÉ – ÉGALITÉ – FRATERNITÉ        Page '], 'marges': [6.9, 3.8]})
            branding.upload_gabarit(tmp, papier_a_en_tete())
            arrete_document.create_word(Path(tmp) / 'b.docx', DOCUMENT, tmp)
            self.assertEqual(_empreinte(Path(tmp) / 'b.docx'), {
                'corps': [('Normal', 'PROJET — NON SIGNÉ')] + CORPS_DOCUMENT, 'entete': ['Mairie de Testville — 1 place de la Mairie'],
                'pied': ['www.testville.fr'], 'marges': [2.5, 2.54]})

    def test_document_vierge_sur_papier_a_en_tete_ou_mise_en_page_territoria(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = arrete_document.document_vierge(tmp, 'Commune de Testville')
            self.assertEqual([p.text for p in doc.paragraphs], [])
            self.assertEqual([p.text for p in doc.sections[0].header.paragraphs], ['Commune de Testville'])
            branding.upload_gabarit(tmp, papier_a_en_tete())
            doc = arrete_document.document_vierge(tmp, 'Commune de Testville', 'TRANSCRIPTION')
            self.assertEqual([p.text for p in doc.paragraphs], ['TRANSCRIPTION'])
            self.assertEqual(doc.sections[0].header.paragraphs[0].text, 'Mairie de Testville — 1 place de la Mairie')
            self.assertEqual([p.text for p in arrete_document.document_vierge(tmp, 'Commune de Testville').paragraphs], [])


class MentionDemonstrationTests(unittest.TestCase):
    """Serveur de démonstration : chaque Word porte « Document de démonstration — sans valeur juridique » en pied de page."""
    def pied(self, avec_gabarit, demonstration):
        from docx import Document
        with tempfile.TemporaryDirectory() as tmp:
            if avec_gabarit:
                branding.upload_gabarit(tmp, papier_a_en_tete())
            sortie = Path(tmp) / 'acte.docx'
            with patch.object(arrete_document, 'REGLAGES', replace(config.charger({}), demonstration=demonstration)):
                arrete_document.create_word(sortie, DOCUMENT, tmp)
            return '\n'.join(p.text for p in Document(str(sortie)).sections[-1].footer.paragraphs)

    def test_mention_demonstration_dans_l_arrete(self):
        for avec_gabarit in (False, True):
            with self.subTest(avec_gabarit=avec_gabarit):
                self.assertIn('Document de démonstration — sans valeur juridique', self.pied(avec_gabarit, True))
        self.assertIn('www.testville.fr', self.pied(True, True))  # le pied de page de la collectivité reste

    def test_aucune_mention_hors_demonstration(self):
        for avec_gabarit in (False, True):
            with self.subTest(avec_gabarit=avec_gabarit):
                self.assertNotIn('démonstration', self.pied(avec_gabarit, False))

    def test_mention_sur_la_premiere_page_et_les_pages_paires(self):
        # Relecture finale : un papier à en-tête « première page différente » n'avait pas la mention en page 1.
        from docx import Document
        modele = Document()
        section = modele.sections[0]
        section.different_first_page_header_footer = True
        section.first_page_footer.paragraphs[0].text = 'Pied de la première page'
        section.footer.paragraphs[0].text = 'Pied des autres pages'
        brut = io.BytesIO(); modele.save(brut)
        with tempfile.TemporaryDirectory() as tmp:
            branding.upload_gabarit(tmp, brut.getvalue())
            sortie = Path(tmp) / 'acte.docx'
            with patch.object(arrete_document, 'REGLAGES', replace(config.charger({}), demonstration=True)):
                arrete_document.create_word(sortie, DOCUMENT, tmp)
            section = Document(str(sortie)).sections[-1]
            for pied in (section.footer, section.first_page_footer, section.even_page_footer):
                self.assertIn('Document de démonstration — sans valeur juridique', '\n'.join(x.text for x in pied.paragraphs))

