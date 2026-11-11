#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Installation de Territoria, une étape à la fois :
#   install/installer.sh --etape <nom> [--simulation] [--auto] [--palier petit|moyen|grand]
# Étapes, dans l'ordre : moteur, lmstudio (ou mistral : API de Mistral au lieu de l'IA locale), secrets, legifrance,
# demarrer, n8n, demo, verifier, raccourci, sauvegarde-auto (en option). Chaque étape est idempotente et finit par « ETAPE <nom> : OK|ECHEC <code> ».
# --simulation affiche les commandes sans rien modifier ; --auto (raccourci) ajoute le démarrage à l'ouverture de
# session ; --palier (lmstudio) remplace le palier recommandé par le diagnostic.
# Les étapes mistral, legifrance et sauvegarde-auto posent des questions : l'utilisateur les lance lui-même dans son terminal
# (saisie masquée), jamais un assistant IA.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

AUTO=0
PALIER=""
ETAPE=""

usage() {
    cat <<'FIN'
Usage : install/installer.sh --etape <nom> [--simulation] [--auto] [--palier petit|moyen|grand]
Étapes : moteur, lmstudio (ou mistral), secrets, legifrance, demarrer, n8n, demo, verifier, raccourci, sauvegarde-auto.
FIN
}

# --- moteur : Docker ou Podman réutilisé ; sinon Podman (macOS) ou Docker Engine de la distribution (Linux) ------

lancer_root() {
    if [ "$(id -u)" = 0 ]; then
        lancer "$@"
    else
        lancer sudo "$@"
    fi
}

# apt, dnf ou aucun (fonction à part : les tests la remplacent).
gestionnaire_paquets() {
    if command -v apt-get >/dev/null 2>&1; then
        printf 'apt'
    elif command -v dnf >/dev/null 2>&1; then
        printf 'dnf'
    else
        printf 'aucun'
    fi
}

# Vrai si apt propose une version installable du paquet (« Candidate: » autre que « (none) »).
paquet_apt_disponible() {
    local candidat
    candidat="$(apt-cache policy "$1" 2>/dev/null | sed -n 's/^ *Candidate: *//p' | head -n 1 || true)"
    [ -n "$candidat" ] && [ "$candidat" != "(none)" ]
}

# Module Compose v2 proposé par la distribution : docker-compose-v2 (Ubuntu) ou docker-compose-plugin ; vide sinon.
paquet_compose_apt() {
    local paquet
    for paquet in docker-compose-v2 docker-compose-plugin; do
        if paquet_apt_disponible "$paquet"; then
            printf '%s' "$paquet"
            return 0
        fi
    done
}

paquet_dnf_disponible() {
    dnf -q list --available "$1" >/dev/null 2>&1 || dnf -q list --installed "$1" >/dev/null 2>&1
}

DOCKER_OFFICIEL="installez Docker Engine en suivant https://docs.docker.com/engine/install/ , puis relancez l'étape moteur."

# Paquet Linux de plus (fournisseur de « podman compose » pour un Podman déjà installé), avec apt-get ou dnf.
installer_paquet_linux() {
    case "$(gestionnaire_paquets)" in
        apt)
            lancer_root apt-get update >&2
            lancer_root apt-get install -y "$@" >&2
            ;;
        dnf) lancer_root dnf install -y "$@" >&2 ;;
        *)
            if [ "$SIMULATION" = 1 ]; then
                expliquer "selon la distribution : apt-get install -y $* ou dnf install -y $*"
                return 0
            fi
            echouer gestionnaire_de_paquets_absent "Ni apt-get ni dnf : installez $* avec le gestionnaire de paquets du système."
            ;;
    esac
}

# Docker Engine des paquets de la distribution (spécification, §3.1 : Docker Engine sous Linux). Jamais de script
# d'installation distant : sans paquet convenable, l'étape s'arrête et renvoie à la documentation officielle.
installer_docker_linux() {
    local paquet_compose=""
    case "$(gestionnaire_paquets)" in
        apt)
            phase mise_a_jour_des_paquets_impossible
            lancer_root apt-get update >&2
            if [ "$SIMULATION" = 1 ]; then
                expliquer "module Compose v2 : docker-compose-v2, sinon docker-compose-plugin, selon « apt-cache policy »"
                paquet_compose=docker-compose-v2
            else
                paquet_compose="$(paquet_compose_apt)"
                if ! paquet_apt_disponible docker.io || [ -z "$paquet_compose" ]; then
                    echouer moteur_paquet_indisponible "Paquets docker.io et module Compose v2 introuvables dans apt : $DOCKER_OFFICIEL"
                fi
            fi
            phase installation_docker_impossible
            lancer_root apt-get install -y docker.io "$paquet_compose" >&2
            ;;
        dnf)
            if [ "$SIMULATION" != 1 ] && { ! paquet_dnf_disponible moby-engine || ! paquet_dnf_disponible docker-compose; }; then
                echouer moteur_paquet_indisponible "Paquets moby-engine et docker-compose introuvables dans dnf : $DOCKER_OFFICIEL"
            fi
            phase installation_docker_impossible
            lancer_root dnf install -y moby-engine docker-compose >&2
            ;;
        *)
            if [ "$SIMULATION" = 1 ]; then
                expliquer "ni apt-get ni dnf : l'étape s'arrêterait (moteur_paquet_indisponible) et renverrait à https://docs.docker.com/engine/install/"
                return 0
            fi
            echouer moteur_paquet_indisponible "Ni apt-get ni dnf : $DOCKER_OFFICIEL"
            ;;
    esac
    phase demarrage_docker_impossible
    lancer_root systemctl enable --now docker >&2
}

# Sous Linux, Docker sans sudo : le compte est ajouté au groupe docker, ce qui donne des droits équivalents à ceux
# d'administrateur sur la machine. Ce choix permet aux sauvegardes planifiées (cron) de lancer Docker sans mot de passe.
# Le groupe ne vaut qu'après une nouvelle ouverture de session : l'étape s'arrête alors (reconnexion_requise) et se
# relance ensuite sans rien réinstaller.
assurer_groupe_docker() {
    local utilisateur
    if [ "$(id -u)" = 0 ]; then return 0; fi
    utilisateur="$(id -un)"
    if [ "$SIMULATION" = 1 ]; then
        expliquer "si le compte $utilisateur n'a pas encore accès à Docker : ajout au groupe docker (droits d'administrateur), puis nouvelle session"
        lancer_root usermod -aG docker "$utilisateur"
        return 0
    fi
    if avec_delai 30 docker info >/dev/null 2>&1; then return 0; fi
    if id -nG | tr ' ' '\n' | grep -x docker >/dev/null; then return 0; fi  # groupe déjà actif : Docker est seulement arrêté
    if ! id -nG "$utilisateur" | tr ' ' '\n' | grep -x docker >/dev/null; then
        phase groupe_docker_impossible
        lancer_root usermod -aG docker "$utilisateur" >&2
    fi
    echouer reconnexion_requise "Docker Engine est prêt et le compte $utilisateur fait partie du groupe docker (droits équivalents à ceux d'administrateur sur cette machine). Fermez votre session (ou redémarrez), puis relancez install/installer.sh --etape moteur."
}

exiger_homebrew() {
    if [ "$SIMULATION" != 1 ] && ! command -v brew >/dev/null 2>&1; then
        echouer homebrew_absent "$1"
    fi
}

installer_podman() {
    phase installation_podman_impossible
    exiger_homebrew "Homebrew (https://brew.sh) est requis pour installer Podman ; ou installez Podman Desktop (https://podman-desktop.io)."
    lancer brew install podman >&2
}

# Fournisseur de « podman compose » et, sur macOS, machine virtuelle Podman.
preparer_podman() {
    if [ "$SIMULATION" = 1 ] || ! podman compose version >/dev/null 2>&1; then
        expliquer "si « podman compose » n'a pas encore de fournisseur Compose :"
        phase compose_absent
        case "$(systeme)" in
            macos) lancer brew install docker-compose >&2 ;;
            linux) installer_paquet_linux podman-compose ;;
        esac
    fi
    if [ "$(systeme)" = macos ]; then
        if [ "$SIMULATION" = 1 ] || [ -z "$(podman machine list --format '{{.Name}}' 2>/dev/null || true)" ]; then
            expliquer "si aucune machine Podman n'existe encore :"
            phase machine_podman_impossible
            lancer_detache podman machine init
        fi
    fi
}

etape_moteur() {
    local present
    present="$(moteur_present)"
    if [ "$present" = aucun ]; then
        case "$(systeme)" in
            macos)
                installer_podman
                present=podman
                ;;
            linux)
                installer_docker_linux
                present=docker
                ;;
            *) echouer systeme_non_pris_en_charge "Système non pris en charge par ce script (Windows : installer.ps1)." ;;
        esac
    else
        info "Moteur de conteneurs déjà installé : $present (réutilisé)."
        expliquer "moteur de conteneurs déjà installé : $present (réutilisé, rien à installer)"
    fi
    if [ "$present" = podman ]; then preparer_podman; fi
    if [ "$present" = docker ] && [ "$(systeme)" = linux ]; then assurer_groupe_docker; fi
    assurer_moteur_actif
    if [ "$MOTEUR" = docker ] && [ "$SIMULATION" != 1 ] \
        && avec_delai 10 docker version --format '{{.Server.Platform.Name}}' 2>/dev/null | grep 'Docker Desktop' >/dev/null; then
        info "Docker Desktop : son abonnement est payant pour les administrations ; Podman est l'option par défaut."
    fi
    if ! lancer "$MOTEUR" compose version >&2; then
        echouer compose_absent "« $MOTEUR compose » est indisponible : installez le module Compose (docker-compose-plugin pour Docker ; docker-compose ou podman-compose pour Podman)."
    fi
}

# --- lmstudio : LM Studio, modèle du palier, écoute limitée, journalisation coupée, compose.env -----------------

installer_lmstudio() {
    local installateur
    phase installation_lmstudio_impossible
    case "$(systeme)" in
        macos)
            exiger_homebrew "Homebrew (https://brew.sh) est requis ; ou installez LM Studio depuis https://lmstudio.ai/download."
            # La commande lms est livrée dans l'application (Contents/Resources/app/.webpack/lms) : chemin_lms la trouve.
            lancer brew install --cask lm-studio >&2
            ;;
        linux)
            # Installateur officiel de llmster (LM Studio sans interface), documenté sur lmstudio.ai ; téléchargé
            # entièrement avant d'être exécuté.
            if [ "$SIMULATION" = 1 ]; then
                lancer curl -fsSL -o /tmp/lmstudio-install.sh https://lmstudio.ai/install.sh
                lancer sh /tmp/lmstudio-install.sh
            else
                installateur="$(mktemp "${TMPDIR:-/tmp}/lmstudio-install.XXXXXX")"
                curl -fsSL -o "$installateur" https://lmstudio.ai/install.sh
                sh "$installateur" </dev/null >&2
                rm -f "$installateur"
            fi
            ;;
        *) echouer systeme_non_pris_en_charge "Système non pris en charge par ce script (Windows : installer.ps1)." ;;
    esac
}

# Dossier de LM Studio confirmé (pointeur ~/.lmstudio-home-pointer ou emplacement documenté existant), sinon échec :
# jamais de réglage écrit ni de journal cherché dans un dossier que LM Studio n'utilise pas.
exiger_dossier_lmstudio() {
    DOSSIER_LMS="$(dossier_lmstudio)"
    if [ -n "$DOSSIER_LMS" ]; then return 0; fi
    if [ "$SIMULATION" = 1 ]; then
        DOSSIER_LMS="<dossier de LM Studio : ~/.lmstudio-home-pointer, sinon ~/.lmstudio>"
        return 0
    fi
    echouer lmstudio_dossier_introuvable "Dossier de LM Studio introuvable (ni ~/.lmstudio-home-pointer valide, ni ~/.lmstudio, ni ~/.cache/lm-studio) : ouvrez LM Studio une première fois, puis relancez l'étape."
}

# Réglages du serveur LM Studio (relevés dans l'application : clés de http-server-config.json) : écoute sur l'adresse
# choisie (networkInterface), aucun fichier de journal (fileLoggingMode=off), pas de journal détaillé ni des jetons
# générés, contenu masqué (logSensitiveData=false, c'est-à-dire « Redact Content » activé). Serveur arrêté pendant le
# réglage ; le fichier est cherché dans le dossier confirmé de LM Studio (exiger_dossier_lmstudio).
regler_journalisation_lms() {
    local adresse="$1" fichier="$DOSSIER_LMS/.internal/http-server-config.json"
    if [ "$SIMULATION" = 1 ]; then
        expliquer "réglages du serveur dans $fichier : networkInterface=$adresse, fileLoggingMode=off, verbose=false, logIncomingTokens=false, logSensitiveData=false"
        return 0
    fi
    phase journalisation_non_reglee
    if [ ! -f "$fichier" ]; then
        mkdir -p "$DOSSIER_LMS/.internal"
        # Réglages par défaut de LM Studio (emptyHttpServerSettings de l'application), journalisation coupée.
        printf '{"autoStartOnLaunch":false,"port":1234,"cors":false,"logSensitiveData":false,"logIncomingTokens":false,"verbose":false,"logLinesLimit":500,"networkInterface":"%s","justInTimeModelLoading":true,"fileLoggingMode":"off"}\n' "$adresse" >"$fichier"
        return 0
    fi
    if [ "$(systeme)" = macos ]; then
        plutil -replace networkInterface -string "$adresse" "$fichier"
        plutil -replace fileLoggingMode -string off "$fichier"
        plutil -replace verbose -bool NO "$fichier"
        plutil -replace logIncomingTokens -bool NO "$fichier"
        plutil -replace logSensitiveData -bool NO "$fichier"
    else
        if ! command -v python3 >/dev/null 2>&1; then
            echouer python3_absent "python3 est requis pour régler LM Studio (ou réglez-le dans son interface)."
        fi
        python3 -I -c '
import json, os, sys
chemin = sys.argv[1]
with open(chemin, encoding="utf-8") as f:
    reglages = json.load(f)
reglages.update(networkInterface=sys.argv[2], fileLoggingMode="off", verbose=False, logIncomingTokens=False,
                logSensitiveData=False)
with open(chemin + ".tmp", "w", encoding="utf-8") as f:
    json.dump(reglages, f, indent=2)
os.replace(chemin + ".tmp", chemin)
' "$fichier" "$adresse"
    fi
}

verifier_modele_servi() {
    local base="$1" cle="$2" modeles
    if [ "$SIMULATION" = 1 ]; then
        lancer curl -s "$base/models"
        expliquer "le modèle $cle doit figurer dans la réponse"
        return 0
    fi
    phase lmstudio_injoignable
    modeles="$(curl -s --max-time 30 "$base/models")"
    case "$modeles" in
        *"\"$cle\""*) ;;
        *) echouer modele_introuvable "Le modèle $cle n'apparaît pas dans $base/models." ;;
    esac
}

# Critère d'acceptation : LM Studio répond depuis un conteneur (même adresse que Territoria).
verifier_acces_conteneur() {
    local url="$1"
    local hote=()
    if [ "$MOTEUR" = docker ]; then hote=(--add-host host.docker.internal:host-gateway); fi
    phase lmstudio_injoignable_depuis_conteneur
    moteur run --rm --user 10001:10001 --read-only ${hote[@]+"${hote[@]}"} "$IMAGE_PYTHON" python -c '
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + "/models", timeout=15).read()
print("LM Studio joignable depuis un conteneur :", sys.argv[1])
' "$url" >&2
}

# Une requête de test contenant un marqueur ne doit apparaître dans aucun journal de LM Studio (seul le nombre de
# fichiers concernés est imprimé, jamais leur contenu).
verifier_journaux_lms() {
    local base="$1" cle="$2" marqueur corps trouves=0 journaux="$DOSSIER_LMS/server-logs"
    marqueur="marqueur-territoria-$(date +%s)-$$"
    corps="{\"model\":\"$cle\",\"messages\":[{\"role\":\"user\",\"content\":\"Répondez seulement OK. $marqueur\"}],\"max_tokens\":8,\"stream\":false}"
    if [ "$SIMULATION" = 1 ]; then
        lancer curl -s -H 'Content-Type: application/json' -d "$corps" "$base/chat/completions"
        expliquer "le marqueur ne doit apparaître dans aucun fichier de $journaux"
        return 0
    fi
    phase requete_de_test_impossible
    curl -s --fail --max-time 600 -H 'Content-Type: application/json' -d "$corps" "$base/chat/completions" >/dev/null
    sleep 2
    if [ ! -d "$journaux" ]; then
        # Sans ce dossier, aucun contrôle n'a eu lieu : on ne conclut pas à l'absence de journal.
        echouer journaux_lmstudio_introuvables "Contrôle impossible : le dossier des journaux de LM Studio ($journaux) est introuvable après la requête de test. Vérifiez que File Logging Mode est sur Off (Developer > Server Settings) ; si LM Studio n'a encore jamais écrit de journal, créez ce dossier vide, puis relancez l'étape."
    fi
    trouves="$({ grep -rlF "$marqueur" "$journaux" 2>/dev/null || true; } | wc -l | tr -d ' ')"
    info "fichiers de journal de LM Studio contenant la requête de test : $trouves"
    if [ "$trouves" != 0 ]; then
        echouer journalisation_active "La requête de test apparaît dans les journaux de LM Studio. Dans LM Studio (Developer > Server Settings) : File Logging Mode sur Off, Verbose Logging et Log Incoming Tokens décochés, Redact Content coché ; puis relancez l'étape."
    fi
}

etape_lmstudio() {
    local palier id cle lms adresse url base
    exiger_moteur
    verifier_dossier_donnees
    palier="${PALIER:-$(palier_modele "$(memoire_go)")}"
    id="$(modele_du_palier "$palier")"
    if [ -z "$id" ]; then echouer modele_inconnu "Aucun modèle pour le palier $palier dans install/modeles.json."; fi
    cle="${id%@*}"  # identifiant du modèle dans l'API (TERRITORIA_MODELE)
    info "Palier $palier : modèle $id."
    lms="$(chemin_lms)"
    if [ -z "$lms" ]; then
        installer_lmstudio
        lms="$(chemin_lms)"
        if [ -z "$lms" ]; then
            if [ "$SIMULATION" != 1 ]; then echouer lms_absent "Commande lms introuvable après l'installation de LM Studio."; fi
            lms=lms
        fi
    fi
    exiger_dossier_lmstudio
    adresse="$(adresse_ecoute_lms)"
    if [ -z "$adresse" ]; then echouer passerelle_introuvable "Passerelle du pont docker0 introuvable (Docker démarré ?)."; fi
    url="$(url_lms_conteneurs)"
    base="http://$adresse:$PORT_LMS/v1"

    # 1. Modèle du palier (GGUF Q4_K_M), téléchargé une seule fois.
    if [ "$SIMULATION" = 1 ] || ! "$lms" ls --json 2>/dev/null | grep -F "\"$id\"" >/dev/null; then
        phase telechargement_impossible
        lancer "$lms" get "$id" --gguf --yes >&2
    fi
    # 2. Journalisation coupée, serveur arrêté pendant le réglage.
    if [ "$(systeme)" = linux ]; then
        phase lmstudio_demarrage_impossible
        lancer_detache "$lms" daemon up
    fi
    if [ "$SIMULATION" = 1 ] || lms_en_marche "$lms"; then
        phase lmstudio_arret_impossible
        lancer_detache "$lms" server stop
    fi
    regler_journalisation_lms "$adresse"
    # 3. Serveur sur l'adresse d'écoute limitée (docs/acces-lmstudio.md), contrôlée sur l'écoute réelle.
    phase lmstudio_demarrage_impossible
    lancer_detache "$lms" server start --port "$PORT_LMS" --bind "$adresse"
    verifier_ecoute_lms "$adresse"
    # 4. Modèle chargé sous un identifiant stable, celui que Territoria demande.
    charger_modele_lms "$lms" "$id" "$cle"
    # 5. Réglages de l'IA pour Compose (jamais dans secrets/territoria.env).
    creer_dossier "$TERRITORIA_DONNEES_HOTE" 700
    creer_dossier "$S" 700
    ecrire_variable "$S/compose.env" TERRITORIA_LLM_URL "$url"
    ecrire_variable "$S/compose.env" TERRITORIA_MODELE "$cle"
    # 6. Contrôles : modèle servi, joignable depuis un conteneur, absent des journaux.
    verifier_modele_servi "$base" "$cle"
    verifier_acces_conteneur "$url"
    verifier_journaux_lms "$base" "$cle"
    retirer_mistral
}

# Retour à l'IA locale : l'option Mistral de l'étape mistral est retirée (une valeur vide vaut absence pour config.py).
retirer_mistral() {
    if variable_renseignee "$S/compose.env" TERRITORIA_IA_EN_LIGNE; then
        ecrire_variable "$S/compose.env" TERRITORIA_IA_EN_LIGNE ""
    fi
    if variable_renseignee "$S/territoria.env" TERRITORIA_MISTRAL_CLE; then
        ecrire_variable "$S/territoria.env" TERRITORIA_MISTRAL_CLE "" litteral,secret
    fi
}

# --- mistral : l'API de Mistral au lieu de l'IA locale (docs/mistral.md) ----------------------------------------------

AVERTISSEMENT_MISTRAL="Avec l'API de Mistral, ces textes partent chez Mistral, hors de ce poste : les questions des agents et les
extraits de documents retenus, tous les extraits des documents importés (recherche par le sens), les données et projets
d'arrêtés, de délibérations et de courriers, les passages des séances pour le procès-verbal. Ne partent jamais : le son
des séances, les comptes, les journaux, les sauvegardes. Avant d'accepter : contrat de sous-traitance (DPA) de Mistral,
inscription au registre des traitements, avis du DPO (docs/mistral.md)."

etape_mistral() {
    local reponse="" cle=""
    verifier_dossier_donnees
    if [ "$SIMULATION" = 1 ]; then
        expliquer "avertissement : ce qui part chez Mistral, démarches RGPD (docs/mistral.md)"
        expliquer "question dans le terminal de l'utilisateur : utiliser l'API de Mistral au lieu de l'IA locale ? [o/N]"
        expliquer "si oui : saisie masquée de la clé API"
        ecrire_variable "$S/territoria.env" TERRITORIA_MISTRAL_CLE "" litteral,secret
        ecrire_variable "$S/compose.env" TERRITORIA_IA_EN_LIGNE mistral
        return 0
    fi
    if [ "$(lire_variable "$S/compose.env" TERRITORIA_IA_EN_LIGNE)" = mistral ] \
        && variable_renseignee "$S/territoria.env" TERRITORIA_MISTRAL_CLE; then
        info "API de Mistral déjà choisie, clé enregistrée dans $S/territoria.env : conservées."
        return 0
    fi
    exiger_terminal
    info "$AVERTISSEMENT_MISTRAL"
    lire_reponse reponse "Utiliser l'API de Mistral au lieu de l'IA locale ? [o/N] "
    if ! repondu_oui "$reponse"; then
        info "IA locale conservée : rien n'est envoyé à Mistral. Pour l'installer : install/installer.sh --etape lmstudio."
        return 0
    fi
    lire_secret cle "Clé API de Mistral (saisie masquée) : "
    if [ -z "$cle" ]; then echouer saisie_vide "Clé vide : rien n'a été enregistré."; fi
    creer_dossier "$TERRITORIA_DONNEES_HOTE" 700
    creer_dossier "$S" 700
    ecrire_variable "$S/territoria.env" TERRITORIA_MISTRAL_CLE "$cle" litteral,secret
    cle=""
    ecrire_variable "$S/compose.env" TERRITORIA_IA_EN_LIGNE mistral
    info "API de Mistral choisie ; clé enregistrée dans $S/territoria.env (0600), jamais affichée. Elle sert au prochain démarrage."
}

# --- secrets, legifrance --------------------------------------------------------------------------------------------

QUESTION_CONNECTEURS="Autoriser les connecteurs publics (Légifrance, data.gouv.fr), à activer ensuite par l'administration ? [o/N]"

etape_secrets() {
    exiger_moteur
    preparer_dossiers
    phase construction_impossible
    compose build territoria >&2
    outils_jetable secrets
}

etape_legifrance() {
    local reponse=""
    verifier_dossier_donnees
    if [ "$SIMULATION" = 1 ]; then
        expliquer "identifiants PISTE d'une ancienne installation : retirés de territoria.env, que plus rien ne lit"
        retirer_variable "$S/territoria.env" PISTE_CLIENT_ID
        retirer_variable "$S/territoria.env" PISTE_CLIENT_SECRET
        expliquer "question dans le terminal de l'utilisateur : $QUESTION_CONNECTEURS"
        expliquer "si oui : connecteurs autorisés, à activer dans Administration › Connecteurs"
        ecrire_variable "$S/compose.env" TERRITORIA_HORS_LIGNE 0
        expliquer "si non : mode hors ligne"
        ecrire_variable "$S/compose.env" TERRITORIA_HORS_LIGNE 1
        return 0
    fi
    exiger_terminal
    creer_dossier "$TERRITORIA_DONNEES_HOTE" 700
    creer_dossier "$S" 700
    # Ancienne étape 4 : identifiants PISTE rangés ici, que plus rien ne lit (le connecteur a les siens).
    if variable_renseignee "$S/territoria.env" PISTE_CLIENT_ID || variable_renseignee "$S/territoria.env" PISTE_CLIENT_SECRET; then
        retirer_variable "$S/territoria.env" PISTE_CLIENT_ID
        retirer_variable "$S/territoria.env" PISTE_CLIENT_SECRET
        info "Identifiants PISTE retirés de $S/territoria.env : TerritorIA ne les lit plus. Pour Légifrance, saisissez-les dans Administration › Connecteurs."
    fi
    lire_reponse reponse "$QUESTION_CONNECTEURS "
    if ! repondu_oui "$reponse"; then
        ecrire_variable "$S/compose.env" TERRITORIA_HORS_LIGNE 1
        info "Mode hors ligne : TERRITORIA_HORS_LIGNE=1 dans $S/compose.env (connecteurs coupés ; les textes déjà en cache restent utilisables)."
        return 0
    fi
    ecrire_variable "$S/compose.env" TERRITORIA_HORS_LIGNE 0
    info "Connecteurs autorisés : TERRITORIA_HORS_LIGNE=0 dans $S/compose.env. Ils restent désactivés jusqu'à leur activation dans Administration › Connecteurs."
}

# --- demarrer, n8n, demo, verifier ----------------------------------------------------------------------------------

etape_demarrer() {
    assurer_moteur_actif
    demarrer_lmstudio
    demarrer_services construire
}

etape_n8n() {
    exiger_moteur
    exiger_secrets
    provisionner_n8n
    phase demarrage_impossible
    compose up -d >&2
    attendre_sain n8n
    attendre_sain territoria
    outils_jetable proprietaire-n8n
}

# `outils demo` refuse d'installer le corpus fictif par-dessus une bibliothèque réelle (bibliotheque_etrangere).
etape_demo() {
    exiger_moteur
    outils_service demo
}

# Sur une installation réelle, verifier tourne sans --strict (un vrai modèle n'a pas à citer [1]).
etape_verifier() {
    exiger_moteur
    outils_service verifier
}

# --- raccourci ------------------------------------------------------------------------------------------------------

raccourci_macos() {
    local demarrer="$1" bureau="$HOME/Desktop" agents="$HOME/Library/LaunchAgents"
    creer_dossier "$bureau" 755
    ecrire_fichier "$bureau/Territoria.command" 755 <<FIN
#!/bin/bash
# Raccourci Territoria : démarre Territoria (moteur de conteneurs, LM Studio, services) puis ouvre l'interface.
exec $(printf '%q' "$demarrer") --ouvrir
FIN
    if [ "$AUTO" = 1 ]; then
        creer_dossier "$agents" 755
        ecrire_fichier "$agents/fr.territoria.demarrer.plist" 644 <<FIN
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>fr.territoria.demarrer</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(echapper_xml "$demarrer")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TERRITORIA_DONNEES_HOTE</key><string>$(echapper_xml "$TERRITORIA_DONNEES_HOTE")</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$(echapper_xml "$TERRITORIA_DONNEES_HOTE/demarrage-auto.log")</string>
  <key>StandardErrorPath</key><string>$(echapper_xml "$TERRITORIA_DONNEES_HOTE/demarrage-auto.log")</string>
</dict>
</plist>
FIN
        info "Démarrage à l'ouverture de session : $agents/fr.territoria.demarrer.plist (actif à la prochaine session)."
    fi
}

# Les entrées .desktop citent le chemin entre guillemets ; on refuse les caractères qu'il faudrait y échapper.
chemin_pour_desktop() {
    case "$1" in
        *'"'* | *'`'* | *'$'* | *\\* | *'%'* | *"$NL"*)
            echouer chemin_non_pris_en_charge "Le chemin du dépôt contient un caractère non pris en charge par les raccourcis Linux." ;;
    esac
}

raccourci_linux() {
    local demarrer="$1" bureau="" autostart="$HOME/.config/autostart"
    chemin_pour_desktop "$demarrer"
    if command -v xdg-user-dir >/dev/null 2>&1; then bureau="$(xdg-user-dir DESKTOP 2>/dev/null || true)"; fi
    if [ -z "$bureau" ] || [ "$bureau" = "$HOME" ]; then bureau="$HOME/Desktop"; fi
    creer_dossier "$bureau" 755
    ecrire_fichier "$bureau/territoria.desktop" 755 <<FIN
[Desktop Entry]
Type=Application
Name=Territoria
Comment=Démarre Territoria puis ouvre l'interface dans le navigateur
Exec="$demarrer" --ouvrir
Terminal=true
Categories=Office;
FIN
    if [ "$SIMULATION" != 1 ] && command -v gio >/dev/null 2>&1; then
        gio set "$bureau/territoria.desktop" metadata::trusted true 2>/dev/null || true  # GNOME : lancement autorisé
    fi
    if [ "$AUTO" = 1 ]; then
        creer_dossier "$autostart" 755
        ecrire_fichier "$autostart/territoria.desktop" 644 <<FIN
[Desktop Entry]
Type=Application
Name=Territoria (démarrage)
Exec="$demarrer"
Terminal=false
X-GNOME-Autostart-enabled=true
FIN
        info "Démarrage à l'ouverture de session : $autostart/territoria.desktop."
    fi
}

etape_raccourci() {
    case "$(systeme)" in
        macos) raccourci_macos "$RACINE/install/demarrer.sh" ;;
        linux) raccourci_linux "$RACINE/install/demarrer.sh" ;;
        *) echouer systeme_non_pris_en_charge "Système non pris en charge par ce script (Windows : installer.ps1)." ;;
    esac
}

# --- sauvegarde-auto : mot de passe (0600), destination, planification quotidienne à 12 h 30 ----------------------

ecrire_mot_de_passe() {
    local temporaire="$FICHIER_MOT_DE_PASSE.tmp.$$"
    if ! (umask 077 && printf '%s\n' "$1" >"$temporaire" && chmod 600 "$temporaire" && mv -f "$temporaire" "$FICHIER_MOT_DE_PASSE"); then
        rm -f "$temporaire"
        echouer ecriture_impossible "Écriture impossible dans $FICHIER_MOT_DE_PASSE."
    fi
}

# PATH de la tâche planifiée : dossier du moteur de conteneurs et emplacements habituels.
chemin_tache() {
    local moteur_trouve dossier=""
    moteur_trouve="$(command -v docker 2>/dev/null || command -v podman 2>/dev/null || true)"
    if [ -n "$moteur_trouve" ]; then dossier="$(dirname "$moteur_trouve"):"; fi
    printf '%s/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin' "$dossier"
}

planifier_macos() {
    local destination="$1" agents="$HOME/Library/LaunchAgents" plist journal
    plist="$agents/fr.territoria.sauvegarde.plist"
    journal="$TERRITORIA_DONNEES_HOTE/sauvegarde-auto.log"
    creer_dossier "$agents" 755
    ecrire_fichier "$plist" 644 <<FIN
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>fr.territoria.sauvegarde</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(echapper_xml "$RACINE/install/sauvegarder.sh")</string>
    <string>--auto</string>
    <string>--destination</string>
    <string>$(echapper_xml "$destination")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(echapper_xml "$(chemin_tache)")</string>
    <key>TERRITORIA_DONNEES_HOTE</key><string>$(echapper_xml "$TERRITORIA_DONNEES_HOTE")</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>12</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
  <key>StandardOutPath</key><string>$(echapper_xml "$journal")</string>
  <key>StandardErrorPath</key><string>$(echapper_xml "$journal")</string>
</dict>
</plist>
FIN
    phase planification_impossible
    lancer launchctl bootout "gui/$(id -u)/fr.territoria.sauvegarde" 2>/dev/null || true
    lancer launchctl bootstrap "gui/$(id -u)" "$plist"
}

planifier_linux() {
    local destination="$1" valeur ligne marque="# territoria-sauvegarde-auto"
    for valeur in "$destination" "$RACINE" "$TERRITORIA_DONNEES_HOTE"; do
        case "$valeur" in
            *"'"* | *%* | *"$NL"*) echouer chemin_non_pris_en_charge "Chemin non pris en charge par cron (apostrophe, % ou fin de ligne)." ;;
        esac
    done
    ligne="30 12 * * * PATH='$(chemin_tache)' TERRITORIA_DONNEES_HOTE='$TERRITORIA_DONNEES_HOTE' '$RACINE/install/sauvegarder.sh' --auto --destination '$destination' >>'$TERRITORIA_DONNEES_HOTE/sauvegarde-auto.log' 2>&1 $marque"
    if [ "$SIMULATION" = 1 ]; then
        printf '+ crontab - (la ligne marquée « %s » est remplacée par) :\n|   %s\n' "$marque" "$ligne" >&9
        return 0
    fi
    phase planification_impossible
    { { crontab -l 2>/dev/null || true; } | { grep -vF "$marque" || true; }; printf '%s\n' "$ligne"; } | crontab -
}

etape_sauvegarde_auto() {
    local destination="" motdepasse="" confirmation=""
    verifier_dossier_donnees
    if [ "$SIMULATION" = 1 ]; then
        expliquer "question : dossier de destination des sauvegardes (disque externe, serveur de fichiers interne), déjà existant"
        if [ -s "$FICHIER_MOT_DE_PASSE" ]; then
            expliquer "mot de passe de sauvegarde déjà enregistré : conservé"
        else
            expliquer "saisie masquée du mot de passe de sauvegarde (deux fois, 12 caractères au moins)"
            printf '+ écrire le mot de passe (saisie masquée) dans %q (0600)\n' "$FICHIER_MOT_DE_PASSE" >&9
        fi
        expliquer "planification quotidienne à 12 h 30 : sauvegarder.sh --auto (sauvegarde puis élagage)"
        destination="/chemin/de/la/destination"
    else
        exiger_terminal
        if [ ! -d "$S" ]; then echouer secrets_absents "Lancez d'abord install/installer.sh --etape secrets."; fi
        lire_reponse destination "Dossier de destination des sauvegardes (chemin absolu d'un dossier existant) : "
        verifier_destination "$destination"
        if [ -s "$FICHIER_MOT_DE_PASSE" ]; then
            info "Mot de passe de sauvegarde déjà enregistré ($FICHIER_MOT_DE_PASSE) : conservé."
        else
            lire_secret motdepasse "Mot de passe des sauvegardes (saisie masquée, 12 caractères au moins) : "
            lire_secret confirmation "Confirmez le mot de passe : "
            if [ "${#motdepasse}" -lt 12 ]; then echouer mot_de_passe_trop_court "Le mot de passe doit compter au moins 12 caractères."; fi
            if [ "$motdepasse" != "$confirmation" ]; then echouer saisies_differentes "Les deux saisies diffèrent : rien n'a été enregistré."; fi
            confirmation=""
            ecrire_mot_de_passe "$motdepasse"
            motdepasse=""
            info "Mot de passe enregistré dans $FICHIER_MOT_DE_PASSE (0600). Gardez-en une copie ailleurs : sans lui, les sauvegardes sont illisibles."
        fi
    fi
    case "$(systeme)" in
        macos) planifier_macos "$destination" ;;
        linux) planifier_linux "$destination" ;;
        *) echouer systeme_non_pris_en_charge "Système non pris en charge par ce script (Windows : installer.ps1)." ;;
    esac
    if [ "$SIMULATION" != 1 ]; then
        info "Sauvegarde planifiée tous les jours à 12 h 30 vers $destination (journal : $TERRITORIA_DONNEES_HOTE/sauvegarde-auto.log)."
    fi
}

# --- Arguments et lancement -----------------------------------------------------------------------------------------

# Lancé seulement quand le script est exécuté : les tests le sourcent pour appeler ses fonctions.
principal() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --etape)
                if [ "$#" -lt 2 ]; then refuser_arguments installer; fi
                ETAPE="$2"
                shift 2
                ;;
            --simulation) activer_simulation; shift ;;
            --auto) AUTO=1; shift ;;
            --palier)
                if [ "$#" -lt 2 ]; then refuser_arguments installer; fi
                PALIER="$2"
                shift 2
                ;;
            -h | --help) usage; exit 0 ;;
            *) refuser_arguments installer ;;
        esac
    done

    case "$ETAPE" in
        moteur | lmstudio | mistral | secrets | legifrance | demarrer | n8n | demo | verifier | raccourci | sauvegarde-auto) ;;
        '') refuser_arguments installer ;;
        *) refuser_arguments installer etape_inconnue ;;
    esac
    if [ "$AUTO" = 1 ] && [ "$ETAPE" != raccourci ]; then refuser_arguments "$ETAPE"; fi
    case "$PALIER" in
        '') ;;
        petit | moyen | grand) if [ "$ETAPE" != lmstudio ]; then refuser_arguments "$ETAPE"; fi ;;
        *) refuser_arguments "$ETAPE" palier_inconnu ;;
    esac

    debut_etape "$ETAPE"
    expliquer "simulation de l'étape $ETAPE : rien n'est modifié ; commandes qui seraient lancées :"
    case "$ETAPE" in
        moteur) etape_moteur ;;
        lmstudio) etape_lmstudio ;;
        mistral) etape_mistral ;;
        secrets) etape_secrets ;;
        legifrance) etape_legifrance ;;
        demarrer) etape_demarrer ;;
        n8n) etape_n8n ;;
        demo) etape_demo ;;
        verifier) etape_verifier ;;
        raccourci) etape_raccourci ;;
        sauvegarde-auto) etape_sauvegarde_auto ;;
    esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    principal "$@"
fi
