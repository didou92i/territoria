# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Démonstration légère sur un hébergement sans conteneurs (cPanel, « Setup Python App ») : Passenger charge ce fichier.

Mêmes étapes de démarrage que app/serve.py, sans waitress : Passenger sert `application`. Les réglages viennent des
variables d'environnement de l'application cPanel (docs/demonstration-legere.md), dont TERRITORIA_ORCHESTRATION=interne."""
import os
import sys

RACINE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(RACINE, 'app'), RACINE]
os.umask(0o077)

import config  # noqa: E402
import llm_backend  # noqa: E402

config.verifier_demarrage(config.REGLAGES)
llm_backend.read(config.REGLAGES.donnees)
llm_backend.oublier_fournisseur_retire(config.REGLAGES.donnees)

import server  # noqa: E402
import web  # noqa: E402
from territoria import semantique  # noqa: E402

server.init()
semantique.activer()
semantique.lancer(server.DB, server.DATA)  # vecteurs des extraits qui n'en ont pas encore

application = web.application
