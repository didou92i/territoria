# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Démarrage de Territoria : réglages vérifiés, base initialisée, serveur waitress sans journal des accès."""
from __future__ import annotations

import logging
import os


def main() -> None:
    try:
        # Les réglages sont lus à l'import : une valeur invalide doit arrêter le démarrage par un message clair.
        import config
        import llm_backend
        config.verifier_demarrage(config.REGLAGES)
        llm_backend.read(config.REGLAGES.donnees)
        llm_backend.oublier_fournisseur_retire(config.REGLAGES.donnees)  # changement de fournisseur par l'installation
        import server
        import web
    except ValueError as erreur:
        raise SystemExit(f'Territoria ne peut pas démarrer : {erreur}') from None
    import waitress
    reglages = config.REGLAGES
    os.umask(0o077)
    server.init()
    from territoria import semantique
    semantique.activer()
    semantique.lancer(server.DB, server.DATA)  # vecteurs des extraits qui n'en ont pas encore
    from territoria import seances
    seances.activer()
    seances.lancer(server.DB, server.DATA)  # relevé des transcriptions et purge des envois abandonnés
    # waitress n'écrit aucune ligne par requête ; seuls ses avertissements de fonctionnement restent visibles.
    logging.getLogger('waitress').setLevel(logging.WARNING)
    print(f'Territoria : {reglages.origine}', flush=True)
    # waitress met tout le corps en tampon (jusqu'à 1 Gio par défaut) avant d'appeler l'application : on le borne
    # à la limite de celle-ci. Il refuse dès que Content-Length >= la borne, d'où + 1 pour garder 20 Mo exacts.
    waitress.serve(web.application, host=reglages.ecoute, port=reglages.port, threads=8,
                   max_request_body_size=server.MAX_UPLOAD + 1)


if __name__ == '__main__':
    main()
