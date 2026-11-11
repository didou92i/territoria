#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Arrête les services de Territoria (n8n, territoria) sans rien supprimer : les données restent dans
# territoria-donnees, les conteneurs sont seulement arrêtés. LM Studio, application de l'utilisateur, reste ouvert.
#   install/arreter.sh [--simulation]
# Dernière ligne : « ETAPE arreter : OK|ECHEC <code> ».
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --simulation) activer_simulation; shift ;;
        -h | --help) echo "Usage : install/arreter.sh [--simulation]"; exit 0 ;;
        *) refuser_arguments arreter ;;
    esac
done

debut_etape arreter
verifier_dossier_donnees
if [ "$SIMULATION" != 1 ] && [ ! -f "$S/compose.env" ]; then
    info "Territoria n'est pas installé dans $TERRITORIA_DONNEES_HOTE : rien à arrêter."
    exit 0
fi
exiger_moteur
phase arret_impossible
compose stop >&2
