# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Routes de publication : parcours complet, droits de validation, dépôts."""
import io
import json
import sys
import unittest
import zipfile
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


def docx(*paragraphes):
    from docx import Document
    document = Document()
    for p in paragraphes:
        document.add_paragraph(p)
    sortie = io.BytesIO()
    document.save(sortie)
    return sortie.getvalue()


class PublicationWebTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = self.premier_admin()
        self.referent = self.creer(self.admin, 'referent', 'referent')[1]
        self.agent_id, self.agent = self.creer(self.admin, 'agent')
        branding.save(server.DATA, {'collectivite': 'Commune de Démoville', 'siret': '21860194600013'})

    def acte_du_registre(self):
        ident = 'a' * 32
        (server.DATA / 'arretes').mkdir(exist_ok=True)
        document = arrete_document.assemble({'collectivite': 'Commune de Démoville'},
                                            {'objet': 'Stationnement pour un déménagement',
                                             'considerant': 'Considérant la demande de M. Jean Martin, 12 rue des Lilas.',
                                             'articles': [], 'destinataires': 'Services compétents'})
        arrete_document.create_word(server.DATA / 'arretes' / f'{ident}.docx', document)
        return registre.attribuer(server.DB, ident, 'arrete', 'Stationnement pour un déménagement')

    def deposer(self, jeton, contenu, nom='acte.docx', **champs):
        champs = {'type': 'deliberation', 'numero': 'D2026-012', 'date_acte': '2026-10-14', 'objet': 'Cession d’un terrain',
                  'matiere_code': '3.2'} | champs
        requete = '&'.join(f'{k}={v}' for k, v in ({'nom': nom} | champs).items())
        entetes = NAVIGATEUR | {'Content-Type': 'application/octet-stream'}
        return self.appel('POST', f'/api/publications/depot?{requete}', contenu, jeton, entetes)

    def test_parcours_complet_referent(self):
        numero = self.acte_du_registre()
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
        reponse = web.handle('POST', '/api/publications/dossier', NAVIGATEUR | {'Cookie': f'{comptes.COOKIE}={self.referent}'},
                             json.dumps({'ids': [acte['id']], 'date': aujourdhui()}).encode())
        self.assertEqual(reponse.status, 200)
        self.assertEqual(dict(reponse.headers)['Content-Type'], 'application/zip')
        archive = zipfile.ZipFile(io.BytesIO(reponse.body))
        self.assertIn(numero.replace('/', '-') + '.pdf', archive.namelist())
        lu = self.appel('GET', f'/api/publications/{acte["id"]}', jeton=self.referent)[1]
        self.assertEqual(len(lu['empreinte_pdf']), 64)
        statut, publie = self.appel('POST', f'/api/publications/{acte["id"]}/en-ligne', {'date': aujourdhui()}, self.referent)
        self.assertEqual((statut, publie['statut'], publie['publie_le']), (200, 'publie', aujourdhui()))

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

    def test_depot_pdf_image_refuse(self):
        from reportlab.pdfgen import canvas
        sortie = io.BytesIO()
        page = canvas.Canvas(sortie)
        page.rect(100, 100, 200, 200, fill=1)  # une « image » : aucun texte
        page.save()
        with patch.object(server, '_ocr_ou_rien', return_value=None):  # pas de lecture optique sur ce poste
            statut, corps = self.deposer(self.agent, sortie.getvalue(), nom='acte.pdf')
        self.assertEqual((statut, corps['error']), (400, 'Ce PDF est une image : déposez la version Word, ou un PDF texte.'))

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
        reponse = web.handle('POST', '/api/publications/dossier', NAVIGATEUR | {'Cookie': f'{comptes.COOKIE}={jeton}'},
                             json.dumps({'ids': [acte['id']], 'date': aujourdhui()}).encode())
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
        reponse = web.handle('POST', '/api/publications/dossier', NAVIGATEUR | {'Cookie': f'{comptes.COOKIE}={self.referent}'},
                             json.dumps({'ids': [nouveau['id']], 'date': aujourdhui()}).encode())
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


class VisiteurPublicationTests(Base):
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
