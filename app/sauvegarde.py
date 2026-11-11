# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Sauvegardes chiffrées de `territoria-donnees` : création, vérification, restauration et élagage.

Une archive `territoria-AAAAMMJJ-HHMMSS.tsv1` est un tar non compressé (format PAX), chiffré par blocs.

Contenu :
- `territoria/`, où `territoire.sqlite3` est une copie faite par l'API de sauvegarde de SQLite, sans `audio/` (les
  enregistrements des séances ne sont gardés que le temps de la relecture : ils n'entrent jamais dans une archive) ;
- `n8n/database.sqlite` (copie faite de la même façon) et `n8n/config` (clé de chiffrement de n8n). n8n recrée
  lui-même tout le reste de son dossier au démarrage ;
- `secrets/`, sans `sauvegarde.motdepasse` ni `provision/` ;
- `MANIFESTE.json`, au format `{chemin: sha256}` pour chaque fichier de l'archive.
Sont exclus partout les fichiers `*-wal`, `*-shm`, `*-journal` et `*.log`. Les liens symboliques et les fichiers
spéciaux ne sont pas suivis. Les droits, propriétaires (numériques) et dates de chaque élément sont conservés.

Format `.tsv1` :
- en-tête `b'TRSV1\\n'`, puis 16 octets de sel ;
- clé : `Scrypt(sel, longueur=32, n=2**15, r=8, p=1)` sur le mot de passe encodé en UTF-8 ;
- corps : une suite de blocs `[longueur sur 4 octets big-endian][chiffré AES-256-GCM]`, de 4 Mio de clair chacun
  (le dernier peut être plus court, ou vide) ;
- nonce : 4 octets nuls suivis de l'index du bloc sur 8 octets big-endian ;
- AAD : en-tête + sel + `b'F'` pour le dernier bloc, `b'C'` pour les autres. Sans bloc `F`, l'archive est incomplète.

Le mot de passe n'apparaît dans aucun message d'erreur. Le clair déchiffré n'est écrit que dans un dossier temporaire
privé (0700), supprimé dans tous les cas.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

EN_TETE = b'TRSV1\n'
TAILLE_SEL = 16
TAILLE_BLOC = 4 * 1024 * 1024  # octets de clair par bloc
TAILLE_ETIQUETTE = 16  # étiquette d'authentification AES-GCM
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1

NOM_ARCHIVE = re.compile(r'territoria-(\d{8})-(\d{6})\.tsv1')
MANIFESTE = 'MANIFESTE.json'
FICHIER_MOT_DE_PASSE = 'sauvegarde.motdepasse'
RACINES = ('territoria', 'n8n', 'secrets')
BASES = ('territoria/territoire.sqlite3', 'n8n/database.sqlite')
FICHIERS_N8N = ('database.sqlite', 'config')
EXCLUS_SECRETS = (FICHIER_MOT_DE_PASSE, 'provision')
EXCLUS_TERRITORIA = ('audio',)  # enregistrements des séances, effacés après la relecture
EXCLUS = re.compile(r'.*(-wal|-shm|-journal|\.log)', re.S)
TAILLE_MAX_MANIFESTE = 64 * 1024 * 1024
JOURS_CONSERVES, SEMAINES_CONSERVEES = 7, 4


class ArchiveIllisible(ValueError):
    def __init__(self):
        super().__init__('Archive illisible : mot de passe incorrect ou fichier altéré.')


class ArchiveIncomplete(ValueError):
    def __init__(self):
        super().__init__('Archive incomplète.')


class ArchiveNonConforme(ValueError):
    def __init__(self):
        super().__init__('Archive non conforme : contenu inattendu ou différent du manifeste.')


class CibleNonVide(ValueError):
    def __init__(self):
        super().__init__("Restauration refusée : le dossier cible n'est pas vide.")


class SourceInvalide(ValueError):
    def __init__(self):
        super().__init__('Dossier de données invalide : territoria/ est introuvable.')


class RestaurationInterrompue(RuntimeError):
    """Échec pendant la mise en place, puis échec du retour arrière : rien n'a été supprimé, les données précédentes
    sont dans `dossier` (le dossier temporaire de la restauration, conservé)."""

    def __init__(self, dossier: Path):
        super().__init__(f'Restauration interrompue : les données précédentes sont conservées dans {dossier}.')
        self.dossier = dossier


# --- Chiffrement par blocs -------------------------------------------------------------------------------------------

def _aead(mot_de_passe: str, sel: bytes) -> AESGCM:
    cle = Scrypt(salt=sel, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(mot_de_passe.encode('utf-8'))
    return AESGCM(cle)


def _nonce(index: int) -> bytes:
    return bytes(4) + index.to_bytes(8, 'big')


class _EcritureChiffree:
    """Flux en écriture qui chiffre ce qu'on y écrit ; `terminer()` écrit le bloc final (F)."""

    def __init__(self, fichier, mot_de_passe: str):
        sel = os.urandom(TAILLE_SEL)
        self._fichier, self._aead, self._aad = fichier, _aead(mot_de_passe, sel), EN_TETE + sel
        self._tampon, self._index = bytearray(), 0
        fichier.write(EN_TETE + sel)

    def write(self, donnees) -> int:
        self._tampon += donnees
        while len(self._tampon) > TAILLE_BLOC:  # un bloc plein n'est écrit qu'une fois la suite connue : le dernier sera F
            self._bloc(bytes(self._tampon[:TAILLE_BLOC]), b'C')
            del self._tampon[:TAILLE_BLOC]
        return len(donnees)

    def terminer(self) -> None:
        self._bloc(bytes(self._tampon), b'F')
        self._tampon.clear()

    def _bloc(self, clair: bytes, drapeau: bytes) -> None:
        chiffre = self._aead.encrypt(_nonce(self._index), clair, self._aad + drapeau)
        self._fichier.write(len(chiffre).to_bytes(4, 'big') + chiffre)
        self._index += 1


class _LectureChiffree:
    """Flux en lecture qui rend le clair d'une archive, chaque bloc étant authentifié avant d'être rendu."""

    def __init__(self, fichier, mot_de_passe: str):
        debut = fichier.read(len(EN_TETE) + TAILLE_SEL)
        if not EN_TETE.startswith(debut[:len(EN_TETE)]):
            raise ArchiveIllisible()
        if len(debut) < len(EN_TETE) + TAILLE_SEL:
            raise ArchiveIncomplete()
        self._fichier, self._aad = fichier, debut
        self._aead = _aead(mot_de_passe, debut[len(EN_TETE):])
        self._index, self._fin = 0, False
        self._tampon, self._position = b'', 0

    def read(self, taille: int | None = -1) -> bytes:
        taille = -1 if taille is None else taille
        morceaux = []
        while taille != 0:
            if self._position >= len(self._tampon):
                if self._fin:
                    break
                self._tampon, self._position = self._bloc_suivant(), 0
                continue
            fin = len(self._tampon) if taille < 0 else min(len(self._tampon), self._position + taille)
            morceaux.append(self._tampon[self._position:fin])
            if taille > 0:
                taille -= fin - self._position
            self._position = fin
        return b''.join(morceaux)

    def terminer(self) -> None:
        """Lit et authentifie tout ce qui reste, jusqu'au bloc final : une archive tronquée échoue ici."""
        while self.read(TAILLE_BLOC):
            pass

    def _bloc_suivant(self) -> bytes:
        longueur = self._fichier.read(4)
        if len(longueur) < 4:
            raise ArchiveIncomplete()  # fin du fichier sans bloc F
        longueur = int.from_bytes(longueur, 'big')
        if not TAILLE_ETIQUETTE <= longueur <= TAILLE_BLOC + TAILLE_ETIQUETTE:
            raise ArchiveIllisible()
        chiffre = self._fichier.read(longueur)
        if len(chiffre) < longueur:
            raise ArchiveIncomplete()
        nonce = _nonce(self._index)
        self._index += 1
        for drapeau in (b'C', b'F'):
            try:
                clair = self._aead.decrypt(nonce, chiffre, self._aad + drapeau)
            except InvalidTag:
                continue
            if drapeau == b'F':
                self._fin = True
                if self._fichier.read(1):
                    raise ArchiveIllisible()  # données ajoutées après le bloc final
            return clair
        raise ArchiveIllisible()


# --- Création ----------------------------------------------------------------------------------------------------------

def _root() -> bool:
    return hasattr(os, 'geteuid') and os.geteuid() == 0


def _sauvegarde_sqlite(adresse: str, copie: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        copie.unlink()
    source = sqlite3.connect(adresse, uri=True, timeout=60)
    try:
        destination = sqlite3.connect(copie)
        try:
            source.backup(destination)  # une seule transaction de lecture : copie cohérente
        finally:
            destination.close()
    finally:
        source.close()


def _copier_base(chemin: Path, copie: Path) -> None:
    """Copie cohérente d'une base par l'API de sauvegarde de SQLite, écritures encore dans le WAL comprises.

    La source est ouverte en lecture seule : elle peut être montée en lecture seule pendant que son service écrit."""
    adresse = Path(chemin).resolve().as_uri()
    journaux = (Path(f'{chemin}-wal'), Path(f'{chemin}-journal'))
    try:
        _sauvegarde_sqlite(f'{adresse}?mode=ro', copie)
        return
    except sqlite3.OperationalError:
        # Base en mode WAL sur un support en lecture seule, service arrêté : sans fichier -wal, personne n'écrit, mais
        # SQLite ne peut pas créer son index partagé. La base se lit alors comme un fichier immuable. Jamais si un
        # journal existe : ses pages seraient ignorées (transaction à moitié appliquée).
        if any(os.path.lexists(journal) for journal in journaux):
            raise
    _sauvegarde_sqlite(f'{adresse}?mode=ro&immutable=1', copie)
    if any(os.path.lexists(journal) for journal in journaux):  # le service a démarré pendant la copie : on la refait
        _sauvegarde_sqlite(f'{adresse}?mode=ro', copie)


class _LectureEmpreinte:
    """Lit au plus `taille` octets d'un fichier en calculant leur SHA-256 (taille figée au moment de l'ouverture)."""

    def __init__(self, fichier, taille: int):
        self._fichier, self._reste, self.empreinte = fichier, taille, hashlib.sha256()

    def read(self, taille: int | None = -1) -> bytes:
        if taille is None or taille < 0 or taille > self._reste:
            taille = self._reste
        donnees = self._fichier.read(taille)
        self._reste -= len(donnees)
        self.empreinte.update(donnees)
        return donnees


def _entete(nom: str, st: os.stat_result, taille: int = 0, dossier: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(nom)
    info.type = tarfile.DIRTYPE if dossier else tarfile.REGTYPE
    info.mode = stat.S_IMODE(st.st_mode) & 0o777
    info.uid, info.gid, info.uname, info.gname = st.st_uid, st.st_gid, '', ''
    info.mtime = int(st.st_mtime)
    info.size = taille
    return info


def _ajouter_fichier(tar: tarfile.TarFile, nom: str, chemin: Path, empreintes: dict, meta: os.stat_result | None = None) -> None:
    descripteur = os.open(chemin, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descripteur, 'rb') as fichier:
        st = os.fstat(fichier.fileno())
        if not stat.S_ISREG(st.st_mode):
            return  # remplacé entre-temps par autre chose qu'un fichier
        lecture = _LectureEmpreinte(fichier, st.st_size)
        tar.addfile(_entete(nom, meta or st, taille=st.st_size), lecture)
        empreintes[nom] = lecture.empreinte.hexdigest()


def _lstat(chemin: Path) -> os.stat_result | None:
    """stat sans suivre les liens ; None si l'élément a disparu (un document supprimé pendant la sauvegarde)."""
    try:
        return os.lstat(chemin)
    except FileNotFoundError:
        return None


def _erreur_de_parcours(erreur: OSError) -> None:
    if not isinstance(erreur, FileNotFoundError):  # un dossier illisible n'est jamais ignoré en silence
        raise erreur


def _elements(source: Path):
    """(nom dans l'archive, chemin, stat) de ce qui entre dans l'archive ; chaque dossier précède son contenu.

    Seuls les dossiers et les fichiers ordinaires sont retenus : les liens symboliques ne sont pas suivis."""
    for racine in RACINES:
        st = _lstat(source / racine)
        if st is None or not stat.S_ISDIR(st.st_mode):
            continue
        if racine == 'n8n':
            yield racine, source / racine, st
            for nom in FICHIERS_N8N:
                st = _lstat(source / racine / nom)
                if st is not None and stat.S_ISREG(st.st_mode):
                    yield f'{racine}/{nom}', source / racine / nom, st
            continue
        for parent, dossiers, fichiers in os.walk(source / racine, onerror=_erreur_de_parcours):
            parent = Path(parent)
            relatif = parent.relative_to(source).as_posix()
            st = _lstat(parent)
            if st is None or not stat.S_ISDIR(st.st_mode):
                dossiers[:] = []
                continue
            yield relatif, parent, st
            exclus = EXCLUS_SECRETS if relatif == 'secrets' else EXCLUS_TERRITORIA if relatif == 'territoria' else ()
            retenus = []
            for nom in sorted(dossiers):
                st = _lstat(parent / nom)
                if nom not in exclus and not EXCLUS.fullmatch(nom) and st is not None and stat.S_ISDIR(st.st_mode):
                    retenus.append(nom)
            dossiers[:] = retenus
            for nom in sorted(fichiers):
                st = None if nom in exclus or EXCLUS.fullmatch(nom) else _lstat(parent / nom)
                if st is not None and stat.S_ISREG(st.st_mode):
                    yield f'{relatif}/{nom}', parent / nom, st


def _ecrire_tar(source: Path, flux: _EcritureChiffree) -> None:
    empreintes = {}
    with tempfile.TemporaryDirectory(prefix='territoria-sauvegarde-') as copies, \
            tarfile.open(fileobj=flux, mode='w|', format=tarfile.PAX_FORMAT) as tar:
        for nom, chemin, st in _elements(source):
            if stat.S_ISDIR(st.st_mode):
                tar.addfile(_entete(nom, st, dossier=True))
            elif nom in BASES:
                copie = Path(copies) / 'copie.sqlite'
                _copier_base(chemin, copie)
                _ajouter_fichier(tar, nom, copie, empreintes, meta=st)
                copie.unlink()
            else:
                with contextlib.suppress(FileNotFoundError):  # supprimé depuis le parcours : il ne fait plus partie des données
                    _ajouter_fichier(tar, nom, chemin, empreintes)
        corps = json.dumps(empreintes, ensure_ascii=False, indent=2, sort_keys=True).encode('utf-8') + b'\n'
        info = tarfile.TarInfo(MANIFESTE)
        info.size, info.mode, info.mtime = len(corps), 0o600, int(time.time())
        tar.addfile(info, io.BytesIO(corps))


def _synchroniser_dossier(dossier: Path) -> None:
    with contextlib.suppress(OSError):  # certains systèmes de fichiers (partages, disques externes) le refusent
        descripteur = os.open(dossier, os.O_RDONLY)
        try:
            os.fsync(descripteur)
        finally:
            os.close(descripteur)


def creer(source: Path, destination: Path, mot_de_passe: str, maintenant: datetime) -> Path:
    """Crée `destination/territoria-AAAAMMJJ-HHMMSS.tsv1` à partir de `source` (racine de territoria-donnees).

    L'archive est écrite sous un nom provisoire dans `destination`, en 0600, puis renommée : un échec ne laisse aucun
    fichier. En root, elle appartient au propriétaire de `destination`."""
    source, destination = Path(source), Path(destination)
    if not mot_de_passe:
        raise ValueError('Mot de passe vide.')
    if not (source / 'territoria').is_dir():
        raise SourceInvalide()
    if not destination.is_dir():
        raise FileNotFoundError('Dossier de destination introuvable.')
    finale = destination / f'territoria-{maintenant:%Y%m%d-%H%M%S}.tsv1'
    if os.path.lexists(finale):
        raise FileExistsError('Une archive porte déjà ce nom.')
    descripteur, provisoire = tempfile.mkstemp(dir=destination, prefix=f'.{finale.name}.', suffix='.tmp')
    try:
        with open(descripteur, 'wb') as fichier:
            os.fchmod(fichier.fileno(), 0o600)
            if _root():
                proprietaire = os.stat(destination)
                os.fchown(fichier.fileno(), proprietaire.st_uid, proprietaire.st_gid)
            flux = _EcritureChiffree(fichier, mot_de_passe)
            _ecrire_tar(source, flux)
            flux.terminer()
            fichier.flush()
            os.fsync(fichier.fileno())
        os.replace(provisoire, finale)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(provisoire)
        raise
    _synchroniser_dossier(destination)
    return finale


# --- Lecture : vérification et restauration ----------------------------------------------------------------------------

@dataclass
class _Element:
    parties: tuple[str, ...]
    dossier: bool
    mode: int
    uid: int
    gid: int
    mtime: int


@dataclass
class _Extraction:
    empreintes: dict
    elements: list
    conforme: bool


def _parties(nom: str) -> tuple[str, ...]:
    """Découpe et contrôle le nom d'un élément : chemin relatif, sans `.` ni `..`, sous une racine attendue."""
    parties = tuple(nom.split('/'))
    if '\x00' in nom or any(partie in ('', '.', '..') for partie in parties):
        raise ArchiveNonConforme()  # chemin absolu, remontée, composant vide
    if parties == (MANIFESTE,):
        return parties
    if parties[0] not in RACINES or EXCLUS.fullmatch(parties[-1]) or parties == ('secrets', FICHIER_MOT_DE_PASSE):
        raise ArchiveNonConforme()
    return parties


def _manifeste(donnees: bytes) -> dict | None:
    try:
        manifeste = json.loads(donnees.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(manifeste, dict) or not all(isinstance(c, str) and isinstance(v, str) for c, v in manifeste.items()):
        return None
    return manifeste


def _base_saine(chemin: Path) -> bool:
    try:  # immutable : ni verrou ni fichier -wal/-shm créé à côté de la copie
        connexion = sqlite3.connect(f'{chemin.resolve().as_uri()}?mode=ro&immutable=1', uri=True)
        try:
            return connexion.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        finally:
            connexion.close()
    except sqlite3.DatabaseError:
        return False


def _extraire(archive: Path, mot_de_passe: str, racine: Path) -> _Extraction:
    """Déchiffre `archive` dans `racine` (dossier vide et privé), puis la compare à son manifeste.

    Seuls des dossiers et des fichiers ordinaires sont créés, tous sous `racine` ; le manifeste reste en mémoire."""
    empreintes, elements, vus, manifeste = {}, [], set(), None
    with open(archive, 'rb') as fichier:
        flux = _LectureChiffree(fichier, mot_de_passe)
        try:
            with tarfile.open(fileobj=flux, mode='r|') as tar:
                for membre in tar:
                    parties = _parties(membre.name)
                    nom = '/'.join(parties)
                    if nom in vus:
                        raise ArchiveNonConforme()
                    vus.add(nom)
                    if nom == MANIFESTE:
                        if membre.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or membre.size > TAILLE_MAX_MANIFESTE:
                            raise ArchiveNonConforme()
                        manifeste = _manifeste(tar.extractfile(membre).read())
                        continue
                    if len(parties) == 1 and membre.type != tarfile.DIRTYPE:
                        raise ArchiveNonConforme()  # territoria, n8n et secrets ne peuvent être que des dossiers
                    chemin = racine.joinpath(*parties)
                    try:
                        if membre.type == tarfile.DIRTYPE:
                            os.makedirs(chemin, mode=0o700, exist_ok=True)
                            if chemin.is_symlink() or not chemin.is_dir():
                                raise ArchiveNonConforme()
                        elif membre.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                            os.makedirs(chemin.parent, mode=0o700, exist_ok=True)
                            empreintes[nom] = _ecrire(chemin, tar.extractfile(membre))
                        else:
                            raise ArchiveNonConforme()  # lien, périphérique, tube, fichier creux…
                    except (FileExistsError, NotADirectoryError):
                        raise ArchiveNonConforme() from None
                    dossier = membre.type == tarfile.DIRTYPE
                    mode = (membre.mode & 0o755) | (0o700 if dossier else 0o600)  # ni setuid, ni setgid, ni écriture des autres
                    elements.append(_Element(parties, dossier, mode, membre.uid, membre.gid, int(membre.mtime)))
        except tarfile.TarError:
            raise ArchiveNonConforme() from None
        flux.terminer()
    conforme = manifeste == empreintes and all(_base_saine(racine / base) for base in BASES if base in empreintes)
    return _Extraction(empreintes, elements, conforme)


def _ecrire(chemin: Path, source) -> str:
    empreinte = hashlib.sha256()
    descripteur = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descripteur, 'wb') as sortie:
        while bloc := source.read(1024 * 1024):
            empreinte.update(bloc)
            sortie.write(bloc)
    return empreinte.hexdigest()


def verifier(archive: Path, mot_de_passe: str) -> dict:
    """Restaure l'archive dans un dossier temporaire privé, la compare à son manifeste et contrôle chaque base.

    Lève `ValueError` si elle ne peut pas être déchiffrée en entier ; renvoie `{'fichiers': int, 'conforme': bool}`."""
    with tempfile.TemporaryDirectory(prefix='territoria-verification-') as dossier:
        extraction = _extraire(Path(archive), mot_de_passe, Path(dossier))
    return {'fichiers': len(extraction.empreintes), 'conforme': extraction.conforme}


def _supprimer(chemin: Path) -> None:
    def deverrouiller(fonction, cible, _erreur):
        with contextlib.suppress(OSError):
            os.chmod(os.path.dirname(cible), 0o700)
            os.chmod(cible, 0o700)
            fonction(cible)

    if os.path.lexists(chemin):
        shutil.rmtree(chemin, onexc=deverrouiller)


def _dossier_reel(chemin: Path) -> bool:
    return chemin.is_dir() and not chemin.is_symlink()


def _completer(nouveau: Path, ancien: Path, root: bool) -> None:
    """Copie dans `nouveau` ce qu'`ancien` contient et que l'archive n'a pas (dont sauvegarde.motdepasse), avec droits,
    dates et (en root) propriétaires ; un élément présent des deux côtés garde la version de l'archive. `ancien` n'est
    pas modifié : en cas d'échec, il suffit d'abandonner `nouveau`."""
    for element in sorted(ancien.iterdir()):
        destination = nouveau / element.name
        if os.path.lexists(destination):
            if _dossier_reel(destination) and _dossier_reel(element):
                _completer(destination, element, root)
            continue
        if _dossier_reel(element):
            shutil.copytree(element, destination, symlinks=True)
            copies = [destination, *destination.rglob('*')]
        else:
            shutil.copy2(element, destination, follow_symlinks=False)
            copies = [destination]
        if root:
            for copie in copies:
                origine = os.lstat(element / copie.relative_to(destination))
                os.chown(copie, origine.st_uid, origine.st_gid, follow_symlinks=False)


def _deplacer(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _echanger(temporaire: Path, cible: Path) -> None:
    """Remplace territoria/, n8n/ et secrets/ de la cible par ceux, complets, de `temporaire` ; les anciens vont dans
    `temporaire/.ancien-*`. Si un déplacement échoue, ceux déjà faits sont défaits (l'erreur est ensuite relevée) ; si le
    retour arrière échoue aussi, `RestaurationInterrompue` signale que les données précédentes sont dans `temporaire`."""
    faits = []  # (emplacement d'origine, emplacement actuel)
    try:
        for racine in RACINES:
            nouveau, existant = temporaire / racine, cible / racine
            if not os.path.lexists(nouveau):
                continue
            if os.path.lexists(existant):
                ancien = temporaire / f'.ancien-{racine}'
                _deplacer(existant, ancien)
                faits.append((existant, ancien))
            _deplacer(nouveau, existant)
            faits.append((nouveau, existant))
    except BaseException as erreur:
        defait = True
        for origine, actuel in reversed(faits):
            try:
                _deplacer(actuel, origine)
            except OSError:
                defait = False  # on continue : chaque élément remis en place compte
        if not defait:
            raise RestaurationInterrompue(temporaire) from erreur
        raise


def _appliquer(elements: list, racine: Path) -> None:
    """Droits, dates et (en root) propriétaires enregistrés ; le contenu d'un dossier passe avant le dossier."""
    root = _root()
    for element in sorted(elements, key=lambda e: len(e.parties), reverse=True):
        chemin = racine.joinpath(*element.parties)
        if root:
            os.chown(chemin, element.uid, element.gid, follow_symlinks=False)
        os.chmod(chemin, element.mode)
        os.utime(chemin, (element.mtime, element.mtime))


def restaurer(archive: Path, cible: Path, mot_de_passe: str, forcer: bool = False) -> None:
    """Restaure l'archive dans `cible` (racine de territoria-donnees), services arrêtés.

    Refuse une cible non vide sans `forcer`. Tout est préparé dans un dossier temporaire privé créé dans la cible :
    archive entièrement déchiffrée et vérifiée, secrets/ complété des fichiers de la cible absents de l'archive (dont
    sauvegarde.motdepasse), droits, dates et propriétaires appliqués. Ensuite seulement, territoria/, n8n/ et secrets/
    sont échangés par renommage ; un échec défait l'échange, et les anciennes versions ne sont supprimées qu'après un
    succès complet. En cas d'échec, territoria/, n8n/ et secrets/ de la cible restent donc identiques, et le reste de la
    cible (echec-*, caddy/…) n'est jamais touché. Si même le retour arrière échoue, `RestaurationInterrompue` est levée
    et le dossier temporaire, qui contient les données précédentes, est conservé."""
    archive, cible = Path(archive), Path(cible)
    creee = False
    if os.path.lexists(cible):
        if not cible.is_dir():
            raise NotADirectoryError('La cible n\'est pas un dossier.')
        if not forcer and any(cible.iterdir()):
            raise CibleNonVide()
    else:
        cible.mkdir(mode=0o700)
        creee = True
    temporaire, reussie, conserver = None, False, False
    try:
        temporaire = Path(tempfile.mkdtemp(dir=cible, prefix='.restauration-'))
        extraction = _extraire(archive, mot_de_passe, temporaire)
        if not extraction.conforme:
            raise ArchiveNonConforme()
        if _dossier_reel(temporaire / 'secrets') and _dossier_reel(cible / 'secrets'):
            _completer(temporaire / 'secrets', cible / 'secrets', _root())
        _appliquer(extraction.elements, temporaire)
        _echanger(temporaire, cible)
        reussie = True
    except RestaurationInterrompue:
        conserver = True
        raise
    finally:
        if temporaire is not None and not conserver:
            _supprimer(temporaire)  # après un succès, il ne contient plus que les anciennes versions
        if creee and not reussie and not conserver:
            with contextlib.suppress(OSError):
                cible.rmdir()


# --- Élagage -----------------------------------------------------------------------------------------------------------

def elaguer(dossier: Path, aujourd_hui: date) -> list[Path]:
    """Garde la plus récente archive de chacun des 7 derniers jours qui en ont une, plus la plus récente de chacune des
    4 dernières semaines ISO qui en ont une et ne sont pas déjà couvertes ; supprime les autres et renvoie leur liste.

    Ne touche qu'aux fichiers `territoria-AAAAMMJJ-HHMMSS.tsv1` de `dossier` (sans descendre dans les sous-dossiers).
    Une archive datée après `aujourd_hui` (horloge décalée) n'est ni comptée ni supprimée."""
    archives = []
    with os.scandir(dossier) as entrees:
        for entree in entrees:
            trouve = NOM_ARCHIVE.fullmatch(entree.name)
            if not trouve or not entree.is_file(follow_symlinks=False):
                continue
            try:
                moment = datetime.strptime(trouve.group(1) + trouve.group(2), '%Y%m%d%H%M%S')
            except ValueError:
                continue
            if moment.date() <= aujourd_hui:
                archives.append((moment, Path(entree.path)))
    archives.sort(reverse=True)
    gardees, jours = set(), []
    for moment, chemin in archives:
        if len(jours) == JOURS_CONSERVES:
            break
        if moment.date() not in jours:
            jours.append(moment.date())
            gardees.add(chemin)
    semaines = {tuple(jour.isocalendar())[:2] for jour in jours}
    hebdomadaires = 0
    for moment, chemin in archives:
        if hebdomadaires == SEMAINES_CONSERVEES:
            break
        semaine = tuple(moment.date().isocalendar())[:2]
        if semaine not in semaines:
            semaines.add(semaine)
            gardees.add(chemin)
            hebdomadaires += 1
    supprimees = sorted(chemin for _, chemin in archives if chemin not in gardees)
    for chemin in supprimees:
        chemin.unlink(missing_ok=True)
    return supprimees
