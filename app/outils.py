# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Outils d'installation de Territoria : `python -m outils <commande>`.

Chaque commande imprime une seule ligne, `ETAPE <commande> : OK` ou `ETAPE <commande> : ECHEC <code>` (code en
minuscules et tirets bas), et rend 0 ou 1. Exception : `reprendre-pilote` imprime d'abord une ligne `RAPPORT
reprendre-pilote : espaces=<n> … reference_conforme=oui|non` (entiers et oui/non seulement), la ligne `ETAPE` restant la
dernière. Aucune option n'accepte de secret et aucune sortie n'en montre : les
secrets sont générés sur place, écrits dans des fichiers en 0600 et jamais régénérés quand ils existent déjà. Le mot de
passe de sauvegarde, choisi par la collectivité, arrive par l'entrée standard (saisie masquée dans un terminal) ou par
un fichier désigné par `--depuis-fichier`.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import secrets
import string
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

DOSSIER_SECRETS = Path('/donnees-secrets')
DONNEES = Path('/donnees')  # dossier de données de l'application, tel que Compose le monte dans le conteneur
# Conteneur ponctuel de sauvegarde (root) : territoria-donnees en lecture seule sur /source, les archives sur /destination,
# et pour une restauration (services arrêtés) le territoria-donnees à remplir sur /cible.
SOURCE = Path('/source')
DESTINATION = Path('/destination')
CIBLE = Path('/cible')
TAILLE_MAX_MOT_DE_PASSE = 64 * 1024
ADRESSE_N8N = 'http://n8n:5678'
COURRIEL_PROPRIETAIRE = 'admin@territoria.local'
FICHIER_PROPRIETAIRE = 'n8n-admin.txt'
DELAI_HTTP = 30

# Variables de chaque fichier d'environnement Compose, en 48 caractères (token_urlsafe(36)) : Compose lit
# territoria.env pour le service territoria et n8n.env pour n8n.
VARIABLES = {
    'territoria.env': ('TERRITORIA_SERVICE_TOKEN', 'TERRITORIA_WEBHOOK_TOKEN'),
    'n8n.env': ('N8N_ENCRYPTION_KEY',),
}
# Identifiants fixes des credentials n8n : les deux workflows y font référence.
CREDENTIALS = (
    ('TerritoriaWebh01', 'Territoria — entrée privée', 'X-Territoria-Webhook-Token', 'TERRITORIA_WEBHOOK_TOKEN'),
    ('TerritoriaServ01', 'Territoria — services internes', 'X-Territoria-Service-Token', 'TERRITORIA_SERVICE_TOKEN'),
)
_VARIABLE = re.compile(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)')


class Echec(Exception):
    """Échec attendu d'une commande ; `code` est le seul détail imprimé (motif `[a-z_]+`)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _Usage(Exception):
    pass


class _Parseur(argparse.ArgumentParser):
    def error(self, message):  # le message citerait les arguments reçus : il ne doit jamais être imprimé
        raise _Usage


def dossier_workflows() -> Path:
    """Dossier des workflows : `workflows/` à côté du code dans l'image, `n8n/workflows/` à la racine du dépôt."""
    ici = Path(__file__).resolve().parent
    for candidat in (ici / 'workflows', ici.parent / 'n8n' / 'workflows'):
        if candidat.is_dir():
            return candidat
    return ici.parent / 'n8n' / 'workflows'


def _ecrire_prive(chemin: Path, contenu: bytes) -> None:
    """Écrit (ou remplace) un fichier en 0600, sans jamais le laisser lisible par d'autres."""
    try:
        fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, contenu)
        finally:
            os.close(fd)
    except OSError:
        raise Echec('ecriture_impossible') from None


def _texte(chemin: Path) -> str:
    """Contenu d'un fichier d'environnement ; fichier absent = fichier vide."""
    try:
        return chemin.read_text(encoding='utf-8') if chemin.exists() else ''
    except (OSError, UnicodeDecodeError):
        raise Echec('fichier_illisible') from None


def _variables(texte: str) -> dict[str, str]:
    """Variables `NOM=valeur` d'un fichier d'environnement (les lignes de commentaire sont ignorées)."""
    valeurs = {}
    for ligne in texte.splitlines():
        trouve = None if ligne.lstrip().startswith('#') else _VARIABLE.fullmatch(ligne)
        if trouve:
            valeurs[trouve.group(1)] = trouve.group(2).strip().strip('"\'')
    return valeurs


def commande_secrets(args) -> None:
    """Crée les deux fichiers de secrets ou y ajoute les seules variables manquantes (jamais de réécriture)."""
    try:
        args.dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise Echec('dossier_inaccessible') from None
    ajouts = {}  # tout est vérifié avant la première écriture : un échec ne laisse rien de modifié
    for nom, variables in VARIABLES.items():
        chemin = args.dossier / nom
        texte = _texte(chemin)
        presentes = _variables(texte)
        if any(v in presentes and not presentes[v] for v in variables):
            raise Echec('variable_vide')  # une valeur existante n'est jamais remplacée, même vide : l'opérateur tranche
        lignes = ''.join(f'{v}={secrets.token_urlsafe(36)}\n' for v in variables if v not in presentes)
        ajouts[chemin] = ('\n' if texte and not texte.endswith('\n') else '') + lignes if lignes else ''
    for chemin, ajout in ajouts.items():
        try:
            fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
                if ajout:
                    os.write(fd, ajout.encode('ascii'))
            finally:
                os.close(fd)
        except OSError:
            raise Echec('ecriture_impossible') from None


def commande_provision(args) -> None:
    """Écrit credentials.json (credentials n8n en clair, chiffrés par n8n à l'import) et copie les workflows."""
    valeurs = _variables(_texte(args.dossier / 'territoria.env'))
    if not all(valeurs.get(variable) for *_, variable in CREDENTIALS):
        raise Echec('secrets_absents')
    workflows = sorted(dossier_workflows().glob('*.json'))
    if not workflows:
        raise Echec('workflows_absents')
    try:
        args.sortie.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(args.sortie, 0o700)
    except OSError:
        raise Echec('dossier_inaccessible') from None
    credentials = [{'id': ident, 'name': nom, 'type': 'httpHeaderAuth', 'data': {'name': en_tete, 'value': valeurs[variable]}}
                   for ident, nom, en_tete, variable in CREDENTIALS]
    _ecrire_prive(args.sortie / 'credentials.json', json.dumps(credentials, ensure_ascii=False, indent=2).encode('utf-8') + b'\n')
    for workflow in workflows:
        _ecrire_prive(args.sortie / workflow.name, workflow.read_bytes())


def _mot_de_passe() -> str:
    """Mot de passe conforme aux exigences de n8n (8 à 64 caractères, majuscule, minuscule et chiffre)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        mot = ''.join(secrets.choice(alphabet) for _ in range(24))
        if any(c.isupper() for c in mot) and any(c.islower() for c in mot) and any(c.isdigit() for c in mot):
            return mot


def _mot_de_passe_enregistre(chemin: Path) -> str:
    try:
        texte = chemin.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        raise Echec('fichier_illisible') from None
    trouve = re.search(r'^Mot de passe : (\S+)$', texte, re.M)
    if not trouve:
        raise Echec('fichier_illisible')
    return trouve.group(1)


def _creer_proprietaire(adresse: str, mot_de_passe: str) -> tuple[int, str]:
    """POST /rest/owner/setup ; renvoie (statut HTTP, corps de la réponse en texte)."""
    corps = json.dumps({'email': COURRIEL_PROPRIETAIRE, 'firstName': 'Administrateur', 'lastName': 'Territoria',
                        'password': mot_de_passe}).encode('utf-8')
    requete = urllib.request.Request(adresse.rstrip('/') + '/rest/owner/setup', data=corps, method='POST',
                                     headers={'Content-Type': 'application/json'})
    ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # le mot de passe ne passe par aucun proxy
    try:
        with ouvreur.open(requete, timeout=DELAI_HTTP) as reponse:
            return reponse.status, reponse.read(65536).decode('utf-8', 'replace')
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.read(65536).decode('utf-8', 'replace')
    except (urllib.error.URLError, OSError):
        raise Echec('n8n_injoignable') from None


def commande_proprietaire(args) -> None:
    """Crée le compte propriétaire de n8n avec un mot de passe généré, gardé dans n8n-admin.txt (0600)."""
    if not re.match(r'https?://', args.adresse_n8n):
        raise Echec('adresse_invalide')
    fichier = args.dossier / FICHIER_PROPRIETAIRE
    nouveau = not fichier.exists()
    if nouveau:
        mot = _mot_de_passe()
        try:
            args.dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise Echec('dossier_inaccessible') from None
        # Écrit avant l'appel : si l'appel aboutit puis que l'outil s'arrête, le mot de passe n'est pas perdu.
        _ecrire_prive(fichier, ('Compte propriétaire de n8n (accessible depuis le réseau interne uniquement).\n'
                                f'Adresse : {COURRIEL_PROPRIETAIRE}\nMot de passe : {mot}\n').encode('utf-8'))
    else:
        mot = _mot_de_passe_enregistre(fichier)
    try:
        statut, corps = _creer_proprietaire(args.adresse_n8n, mot)
        if statut == 200:
            return
        if statut == 404:
            raise Echec('route_absente')
        if statut == 400 and 'already' in corps.lower():
            if nouveau:
                raise Echec('proprietaire_deja_cree')  # le mot de passe écrit ne correspond à aucun compte
            return  # relance : le compte existe déjà avec le mot de passe enregistré
        raise Echec('reponse_inattendue')
    except Echec:
        if nouveau:
            fichier.unlink(missing_ok=True)
        raise


def commande_demo(args) -> None:
    """Installe le corpus fictif de la Commune de Démoville dans /donnees, sans jamais remplacer un modèle existant."""
    import demo  # reportlab n'est chargé que pour cette commande
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # l'indexation imprime sa progression : une seule ligne en sortie
            installe = demo.installer(DONNEES)
    except OSError:
        raise Echec('ecriture_impossible') from None
    except RuntimeError:
        raise Echec('indexation_impossible') from None
    if not installe and not (DONNEES / 'modeles' / 'manifest.json').exists():
        raise Echec('bibliotheque_etrangere')  # rien n'a été écrit, et il n'y a toujours aucun modèle de référence


def commande_verifier(args) -> None:
    """Joue le test de bout en bout contre l'application locale ; le contrôle qui échoue donne le code de l'échec.

    `--strict` (la CI, avec la fausse IA) exige aussi la citation [1] et ne rejoue pas l'arrêté après un 502 ; sans lui,
    un vrai modèle n'a pas à citer ni à répondre au format attendu du premier coup."""
    import e2e
    from config import REGLAGES
    from territoria import comptes
    base = REGLAGES.donnees / 'territoire.sqlite3'
    # Avec des comptes, chaque requête exige une session : un compte technique le temps du parcours, jamais en mode « un poste »
    # (sa création ferait passer l'instance en mode équipe).
    equipe = base.is_file() and comptes.mode_equipe(base)
    try:
        e2e.executer(strict=args.strict, jeton=comptes.ouvrir_compte_technique(base) if equipe else '')
    except AssertionError as erreur:
        noms = {nom for nom, _ in e2e.CONTROLES}
        nom = erreur.args[0] if len(erreur.args) == 1 and erreur.args[0] in noms else ''
        # Seul un nom de contrôle connu est repris (sans ses chiffres, le code est en `[a-z_]+`) ; jamais un message.
        raise Echec(re.sub(r'[^a-z_]', '', nom) or 'controle_echoue') from None
    finally:
        if equipe:
            comptes.fermer_compte_technique(base)


def _lire_mot_de_passe(args, invite: str = 'Mot de passe de la sauvegarde : ') -> str:
    """Mot de passe de sauvegarde : fichier `--depuis-fichier`, sinon entrée standard (saisie masquée dans un terminal).

    Une seule fin de ligne finale est retirée ; le mot de passe n'est jamais imprimé ni repris dans un code d'échec."""
    if getattr(args, 'depuis_fichier', None) is not None:
        try:
            with open(args.depuis_fichier, 'rb') as fichier:
                brut = fichier.read(TAILLE_MAX_MOT_DE_PASSE + 1)
        except FileNotFoundError:
            raise Echec('mot_de_passe_absent') from None
        except OSError:
            raise Echec('mot_de_passe_illisible') from None
    elif sys.stdin is None:
        raise Echec('mot_de_passe_absent')
    elif sys.stdin.isatty():
        import getpass
        try:  # getpass écrit l'invite sur le terminal, jamais sur la sortie standard
            brut = getpass.getpass(invite, stream=sys.stderr).encode('utf-8')
        except (EOFError, UnicodeError):
            raise Echec('mot_de_passe_absent') from None
    else:
        try:
            entree = getattr(sys.stdin, 'buffer', None)
            brut = entree.read(TAILLE_MAX_MOT_DE_PASSE + 1) if entree is not None else sys.stdin.read().encode('utf-8')
        except (OSError, UnicodeError):
            raise Echec('mot_de_passe_illisible') from None
    if len(brut) > TAILLE_MAX_MOT_DE_PASSE:
        raise Echec('mot_de_passe_illisible')
    try:
        texte = brut.decode('utf-8')
    except UnicodeDecodeError:
        raise Echec('mot_de_passe_illisible') from None
    texte = texte[:-2] if texte.endswith('\r\n') else texte[:-1] if texte.endswith('\n') else texte
    if not texte:
        raise Echec('mot_de_passe_absent')
    return texte


def _archive(chemin: Path) -> Path:
    """L'archive désignée ; pour un dossier, la plus récente `territoria-AAAAMMJJ-HHMMSS.tsv1` qu'il contient."""
    import sauvegarde
    if chemin.is_dir():
        try:
            archives = sorted(p for p in chemin.iterdir() if sauvegarde.NOM_ARCHIVE.fullmatch(p.name) and p.is_file())
        except OSError:
            raise Echec('archive_absente') from None
        if not archives:
            raise Echec('archive_absente')
        return archives[-1]  # AAAAMMJJ-HHMMSS : l'ordre des noms est l'ordre chronologique
    if not chemin.is_file():
        raise Echec('archive_absente')
    return chemin


def _lecture_d_archive(action):
    """Exécute `action()` en traduisant les erreurs de lecture d'une archive en codes d'échec."""
    import sauvegarde
    try:
        return action()
    except sauvegarde.ArchiveIncomplete:
        raise Echec('archive_incomplete') from None
    except sauvegarde.ArchiveIllisible:
        raise Echec('archive_illisible') from None
    except sauvegarde.ArchiveNonConforme:
        raise Echec('archive_non_conforme') from None
    except sauvegarde.CibleNonVide:
        raise Echec('cible_non_vide') from None
    except sauvegarde.RestaurationInterrompue:
        raise Echec('restauration_interrompue') from None  # données précédentes conservées dans la cible (.restauration-*)
    except OSError:
        raise Echec('fichier_inaccessible') from None


def commande_sauvegarder(args) -> None:
    """Crée une archive chiffrée de --source (territoria-donnees, en lecture seule) dans --destination."""
    import sqlite3

    import sauvegarde
    if not (args.source / 'territoria').is_dir():
        raise Echec('source_invalide')
    if not args.destination.is_dir():
        raise Echec('destination_absente')  # jamais créée : un disque externe débranché ne doit pas être remplacé
    mot_de_passe = _lire_mot_de_passe(args)
    try:
        sauvegarde.creer(args.source, args.destination, mot_de_passe, datetime.now())
    except sauvegarde.SourceInvalide:
        raise Echec('source_invalide') from None
    except FileExistsError:
        raise Echec('archive_existante') from None
    except sqlite3.Error:
        raise Echec('base_illisible') from None
    except OSError:
        raise Echec('fichier_inaccessible') from None


def commande_verifier_sauvegarde(args) -> None:
    """Déchiffre l'archive dans un dossier temporaire, la compare à son manifeste et contrôle chaque base."""
    import sauvegarde
    archive = _archive(args.archive)
    mot_de_passe = _lire_mot_de_passe(args)
    if not _lecture_d_archive(lambda: sauvegarde.verifier(archive, mot_de_passe))['conforme']:
        raise Echec('archive_non_conforme')


def commande_restaurer(args) -> None:
    """Restaure une archive dans --cible (services arrêtés), après l'avoir entièrement déchiffrée et vérifiée."""
    import sauvegarde
    archive = _archive(args.archive)
    try:
        non_vide = args.cible.is_dir() and any(args.cible.iterdir())
    except OSError:
        raise Echec('fichier_inaccessible') from None
    if non_vide and not args.forcer:
        raise Echec('cible_non_vide')  # avant toute saisie du mot de passe
    mot_de_passe = _lire_mot_de_passe(args)
    _lecture_d_archive(lambda: sauvegarde.restaurer(archive, args.cible, mot_de_passe, forcer=args.forcer))


def commande_elaguer(args) -> None:
    """Garde 7 archives quotidiennes et 4 hebdomadaires dans --destination ; supprime les autres."""
    import sauvegarde
    if not args.destination.is_dir():
        raise Echec('destination_absente')
    try:
        sauvegarde.elaguer(args.destination, date.today())
    except OSError:
        raise Echec('suppression_impossible') from None


def _rapport_reprise(bilan: dict) -> None:
    """Ligne de comptes de la reprise, avant la ligne ETAPE : entiers et oui/non seulement, aucun nom ni contenu."""
    comptes = ' '.join(f'{cle}={int(bilan[cle])}' for cle in ('espaces', 'documents', 'conversations', 'messages', 'pdf_conformes'))
    print(f"RAPPORT reprendre-pilote : {comptes} reference_conforme={'oui' if bilan['reference_conforme'] else 'non'}", flush=True)


def commande_reprendre_pilote(args) -> None:
    """Reprend les données du pilote (son dossier `data/`, arrêté et monté en lecture seule) dans /donnees.

    Seule commande qui imprime deux lignes : `RAPPORT reprendre-pilote : …` (comptes, sur un succès comme sur un échec dès
    que la copie est complète et contrôlée) puis, toujours en dernier, la ligne `ETAPE`. Les contrôles de conformité sont toujours exigés."""
    import reprise_pilote
    codes = ((reprise_pilote.ElementIgnore, 'element_ignore'),
             (reprise_pilote.DocumentsNonConformes, 'documents_non_conformes'),
             (reprise_pilote.ReferenceNonConforme, 'reference_non_conforme'),
             (reprise_pilote.PdfNonConformes, 'pdf_non_conformes'))
    try:
        bilan = reprise_pilote.importer(args.source, DONNEES, forcer=args.forcer, exiger_conformite=True)
    except reprise_pilote.ControleEchoue as erreur:
        _rapport_reprise(erreur.bilan)
        raise Echec(next(code for classe, code in codes if isinstance(erreur, classe))) from None
    except reprise_pilote.CheminsImbriques:
        raise Echec('chemins_imbriques') from None
    except reprise_pilote.SourceInvalide:
        raise Echec('source_invalide') from None
    except reprise_pilote.PiloteActif:
        raise Echec('pilote_actif') from None
    except reprise_pilote.BaseIllisible:
        raise Echec('base_illisible') from None
    except reprise_pilote.CibleNonVide:
        raise Echec('cible_non_vide') from None
    except reprise_pilote.EcartDeComptage:
        raise Echec('ecart_de_comptage') from None
    except reprise_pilote.RepriseInterrompue as erreur:
        if erreur.bilan:
            _rapport_reprise(erreur.bilan)
        raise Echec('reprise_interrompue') from None  # données précédentes conservées dans la cible (.reprise-*)
    except OSError as erreur:
        if getattr(erreur, 'bilan', None):  # échec de l'échange final, déjà défait : les comptes de la copie sont connus
            _rapport_reprise(erreur.bilan)
        raise Echec('fichier_inaccessible') from None
    _rapport_reprise(bilan)


def commande_migrer(args) -> None:
    """Applique les migrations de la base. Avec --essai : sur une copie, intégrité contrôlée, original intact."""
    import sqlite3

    from territoria.base import migrer
    if not args.base.is_file():
        raise Echec('migration_impossible')  # jamais de base vide créée à la place d'un chemin faux
    try:
        if args.essai:
            if migrer.essai(args.base)['integrite'] != 'ok':
                raise Echec('migration_impossible')
        else:
            migrer.appliquer(args.base)
    except (sqlite3.Error, OSError):
        raise Echec('migration_impossible') from None


def commande_reinitialiser_acces(args) -> None:
    """Secours d'un compte (administrateur qui a oublié son mot de passe ou s'est bloqué) : nouveau mot de passe saisi masqué."""
    import sqlite3

    from territoria import comptes
    if not args.base.is_file():
        raise Echec('base_absente')
    mot_de_passe = _lire_mot_de_passe(args, 'Nouveau mot de passe (12 caractères au moins) : ')
    try:
        comptes.definir_mot_de_passe(args.base, args.identifiant, mot_de_passe)
    except ValueError:
        raise Echec('mot_de_passe_trop_court') from None
    except LookupError:
        raise Echec('compte_introuvable') from None
    except sqlite3.Error:
        raise Echec('base_illisible') from None


def commande_premier_administrateur(args) -> None:
    """Serveur de démonstration : premier administrateur créé sur la machine, mot de passe saisi masqué, jamais par le web."""
    import sqlite3

    from territoria import comptes
    if not args.base.is_file():
        raise Echec('base_absente')
    mot_de_passe = _lire_mot_de_passe(args, 'Mot de passe du premier administrateur (12 caractères au moins) : ')
    try:
        comptes.valider_mot_de_passe(mot_de_passe)
    except ValueError:
        raise Echec('mot_de_passe_trop_court') from None
    try:
        comptes.creer_premier_administrateur(args.base, {'identifiant': args.identifiant, 'nom': args.nom,
                                                         'mot_de_passe': mot_de_passe})
    except PermissionError:
        raise Echec('premier_compte_existant') from None
    except ValueError:
        raise Echec('compte_invalide') from None
    except sqlite3.Error:
        raise Echec('base_illisible') from None


COMMANDES = {
    'secrets': ('Génère les fichiers de secrets (sans jamais réécrire une valeur existante).', commande_secrets),
    'provision-n8n': ('Prépare les credentials et les workflows à importer dans n8n.', commande_provision),
    'proprietaire-n8n': ('Crée le compte propriétaire de n8n.', commande_proprietaire),
    'demo': ('Installe le corpus fictif de démonstration (Commune de Démoville).', commande_demo),
    'verifier': ('Vérifie l\'application de bout en bout (création, recherche, arrêté, refus, nettoyage).', commande_verifier),
    'sauvegarder': ('Crée une sauvegarde chiffrée de territoria-donnees.', commande_sauvegarder),
    'verifier-sauvegarde': ('Vérifie une sauvegarde (déchiffrement complet, empreintes, intégrité des bases).',
                            commande_verifier_sauvegarde),
    'restaurer': ('Restaure une sauvegarde dans un dossier vide (services arrêtés).', commande_restaurer),
    'elaguer': ('Garde 7 sauvegardes quotidiennes et 4 hebdomadaires, supprime les autres.', commande_elaguer),
    'reprendre-pilote': ('Reprend les données du pilote (base, documents, bibliothèque d\'arrêtés, modèle) dans /donnees.',
                         commande_reprendre_pilote),
    'migrer': ('Applique les migrations de la base (--essai : sur une copie, sans toucher à l\'original).', commande_migrer),
    'reinitialiser-acces': ('Donne un nouveau mot de passe à un compte et le débloque (saisie masquée).',
                            commande_reinitialiser_acces),
    'premier-administrateur': ('Crée le premier compte administrateur du serveur de démonstration (saisie masquée).',
                               commande_premier_administrateur),
}
AVEC_DOSSIER = ('secrets', 'provision-n8n', 'proprietaire-n8n')
AVEC_MOT_DE_PASSE = ('sauvegarder', 'verifier-sauvegarde', 'restaurer')


def construire_parseur() -> argparse.ArgumentParser:
    parseur = _Parseur(prog='outils', description="Outils d'installation de Territoria.", allow_abbrev=False)
    sous = parseur.add_subparsers(dest='commande', required=True, parser_class=_Parseur)
    for nom, (aide, executer) in COMMANDES.items():
        commande = sous.add_parser(nom, help=aide, description=aide, allow_abbrev=False)
        if nom in AVEC_DOSSIER:
            commande.add_argument('--dossier', type=Path, default=DOSSIER_SECRETS,
                                  help=f'dossier des fichiers de secrets (défaut : {DOSSIER_SECRETS})')
        if nom == 'provision-n8n':
            commande.add_argument('--sortie', type=Path, default=None,
                                  help='dossier de sortie (défaut : provision/ dans le dossier des secrets)')
        if nom == 'proprietaire-n8n':
            commande.add_argument('--adresse-n8n', default=ADRESSE_N8N, help=f'adresse de n8n (défaut : {ADRESSE_N8N})')
        if nom == 'verifier':
            commande.add_argument('--strict', action='store_true',
                                  help='mode de la CI avec la fausse IA : exige la citation [1] dans la réponse sourcée')
        if nom == 'sauvegarder':
            commande.add_argument('--source', type=Path, default=SOURCE,
                                  help=f'racine de territoria-donnees, lue sans rien y écrire (défaut : {SOURCE})')
        if nom in ('sauvegarder', 'elaguer'):
            commande.add_argument('--destination', type=Path, default=DESTINATION,
                                  help=f'dossier des archives, qui doit exister (défaut : {DESTINATION})')
        if nom in ('verifier-sauvegarde', 'restaurer'):
            commande.add_argument('--archive', type=Path, default=DESTINATION,
                                  help=f'archive .tsv1, ou dossier dont on prend la plus récente (défaut : {DESTINATION})')
        if nom == 'restaurer':
            commande.add_argument('--cible', type=Path, default=CIBLE,
                                  help=f'territoria-donnees à remplir, vide sauf avec --forcer (défaut : {CIBLE})')
            commande.add_argument('--forcer', action='store_true',
                                  help='remplace territoria/ et n8n/ et les fichiers de secrets/ présents dans la cible')
        if nom == 'reprendre-pilote':
            commande.add_argument('source', type=Path,
                                  help='dossier data/ du pilote, monté en lecture seule (le pilote doit être arrêté)')
            commande.add_argument('--forcer', action='store_true',
                                  help='remplace les données déjà présentes dans la cible (conversations, documents, bibliothèque)')
        if nom == 'migrer':
            commande.add_argument('--essai', action='store_true',
                                  help='joue les migrations sur une copie de la base, sans modifier l\'original')
            commande.add_argument('--base', type=Path, default=DONNEES / 'territoire.sqlite3',
                                  help=f'base à migrer (défaut : {DONNEES / "territoire.sqlite3"})')
        if nom == 'premier-administrateur':
            commande.add_argument('identifiant', help='identifiant de connexion du premier administrateur')
            commande.add_argument('--nom', required=True, help='nom affiché du premier administrateur')
            commande.add_argument('--base', type=Path, default=DONNEES / 'territoire.sqlite3',
                                  help=f'base de Territoria (défaut : {DONNEES / "territoire.sqlite3"})')
        if nom == 'reinitialiser-acces':
            commande.add_argument('identifiant', help='identifiant de connexion du compte')
            commande.add_argument('--base', type=Path, default=DONNEES / 'territoire.sqlite3',
                                  help=f'base de Territoria (défaut : {DONNEES / "territoire.sqlite3"})')
        if nom in AVEC_MOT_DE_PASSE:
            commande.add_argument('--depuis-fichier', type=Path, default=None, metavar='FICHIER',
                                  help='lit le mot de passe de sauvegarde dans ce fichier plutôt que sur l\'entrée standard')
        commande.set_defaults(executer=executer)
    return parseur


def _terminer(commande: str, echec: str | None = None) -> int:
    print(f'ETAPE {commande} : ECHEC {echec}' if echec else f'ETAPE {commande} : OK', flush=True)
    return 1 if echec else 0


def main(argv: list[str]) -> int:
    parseur = construire_parseur()
    try:
        args = parseur.parse_args(argv)
    except _Usage:
        return _terminer(argv[0] if argv and argv[0] in COMMANDES else 'outils', 'arguments_invalides')
    except SystemExit as sortie:  # --help
        return int(bool(sortie.code))
    if args.commande == 'provision-n8n' and args.sortie is None:
        args.sortie = args.dossier / 'provision'
    try:
        args.executer(args)
    except Echec as echec:
        return _terminer(args.commande, echec.code)
    except Exception:  # aucune trace : elle pourrait citer un chemin ou une valeur
        return _terminer(args.commande, 'erreur_interne')
    return _terminer(args.commande)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
