# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Routes de publication : parcours complet, droits de validation, dépôts."""
import io
import json
import sys
import unittest
import zipfile
from datetime import timedelta
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import arrete_document
import branding
import llm_backend
import server
import web
from territoria import comptes, registre
from territoria.publication_dossier import jour_courant
from tests.test_comptes import MDP_ADMIN, NAVIGATEUR, Base



def aujourdhui():
    """Le jour de l'application (heure de Paris), lu au moment de s'en servir : voir tests/test_publication_dossier.py."""
    return jour_courant().isoformat()


MESSAGE_VALIDATION = 'Seuls un référent juridique ou un administrateur peuvent valider une publication.'


def ia_qui_trouve(*noms):
    reponse = {'personnes': [{'texte': n, 'role': 'particulier'} for n in noms]}
    return patch.object(llm_backend, 'chat', return_value={'choices': [{'message': {'content': json.dumps(reponse)}}]})


def docx(*paragraphes, entete=None):
    from docx import Document
    document = Document()
    if entete:
        document.sections[0].header.paragraphs[0].text = entete
    for p in paragraphes:
        document.add_paragraph(p)
    sortie = io.BytesIO()
    document.save(sortie)
    return sortie.getvalue()


class ClientPublication(Base):
    def appel(self, methode, chemin, donnees=None, jeton=None, entetes=None):
        """Simule une lecture fraîche avant l'action si le cas ne donne pas explicitement sa révision."""
        if methode == 'DELETE' and chemin.startswith('/api/publications/') and '?' not in chemin:
            statut, acte = super().appel('GET', chemin, jeton=jeton)
            if statut == 200:
                chemin += f"?revision={acte['revision']}"
        if methode == 'POST' and isinstance(donnees, dict):
            morceaux = chemin.split('/')
            if len(morceaux) == 5 and morceaux[2] == 'publications' and 'revision' not in donnees:
                statut, acte = super().appel('GET', '/'.join(morceaux[:-1]), jeton=jeton)
                if statut == 200:
                    donnees = donnees | {'revision': acte['revision']}
            if chemin == '/api/publications/dossier' and 'revisions' not in donnees:
                visibles = super().appel('GET', '/api/publications', jeton=jeton)[1]['publications']
                donnees = donnees | {'revisions': {a['id']: a['revision'] for a in visibles}}
        return super().appel(methode, chemin, donnees, jeton, entetes)


class PublicationWebTests(ClientPublication):
    def test_relecture_perimee_refuse_validation_ecrasement_et_ancien_client(self):
        acte = self.deposer(self.agent, docx('Contacter test@example.invalid.'))[1]
        base = '/api/publications/' + acte['id']
        def appel(action, corps, utilisateur):
            return Base.appel(self, 'POST', base + '/' + action, corps, utilisateur)
        with ia_qui_trouve():
            self.assertEqual(appel('reperer', {'revision': acte.get('revision', 0)}, self.agent)[0], 200)
        lu = self.appel('GET', base, jeton=self.referent)[1]
        revision = lu.get('revision', 0)
        self.assertEqual(appel('passages', {'revision': revision, 'passages': []}, self.agent)[0], 200)
        self.assertEqual(Base.appel(self, 'DELETE', base + '?revision=' + str(revision), jeton=self.agent)[0], 409)
        for action, corps in [('valider', {}), ('valider', {'revision': revision}),
                              ('passages', {'passages': lu['passages'], 'revision': revision}),
                              ('passages', {'passages': lu['passages']})]:
            with self.subTest(action=action, corps=corps):
                self.assertEqual(appel(action, corps, self.referent)[0], 409)
        actuel = self.appel('GET', base, jeton=self.referent)[1]
        self.assertEqual((actuel['passages'], actuel['statut']), ([], 'a_relire'))
        statut, valide = appel('valider', {'revision': actuel['revision']}, self.referent)
        self.assertEqual(statut, 200)
        for revisions in ({}, {acte['id']: revision}):
            statut, _ = Base.appel(self, 'POST', '/api/publications/dossier', {'ids': [acte['id']], 'revisions': revisions}, self.referent)
            self.assertEqual(statut, 409)
        self.assertEqual(Base.appel(self, 'POST', '/api/publications/dossier', {'ids': [acte['id']]}, self.referent)[0], 409)
        self.assertEqual(self.appel('POST', '/api/publications/dossier', {'ids': [acte['id']]}, self.referent)[0], 200)
        self.assertEqual(appel('en-ligne', {'revision': valide['revision']}, self.referent)[0], 409)
        self.assertEqual(appel('en-ligne', {}, self.referent)[0], 409)

    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()
        self.referent = self.creer(self.admin, 'referent', 'referent')[1]
        self.agent_id, self.agent = self.creer(self.admin, 'agent')
        branding.save(server.DATA, {'collectivite': 'Commune de Démoville', 'siret': '21860194600013'})

    def acte_du_registre(self, version_signee=None):
        ident = 'a' * 32
        (server.DATA / 'arretes').mkdir(exist_ok=True)
        objet = 'Stationnement pour un déménagement'
        numero = registre.attribuer(server.DB, ident, 'arrete', objet)
        chemin = server.DATA / 'arretes' / f'{ident}.docx'
        if version_signee:
            chemin.write_bytes(version_signee)
        else:
            document = arrete_document.assemble({'collectivite': 'Commune de Démoville', 'numero': numero},
                                                {'objet': objet, 'considerant': 'Considérant la demande de M. Jean Martin.',
                                                 'articles': [], 'destinataires': 'Services compétents'})
            arrete_document.create_word(chemin, document)
        return numero

    def deposer(self, jeton, contenu, nom='acte.docx', **champs):
        champs = {'type': 'deliberation', 'numero': 'D2026-012', 'date_acte': '2026-10-14', 'objet': 'Cession d’un terrain',
                  'matiere_code': '3.2'} | champs
        requete = '&'.join(f'{k}={v}' for k, v in ({'nom': nom} | champs).items())
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        return self.appel('POST', f'/api/publications/depot?{requete}', contenu, jeton, entetes)

    def test_reperage_en_ligne_garde_le_texte_sur_le_serveur_territoria(self):
        statut, acte = self.deposer(self.referent, docx('Contacter test@example.invalid.'))
        self.assertEqual(statut, 201)
        for distant in ('openrouter', 'mistral'):
            with self.subTest(distant=distant), patch.object(llm_backend, 'read', return_value={'provider': distant}), \
                    patch.object(web, '_demander_ia', side_effect=AssertionError('envoi externe')) as appel:
                statut, reponse = self.appel('POST', f'/api/publications/{acte["id"]}/reperer', {}, self.referent)
                self.assertEqual(statut, 200)
                appel.assert_not_called()
                self.assertIn('test@example.invalid', [p['texte'] for p in reponse['passages']])
                self.assertIn('noms', reponse['avertissement'])

    def test_un_nouveau_reperage_conserve_les_choix_de_relecture(self):
        statut, acte = self.deposer(self.referent, docx('Mina Fictive et contact@example.invalid.'))
        self.assertEqual(statut, 201)
        route = f'/api/publications/{acte["id"]}'
        with ia_qui_trouve('Mina Fictive'):
            premier = self.appel('POST', route + '/reperer', {}, self.referent)[1]
        passages = premier['passages']
        courriel = next(p for p in passages if p['texte'] == 'contact@example.invalid')
        courriel['coche'] = False
        self.assertEqual(self.appel('POST', route + '/passages', {'passages': passages}, self.referent)[0], 200)
        with patch.object(llm_backend, 'read', return_value={'provider': 'openrouter'}):
            suivant = self.appel('POST', route + '/reperer', {}, self.referent)[1]
        choix = {p['texte']: p['coche'] for p in suivant['passages']}
        self.assertTrue(choix.get('Mina Fictive'))
        self.assertFalse(choix['contact@example.invalid'])

    def test_parcours_complet_referent(self):
        numero = self.acte_du_registre(docx('Vu la demande de M. Jean Martin, 12 rue des Lilas.',
                                            'Article 1. Le stationnement est interdit.', 'Signé le 14 octobre 2026.'))
        statut, acte = self.appel('POST', '/api/publications', {'registre': numero, 'date_acte': '2026-10-14'}, self.referent)
        self.assertEqual((statut, acte['statut'], acte['source']), (201, 'a_relire', 'registre'))
        with ia_qui_trouve('Jean Martin'):
            statut, repere = self.appel('POST', f'/api/publications/{acte["id"]}/reperer', {}, self.referent)
        self.assertEqual(statut, 200)
        passages = {p['texte']: p for p in repere['passages']}
        self.assertTrue(passages['Jean Martin']['coche'])
        self.assertEqual(passages['12 rue des Lilas']['categorie'], 'adresse')
        self.assertIsNone(repere['avertissement'])
        self.assertEqual(self.appel('POST', f'/api/publications/{acte["id"]}/valider', {}, self.referent)[1]['statut'], 'valide')
        self.appel('POST', '/api/publications/dossier', {'ids': [acte['id']], 'date': aujourdhui()}, self.referent)
        reponse = self.derniere
        self.assertEqual(reponse.status, 200)
        self.assertEqual(dict(reponse.headers)['Content-Type'], 'application/zip')
        archive = zipfile.ZipFile(io.BytesIO(reponse.body))
        self.assertIn(numero.replace('/', '-') + '.pdf', archive.namelist())
        lu = self.appel('GET', f'/api/publications/{acte["id"]}', jeton=self.referent)[1]
        self.assertEqual(len(lu['empreinte_pdf']), 64)
        statut, publie = self.appel('POST', f'/api/publications/{acte["id"]}/en-ligne', {'date': aujourdhui()}, self.referent)
        self.assertEqual((statut, publie['statut'], publie['publie_le']), (200, 'publie', aujourdhui()))

    def test_projet_numerote_refuse_et_depot_de_la_version_signee(self):
        numero = self.acte_du_registre()
        statut, corps = self.appel('POST', '/api/publications', {'registre': numero}, self.referent)
        self.assertEqual(statut, 400)
        self.assertIn('La version signée', corps['error'])
        self.assertEqual(self.appel('GET', '/api/publications', jeton=self.referent)[1]['publications'], [])
        statut, _ = self.deposer(self.referent, docx('Le projet — non signé en annexe [1] — est remplacé.', 'Signé le 14 octobre 2026.'), numero=numero, type='arrete')
        self.assertEqual(statut, 201)

    def test_depot_refuse_chaque_indice_explicite_de_projet(self):
        fichiers = (docx('Vu la demande du [à compléter].'), docx('Fait le [date de signature].'),
                    docx('Le Maire, [nom et signature]'), docx('DÉCIDE', entete='PROJET — NON SIGNÉ'))
        for i, fichier in enumerate(fichiers):
            with self.subTest(cas=i):
                statut, corps = self.deposer(self.agent, fichier, numero=f'A{i}')
                self.assertEqual(statut, 400)
                self.assertIn('La version signée', corps['error'])

    def test_depot_pdf_refuse_le_bandeau_avant_regroupement_des_lignes(self):
        from reportlab.pdfgen import canvas
        sortie = io.BytesIO()
        page = canvas.Canvas(sortie)
        for i, ligne in enumerate(('COMMUNE', 'PROJET — NON SIGNÉ', 'Article 1.')):
            page.drawString(40, 800 - 25 * i, ligne)
        page.save()
        statut, corps = self.deposer(self.agent, sortie.getvalue(), nom='acte.pdf')
        self.assertEqual(statut, 400)
        self.assertIn('La version signée', corps['error'])

    def test_ancienne_fiche_incomplete_meme_validee_reste_bloquee(self):
        statut, acte = self.deposer(self.referent, docx('Arrêté signé.'))
        self.assertEqual(statut, 201)
        ident = acte['id']
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{ident}/reperer', {}, self.referent)
        self.assertEqual(self.appel('POST', f'/api/publications/{ident}/valider', {}, self.referent)[0], 200)
        self.assertEqual(self.appel('POST', '/api/publications/dossier', {'ids': [ident]}, self.referent)[0], 200)
        # Reconstitue les données conservées par l'ancienne version, y compris l'empreinte d'un ZIP déjà préparé.
        texte = server.DATA / 'publication' / ident / 'texte.json'
        texte.write_text(json.dumps(['Fait le [date de signature].']), encoding='utf-8')
        for route, params in ((f'{ident}/valider', {}), ('dossier', {'ids': [ident]}), (f'{ident}/en-ligne', {})):
            with self.subTest(route=route):
                statut, corps = self.appel('POST', '/api/publications/' + route, params, self.referent)
                self.assertEqual(statut, 400)
                self.assertIn('La version signée', corps['error'])

    def test_agent_ne_valide_pas(self):
        statut, acte = self.deposer(self.agent, docx('Le conseil municipal,', 'Vu la demande de M. Jean Martin ;'))
        self.assertEqual(statut, 201)
        statut, corps = self.appel('POST', f'/api/publications/{acte["id"]}/valider', {}, self.agent)
        self.assertEqual((statut, corps['error']), (403, MESSAGE_VALIDATION))
        statut, _ = self.appel('POST', '/api/publications/dossier', {'ids': [acte['id']], 'date': aujourdhui()}, self.agent)
        self.assertEqual(statut, 403)

    def test_depot_docx(self):
        statut, acte = self.deposer(self.agent, docx('Le conseil municipal,', 'DÉCIDE'))
        self.assertEqual((statut, acte['source'], acte['paragraphes']), (201, 'depot', ['Le conseil municipal,', 'DÉCIDE']))

    def test_depot_format_refuse(self):
        statut, corps = self.deposer(self.agent, b'texte', nom='acte.txt')
        self.assertEqual((statut, corps['error']), (400, 'Déposez l’acte en Word (.docx) ou en PDF.'))

    def pdf_avec_plan(self, plan_separe):
        from reportlab.pdfgen.canvas import Canvas
        sortie = io.BytesIO()
        document = Canvas(sortie)
        document.drawString(40, 800, 'Article 1. Le secteur interdit figure dans le plan joint au texte de la decision.')
        if plan_separe:
            document.showPage()
        document.rect(80, 90, 200, 160, fill=1)
        document.save()
        return sortie.getvalue()

    def test_refuse_plan_sur_page_mixte_ou_annexe(self):
        for separe in (False, True):
            with self.subTest(separe=separe):
                statut, corps = self.deposer(self.agent, self.pdf_avec_plan(separe), nom='acte.pdf', numero=f'PLAN-{separe}')
                self.assertEqual(statut, 400)
                self.assertIn('graphique', corps['error'])

    def test_pdf_texte_sans_graphique_reste_publiable(self):
        from reportlab.pdfgen.canvas import Canvas
        sortie = io.BytesIO()
        document = Canvas(sortie)
        document.drawString(40, 800, 'Article 1. La commune ouvre la salle.')
        document.showPage()
        document.drawString(40, 800, 'Article 2. La decision prend effet le jour du depot.')
        document.save()
        statut, acte = self.deposer(self.referent, sortie.getvalue(), nom='acte.pdf')
        self.assertEqual(statut, 201)
        self.assertEqual(len(acte['paragraphes']), 2)
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{acte["id"]}/reperer', {}, self.referent)
        self.assertEqual(self.appel('POST', f'/api/publications/{acte["id"]}/valider', {}, self.referent)[0], 200)
        self.assertEqual(self.appel('POST', '/api/publications/dossier', {'ids': [acte['id']]}, self.referent)[0], 200)

    def test_refuse_image_dans_word(self):
        from docx import Document
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(image, 'PNG')
        document = Document()
        document.add_paragraph('Voir le plan du périmètre.')
        document.add_picture(image)
        sortie = io.BytesIO()
        document.save(sortie)
        statut, corps = self.deposer(self.agent, sortie.getvalue())
        self.assertEqual(statut, 400)
        self.assertIn('graphique', corps['error'])

    def test_ancien_depot_partiel_ne_peut_plus_etre_diffuse(self):
        ident = self.deposer(self.referent, docx('Acte synthétique.'))[1]['id']
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{ident}/reperer', {}, self.referent)
        self.assertEqual(self.appel('POST', f'/api/publications/{ident}/valider', {}, self.referent)[0], 200)
        self.assertEqual(self.appel('POST', '/api/publications/dossier', {'ids': [ident]}, self.referent)[0], 200)
        # Ancienne fiche validée dont la source réelle comporte une page graphique ignorée au dépôt.
        dossier = server.DATA / 'publication' / ident
        (dossier / 'source.docx').unlink()
        (dossier / 'source.pdf').write_bytes(self.pdf_avec_plan(True))
        for route, params in ((f'{ident}/valider', {}), ('dossier', {'ids': [ident]}), (f'{ident}/en-ligne', {})):
            with self.subTest(route=route):
                statut, corps = self.appel('POST', '/api/publications/' + route, params, self.referent)
                self.assertEqual(statut, 400)
                self.assertIn('graphique', corps['error'])

    def test_confirmer_demain_est_refuse(self):
        ident = self.deposer(self.referent, docx('Acte synthétique.'))[1]['id']
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{ident}/reperer', {}, self.referent)
        self.appel('POST', f'/api/publications/{ident}/valider', {}, self.referent)
        demain = (jour_courant() + timedelta(days=1)).isoformat()
        self.assertEqual(self.appel('POST', '/api/publications/dossier', {'ids': [ident], 'date': demain}, self.referent)[0], 200)
        self.assertEqual(self.appel('POST', f'/api/publications/{ident}/en-ligne', {}, self.referent)[0], 400)
        self.assertEqual(self.appel('GET', f'/api/publications/{ident}', jeton=self.referent)[1]['statut'], 'valide')

    def test_retrait_du_dernier_acte_et_remplacement(self):
        numero = 'D2026-240'
        ancien, _ = self.publier(self.referent, numero)
        retrait = self.appel('POST', f'/api/publications/{ancien}/retrait', {'motif': 'Copie à rectifier'}, self.referent)[1]
        self.assertEqual(self.appel('POST', '/api/publications/dossier', {'ids': []}, self.agent)[0], 403)
        statut, contenu = self.appel('POST', '/api/publications/dossier', {'ids': []}, self.referent)
        self.assertEqual(statut, 200)
        with zipfile.ZipFile(io.BytesIO(contenu)) as index:
            self.assertFalse(any(n.endswith('.pdf') for n in index.namelist()))
            self.assertNotIn(numero, index.read('index.html').decode())
            self.assertEqual(len(index.read('deliberations.csv').splitlines()), 1)
        for champs in ({'type': 'arrete'}, {'date_acte': '2026-10-15'}, {'numero': 'D2026/240'}):
            self.assertEqual(self.deposer(self.agent, docx('Autre acte'), **({'numero': numero} | champs))[0], 400)
        nouveau = self.deposer(self.agent, docx('Copie corrigée.'), numero=numero)
        self.assertEqual(nouveau[0], 201)
        self.assertEqual(nouveau[1]['remplace_id'], ancien)
        self.assertNotEqual(nouveau[1]['id'], ancien)
        self.assertEqual(self.deposer(self.agent, docx('Doublon actif'), numero=numero)[0], 400)
        ancien_lu = self.appel('GET', f'/api/publications/{ancien}', jeton=self.referent)[1]
        self.assertEqual((ancien_lu['statut'], ancien_lu['empreinte_pdf']), ('retire', retrait['empreinte_pdf']))

    def test_depot_pdf_image_refuse(self):
        from reportlab.pdfgen import canvas
        sortie = io.BytesIO()
        page = canvas.Canvas(sortie)
        page.rect(100, 100, 200, 200, fill=1)  # une « image » : aucun texte
        page.save()
        with patch.object(server, '_ocr_ou_rien', return_value=None):  # pas de lecture optique sur ce poste
            statut, corps = self.deposer(self.agent, sortie.getvalue(), nom='acte.pdf')
        self.assertEqual(statut, 400)
        self.assertIn('graphique', corps['error'])

    def test_agent_ne_voit_que_les_siennes(self):
        self.deposer(self.agent, docx('Texte de l’agent'))
        self.deposer(self.referent, docx('Texte du référent'), numero='D2026-013')
        self.assertEqual([a['numero'] for a in self.appel('GET', '/api/publications', jeton=self.agent)[1]['publications']], ['D2026-012'])
        self.assertEqual(len(self.appel('GET', '/api/publications', jeton=self.referent)[1]['publications']), 2)
        autre = self.appel('GET', '/api/publications', jeton=self.referent)[1]['publications'][0]['id']
        self.assertEqual(self.appel('GET', f'/api/publications/{autre}', jeton=self.agent)[0], 404)

    def test_matieres(self):
        statut, corps = self.appel('GET', '/api/publications/matieres', jeton=self.agent)
        self.assertEqual((statut, len(corps['matieres'])), (200, 57))

    def test_import_du_registre_d_un_autre_refuse(self):
        # C2 : un agent n'importe pas (donc ne lit pas) un acte du registre qu'il n'a pas rédigé.
        numero = self.acte_du_registre()
        statut, _ = self.appel('POST', '/api/publications', {'registre': numero, 'date_acte': '2026-10-14'}, self.agent)
        self.assertEqual(statut, 404)

    def publier(self, jeton, numero):
        statut, acte = self.deposer(jeton, docx('Le conseil municipal,', 'DÉCIDE'), numero=numero)
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{acte["id"]}/reperer', {}, jeton)
        self.appel('POST', f'/api/publications/{acte["id"]}/valider', {}, jeton)
        self.appel('POST', '/api/publications/dossier', {'ids': [acte['id']], 'date': aujourdhui()}, jeton)
        reponse = self.derniere
        self.assertEqual(reponse.status, 200)
        self.assertEqual(self.appel('POST', f'/api/publications/{acte["id"]}/en-ligne', {}, jeton)[0], 200)
        return acte['id'], zipfile.ZipFile(io.BytesIO(reponse.body))

    def test_retrait_reserve_aux_valideurs_et_retire_l_acte_de_l_index(self):
        gardé, _ = self.publier(self.referent, 'D2026-021')
        retiré, _ = self.publier(self.referent, 'D2026-022')
        statut, corps = self.appel('POST', f'/api/publications/{retiré}/retrait', {'motif': 'Erreur'}, self.agent)
        self.assertEqual(statut, 404)  # l'acte d'un autre agent reste invisible
        statut, corps = self.appel('POST', f'/api/publications/{retiré}/retrait', {'motif': ''}, self.referent)
        self.assertEqual((statut, corps['error']), (400, 'Indiquez le motif du retrait.'))
        statut, corps = self.appel('POST', f'/api/publications/{retiré}/retrait', {'motif': 'Donnée personnelle visible'}, self.referent)
        self.assertEqual((statut, corps['statut'], corps['retire_motif']), (200, 'retire', 'Donnée personnelle visible'))
        # Le prochain dossier ne reprend plus l'acte retiré, mais garde l'acte publié.
        statut, nouveau = self.deposer(self.referent, docx('Autre acte'), numero='D2026-023')
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{nouveau["id"]}/reperer', {}, self.referent)
        self.appel('POST', f'/api/publications/{nouveau["id"]}/valider', {}, self.referent)
        self.appel('POST', '/api/publications/dossier', {'ids': [nouveau['id']], 'date': aujourdhui()}, self.referent)
        reponse = self.derniere
        index = zipfile.ZipFile(io.BytesIO(reponse.body)).read('index.html').decode()
        self.assertIn('D2026-021', index)
        self.assertIn('D2026-023', index)
        self.assertNotIn('D2026-022', index)

    def test_retrait_d_un_acte_non_publie_refuse(self):
        statut, acte = self.deposer(self.referent, docx('Texte'), numero='D2026-031')
        statut, corps = self.appel('POST', f'/api/publications/{acte["id"]}/retrait', {'motif': 'Erreur'}, self.referent)
        self.assertEqual((statut, corps['error']), (400, 'Seul un acte publié peut être retiré de la publication.'))

    def test_reperage_d_un_acte_retire_n_appelle_pas_l_ia(self):
        retiré, _ = self.publier(self.referent, 'D2026-041')
        self.appel('POST', f'/api/publications/{retiré}/retrait', {'motif': 'Erreur'}, self.referent)
        with patch.object(llm_backend, 'chat') as chat:
            statut, corps = self.appel('POST', f'/api/publications/{retiré}/reperer', {}, self.referent)
        self.assertEqual(statut, 403)
        chat.assert_not_called()

    def test_supprimer_route(self):
        statut, acte = self.deposer(self.agent, docx('Texte'))
        self.assertEqual(self.appel('DELETE', f'/api/publications/{acte["id"]}', jeton=self.agent)[0], 200)
        self.assertEqual(self.appel('GET', f'/api/publications/{acte["id"]}', jeton=self.agent)[0], 404)


class VisiteurPublicationTests(ClientPublication):
    """Démonstration : un visiteur va au bout du parcours avec ses propres actes fictifs, jamais ceux des autres."""
    def setUp(self):
        super().setUp()
        comptes.creer_premier_administrateur(server.DB, {'identifiant': 'admin', 'nom': 'Alice Admin', 'mot_de_passe': MDP_ADMIN})
        correctif = patch.object(web, 'REGLAGES', replace(web.REGLAGES, demonstration=True))
        correctif.start()
        self.addCleanup(correctif.stop)

    def visiteur(self):
        self.appel('POST', '/api/session/visiteur', {})
        return self.jeton_de(self.derniere)

    def test_visiteur_valide_les_siennes_seulement(self):
        a, b = self.visiteur(), self.visiteur()
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        statut, acte = self.appel('POST', '/api/publications/depot?nom=a.docx&type=arrete&numero=A1&date_acte=2026-10-14&objet=Essai',
                                  docx('Arrêté d’essai'), a, entetes)
        self.assertEqual(statut, 201)
        self.assertEqual(self.appel('POST', f'/api/publications/{acte["id"]}/valider', {}, b)[0], 404)
        with ia_qui_trouve():
            self.appel('POST', f'/api/publications/{acte["id"]}/reperer', {}, a)
        self.assertEqual(self.appel('POST', f'/api/publications/{acte["id"]}/valider', {}, a)[1]['statut'], 'valide')

    def deposer_en(self, jeton, numero):
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        return self.appel('POST', f'/api/publications/depot?nom=a.docx&type=arrete&numero={numero}&date_acte=2026-10-14&objet=Essai',
                          docx('Arrêté d’essai'), jeton, entetes)[0]

    def test_les_numeros_des_visiteurs_ne_bloquent_ni_l_administration_ni_les_autres(self):
        # Démonstration : un acte d'essai « A1 » d'un visiteur ne doit pas empêcher l'administration de publier son « A1 ».
        a, b = self.visiteur(), self.visiteur()
        admin = self.connexion('admin', MDP_ADMIN)
        self.assertEqual(self.deposer_en(a, 'A1'), 201)
        self.assertEqual(self.deposer_en(b, 'A1'), 201)
        self.assertEqual(self.deposer_en(admin, 'A1'), 201)
        self.assertEqual(self.deposer_en(a, 'A1'), 400)  # le même visiteur, deux fois : refusé
        self.assertEqual(self.deposer_en(admin, 'A1'), 400)

    def test_l_administration_ne_voit_pas_les_actes_d_essai_des_visiteurs(self):
        a = self.visiteur()
        admin = self.connexion('admin', MDP_ADMIN)
        self.deposer_en(a, 'A7')
        self.deposer_en(admin, 'A8')
        self.assertEqual([x['numero'] for x in self.appel('GET', '/api/publications', jeton=admin)[1]['publications']], ['A8'])
        self.assertEqual([x['numero'] for x in self.appel('GET', '/api/publications', jeton=a)[1]['publications']], ['A7'])


if __name__ == '__main__':
    unittest.main()
