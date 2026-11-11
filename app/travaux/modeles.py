# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Modèles de la transcription : manifeste figé dans le dépôt, téléchargement vérifié, contrôle de présence.

Le téléchargement n'a lieu qu'à l'installation ou à la mise à jour, par le service ponctuel `travaux-modeles` (le conteneur
de travaux, lui, n'a pas de réseau). Chaque fichier est écrit sous `<chemin>.partiel`, contrôlé (taille et SHA-256 du
manifeste), puis renommé : un fichier présent est toujours un fichier vérifié. Une coupure laisse le fichier partiel, que
le passage suivant complète (requête `Range`) ; seul un fichier à l'empreinte fausse est effacé.

Ligne de commande (contrat des étapes d'installation, une seule ligne sur la sortie standard) :
    python -m travaux.modeles telecharger --manifeste M --dossier D --modeles large-v3-turbo,segmentation,empreintes
    → « ETAPE modeles-audio : OK » ou « ETAPE modeles-audio : ECHEC <reseau|disque|empreinte|manifeste> »
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

LICENCES = frozenset({'MIT', 'Apache-2.0', 'BSD-3-Clause'})
BLOC = 1024 * 1024


def lire_manifeste(chemin: Path) -> dict:
    manifeste = json.loads(Path(chemin).read_text(encoding='utf-8'))
    if not isinstance(manifeste, dict) or manifeste.get('version') != 1 or not isinstance(manifeste.get('modeles'), dict):
        raise ValueError('manifeste')
    for nom, modele in manifeste['modeles'].items():
        if not isinstance(modele, dict) or modele.get('licence') not in LICENCES or not isinstance(modele.get('fichiers'), list):
            raise ValueError(f'manifeste : licence ou fichiers de « {nom} »')
        for fichier in modele['fichiers']:
            chemin_relatif = PurePosixPath(str(fichier.get('chemin', '')))
            if chemin_relatif.is_absolute() or '..' in chemin_relatif.parts or not chemin_relatif.parts:
                raise ValueError(f'manifeste : chemin de « {nom} »')
    return manifeste


def _fichiers(manifeste: dict, noms: list[str]) -> list[dict]:
    try:
        return [f for nom in noms for f in manifeste['modeles'][nom]['fichiers']]
    except KeyError as exc:
        raise ValueError('manifeste') from exc


def _empreinte(chemin: Path) -> str:
    calcul = hashlib.sha256()
    with open(chemin, 'rb') as flux:
        for bloc in iter(lambda: flux.read(BLOC), b''):
            calcul.update(bloc)
    return calcul.hexdigest()


def _intact(chemin: Path, fichier: dict) -> bool:
    return chemin.is_file() and chemin.stat().st_size == fichier['taille'] and _empreinte(chemin) == fichier['sha256']


def verifier(manifeste: dict, dossier: Path, noms: list[str]) -> bool:
    try:
        return all(_intact(Path(dossier) / f['chemin'], f) for f in _fichiers(manifeste, noms))
    except (OSError, ValueError, TypeError):
        return False


def telecharger(manifeste: dict, dossier: Path, noms: list[str], ouvrir=urllib.request.urlopen) -> list[str]:
    """Télécharge les fichiers absents ou altérés ; rend leurs chemins. `ValueError('empreinte')` ou `('manifeste')`."""
    faits = []
    for fichier in _fichiers(manifeste, noms):
        if not isinstance(fichier.get('sha256'), str) or not isinstance(fichier.get('taille'), int):
            raise ValueError('manifeste')
        cible = Path(dossier) / fichier['chemin']
        if _intact(cible, fichier):
            continue
        cible.parent.mkdir(parents=True, exist_ok=True)
        partiel = cible.with_name(cible.name + '.partiel')
        deja = partiel.stat().st_size if partiel.is_file() else 0
        if deja >= fichier['taille'] and not _intact(partiel, fichier):
            partiel.unlink()
            deja = 0
        if deja < fichier['taille']:
            requete = urllib.request.Request(fichier['url'], headers={'Range': f'bytes={deja}-'} if deja else {})
            with ouvrir(requete, timeout=60) as reponse:
                reprise = deja > 0 and getattr(reponse, 'status', None) == 206  # sinon le serveur renvoie tout le fichier
                with open(partiel, 'ab' if reprise else 'wb') as flux:
                    for bloc in iter(lambda: reponse.read(BLOC), b''):
                        flux.write(bloc)
        if not _intact(partiel, fichier):
            partiel.unlink(missing_ok=True)
            raise ValueError('empreinte')
        os.replace(partiel, cible)
        faits.append(fichier['chemin'])
    return faits


def main(argv: list[str]) -> int:
    parseur = argparse.ArgumentParser(prog='travaux.modeles')
    parseur.add_argument('action', choices=['telecharger'])
    parseur.add_argument('--manifeste', required=True)
    parseur.add_argument('--dossier', required=True)
    parseur.add_argument('--modeles', required=True)
    args = parseur.parse_args(argv)
    try:
        telecharger(lire_manifeste(Path(args.manifeste)), Path(args.dossier), [n for n in args.modeles.split(',') if n])
    except ValueError as exc:
        code = 'empreinte' if str(exc) == 'empreinte' else 'manifeste'
    except urllib.error.URLError:
        code = 'reseau'
    except OSError as exc:  # droits ou place sur le disque, à ne pas confondre avec un accès à Internet coupé
        code = 'disque' if isinstance(exc, PermissionError) or exc.errno in (errno.ENOSPC, errno.EROFS, errno.EDQUOT) else 'reseau'
    else:
        print('ETAPE modeles-audio : OK', flush=True)
        return 0
    print(f'ETAPE modeles-audio : ECHEC {code}', flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
