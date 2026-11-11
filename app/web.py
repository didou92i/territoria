# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Couche HTTP de Territoria : routage, contrôle d'accès et mise en forme des réponses, servie en WSGI.

La logique métier reste dans `server` ; chaque requête laisse une ligne de journal sans contenu.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Mapping, NamedTuple
from urllib.parse import parse_qs, urlsplit

import arretes
import branding
import journal
import llm_backend
import server
from config import REGLAGES
from territoria import administration, anonymisation, comptes, connecteurs, nomenclature, publication, publication_dossier, registre, \
    retours, seances, semantique
from territoria import VERSION
from territoria import assistants as assistants_perso

Reponse = NamedTuple('Reponse', [('status', int), ('headers', list[tuple[str, str]]), ('body', bytes)])

# Chemins appelés par n8n : jeton de service obligatoire, sans contrôle de Host ni d'Origin.
PRIVEES = frozenset({'/api/verify', '/api/llm/chat', '/api/arretes/analyser', '/api/arretes/sources',
                     '/api/arretes/preparer', '/api/arretes/finaliser'})
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
MAX_JSON = 200000
REFUS = {'error': 'Accès refusé.'}
ERREUR_LECTURE = {'error': 'Erreur locale lors de la lecture.'}
ERREUR_TRAITEMENT = {'error': 'Erreur locale pendant le traitement.'}
TROP_VOLUMINEUX = {'error': 'Fichier trop volumineux ou requête vide (maximum 20 Mo).'}

# Segments fixes des routes : dans le journal, tout autre segment devient « :id ».
SEGMENTS = frozenset({'api', 'healthz', 'spaces', 'assistants', 'documents', 'search', 'conversations', 'identite',
                      'logo', 'ia', 'status', 'arretes', 'modele', 'bibliotheque-arretes', 'file', 'upload', 'tester',
                      'llm', 'chat', 'verify', 'analyser', 'sources', 'preparer', 'finaliser', 'generer', 'admin',
                      'regles', 'activite', 'statistiques', 'sante', 'modeles', 'retablir', 'session', 'connexion',
                      'deconnexion', 'comptes', 'premier', 'compte', 'mot-de-passe', 'utilisateurs', 'services',
                      'reinitialiser', 'transferer', 'annuaire', 'partages', 'espace', 'assistant', 'retirer', 'messages',
                      'evaluation', 'signalement', 'retours', 'aide', 'recherche', 'connecteurs', 'datagouv', 'import',
                      'legifrance', 'tester', 'article', 'ajouter', 'gabarit', 'supprimer', 'actes', 'numero', 'numerotation',
                      'registre', 'csv', 'seances', 'audio', 'fin', 'intervenants', 'fusion', 'segments', 'valider',
                      'reessayer', 'export', 'details', 'points', 'ordre-du-jour', 'rediger', 'publications', 'depot',
                      'reperer', 'passages', 'dossier', 'en-ligne', 'retrait', 'matieres'})
# Routes ouvertes sans session : l'état de la connexion, la connexion elle-même et la création du premier compte.
PUBLIQUES = frozenset({('GET', '/api/session'), ('POST', '/api/connexion'), ('POST', '/api/deconnexion'),
                       ('POST', '/api/session/visiteur'),
                       ('POST', '/api/comptes/premier')})
# Seules routes permises tant que le mot de passe provisoire n'est pas changé.
PREMIER_EN_DEMONSTRATION = 'En démonstration, le premier administrateur se crée sur le serveur (install/demonstration.sh).'
CHANGEMENT = frozenset({('GET', '/api/session'), ('POST', '/api/compte/mot-de-passe'), ('POST', '/api/deconnexion')})
ADMINISTRATION = frozenset({('POST', '/api/identite'), ('POST', '/api/identite/logo'), ('POST', '/api/identite/gabarit'),
                            ('POST', '/api/identite/gabarit/supprimer'), ('GET', '/api/ia'), ('POST', '/api/ia'),
                            ('POST', '/api/ia/tester')})
CONNEXION_REQUISE = {'error': 'Connexion requise.', 'code': 'connexion'}
# Sans conteneur de transcription (orchestration interne), une séance déposée ne serait jamais transcrite.
SEANCES_INDISPONIBLES = {'error': 'La transcription des séances n’est pas disponible sur ce serveur.', 'code': 'seances_indisponibles'}
_requete = threading.local()  # compte de la requête en cours, pour le journal d'activité
# Types qu'une page tierce peut envoyer sans demande préalable du navigateur (CORS) : refusés pour toute écriture.
TYPES_SIMPLES = frozenset({'', 'text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data'})
METHODES = frozenset({'get', 'post', 'delete', 'put', 'patch', 'head', 'options'})
AUDIO = re.compile(r'/api/seances/([0-9a-f]{32})/audio')
PLAGE = re.compile(r'bytes=(\d*)-(\d*)')
PLAGE_MAX = 8 * 1024 * 1024  # au plus 8 Mio par réponse : le lecteur du navigateur redemande la suite
TYPES_AUDIO = {'webm': 'audio/webm', 'ogg': 'audio/ogg', 'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'mp4': 'video/mp4',
               'wav': 'audio/wav', 'aac': 'audio/aac', 'flac': 'audio/flac', 'mov': 'video/quicktime'}


def _reponse(status, corps, type_contenu='application/json; charset=utf-8', disposition=None):
    entetes = [('Content-Type', type_contenu), ('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'no-store'),
               ('Content-Security-Policy', CSP)]
    if REGLAGES.demonstration:  # serveur d'essai public, sur un nom d'hôte à lui
        entetes.append(('X-Robots-Tag', 'noindex, nofollow'))  # il n'a pas à figurer dans les moteurs de recherche
        if urlsplit(REGLAGES.origine).scheme == 'https':  # le navigateur n'y revient plus en clair
            entetes.append(('Strict-Transport-Security', 'max-age=31536000'))
    if disposition:
        entetes.append(('Content-Disposition', disposition))
    entetes.append(('Content-Length', str(len(corps))))
    return Reponse(status, entetes, corps)


def _json(status, donnees):
    return _reponse(status, json.dumps(donnees, ensure_ascii=False).encode())


def _code(erreur):
    return type(erreur).__name__.lower()


def _etape(methode, chemin):
    """Nom de route normalisé, jamais le chemin brut : `get:/api/conversations/:id`, `get:/static`…"""
    m = methode.lower() if methode.lower() in METHODES else 'autre'
    if chemin == '/healthz' or chemin.startswith('/api/'):
        route = '/'.join(s if not s or s in SEGMENTS else ':id' for s in chemin.split('/'))
    else:
        route = '/' if chemin == '/' else '/static'
    return f'{m}:{route}'


PUBLICATION = re.compile(r'/api/publications/([0-9a-f]{32})')
PUBLICATION_ACTION = re.compile(r'/api/publications/([0-9a-f]{32})/(reperer|passages|valider|en-ligne|retrait)')
MESSAGE_VALIDATION = 'Seuls un référent juridique ou un administrateur peuvent valider une publication.'


def _publication(compte, ident):
    """Acte à publier visible du compte : tous pour le référent et l'administrateur, les siens pour les autres."""
    acte = publication.lire(server.DB, server.DATA, ident)
    if compte['role'] not in ('administrateur', 'referent') and acte['cree_par'] != compte['id']:
        raise LookupError(ident)
    return acte


def _peut_valider(compte, acte):
    """Référent juridique et administrateur ; en démonstration, un visiteur pour ses propres actes fictifs."""
    if compte['role'] in ('administrateur', 'referent'):
        return True
    return (REGLAGES.demonstration and str(compte.get('identifiant', '')).startswith('visiteur-')
            and acte['cree_par'] == compte['id'])


def _paragraphes_pdf(pages):
    """Lignes d'un PDF regroupées en paragraphes : un paragraphe se termine par « . », « ; » ou « : »."""
    paragraphes, courant = [], []
    for page in pages:
        for ligne in page.splitlines():
            if ligne.strip():
                courant.append(ligne.strip())
                if ligne.rstrip().endswith(('.', ';', ':')):
                    paragraphes.append(' '.join(courant))
                    courant = []
    return paragraphes + ([' '.join(courant)] if courant else [])


def _depot_publication(requete, corps, compte):
    q = {k: v[0] for k, v in parse_qs(requete).items()}
    nom = q.get('nom', '')
    extension = Path(nom).suffix.lower()
    if extension not in ('.docx', '.pdf'):
        raise ValueError('Déposez l’acte en Word (.docx) ou en PDF.')
    refus = _place_visiteur(compte, corps) if _visiteur(compte) else None
    if refus:
        return refus
    with tempfile.TemporaryDirectory(prefix='territoria-depot-') as tmp:
        source = Path(tmp) / f'source{extension}'
        source.write_bytes(corps)
        try:
            paragraphes = (publication.paragraphes_docx(source) if extension == '.docx'
                           else _paragraphes_pdf(server.pages_importees(source)[0]))
        except server.Illisible:  # fichier abîmé : même consigne qu'avant pour un acte à déposer
            raise ValueError('Déposez l’acte en Word (.docx) ou en PDF.') from None
        except ValueError as erreur:
            if extension == '.pdf' and 'Aucun texte lisible' in str(erreur):
                raise ValueError('Ce PDF est une image : déposez la version Word, ou un PDF texte.') from None
            raise
        except Exception:
            raise ValueError('Déposez l’acte en Word (.docx) ou en PDF.') from None
        if not paragraphes:
            raise ValueError('Ce PDF est une image : déposez la version Word, ou un PDF texte.')
        acte = publication.creer(server.DB, server.DATA, type=q.get('type', ''), numero=q.get('numero', ''),
                                 date_acte=q.get('date_acte', ''), objet=q.get('objet', ''),
                                 paragraphes=_texte_visiteur(compte, paragraphes),
                                 source='depot', matiere_code=q.get('matiere_code', ''), par=compte['id'],
                                 visiteurs_a_part=REGLAGES.demonstration, avant_ecriture=_a_l_ecriture(compte))
        publication.ecrire_prive(publication.dossier(server.DATA, acte['id']) / f'source{extension}', corps)
    _noter('publication_ajoutee', acte['id'], 'depot')
    return _json(201, acte)


def _demander_ia(requete):
    """Une demande de repérage des noms, au modèle choisi dans les réglages (plafonnée pour un visiteur de démonstration)."""
    modele = llm_backend.require_configured(server.DATA)['model']
    return llm_backend.chat(server.DATA, {'request': requete | {'model': modele}})


def _publications_post(p, d, compte):
    """Routes JSON de publication ; None si le chemin n'en est pas une."""
    d = d if isinstance(d, dict) else {}
    if p == '/api/publications':
        entree = publication.entree_du_registre(server.DB, str(d.get('registre', '')))
        if compte['role'] not in ('administrateur', 'referent') and not comptes.projet_de(server.DB, entree['acte_id'], compte['id']):
            raise LookupError(entree['numero'])  # même règle que le Word de l'acte : celui qui l'a rédigé
        acte = publication.creer(server.DB, server.DATA, type=entree['type'], numero=entree['numero'],
                                 date_acte=d.get('date_acte') or entree['attribue_le'][:10], objet=d.get('objet') or entree['objet'],
                                 paragraphes=_texte_visiteur(compte, publication.paragraphes_du_registre(server.DATA, entree['acte_id'])),
                                 source='registre', acte_id=entree['acte_id'], matiere_code=d.get('matiere_code', ''),
                                 votes=d.get('votes'), par=compte['id'], visiteurs_a_part=REGLAGES.demonstration, avant_ecriture=_a_l_ecriture(compte))
        _noter('publication_ajoutee', acte['id'], 'registre')
        return _json(201, acte)
    if p == '/api/publications/dossier':
        ids = d.get('ids') if isinstance(d.get('ids'), list) else []
        actes = [_publication(compte, str(i)) for i in ids]
        if not actes or any(not _peut_valider(compte, a) for a in actes):
            return _json(403, {'error': MESSAGE_VALIDATION}) if actes else _json(400, {'error': 'Choisissez au moins un acte validé.'})
        if any(a['statut'] != 'valide' for a in actes):
            raise ValueError('Seul un acte validé entre dans un dossier de publication.')
        proprietaire = None if compte['role'] in ('administrateur', 'referent') else compte['id']
        deja = [a for a in publication.liste(server.DB, proprietaire, REGLAGES.demonstration) if a['statut'] == 'publie']
        date_mise_en_ligne = str(d.get('date', '')) or publication.aujourdhui()
        archive, empreintes = publication_dossier.dossier(actes, deja, branding.profile(server.DATA), date_mise_en_ligne)
        for ident, produit in empreintes.items():
            publication.enregistrer_dossier(server.DB, server.DATA, ident, produit['empreinte'], produit['pdf'], date_mise_en_ligne)
        _noter('dossier_publication', detail=str(len(actes)))
        return _reponse(200, archive, 'application/zip', f'attachment; filename="publication-{date_mise_en_ligne}.zip"')
    m = PUBLICATION_ACTION.fullmatch(p)
    if not m:
        return None
    ident, action = m.groups()
    acte = _publication(compte, ident)
    if action == 'reperer':
        publication.refuser_si_fige(acte)  # avant d'appeler l'IA, qui coûte
        texte = '\n'.join([acte['objet']] + acte['paragraphes'])
        proposes, avertissement = anonymisation.reperer(texte, _demander_ia)
        manuels = [p for p in acte['passages'] if p['origine'] == 'manuel']
        vus = {p['texte'].lower() for p in proposes}
        acte = publication.enregistrer_reperage(server.DB, ident, proposes + [p for p in manuels if p['texte'].lower() not in vus],
                                                avertissement)
        return _json(200, {'passages': acte['passages'], 'avertissement': avertissement, 'statut': acte['statut']})
    if action == 'passages':
        return _json(200, publication.enregistrer_passages(server.DB, ident, d.get('passages')))
    if not _peut_valider(compte, acte):
        return _json(403, {'error': MESSAGE_VALIDATION})
    if action == 'valider':
        acte = publication.valider(server.DB, server.DATA, ident, compte['id'])
        _noter('publication_validee', ident)
        return _json(200, acte)
    if action == 'retrait':
        acte = publication.retirer(server.DB, ident, d.get('motif'), compte['id'])
        _noter('publication_retiree', ident)
        return _json(200, acte)
    acte = publication.marquer_publie(server.DB, ident)  # à la date du dossier, imprimée sur le PDF
    _noter('publication_en_ligne', ident)
    return _json(200, acte)


MESSAGE_LEGIFRANCE_VISITEUR = 'La consultation de Légifrance est réservée aux comptes de la collectivité.'


def _fournisseur_en_ligne() -> str:
    """Nom du service d'IA en ligne de la démonstration, affiché avant la connexion ; vide s'il est local ou illisible."""
    try:
        return {'openrouter': 'OpenRouter', 'mistral': 'Mistral'}.get(llm_backend.read(server.DATA).get('provider'), '')
    except ValueError:
        return ''


def _visiteur(compte) -> bool:
    """Compte d'essai sans identité de la démonstration publique."""
    return bool(REGLAGES.demonstration and compte and str(compte.get('identifiant', '')).startswith('visiteur-'))


# Démonstration publique : ce qu'un visiteur dépose reste petit et compté (le disque du serveur est partagé), et rien de ce
# qu'il crée n'est visible d'un autre compte. Son compte et ses contenus s'effacent au bout de 24 heures (server.purger_visiteurs).
DOCUMENTS_PAR_VISITEUR = 3  # documents importés ; autant d'actes déposés
TAILLE_VISITEUR = 5 * 1024 * 1024
DOCUMENTS_VISITEURS_MAX = 150  # documents (ou actes déposés) de l'ensemble des visiteurs présents
TEXTE_VISITEUR = 300_000  # caractères lus dans un document : un fichier léger peut en contenir des millions une fois ouvert
ESPACES_PAR_VISITEUR = 5
ASSISTANTS_PAR_VISITEUR = 5
MESSAGES_PAR_VISITEUR = 80  # questions et réponses gardées ; certaines questions ne passent pas par l'IA et n'étaient comptées nulle part
MESSAGE_DOCUMENTS_VISITEUR = ("Limite de l'essai atteinte (3 documents importés et 3 actes déposés). Pour continuer, installez "
                              'TerritorIA chez vous : https://territoria.ovh')
MESSAGE_TAILLE_VISITEUR = 'En démonstration, un fichier pèse 5 Mo au plus.'
MESSAGE_STOCKAGE_VISITEURS = ('La démonstration a reçu beaucoup de documents aujourd’hui. Revenez demain, ou installez TerritorIA '
                              'chez vous : https://territoria.ovh')
MESSAGE_TEXTE_VISITEUR = 'En démonstration, un document contient 300 000 caractères au plus (une centaine de pages).'
MESSAGE_ESSAI_VISITEUR = "Limite de l'essai atteinte. Pour continuer, installez TerritorIA chez vous : https://territoria.ovh"
SEANCES_VISITEUR = 'En démonstration, la transcription des séances s’essaie avec un compte, pas en visiteur.'
RESERVE_AUX_COMPTES = 'En démonstration, cette action est réservée aux comptes.'
PARTAGE_VISITEUR = 'En démonstration, un visiteur ne partage ni ses espaces ni ses assistants.'
IMPORT_VISITEUR = 'En démonstration, importez vos documents dans un espace que vous avez créé.'
_VISITEURS = "SELECT id FROM utilisateurs WHERE identifiant LIKE 'visiteur-%'"


class LimiteAtteinte(RuntimeError):
    """Plafond de demandes à l'IA d'un visiteur de la démonstration : « trop de demandes » (429), pas une panne."""


def _garde_ia():
    """Avant chaque demande à l'IA : un visiteur de la démonstration est plafonné (crédits de l'administrateur)."""
    compte = getattr(_requete, 'compte', None)
    if REGLAGES.demonstration and compte and str(compte.get('identifiant', '')).startswith('visiteur-'):
        try:
            comptes.compter_essai_ia(server.DB, compte['id'])
        except PermissionError as refus:
            raise LimiteAtteinte(str(refus)) from None


llm_backend.GARDE = _garde_ia
REQUETE_MESSAGES = 'SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.proprietaire=?'


def _garde_messages(c, utilisateur):
    """Dans la transaction qui enregistre une question et sa réponse : le plafond de messages d'un visiteur, tenu même si deux
    de ses demandes arrivent ensemble."""
    compte = getattr(_requete, 'compte', None)
    if compte and compte['id'] == utilisateur:
        _plafond_visiteur(compte, REQUETE_MESSAGES, MESSAGES_PAR_VISITEUR, c, 2)


server.GARDE_MESSAGES = _garde_messages
# La question d'une recherche par le sens part chez le fournisseur d'IA, sans compter dans les demandes d'un visiteur : la sienne
# ne part pas, il cherche par les mots.
server.SANS_SENS = lambda: _visiteur(getattr(_requete, 'compte', None))


def _plafond_visiteur(compte, requete, maximum, c=None, ajout=1):
    """Démonstration : ce qu'un visiteur crée sans fichier (espaces, assistants, messages) est compté lui aussi. Avec `c`, le
    compte se fait dans la transaction d'écriture (ouverte par BEGIN IMMEDIATE) : deux demandes arrivées ensemble ne
    passent pas toutes les deux."""
    if not _visiteur(compte):
        return
    if c is None:
        with server.db() as lecture:
            return _plafond_visiteur(compte, requete, maximum, lecture, ajout)
    if c.execute(requete, (compte['id'],)).fetchone()[0] + ajout > maximum:
        raise LimiteAtteinte(MESSAGE_ESSAI_VISITEUR)


def _plafonds_documents(c, compte, espace):
    """Plafonds de ce qu'un visiteur dépose (documents d'un espace, ou actes à publier si `espace` est None), pour lui et pour
    l'ensemble des visiteurs. Contrôlés avant de lire le fichier, puis de nouveau dans la transaction d'écriture."""
    if espace is None:
        miens = c.execute('SELECT COUNT(*) FROM publications WHERE cree_par=?', (compte['id'],)).fetchone()[0]
        tous = c.execute(f'SELECT COUNT(*) FROM publications WHERE cree_par IN ({_VISITEURS})').fetchone()[0]
    else:
        miens = c.execute('SELECT COUNT(*) FROM documents d JOIN spaces s ON s.id=d.space_id WHERE s.proprietaire=?',
                          (compte['id'],)).fetchone()[0]
        tous = c.execute(f'SELECT COUNT(*) FROM documents d JOIN spaces s ON s.id=d.space_id WHERE s.proprietaire IN ({_VISITEURS})').fetchone()[0]
    if miens >= DOCUMENTS_PAR_VISITEUR:
        raise LimiteAtteinte(MESSAGE_DOCUMENTS_VISITEUR)
    if tous >= DOCUMENTS_VISITEURS_MAX:
        raise LimiteAtteinte(MESSAGE_STOCKAGE_VISITEURS)


def _texte_visiteur(compte, paragraphes):
    """Démonstration : l'acte d'un visiteur a la même limite de texte que ses documents (le poids du fichier n'en dit rien)."""
    if _visiteur(compte) and sum(len(p) for p in paragraphes if isinstance(p, str)) > TEXTE_VISITEUR:
        raise ValueError(MESSAGE_TEXTE_VISITEUR)
    return paragraphes


def _a_l_ecriture(compte, espace=None):
    """Contrôle à rejouer dans la transaction d'écriture de ce qu'un visiteur dépose ; None pour un compte."""
    return (lambda c: _plafonds_documents(c, compte, espace)) if _visiteur(compte) else None


def _place_visiteur(compte, corps, espace=None, nom=''):
    """Avant de lire le fichier d'un visiteur (document d'un espace, ou acte déposé si `espace` est None) : sa taille, puis
    les plafonds. Rend la réponse 413 d'un fichier trop gros, sinon None ; lève pour les autres refus."""
    if len(corps) > TAILLE_VISITEUR:
        return _json(413, {'error': MESSAGE_TAILLE_VISITEUR})
    # Le nom tel que l'import le lira (espaces et fins de ligne autour retirés) ; un acte déposé est contrôlé par publication.
    if espace is not None and Path(server.nom_document(nom)).suffix.lower() == '.docx':
        try:
            publication.verifier_archive_word(io.BytesIO(corps))
        except ValueError:
            raise ValueError(MESSAGE_TEXTE_VISITEUR) from None
    with server.db() as c:
        if espace is not None and c.execute('SELECT proprietaire FROM spaces WHERE id=?', (espace,)).fetchone()['proprietaire'] != compte['id']:
            raise PermissionError(IMPORT_VISITEUR)  # un espace commun garderait le document après l'effacement du compte
        _plafonds_documents(c, compte, espace)
    return None


def _journaliser(methode, chemin, status, debut, code):
    journal.evenement(_etape(methode, chemin), str(status), round((time.monotonic() - debut) * 1000), code)


def _octets(texte):
    return texte.encode('utf-8', 'surrogatepass')


def _autorise(methode, chemin, entetes):
    if chemin in PRIVEES:
        attendu = REGLAGES.service_token
        fourni = entetes.get('x-territoria-service-token', '')
        return bool(attendu) and hmac.compare_digest(_octets(fourni), _octets(attendu))
    origine = urlsplit(REGLAGES.origine)
    localhost = 'localhost' + (f':{origine.port}' if origine.port else '')
    hotes = {origine.netloc.lower(), localhost}
    if entetes.get('host', '').lower() not in hotes:
        return False
    if methode in ('POST', 'DELETE'):
        return entetes.get('origin', '').lower() in {f'{origine.scheme}://{hote}' for hote in hotes}
    return True


def handle(methode: str, chemin: str, entetes: Mapping[str, str], corps: bytes) -> Reponse:
    debut = time.monotonic()
    methode = methode.upper()
    chemin, _, requete = chemin.partition('?')
    reponse, code = _traiter(methode, chemin, requete, {cle.lower(): valeur for cle, valeur in entetes.items()}, corps)
    _journaliser(methode, chemin, reponse.status, debut, code)
    return reponse


def _traiter(methode, chemin, requete, entetes, corps):
    """Renvoie la réponse et, quand une exception l'a produite, son code pour le journal."""
    if methode == 'GET' and chemin == '/healthz':
        return _json(200, {'ready': True}), None
    if not _autorise(methode, chemin, entetes):
        return _json(403, REFUS), None
    _conservation()
    compte = None
    if chemin not in PRIVEES:
        compte = _compte(entetes)
        refus = _politique(methode, chemin, compte)
        if refus:
            return refus, None
    _requete.compte = compte
    if methode == 'GET':
        try:
            if AUDIO.fullmatch(chemin):  # seule lecture qui dépend des en-têtes (plages d'octets)
                return _audio(chemin, entetes, compte), None
            return _get(chemin, parse_qs(requete), compte), None
        except LookupError as erreur:
            return _json(404, {'error': 'Introuvable.'}), _code(erreur)
        except ValueError as erreur:
            if chemin == '/api/ia':  # réglages IA de l'opérateur inutilisables : message explicite
                return _json(400, {'error': str(erreur)}), _code(erreur)
            return _json(500, ERREUR_LECTURE), _code(erreur)
        except Exception as erreur:
            return _json(500, ERREUR_LECTURE), _code(erreur)
    if methode == 'POST':
        if not 1 <= len(corps) <= server.MAX_UPLOAD:
            return _json(413, TROP_VOLUMINEUX), None
        if chemin not in PRIVEES and entetes.get('content-type', '').split(';')[0].strip().lower() in TYPES_SIMPLES:
            return _json(415, {'error': 'Type de contenu non accepté.'}), None
        try:
            return _post(chemin, requete, corps, compte, entetes), None
        except PermissionError as erreur:
            return _json(403, {'error': str(erreur)}), _code(erreur)
        except LookupError as erreur:
            return _json(404, {'error': 'Introuvable.'}), _code(erreur)
        except LimiteAtteinte as erreur:
            return _json(429, {'error': str(erreur)}), _code(erreur)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as erreur:
            if chemin in PRIVEES:  # étape appelée par n8n : sa raison remonte à l'écran (server.call_n8n, draft_arrete)
                llm_backend.retenir_cause(str(erreur) or 'Document ou requête invalide.')
            return _json(400, {'error': str(erreur) or 'Document ou requête invalide.'}), _code(erreur)
        except RuntimeError as erreur:
            if chemin in PRIVEES:
                llm_backend.retenir_cause(str(erreur))
            return _json(502, {'error': str(erreur)}), _code(erreur)
        except Exception as erreur:
            return _json(500, ERREUR_TRAITEMENT), _code(erreur)
    if methode == 'DELETE':
        try:
            return _delete(chemin, compte), None
        except PermissionError as erreur:
            return _json(403, {'error': str(erreur)}), _code(erreur)
        except LookupError as erreur:
            return _json(404, {'error': 'Introuvable.'}), _code(erreur)
        except Exception as erreur:
            return _json(500, ERREUR_TRAITEMENT), _code(erreur)
    return _json(405, {'error': 'Méthode non autorisée.'}), None


def _jeton(entetes):
    try:
        morceau = SimpleCookie(entetes.get('cookie', '')).get(comptes.COOKIE)
    except CookieError:
        return ''
    return morceau.value if morceau else ''


def _compte(entetes):
    """Compte de la requête : l'utilisateur implicite tant qu'aucun compte n'existe, sinon celui de la session (ou None).

    En démonstration, jamais d'utilisateur implicite : sans session, aucun accès."""
    if not REGLAGES.demonstration and not comptes.mode_equipe(server.DB):
        return comptes.IMPLICITE
    return comptes.session(server.DB, _jeton(entetes))


def _politique(methode, chemin, compte):
    """Refus selon la connexion et le rôle ; les droits sur chaque espace, conversation ou fichier sont vérifiés ensuite."""
    if not chemin.startswith('/api/') or (methode, chemin) in PUBLIQUES:
        return None
    if compte is None:
        return _json(401, CONNEXION_REQUISE)
    if compte['doit_changer'] and (methode, chemin) not in CHANGEMENT:
        return _json(403, {'error': 'Choisissez d’abord votre mot de passe.', 'code': 'changer_mot_de_passe'})
    if chemin == '/api/seances' or chemin.startswith('/api/seances/'):
        # Sans transcription, rien ne s'ajoute et rien ne se lit (masquer la page ne suffit pas). Seules restent, pour un compte,
        # la liste de ses séances venues d'une installation complète et leur suppression, pour libérer la place.
        ancienne = (methode, chemin) == ('GET', '/api/seances') or (methode == 'DELETE' and re.fullmatch(r'/api/seances/[0-9a-f]{32}', chemin))
        if REGLAGES.orchestration == 'interne' and (_visiteur(compte) or not ancienne):
            return _json(404, SEANCES_INDISPONIBLES)
        if _visiteur(compte):  # une séance accepte 1 Gio d'audio : bien au-delà de ce qu'un visiteur peut déposer
            return _json(403, {'error': SEANCES_VISITEUR, 'code': 'role'})
    role = compte['role']
    if chemin.startswith(('/api/admin/assistants', '/api/admin/retours')):
        autorise = role in ('administrateur', 'referent')
    elif chemin.startswith('/api/admin/') or (methode, chemin) in ADMINISTRATION:
        autorise = role == 'administrateur'
    elif methode in ('POST', 'DELETE') and (chemin == '/api/spaces' or chemin.startswith(('/api/assistants', '/api/publications'))):
        autorise = role != 'lecteur'
    else:
        autorise = True
    return None if autorise else _json(403, {'error': 'Votre rôle ne permet pas cette action.', 'code': 'role'})


def _cookie(reponse, jeton, duree):
    # HttpOnly : illisible par le JavaScript de la page ; SameSite=Strict : jamais envoyé par une page d'un autre site.
    valeur = f'{comptes.COOKIE}={jeton}; Path=/; HttpOnly; SameSite=Strict; Max-Age={duree}'
    if urlsplit(REGLAGES.origine).scheme == 'https':  # derrière le proxy HTTPS de la démonstration : jamais en clair
        valeur += '; Secure'
    return Reponse(reponse.status, reponse.headers + [('Set-Cookie', valeur)], reponse.body)


def _proprietaire_vu(compte):
    """Assistants personnels gérables dans Administration › Assistants : tous en mode « un poste », sinon les siens seulement."""
    return None if compte.get('implicite') else compte['id']


def _session_publique(compte):
    if compte is None:
        return None
    return {k: compte.get(k) for k in ('id', 'identifiant', 'nom', 'role', 'service', 'doit_changer')} | {'implicite': bool(compte.get('implicite'))}


def _noter(action, objet='', detail='', duree_ms=None, statut='ok', utilisateur=None):
    """Journal d'activité ; son échec ne fait pas échouer une action déjà faite (il est signalé au journal de fonctionnement)."""
    if utilisateur is None:
        utilisateur = (getattr(_requete, 'compte', None) or {}).get('id', '')
    try:
        administration.noter(server.DB, action, objet, detail, duree_ms, statut, utilisateur)
    except Exception as erreur:
        journal.evenement('activite:noter', 'echec', code=_code(erreur))


def _conservation():
    try:
        server.conservation_si_necessaire()
    except Exception as erreur:
        journal.evenement('conservation', 'echec', code=_code(erreur))


def _espace_du_document(ident):
    with server.db() as c:
        ligne = c.execute('SELECT space_id FROM documents WHERE id=?', (ident,)).fetchone()
    if not ligne:
        raise LookupError(ident)
    return ligne['space_id']


def _exiger_conversation(compte, ident):
    if comptes.proprietaire_conversation(server.DB, ident) != compte['id']:
        raise LookupError(ident)  # celle d'un autre compte reste « introuvable », administrateur compris


def _audio(chemin, entetes, compte):
    """Audio d'une séance pour la relecture, par plages d'octets (en-tête Range) : jamais tout le fichier en mémoire."""
    source = seances.source_audio(server.DB, server.DATA, AUDIO.fullmatch(chemin).group(1), compte['id'])
    try:  # ouvert sans suivre de lien, puis vérifié : le dossier est aussi écrit par le conteneur de travaux
        flux = os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb')
    except OSError:
        raise LookupError('aucun audio') from None
    with flux:
        etat = os.fstat(flux.fileno())
        if not stat.S_ISREG(etat.st_mode):
            raise LookupError('aucun audio')
        return _plage(flux, etat.st_size, source.suffix.lstrip('.'), entetes)


def _plage(flux, taille, format_, entetes):
    m = PLAGE.fullmatch(entetes.get('range', '').strip())
    partiel = bool(m and (m.group(1) or m.group(2)))
    if not partiel:  # absent ou illisible : on l'ignore, comme le permet HTTP
        debut, fin = 0, taille - 1
    elif m.group(1):
        debut, fin = int(m.group(1)), int(m.group(2)) if m.group(2) else taille - 1
    else:  # « bytes=-N » : les N derniers octets
        debut, fin = max(0, taille - int(m.group(2))), taille - 1
    if partiel and (debut >= taille or fin < debut):
        reponse = _json(416, {'error': 'Plage demandée hors du fichier.'})
        return Reponse(416, reponse.headers + [('Content-Range', f'bytes */{taille}')], reponse.body)
    fin = min(fin, taille - 1, debut + PLAGE_MAX - 1)
    partiel = partiel or fin < taille - 1
    flux.seek(debut)
    octets = flux.read(fin - debut + 1)
    reponse = _reponse(206 if partiel else 200, octets, TYPES_AUDIO.get(format_, 'application/octet-stream'))
    entetes_sortie = reponse.headers + [('Accept-Ranges', 'bytes')]
    if partiel:
        entetes_sortie.append(('Content-Range', f'bytes {debut}-{fin}/{taille}'))
    return Reponse(reponse.status, entetes_sortie, reponse.body)


def _delete(chemin, compte):
    """Suppression réelle d'une conversation, d'un document, d'un assistant personnalisé, d'un service ou d'une séance ;
    sinon 405."""
    m = PUBLICATION.fullmatch(chemin)
    if m:
        _publication(compte, m.group(1))
        publication.supprimer(server.DB, server.DATA, m.group(1))
        _noter('publication_supprimee', m.group(1))
        return _json(200, {'supprime': True})
    m = re.fullmatch(r'/api/seances/([0-9a-f]{32})', chemin)
    if m:
        genre = seances.lire(server.DB, m.group(1), compte['id'], server.DATA)['type']
        seances.supprimer(server.DB, server.DATA, m.group(1), compte['id'])
        server.purger_journal()
        _noter('seance_supprimee', genre)
        return _json(200, {'supprime': True})
    m = re.fullmatch(r'/api/assistants/([0-9a-f]{32})', chemin)
    if m:
        assistants_perso.supprimer(server.DB, m.group(1), _proprietaire_vu(compte))
        _noter('assistant_supprime', m.group(1))
        return _json(200, {'supprime': True})
    m = re.fullmatch(r'/api/admin/assistants/([^/]+)', chemin)
    if m:
        administration.supprimer_assistant(server.DB, m.group(1), _proprietaire_vu(compte))
        _noter('assistant_supprime', m.group(1))
        return _json(200, {'supprime': True})
    m = re.fullmatch(r'/api/admin/retours/([^/]+)', chemin)
    if m:
        retours.supprimer(server.DB, m.group(1))
        _noter('signalement_supprime', m.group(1))
        return _json(200, {'supprime': True})
    m = re.fullmatch(r'/api/admin/services/([^/]+)', chemin)
    if m:
        comptes.supprimer_service(server.DB, m.group(1))
        _noter('service_supprime', m.group(1))
        return _json(200, {'supprime': True})
    m = re.fullmatch(r'/api/(conversations|documents)/([^/]+)', chemin)
    if not m:
        return _json(405, {'error': 'Méthode non autorisée.'})
    if m.group(1) == 'conversations':
        _exiger_conversation(compte, m.group(2))
    else:
        comptes.exiger_espace(server.DB, compte, _espace_du_document(m.group(2)), 'edition')
    supprimer = server.supprimer_conversation if m.group(1) == 'conversations' else server.supprimer_document
    if not supprimer(m.group(2)):
        return _json(404, {'error': 'Introuvable.'})
    _noter('conversation_supprimee' if m.group(1) == 'conversations' else 'document_supprime', m.group(2))
    return _json(200, {'supprime': True})


def _statut():
    etats = {}
    try:
        if REGLAGES.orchestration == 'interne':  # démonstration légère : TerritorIA enchaîne lui-même les étapes
            etats['n8n'] = True
        else:
            with server.ouvrir_sans_proxy(server.N8N_STATUS, timeout=2) as r:  # jamais par un proxy
                json.load(r)
            etats['n8n'] = True
    except Exception:
        etats['n8n'] = False
    try:
        etats['lm'] = llm_backend.test(server.DATA)['model_available']
    except Exception:
        etats['lm'] = False
    try:
        reglages = llm_backend.public(server.DATA)
    except ValueError as erreur:
        return etats | {'lm': False, 'model': '', 'provider': llm_backend.PROVIDER, 'erreur': str(erreur)}
    return etats | {'model': reglages['model'], 'provider': reglages['provider']}


def _seance_post(ident, action, segment, d, compte):
    base, data, proprietaire = server.DB, server.DATA, compte['id']
    if action == 'audio/fin':
        seances.terminer_envoi(base, data, ident, proprietaire, REGLAGES.transcription_modele)
        _noter('transcription_lancee', seances.lire(base, ident, proprietaire, data)['type'])
    elif action == 'intervenants':
        seances.nommer_intervenant(base, ident, proprietaire, d.get('etiquette'), d.get('nom'))
    elif action == 'fusion':
        seances.fusionner_intervenants(base, ident, proprietaire, d.get('source'), d.get('cible'))
    elif action == 'valider':
        seances.valider(base, data, ident, proprietaire)
        _noter('transcription_validee', seances.lire(base, ident, proprietaire, data)['type'])
    elif action == 'reessayer':
        seances.reessayer(base, data, ident, proprietaire)
    elif action == 'details':
        return _json(200, {'details': seances.enregistrer_details(base, ident, proprietaire, d)})
    elif action == 'points':
        return _json(200, {'points': seances.enregistrer_points(base, ident, proprietaire, d.get('points'))})
    elif action == 'rediger':  # la rédaction tourne en arrière-plan : la réponse n'attend pas le modèle
        seances.demander_redaction(base, ident, proprietaire, d.get('document'))
        return _json(200, {'etat': 'redaction'})
    else:
        seances.modifier_segment(base, ident, proprietaire, segment, d.get('texte'), d.get('etiquette'))
    return _json(200, {'ok': True})


def _get(p, q, compte):
    if p == '/api/publications':
        proprietaire = None if compte['role'] in ('administrateur', 'referent') else compte['id']
        return _json(200, {'publications': publication.liste(server.DB, proprietaire, REGLAGES.demonstration)})
    if p == '/api/publications/matieres':
        return _json(200, {'matieres': nomenclature.matieres()})
    m = PUBLICATION.fullmatch(p)
    if m:
        return _json(200, _publication(compte, m.group(1)))
    data = server.DATA
    if p == '/api/seances':
        return _json(200, {'seances': seances.lister(server.DB, compte['id'])})
    m = re.fullmatch(r'/api/seances/([0-9a-f]{32})(/export|/ordre-du-jour)?', p)
    if m:
        seance = seances.lire(server.DB, m.group(1), compte['id'], data)  # séance d'un autre compte : 404
        if not m.group(2):
            seances.relever(server.DB, data)
            return _json(200, seances.lire(server.DB, m.group(1), compte['id'], data))
        try:  # état ne permettant pas la demande : 400 (une lecture en échec répondrait sinon 500)
            if m.group(2) == '/ordre-du-jour':
                return _json(200, {'points': seances.proposer_ordre_du_jour(server.DB, data, seance['id'], compte['id'])})
            document = q.get('document', [''])[0]
            if document:
                chemin = seances.word_document(server.DB, data, seance['id'], compte['id'], document)
                return _reponse(200, chemin.read_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                f'attachment; filename="{chemin.name}"')
        except ValueError as erreur:
            return _json(400, {'error': str(erreur)})
        heures = q.get('heures', ['0'])[0] == '1'
        if q.get('format', ['txt'])[0] == 'docx':
            chemin = seances.word_integral(server.DB, data, seance['id'], compte['id'], heures)
            return _reponse(200, chemin.read_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                            'attachment; filename="transcription.docx"')
        return _reponse(200, seances.texte_integral(server.DB, seance['id'], compte['id'], heures).encode('utf-8'),
                        'text/plain; charset=utf-8', 'attachment; filename="transcription.txt"')
    if p == '/api/session':
        session = {'mode': 'equipe' if REGLAGES.demonstration or comptes.mode_equipe(server.DB) else 'solo',
                   'utilisateur': _session_publique(compte), 'roles': comptes.ROLES}
        if REGLAGES.demonstration:  # écran de connexion, ou « Démonstration en préparation » tant qu'aucun compte n'existe
            session |= {'demonstration': True, 'pret': comptes.mode_equipe(server.DB), 'fournisseur': _fournisseur_en_ligne(),
                        'version': VERSION}
        if REGLAGES.orchestration == 'interne' or _visiteur(compte):  # pas de transcription, ou réservée aux comptes : page masquée
            session['seances'] = False
        return _json(200, session)
    if p == '/api/spaces':
        niveaux = comptes.niveaux_espaces(server.DB, compte)
        with server.db() as c:
            rows = c.execute('SELECT spaces.id,spaces.title,spaces.description,spaces.created,spaces.proprietaire,COUNT(documents.id) AS count FROM spaces LEFT JOIN documents ON documents.space_id=spaces.id GROUP BY spaces.id ORDER BY spaces.created').fetchall()
        return _json(200, [{k: x[k] for k in ('id', 'title', 'description', 'created', 'count')} | {'niveau': niveaux[x['id']]}
                           for x in rows if x['id'] in niveaux])
    if p == '/api/assistants':
        visibles = comptes.assistants_visibles(server.DB, compte)
        with server.db() as c:
            rows = c.execute('SELECT id,title,description,custom,proprietaire,champs FROM assistants WHERE actif=1 ORDER BY custom DESC,rowid').fetchall()
        return _json(200, [{k: x[k] for k in ('id', 'title', 'description', 'custom')} | {'mien': bool(x['custom']) and x['proprietaire'] == compte['id'],
                            'categorie': 'Vos assistants' if x['custom'] else server.CATEGORIE.get(x['id'], 'Autres'),
                            'champs': assistants_perso.champs_de(x)}
                           for x in rows if x['id'] in visibles])
    m = re.fullmatch(r'/api/assistants/([0-9a-f]{32})', p)
    if m:
        return _json(200, assistants_perso.lire(server.DB, m.group(1), _proprietaire_vu(compte)))
    if p == '/api/annuaire':
        # En démonstration, un visiteur ne partage rien : il n'a pas à connaître les noms des autres comptes.
        return _json(200, {'utilisateurs': [], 'services': []} if _visiteur(compte) else comptes.annuaire(server.DB))
    m = re.fullmatch(r'/api/partages/(espace|assistant)/([^/]+)', p)
    if m:
        return _json(200, comptes.partages(server.DB, compte, m.group(1), m.group(2)))
    if p == '/api/admin/utilisateurs':
        return _json(200, comptes.utilisateurs(server.DB))
    if p == '/api/admin/retours':
        return _json(200, retours.signalements(server.DB))
    if p == '/api/admin/recherche':
        return _json(200, semantique.etat(server.DB, data) | {'disponibles': semantique.disponibles(data)})
    if p == '/api/connecteurs':
        etat = connecteurs.etat(data, REGLAGES.hors_ligne)
        return _json(200, {'datagouv': etat['datagouv']['actif'] and not _visiteur(compte),
                           'legifrance': etat['legifrance']['actif'] and not _visiteur(compte),
                           'hors_ligne': REGLAGES.hors_ligne})
    if p == '/api/admin/connecteurs':
        return _json(200, connecteurs.etat(data, REGLAGES.hors_ligne))
    if p == '/api/connecteurs/datagouv':
        if _visiteur(compte):
            return _json(403, {'error': RESERVE_AUX_COMPTES})
        try:
            connecteurs.exiger(data, REGLAGES.hors_ligne, 'datagouv')
            return _json(200, connecteurs.rechercher(q.get('q', [''])[0], int(q.get('page', ['1'])[0] or 1)))
        except PermissionError as erreur:
            return _json(403, {'error': str(erreur)})
        except ValueError as erreur:
            return _json(400, {'error': str(erreur)})
        except RuntimeError as erreur:
            return _json(502, {'error': str(erreur)})
    if p == '/api/admin/services':
        return _json(200, comptes.services(server.DB))
    if p == '/api/regles':
        return _json(200, administration.regles(server.DB))
    if p == '/api/admin/assistants':
        return _json(200, administration.assistants(server.DB, server.TEMPLATES, _proprietaire_vu(compte)))
    if p == '/api/admin/activite':
        avant = q.get('avant', [''])[0]
        return _json(200, administration.activite(server.DB, 100, int(avant) if avant.isdigit() else None))
    if p == '/api/admin/statistiques':
        return _json(200, administration.statistiques(server.DB, 30))
    if p == '/api/admin/modeles':
        return _json(200, llm_backend.etat_modeles(data))
    if p == '/api/admin/sante':
        return _json(200, administration.sante(data, server.DB) | {'services': _statut(), 'hors_ligne': REGLAGES.hors_ligne,
                                                                   'ia': llm_backend.etat_modeles(data)})
    if p == '/api/documents':
        space = q.get('space', ['all'])[0]
        lisibles = [e for e in comptes.niveaux_espaces(server.DB, compte) if space in ('all', e)]
        if not lisibles:
            return _json(200, [])
        with server.db() as c:
            sql = 'SELECT documents.id,documents.name,documents.space_id,documents.source_url,documents.captured,documents.created,spaces.title AS space_title FROM documents JOIN spaces ON spaces.id=documents.space_id'
            rows = c.execute(sql + ' WHERE space_id IN (' + ','.join('?' * len(lisibles)) + ') ORDER BY documents.created DESC', lisibles).fetchall()
        return _json(200, [dict(x) for x in rows])
    if p == '/api/search':
        return _json(200, server.search(q.get('q', [''])[0][:3000], q.get('space', ['all'])[0],
                                        espaces=list(comptes.niveaux_espaces(server.DB, compte))))
    if p == '/api/conversations':
        with server.db() as c:
            rows = c.execute('SELECT * FROM conversations WHERE proprietaire=? ORDER BY updated DESC LIMIT 30', (compte['id'],)).fetchall()
        return _json(200, [dict(x) for x in rows])
    if p.startswith('/api/conversations/'):
        ident = p.rsplit('/', 1)[-1]
        with server.db() as c:
            conversation = c.execute('SELECT * FROM conversations WHERE id=? AND proprietaire=?', (ident, compte['id'])).fetchone()
            rows = c.execute('SELECT m.id,m.role,m.text,m.sources,m.created,m.artifact,m.note,EXISTS(SELECT 1 FROM signalements s WHERE s.message_id=m.id) AS signale '
                             'FROM messages m WHERE m.conversation_id=? ORDER BY m.id', (ident,)).fetchall()
        if not conversation:
            return _json(404, {'error': 'Conversation introuvable.'})
        return _json(200, {'conversation': dict(conversation), 'messages': [dict(x) | {'sources': json.loads(x['sources']), 'artifact': arretes.present_artifact(json.loads(x['artifact']))} for x in rows]})
    if p == '/api/identite':
        return _json(200, branding.profile(data))
    if p == '/api/admin/numerotation':
        return _json(200, registre.reglages(server.DB))
    if p == '/api/admin/registre':
        return _json(200, {'actes': registre.liste(server.DB)})
    if p == '/api/admin/registre/csv':
        annee = q.get('annee', [''])[0]
        if not re.fullmatch(r'20\d\d', annee):
            return _json(400, {'error': 'Année invalide.'})
        return _reponse(200, registre.csv_annee(server.DB, int(annee)), 'text/csv; charset=utf-8',
                        f'attachment; filename="registre-des-actes-{annee}.csv"')
    if p == '/api/ia':
        return _json(200, llm_backend.public(data))
    if p.startswith('/api/identite/logo/'):
        path, typ = branding.logo_file(data, p.rsplit('/', 1)[-1])
        return _reponse(200, path.read_bytes(), typ)
    if p == '/api/status':
        return _json(200, _statut())
    if p == '/api/arretes/modele':
        arretes.verify_reference(data)
        return _reponse(200, (data / 'modeles' / arretes.reference(data)['reference']).read_bytes(), 'application/pdf', 'inline')
    if p.startswith('/api/bibliotheque-arretes/') and p.endswith('/file'):
        ident = p.split('/')[3]
        if not re.fullmatch(r'[\w-]{20,80}', ident):
            return _json(404, {'error': 'Exemple introuvable.'})
        root = data / 'bibliotheque-arretes'
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        item = next((x for x in manifest['files'] if x['id'] == ident), None)
        if not item or item.get('filename') != ident + '.pdf':
            return _json(404, {'error': 'Exemple introuvable.'})
        raw = (root / 'pdf' / item['filename']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.get('sha256'):
            return _json(409, {'error': 'L’exemple a changé depuis son indexation.'})
        return _reponse(200, raw, 'application/pdf', 'inline')
    if p.startswith('/api/arretes/') and p.endswith('/file'):
        ident = p.split('/')[3]
        if not re.fullmatch(r'[0-9a-f]{32}', ident):
            return _json(404, {'error': 'Projet introuvable.'})
        file = data / 'arretes' / (ident + '.docx')
        if not compte.get('implicite') and not comptes.projet_de(server.DB, ident, compte['id']):
            return _json(404, {'error': 'Projet introuvable.'})
        if not file.is_file():
            return _json(404, {'error': 'Projet introuvable.'})
        try:  # le projet enregistré dit s'il s'agit d'une délibération ; à défaut, c'est un arrêté
            genre = json.loads((data / 'arretes' / (ident + '.json')).read_text(encoding='utf-8')).get('document', {}).get('kind')
        except (OSError, ValueError, AttributeError):
            genre = ''
        nom = 'projet-deliberation.docx' if genre == 'deliberation' else 'projet-arrete.docx'
        return _reponse(200, file.read_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        f'attachment; filename="{nom}"')
    if p.startswith('/api/documents/') and p.endswith('/file'):
        ident = p.split('/')[3]
        with server.db() as c:
            row = c.execute('SELECT filename,name,sha256 FROM documents WHERE id=?', (ident,)).fetchone()
        if not row or _espace_du_document(ident) not in comptes.niveaux_espaces(server.DB, compte):
            return _json(404, {'error': 'Document introuvable.'})
        raw = (server.FILES / row['filename']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            return _json(409, {'error': 'Document modifié depuis son indexation.'})
        typ = mimetypes.guess_type(row['filename'])[0] or 'application/octet-stream'
        if typ not in ('application/pdf', 'text/plain', 'text/markdown'):
            typ = 'application/octet-stream'
        return _reponse(200, raw, typ, 'inline' if typ == 'application/pdf' else 'attachment')
    path = server.PUBLIC / ('index.html' if p == '/' else p.lstrip('/'))
    if not path.resolve().is_relative_to(server.PUBLIC.resolve()) or not path.is_file():
        return _json(404, {'error': 'Introuvable.'})
    typ = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return _reponse(200, path.read_bytes(), typ + '; charset=utf-8')


def _post(p, requete, corps, compte, entetes):
    data = server.DATA
    if p == '/api/identite/logo':
        return _json(201, {'logo': branding.upload(data, corps)})
    if p == '/api/identite/gabarit':
        branding.upload_gabarit(data, corps)
        _noter('identite_modifiee')
        return _json(201, {'gabarit': True})
    if p.startswith('/api/upload/'):
        administration.exiger(server.DB, 'import_autorise', 'L’import de documents est désactivé par l’administration.')
        nom = parse_qs(requete).get('name', [''])[0]
        espace = p.rsplit('/', 1)[-1]
        comptes.exiger_espace(server.DB, compte, espace, 'contribution')
        visiteur = _visiteur(compte)
        refus = _place_visiteur(compte, corps, espace, nom) if visiteur else None
        if refus:
            return refus
        ident = server.add_doc(espace, nom, corps, TEXTE_VISITEUR if visiteur else None, MESSAGE_TEXTE_VISITEUR,
                               _a_l_ecriture(compte, espace))
        _noter('document_importe', ident, espace)
        return _json(201, {'id': ident, 'pages_ocr': server.pages_ocr(ident), 'pages_sans_texte': server.pages_sans_texte(ident)})
    m = AUDIO.fullmatch(p)
    if m:  # morceau d'audio (8 Mio au plus), corps binaire
        q = parse_qs(requete)
        try:
            rang = int(q.get('rang', [''])[0])
        except ValueError:
            raise ValueError('Rang du morceau invalide.') from None
        try:
            suivant = seances.ajouter_morceau(server.DB, data, m.group(1), compte['id'], rang, corps, q.get('format', [''])[0])
        except seances.Conflit as conflit:
            return _json(409, {'error': 'Morceau arrivé en avance.', 'rang_suivant': conflit.rang_suivant})
        return _json(200, {'rang_suivant': suivant})
    if p == '/api/publications/depot':  # corps : le fichier de l'acte
        return _depot_publication(requete, corps, compte)
    if len(corps) > MAX_JSON:
        return _json(413, {'error': 'Requête trop volumineuse.'})
    d = json.loads(corps)
    if p.startswith('/api/publications'):
        reponse = _publications_post(p, d, compte)
        if reponse is not None:
            return reponse
    if p == '/api/seances':
        ident = seances.creer(server.DB, compte['id'], d if isinstance(d, dict) else {})
        _noter('seance_creee', d['type'])  # le type seulement, jamais le titre
        return _json(201, {'id': ident})
    m = re.fullmatch(r'/api/seances/([0-9a-f]{32})/(audio/fin|intervenants|fusion|valider|reessayer|details|points|rediger|segments/(\d+))', p)
    if m:
        return _seance_post(m.group(1), m.group(2), int(m.group(3)) if m.group(3) else None, d if isinstance(d, dict) else {}, compte)
    if p == '/api/connexion':
        try:
            utilisateur, jeton = comptes.connecter(server.DB, d.get('identifiant') if isinstance(d, dict) else None,
                                                   d.get('mot_de_passe') if isinstance(d, dict) else None)
        except comptes.RefusConnexion as refus:
            _noter('connexion_refusee', statut='refuse', utilisateur='')
            return _json(401, {'error': str(refus), 'code': 'refus'})
        _noter('connexion', utilisateur=utilisateur['id'])
        return _cookie(_json(200, {'utilisateur': _session_publique(utilisateur)}), jeton, int(comptes.DUREE_MAX.total_seconds()))
    if p == '/api/session/visiteur':
        if not REGLAGES.demonstration:  # installation d'une collectivité : jamais d'accès sans compte
            return _json(404, {'error': 'Introuvable.'})
        if not comptes.mode_equipe(server.DB):  # premier administrateur pas encore créé : démonstration en préparation
            return _json(403, {'error': 'Démonstration en préparation : revenez un peu plus tard.'})
        try:
            utilisateur, jeton = comptes.creer_visiteur(server.DB)
        except PermissionError as refus:
            return _json(429, {'error': str(refus)})
        _noter('connexion_visiteur', utilisateur=utilisateur['id'])
        return _cookie(_json(200, {'utilisateur': _session_publique(utilisateur)}), jeton, int(comptes.DUREE_MAX.total_seconds()))
    if p == '/api/deconnexion':
        jeton = _jeton(entetes)
        if jeton:
            if compte and not compte.get('implicite'):
                _noter('deconnexion')
            comptes.deconnecter(server.DB, jeton)
        return _cookie(_json(200, {'deconnecte': True}), '', 0)
    if p == '/api/comptes/premier':
        if REGLAGES.demonstration:  # serveur public : le premier visiteur ne doit pas pouvoir prendre l'administration
            return _json(403, {'error': PREMIER_EN_DEMONSTRATION, 'code': 'demonstration'})
        if not compte or not compte.get('implicite'):
            raise PermissionError('Un premier compte existe déjà.')
        utilisateur = comptes.creer_premier_administrateur(server.DB, d)
        _noter('premier_compte_cree', utilisateur['id'], utilisateur=utilisateur['id'])
        jeton = comptes.ouvrir_session(server.DB, utilisateur['id'])
        return _cookie(_json(201, {'utilisateur': _session_publique(utilisateur | {'doit_changer': False})}), jeton,
                       int(comptes.DUREE_MAX.total_seconds()))
    if p == '/api/compte/mot-de-passe':
        if compte.get('implicite'):
            raise ValueError('Aucun compte n’est encore créé : le mot de passe se choisit dans Administration › Utilisateurs.')
        comptes.changer_mot_de_passe(server.DB, compte['id'], d.get('ancien'), d.get('nouveau'))
        _noter('mot_de_passe_change')
        return _json(200, {'utilisateur': _session_publique(comptes.utilisateur(server.DB, compte['id']))})
    if p == '/api/admin/utilisateurs':
        utilisateur, provisoire = comptes.creer_utilisateur(server.DB, d)
        _noter('compte_cree', utilisateur['id'])
        return _json(201, {'utilisateur': utilisateur, 'mot_de_passe': provisoire})
    m = re.fullmatch(r'/api/admin/utilisateurs/([^/]+)(/reinitialiser|/transferer)?', p)
    if m:
        ident, action = m.groups()
        if action == '/reinitialiser':
            provisoire = comptes.reinitialiser_mot_de_passe(server.DB, ident)
            _noter('mot_de_passe_reinitialise', ident)
            return _json(200, {'mot_de_passe': provisoire})
        if action == '/transferer':
            bilan = comptes.transferer(server.DB, ident, d.get('vers') if isinstance(d, dict) else None)
            _noter('contenus_transferes', ident, f"{bilan['espaces']}.{bilan['assistants']}")
            return _json(200, bilan)
        avant = comptes.utilisateur(server.DB, ident)
        if not avant:
            raise LookupError(ident)
        apres = comptes.modifier_utilisateur(server.DB, ident, d)
        _noter('compte_desactive' if avant['actif'] and not apres['actif'] else 'compte_reactive' if apres['actif'] and not avant['actif']
               else 'compte_modifie', ident)
        return _json(200, apres)
    if p == '/api/admin/connecteurs':
        connecteurs.enregistrer(data, d)
        _noter('regles_modifiees', 'connecteurs')
        return _json(200, connecteurs.etat(data, REGLAGES.hors_ligne))
    if p == '/api/admin/connecteurs/legifrance/tester':
        if not connecteurs.tester_legifrance(data, REGLAGES.hors_ligne):
            raise RuntimeError('Légifrance a refusé la demande : vérifiez l’identifiant et le secret PISTE, et que l’application '
                               'PISTE est bien abonnée à l’API Légifrance.')
        return _json(200, {'ok': True})
    if p in ('/api/connecteurs/legifrance/article', '/api/connecteurs/legifrance/ajouter'):
        if _visiteur(compte):  # chaque consultation passe par le compte PISTE de la collectivité, sans cache ni limite
            raise PermissionError(MESSAGE_LEGIFRANCE_VISITEUR)
        code = str(d.get('code', '')) if isinstance(d, dict) else ''
        article = str(d.get('article', '')) if isinstance(d, dict) else ''
        if p.endswith('/ajouter'):
            administration.exiger(server.DB, 'import_autorise', 'L’import de documents est désactivé par l’administration.')
            espace = str(d.get('espace', ''))
            comptes.exiger_espace(server.DB, compte, espace, 'contribution')
        texte = connecteurs.article_legifrance(data, REGLAGES.hors_ligne, code, article)
        if p.endswith('/article'):
            return _json(200, texte)
        nom = f"{texte['code']} — article {texte['article']}.txt"
        corps = f"{texte['code']} — Article {texte['article']}\nVersion en vigueur au {texte['as_of']} (Légifrance).\n\n{texte['text']}"
        ident = server.add_doc(espace, nom, corps.encode('utf-8'))
        with server.db() as c:
            c.execute('UPDATE documents SET source_url=?, captured=? WHERE id=?', (texte['url'], texte['as_of'], ident))
        _noter('document_importe', ident, 'legifrance')
        return _json(201, {'id': ident, 'nom': nom})
    if p == '/api/connecteurs/datagouv/import':
        if _visiteur(compte):  # un fichier pris ailleurs échapperait aux plafonds de ce qu'un visiteur dépose
            raise PermissionError(RESERVE_AUX_COMPTES)
        connecteurs.exiger(data, REGLAGES.hors_ligne, 'datagouv')
        administration.exiger(server.DB, 'import_autorise', 'L’import de documents est désactivé par l’administration.')
        espace = str(d.get('espace', '')) if isinstance(d, dict) else ''
        comptes.exiger_espace(server.DB, compte, espace, 'contribution')
        nom, contenu, page = connecteurs.recuperer(str(d.get('jeu', '')), str(d.get('ressource', '')))
        ident = server.add_doc(espace, nom, contenu)
        with server.db() as c:  # la source officielle reste citée avec le document
            c.execute('UPDATE documents SET source_url=? WHERE id=?', (page, ident))
        _noter('document_importe', ident, 'datagouv')
        return _json(201, {'id': ident, 'nom': nom})
    if p == '/api/admin/recherche':
        semantique.enregistrer(data, d.get('modele', '') if isinstance(d, dict) else None)
        _noter('ia_modifiee', 'plongements')
        semantique.lancer(server.DB, data)  # vecteurs du nouveau modèle, en arrière-plan
        return _json(200, semantique.etat(server.DB, data))
    m = re.fullmatch(r'/api/messages/(\d{1,18})/(evaluation|signalement)', p)
    if m:
        message = int(m.group(1))
        if m.group(2) == 'evaluation':
            note = retours.noter(server.DB, message, compte['id'], d.get('note') if isinstance(d, dict) else None)
            _noter('evaluation', str(message), str(note))
            return _json(200, {'note': note})
        if _visiteur(compte):  # un signalement garde la question, la réponse et le commentaire un an : pas pour un essai effacé en 24 h
            raise PermissionError(RESERVE_AUX_COMPTES)
        retours.signaler(server.DB, message, compte, d)
        _noter('signalement_envoye', str(message))
        return _json(201, {'signale': True})
    m = re.fullmatch(r'/api/admin/retours/([^/]+)', p)
    if m:
        statut = d.get('statut') if isinstance(d, dict) else None
        retours.changer_statut(server.DB, m.group(1), statut)
        _noter('signalement_traite' if statut == 'traite' else 'signalement_rouvert', m.group(1))
        return _json(200, {'statut': statut})
    m = re.fullmatch(r'/api/admin/services(?:/([^/]+))?', p)
    if m:
        service = comptes.enregistrer_service(server.DB, m.group(1), d)
        _noter('service_modifie' if m.group(1) else 'service_cree', service['id'])
        return _json(200 if m.group(1) else 201, service)
    m = re.fullmatch(r'/api/partages/(espace|assistant)/([^/]+)(/retirer)?', p)
    if m:
        ressource, ident, retirer = m.groups()
        if retirer:
            comptes.retirer_partage(server.DB, compte, ressource, ident, d)
            _noter('partage_retire', ident, ressource)
        else:
            if _visiteur(compte):
                raise PermissionError(PARTAGE_VISITEUR)
            if compte['role'] != 'administrateur':
                administration.exiger(server.DB, 'partage_autorise', 'Le partage est désactivé par l’administration.')
            comptes.partager(server.DB, compte, ressource, ident, d)
            _noter('partage_modifie', ident, ressource)
        return _json(200, comptes.partages(server.DB, compte, ressource, ident))
    acte = re.fullmatch(r'/api/actes/([0-9a-f]{32})/numero', p)
    if acte:
        if not compte.get('implicite') and not comptes.projet_de(server.DB, acte.group(1), compte['id']):
            return _json(404, {'error': 'Acte introuvable.'})
        try:
            resultat = server.numeroter(acte.group(1), '' if compte.get('implicite') else compte['id'])
        except LookupError:
            return _json(404, {'error': 'Acte introuvable.'})
        _noter('acte_numerote', resultat['type'])
        return _json(200, resultat)
    if p == '/api/admin/numerotation':
        resultat = registre.enregistrer_reglages(server.DB, d)
        _noter('numerotation_modifiee')
        return _json(200, resultat)
    if p == '/api/identite/gabarit/supprimer':
        branding.supprimer_gabarit(data)
        _noter('identite_modifiee')
        return _json(200, {'gabarit': False})
    if p == '/api/identite':
        resultat = branding.save(data, d)
        _noter('identite_modifiee')
        return _json(200, resultat)
    if p == '/api/ia':
        avant = llm_backend.read(data)['provider']
        resultat = llm_backend.save(data, d)
        if resultat['provider'] != avant:  # le modèle de plongements de l'ancien fournisseur n'existe pas chez le nouveau
            semantique.enregistrer(data, '')
            semantique.lancer(server.DB, data)
        _noter('ia_modifiee')
        return _json(200, resultat)
    if p == '/api/admin/regles':
        resultat = administration.enregistrer_regles(server.DB, d)
        _noter('regles_modifiees')
        server.appliquer_conservation()  # une durée raccourcie s'applique tout de suite
        return _json(200, resultat)
    m = re.fullmatch(r'/api/admin/assistants/([^/]+)(/retablir)?', p)
    if m:
        if m.group(2):
            administration.retablir_assistant(server.DB, m.group(1), server.TEMPLATES)  # assistants du logiciel seulement
            _noter('assistant_retabli', m.group(1))
        else:
            for action in administration.modifier_assistant(server.DB, m.group(1), d, _proprietaire_vu(compte)):
                _noter(action, m.group(1))
        return _json(200, next(a for a in administration.assistants(server.DB, server.TEMPLATES, _proprietaire_vu(compte)) if a['id'] == m.group(1)))
    if p == '/api/ia/tester':
        return _json(200, llm_backend.test(data))
    if p == '/api/llm/chat':
        return _json(200, llm_backend.chat(data, d))
    if p == '/api/verify':
        return _json(200, server.verify_for_n8n(d))
    if p == '/api/arretes/analyser':
        return _json(200, arretes.analyze(d, data))
    if p == '/api/arretes/sources':
        return _json(200, arretes.enrich(d, data))
    if p == '/api/arretes/preparer':
        reglages = llm_backend.read(data)
        return _json(200, arretes.prepare(d, data, reglages['model']) | {'provider': reglages['provider']})
    if p == '/api/arretes/finaliser':
        return _json(200, arretes.finish(d, data, llm_backend.read(data)['model']))
    if p == '/api/arretes/generer':
        if _visiteur(compte):  # le projet écrit ici ne tient à aucune conversation : il ne serait jamais effacé
            raise PermissionError(RESERVE_AUX_COMPTES)
        return _json(200, server.draft_arrete(d))
    if p == '/api/spaces':
        title = str(d.get('title', '')).strip()[:100]
        desc = str(d.get('description', '')).strip()[:300]
        if not title:
            raise ValueError('Indiquez le nom de l’espace.')
        ident = uuid.uuid4().hex
        with server.db() as c:
            c.execute('BEGIN IMMEDIATE')  # le plafond d'un visiteur et l'écriture d'un seul tenant
            _plafond_visiteur(compte, 'SELECT COUNT(*) FROM spaces WHERE proprietaire=?', ESPACES_PAR_VISITEUR, c)
            c.execute('INSERT INTO spaces(id,title,description,created,proprietaire) VALUES (?,?,?,?,?)',
                      (ident, title, desc, server.now(), compte['id']))
        _noter('espace_cree', ident)
        return _json(201, {'id': ident})
    if p == '/api/assistants':
        administration.exiger(server.DB, 'assistants_perso_autorises',
                              'La création d’assistants personnels est désactivée par l’administration.')
        ident = uuid.uuid4().hex
        assistants_perso.creer(server.DB, ident, compte['id'], d, lambda c: _plafond_visiteur(
            compte, 'SELECT COUNT(*) FROM assistants WHERE custom=1 AND proprietaire=?', ASSISTANTS_PAR_VISITEUR, c))
        _noter('assistant_cree', ident)
        return _json(201, {'id': ident})
    if p == '/api/assistants/aide':
        administration.exiger(server.DB, 'assistants_perso_autorises',
                              'La création d’assistants personnels est désactivée par l’administration.')
        modele = next(t[3] for t in server.TEMPLATES if t[0] == 'consignes-assistant')
        requete_ia = assistants_perso.requete_aide(llm_backend.require_configured(data)['model'], modele, d)
        return _json(200, assistants_perso.lire_aide(llm_backend.chat(data, requete_ia)))
    m = re.fullmatch(r'/api/assistants/([0-9a-f]{32})', p)
    if m:
        assistants_perso.modifier(server.DB, m.group(1), _proprietaire_vu(compte), d)
        _noter('assistant_modifie', m.group(1))
        return _json(200, assistants_perso.lire(server.DB, m.group(1), _proprietaire_vu(compte)))
    if p == '/api/chat':
        _plafond_visiteur(compte, REQUETE_MESSAGES, MESSAGES_PAR_VISITEUR, ajout=2)  # avant de solliciter l'IA ; rejoué à l'écriture
        debut, objet, mode = time.monotonic(), str(d.get('assistant') or ''), str(d.get('mode') or '')
        lisibles = comptes.niveaux_espaces(server.DB, compte)
        if d.get('space', 'all') not in ('all', *lisibles):
            raise ValueError('Espace introuvable.')
        if objet and objet not in comptes.assistants_visibles(server.DB, compte):
            raise ValueError('Cet assistant n’est pas disponible (désactivé ou non partagé avec vous). Choisissez-en un autre.')
        if objet and not d.get('conversation'):  # champs de l'assistant, demandés au premier message de la conversation
            with server.db() as c:
                ligne = c.execute('SELECT champs FROM assistants WHERE id=?', (objet,)).fetchone()
            bloc = assistants_perso.informations(assistants_perso.champs_de(ligne) if ligne else [], d.get('champs'))
            if bloc:
                d = dict(d, question=str(d.get('question', '')).strip()[:10000] + bloc)
        try:
            resultat = server.chat(d, compte['id'], None if compte.get('implicite') else list(lisibles))
        except ValueError:
            raise  # demande incomplète : rien à compter
        except Exception:
            _noter('question', objet, mode, round((time.monotonic() - debut) * 1000), 'erreur')
            raise
        genre = (resultat.get('artifact') or {}).get('kind')
        _noter({'arrete': 'arrete', 'deliberation': 'deliberation', 'arrete_rh': 'arrete', 'arrete_police': 'arrete', 'arrete_delegation': 'arrete',
                'arrete_clarification': 'precision', 'arrete_delegation_clarification': 'precision', 'deliberation_clarification': 'precision', 'arrete_rh_clarification': 'precision',
                'arrete_police_clarification': 'precision'}.get(genre, 'question'), objet,
               resultat.get('mode', mode), round((time.monotonic() - debut) * 1000))
        return _json(200, resultat)
    return _json(404, {'error': 'Introuvable.'})


def _entetes_wsgi(environ):
    entetes = {cle[5:].replace('_', '-').lower(): valeur for cle, valeur in environ.items() if cle.startswith('HTTP_')}
    if environ.get('CONTENT_TYPE'):  # WSGI le range hors des HTTP_*
        entetes['content-type'] = environ['CONTENT_TYPE']
    return entetes


def _chemin_wsgi(environ):
    """PATH_INFO décodé en UTF-8 ; un « ? » issu de %3F reste dans le chemin au lieu d'ouvrir une requête."""
    chemin = environ.get('PATH_INFO') or '/'
    try:
        chemin = chemin.encode('latin-1').decode('utf-8')
    except UnicodeError:
        pass
    return chemin.replace('?', '%3F')


def _depuis_wsgi(environ, methode, chemin, debut):
    longueur = str(environ.get('CONTENT_LENGTH') or '').strip()
    if longueur and not re.fullmatch(r'[0-9]{1,18}', longueur):
        reponse = _json(400, {'error': 'Requête invalide.'})
    elif longueur and int(longueur) > server.MAX_UPLOAD:
        reponse = _json(413, TROP_VOLUMINEUX)  # refusé avant toute lecture du corps
    else:
        corps = environ['wsgi.input'].read(int(longueur)) if longueur and int(longueur) else b''
        requete = environ.get('QUERY_STRING', '')
        return handle(methode, chemin + ('?' + requete if requete else ''), _entetes_wsgi(environ), corps)
    _journaliser(methode, chemin, reponse.status, debut, None)
    return reponse


def application(environ, start_response):
    debut = time.monotonic()
    methode = str(environ.get('REQUEST_METHOD') or 'GET').upper()
    chemin = _chemin_wsgi(environ)
    try:
        reponse = _depuis_wsgi(environ, methode, chemin, debut)
    except Exception as erreur:  # aucune exception ne remonte au serveur, qui journaliserait le chemin
        reponse = _json(500, ERREUR_TRAITEMENT)
        _journaliser(methode, chemin, reponse.status, debut, _code(erreur))
    start_response(f'{reponse.status} {HTTPStatus(reponse.status).phrase}', reponse.headers)
    return [reponse.body]
