# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Un même document structuré pour l'aperçu conversationnel et le Word."""
import re
import branding
import legal_sources
from config import REGLAGES

MENTION_DEMONSTRATION='Document de démonstration — sans valeur juridique'

ARTICLE_TITLES={
    'mesure':'Prescriptions',
    'mise_en_oeuvre':'Mise en œuvre',
    'controle':'Contrôle et sanctions',
    'publicite':'Publicité et entrée en vigueur',
    'recours':'Voies et délais de recours',
    'execution':'Exécution et ampliation',
}


def _commune(collectivite):
    """« Commune de Démoville » → (« Le Maire de la commune de Démoville », « Démoville ») ; (« Le Maire », '') sinon."""
    m=re.match(r"\s*(commune|ville|mairie)\s+(de\s+|d['’]\s*)(\S.*)",collectivite,re.IGNORECASE)
    if not m:
        return 'Le Maire',''
    lieu=m.group(3).strip()
    if m.group(1).lower()=='mairie':
        return 'Le Maire '+m.group(2).lower()+lieu,lieu
    return 'Le Maire de la '+m.group(1).lower()+' '+m.group(2).lower()+lieu,lieu


def assemble(data,generated,legal=None,data_dir=None):
    legal=legal or {};sources=legal.get('sources',[])
    refs={s['article'] for s in sources}
    commune=legal_sources.is_commune(data.get('collectivite',''))
    maire,lieu=_commune(data.get('collectivite','')) if commune else ('','')
    authority=data.get('autorite') or (maire if commune else '[Autorité compétente à préciser]')
    signature_place=('Fait à '+lieu+', le [date de signature]') if lieu else '[Lieu et date de signature à compléter]'
    articles=[dict(a) for a in generated['articles']]
    for a in articles:
        if a['role']=='mise_en_oeuvre' and data.get('signalisation'):a['texte']=data['signalisation']
        if a['role']=='controle' and data.get('sanctions'):a['texte']=data['sanctions']
    publicity=('Le présent arrêté sera porté à la connaissance du public selon le mode de publicité légalement applicable à la commune. Il ne sera exécutoire qu’après accomplissement des formalités requises et, si une transmission au représentant de l’État est exigée pour cet acte, après cette transmission.'
               if 'L2131-1' in refs else
               'Le présent arrêté fera l’objet des formalités de publicité ou de notification requises. Sa prise d’effet est subordonnée à l’accomplissement des formalités légalement applicables à cet acte et à son auteur.')
    if legal_sources.classify(data.get('contexte',''))=='circulation':
        publicity+=' Les prescriptions de circulation ou de stationnement seront portées à la connaissance des usagers par la signalisation réglementaire requise.'
    recourse=('Le présent arrêté peut faire l’objet d’un recours devant le tribunal administratif territorialement compétent dans un délai de deux mois à compter de sa publication ou de sa notification, selon le cas, sous réserve des dispositions particulières applicables.'
              if 'R421-1' in refs else
              '[Préciser les voies et délais de recours après vérification du régime applicable à cet acte.]')
    recipients=data.get('destinataires') or generated['destinataires']
    execution='Les services compétents de la collectivité et les autorités légalement habilitées sont chargés, chacun en ce qui le concerne et dans la limite de ses compétences, de l’exécution du présent arrêté. Ampliation sera transmise à :\n'+'\n'.join('– '+x.strip() for x in recipients.splitlines() if x.strip())
    articles.extend([{'role':'publicite','texte':publicity},
                     {'role':'recours','texte':recourse},
                     {'role':'execution','texte':execution}])
    for n,a in enumerate(articles,1):a.update(number=n,title=ARTICLE_TITLES.get(a['role'],'Dispositions'))
    visas=['Vu l’article '+s['article']+' du '+s['code'].lower()+',' for s in sources]
    for value in data.get('visas','').splitlines():
        if value.strip():visas.append(value.strip() if re.match(r'^vu\b',value.strip(),re.I) else 'Vu '+value.strip())
    if not visas:visas=['[Références juridiques spécifiques à identifier et vérifier]']
    logo=branding.for_document(data_dir,data.get('collectivite','')) if data_dir else {}
    return {'title':('Arrêté du maire' if commune else 'Projet d’arrêté')+' n° '+(data.get('numero') or '[à attribuer]'),
            'collectivite':data.get('collectivite') or '[Collectivité à préciser]',
            'objet':data.get('objet') or generated['objet'],'autorite':authority,
            'visas':visas,'considerants':[x.strip() for x in generated['considerant'].split('\n\n') if x.strip()],
            'articles':articles,'lieu_date':data.get('lieu_date') or signature_place,
            'signature':'Signature à apposer par l’autorité compétente','logo':logo}


def document_vierge(data_dir,collectivite,mention='',logo=None):
    """Document Word sans contenu : le papier à en-tête de la collectivité s'il est déposé (ses en-têtes, pieds de page,
    marges et styles restent, son corps est vidé ; la mention en tête du corps), sinon la mise en page de Territoria (la
    mention dans l'en-tête). `logo` : celui de l'acte, sinon celui de l'identité de la collectivité.

    Sur le serveur de démonstration, le pied de page porte en plus MENTION_DEMONSTRATION : aucun Word de démonstration ne
    doit pouvoir passer pour un acte."""
    doc=_document_vierge(data_dir,collectivite,mention,logo)
    if REGLAGES.demonstration:  # pied de page courant, de première page et des pages paires (papier à en-tête qui les distingue)
        section=doc.sections[-1]
        for pied in (section.footer,section.first_page_footer,section.even_page_footer):
            pied.add_paragraph(MENTION_DEMONSTRATION)
    return doc


def _document_vierge(data_dir,collectivite,mention,logo):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm,Pt,RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from PIL import Image

    gabarit=branding.gabarit_file(data_dir) if data_dir else None
    if gabarit:
        doc=Document(str(gabarit));corps=doc.element.body
        for element in list(corps):
            if element.tag!=qn('w:sectPr'):corps.remove(element)
        if mention:
            p=doc.add_paragraph(mention);p.alignment=WD_ALIGN_PARAGRAPH.RIGHT;p.runs[0].font.size=Pt(8)
        return doc
    if logo is None:
        logo=branding.for_document(data_dir,collectivite) if data_dir else {}
    return _document_type(Document,WD_ALIGN_PARAGRAPH,Cm,Pt,RGBColor,OxmlElement,qn,Image,collectivite,logo,mention,data_dir)


def create_word(path,document,data_dir=None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    gabarit=branding.gabarit_file(data_dir) if data_dir else None
    doc=document_vierge(data_dir,document['collectivite'],'PROJET — NON SIGNÉ',logo=document.get('logo') or {})
    try:
        p=doc.add_paragraph(document['title'],'Title')
    except KeyError:  # gabarit sans style « Titre »
        p=doc.add_paragraph();run=p.add_run(document['title']);run.bold=True;run.font.size=Pt(16)
    p.paragraph_format.space_after=Pt(10)
    _corps(doc,document,WD_ALIGN_PARAGRAPH,Pt)
    doc.core_properties.title=document['objet'];doc.core_properties.author=''
    if not gabarit:
        # Le modèle Word fourni avec le runtime peut porter une bordure bleue sur
        # Title : l'arrêté conserve une typographie noire sans ce décor hérité.
        for root in (doc._element,doc.styles.element):
            for border in root.xpath('.//w:pBdr'):
                border.getparent().remove(border)
    doc.save(str(path))


def _document_type(Document,WD_ALIGN_PARAGRAPH,Cm,Pt,RGBColor,OxmlElement,qn,Image,collectivite,logo,mention,data_dir):
    """Document Word de Territoria, sans gabarit : marges, police, logo en marge et mentions de pied de page."""
    doc=Document();section=doc.sections[0]
    section.page_width,section.page_height=Cm(21),Cm(29.7)
    section.left_margin,section.right_margin=Cm(6.9),Cm(1.5)
    section.top_margin,section.bottom_margin=Cm(3.8),Cm(2)
    section.header_distance,section.footer_distance=Cm(1),Cm(1)
    normal=doc.styles['Normal'];normal.font.name='Arial';normal.font.size=Pt(10)
    normal.paragraph_format.space_after=Pt(6);normal.paragraph_format.line_spacing=1.05
    normal.paragraph_format.widow_control=True
    for name in ('Title','Heading 1','Heading 2'):
        doc.styles[name].font.name='Arial';doc.styles[name].font.color.rgb=RGBColor(0,0,0)
    doc.styles['Title'].font.size=Pt(20);doc.styles['Title'].font.bold=True
    # Le logo fourni par l'utilisateur est placé dans la marge gauche du modèle.
    p=section.header.paragraphs[0];p.paragraph_format.left_indent=Cm(-5.3)
    p.paragraph_format.space_after=Pt(0)
    if logo and data_dir:
        image,_=branding.logo_file(data_dir,logo['id'])
        with Image.open(image) as im:
            width=min(3.4,2.35*im.width/im.height)
        p.add_run().add_picture(str(image),width=Cm(width))
    else:
        p.add_run(collectivite).bold=True
    if mention:
        p=section.header.add_paragraph(mention);p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_after=Pt(0);p.runs[0].font.size=Pt(8)
    p=section.footer.paragraphs[0];p.paragraph_format.left_indent=Cm(-5.3)
    p.add_run('LIBERTÉ – ÉGALITÉ – FRATERNITÉ').font.size=Pt(8)
    p.add_run(' '*8+'Page ').font.size=Pt(8)
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');p._p.append(field)
    return doc


def _corps(doc,document,WD_ALIGN_PARAGRAPH,Pt):
    p=doc.add_paragraph(document['collectivite'].upper());p.runs[0].bold=True;p.runs[0].font.size=Pt(9)
    for ligne in document.get('preambule',[]):
        p=doc.add_paragraph(ligne);p.paragraph_format.space_after=Pt(2);p.runs[0].font.size=Pt(9)
    p=doc.add_paragraph(document['objet']);p.runs[0].bold=True
    p.paragraph_format.space_after=Pt(12);p.paragraph_format.space_before=Pt(8 if document.get('preambule') else 0)
    doc.add_paragraph(document['autorite']+',')
    for text in document['visas']:doc.add_paragraph(text)
    for text in document['considerants']:
        p=doc.add_paragraph(text);p.alignment=WD_ALIGN_PARAGRAPH.JUSTIFY
    if document.get('vote'):
        p=doc.add_paragraph(document['vote']);p.paragraph_format.space_before=Pt(6)
    p=doc.add_paragraph(document.get('decision','ARRÊTE'));p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.runs[0].bold=True
    p.paragraph_format.space_before=Pt(10);p.paragraph_format.space_after=Pt(12);p.paragraph_format.keep_with_next=True
    for article in document['articles']:
        p=doc.add_paragraph();p.paragraph_format.keep_with_next=True
        run=p.add_run('Article '+str(article['number'])+(' : '+article['title'] if article['title'] else ''));run.bold=True
        p.paragraph_format.space_before=Pt(7);p.paragraph_format.space_after=Pt(3)
        for block in article['texte'].split('\n'):
            p=doc.add_paragraph(block);p.alignment=WD_ALIGN_PARAGRAPH.JUSTIFY
    p=doc.add_paragraph(document['lieu_date']);p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_before=Pt(20);p.paragraph_format.keep_with_next=True
    if document.get('signatures'):
        for ligne in document['signatures']:
            p=doc.add_paragraph(ligne);p.alignment=WD_ALIGN_PARAGRAPH.RIGHT;p.paragraph_format.keep_with_next=True
    else:
        p=doc.add_paragraph(document['autorite']);p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.keep_with_next=True
        p=doc.add_paragraph(document['signature']);p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        p.runs[0].italic=True;p.runs[0].font.size=Pt(9)
    if document.get('certification'):
        p=doc.add_paragraph(document['certification']);p.paragraph_format.space_before=Pt(18);p.runs[0].font.size=Pt(8)
