#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Fonctions communes des scripts d'installation de Territoria (install/*.sh) et du parcours d'intégration
# (tests/integration.sh). Ce fichier est sourcé, jamais exécuté seul ; le sourcer ne modifie rien sur la machine.
#
# Contrat de chaque script : la dernière ligne de la sortie standard est « ETAPE <nom> : OK » ou
# « ETAPE <nom> : ECHEC <code> » (code en minuscules, chiffres et tirets bas), et le code de sortie vaut 0 ou 1.
# La progression va sur la sortie d'erreur ; elle ne montre ni secret ni contenu de la collectivité.
# Avec --simulation, chaque commande qui modifierait la machine est affichée (« + commande ») sans être lancée,
# aucune question n'est posée et rien n'est créé.
# Compatible avec le bash 3.2 de macOS : ni tableaux associatifs, ni ${v,,}, ni mapfile, ni chaînes << / <<< pour
# un secret (bash 3.2 les écrit dans un fichier temporaire).
# shellcheck disable=SC2034,SC2329  # bibliothèque : variables et fonctions servent aux scripts qui la sourcent
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Projet Compose et fichiers : tests/integration.sh les remplace (territoria-ci, surcharge compose.ci.yaml) après
# avoir sourcé ce fichier ; une installation réelle garde toujours `-p territoria`.
PROJET=territoria
FICHIERS_COMPOSE=(-f "$RACINE/conteneurs/compose.yaml")
ADRESSE_TERRITORIA="http://127.0.0.1:8878"
PORT_LMS=1234
ATTENTE_MAX=300  # secondes d'attente de la bonne santé d'un service

# Dossier de données : territoria-donnees, à côté du dépôt (jamais dedans). Une valeur déjà exportée l'emporte
# (dossier temporaire des essais et de l'intégration).
if [ -z "${TERRITORIA_DONNEES_HOTE:-}" ]; then
    TERRITORIA_DONNEES_HOTE="$(dirname "$RACINE")/territoria-donnees"
fi
export TERRITORIA_DONNEES_HOTE
S="$TERRITORIA_DONNEES_HOTE/secrets"

# Image Python épinglée de territoria.Dockerfile (même empreinte que celle de compose.ci.yaml) : conteneurs jetables
# en root pour donner les dossiers de données aux utilisateurs des services, et pour les contrôles d'accès réseau.
IMAGE_PYTHON="$(sed -n 's/^FROM \(python:3[^[:space:]]*\).*/\1/p' "$RACINE/conteneurs/territoria.Dockerfile" | head -n 1)"

# Les tâches planifiées (launchd, cron) et les raccourcis démarrent avec un PATH réduit : on y ajoute les emplacements
# habituels de Docker, Podman, Homebrew et de la commande lms de LM Studio (sans changer l'ordre choisi par l'utilisateur).
PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/opt/podman/bin:$HOME/.lmstudio/bin"

SIMULATION=0
MOTEUR=""       # docker ou podman, fixé par choisir_moteur
NOM_ETAPE=""
CODE_ECHEC=""
NL='
'
CR="$(printf '\r')"

# --- Sorties, codes d'échec et fin d'étape -----------------------------------------------------------------------

info() { printf '%s\n' "$*" >&2; }

# Code imprimé si la suite échoue (set -e) ; à poser avant chaque opération qui peut échouer.
phase() { CODE_ECHEC="$1"; }

echouer() {
    CODE_ECHEC="$1"
    shift
    if [ "$#" -gt 0 ]; then info "$*"; fi
    exit 1
}

# Code d'échec imprimé tel quel s'il n'a que des minuscules, des chiffres et des tirets bas (n8n_pas_sain,
# n8n_provisionnement) ; sinon erreur_inattendue.
code_propre() {
    case "$1" in
        '' | *[!abcdefghijklmnopqrstuvwxyz0123456789_]*) printf 'erreur_inattendue' ;;
        *) printf '%s' "$1" ;;
    esac
}

# Installe le piège qui imprime la ligne ETAPE finale, quoi qu'il arrive (set -e compris).
debut_etape() {
    NOM_ETAPE="$1"
    trap terminer EXIT
    trap 'CODE_ECHEC=interrompu; exit 130' INT
    trap 'CODE_ECHEC=interrompu; exit 143' TERM
}

terminer() {
    local statut=$?
    trap - EXIT
    if [ "$statut" -eq 0 ]; then
        printf 'ETAPE %s : OK\n' "$NOM_ETAPE"
    else
        printf 'ETAPE %s : ECHEC %s\n' "$NOM_ETAPE" "$(code_propre "$CODE_ECHEC")"
        statut=1
    fi
    exit "$statut"
}

# Échec d'arguments, avant toute étape : même contrat, sans rien lancer.
refuser_arguments() {
    printf 'ETAPE %s : ECHEC %s\n' "$1" "${2:-arguments_invalides}"
    exit 1
}

# --- Simulation --------------------------------------------------------------------------------------------------

# Les lignes de simulation vont sur le descripteur 9, ouvert sur la sortie standard par activer_simulation : elles
# restent visibles même quand l'appel est redirigé vers la sortie d'erreur ou capturé.
activer_simulation() {
    SIMULATION=1
    exec 9>&1
}

afficher_commande() {
    local mot
    {
        printf '+'
        for mot in "$@"; do printf ' %q' "$mot"; done
        printf '\n'
    } >&9
}

# Mot d'une commande que l'utilisateur recopie dans son terminal (bash, zsh, sh) : tel quel s'il n'a que des caractères
# sans effet pour le shell, sinon entre apostrophes droites (une apostrophe du mot devient '\''). Un chemin avec des
# espaces, des accents ou une apostrophe reste ainsi un seul argument.
citer() {
    local mot="$1" rendu=""
    case "$mot" in
        '' | *[!A-Za-z0-9_@%+=:,./-]*) ;;
        *)
            printf '%s' "$mot"
            return 0
            ;;
    esac
    while :; do
        case "$mot" in
            *"'"*)
                rendu="$rendu${mot%%"'"*}'\\''"
                mot="${mot#*"'"}"
                ;;
            *) break ;;
        esac
    done
    printf "'%s'" "$rendu$mot"
}

# Lance une commande, ou l'affiche seulement en simulation.
lancer() {
    if [ "$SIMULATION" = 1 ]; then
        afficher_commande "$@"
    else
        "$@"
    fi
}

# Ligne d'explication en simulation (rien sinon).
expliquer() {
    if [ "$SIMULATION" = 1 ]; then printf '# %s\n' "$*" >&9; fi
}

# Commande qui peut laisser un démon derrière elle (serveur LM Studio, machine Podman, application) : ses entrées et
# sorties ne restent pas attachées au terminal ou au tube de l'appelant ; sa sortie est recopiée ensuite.
lancer_detache() {
    local journal code=0
    if [ "$SIMULATION" = 1 ]; then
        afficher_commande "$@"
        return 0
    fi
    journal="$(mktemp "${TMPDIR:-/tmp}/territoria.XXXXXX")"
    "$@" </dev/null >"$journal" 2>&1 || code=$?
    cat "$journal" >&2 || true
    rm -f "$journal"
    return "$code"
}

# Lance une commande avec un délai maximal (macOS n'a pas `timeout`) ; rend son code, ou celui du signal.
avec_delai() {
    local delai="$1" pid garde code=0
    shift
    "$@" &
    pid=$!
    (
        exec >/dev/null 2>&1
        sleep "$delai"
        kill "$pid"
    ) &
    garde=$!
    wait "$pid" || code=$?
    kill "$garde" 2>/dev/null || true
    wait "$garde" 2>/dev/null || true
    return "$code"
}

# --- Machine -----------------------------------------------------------------------------------------------------

systeme() {
    case "$(uname -s)" in
        Darwin) printf 'macos' ;;
        Linux) printf 'linux' ;;
        *) printf 'autre' ;;
    esac
}

# Mémoire vive en Go, arrondie au plus proche.
memoire_go() {
    local octets=0 ko
    case "$(systeme)" in
        macos) octets="$(sysctl -n hw.memsize 2>/dev/null || echo 0)" ;;
        linux)
            ko="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo 2>/dev/null || echo 0)"
            octets=$((${ko:-0} * 1024))
            ;;
    esac
    printf '%s' $(((${octets:-0} + 536870912) / 1073741824))
}

# Palier du modèle selon la mémoire (seuils indicatifs de la spécification, à calibrer en recette).
palier_modele() {
    if [ "$1" -lt 12 ]; then
        printf 'petit'
    elif [ "$1" -lt 28 ]; then
        printf 'moyen'
    else
        printf 'grand'
    fi
}

# Identifiant `lms get` du palier, lu dans install/modeles.json.
modele_du_palier() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$RACINE/install/modeles.json" | head -n 1
}

# Chemin de la commande lms de LM Studio (vide si absente).
chemin_lms() {
    local candidat dossier
    if command -v lms >/dev/null 2>&1; then
        command -v lms
        return 0
    fi
    dossier="$(dossier_lmstudio)"
    for candidat in ${dossier:+"$dossier/bin/lms"} "/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms"; do
        if [ -x "$candidat" ]; then
            printf '%s' "$candidat"
            return 0
        fi
    done
}

# Dossier de LM Studio, résolu comme le fait son installateur officiel (lmstudio.ai/install.sh) : le chemin écrit dans
# ~/.lmstudio-home-pointer s'il existe, sinon ~/.lmstudio, sinon ~/.cache/lm-studio (autre emplacement documenté).
# Vide si aucun n'existe, ou si le pointeur désigne un dossier absent : le dossier n'est alors jamais deviné.
dossier_lmstudio() {
    local pointeur="$HOME/.lmstudio-home-pointer" dossier="" candidat
    if [ -f "$pointeur" ]; then
        IFS= read -r dossier <"$pointeur" || true
        dossier="${dossier%"$CR"}"
        case "$dossier" in
            /*) if [ -d "$dossier" ]; then printf '%s' "$dossier"; fi ;;
        esac
        return 0
    fi
    for candidat in "$HOME/.lmstudio" "$HOME/.cache/lm-studio"; do
        if [ -d "$candidat" ]; then
            printf '%s' "$candidat"
            return 0
        fi
    done
}

# Moteur installé, qu'il tourne ou non : docker, podman ou aucun.
moteur_present() {
    if command -v docker >/dev/null 2>&1; then
        printf 'docker'
    elif command -v podman >/dev/null 2>&1; then
        printf 'podman'
    else
        printf 'aucun'
    fi
}

moteur_repond() {
    avec_delai 30 "$1" info >/dev/null 2>&1
}

# Moteur utilisé : Docker s'il répond, sinon Podman s'il répond, sinon celui qui est installé (arrêté), sinon aucun.
# En simulation, aucun moteur n'est interrogé ; sans moteur installé, on affiche celui que l'étape moteur installerait
# (Docker Engine sous Linux, Podman ailleurs).
choisir_moteur() {
    local installe=""
    if [ "$SIMULATION" = 1 ]; then
        MOTEUR="$(moteur_present)"
        if [ "$MOTEUR" = aucun ]; then
            if [ "$(systeme)" = linux ]; then MOTEUR=docker; else MOTEUR=podman; fi
        fi
        return 0
    fi
    if command -v docker >/dev/null 2>&1; then
        if moteur_repond docker; then
            MOTEUR=docker
            return 0
        fi
        installe=docker
    fi
    if command -v podman >/dev/null 2>&1; then
        if moteur_repond podman; then
            MOTEUR=podman
            return 0
        fi
        if [ -z "$installe" ]; then installe=podman; fi
    fi
    MOTEUR="${installe:-aucun}"
}

# Moteur choisi et en marche, ou échec explicite (sans le démarrer).
exiger_moteur() {
    choisir_moteur
    if [ "$MOTEUR" = aucun ]; then
        echouer moteur_absent "Aucun moteur de conteneurs : lancez d'abord install/installer.sh --etape moteur."
    fi
    if [ "$SIMULATION" = 1 ] || moteur_repond "$MOTEUR"; then return 0; fi
    echouer moteur_arrete "Le moteur de conteneurs ($MOTEUR) ne répond pas : démarrez-le (install/demarrer.sh le fait)."
}

podman_sans_root() {
    [ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)" = true ]
}

# Démarre le moteur s'il est installé mais arrêté : machine Podman (macOS), Docker Desktop (macOS).
assurer_moteur_actif() {
    local debut
    choisir_moteur
    if [ "$MOTEUR" = aucun ]; then
        echouer moteur_absent "Aucun moteur de conteneurs : lancez d'abord install/installer.sh --etape moteur."
    fi
    if [ "$SIMULATION" = 1 ]; then
        if [ "$MOTEUR" = podman ] && [ "$(systeme)" = macos ]; then
            expliquer "si la machine Podman est arrêtée :"
            lancer podman machine start
        fi
        return 0
    fi
    if moteur_repond "$MOTEUR"; then return 0; fi
    if [ "$MOTEUR" = podman ] && [ "$(systeme)" = macos ]; then
        info "Démarrage de la machine Podman…"
        podman machine start >&2 || true
    elif [ "$MOTEUR" = docker ] && [ "$(systeme)" = macos ] && [ -d /Applications/Docker.app ]; then
        info "Démarrage de Docker Desktop…"
        open -a Docker || true
    fi
    debut="$(date +%s)"
    until moteur_repond "$MOTEUR"; do
        if [ $(($(date +%s) - debut)) -ge 180 ]; then
            echouer moteur_arrete "Le moteur de conteneurs ($MOTEUR) ne répond toujours pas après 180 s."
        fi
        sleep 5
    done
}

# Adresse des conteneurs vers LM Studio sur la machine (docs/acces-lmstudio.md).
url_lms_conteneurs() {
    if [ "$MOTEUR" = podman ]; then
        printf 'http://host.containers.internal:%s/v1' "$PORT_LMS"
    else
        printf 'http://host.docker.internal:%s/v1' "$PORT_LMS"
    fi
}

# Adresse d'écoute de LM Studio : 127.0.0.1 sur macOS (Docker Desktop et la machine Podman relaient
# host.docker.internal / host.containers.internal vers la boucle locale) ; sous Linux avec Docker, la passerelle du
# pont docker0, vers laquelle `host-gateway` pointe et qu'aucune autre machine du réseau local n'atteint.
adresse_ecoute_lms() {
    local passerelle=""
    if [ "$(systeme)" != linux ] || [ "$MOTEUR" != docker ]; then
        printf '127.0.0.1'
        return 0
    fi
    if [ "$SIMULATION" = 1 ]; then
        printf '<passerelle de docker0>'
        return 0
    fi
    passerelle="$(docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || true)"
    if [ -z "$passerelle" ] && command -v ip >/dev/null 2>&1; then
        passerelle="$(ip -4 -o addr show docker0 2>/dev/null | awk '{ sub(/\/.*/, "", $4); print $4; exit }' || true)"
    fi
    printf '%s' "$passerelle"
}

# --- Compose et conteneurs ---------------------------------------------------------------------------------------

# En simulation, MOTEUR peut ne pas être encore installé : la commande affichée est celle qui serait lancée.
compose() {
    lancer "$MOTEUR" compose -p "$PROJET" --env-file "$S/compose.env" "${FICHIERS_COMPOSE[@]}" "$@"
}

moteur() {
    lancer "$MOTEUR" "$@"
}

# Utilisateur des commandes `outils` jetables : celui de la machine, pour que les fichiers de secrets/ lui
# appartiennent. Avec Podman sans root (Linux), root dans le conteneur est déjà l'utilisateur de la machine.
utilisateur_outils() {
    if [ "$SIMULATION" != 1 ] && [ "$MOTEUR" = podman ] && [ "$(systeme)" = linux ] && podman_sans_root; then
        printf '0:0'
    else
        printf '%s:%s' "$(id -u)" "$(id -g)"
    fi
}

# Contrôle la sortie d'une commande `outils` : sa dernière ligne doit être « ETAPE <commande> : OK ». La sortie est
# recopiée sur la sortie d'erreur ; en cas d'échec, CODE_ECHEC reçoit le code d'outils et la fonction rend 1.
exiger_etape() {
    local commande="$1" sortie="$2" derniere
    derniere="${sortie##*"$NL"}"
    if [ -n "$sortie" ]; then printf '%s\n' "$sortie" >&2; fi
    if [ "$derniere" = "ETAPE $commande : OK" ]; then return 0; fi
    case "$derniere" in
        "ETAPE $commande : ECHEC "*) CODE_ECHEC="$(code_propre "${derniere#"ETAPE $commande : ECHEC "}")" ;;
        *) CODE_ECHEC=sortie_inattendue ;;
    esac
    info "Attendu : ETAPE $commande : OK"
    return 1
}

# `outils <commande>` dans un conteneur jetable de l'image territoria, sur le dossier des secrets (sans réseau
# requis, sans dépendances démarrées), avec l'identifiant de l'utilisateur de la machine.
outils_jetable() {
    local commande="$1"
    shift
    local arguments=(run --rm --no-deps -T --user "$(utilisateur_outils)" -v "$S:/donnees-secrets"
        territoria python -m outils "$commande" "$@")
    if [ "$SIMULATION" = 1 ]; then
        compose "${arguments[@]}"
        return 0
    fi
    exiger_etape "$commande" "$(compose "${arguments[@]}" </dev/null)"
}

# `outils <commande>` dans le service territoria démarré (il voit /donnees et joint n8n).
outils_service() {
    local commande="$1"
    shift
    local arguments=(exec -T territoria python -m outils "$commande" "$@")
    if [ "$SIMULATION" = 1 ]; then
        compose "${arguments[@]}"
        return 0
    fi
    exiger_etape "$commande" "$(compose "${arguments[@]}" </dev/null)"
}

# Conteneur ponctuel de l'image Territoria pour les sauvegardes et restaurations : root (il lit et rétablit les
# fichiers de 10001, de 1000 et de l'utilisateur de la machine), sans réseau, racine en lecture seule. Le clair
# déchiffré reste dans /tmp, en mémoire (tmpfs). Les montages et l'image sont passés en arguments ; le mot de passe
# arrive par l'entrée standard ou par un fichier monté, jamais en argument.
ponctuel() {
    moteur run --rm -i --network none --user 0:0 --read-only --tmpfs /tmp:mode=1777 "$@"
}

# Image territoria construite pour ce projet (vide si absente). `compose images` ne voit que les images des conteneurs
# créés : après `run --rm`, après `down` ou sur une machine où l'on restaure, on la cherche par son nom de construction,
# <projet>-territoria (Compose v2 ; <projet>_territoria pour podman-compose, à valider en recette).
image_territoria() {
    local image="" nom
    if [ "$SIMULATION" = 1 ]; then
        printf '<image territoria du projet %s>' "$PROJET"
        return 0
    fi
    image="$(compose images -q territoria 2>/dev/null | head -n 1 || true)"
    for nom in "$PROJET-territoria" "${PROJET}_territoria"; do
        if [ -n "$image" ]; then break; fi
        image="$("$MOTEUR" image inspect --format '{{.Id}}' "$nom" 2>/dev/null || true)"
    done
    printf '%s' "$image"
}

attendre_sain() {
    local service="$1" conteneur etat debut
    if [ "$SIMULATION" = 1 ]; then
        expliquer "attente de la bonne santé du service $service (au plus ${ATTENTE_MAX} s)"
        return 0
    fi
    debut="$(date +%s)"
    while :; do
        conteneur="$(compose ps -q "$service" 2>/dev/null || true)"
        etat=""
        if [ -n "$conteneur" ]; then
            etat="$("$MOTEUR" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$conteneur" 2>/dev/null || true)"
        fi
        if [ "$etat" = "healthy" ]; then
            info "$service : sain"
            return 0
        fi
        if [ $(($(date +%s) - debut)) -ge "$ATTENTE_MAX" ]; then
            CODE_ECHEC="${service//-/_}_pas_sain"
            info "$service : toujours « ${etat:-absent} » après ${ATTENTE_MAX} s"
            return 1
        fi
        sleep 3
    done
}

# --- Dossiers, fichiers et propriétaires -------------------------------------------------------------------------

verifier_dossier_donnees() {
    case "$TERRITORIA_DONNEES_HOTE" in
        /*) ;;
        *) echouer donnees_invalides "TERRITORIA_DONNEES_HOTE doit être un chemin absolu." ;;
    esac
    case "$TERRITORIA_DONNEES_HOTE/" in
        "$RACINE"/*) echouer donnees_dans_le_depot "territoria-donnees doit être à côté du dépôt, jamais dedans." ;;
    esac
}

# Crée un dossier absent avec ses droits ; un dossier existant n'est pas touché (sous Linux il peut appartenir à
# l'utilisateur d'un service).
creer_dossier() {
    if [ -d "$1" ]; then return 0; fi
    lancer mkdir -p -m "$2" "$1"
}

creer_fichier_vide() {
    if [ -e "$1" ]; then return 0; fi
    if [ "$SIMULATION" = 1 ]; then
        afficher_commande install -m 600 /dev/null "$1"
        return 0
    fi
    (umask 077 && : >>"$1")
}

# Donne territoria/ à l'uid 10001 et n8n/ à l'uid 1000 (utilisateurs des services) par un conteneur jetable en root :
# sans sudo, de la même façon sous Linux et sur un Mac. Seul le dossier lui-même change de propriétaire. Le conteneur
# crée aussi territoria/audio/ : sous Linux, l'application restreint territoria/ à son uid (0700) dès son démarrage, et
# la machine n'y entre plus (mise à jour, étape relancée).
attribuer_proprietaires() {
    if [ -z "$IMAGE_PYTHON" ]; then echouer image_python_introuvable "Image Python introuvable dans territoria.Dockerfile."; fi
    phase proprietaires_impossibles
    moteur run --rm --network none --user 0:0 --entrypoint sh \
        -v "$TERRITORIA_DONNEES_HOTE/territoria:/territoria" -v "$TERRITORIA_DONNEES_HOTE/n8n:/n8n" \
        -v "$TERRITORIA_DONNEES_HOTE/caddy:/caddy" -v "$TERRITORIA_DONNEES_HOTE/modeles-audio:/modeles-audio" "$IMAGE_PYTHON" \
        -c 'mkdir -p /territoria/audio && chmod 0700 /territoria/audio && chown 10001:10001 /territoria /territoria/audio /modeles-audio && chown 1000:1000 /n8n && chown 10002:10002 /caddy && chmod 0700 /caddy'
}

# Dossier de données complet avant tout démarrage : territoria/ (uid 10001), n8n/ (uid 1000), caddy/ (uid 10002, 0700 :
# autorité de certification du proxy du mode équipe, compose.equipe.yaml), secrets/ (0700), territoria/audio/ (uid 10001,
# 0700 : enregistrements des séances, partagés avec le conteneur de transcription), modeles-audio/ (uid 10001 : modèles
# de transcription) et les fichiers d'environnement que Compose exige, même vides.
preparer_dossiers() {
    verifier_dossier_donnees
    phase dossiers_impossibles
    creer_dossier "$TERRITORIA_DONNEES_HOTE" 700
    creer_dossier "$TERRITORIA_DONNEES_HOTE/territoria" 755
    creer_dossier "$TERRITORIA_DONNEES_HOTE/n8n" 755
    creer_dossier "$TERRITORIA_DONNEES_HOTE/caddy" 700
    creer_dossier "$TERRITORIA_DONNEES_HOTE/modeles-audio" 755
    creer_dossier "$S" 700
    creer_fichier_vide "$S/territoria.env"
    creer_fichier_vide "$S/n8n.env"
    creer_fichier_vide "$S/compose.env"
    attribuer_proprietaires
}

# Vrai si le fichier d'environnement contient CLE avec une valeur non vide (la valeur n'est jamais imprimée).
variable_renseignee() {
    local fichier="$1" cle="$2" ligne
    [ -f "$fichier" ] || return 1
    while IFS= read -r ligne || [ -n "$ligne" ]; do
        case "$ligne" in
            "$cle="?*) return 0 ;;
        esac
    done <"$fichier"
    return 1
}

# Valeur d'une variable non secrète d'un fichier d'environnement (guillemets simples retirés).
lire_variable() {
    local fichier="$1" cle="$2" ligne valeur=""
    if [ -f "$fichier" ]; then
        while IFS= read -r ligne || [ -n "$ligne" ]; do
            case "$ligne" in
                "$cle="*) valeur="${ligne#"$cle="}" ;;
            esac
        done <"$fichier"
    fi
    valeur="${valeur#\'}"
    printf '%s' "${valeur%\'}"
}

# Écrit CLE=valeur dans un fichier d'environnement (remplace la première ligne CLE=, retire les doublons, garde les
# autres lignes), en 0600, par remplacement atomique. La valeur ne passe jamais en argument d'un programme : seules
# les commandes internes de bash la manipulent. Option « litteral » : valeur entre guillemets simples (Compose ne
# l'interpole pas) ; « secret » : la valeur est masquée en simulation.
ecrire_variable() {
    local fichier="$1" cle="$2" valeur="$3" options="${4:-}" temporaire ligne ecrite=0 rendu
    case "$valeur" in
        *"$NL"* | *"$CR"*) echouer valeur_invalide "Une valeur ne peut pas contenir de fin de ligne." ;;
    esac
    rendu="$valeur"
    case "$options" in
        *litteral*)
            case "$valeur" in *"'"*) echouer valeur_invalide "Une valeur ne peut pas contenir d'apostrophe droite." ;; esac
            rendu="'$valeur'"
            ;;
    esac
    if [ "$SIMULATION" = 1 ]; then
        case "$options" in
            *secret*) printf '+ écrire %s=(saisie masquée) dans %q (0600)\n' "$cle" "$fichier" >&9 ;;
            *) printf '+ écrire %s=%s dans %q (0600)\n' "$cle" "$rendu" "$fichier" >&9 ;;
        esac
        return 0
    fi
    temporaire="$fichier.tmp.$$"
    if ! (
        umask 077
        : >"$temporaire" || exit 1
        if [ -f "$fichier" ]; then
            while IFS= read -r ligne || [ -n "$ligne" ]; do
                case "$ligne" in
                    "$cle=" | "$cle="*)
                        if [ "$ecrite" = 0 ]; then
                            printf '%s=%s\n' "$cle" "$rendu" >>"$temporaire" || exit 1
                            ecrite=1
                        fi
                        ;;
                    *) printf '%s\n' "$ligne" >>"$temporaire" || exit 1 ;;
                esac
            done <"$fichier" || exit 1
        fi
        if [ "$ecrite" = 0 ]; then printf '%s=%s\n' "$cle" "$rendu" >>"$temporaire" || exit 1; fi
        chmod 600 "$temporaire" || exit 1
        mv -f "$temporaire" "$fichier" || exit 1
    ); then
        rm -f "$temporaire"
        echouer ecriture_impossible "Écriture impossible dans $fichier."
    fi
}

# Retire CLE d'un fichier d'environnement (toutes ses lignes CLE=), garde les autres lignes, en 0600, par remplacement
# atomique ; sans effet si le fichier ou la clé est absent. La valeur n'est jamais affichée : seules les commandes
# internes de bash lisent les lignes.
retirer_variable() {
    local fichier="$1" cle="$2" temporaire ligne presente=0
    if [ "$SIMULATION" = 1 ]; then
        printf '+ retirer %s de %q (0600)\n' "$cle" "$fichier" >&9
        return 0
    fi
    [ -f "$fichier" ] || return 0
    while IFS= read -r ligne || [ -n "$ligne" ]; do
        case "$ligne" in "$cle=" | "$cle="*) presente=1 ;; esac
    done <"$fichier"
    [ "$presente" = 1 ] || return 0
    temporaire="$fichier.tmp.$$"
    if ! (
        umask 077
        : >"$temporaire" || exit 1
        while IFS= read -r ligne || [ -n "$ligne" ]; do
            case "$ligne" in
                "$cle=" | "$cle="*) ;;
                *) printf '%s\n' "$ligne" >>"$temporaire" || exit 1 ;;
            esac
        done <"$fichier" || exit 1
        chmod 600 "$temporaire" || exit 1
        mv -f "$temporaire" "$fichier" || exit 1
    ); then
        rm -f "$temporaire"
        echouer ecriture_impossible "Écriture impossible dans $fichier."
    fi
}

# Écrit un fichier (contenu sur l'entrée standard, jamais secret), avec ses droits ; affiché en simulation.
ecrire_fichier() {
    local fichier="$1" mode="$2" contenu
    contenu="$(cat)"
    if [ "$SIMULATION" = 1 ]; then
        {
            printf '+ écrire %q (%s) :\n' "$fichier" "$mode"
            printf '%s\n' "$contenu" | sed 's/^/|   /'
        } >&9
        return 0
    fi
    printf '%s\n' "$contenu" >"$fichier.tmp.$$"
    chmod "$mode" "$fichier.tmp.$$"
    mv -f "$fichier.tmp.$$" "$fichier"
}

echapper_xml() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# --- Saisie de l'utilisateur, dans son terminal ------------------------------------------------------------------

terminal_disponible() {
    (: </dev/tty) 2>/dev/null
}

exiger_terminal() {
    if ! terminal_disponible; then
        echouer terminal_requis "Cette étape pose des questions : lancez-la vous-même dans un terminal."
    fi
}

# Lit une réponse visible ; nom de variable, invite. (Les variables locales portent des noms qu'aucun appelant
# n'emploie : printf -v écrirait sinon dans la variable locale homonyme au lieu de celle de l'appelant.)
lire_reponse() {
    local __saisie_visible=""
    printf '%s' "$2" >/dev/tty
    IFS= read -r __saisie_visible </dev/tty || __saisie_visible=""
    printf -v "$1" '%s' "$__saisie_visible"
}

# Lit une valeur en saisie masquée (rien ne s'affiche) ; nom de variable, invite.
lire_secret() {
    local __saisie_masquee=""
    printf '%s' "$2" >/dev/tty
    IFS= read -r -s __saisie_masquee </dev/tty || __saisie_masquee=""
    printf '\n' >/dev/tty
    printf -v "$1" '%s' "$__saisie_masquee"
    __saisie_masquee=""
}

repondu_oui() {
    case "$1" in
        o | O | oui | Oui | OUI) return 0 ;;
        *) return 1 ;;
    esac
}

# --- Démarrage et provisionnement (séquence vérifiée de la tâche 8) ----------------------------------------------

exiger_secrets() {
    if [ "$SIMULATION" = 1 ]; then return 0; fi
    if ! variable_renseignee "$S/n8n.env" N8N_ENCRYPTION_KEY || ! variable_renseignee "$S/territoria.env" TERRITORIA_SERVICE_TOKEN; then
        # n8n inventerait sa propre clé de chiffrement s'il démarrait sans elle.
        echouer secrets_absents "Secrets absents : lancez d'abord install/installer.sh --etape secrets."
    fi
}

# Démarre LM Studio (serveur local, écoute limitée) s'il est installé sur la machine.
demarrer_lmstudio() {
    local lms adresse
    if [ "$(lire_variable "$S/compose.env" TERRITORIA_IA_EN_LIGNE)" = mistral ]; then
        info "API de Mistral choisie (étape mistral) : LM Studio n'est pas démarré."
        return 0
    fi
    lms="$(chemin_lms)"
    if [ -z "$lms" ]; then
        info "LM Studio (lms) introuvable : serveur IA non démarré."
        return 0
    fi
    adresse="$(adresse_ecoute_lms)"
    if [ -z "$adresse" ]; then echouer passerelle_introuvable "Passerelle du pont docker0 introuvable."; fi
    if [ "$SIMULATION" != 1 ] && lms_en_marche "$lms"; then
        # Sous Linux, un serveur relancé par LM Studio lui-même (ouverture de session) écoute peut-être sur 127.0.0.1,
        # injoignable des conteneurs : on le relance alors sur la passerelle de docker0.
        if [ "$adresse" != 127.0.0.1 ] && ! curl -s --max-time 5 "http://$adresse:$PORT_LMS/v1/models" >/dev/null 2>&1; then
            info "LM Studio n'écoute pas sur $adresse : redémarrage de son serveur sur cette adresse."
            lancer_detache "$lms" server stop || true
            phase lmstudio_arrete
            lancer_detache "$lms" server start --port "$PORT_LMS" --bind "$adresse"
        fi
    else
        phase lmstudio_arrete
        lancer_detache "$lms" server start --port "$PORT_LMS" --bind "$adresse"
    fi
    # Un serveur déjà ouvert au réseau local (« Serve on Local Network ») n'est jamais accepté.
    verifier_ecoute_lms "$adresse"
    assurer_modele_charge "$lms"
}

# Charge le modèle dans LM Studio sous l'identifiant que Territoria demande (TERRITORIA_MODELE), s'il n'y est pas déjà :
# `lms load <id> --identifier <clé>`. Sans lui, la première demande après un redémarrage de LM Studio chargerait le
# modèle à la volée (plusieurs minutes sur un PC sans carte graphique), et le test de bout en bout pourrait échouer.
charger_modele_lms() {
    local lms="$1" id="$2" cle="$3"
    # Espaces retirés : la sortie JSON de `lms ps` peut être indentée.
    if [ "$SIMULATION" != 1 ] && "$lms" ps --json 2>/dev/null | tr -d ' \t\r\n' | grep -F "\"identifier\":\"$cle\"" >/dev/null; then
        return 0
    fi
    phase chargement_impossible
    lancer "$lms" load "$id" --identifier "$cle" --yes >&2
}

# Identifiant `lms get` d'une clé de modèle (TERRITORIA_MODELE) : celui de install/modeles.json dont la clé (l'identifiant
# sans sa variante @…) correspond, sinon la clé elle-même (modèle choisi par la personne qui gère l'installation).
modele_de_la_cle() {
    local palier id
    for palier in petit moyen grand; do
        id="$(modele_du_palier "$palier")"
        if [ -n "$id" ] && [ "${id%@*}" = "$1" ]; then
            printf '%s' "$id"
            return 0
        fi
    done
    printf '%s' "$1"
}

# Après le démarrage du serveur de LM Studio : le modèle de compose.env (TERRITORIA_MODELE, écrit par l'étape lmstudio)
# est chargé s'il ne l'est pas. Sans ce réglage (autre serveur IA), rien n'est chargé.
assurer_modele_charge() {
    local cle
    cle="$(lire_variable "$S/compose.env" TERRITORIA_MODELE)"
    if [ -z "$cle" ]; then
        expliquer "modèle de compose.env (TERRITORIA_MODELE) chargé dans LM Studio s'il ne l'est pas : lms load <id> --identifier <clé> --yes"
        return 0
    fi
    charger_modele_lms "$1" "$(modele_de_la_cle "$cle")" "$cle"
}

# Adresses locales sur lesquelles le port de LM Studio est en écoute, une par ligne (sans le port) : lsof sur macOS,
# ss sous Linux ; « * », « 0.0.0.0 » et « [::] » désignent toutes les interfaces.
adresses_ecoute_lms() {
    if [ "$(systeme)" = macos ]; then
        { lsof -nP -iTCP:"$PORT_LMS" -sTCP:LISTEN -Fn 2>/dev/null || true; } | sed -n "s/^n\(.*\):$PORT_LMS\$/\1/p"
    else
        { ss -ltn 2>/dev/null || true; } | awk -v port=":$PORT_LMS" 'NR > 1 && substr($4, length($4) - length(port) + 1) == port { print substr($4, 1, length($4) - length(port)) }'
    fi
}

# L'écoute réelle de LM Studio ne doit viser que la boucle locale ou l'adresse choisie (passerelle de docker0 sous
# Linux) : une écoute sur toutes les interfaces ou sur une adresse du réseau local arrête l'étape.
verifier_ecoute_lms() {
    local attendue="$1" adresses adresse outil
    if [ "$SIMULATION" = 1 ]; then
        if [ "$(systeme)" = macos ]; then lancer lsof -nP -iTCP:"$PORT_LMS" -sTCP:LISTEN; else lancer ss -ltn; fi
        expliquer "LM Studio ne doit écouter que sur $attendue ou la boucle locale, jamais sur toutes les interfaces ni sur le réseau local"
        return 0
    fi
    outil=ss
    if [ "$(systeme)" = macos ]; then outil=lsof; fi
    if ! command -v "$outil" >/dev/null 2>&1; then
        echouer ecoute_non_verifiable "Impossible de vérifier l'adresse d'écoute de LM Studio ($outil absent)."
    fi
    adresses="$(adresses_ecoute_lms)"
    if [ -z "$adresses" ]; then
        echouer lmstudio_injoignable "Aucun serveur n'écoute sur le port $PORT_LMS : LM Studio n'a pas démarré son serveur."
    fi
    # Boucle sur des lignes (et non sur des mots) : « * » ne doit pas être développé en noms de fichiers.
    while IFS= read -r adresse; do
        case "$adresse" in
            "$attendue" | 127.0.0.1 | '[::1]' | ::1 | localhost) ;;
            *) echouer lmstudio_expose_reseau "LM Studio écoute sur $adresse:$PORT_LMS, donc au-delà de cette machine. Dans LM Studio (Developer > Server Settings), décochez « Serve on Local Network », puis relancez l'étape." ;;
        esac
    done <<FIN
$adresses
FIN
    info "LM Studio n'écoute que sur : $(printf '%s' "$adresses" | tr '\n' ' ')"
}

# Pas de `grep -q` derrière un tube avec pipefail : il fermerait le tube avant la fin et ferait échouer la commande.
lms_en_marche() {
    "$1" server status --json 2>/dev/null | grep '"running":true' >/dev/null
}

# Démarre les services ; avec « construire », reconstruit d'abord l'image territoria.
demarrer_services() {
    exiger_secrets
    if [ "${1:-}" = construire ]; then
        phase construction_impossible
        compose build territoria >&2
        modeles_audio_facultatif
    fi
    phase demarrage_impossible
    compose up -d >&2
    attendre_sain n8n
    attendre_sain territoria
}

# Modèles de transcription des séances : image du conteneur de travaux, puis téléchargement vérifié (empreintes SHA-256
# du manifeste conteneurs/modeles-audio.json) par le service ponctuel travaux-modeles, seul moment où cette image a un
# réseau. Environ 1,7 Go la première fois ; ensuite, les fichiers présents et intacts ne sont pas retéléchargés.
modeles_audio() {
    phase construction_impossible
    compose build travaux >&2
    phase modeles_audio
    local arguments=(--profile outils run --rm -T travaux-modeles)
    if [ "$SIMULATION" = 1 ]; then
        compose "${arguments[@]}"
        return 0
    fi
    exiger_etape modeles-audio "$(compose "${arguments[@]}" </dev/null)"
}

# Les modèles ne conditionnent que la transcription des séances : leur absence (proxy, coupure, disque) n'empêche ni
# l'installation ni la mise à jour. La transcription attend alors (« Modèles absents » dans Santé), et l'étape demarrer
# relancée reprend le téléchargement là où il s'était arrêté.
modeles_audio_facultatif() {
    if ! modeles_audio; then
        info "Modèles de transcription non téléchargés (code $CODE_ECHEC) : Territoria démarre sans eux et la transcription des séances attend. Pour réessayer : install/installer.sh --etape demarrer"
    fi
    return 0
}

# Provisionnement de n8n (credentials et workflows), n8n arrêté pendant l'import. Les jetons en clair de
# secrets/provision/ sont supprimés en sortie, que la séquence réussisse ou non (piège du sous-shell, qui ne remplace
# pas celui de l'appelant). Le démarrage et le compte propriétaire viennent ensuite (voir l'appelant).
provisionner_n8n() {
    phase n8n_provisionnement
    (
        trap nettoyer_provision EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        outils_jetable provision-n8n --sortie /donnees-secrets/provision
        # n8n (uid 1000) lit ce dossier : ouvert en lecture le temps de l'import ; secrets/ reste en 0700.
        lancer chmod 0755 "$S/provision"
        lancer chmod 0644 "$S"/provision/*.json
        compose stop n8n >&2  # sans effet à la première installation
        compose run --rm --no-deps -T -v "$S/provision:/provision:ro" n8n import:credentials --input=/provision/credentials.json </dev/null >&2
        compose run --rm --no-deps -T -v "$S/provision:/provision:ro" n8n import:workflow --separate --input=/provision/ </dev/null >&2
        # Un import désactive les workflows ; publish:workflow les réactive et rend 1 si l'identifiant est inconnu
        # (import:workflow rend 0 même sur un dossier vide : c'est la publication qui signale l'échec).
        compose run --rm --no-deps -T n8n publish:workflow --id=TerritoriaConv01 </dev/null >&2
        compose run --rm --no-deps -T n8n publish:workflow --id=TerritoriaArre01 </dev/null >&2
        lancer rm -rf "$S/provision"
        if [ "$SIMULATION" != 1 ]; then test ! -e "$S/provision"; fi
    )
}

nettoyer_provision() {
    if [ "$SIMULATION" != 1 ]; then rm -rf "$S/provision"; fi
}

# --- Sauvegardes et restaurations (conteneur ponctuel ; aussi pour la mise à jour, tâche 13) ---------------------

FICHIER_MOT_DE_PASSE="$S/sauvegarde.motdepasse"

verifier_destination() {
    case "$1" in
        /*) ;;
        *) echouer destination_invalide "La destination doit être un chemin absolu." ;;
    esac
    case "$1/" in
        "$TERRITORIA_DONNEES_HOTE"/* | "$RACINE"/*)
            echouer destination_invalide "La destination ne peut être ni dans territoria-donnees ni dans le dépôt." ;;
    esac
    if [ "$SIMULATION" != 1 ] && [ ! -d "$1" ]; then
        # Jamais créée : un disque externe débranché ne doit pas être remplacé par un dossier vide.
        echouer destination_absente "Dossier de destination introuvable : $1"
    fi
}

exiger_image_territoria() {
    IMAGE="$(image_territoria)"
    if [ -z "$IMAGE" ]; then
        echouer image_absente "Image territoria absente : lancez d'abord install/installer.sh --etape secrets."
    fi
}

# Mot de passe des sauvegardes saisi deux fois, masqué, dans le terminal. Il est gardé dans la variable motdepasse (jamais
# exportée, jamais écrite sur le disque, jamais passée en argument) ; l'appelant la vide dès qu'il n'en a plus besoin.
saisir_mot_de_passe_sauvegarde() {
    local confirmation=""
    exiger_terminal
    lire_secret motdepasse "Mot de passe de la sauvegarde (saisie masquée) : "
    lire_secret confirmation "Confirmez le mot de passe : "
    if [ -z "$motdepasse" ]; then echouer mot_de_passe_absent "Mot de passe vide."; fi
    if [ "$motdepasse" != "$confirmation" ]; then echouer saisies_differentes "Les deux saisies diffèrent."; fi
    confirmation=""
}

# Archive chiffrée de territoria-donnees (monté en lecture seule) dans la destination, services en marche.
# Avec « auto », le mot de passe vient de secrets/sauvegarde.motdepasse ; sinon il est saisi deux fois, masqué.
sauvegarde_creer() {
    local destination="$1" mode="${2:-}"
    verifier_dossier_donnees
    verifier_destination "$destination"
    sauvegarde_ecrire "$destination" "$mode"
}

# Écrit l'archive dans une destination déjà contrôlée par l'appelant. Mode du mot de passe : « auto » (fichier
# secrets/sauvegarde.motdepasse), « memoire » (déjà saisi dans la variable motdepasse, que l'appelant videra : mise à
# jour), sinon saisi ici deux fois, masqué. L'image utilisée reste dans IMAGE.
sauvegarde_ecrire() {
    local destination="$1" mode="${2:-}"
    if [ "$SIMULATION" != 1 ] && [ ! -d "$TERRITORIA_DONNEES_HOTE/territoria" ]; then
        echouer installation_absente "Aucune installation dans $TERRITORIA_DONNEES_HOTE."
    fi
    exiger_moteur
    exiger_image_territoria
    local montages=(-v "$TERRITORIA_DONNEES_HOTE:/source:ro" -v "$destination:/destination" "$IMAGE")
    if [ "$mode" = auto ]; then
        if [ "$SIMULATION" != 1 ] && [ ! -s "$FICHIER_MOT_DE_PASSE" ]; then
            echouer mot_de_passe_absent "Mot de passe de sauvegarde absent : lancez install/installer.sh --etape sauvegarde-auto."
        fi
        if [ "$SIMULATION" = 1 ]; then
            ponctuel "${montages[@]}" python -m outils sauvegarder --depuis-fichier /source/secrets/sauvegarde.motdepasse
            return 0
        fi
        exiger_etape sauvegarder "$(ponctuel "${montages[@]}" python -m outils sauvegarder \
            --depuis-fichier /source/secrets/sauvegarde.motdepasse </dev/null)"
        return 0
    fi
    if [ "$SIMULATION" = 1 ]; then
        expliquer "saisie masquée du mot de passe de la sauvegarde (deux fois), transmis par l'entrée standard"
        ponctuel "${montages[@]}" python -m outils sauvegarder
        return 0
    fi
    if [ "$mode" != memoire ]; then saisir_mot_de_passe_sauvegarde; fi
    # printf est une commande interne : le mot de passe passe par un tube, jamais par un argument ni par le disque.
    exiger_etape sauvegarder "$(printf '%s\n' "$motdepasse" | ponctuel "${montages[@]}" python -m outils sauvegarder)"
    if [ "$mode" != memoire ]; then motdepasse=""; fi
}

# Vérifie une archive précise (déchiffrement complet, manifeste, intégrité des bases) avec l'image IMAGE ; mot de passe
# comme pour sauvegarde_ecrire (« auto » ou « memoire »), transmis par l'entrée standard.
sauvegarde_verifier() {
    local archive="$1" mode="${2:-}"
    local commande=(ponctuel -v "$(dirname "$archive"):/destination:ro" "$IMAGE"
        python -m outils verifier-sauvegarde --archive "/destination/$(basename "$archive")")
    if [ "$SIMULATION" = 1 ]; then
        "${commande[@]}"
        return 0
    fi
    if [ "$mode" = auto ]; then
        exiger_etape verifier-sauvegarde "$("${commande[@]}" <"$FICHIER_MOT_DE_PASSE")"
    else
        exiger_etape verifier-sauvegarde "$(printf '%s\n' "$motdepasse" | "${commande[@]}")"
    fi
}

# Garde 7 archives quotidiennes et 4 hebdomadaires dans la destination.
sauvegarde_elaguer() {
    local destination="$1"
    verifier_destination "$destination"
    exiger_moteur
    exiger_image_territoria
    if [ "$SIMULATION" = 1 ]; then
        ponctuel -v "$destination:/destination" "$IMAGE" python -m outils elaguer
        return 0
    fi
    exiger_etape elaguer "$(ponctuel -v "$destination:/destination" "$IMAGE" python -m outils elaguer </dev/null)"
}

# Restaure une archive (fichier, ou dossier dont la plus récente est prise) dans territoria-donnees, services arrêtés.
# Un territoria-donnees non vide exige « forcer ». Mot de passe : « auto » (sauvegarde.motdepasse), « memoire » (déjà
# saisi dans la variable motdepasse : mise à jour), sinon saisi ici, masqué. Une image peut être imposée (4e argument :
# la mise à jour restaure avec l'image de la version qui a fait la sauvegarde) ; sinon, celle du projet.
sauvegarde_restaurer() {
    local archive="$1" forcer="${2:-}" mode="${3:-}" image="${4:-}" dossier nom=""
    local options=()
    verifier_dossier_donnees
    case "$archive" in
        /*) ;;
        *) echouer archive_invalide "L'archive doit être désignée par un chemin absolu." ;;
    esac
    if [ -d "$archive" ]; then
        dossier="$archive"
    elif [ -f "$archive" ] || [ "$SIMULATION" = 1 ]; then
        dossier="$(dirname "$archive")"
        nom="$(basename "$archive")"
    else
        echouer archive_absente "Archive introuvable : $archive"
    fi
    if [ "$forcer" = forcer ]; then
        options=(--forcer)
    elif [ "$SIMULATION" != 1 ] && [ -d "$TERRITORIA_DONNEES_HOTE" ] && [ -n "$(ls -A "$TERRITORIA_DONNEES_HOTE" 2>/dev/null)" ]; then
        echouer cible_non_vide "territoria-donnees n'est pas vide : après une sauvegarde, relancez avec --forcer pour remplacer territoria/, n8n/ et les fichiers de secrets/."
    fi
    if [ "$mode" = auto ] && [ "$SIMULATION" != 1 ] && [ ! -s "$FICHIER_MOT_DE_PASSE" ]; then
        echouer mot_de_passe_absent "Mot de passe de sauvegarde absent de $FICHIER_MOT_DE_PASSE."
    fi
    exiger_moteur
    if [ -n "$image" ]; then IMAGE="$image"; else exiger_image_territoria; fi
    if [ "$mode" != auto ] && [ "$mode" != memoire ] && [ "$SIMULATION" != 1 ]; then
        exiger_terminal
        lire_secret motdepasse "Mot de passe de la sauvegarde à restaurer (saisie masquée) : "
        if [ -z "$motdepasse" ]; then echouer mot_de_passe_absent "Mot de passe vide."; fi
    fi
    if [ "$SIMULATION" = 1 ] || [ -f "$S/compose.env" ]; then
        phase arret_impossible
        compose stop >&2
    fi
    creer_dossier "$TERRITORIA_DONNEES_HOTE" 700
    local commande=(ponctuel -v "$dossier:/destination:ro" -v "$TERRITORIA_DONNEES_HOTE:/cible" "$IMAGE"
        python -m outils restaurer --archive "/destination${nom:+/$nom}" ${options[@]+"${options[@]}"})
    if [ "$SIMULATION" = 1 ]; then
        expliquer "mot de passe transmis par l'entrée standard ($([ "$mode" = auto ] && printf 'fichier sauvegarde.motdepasse' || printf 'saisie masquée'))"
        "${commande[@]}"
        return 0
    fi
    if [ "$mode" = auto ]; then
        exiger_etape restaurer "$("${commande[@]}" <"$FICHIER_MOT_DE_PASSE")"
    else
        exiger_etape restaurer "$(printf '%s\n' "$motdepasse" | "${commande[@]}")"
        if [ "$mode" != memoire ]; then motdepasse=""; fi
    fi
}

# Serveur de démonstration (install/demonstration.sh, VPS Linux seulement) : Caddy en HTTPS, accès réservé aux comptes,
# OpenRouter permis. Lu une fois au chargement, comme les autres réglages de compose.env.
if [ "$(lire_variable "$S/compose.env" TERRITORIA_DEMONSTRATION)" = 1 ]; then
    FICHIERS_COMPOSE+=(-f "$RACINE/conteneurs/compose.demonstration.yaml")
fi
