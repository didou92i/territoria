#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Diagnostic de la machine, sans aucune modification : imprime uniquement des lignes « cle=valeur », dans cet ordre :
# systeme, architecture, memoire_go, gpu, disque_libre_go, port_8878 (libre|occupe), moteur (docker|podman|aucun),
# docker_desktop (oui|non), lms (present|absent), chiffrement_disque (actif|inactif|inconnu), palier_modele
# (petit|moyen|grand), transcription (en_marche|arrete|inconnu), modeles_audio (presents|absents) ; puis, si Docker Desktop est présent, la ligne d'avertissement sur sa licence.
# Chaque sonde est protégée : une commande absente donne « inconnu », jamais une erreur.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

# Minuscules, tout autre caractère que [a-z0-9] devient « _ » (valeur d'une seule chaîne, sans espace).
normaliser() {
    printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C tr -cs 'a-z0-9' '_' | sed -e 's/^_*//' -e 's/_*$//'
}

architecture() {
    case "$(uname -m 2>/dev/null || true)" in
        arm64 | aarch64) printf 'arm64' ;;
        x86_64 | amd64) printf 'x86_64' ;;
        '') printf 'inconnue' ;;
        *) normaliser "$(uname -m)" ;;
    esac
}

gpu() {
    local nom=""
    case "$(systeme)" in
        macos)
            if [ "$(architecture)" = arm64 ]; then
                nom="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"  # puce Apple (GPU intégré)
            else
                nom="$(system_profiler SPDisplaysDataType 2>/dev/null | sed -n 's/^ *Chipset Model: //p' | head -n 1 || true)"
            fi
            ;;
        linux)
            if command -v nvidia-smi >/dev/null 2>&1; then
                nom="$(avec_delai 10 nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || true)"
            fi
            if [ -z "$nom" ] && command -v lspci >/dev/null 2>&1; then
                nom="$(lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -n 1 | sed -e 's/^[^:]*: //' -e 's/ (rev [^)]*)$//' || true)"
            fi
            ;;
    esac
    nom="$(normaliser "$nom")"
    printf '%s' "${nom:-inconnu}"
}

# Espace libre là où territoria-donnees sera rangé (son premier dossier parent existant).
disque_libre_go() {
    local dossier="$TERRITORIA_DONNEES_HOTE" ko
    while [ ! -d "$dossier" ] && [ "$dossier" != / ]; do dossier="$(dirname "$dossier")"; done
    ko="$(df -Pk "$dossier" 2>/dev/null | awk 'NR == 2 { print $4 }' || true)"
    case "$ko" in
        '' | *[!0-9]*) printf 'inconnu' ;;
        *) printf '%s' $((ko / 1048576)) ;;
    esac
}

port_8878() {
    if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8878 -sTCP:LISTEN >/dev/null 2>&1; then
        printf 'occupe'
    elif command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk 'NR > 1 { print $4 }' | grep -E '[:.]8878$' >/dev/null; then
        printf 'occupe'
    elif (exec 3<>/dev/tcp/127.0.0.1/8878) 2>/dev/null; then
        printf 'occupe'
    else
        printf 'libre'
    fi
}

docker_desktop() {
    local plateforme=""
    if command -v docker >/dev/null 2>&1; then
        plateforme="$(avec_delai 10 docker version --format '{{.Server.Platform.Name}}' 2>/dev/null || true)"
    fi
    case "$plateforme" in
        *"Docker Desktop"*) printf 'oui'; return 0 ;;
    esac
    if { [ "$(systeme)" = macos ] && [ -d /Applications/Docker.app ]; } || { [ "$(systeme)" = linux ] && [ -d /opt/docker-desktop ]; }; then
        printf 'oui'
    else
        printf 'non'
    fi
}

etat_lms() {
    if [ -n "$(chemin_lms)" ]; then printf 'present'; else printf 'absent'; fi
}

# FileVault (macOS) ou un volume chiffré LUKS, vu par lsblk (Linux).
chiffrement_disque() {
    local etat=""
    case "$(systeme)" in
        macos)
            etat="$(fdesetup status 2>/dev/null || true)"
            case "$etat" in
                "FileVault is On"*) printf 'actif' ;;
                "FileVault is Off"*) printf 'inactif' ;;
                *) printf 'inconnu' ;;
            esac
            ;;
        linux)
            if ! command -v lsblk >/dev/null 2>&1 || ! etat="$(lsblk -o TYPE 2>/dev/null)"; then
                printf 'inconnu'
            elif printf '%s\n' "$etat" | grep -w crypt >/dev/null; then
                printf 'actif'
            else
                printf 'inactif'
            fi
            ;;
        *) printf 'inconnu' ;;
    esac
}

# Conteneur de transcription des séances (projet Compose « territoria ») : en marche, arrêté, ou inconnu sans moteur.
etat_transcription() {
    local moteur nom
    moteur="$(moteur_present)"
    case "$moteur" in docker | podman) ;; *) printf 'inconnu'; return 0 ;; esac
    if ! nom="$(avec_delai 10 "$moteur" ps --filter "name=${PROJET:-territoria}[-_]travaux" --format '{{.Names}}' 2>/dev/null)"; then
        printf 'inconnu'
    elif [ -n "$nom" ]; then
        printf 'en_marche'
    else
        printf 'arrete'
    fi
}

# Modèles de transcription téléchargés (modèle par défaut, découpage des tours de parole, empreintes de voix).
presence_modeles_audio() {
    local dossier="$TERRITORIA_DONNEES_HOTE/modeles-audio"
    if [ -s "$dossier/large-v3-turbo/model.bin" ] && [ -s "$dossier/segmentation/model.onnx" ] && [ -n "$(ls "$dossier/empreintes" 2>/dev/null)" ]; then
        printf 'presents'
    else
        printf 'absents'
    fi
}

MEMOIRE="$(memoire_go)"
DOCKER_DESKTOP="$(docker_desktop)"
printf 'systeme=%s\n' "$(systeme)"
printf 'architecture=%s\n' "$(architecture)"
printf 'memoire_go=%s\n' "$MEMOIRE"
printf 'gpu=%s\n' "$(gpu)"
printf 'disque_libre_go=%s\n' "$(disque_libre_go)"
printf 'port_8878=%s\n' "$(port_8878)"
printf 'moteur=%s\n' "$(moteur_present)"
printf 'docker_desktop=%s\n' "$DOCKER_DESKTOP"
printf 'lms=%s\n' "$(etat_lms)"
printf 'chiffrement_disque=%s\n' "$(chiffrement_disque)"
printf 'palier_modele=%s\n' "$(palier_modele "$MEMOIRE")"
printf 'transcription=%s\n' "$(etat_transcription)"
printf 'modeles_audio=%s\n' "$(presence_modeles_audio)"
# IA en ligne choisie par l'étape mistral, sinon IA locale.
if [ "$(lire_variable "$S/compose.env" TERRITORIA_IA_EN_LIGNE)" = mistral ]; then
    printf 'ia_en_ligne=mistral\n'
else
    printf 'ia_en_ligne=aucune\n'
fi
if [ "$DOCKER_DESKTOP" = oui ]; then
    printf 'avertissement=licence_docker_desktop_payante_pour_les_administrations\n'
fi
