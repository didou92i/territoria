#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Restaure une sauvegarde dans territoria-donnees, services arrêtés, par un conteneur ponctuel (root, sans réseau) :
#   install/restaurer.sh --archive <fichier .tsv1 | dossier> [--forcer] [--auto] [--simulation]
# Un dossier désigne sa sauvegarde la plus récente. Un territoria-donnees non vide exige --forcer (territoria/ et n8n/
# remplacés, fichiers de secrets/ fusionnés ; faites d'abord une sauvegarde). Le mot de passe est saisi masqué, ou lu
# dans secrets/sauvegarde.motdepasse avec --auto. Les services restent arrêtés : relancez install/demarrer.sh.
# Dernière ligne : « ETAPE restaurer : OK|ECHEC <code> ».
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

ARCHIVE=""
FORCER=""
MODE=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive)
            if [ "$#" -lt 2 ]; then refuser_arguments restaurer; fi
            ARCHIVE="$2"
            shift 2
            ;;
        --forcer) FORCER=forcer; shift ;;
        --auto) MODE=auto; shift ;;
        --simulation) activer_simulation; shift ;;
        -h | --help) echo "Usage : install/restaurer.sh --archive <fichier|dossier> [--forcer] [--auto] [--simulation]"; exit 0 ;;
        *) refuser_arguments restaurer ;;
    esac
done
if [ -z "$ARCHIVE" ]; then refuser_arguments restaurer; fi

debut_etape restaurer
sauvegarde_restaurer "$ARCHIVE" "$FORCER" "$MODE"
if [ "$SIMULATION" != 1 ]; then info "Restauration terminée : relancez Territoria avec install/demarrer.sh."; fi
