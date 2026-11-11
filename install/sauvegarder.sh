#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Sauvegarde chiffrée de territoria-donnees (monté en lecture seule, services en marche) dans un dossier existant :
#   install/sauvegarder.sh --destination <dossier> [--auto] [--simulation]
# Sans --auto, le mot de passe est saisi deux fois, masqué, dans le terminal. Avec --auto (tâche planifiée), il est lu
# dans territoria-donnees/secrets/sauvegarde.motdepasse, puis les archives sont élaguées (7 quotidiennes et
# 4 hebdomadaires gardées). Dernière ligne : « ETAPE sauvegarder : OK|ECHEC <code> ».
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

DESTINATION=""
MODE=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --destination)
            if [ "$#" -lt 2 ]; then refuser_arguments sauvegarder; fi
            DESTINATION="$2"
            shift 2
            ;;
        --auto) MODE=auto; shift ;;
        --simulation) activer_simulation; shift ;;
        -h | --help) echo "Usage : install/sauvegarder.sh --destination <dossier> [--auto] [--simulation]"; exit 0 ;;
        *) refuser_arguments sauvegarder ;;
    esac
done
if [ -z "$DESTINATION" ]; then refuser_arguments sauvegarder; fi

debut_etape sauvegarder
sauvegarde_creer "$DESTINATION" "$MODE"
if [ "$MODE" = auto ]; then
    phase elagage_impossible
    sauvegarde_elaguer "$DESTINATION"
fi
