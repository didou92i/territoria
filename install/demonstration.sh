#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Serveur de démonstration de TerritorIA, sur un VPS Linux seulement (docs/demonstration.md) : OpenRouter permis, accès
# réservé aux comptes, HTTPS par Caddy (conteneurs/compose.demonstration.yaml). Jamais sur un poste de collectivité.
#   install/demonstration.sh --domaine <nom> [--simulation]
#   install/demonstration.sh --premier-administrateur <identifiant> --nom <nom> [--simulation]
# --domaine écrit TERRITORIA_DEMONSTRATION=1 et TERRITORIA_DOMAINE dans secrets/compose.env ; --premier-administrateur
# crée le premier compte (mot de passe saisi masqué dans le conteneur), une fois les services démarrés.
# Dernière ligne : « ETAPE demonstration : OK|ECHEC <code> ».
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

USAGE="Usage : install/demonstration.sh --domaine <nom> | --premier-administrateur <identifiant> --nom <nom> [--simulation]"
DOMAINE=""
IDENTIFIANT=""
NOM=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --domaine) [ "$#" -ge 2 ] || refuser_arguments demonstration; DOMAINE="$2"; shift 2 ;;
        --premier-administrateur) [ "$#" -ge 2 ] || refuser_arguments demonstration; IDENTIFIANT="$2"; shift 2 ;;
        --nom) [ "$#" -ge 2 ] || refuser_arguments demonstration; NOM="$2"; shift 2 ;;
        --simulation) activer_simulation; shift ;;
        -h | --help) echo "$USAGE"; exit 0 ;;
        *) refuser_arguments demonstration ;;
    esac
done
# Une seule action à la fois ; le premier administrateur a toujours un nom.
if [ -n "$DOMAINE" ] && [ -n "$IDENTIFIANT" ]; then refuser_arguments demonstration; fi
if [ -z "$DOMAINE" ] && [ -z "$IDENTIFIANT" ]; then refuser_arguments demonstration; fi
if [ -n "$IDENTIFIANT" ] && [ -z "$NOM" ]; then refuser_arguments demonstration; fi

debut_etape demonstration
if [ "$(systeme)" != linux ]; then
    echouer systeme_non_pris_en_charge "Le serveur de démonstration s'installe sur un VPS Linux (docs/demonstration.md)."
fi
verifier_dossier_donnees
if [ -n "$DOMAINE" ]; then
    # Nom en minuscules, au moins un point, étiquettes sans tiret au bord (pas de chemin, pas d'adresse IP nue).
    MOTIF='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
    if [ "${#DOMAINE}" -gt 253 ] || ! [[ "$DOMAINE" =~ $MOTIF ]]; then
        echouer domaine_invalide "Nom de domaine invalide : en minuscules, avec au moins un point (par exemple demo.exemple.fr)."
    fi
    exiger_secrets
    ecrire_variable "$S/compose.env" TERRITORIA_DEMONSTRATION 1
    ecrire_variable "$S/compose.env" TERRITORIA_DOMAINE "$DOMAINE"
    info "Démonstration activée pour https://$DOMAINE : lancez install/demarrer.sh, puis créez le premier administrateur."
else
    exiger_terminal
    exiger_moteur
    exiger_secrets
    # Avec un terminal : outils lit le mot de passe en saisie masquée ; son code d'échec s'affiche au-dessus.
    phase premier_administrateur
    compose exec territoria python -m outils premier-administrateur "$IDENTIFIANT" --nom "$NOM"
fi
