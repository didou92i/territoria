# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Connecteurs vers des sources publiques : data.gouv.fr (sans compte) et Légifrance (identifiants PISTE de la collectivité).

Ils sont désactivés par défaut, activés par l'administration, et coupés en mode « hors ligne ». Ce qui sort du poste :
- data.gouv.fr : les mots de la recherche, puis le téléchargement du fichier choisi ;
- Légifrance : les références d'articles demandées.
Jamais un document, une conversation ou un nom d'agent.

Un fichier de data.gouv.fr peut être hébergé n'importe où : son adresse est relue sur data.gouv.fr (jamais prise du
navigateur), et une adresse du réseau interne ou de la machine est refusée, à chaque redirection (pas de rebond vers
les serveurs de la collectivité).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_DATAGOUV = 'https://www.data.gouv.fr/api/1'
TAILLE_MAX = 20 * 1024 * 1024
DELAI = 30
FORMATS = {'pdf': '.pdf', 'docx': '.docx', 'txt': '.txt', 'md': '.md', 'csv': '.csv', 'json': '.txt'}
IDENTIFIANT = re.compile(r'[A-Za-z0-9-]{8,64}')
AGENT = 'Territoria (collectivité territoriale ; https://www.data.gouv.fr)'


class ConnecteurIndisponible(PermissionError):
    """Connecteur désactivé par l'administration ou mode hors ligne."""


def _fichier(donnees: Path) -> Path:
    return Path(donnees) / 'configuration' / 'connecteurs.json'


def lire(donnees: Path) -> dict:
    try:
        valeur = json.loads(_fichier(donnees).read_text(encoding='utf-8'))
        return valeur if isinstance(valeur, dict) else {}
    except (OSError, ValueError):
        return {}


def _ecrire(donnees: Path, valeur: dict) -> None:
    dossier = _fichier(donnees).parent
    dossier.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporaire = dossier / '.connecteurs.tmp'
    temporaire.write_text(json.dumps(valeur, ensure_ascii=False), encoding='utf-8')
    os.chmod(temporaire, 0o600)
    os.replace(temporaire, _fichier(donnees))


def etat(donnees: Path, hors_ligne: bool) -> dict:
    """Ce que voit l'administration : jamais le secret, seulement s'il est enregistré."""
    config = lire(donnees)
    legifrance = config.get('legifrance', {}) if isinstance(config.get('legifrance'), dict) else {}
    return {'hors_ligne': hors_ligne,
            'datagouv': {'actif': config.get('datagouv') is True and not hors_ligne, 'choisi': config.get('datagouv') is True},
            'legifrance': {'actif': legifrance.get('actif') is True and not hors_ligne, 'choisi': legifrance.get('actif') is True,
                           'identifiants': bool(legifrance.get('client_id') and legifrance.get('client_secret')),
                           'client_id': legifrance.get('client_id', '')}}


def enregistrer(donnees: Path, demande) -> None:
    if not isinstance(demande, dict):
        raise ValueError('Réglage invalide.')
    config = lire(donnees)
    if 'datagouv' in demande:
        if not isinstance(demande['datagouv'], bool):
            raise ValueError('Réglage invalide.')
        config['datagouv'] = demande['datagouv']
    if isinstance(demande.get('legifrance'), dict):
        actuel = config.get('legifrance', {}) if isinstance(config.get('legifrance'), dict) else {}
        nouveau = demande['legifrance']
        if 'actif' in nouveau:
            if not isinstance(nouveau['actif'], bool):
                raise ValueError('Réglage invalide.')
            actuel['actif'] = nouveau['actif']
        if nouveau.get('effacer') is True:
            actuel.pop('client_id', None)
            actuel.pop('client_secret', None)
            actuel['actif'] = False
        for cle in ('client_id', 'client_secret'):
            valeur = nouveau.get(cle)
            if valeur:  # un champ laissé vide garde la valeur enregistrée
                if not isinstance(valeur, str) or len(valeur) > 200 or any(ord(x) < 33 for x in valeur):
                    raise ValueError('Identifiant ou secret PISTE invalide.')
                actuel[cle] = valeur
        config['legifrance'] = actuel
    _ecrire(donnees, config)


def exiger(donnees: Path, hors_ligne: bool, connecteur: str) -> None:
    if hors_ligne:
        raise ConnecteurIndisponible('Le mode hors ligne est activé : les connecteurs vers Internet sont coupés.')
    if not etat(donnees, hors_ligne)[connecteur]['actif']:
        raise ConnecteurIndisponible('Ce connecteur n’est pas activé par l’administration.')


# --- Téléchargements sûrs ----------------------------------------------------------------------------------------

class _SansRedirection(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _adresse_publique(url: str) -> None:
    """Refuse une adresse qui n'est pas https ou http public : machine locale, réseau interne, lien local, multidiffusion."""
    morceaux = urlsplit(url)
    if morceaux.scheme not in ('https', 'http') or not morceaux.hostname or morceaux.username or morceaux.password:
        raise ValueError('Adresse de fichier refusée.')
    try:
        adresses = {info[4][0] for info in socket.getaddrinfo(morceaux.hostname, morceaux.port or (443 if morceaux.scheme == 'https' else 80))}
    except socket.gaierror:
        raise RuntimeError('Hébergeur du fichier injoignable.') from None
    for brute in adresses:
        adresse = ipaddress.ip_address(brute.split('%')[0])
        if adresse.version == 6 and adresse.ipv4_mapped:
            adresse = adresse.ipv4_mapped
        if not adresse.is_global or adresse.is_multicast:
            raise ValueError('Adresse de fichier refusée : elle désigne le réseau interne.')


def telecharger(url: str, taille_max=TAILLE_MAX, accepter='*/*') -> tuple[bytes, str]:
    """Contenu et type d'un fichier public, en suivant au plus trois redirections, chacune contrôlée."""
    ouvreur = build_opener(_SansRedirection)
    for _ in range(4):
        _adresse_publique(url)
        requete = Request(url, headers={'User-Agent': AGENT, 'Accept': accepter})
        try:
            with ouvreur.open(requete, timeout=DELAI) as reponse:
                contenu = reponse.read(taille_max + 1)
                if len(contenu) > taille_max:
                    raise ValueError('Fichier trop volumineux (20 Mo au plus).')
                return contenu, reponse.headers.get('Content-Type', '')
        except HTTPError as erreur:
            if erreur.code in (301, 302, 303, 307, 308) and erreur.headers.get('Location'):
                url = urljoin(url, erreur.headers['Location'])
                continue
            raise RuntimeError(f'Le serveur du fichier a répondu {erreur.code}.') from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError('Hébergeur du fichier injoignable.') from None
    raise RuntimeError('Trop de redirections.')


def _api(chemin: str, parametres=None) -> dict:
    url = API_DATAGOUV + chemin + ('?' + urlencode(parametres) if parametres else '')
    contenu, _ = telecharger(url, 4 * 1024 * 1024, 'application/json')
    try:
        valeur = json.loads(contenu)
    except ValueError:
        raise RuntimeError('Réponse de data.gouv.fr illisible.') from None
    if not isinstance(valeur, dict):
        raise RuntimeError('Réponse de data.gouv.fr illisible.')
    return valeur


# --- data.gouv.fr --------------------------------------------------------------------------------------------------

# data.gouv.fr donne parfois un type MIME au lieu d'un format : un libellé court et lisible pour l'agent.
FORMATS_MIME = {'vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx', 'vnd.ms-excel': 'xls',
                'vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx', 'msword': 'doc',
                'vnd.oasis.opendocument.spreadsheet': 'ods', 'vnd.oasis.opendocument.text': 'odt', 'plain': 'txt',
                'rdf+xml': 'rdf', 'ld+json': 'jsonld', 'turtle': 'ttl'}
# Formats du web sémantique ou de traitement de données : inutiles à un agent, ils encombraient chaque jeu.
FORMATS_TECHNIQUES = frozenset({'rdf', 'ttl', 'n3', 'nt', 'jsonld', 'jsonl', 'parquet'})


def _format(ressource: dict) -> str:
    nom = str(ressource.get('format') or '').lower().strip('.')
    nom = FORMATS_MIME.get(nom.rsplit('/', 1)[-1], nom.rsplit('/', 1)[-1])
    if nom not in FORMATS:
        extension = Path(urlsplit(str(ressource.get('url', ''))).path).suffix.lower().strip('.')
        nom = extension if extension in FORMATS else nom
    return nom


def _ressource(r: dict) -> dict:
    return {'id': str(r.get('id', '')), 'titre': str(r.get('title') or '')[:200], 'format': _format(r),
            'taille': r.get('filesize') if isinstance(r.get('filesize'), int) else None, 'importable': _format(r) in FORMATS}


def rechercher(texte: str, page=1) -> dict:
    texte = re.sub(r'\s+', ' ', texte or '').strip()[:200]
    if len(texte) < 2:
        raise ValueError('Saisissez au moins deux caractères.')
    reponse = _api('/datasets/', {'q': texte, 'page_size': 10, 'page': max(1, min(int(page), 50))})
    jeux = []
    for jeu in reponse.get('data', []):
        if not isinstance(jeu, dict):
            continue
        jeux.append({'id': str(jeu.get('id', '')), 'titre': str(jeu.get('title') or '')[:300],
                     'organisation': str((jeu.get('organization') or {}).get('name') or ''),
                     'mise_a_jour': str(jeu.get('last_update') or '')[:10], 'page': str(jeu.get('page') or ''),
                     'licence': str(jeu.get('license') or ''),
                     'ressources': [_ressource(r) for r in jeu.get('resources', [])
                                    if isinstance(r, dict) and _format(r) not in FORMATS_TECHNIQUES][:12]})
    return {'total': reponse.get('total', 0) if isinstance(reponse.get('total'), int) else 0, 'jeux': jeux}


def recuperer(jeu_id: str, ressource_id: str) -> tuple[str, bytes, str]:
    """Nom de fichier, contenu et adresse de la page du jeu : l'adresse du fichier est relue sur data.gouv.fr."""
    if not IDENTIFIANT.fullmatch(jeu_id or '') or not IDENTIFIANT.fullmatch(ressource_id or ''):
        raise ValueError('Ressource invalide.')
    jeu = _api('/datasets/' + quote(jeu_id) + '/')
    ressource = next((r for r in jeu.get('resources', []) if isinstance(r, dict) and r.get('id') == ressource_id), None)
    if not ressource:
        raise LookupError(ressource_id)
    forme = _format(ressource)
    if forme not in FORMATS:
        raise ValueError('Format non importable : PDF, Word, texte, Markdown, CSV ou JSON seulement.')
    contenu, _ = telecharger(str(ressource.get('url', '')))
    if forme in ('csv', 'json'):  # tableau ou données : importés comme texte, interrogeables comme le reste
        try:
            texte = contenu.decode('utf-8-sig')
        except UnicodeDecodeError:
            texte = contenu.decode('latin-1')
        contenu = texte.encode('utf-8')
        forme = 'txt' if forme == 'json' else 'csv'
    titre = re.sub(r'[\\/:*?"<>|\x00-\x1f]', ' ', str(ressource.get('title') or jeu.get('title') or 'ressource')).strip()[:150]
    extension = '.txt' if forme == 'csv' else FORMATS[forme]
    if extension == '.txt':  # « liste.csv » devient « liste.txt », pas « liste.csv.txt »
        titre = re.sub(r'\.(csv|json)$', '', titre, flags=re.IGNORECASE).strip() or 'ressource'
    nom = titre if titre.lower().endswith(extension) else titre + extension
    return nom, contenu, str(jeu.get('page') or 'https://www.data.gouv.fr/datasets/' + jeu_id)


# --- Légifrance (PISTE) ---------------------------------------------------------------------------------------------

PONT = Path(__file__).resolve().parent.parent / 'legal_bridge.py'
CODES_COURANTS = ('Code général des collectivités territoriales', 'Code général de la fonction publique', "Code de l'éducation",
                  'Code de la route', "Code de l'urbanisme", 'Code de la commande publique',
                  "Code des relations entre le public et l'administration", "Code de l'environnement", 'Code civil',
                  'Code de la santé publique', 'Code de la construction et de l’habitation', 'Code de justice administrative')
ARTICLE = re.compile(r'(?:[LRDA]\*{0,2})?\d[\d.-]{0,20}')  # « L2122-22 », « R*411-25 » ou « 1240 » (code civil)


def environnement_legifrance(donnees: Path, hors_ligne: bool) -> dict:
    """Variables d'environnement du pont Légifrance : les identifiants enregistrés, seulement si le connecteur est actif."""
    if hors_ligne:
        return {}
    config = lire(donnees).get('legifrance', {})
    if not isinstance(config, dict) or config.get('actif') is not True or not config.get('client_id') or not config.get('client_secret'):
        return {}
    return {'PISTE_CLIENT_ID': config['client_id'], 'PISTE_CLIENT_SECRET': config['client_secret']}


def _normaliser_article(article: str) -> str:
    article = re.sub(r'\s+', '', str(article or '')).upper()
    article = re.sub(r'^ARTICLE', '', article, flags=re.IGNORECASE)
    if not ARTICLE.fullmatch(article):
        raise ValueError('Numéro d’article invalide (par exemple L2122-22, R531-52 ou 1240).')
    return article


def textes_legifrance(donnees: Path, hors_ligne: bool, references, delai=90) -> dict:
    """Textes en vigueur aujourd'hui, par article ; seules les références sortent du poste. Lève RuntimeError sans réponse."""
    env = environnement_legifrance(donnees, hors_ligne)
    if not env:
        raise ConnecteurIndisponible('Le connecteur Légifrance n’est pas activé ou ses identifiants PISTE manquent.')
    demande = json.dumps({'references': [[code, _normaliser_article(article)] for code, article in references]}, ensure_ascii=False)
    try:
        sortie = subprocess.run([sys.executable, str(PONT)], input=demande.encode('utf-8'), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=delai, check=True, env={**os.environ, **env})
        reponse = json.loads(sortie.stdout.decode('utf-8'))
    except (subprocess.SubprocessError, OSError, ValueError):
        raise RuntimeError('Légifrance n’a pas répondu. Vérifiez la connexion à Internet et réessayez.') from None
    textes = {}
    for source in reponse.get('sources', []):
        texte = source.get('text', '')
        if (source.get('status') == 'ok' and isinstance(texte, str) and texte
                and hashlib.sha256(texte.encode()).hexdigest() == source.get('sha256')
                and re.fullmatch(r'https://www\.legifrance\.gouv\.fr/codes/article_lc/LEGIARTI\d+', str(source.get('url', '')))):
            textes[source['article']] = source
    return textes


def tester_legifrance(donnees: Path, hors_ligne: bool) -> bool:
    return 'L2121-29' in textes_legifrance(donnees, hors_ligne, [('Code général des collectivités territoriales', 'L2121-29')], delai=60)


# Le pont rend l'article d'un seul tenant : chaque alinéa numéroté (« 1° », « 2° »…) qui suit « : » ou « ; » revient à la ligne.
_ALINEA = re.compile(r'(?<=[:;])[ \u00a0]+(?=\d{1,2}° )')


def article_legifrance(donnees: Path, hors_ligne: bool, code: str, article: str) -> dict:
    if code not in CODES_COURANTS:
        raise ValueError('Code non proposé.')
    numero = _normaliser_article(article)
    textes = textes_legifrance(donnees, hors_ligne, [(code, numero)], delai=60)
    if numero not in textes:
        raise LookupError(numero)
    source = textes[numero]
    resultat = {k: source.get(k) for k in ('code', 'article', 'text', 'url', 'as_of', 'version')}
    resultat['text'] = _ALINEA.sub('\n', str(resultat.get('text') or ''))
    return resultat
