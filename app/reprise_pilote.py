# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Reprise du pilote « Territoire IA » dans une installation neuve de Territoria.

`importer(source, cible)` recopie dans `cible` (le dossier de données de l'application, `/donnees` dans le conteneur) :
- la base `territoire.sqlite3`, par l'API de sauvegarde de SQLite ;
- les dossiers `documents/`, `bibliotheque-arretes/`, `modeles/`, `ressources-juridiques/`, `arretes/` et `identite/` ;
et réécrit `modeles/manifest.json` (modèle de référence du pilote) et `configuration/ia.json` (serveur local, sans clé
ni profils : l'ancienne configuration du pilote, avec sa clé OpenRouter, n'est jamais reprise). `deployment/` (archives
privées), `server.log` et le reste du pilote ne sont jamais importés.

Le pilote n'est jamais modifié. `source` est le dossier `data/` du pilote, monté en lecture seule : la base y est ouverte
par `file:…?mode=ro&immutable=1`, c'est-à-dire sans verrou et sans créer aucun fichier à côté d'elle. Le pilote doit donc
être **arrêté** pendant la reprise. SQLite ignorerait un journal (`-wal` ou `-journal`) laissé par un pilote encore actif ;
la reprise le refuse (`PiloteActif`) plutôt que de copier un état incomplet. Les liens symboliques et les fichiers spéciaux
ne sont ni suivis ni copiés.

Source et cible ne doivent ni être le même dossier ni s'imbriquer (liens symboliques résolus) : rien n'est créé avant ce
contrôle, pour que la reprise ne puisse jamais écrire dans le pilote.

Une cible qui contient des données de l'utilisateur est refusée, sauf avec `forcer` : au moins une conversation, un
document autre que celui de départ, un assistant personnalisé, une bibliothèque d'arrêtés ou un modèle de référence autres
que ceux de démonstration, ou une base illisible. Un espace n'est une donnée que si un document ou une conversation s'y
rattache (l'essai de bout en bout laisse un espace vide). Une cible neuve (base créée par `server.init()`) ou qui ne
contient que le corpus de démonstration est acceptée, et ce corpus est remplacé.

Tout est préparé et vérifié dans un dossier temporaire privé créé dans la cible, avant le moindre échange : comptes des
tables, intégrité de la base, aucun élément du pilote ignoré (lien symbolique ou fichier spécial dans les six dossiers),
fichier et empreinte de chaque ligne de `documents`, et, avec `exiger_conformite`, modèle de référence et PDF conformes à
l'inventaire du pilote. Ensuite seulement, les éléments sont échangés par renommage ; un échec défait l'échange, et les
anciennes versions ne sont supprimées qu'après un succès complet. En cas d'échec, la cible reste donc identique. Fichiers en
0600 et dossiers en 0700, comme dans l'application.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path

import arretes
import demo
import sauvegarde
from config import REGLAGES

BASE = 'territoire.sqlite3'
ANNEXES_BASE = (BASE + '-wal', BASE + '-shm', BASE + '-journal')
JOURNAUX_ACTIFS = (BASE + '-wal', BASE + '-journal')
DOSSIERS = ('documents', 'bibliotheque-arretes', 'modeles', 'ressources-juridiques', 'arretes', 'identite')
IA = Path('configuration') / 'ia.json'
# Tables comparées entre le pilote et la copie : (nom de la table, clé du résultat).
TABLES = (('spaces', 'espaces'), ('documents', 'documents'), ('conversations', 'conversations'), ('messages', 'messages'))
REFERENCE = 'reference-stationnement-2023-13.pdf'
REFERENCE_SHA256 = '41bda5d664a888019342c539a8e81692338bfd265d89c8df6806ca856b50d190'
TRAME = 'stationnement-2023-13'
# Ce que `server.init()` crée dans une base neuve.
DOCUMENT_INITIAL = 'cgfp-l121-1'
TAILLE_COPIE = 1024 * 1024


class SourceInvalide(ValueError):
    def __init__(self):
        super().__init__('Dossier du pilote invalide : territoire.sqlite3 est introuvable.')


class PiloteActif(ValueError):
    def __init__(self):
        super().__init__('Le pilote semble encore actif (journal SQLite présent) : arrêtez-le avant la reprise, '
                         'puis recommencez.')


class BaseIllisible(ValueError):
    def __init__(self):
        super().__init__("Base du pilote illisible ou occupée : arrêtez le pilote avant la reprise (la base est lue "
                         "sans verrou, en lecture seule), puis recommencez.")


class CibleNonVide(ValueError):
    def __init__(self):
        super().__init__('Reprise refusée : la cible contient déjà des données (conversations, documents, espaces, '
                         'assistants ou bibliothèque). Utilisez --forcer pour les remplacer.')


class EcartDeComptage(ValueError):
    def __init__(self, table: str):
        super().__init__(f'Écart de comptage : {table}')


class CheminsImbriques(ValueError):
    def __init__(self):
        super().__init__('Reprise refusée : le dossier du pilote et la cible sont le même dossier ou l\'un contient l\'autre.')


class ControleEchoue(ValueError):
    """Contrôle de la copie en échec, avant tout échange. `bilan` donne les comptes connus ; le message ne cite jamais de
    nom de fichier ni de contenu."""

    def __init__(self, message: str, bilan: dict):
        super().__init__(message)
        self.bilan = bilan


class ElementIgnore(ControleEchoue):
    def __init__(self, bilan: dict, nombre: int):
        super().__init__(f'{nombre} élément(s) du pilote ne peuvent pas être copiés (lien symbolique, fichier spécial '
                         'ou dossier remplacé par autre chose).', bilan)


class DocumentsNonConformes(ControleEchoue):
    def __init__(self, bilan: dict, nombre: int):
        super().__init__(f'{nombre} document(s) de la base sont absents de la copie ou ont une empreinte différente.', bilan)


class ReferenceNonConforme(ControleEchoue):
    def __init__(self, bilan: dict):
        super().__init__("Le modèle de référence du pilote est absent ou n'a pas l'empreinte attendue.", bilan)


class PdfNonConformes(ControleEchoue):
    def __init__(self, bilan: dict, attendus: int | None):
        detail = 'inventaire illisible' if attendus is None else f'{bilan["pdf_conformes"]} conformes sur {attendus}'
        super().__init__(f'PDF de la bibliothèque non conformes à l\'inventaire du pilote ({detail}).', bilan)


class RepriseInterrompue(RuntimeError):
    """Échec pendant l'échange, puis échec du retour arrière : rien n'a été supprimé, les données précédentes sont
    dans `dossier` (le dossier temporaire de la reprise, conservé)."""

    def __init__(self, dossier: Path):
        super().__init__(f'Reprise interrompue : les données précédentes sont conservées dans {dossier}.')
        self.dossier = dossier
        self.bilan = None  # renseigné par `importer` : la copie était complète et contrôlée quand l'échange a échoué


# --- Chemins ----------------------------------------------------------------------------------------------------------

def _contient(parent: Path, enfant: Path) -> bool:
    """Vrai si `enfant` est `parent` ou se trouve dessous. Les dossiers sont comparés par (périphérique, inode) en
    remontant les ancêtres existants de `enfant` : insensible à la casse (APFS), aux liens symboliques et aux montages liés."""
    for candidat in (enfant, *enfant.parents):
        try:
            if os.path.samefile(parent, candidat):
                return True
        except OSError:
            continue  # parent ou candidat inexistant (cible encore à créer)
    return False


def _imbriques(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return _contient(a, b) or _contient(b, a)


# --- Lecture de la source ----------------------------------------------------------------------------------------------

def _uri_immuable(chemin: Path) -> str:
    return f'{Path(chemin).resolve().as_uri()}?mode=ro&immutable=1'


def _compter(chemin: Path) -> dict[str, int]:
    """Nombre de lignes de chaque table comparée ; la base est ouverte sans verrou et sans rien créer à côté."""
    try:
        connexion = sqlite3.connect(_uri_immuable(chemin), uri=True)
        try:
            return {table: connexion.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table, _ in TABLES}
        finally:
            connexion.close()
    except sqlite3.Error:
        raise BaseIllisible() from None


def _base_source(source: Path) -> Path:
    """La base du pilote, après contrôle : fichier ordinaire (jamais un lien) et aucun journal SQLite laissé à côté."""
    base = source / BASE
    try:
        if not stat.S_ISREG(os.lstat(base).st_mode):
            raise SourceInvalide()
    except OSError:
        raise SourceInvalide() from None
    if any(os.path.lexists(source / journal) for journal in JOURNAUX_ACTIFS):
        raise PiloteActif()
    return base


# --- Cible ------------------------------------------------------------------------------------------------------------

def _base_avec_donnees(base: Path) -> bool:
    """Vrai si la base contient des données de l'utilisateur, ou si elle ne peut pas être lue.

    Un espace n'est une donnée que si un document ou une conversation s'y rattache (R2b) : l'essai de bout en bout laisse
    un espace vide, et les espaces ne peuvent pas être supprimés. Tout document autre que celui de départ et toute
    conversation sont déjà comptés ci-dessous, ce qui couvre les espaces qu'ils référencent."""
    controles = (('conversations', 'SELECT 1 FROM conversations LIMIT 1', ()),
                 ('documents', 'SELECT 1 FROM documents WHERE id != ? LIMIT 1', (DOCUMENT_INITIAL,)),
                 ('assistants', 'SELECT 1 FROM assistants WHERE custom != 0 LIMIT 1', ()))
    try:
        connexion = sqlite3.connect(f'{base.resolve().as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            tables = {ligne[0] for ligne in connexion.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return any(table in tables and connexion.execute(requete, parametres).fetchone() is not None
                       for table, requete, parametres in controles)
        finally:
            connexion.close()
    except sqlite3.Error:
        return True


def _reference_etrangere(cible: Path) -> bool:
    if not os.path.lexists(cible / 'modeles' / 'manifest.json'):
        return False
    try:
        info = arretes.reference(cible)
    except ValueError:
        return True
    return info['reference'] != demo.REFERENCE or info['trame'] != demo.TRAME


def _cible_avec_donnees(cible: Path) -> bool:
    """Vrai si la cible contient autre chose qu'une base neuve et le corpus de démonstration."""
    if not os.path.lexists(cible):
        return False
    base = cible / BASE
    return ((os.path.lexists(base) and _base_avec_donnees(base))
            or demo._bibliotheque_etrangere(cible / 'bibliotheque-arretes' / 'manifest.json')
            or _reference_etrangere(cible))


# --- Copie ------------------------------------------------------------------------------------------------------------

def _dossier_reel(chemin: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(chemin).st_mode)
    except OSError:
        return False


def _copier_fichier(source: Path, destination: Path) -> bool:
    """Copie un fichier ordinaire en 0600, sans suivre de lien ; renvoie False si ce n'en est pas un (rien n'est copié)."""
    entree = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        if not stat.S_ISREG(os.fstat(entree).st_mode):
            return False
        with os.fdopen(entree, 'rb', closefd=False) as lecture:
            sortie = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.fchmod(sortie, 0o600)
                with os.fdopen(sortie, 'wb', closefd=False) as ecriture:
                    shutil.copyfileobj(lecture, ecriture, TAILLE_COPIE)
            finally:
                os.close(sortie)
        return True
    finally:
        os.close(entree)


def _copier_arbre(source: Path, destination: Path) -> int:
    """Copie un dossier (0700) et son contenu (0600) sans suivre aucun lien. Renvoie le nombre d'éléments ignorés : liens
    symboliques, tubes, sockets et périphériques ne sont pas copiés, et l'appelant doit en faire un échec."""
    destination.mkdir(mode=0o700)
    os.chmod(destination, 0o700)
    ignores = 0
    with os.scandir(source) as elements:
        for element in sorted(elements, key=lambda e: e.name):
            if element.is_dir(follow_symlinks=False):
                ignores += _copier_arbre(source / element.name, destination / element.name)
            elif element.is_file(follow_symlinks=False):
                ignores += not _copier_fichier(source / element.name, destination / element.name)
            else:
                ignores += 1
    return ignores


def _ecrire_prive(chemin: Path, contenu: dict, **options) -> None:
    chemin.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(chemin.parent, 0o700)
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(contenu, ensure_ascii=False, **options).encode('utf-8'))
    finally:
        os.close(fd)


def _preparer(source: Path, base: Path, nouveau: Path, exiger_conformite: bool) -> dict:
    """Prépare dans `nouveau` tout ce qui sera installé, le contrôle, et renvoie le bilan de la reprise.

    Les contrôles de la copie passent tous avant le moindre échange avec la cible ; chaque échec de contrôle porte le
    bilan complet. Ordre : éléments ignorés, documents de la base, puis (avec `exiger_conformite`) modèle de référence et PDF."""
    comptes_source = _compter(base)
    copie = nouveau / BASE
    try:  # même helper que les sauvegardes : API de sauvegarde de SQLite, ici sur une source immuable
        sauvegarde._sauvegarde_sqlite(_uri_immuable(base), copie)
    except sqlite3.Error:
        raise BaseIllisible() from None
    os.chmod(copie, 0o600)
    comptes_copie = _compter(copie)
    for table, _ in TABLES:
        if comptes_copie[table] != comptes_source[table]:
            raise EcartDeComptage(table)
    if not sauvegarde._base_saine(copie):
        raise BaseIllisible()

    ignores = 0
    for nom in DOSSIERS:
        if _dossier_reel(source / nom):
            ignores += _copier_arbre(source / nom, nouveau / nom)
        elif os.path.lexists(source / nom):
            ignores += 1  # lien symbolique, fichier ou élément spécial à la place du dossier : il n'est pas copié
    if (nouveau / 'modeles').is_dir():
        _ecrire_prive(nouveau / 'modeles' / 'manifest.json', {'reference': REFERENCE, 'sha256': REFERENCE_SHA256, 'trame': TRAME},
                      indent=2)
    _ecrire_prive(nouveau / IA, {'provider': 'lmstudio', 'base_url': REGLAGES.llm_url, 'model': REGLAGES.modele, 'api_key': ''})

    attendus = _inventaire_source(source)
    bilan = {cle: comptes_copie[table] for table, cle in TABLES} | {
        'pdf_conformes': _pdf_conformes(nouveau / 'bibliotheque-arretes'),
        'reference_conforme': _reference_conforme(nouveau)}
    if ignores:
        raise ElementIgnore(bilan, ignores)
    manquants = _documents_non_conformes(copie, nouveau / 'documents')
    if manquants:
        raise DocumentsNonConformes(bilan, manquants)
    if exiger_conformite:
        if not bilan['reference_conforme']:
            raise ReferenceNonConforme(bilan)
        if attendus is None or bilan['pdf_conformes'] < attendus:
            raise PdfNonConformes(bilan, attendus)
    return bilan


def _inventaire_source(source: Path) -> int | None:
    """Nombre d'entrées de l'inventaire de la bibliothèque du **pilote** (pas de la copie : un dossier ignoré ferait
    croire à un inventaire vide) ; None si l'inventaire est absent ou illisible. Aucun lien n'est suivi."""
    racine = source / 'bibliotheque-arretes'
    manifeste = racine / 'manifest.json'
    try:
        if not _dossier_reel(racine) or not stat.S_ISREG(os.lstat(manifeste).st_mode):
            return None
        entrees = json.loads(manifeste.read_text(encoding='utf-8'))['files']
        return len(entrees) if isinstance(entrees, list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _documents_non_conformes(copie: Path, documents: Path) -> int:
    """Nombre de lignes de `documents` (base copiée) dont le fichier est absent de la copie ou a une autre empreinte."""
    try:
        connexion = sqlite3.connect(_uri_immuable(copie), uri=True)
        try:
            lignes = connexion.execute('SELECT filename, sha256 FROM documents').fetchall()
        finally:
            connexion.close()
    except sqlite3.Error:
        raise BaseIllisible() from None
    manquants = 0
    for nom, empreinte in lignes:
        chemin = documents / nom if isinstance(nom, str) and nom and Path(nom).name == nom else None
        try:
            conforme = (chemin is not None and stat.S_ISREG(os.lstat(chemin).st_mode)
                        and hashlib.sha256(chemin.read_bytes()).hexdigest() == empreinte)
        except OSError:
            conforme = False
        manquants += not conforme
    return manquants


def _pdf_conformes(bibliotheque: Path) -> int:
    """Nombre de PDF de l'inventaire dont le fichier existe et a l'empreinte annoncée."""
    try:
        entrees = json.loads((bibliotheque / 'manifest.json').read_text(encoding='utf-8'))['files']
        conformes = 0
        for entree in entrees:
            nom = entree['filename']
            if not isinstance(nom, str) or Path(nom).name != nom:
                continue
            chemin = bibliotheque / 'pdf' / nom
            if chemin.is_file() and not chemin.is_symlink() and hashlib.sha256(chemin.read_bytes()).hexdigest() == entree['sha256']:
                conformes += 1
        return conformes
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _reference_conforme(donnees: Path) -> bool:
    try:
        return arretes.verify_reference(donnees) == REFERENCE_SHA256
    except ValueError:
        return False


# --- Échange ----------------------------------------------------------------------------------------------------------

def _deplacer(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _echanger(nouveau: Path, ancien: Path, cible: Path) -> None:
    """Installe dans `cible` ce que `nouveau` contient ; ce qui est remplacé va dans `ancien`. Les fichiers annexes de
    l'ancienne base (-wal, -shm, -journal) sont écartés avec elle. Si un déplacement échoue, ceux déjà faits sont défaits
    (l'erreur est ensuite relevée) ; si le retour arrière échoue aussi, `RepriseInterrompue` désigne le dossier qui
    conserve les données précédentes."""
    faits, crees = [], []  # faits : (emplacement d'origine, emplacement actuel) ; crees : dossiers créés dans la cible
    try:
        for nom in (*ANNEXES_BASE, BASE, *DOSSIERS, str(IA)):  # les annexes d'abord : jamais à côté de la nouvelle base
            neuf, existant = nouveau / nom, cible / nom
            if os.path.lexists(existant) and (nom in ANNEXES_BASE or os.path.lexists(neuf)):
                sauve = ancien / nom
                sauve.parent.mkdir(parents=True, exist_ok=True)
                _deplacer(existant, sauve)
                faits.append((existant, sauve))
            if os.path.lexists(neuf):
                if not existant.parent.is_dir():
                    existant.parent.mkdir(mode=0o700)
                    crees.append(existant.parent)
                _deplacer(neuf, existant)
                faits.append((neuf, existant))
    except BaseException as erreur:
        defait = True
        for origine, actuel in reversed(faits):
            try:
                _deplacer(actuel, origine)
            except OSError:
                defait = False  # on continue : chaque élément remis en place compte
        for dossier in reversed(crees):
            with contextlib.suppress(OSError):
                dossier.rmdir()  # seulement s'il est vide
        if not defait:
            raise RepriseInterrompue(nouveau.parent) from erreur
        raise


def importer(source: Path, cible: Path, forcer: bool = False, exiger_conformite: bool = False) -> dict:
    """Reprend le pilote (`source` : son dossier `data/`, arrêté et monté en lecture seule) dans `cible`.

    Renvoie `{'espaces', 'documents', 'conversations', 'messages', 'pdf_conformes', 'reference_conforme'}`. Lève
    `ValueError` (ses sous-classes disent laquelle) si source et cible sont le même dossier ou s'imbriquent, si la source
    n'est pas un pilote lisible et arrêté, si la cible contient des données de l'utilisateur sans `forcer`, si les comptes
    de la copie diffèrent de ceux du pilote (`Écart de comptage : <table>`), si un élément des six dossiers du pilote ne
    peut pas être copié ou si un document de la base manque dans la copie (`ControleEchoue`, qui porte le bilan).
    Avec `exiger_conformite`, un modèle de référence non conforme ou moins de PDF conformes que l'inventaire du pilote
    échouent aussi. Dans tous ces cas la cible n'est pas modifiée."""
    source, cible = Path(source), Path(cible)
    if _imbriques(source, cible):
        raise CheminsImbriques()  # avant tout : rien ne doit être créé ni lu dans la cible, ni écrit dans le pilote
    base = _base_source(source)
    creee = False
    if os.path.lexists(cible):
        if not cible.is_dir():
            raise NotADirectoryError("La cible n'est pas un dossier.")
        if not forcer and _cible_avec_donnees(cible):
            raise CibleNonVide()
    else:
        cible.mkdir(mode=0o700, parents=True)
        creee = True
    travail, reussie, conserver = None, False, False
    try:
        travail = Path(tempfile.mkdtemp(dir=cible, prefix='.reprise-'))
        nouveau, ancien = travail / 'nouveau', travail / 'ancien'
        nouveau.mkdir(mode=0o700)
        ancien.mkdir(mode=0o700)
        bilan = _preparer(source, base, nouveau, exiger_conformite)
        try:
            _echanger(nouveau, ancien, cible)
        except (OSError, RepriseInterrompue) as erreur:
            erreur.bilan = bilan  # le bilan est connu et fiable : la CLI peut le présenter même si l'échange échoue
            raise
        reussie = True
        return bilan
    except RepriseInterrompue:
        conserver = True
        raise
    finally:
        if travail is not None and not conserver:
            sauvegarde._supprimer(travail)  # après un succès, il ne contient plus que les anciennes versions
        if creee and not reussie and not conserver:
            with contextlib.suppress(OSError):
                cible.rmdir()
