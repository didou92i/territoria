#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Démarre Territoria : moteur de conteneurs (machine Podman ou Docker Desktop s'il est arrêté), serveur LM Studio
# (écoute limitée, voir docs/acces-lmstudio.md) puis les services n8n et territoria, jusqu'à leur bonne santé.
#   install/demarrer.sh [--ouvrir] [--simulation]
# --ouvrir ouvre ensuite http://127.0.0.1:8878 dans le navigateur (raccourci du bureau).
# Dernière ligne : « ETAPE demarrer : OK|ECHEC <code> ».
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

OUVRIR=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --ouvrir) OUVRIR=1; shift ;;
        --simulation) activer_simulation; shift ;;
        -h | --help) echo "Usage : install/demarrer.sh [--ouvrir] [--simulation]"; exit 0 ;;
        *) refuser_arguments demarrer ;;
    esac
done

debut_etape demarrer
verifier_dossier_donnees
assurer_moteur_actif
demarrer_lmstudio
demarrer_services
if [ "$OUVRIR" = 1 ]; then
    case "$(systeme)" in
        macos) lancer_detache open "$ADRESSE_TERRITORIA" ;;
        linux) lancer_detache xdg-open "$ADRESSE_TERRITORIA" || info "Ouvrez $ADRESSE_TERRITORIA dans le navigateur." ;;
    esac
fi
info "Territoria est prêt : $ADRESSE_TERRITORIA"
