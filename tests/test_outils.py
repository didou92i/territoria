# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import contextlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import corpus_arretes
import e2e
import outils

DONNEES_PAR_DEFAUT = outils.DONNEES  # lu à l'import, avant que les tests ne la remplacent par un dossier temporaire
ALPHABET_URLSAFE = re.compile(r'[A-Za-z0-9_-]{48}')
SORTIE = re.compile(r'ETAPE (?P<commande>[a-z0-9-]+) : (?P<etat>OK|ECHEC [a-z_]+)')


def executer(*argv):
    """Exécute `outils.main` et renvoie (code de sortie, texte imprimé sur la sortie standard)."""
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(io.StringIO()):
        code = outils.main(list(argv))
    return code, sortie.getvalue()


def lire_env(chemin):
    """Variables d'un fichier d'environnement Compose (NOM=valeur, une par ligne)."""
    valeurs = {}
    for ligne in Path(chemin).read_text(encoding='utf-8').splitlines():
        if '=' in ligne and not ligne.lstrip().startswith('#'):
            nom, _, valeur = ligne.partition('=')
            valeurs[nom] = valeur
    return valeurs


def mode(chemin):
    return stat.S_IMODE(os.stat(chemin).st_mode)


class OutilsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / 'secrets'


class SecretsTests(OutilsBase):
    def test_secrets_cree_les_deux_fichiers_en_0600(self):
        code, texte = executer('secrets', '--dossier', str(self.dir))
        self.assertEqual((code, texte.strip()), (0, 'ETAPE secrets : OK'))
        territoria = lire_env(self.dir / 'territoria.env')
        self.assertEqual(set(territoria), {'TERRITORIA_SERVICE_TOKEN', 'TERRITORIA_WEBHOOK_TOKEN'})
        self.assertEqual(set(lire_env(self.dir / 'n8n.env')), {'N8N_ENCRYPTION_KEY'})
        for valeur in (*territoria.values(), lire_env(self.dir / 'n8n.env')['N8N_ENCRYPTION_KEY']):
            self.assertRegex(valeur, ALPHABET_URLSAFE)  # token_urlsafe(36) : 48 caractères
        self.assertNotEqual(territoria['TERRITORIA_SERVICE_TOKEN'], territoria['TERRITORIA_WEBHOOK_TOKEN'])
        for nom in ('territoria.env', 'n8n.env'):
            self.assertEqual(mode(self.dir / nom), 0o600, nom)

    def test_secrets_idempotents(self):
        outils.main(['secrets', '--dossier', str(self.dir)])
        avant = (self.dir / 'n8n.env').read_text()
        outils.main(['secrets', '--dossier', str(self.dir)])
        self.assertEqual((self.dir / 'n8n.env').read_text(), avant)

    def test_secrets_idempotents_pour_territoria(self):
        executer('secrets', '--dossier', str(self.dir))
        avant = (self.dir / 'territoria.env').read_bytes()
        code, texte = executer('secrets', '--dossier', str(self.dir))
        self.assertEqual((code, texte.strip()), (0, 'ETAPE secrets : OK'))
        self.assertEqual((self.dir / 'territoria.env').read_bytes(), avant)

    def test_secrets_complete_un_fichier_existant_sans_toucher_au_reste(self):
        self.dir.mkdir()
        existant = self.dir / 'territoria.env'
        existant.write_bytes(b'# fichier de la collectivite\nTERRITORIA_SERVICE_TOKEN=valeur-deja-la\nAUTRE=1')  # sans fin de ligne
        existant.chmod(0o644)
        code, _ = executer('secrets', '--dossier', str(self.dir))
        self.assertEqual(code, 0)
        contenu = existant.read_bytes()
        self.assertTrue(contenu.startswith(b'# fichier de la collectivite\nTERRITORIA_SERVICE_TOKEN=valeur-deja-la\nAUTRE=1\n'))
        valeurs = lire_env(existant)
        self.assertEqual(valeurs['TERRITORIA_SERVICE_TOKEN'], 'valeur-deja-la')
        self.assertEqual(valeurs['AUTRE'], '1')
        self.assertRegex(valeurs['TERRITORIA_WEBHOOK_TOKEN'], ALPHABET_URLSAFE)
        self.assertEqual(mode(existant), 0o600)

    def test_secrets_remplit_un_fichier_vide_cree_par_l_installation(self):
        self.dir.mkdir()
        (self.dir / 'territoria.env').touch()
        (self.dir / 'n8n.env').touch()
        code, _ = executer('secrets', '--dossier', str(self.dir))
        self.assertEqual(code, 0)
        self.assertRegex(lire_env(self.dir / 'n8n.env')['N8N_ENCRYPTION_KEY'], ALPHABET_URLSAFE)

    def test_secrets_ne_remplace_jamais_une_variable_vide(self):
        self.dir.mkdir()
        (self.dir / 'territoria.env').write_text('TERRITORIA_SERVICE_TOKEN=\n', encoding='utf-8')
        code, texte = executer('secrets', '--dossier', str(self.dir))
        self.assertEqual((code, texte.strip()), (1, 'ETAPE secrets : ECHEC variable_vide'))
        self.assertEqual((self.dir / 'territoria.env').read_text(encoding='utf-8'), 'TERRITORIA_SERVICE_TOKEN=\n')

    def test_secrets_ne_montre_aucune_valeur(self):
        _, texte = executer('secrets', '--dossier', str(self.dir))
        for valeur in (*lire_env(self.dir / 'territoria.env').values(), *lire_env(self.dir / 'n8n.env').values()):
            self.assertNotIn(valeur, texte)

    def test_secrets_dossier_impossible_a_creer(self):
        bloc = Path(self._tmp.name) / 'fichier'
        bloc.write_text('x', encoding='utf-8')
        code, texte = executer('secrets', '--dossier', str(bloc / 'secrets'))
        self.assertEqual((code, texte.strip()), (1, 'ETAPE secrets : ECHEC dossier_inaccessible'))


class InterfaceTests(OutilsBase):
    def test_aucune_option_secrete(self):
        texte = outils.construire_parseur().format_help().lower()
        for mot in ('mot-de-passe', 'password', 'secret=', 'token'): self.assertNotIn(mot, texte)

    def test_aucune_sous_commande_n_accepte_un_secret(self):
        parseur = outils.construire_parseur()
        sous_commandes = next(a for a in parseur._actions if a.choices).choices
        self.assertGreaterEqual(set(sous_commandes), {'secrets', 'provision-n8n', 'proprietaire-n8n'})
        for nom, sous in sous_commandes.items():
            options = {chaine for action in sous._actions for chaine in action.option_strings}
            for option in options:
                for mot in ('mot-de-passe', 'password', 'token', 'jeton', 'cle', 'secret'):
                    self.assertNotIn(mot, option, f'{nom} {option}')
            self.assertFalse({o for o in options if o not in ('-h', '--help')} - {
                '--dossier', '--sortie', '--adresse-n8n', '--strict',
                '--source', '--destination', '--archive', '--cible', '--forcer', '--depuis-fichier', '--essai', '--base',
                '--nom'}, nom)  # --nom : nom affiché du premier administrateur, jamais un secret

    def test_options_inconnues_donnent_un_echec_et_le_code_1(self):
        code, texte = executer('secrets', '--mot-de-passe', 'x')
        self.assertEqual((code, texte.strip()), (1, 'ETAPE secrets : ECHEC arguments_invalides'))

    def test_commande_inconnue_ou_absente(self):
        self.assertEqual(executer('inexistante'), (1, 'ETAPE outils : ECHEC arguments_invalides\n'))
        self.assertEqual(executer(), (1, 'ETAPE outils : ECHEC arguments_invalides\n'))

    def test_dossier_par_defaut(self):
        self.assertEqual(outils.construire_parseur().parse_args(['secrets']).dossier, Path('/donnees-secrets'))

    def test_chaque_commande_imprime_une_seule_ligne_conforme(self):
        for argv in (('secrets', '--dossier', str(self.dir)),
                     ('provision-n8n', '--dossier', str(self.dir / 'absent'), '--sortie', str(self.dir / 'p')),
                     ('inexistante',)):
            _, texte = executer(*argv)
            self.assertRegex(texte, r'\AETAPE [a-z0-9-]+ : (OK|ECHEC [a-z_]+)\n\Z')


class ProvisionTests(OutilsBase):
    def setUp(self):
        super().setUp()
        executer('secrets', '--dossier', str(self.dir))
        self.sortie = self.dir / 'provision'
        self.env = lire_env(self.dir / 'territoria.env')

    def provisionner(self):
        return executer('provision-n8n', '--dossier', str(self.dir), '--sortie', str(self.sortie))

    def test_ecrit_les_deux_credentials_en_clair(self):
        code, texte = self.provisionner()
        self.assertEqual((code, texte.strip()), (0, 'ETAPE provision-n8n : OK'))
        credentials = json.loads((self.sortie / 'credentials.json').read_text(encoding='utf-8'))
        self.assertEqual(credentials, [
            {'id': 'TerritoriaWebh01', 'name': 'Territoria — entrée privée', 'type': 'httpHeaderAuth',
             'data': {'name': 'X-Territoria-Webhook-Token', 'value': self.env['TERRITORIA_WEBHOOK_TOKEN']}},
            {'id': 'TerritoriaServ01', 'name': 'Territoria — services internes', 'type': 'httpHeaderAuth',
             'data': {'name': 'X-Territoria-Service-Token', 'value': self.env['TERRITORIA_SERVICE_TOKEN']}},
        ])

    def test_copie_les_workflows_dans_le_meme_dossier(self):
        self.provisionner()
        attendus = sorted(p.name for p in outils.dossier_workflows().glob('*.json'))
        self.assertEqual(attendus, ['arretes.json', 'conversation.json'])
        for nom in attendus:
            self.assertEqual((self.sortie / nom).read_bytes(), (outils.dossier_workflows() / nom).read_bytes())

    def test_dossier_et_fichiers_prives(self):
        self.provisionner()
        self.assertEqual(mode(self.sortie), 0o700)
        for fichier in self.sortie.iterdir():
            self.assertEqual(mode(fichier), 0o600, fichier.name)

    def test_ne_montre_aucun_jeton(self):
        _, texte = self.provisionner()
        for valeur in self.env.values():
            self.assertNotIn(valeur, texte)

    def test_peut_etre_relance(self):
        self.provisionner()
        code, _ = self.provisionner()
        self.assertEqual(code, 0)

    def test_jetons_absents(self):
        (self.dir / 'territoria.env').write_text('TERRITORIA_SERVICE_TOKEN=abc\n', encoding='utf-8')
        code, texte = self.provisionner()
        self.assertEqual((code, texte.strip()), (1, 'ETAPE provision-n8n : ECHEC secrets_absents'))
        self.assertFalse((self.sortie / 'credentials.json').exists())

    def test_fichier_de_secrets_absent(self):
        (self.dir / 'territoria.env').unlink()
        code, texte = self.provisionner()
        self.assertEqual((code, texte.strip()), (1, 'ETAPE provision-n8n : ECHEC secrets_absents'))

    def test_sortie_par_defaut_dans_le_dossier_des_secrets(self):
        code, _ = executer('provision-n8n', '--dossier', str(self.dir))
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / 'provision' / 'credentials.json').is_file())


class FauxN8n(BaseHTTPRequestHandler):
    reponses = []  # (statut, corps JSON) à servir dans l'ordre
    recus = []

    def do_POST(self):
        longueur = int(self.headers.get('Content-Length', 0))
        FauxN8n.recus.append((self.path, self.headers.get('Content-Type'), json.loads(self.rfile.read(longueur))))
        statut, corps = FauxN8n.reponses.pop(0)
        donnees = json.dumps(corps).encode()
        self.send_response(statut)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def log_message(self, *args):
        pass


class ProprietaireTests(OutilsBase):
    def setUp(self):
        super().setUp()
        FauxN8n.reponses, FauxN8n.recus = [], []
        self.serveur = HTTPServer(('127.0.0.1', 0), FauxN8n)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.addCleanup(self.serveur.server_close)
        self.addCleanup(self.serveur.shutdown)
        self.adresse = f'http://127.0.0.1:{self.serveur.server_address[1]}'

    def lancer(self):
        return executer('proprietaire-n8n', '--dossier', str(self.dir), '--adresse-n8n', self.adresse)

    def test_adresse_n8n_par_defaut_sur_le_reseau_interne(self):
        args = outils.construire_parseur().parse_args(['proprietaire-n8n'])
        self.assertEqual(args.adresse_n8n, 'http://n8n:5678')

    def test_cree_le_proprietaire_avec_un_mot_de_passe_prive(self):
        FauxN8n.reponses = [(200, {'data': {'id': 'x'}})]
        code, texte = self.lancer()
        self.assertEqual((code, texte.strip()), (0, 'ETAPE proprietaire-n8n : OK'))
        chemin, type_contenu, corps = FauxN8n.recus[0]
        self.assertEqual((chemin, type_contenu), ('/rest/owner/setup', 'application/json'))
        self.assertEqual(corps['email'], 'admin@territoria.local')
        fichier = self.dir / 'n8n-admin.txt'
        self.assertEqual(mode(fichier), 0o600)
        contenu = fichier.read_text(encoding='utf-8')
        self.assertIn('admin@territoria.local', contenu)
        self.assertIn(corps['password'], contenu)
        self.assertNotIn(corps['password'], texte)
        mot = corps['password']  # exigences de n8n : 8 à 64 caractères, au moins un chiffre et une majuscule
        self.assertTrue(8 <= len(mot) <= 64 and re.search(r'\d', mot) and re.search(r'[A-Z]', mot) and re.search(r'[a-z]', mot))

    def test_relance_apres_succes_reutilise_le_mot_de_passe_enregistre(self):
        FauxN8n.reponses = [(200, {}), (400, {'message': 'Instance owner already setup'})]
        self.lancer()
        premier = (self.dir / 'n8n-admin.txt').read_text(encoding='utf-8')
        code, texte = self.lancer()
        self.assertEqual((code, texte.strip()), (0, 'ETAPE proprietaire-n8n : OK'))
        self.assertEqual((self.dir / 'n8n-admin.txt').read_text(encoding='utf-8'), premier)
        self.assertEqual(FauxN8n.recus[0][2]['password'], FauxN8n.recus[1][2]['password'])

    def test_proprietaire_deja_cree_sans_mot_de_passe_enregistre(self):
        FauxN8n.reponses = [(400, {'message': 'Instance owner already setup'})]
        code, texte = self.lancer()
        self.assertEqual((code, texte.strip()), (1, 'ETAPE proprietaire-n8n : ECHEC proprietaire_deja_cree'))
        self.assertFalse((self.dir / 'n8n-admin.txt').exists())

    def test_route_absente(self):
        FauxN8n.reponses = [(404, {'message': 'not found'})]
        code, texte = self.lancer()
        self.assertEqual((code, texte.strip()), (1, 'ETAPE proprietaire-n8n : ECHEC route_absente'))
        self.assertFalse((self.dir / 'n8n-admin.txt').exists())

    def test_n8n_injoignable(self):
        self.serveur.shutdown()
        self.serveur.server_close()
        code, texte = self.lancer()
        self.assertEqual((code, texte.strip()), (1, 'ETAPE proprietaire-n8n : ECHEC n8n_injoignable'))
        self.assertFalse((self.dir / 'n8n-admin.txt').exists())


class DemoTests(OutilsBase):
    def setUp(self):
        super().setUp()
        self.donnees = Path(self._tmp.name) / 'donnees'
        self.donnees.mkdir()
        patcher = patch.object(outils, 'DONNEES', self.donnees)
        patcher.start()
        self.addCleanup(patcher.stop)

    def contenu(self):
        return {p.relative_to(self.donnees): p.read_bytes() for p in self.donnees.rglob('*') if p.is_file()}

    def test_donnees_par_defaut(self):
        self.assertEqual(DONNEES_PAR_DEFAUT, Path('/donnees'))
        args = outils.construire_parseur().parse_args(['demo'])
        self.assertEqual(vars(args).keys(), {'commande', 'executer'})  # aucune option

    def test_installe_le_corpus_fictif_avec_une_seule_ligne(self):
        code, texte = executer('demo')
        self.assertEqual((code, texte), (0, 'ETAPE demo : OK\n'))  # la progression de l'indexation n'est pas imprimée
        self.assertTrue((self.donnees / 'modeles' / 'manifest.json').is_file())
        self.assertEqual(len(list((self.donnees / 'bibliotheque-arretes' / 'pdf').glob('*.pdf'))), 3)

    def test_relance_sans_rien_changer(self):
        executer('demo')
        avant = self.contenu()
        self.assertEqual(executer('demo'), (0, 'ETAPE demo : OK\n'))
        self.assertEqual(self.contenu(), avant)

    def test_un_modele_existant_est_conserve(self):
        (self.donnees / 'modeles').mkdir()
        (self.donnees / 'modeles' / 'manifest.json').write_text('{"reference": "mien.pdf"}', encoding='utf-8')
        self.assertEqual(executer('demo'), (0, 'ETAPE demo : OK\n'))
        self.assertEqual(list(self.contenu()), [Path('modeles/manifest.json')])

    def test_une_bibliotheque_etrangere_sans_modele_est_un_echec(self):
        racine = self.donnees / 'bibliotheque-arretes'
        racine.mkdir()
        (racine / 'manifest.json').write_text(json.dumps({'files': [{'id': 'arrete-reel-0001-xxxxxxxxxxxx'}]}), encoding='utf-8')
        avant = self.contenu()
        self.assertEqual(executer('demo'), (1, 'ETAPE demo : ECHEC bibliotheque_etrangere\n'))
        self.assertEqual(self.contenu(), avant)

    def test_dossier_de_donnees_inaccessible(self):
        bloc = Path(self._tmp.name) / 'fichier'
        bloc.write_text('x', encoding='utf-8')
        with patch.object(outils, 'DONNEES', bloc / 'donnees'):
            self.assertEqual(executer('demo'), (1, 'ETAPE demo : ECHEC ecriture_impossible\n'))

    def test_indexation_impossible(self):
        with patch.object(corpus_arretes, 'build_index', return_value={'documents': 0, 'pages': 0, 'errors': [{'id': 'x'}]}):
            self.assertEqual(executer('demo'), (1, 'ETAPE demo : ECHEC indexation_impossible\n'))
        self.assertFalse((self.donnees / 'modeles' / 'manifest.json').exists())


class VerifierTests(OutilsBase):
    def test_une_seule_option_non_secrete(self):
        parseur = outils.construire_parseur()
        self.assertEqual(vars(parseur.parse_args(['verifier'])).keys(), {'commande', 'executer', 'strict'})
        self.assertIs(parseur.parse_args(['verifier']).strict, False)
        self.assertIs(parseur.parse_args(['verifier', '--strict']).strict, True)
        sous = next(a for a in parseur._actions if a.choices).choices['verifier']
        self.assertEqual({o for a in sous._actions for o in a.option_strings} - {'-h', '--help'}, {'--strict'})

    def test_appelle_e2e_executer_hors_strict_par_defaut(self):
        with patch.object(e2e, 'executer', return_value=['espace']) as executer_e2e:
            self.assertEqual(executer('verifier'), (0, 'ETAPE verifier : OK\n'))
        executer_e2e.assert_called_once_with(strict=False, jeton='')

    def test_strict_est_transmis_a_e2e(self):
        with patch.object(e2e, 'executer', return_value=['espace']) as executer_e2e:
            self.assertEqual(executer('verifier', '--strict'), (0, 'ETAPE verifier : OK\n'))
        executer_e2e.assert_called_once_with(strict=True, jeton='')

    def test_un_echec_en_strict_est_nomme_comme_sans(self):
        with patch.object(e2e, 'executer', side_effect=AssertionError('reponse_sourcee')):
            self.assertEqual(executer('verifier', '--strict'), (1, 'ETAPE verifier : ECHEC reponse_sourcee\n'))

    def test_verifier_n_accepte_pas_d_autre_option(self):
        self.assertEqual(executer('verifier', '--mot-de-passe', 'x'), (1, 'ETAPE verifier : ECHEC arguments_invalides\n'))

    def test_un_controle_en_echec_est_nomme(self):
        for nom, code in (('arrete_word', 'arrete_word'), ('espace', 'espace'), ('recherche_page2', 'recherche_page')):
            with self.subTest(controle=nom), patch.object(e2e, 'executer', side_effect=AssertionError(nom)):
                self.assertEqual(executer('verifier'), (1, f'ETAPE verifier : ECHEC {code}\n'))
                self.assertRegex(code, r'\A[a-z_]+\Z')

    def test_tous_les_controles_donnent_un_code_conforme(self):
        for nom, _ in e2e.CONTROLES:
            with patch.object(e2e, 'executer', side_effect=AssertionError(nom)):
                _, texte = executer('verifier')
                self.assertRegex(texte, r'\AETAPE verifier : ECHEC [a-z_]+\n\Z')

    def test_une_assertion_sans_nom_connu_ne_montre_rien(self):
        for erreur in (AssertionError(), AssertionError('Contenu confidentiel'), AssertionError('arrete_word', 'suite'),
                       AssertionError(42)):
            with self.subTest(erreur=repr(erreur)), patch.object(e2e, 'executer', side_effect=erreur):
                self.assertEqual(executer('verifier'), (1, 'ETAPE verifier : ECHEC controle_echoue\n'))

    def test_une_autre_exception_ne_montre_rien(self):
        with patch.object(e2e, 'executer', side_effect=ConnectionRefusedError('Contenu confidentiel')):
            self.assertEqual(executer('verifier'), (1, 'ETAPE verifier : ECHEC erreur_interne\n'))


if __name__ == '__main__':
    unittest.main()
