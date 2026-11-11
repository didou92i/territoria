# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
CONTENEURS = RACINE / 'conteneurs'
DOCKER = shutil.which('docker')


def _compose_ok():
    if not DOCKER:
        return False
    try:
        return subprocess.run([DOCKER, 'compose', 'version'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


COMPOSE_OK = _compose_ok()
HOTE = {'chemin': ''}


def setUpModule():
    # Compose vérifie l'existence des fichiers d'environnement : un dossier de données factice, vide, suffit.
    dossier = Path(tempfile.mkdtemp(prefix='territoria-test-compose-'))
    for sous_dossier in ('territoria', 'n8n', 'secrets'):
        (dossier / sous_dossier).mkdir()
    for nom in ('territoria.env', 'n8n.env'):
        (dossier / 'secrets' / nom).touch()
    HOTE['chemin'] = str(dossier)


def tearDownModule():
    shutil.rmtree(HOTE['chemin'], ignore_errors=True)


def lire_config(fichiers, env_extra=None, hote='', options=(), env_file=None):
    """Configuration Compose résolue, lue en JSON ; `hote=None` retire la variable pour vérifier qu'elle est exigée."""
    if hote == '':
        hote = HOTE['chemin']
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TERRITORIA_', 'COMPOSE_'))}
    if hote is not None:
        env['TERRITORIA_DONNEES_HOTE'] = hote
    env.update(env_extra or {})
    commande = [DOCKER, 'compose']
    if env_file:
        commande += ['--env-file', str(env_file)]
    for fichier in fichiers:
        commande += ['-f', str(CONTENEURS / fichier)]
    return subprocess.run(commande + ['config', *options, '--format', 'json'], capture_output=True, text=True, env=env,
                          cwd=tempfile.gettempdir(), timeout=60)


@unittest.skipUnless(COMPOSE_OK, 'docker compose absent : configuration Compose non vérifiée')
class ComposeSoloTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resultat = lire_config(['compose.yaml'])
        if resultat.returncode != 0:
            raise AssertionError(f'docker compose config a échoué : {resultat.stderr.strip()}')
        cls.config = json.loads(resultat.stdout)
        cls.services = cls.config['services']

    def test_n8n_seulement_sur_le_reseau_interne_sans_sortie(self):
        self.assertEqual(set(self.services), {'territoria', 'n8n', 'travaux'})  # travaux : aucun réseau (ComposeTravauxTests)
        self.assertEqual(set(self.services['n8n']['networks']), {'interne'})
        self.assertEqual(self.config['networks']['interne']['driver'], 'bridge')
        self.assertIs(self.config['networks']['interne']['internal'], True)

    def test_territoria_sur_le_reseau_interne_et_sur_le_reseau_de_sortie(self):
        # sortie : Légifrance, LM Studio par host-gateway et le port publié (Docker ne publie pas par un réseau internal).
        self.assertEqual(set(self.services['territoria']['networks']), {'interne', 'sortie'})
        sortie = self.config['networks']['sortie']
        self.assertEqual(sortie['driver'], 'bridge')
        self.assertFalse(sortie.get('internal', False))

    def test_nom_de_projet_territoria(self):
        self.assertEqual(self.config['name'], 'territoria')

    def test_n8n_sans_port_publie(self):
        self.assertNotIn('ports', self.services['n8n'])

    def test_territoria_publie_en_boucle_locale(self):
        ports = self.services['territoria']['ports']
        self.assertEqual(len(ports), 1)
        self.assertEqual((ports[0]['host_ip'], ports[0]['published'], ports[0]['target']), ('127.0.0.1', '8878', 8878))

    def test_port_hote_configurable_sans_changer_le_port_du_conteneur(self):
        resultat = lire_config(['compose.yaml'], {'TERRITORIA_PORT': '9001'})
        service = json.loads(resultat.stdout)['services']['territoria']
        self.assertEqual((service['ports'][0]['published'], service['ports'][0]['target']), ('9001', 8878))
        self.assertEqual(service['environment']['TERRITORIA_ORIGINE'], 'http://127.0.0.1:9001')

    def test_images_epinglees(self):
        self.assertRegex(self.services['n8n']['image'], r'^docker\.n8n\.io/n8nio/n8n:2\.45\.5@sha256:[0-9a-f]{64}$')

    def test_n8n_sans_telemetrie(self):
        env = self.services['n8n']['environment']
        self.assertEqual((env['N8N_DIAGNOSTICS_ENABLED'], env['EXECUTIONS_DATA_SAVE_ON_SUCCESS']), ('false', 'none'))
        for nom in ('N8N_VERSION_NOTIFICATIONS_ENABLED', 'N8N_TEMPLATES_ENABLED', 'N8N_PERSONALIZATION_ENABLED',
                    'EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS'):
            self.assertEqual(env[nom], 'false', nom)
        self.assertEqual(env['EXECUTIONS_DATA_SAVE_ON_ERROR'], 'none')
        self.assertEqual((env['TZ'], env['GENERIC_TIMEZONE'], env['N8N_RUNNERS_ENABLED']),
                         ('Europe/Paris', 'Europe/Paris', 'true'))

    def test_n8n_detruit_vite_les_executions_non_conservees(self):
        # « none » ne suffit pas : n8n 2.x garde l'exécution supprimée (donc la question reçue) 1 h de plus par défaut.
        env = self.services['n8n']['environment']
        self.assertEqual((env['EXECUTIONS_DATA_HARD_DELETE_BUFFER'], env['EXECUTIONS_DATA_PRUNE_HARD_DELETE_INTERVAL']), ('0', '1'))

    def test_n8n_ne_garde_pas_une_execution_interrompue(self):
        # Une exécution « crashed » (n8n arrêté pendant une demande) garde le corps du webhook : 336 h par défaut, donc
        # dans les sauvegardes. EXECUTIONS_DATA_MAX_AGE (en heures) : nom relevé dans @n8n/config de l'image 2.45.5.
        self.assertEqual(self.services['n8n']['environment']['EXECUTIONS_DATA_MAX_AGE'], '1')

    def test_appels_internes_jamais_par_un_proxy(self):
        # Podman copie par défaut les variables de proxy de la machine dans les conteneurs : les noms internes et la
        # machine elle-même doivent les contourner, dans territoria comme dans n8n, en majuscules et en minuscules.
        attendus = {'n8n', 'territoria', 'faux-llm', 'host.docker.internal', 'host.containers.internal', 'localhost',
                    '127.0.0.1'}
        for nom in ('territoria', 'n8n'):
            env = self.services[nom]['environment']
            for variable in ('NO_PROXY', 'no_proxy'):
                with self.subTest(service=nom, variable=variable):
                    self.assertEqual(set(env[variable].split(',')), attendus)

    def test_conteneurs_en_lecture_seule_avec_tmp_en_memoire(self):
        for nom, service in self.services.items():
            self.assertIs(service['read_only'], True, nom)
            self.assertIn('/tmp', [t.split(':')[0] for t in service['tmpfs']], nom)

    def test_tmpfs_avec_mode_explicite_pour_survivre_a_un_redemarrage(self):
        # Sans mode, un tmpfs est en 1777 au premier démarrage puis en 0755 root quand le même conteneur redémarre :
        # l'utilisateur du service n'y écrit plus (n8n s'arrête sur « EACCES: mkdir '/home/node/.cache/n8n' »).
        for nom, service in self.services.items():
            for montage in service['tmpfs']:
                chemin, _, options = montage.partition(':')
                self.assertIn('mode=', options, f'{nom} {chemin}')
        n8n = {t.split(':')[0]: t.partition(':')[2] for t in self.services['n8n']['tmpfs']}
        self.assertEqual(n8n['/home/node/.cache'], 'uid=1000,gid=1000,mode=0700')  # utilisateur « node » de l'image n8n
        self.assertEqual({t.split(':')[0]: t.partition(':')[2] for t in self.services['territoria']['tmpfs']}['/tmp'], 'mode=1777')

    def test_chemins_de_donnees_partent_de_la_variable_hote(self):
        hote = HOTE['chemin']
        volumes = {nom: {v['target']: v['source'] for v in service['volumes']} for nom, service in self.services.items()}
        self.assertEqual(volumes['territoria'], {'/donnees': f'{hote}/territoria'})
        self.assertEqual(volumes['n8n'], {'/home/node/.n8n': f'{hote}/n8n'})
        # Fichiers d'environnement : Compose remplace env_file par son contenu (et, selon sa version, `--no-env-resolution`
        # garde ou non le chemin) ; une variable témoin écrite dans chaque fichier doit donc ressortir dans son service.
        with tempfile.TemporaryDirectory(prefix='territoria-test-compose-') as autre:
            for sous_dossier in ('territoria', 'n8n', 'secrets'):
                (Path(autre) / sous_dossier).mkdir()
            for nom in ('territoria', 'n8n'):
                (Path(autre) / 'secrets' / f'{nom}.env').write_text(f'TEMOIN_ENV_FILE={nom}\n', encoding='utf-8')
            services = json.loads(lire_config(['compose.yaml'], hote=autre).stdout)['services']
        self.assertEqual(services['territoria']['environment']['TEMOIN_ENV_FILE'], 'territoria')
        self.assertEqual(services['n8n']['environment']['TEMOIN_ENV_FILE'], 'n8n')

    def test_dossier_de_donnees_exige(self):
        resultat = lire_config(['compose.yaml'], hote=None)
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn('TERRITORIA_DONNEES_HOTE', resultat.stderr)

    def test_environnement_de_territoria(self):
        env = self.services['territoria']['environment']
        self.assertEqual(env['TERRITORIA_ORIGINE'], 'http://127.0.0.1:8878')
        self.assertEqual(env['TERRITORIA_HORS_LIGNE'], '0')
        self.assertEqual(env['TERRITORIA_DEMONSTRATION'], '0')  # démonstration éteinte par défaut
        self.assertEqual(env['TERRITORIA_IA_EN_LIGNE'], '')  # IA locale par défaut ; « mistral » par l'étape mistral
        self.assertIn('TERRITORIA_LLM_URL', env)
        self.assertIn('TERRITORIA_MODELE', env)
        self.assertEqual(env['TERRITORIA_TRANSCRIPTION_MODELE'], '')  # même variable que le conteneur de travaux ; vide = défaut
        self.assertNotIn('TERRITORIA_MODE', env)  # le mode solo est le défaut de l'application

    def test_aucun_avertissement_quand_les_reglages_ia_ne_sont_pas_fournis(self):
        resultat = lire_config(['compose.yaml'])
        self.assertEqual(resultat.returncode, 0)
        self.assertNotIn('is not set', resultat.stderr)
        env = json.loads(resultat.stdout)['services']['territoria']['environment']
        self.assertEqual((env['TERRITORIA_LLM_URL'], env['TERRITORIA_MODELE']), ('', ''))  # vide = défaut de l'application

    def test_reglages_ia_viennent_du_fichier_compose_env(self):
        with tempfile.TemporaryDirectory() as dossier:
            fichier = Path(dossier) / 'compose.env'
            fichier.write_text('TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1\n'
                               'TERRITORIA_MODELE=un-modele-local\n'
                               'TERRITORIA_HORS_LIGNE=1\n', encoding='utf-8')
            resultat = lire_config(['compose.yaml'], env_file=fichier)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertNotIn('is not set', resultat.stderr)
        env = json.loads(resultat.stdout)['services']['territoria']['environment']
        self.assertEqual(env['TERRITORIA_LLM_URL'], 'http://host.docker.internal:1234/v1')
        self.assertEqual((env['TERRITORIA_MODELE'], env['TERRITORIA_HORS_LIGNE']), ('un-modele-local', '1'))

    def test_territoria_atteint_le_serveur_ia_de_l_hote_et_attend_n8n(self):
        service = self.services['territoria']
        self.assertEqual(service['extra_hosts'], ['host.docker.internal=host-gateway'])
        self.assertEqual(list(service['depends_on']), ['n8n'])

    def test_aucun_service_ne_configure_le_proxy_en_solo(self):
        self.assertNotIn('proxy', self.services)


@unittest.skipUnless(COMPOSE_OK, 'docker compose absent : conteneur de transcription non vérifié')
class ComposeTravauxTests(unittest.TestCase):
    """Conteneur de transcription : aucun réseau, seulement l'audio et les modèles ; téléchargement des modèles à part."""

    @classmethod
    def setUpClass(cls):
        resultat = lire_config(['compose.yaml'], env_extra={'COMPOSE_PROFILES': 'outils'})
        if resultat.returncode != 0:
            raise AssertionError(f'docker compose config a échoué : {resultat.stderr.strip()}')
        cls.services = json.loads(resultat.stdout)['services']

    def test_travaux_sans_reseau_ni_acces_aux_donnees(self):
        travaux = self.services['travaux']
        hote = HOTE['chemin']
        self.assertEqual(travaux['network_mode'], 'none')
        self.assertNotIn('ports', travaux)
        self.assertNotIn('networks', travaux)
        self.assertIs(travaux['read_only'], True)
        self.assertEqual(int(travaux['mem_limit']), 3 * 1024 ** 3)
        volumes = {v['target']: (v['source'], v.get('read_only', False)) for v in travaux['volumes']}
        self.assertEqual(volumes, {'/audio': (f'{hote}/territoria/audio', False), '/modeles': (f'{hote}/modeles-audio', True)})
        self.assertEqual(travaux['environment']['TERRITORIA_TRANSCRIPTION_MODELE'], 'large-v3-turbo')

    def test_travaux_durci_et_relance_apres_un_arret_brutal(self):
        # Il décode des fichiers non fiables : aucun privilège ; tué par le plafond de mémoire, il redémarre pour écrire
        # l'erreur de la séance interrompue et reprendre la file.
        for nom in ('travaux', 'travaux-modeles'):
            with self.subTest(service=nom):
                service = self.services[nom]
                self.assertEqual(service['cap_drop'], ['ALL'])
                self.assertEqual(service['security_opt'], ['no-new-privileges:true'])
                self.assertEqual(service['pids_limit'], 256)
        self.assertEqual(self.services['travaux']['restart'], 'on-failure')

    def test_modele_de_transcription_reglable(self):
        services = json.loads(lire_config(['compose.yaml'], env_extra={'TERRITORIA_TRANSCRIPTION_MODELE': 'medium'}).stdout)['services']
        self.assertEqual(services['travaux']['environment']['TERRITORIA_TRANSCRIPTION_MODELE'], 'medium')

    def test_travaux_modeles_ponctuel(self):
        modeles = self.services['travaux-modeles']
        self.assertEqual(modeles['profiles'], ['outils'])
        self.assertEqual(set(modeles['networks']), {'sortie'})
        self.assertNotIn('ports', modeles)
        self.assertEqual({v['target']: v['source'] for v in modeles['volumes']}, {'/modeles': f"{HOTE['chemin']}/modeles-audio"})
        self.assertEqual(modeles['command'][:3], ['python', '-m', 'travaux.modeles'])
        self.assertIn('large-v3-turbo,segmentation,empreintes', modeles['command'])

    def test_le_telechargement_n_est_pas_lance_par_defaut(self):
        self.assertNotIn('travaux-modeles', json.loads(lire_config(['compose.yaml']).stdout)['services'])


@unittest.skipUnless(COMPOSE_OK, 'docker compose absent : surcharge de la CI non vérifiée')
class ComposeCiTests(unittest.TestCase):
    """compose.yaml + compose.ci.yaml : fausse IA embarquée et réseau interne sans sortie vers Internet."""

    @classmethod
    def setUpClass(cls):
        resultat = lire_config(['compose.yaml', 'compose.ci.yaml'])
        if resultat.returncode != 0:
            raise AssertionError(f'docker compose config a échoué : {resultat.stderr.strip()}')
        cls.config = json.loads(resultat.stdout)
        cls.services = cls.config['services']

    def test_trois_services_et_leurs_reseaux(self):
        self.assertEqual(set(self.services), {'territoria', 'n8n', 'faux-llm', 'travaux'})
        self.assertEqual(set(self.services['n8n']['networks']), {'interne'})
        self.assertEqual(set(self.services['faux-llm']['networks']), {'interne'})
        self.assertEqual(set(self.services['territoria']['networks']), {'interne', 'sortie'})

    def test_aucun_reseau_avec_sortie_vers_internet(self):
        # En CI, le réseau de sortie de Territoria devient lui aussi internal : aucune sortie, pour aucun service.
        self.assertEqual(set(self.config['networks']), {'interne', 'sortie'})
        for nom, reseau in self.config['networks'].items():
            self.assertIs(reseau['internal'], True, nom)

    def test_appels_internes_sans_proxy_comme_en_production(self):
        for nom in ('territoria', 'n8n'):
            env = self.services[nom]['environment']
            self.assertIn('faux-llm', env['NO_PROXY'].split(','))
            self.assertEqual(env['NO_PROXY'], env['no_proxy'])

    def test_aucun_port_publie(self):
        # Docker ne route pas un port publié depuis un réseau `internal` : la surcharge le retire (!reset).
        for nom, service in self.services.items():
            self.assertNotIn('ports', service, nom)

    def test_reglages_de_la_fausse_ia_imposes(self):
        env = self.services['territoria']['environment']
        self.assertEqual((env['TERRITORIA_LLM_URL'], env['TERRITORIA_MODELE'], env['TERRITORIA_HORS_LIGNE']),
                         ('http://faux-llm:1234/v1', 'faux-modele', '1'))

    def test_la_surcharge_l_emporte_sur_compose_env(self):
        with tempfile.TemporaryDirectory() as dossier:
            fichier = Path(dossier) / 'compose.env'
            fichier.write_text('TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1\nTERRITORIA_MODELE=vrai-modele\n'
                               'TERRITORIA_HORS_LIGNE=0\n', encoding='utf-8')
            resultat = lire_config(['compose.yaml', 'compose.ci.yaml'], env_file=fichier)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        env = json.loads(resultat.stdout)['services']['territoria']['environment']
        self.assertEqual((env['TERRITORIA_LLM_URL'], env['TERRITORIA_MODELE'], env['TERRITORIA_HORS_LIGNE']),
                         ('http://faux-llm:1234/v1', 'faux-modele', '1'))

    def test_faux_llm_image_python_epinglee_comme_celle_de_territoria(self):
        dockerfile = (CONTENEURS / 'territoria.Dockerfile').read_text(encoding='utf-8')
        python = re.findall(r'^FROM\s+(python:\S+)', dockerfile, re.M)
        self.assertEqual(len(python), 1)
        self.assertRegex(python[0], r'^python:3\.12-slim-bookworm@sha256:[0-9a-f]{64}$')
        self.assertEqual(self.services['faux-llm']['image'], python[0])

    def test_faux_llm_execute_le_script_monte_en_lecture_seule(self):
        service = self.services['faux-llm']
        self.assertEqual(service['command'], ['python', '/faux_llm.py'])
        self.assertEqual([(v['type'], v['source'], v['target'], v['read_only']) for v in service['volumes']],
                         [('bind', str(RACINE / 'tests' / 'faux_llm.py'), '/faux_llm.py', True)])
        self.assertIs(service['read_only'], True)
        self.assertEqual(service['user'], '10001:10001')
        self.assertIn('/v1/models', ' '.join(service['healthcheck']['test']))

    def test_territoria_attend_la_fausse_ia_et_n8n(self):
        depends = self.services['territoria']['depends_on']
        self.assertEqual(depends['faux-llm']['condition'], 'service_healthy')
        self.assertIn('n8n', depends)

    def test_le_reste_de_compose_yaml_est_inchange(self):
        solo = json.loads(lire_config(['compose.yaml']).stdout)['services']
        for nom in ('territoria', 'n8n'):
            for cle in ('image', 'read_only', 'tmpfs', 'volumes', 'healthcheck', 'user'):
                self.assertEqual(self.services[nom].get(cle), solo[nom].get(cle), f'{nom}.{cle}')
        self.assertEqual(self.config['name'], 'territoria')


@unittest.skipUnless(COMPOSE_OK, 'docker compose absent : variante équipe non vérifiée')
class ComposeEquipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resultat = lire_config(['compose.yaml', 'compose.equipe.yaml'])
        if resultat.returncode != 0:
            raise AssertionError(f'docker compose config a échoué : {resultat.stderr.strip()}')
        cls.services = json.loads(resultat.stdout)['services']

    def test_mode_equipe_force_sur_territoria(self):
        self.assertEqual(self.services['territoria']['environment']['TERRITORIA_MODE'], 'equipe')

    def test_proxy_caddy_epingle_sur_le_port_443(self):
        proxy = self.services['proxy']
        self.assertRegex(proxy['image'], r'^caddy:2\.\d+\.\d+@sha256:[0-9a-f]{64}$')
        self.assertEqual([(p['published'], p['target']) for p in proxy['ports']], [('443', 443)])
        self.assertIn('territoria', proxy['depends_on'])

    def test_proxy_joint_territoria_sans_voir_n8n(self):
        # Réseau `sortie` (non internal, nécessaire pour publier 443), partagé avec Territoria ; jamais `interne` (n8n).
        self.assertEqual(set(self.services['proxy']['networks']), {'sortie'})
        self.assertIn('sortie', self.services['territoria']['networks'])
        self.assertNotIn('sortie', self.services['n8n']['networks'])

    def test_proxy_n_ecoute_que_si_territoria_est_en_bonne_sante(self):
        # Le verrou du mode équipe fait sortir Territoria : avec service_started le proxy écouterait quand même sur 0.0.0.0:443.
        self.assertEqual(self.services['proxy']['depends_on']['territoria']['condition'], 'service_healthy')

    def test_proxy_sans_droits_root_et_en_lecture_seule(self):
        proxy = self.services['proxy']
        self.assertEqual(proxy['user'], '10002:10002')
        self.assertIs(proxy['read_only'], True)
        self.assertEqual(proxy['tmpfs'], ['/config:uid=10002,gid=10002,mode=0700'])
        hote = HOTE['chemin']
        en_ecriture = [v['target'] for v in proxy['volumes'] if not v.get('read_only')]
        self.assertEqual(en_ecriture, ['/data'])  # seul dossier de données en écriture
        self.assertEqual({v['target']: v['source'] for v in proxy['volumes']}['/data'], f'{hote}/caddy')

    def test_n8n_reste_sans_port_en_equipe(self):
        self.assertNotIn('ports', self.services['n8n'])


@unittest.skipUnless(COMPOSE_OK, 'docker compose absent : variante démonstration non vérifiée')
class ComposeDemonstrationTests(unittest.TestCase):
    """Serveur de démonstration : Caddy publie 80 et 443 (Let's Encrypt), TerritorIA reste en mode « un poste »."""
    @classmethod
    def setUpClass(cls):
        resultat = lire_config(['compose.yaml', 'compose.demonstration.yaml'], {'TERRITORIA_DOMAINE': 'demo.exemple.fr'})
        if resultat.returncode != 0:
            raise AssertionError(f'docker compose config a échoué : {resultat.stderr.strip()}')
        cls.services = json.loads(resultat.stdout)['services']

    def test_compose_demonstration(self):
        proxy, territoria = self.services['proxy'], self.services['territoria']
        self.assertRegex(proxy['image'], r'^caddy:2\.\d+\.\d+@sha256:[0-9a-f]{64}$')
        self.assertEqual(sorted((p['published'], p['target']) for p in proxy['ports']), [('443', 443), ('80', 80)])
        self.assertEqual(proxy['environment']['TERRITORIA_DOMAINE'], 'demo.exemple.fr')
        self.assertEqual(set(proxy['networks']), {'sortie'})
        self.assertEqual(proxy['depends_on']['territoria']['condition'], 'service_healthy')
        self.assertEqual({v['target']: v['source'] for v in proxy['volumes']}['/data'], f"{HOTE['chemin']}/caddy")
        self.assertEqual(territoria['environment']['TERRITORIA_ORIGINE'], 'https://demo.exemple.fr')
        self.assertEqual(territoria['environment']['TERRITORIA_DEMONSTRATION'], '1')
        self.assertNotIn('TERRITORIA_MODE', territoria['environment'])  # mode « un poste » derrière le proxy
        self.assertNotIn('ports', self.services['n8n'])
        self.assertNotIn('sortie', self.services['n8n']['networks'])

    def test_domaine_exige(self):
        resultat = lire_config(['compose.yaml', 'compose.demonstration.yaml'])
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn('TERRITORIA_DOMAINE', resultat.stderr)

    def test_caddyfile_de_demonstration(self):
        texte = (CONTENEURS / 'Caddyfile.demonstration').read_text(encoding='utf-8')
        self.assertIn('{$TERRITORIA_DOMAINE}', texte)
        self.assertIn('reverse_proxy territoria:8878', texte)


class FichiersDeConstructionTests(unittest.TestCase):
    """Contrôles de texte, sans docker : ils ne sont donc jamais ignorés."""

    @classmethod
    def setUpClass(cls):
        cls.dockerfile = (CONTENEURS / 'territoria.Dockerfile').read_text(encoding='utf-8')
        cls.caddyfile = (CONTENEURS / 'Caddyfile').read_text(encoding='utf-8')
        cls.dockerignore = (RACINE / '.dockerignore').read_text(encoding='utf-8').splitlines()

    def test_images_de_base_epinglees_par_digest(self):
        bases = re.findall(r'^FROM\s+(\S+)', self.dockerfile, re.M)
        self.assertEqual(len(bases), 2)
        for base in bases:
            self.assertRegex(base, r'@sha256:[0-9a-f]{64}$', base)
        self.assertTrue(bases[0].startswith('node:22-bookworm-slim@'))
        self.assertTrue(bases[1].startswith('python:3.12-slim-bookworm@'))

    def test_dependances_figees_et_roue_verifiee(self):
        self.assertIn('npm ci --ignore-scripts', self.dockerfile)
        self.assertIn('pip install --no-cache-dir -r requirements.lock', self.dockerfile)
        self.assertIn('pip install --no-cache-dir --no-deps', self.dockerfile)
        self.assertRegex(self.dockerfile, r'cd vendor && sha256sum -c SHA256SUMS')

    def test_ocr_et_poppler_sans_paquets_recommandes(self):
        self.assertRegex(self.dockerfile, r'apt-get install -y --no-install-recommends[^\n]*tesseract-ocr tesseract-ocr-fra poppler-utils')

    def test_utilisateur_sans_privileges_et_demarrage(self):
        self.assertRegex(self.dockerfile, re.compile(r'^USER 10001:10001$', re.M))
        self.assertIn('ENV PYTHONPATH=/app TERRITORIA_ECOUTE=0.0.0.0 TERRITORIA_DONNEES=/donnees', self.dockerfile)
        self.assertIn('http://127.0.0.1:8878/healthz', self.dockerfile)
        self.assertRegex(self.dockerfile, re.compile(r'^HEALTHCHECK ', re.M))
        self.assertRegex(self.dockerfile, re.compile(r'^CMD \["python", "-m", "serve"\]$', re.M))

    def test_paquet_territoria_dans_l_image(self):
        self.assertRegex(self.dockerfile, re.compile(r'^COPY app/territoria \./territoria$', re.M))  # `COPY app/*.py` ne prend pas les sous-dossiers

    def test_protocole_de_transcription_dans_l_image(self):
        self.assertRegex(self.dockerfile, re.compile(r'^COPY app/travaux \./travaux$', re.M))  # territoria importe travaux.protocole

    def test_workflows_n8n_dans_l_image_pour_le_provisionnement(self):
        self.assertRegex(self.dockerfile, re.compile(r'^COPY n8n/workflows \./workflows$', re.M))
        self.assertNotIn('n8n', self.dockerignore)  # le dossier n8n/ du dépôt doit rester dans le contexte

    def test_caddyfile_proxy_tls_interne(self):
        self.assertIn('reverse_proxy territoria:8878', self.caddyfile)
        self.assertIn('tls internal', self.caddyfile)

    def test_caddyfile_n_installe_pas_l_autorite_dans_le_conteneur(self):
        self.assertIn('skip_install_trust', self.caddyfile)

    def test_dockerignore_exclut_donnees_et_secrets_de_travail(self):
        for motif in ('.git', '.venv', '.superpowers', 'territoria-donnees', 'app/frontend/node_modules', '**/__pycache__'):
            self.assertIn(motif, self.dockerignore)


if __name__ == '__main__':
    unittest.main()
