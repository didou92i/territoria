#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Met à jour Territoria vers une version du dépôt (étiquette, branche ou commit), avec retour automatique en cas d'échec :
#   install/mettre-a-jour.sh --version <ref git> [--destination <dossier>] [--simulation]
#
# Avant tout changement, la mise à jour refuse de démarrer si le dépôt a des modifications locales (fichiers suivis ou non
# suivis, hors fichiers ignorés : .gitignore écarte ceux que le système crée tout seul, .DS_Store, ._*, Thumbs.db,
# desktop.ini), en nommant les fichiers en cause, ou si la version demandée n'existe pas après `git fetch --tags`. Sans réseau, un échec de
# `git fetch` est toléré seulement si la version est déjà connue localement (avertissement, puis la mise à jour continue).
# Ces contrôles passent avant la sauvegarde (le brief place `git fetch` après) : un refus ne laisse ainsi rien derrière lui.
#
# Séquence : version actuelle notée (git rev-parse HEAD, et sa branche) ; moteur et LM Studio démarrés comme le fait
# install/demarrer.sh (modèle de LM Studio chargé compris) ; sauvegarde chiffrée dans territoria-donnees/mises-a-jour/
# (ou dans --destination), aussitôt vérifiée (déchiffrement complet) ; passage à la version (git checkout de son
# commit) ; reconstruction ; credentials et workflows de la nouvelle version importés dans n8n (provisionner_n8n) ;
# redémarrage ; `outils verifier`. Le dossier mises-a-jour/ n'entre dans aucune sauvegarde : seuls territoria/, n8n/ et
# secrets/ sont archivés (app/sauvegarde.py, RACINES).
#
# En cas d'échec : arrêt des conteneurs ; territoria/ et n8n/ déplacés dans territoria-donnees/echec-<horodatage>/
# (0700, conservé, jamais supprimé automatiquement) ; restauration de la sauvegarde, avec l'image de la version qui l'a
# faite (gardée par l'étiquette <projet>-territoria:avant-mise-a-jour, retirée à la fin) ; retour à l'ancienne version
# (sa branche, s'il y en avait une) ; reconstruction ; redémarrage ; `outils verifier` ;
# puis code 1 et le message « Mise à jour annulée : version précédente rétablie. » (dernière ligne de la sortie d'erreur).
# Si le retour échoue à son tour, rien n'est réessayé : un message dit où sont les données et la sauvegarde, et qu'une
# intervention humaine est nécessaire (restauration_interrompue : les données d'avant la restauration sont gardées dans
# territoria-donnees/.restauration-*).
#
# Mot de passe de la sauvegarde : secrets/sauvegarde.motdepasse s'il existe, sinon saisi deux fois, masqué, au début, et
# gardé en mémoire pour la restauration éventuelle (jamais en argument, jamais écrit, jamais affiché).
# Dernière ligne de la sortie standard : « ETAPE mettre-a-jour : OK|ECHEC <code> ».
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=commun.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/commun.sh"

MESSAGE_ANNULATION="Mise à jour annulée : version précédente rétablie."
DELAI_RECUPERATION=120  # secondes au plus pour `git fetch --tags`

VERSION=""
DESTINATION=""
MODE_MOT_DE_PASSE=""   # auto (fichier sauvegarde.motdepasse) ou memoire (saisi au début)
ANCIEN_COMMIT=""
ANCIENNE_BRANCHE=""
COMMIT_CIBLE=""
ARCHIVE=""
IMAGE_AVANT=""         # étiquette de l'image territoria de la version qui fait la sauvegarde : elle sert à la restaurer
DOSSIER_ECHEC=""
CODE_MISE_A_JOUR=""
ETAT=preparation       # preparation, mise_a_jour, retour, termine
ETAPE_RETOUR=""        # arret, deplacement, restauration, version, demarrage, verification

# --- Fin du script : consignes quand la mise à jour ou le retour s'arrête en route ------------------------------------

# shellcheck disable=SC2329  # appelée par le piège de sortie
fin_mise_a_jour() {
    local statut=$?
    set +e
    if [ "$statut" -ne 0 ]; then
        case "$ETAT" in
            mise_a_jour) consigne_interruption ;;
            retour) consigne_retour_impossible ;;
        esac
    fi
    motdepasse=""
    (exit "$statut")
    terminer
}

version_precedente() {
    printf '%s' "$ANCIEN_COMMIT${ANCIENNE_BRANCHE:+ (branche $ANCIENNE_BRANCHE)}"
}

# Arrêt non rattrapé pendant la mise à jour elle-même (interruption) : aucun retour n'a été fait. Les trois commandes sont
# à recopier telles quelles, une par ligne : chemins absolus et protégés (citer), même avec des espaces ou une apostrophe.
# shellcheck disable=SC2329  # appelée par le piège de sortie
consigne_interruption() {
    info "Mise à jour interrompue ($(code_propre "$CODE_ECHEC")) : aucun retour automatique n'a été fait."
    info "Version précédente : $(version_precedente). Sauvegarde d'avant la mise à jour : $ARCHIVE"
    info "Pour revenir en arrière, lancez ces trois commandes l'une après l'autre (la restauration demande le mot de passe de la sauvegarde) :"
    info "  git -C $(citer "$RACINE") checkout $(citer "${ANCIENNE_BRANCHE:-$ANCIEN_COMMIT}")"
    info "  $(citer "$RACINE/install/restaurer.sh") --archive $(citer "$ARCHIVE") --forcer"
    info "  $(citer "$RACINE/install/demarrer.sh")"
}

# Le retour à la version précédente a échoué : rien n'est réessayé, une personne doit intervenir.
# shellcheck disable=SC2329  # appelée par le piège de sortie
consigne_retour_impossible() {
    local dossier restes=""
    if [ "$CODE_ECHEC" = restauration_interrompue ]; then
        for dossier in "$TERRITORIA_DONNEES_HOTE"/.restauration-*; do
            if [ -e "$dossier" ]; then restes="$restes $dossier"; fi
        done
        info "Restauration interrompue pendant le retour à la version précédente : rien n'a été supprimé et rien ne sera réessayé."
        info "Les données présentes avant la restauration sont gardées dans :${restes:- territoria-donnees/.restauration-* (introuvable)}"
    else
        info "Retour à la version précédente impossible (étape $ETAPE_RETOUR : $(code_propre "$CODE_ECHEC"))."
        CODE_ECHEC=retour_impossible
    fi
    if [ -n "$DOSSIER_ECHEC" ]; then
        info "Les données de la tentative ratée sont dans : $DOSSIER_ECHEC"
    fi
    info "Sauvegarde d'avant la mise à jour : $ARCHIVE ; version précédente : $(version_precedente)."
    info "Une intervention humaine est nécessaire : ne relancez ni la mise à jour ni la restauration avant d'avoir examiné ces dossiers."
}

# --- Préparation : rien n'est modifié tant qu'un contrôle échoue -----------------------------------------------------

lire_arguments() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                if [ "$#" -lt 2 ]; then refuser_arguments mettre-a-jour; fi
                VERSION="$2"
                shift 2
                ;;
            --destination)
                if [ "$#" -lt 2 ]; then refuser_arguments mettre-a-jour; fi
                DESTINATION="$2"
                shift 2
                ;;
            --simulation) activer_simulation; shift ;;
            -h | --help)
                echo "Usage : install/mettre-a-jour.sh --version <ref git> [--destination <dossier>] [--simulation]"
                exit 0
                ;;
            *) refuser_arguments mettre-a-jour ;;
        esac
    done
    # Une version qui commence par « - » serait lue comme une option de git.
    case "$VERSION" in
        '' | -*) refuser_arguments mettre-a-jour ;;
    esac
}

git_depot() {
    git -C "$RACINE" "$@"
}

# Dépôt propre, versions récupérées, version demandée résolue en commit ; version actuelle notée.
preparer_version() {
    local modifications recuperee=1
    if [ "$SIMULATION" = 1 ]; then
        expliquer "contrôle : aucune modification locale dans $RACINE (git status --porcelain)"
        lancer git -C "$RACINE" fetch --tags --quiet
        expliquer "contrôle : la version $VERSION existe ; version actuelle notée (git rev-parse HEAD)"
        ANCIEN_COMMIT="<version actuelle>"
        COMMIT_CIBLE="<commit de $VERSION>"
        return 0
    fi
    if ! command -v git >/dev/null 2>&1; then
        echouer git_absent "git est introuvable : la mise à jour suit les versions du dépôt."
    fi
    if [ "$(git_depot rev-parse --is-inside-work-tree 2>/dev/null || true)" != true ]; then
        echouer depot_absent "$RACINE n'est pas un dépôt git : la mise à jour suit les versions du dépôt."
    fi
    phase depot_illisible
    modifications="$(git_depot -c core.quotePath=false status --porcelain)"  # accents lisibles dans le message
    if [ -n "$modifications" ]; then
        refuser_modifications "$modifications"
    fi
    if ! GIT_TERMINAL_PROMPT=0 avec_delai "$DELAI_RECUPERATION" git -C "$RACINE" fetch --tags --quiet </dev/null >&2; then
        recuperee=0
        info "Récupération des versions impossible (git fetch --tags ; réseau ?) : recherche de $VERSION parmi les versions déjà connues."
    fi
    COMMIT_CIBLE="$(git_depot rev-parse --verify --quiet "$VERSION^{commit}" || true)"
    if [ -z "$COMMIT_CIBLE" ]; then
        if [ "$recuperee" = 0 ]; then
            echouer recuperation_impossible "Version $VERSION inconnue localement, et récupération des versions impossible."
        fi
        echouer version_introuvable "Version introuvable : $VERSION."
    fi
    ANCIEN_COMMIT="$(git_depot rev-parse --verify HEAD)"
    ANCIENNE_BRANCHE="$(git_depot symbolic-ref --quiet --short HEAD || true)"
    info "Version actuelle : $(version_precedente) ; version demandée : $VERSION ($COMMIT_CIBLE)."
}

# Refus sur un dépôt modifié : les fichiers en cause sont nommés (10 au plus ; ce ne sont pas des secrets), avec ce
# qu'il faut faire. Les fichiers que le système crée tout seul (.DS_Store, ._*, Thumbs.db, desktop.ini) sont ignorés
# par .gitignore et n'arrivent jamais ici.
refuser_modifications() {
    local ligne chemin etat n=0
    info "Le dossier $RACINE contient des fichiers ajoutés ou modifiés : la mise à jour ne démarre pas."
    while IFS= read -r ligne; do
        n=$((n + 1))
        if [ "$n" -gt 10 ]; then continue; fi
        chemin="${ligne:3}"
        case "${ligne:0:2}" in
            '??') etat=ajouté ;;
            *D*) etat=supprimé ;;
            *) etat=modifié ;;
        esac
        info "  $chemin ($etat)"
    done <<FIN
$1
FIN
    if [ "$n" -gt 10 ]; then info "  … et $((n - 10)) autres"; fi
    echouer modifications_locales "Déplacez hors de ce dossier les fichiers que vous y avez ajoutés, ou demandez à la personne qui gère l'installation d'annuler les modifications (git status), puis relancez la mise à jour."
}

# Dossier de la sauvegarde : celui de l'opérateur (contrôlé comme pour install/sauvegarder.sh), sinon mises-a-jour/.
preparer_destination() {
    if [ -n "$DESTINATION" ]; then
        verifier_destination "$DESTINATION"
        return 0
    fi
    DESTINATION="$TERRITORIA_DONNEES_HOTE/mises-a-jour"
    phase dossier_de_sauvegarde_impossible
    creer_dossier "$DESTINATION" 700
}

preparer_mot_de_passe() {
    if [ -s "$FICHIER_MOT_DE_PASSE" ]; then
        MODE_MOT_DE_PASSE=auto
        return 0
    fi
    MODE_MOT_DE_PASSE=memoire
    if [ "$SIMULATION" = 1 ]; then return 0; fi
    info "Aucun mot de passe de sauvegarde enregistré : choisissez celui de la sauvegarde d'avant la mise à jour (il servira aussi à la restaurer en cas d'échec)."
    saisir_mot_de_passe_sauvegarde
}

# Nom de la plus récente archive territoria-AAAAMMJJ-HHMMSS.tsv1 du dossier (vide s'il n'y en a pas).
derniere_archive() {
    local fichier dernier=""
    for fichier in "$1"/territoria-[0-9]*.tsv1; do
        if [ -f "$fichier" ]; then dernier="${fichier##*/}"; fi
    done
    printf '%s' "$dernier"
}

# Sauvegarde d'avant la mise à jour, puis sa vérification complète : un retour arrière ne découvrira jamais une archive
# illisible ou un mot de passe faux.
sauvegarder_avant() {
    local avant apres
    avant="$(derniere_archive "$DESTINATION")"
    phase sauvegarde_impossible
    sauvegarde_ecrire "$DESTINATION" "$MODE_MOT_DE_PASSE"
    if [ "$SIMULATION" = 1 ]; then
        ARCHIVE="$DESTINATION/territoria-AAAAMMJJ-HHMMSS.tsv1"
    else
        apres="$(derniere_archive "$DESTINATION")"
        if [ -z "$apres" ] || [ "$apres" = "$avant" ]; then
            echouer sauvegarde_introuvable "Archive de la sauvegarde introuvable dans $DESTINATION (horloge de la machine ?)."
        fi
        ARCHIVE="$DESTINATION/$apres"
    fi
    phase verification_de_la_sauvegarde
    sauvegarde_verifier "$ARCHIVE" "$MODE_MOT_DE_PASSE"
    info "Sauvegarde d'avant la mise à jour : $ARCHIVE"
    garder_image_avant
}

# L'image de la version actuelle reçoit une étiquette à elle : avec le magasin d'images de containerd (Docker récent), une
# image qui perd son nom et son conteneur est supprimée aussitôt, et la restauration d'un retour arrière en a besoin.
garder_image_avant() {
    local etiquette="$PROJET-territoria:avant-mise-a-jour"
    phase etiquette_impossible
    moteur tag "$IMAGE" "$etiquette" >&2
    IMAGE_AVANT="$etiquette"
}

# Retire cette étiquette quand elle ne sert plus (mise à jour réussie, ou retour arrière réussi) ; sans conséquence si
# c'est impossible.
retirer_image_avant() {
    if [ -n "$IMAGE_AVANT" ]; then
        moteur image rm "$IMAGE_AVANT" >/dev/null 2>&1 || info "Étiquette $IMAGE_AVANT laissée en place (sans conséquence)."
    fi
}

# --- Mise à jour (étapes 3 à 5) : jouée dans un sous-shell en mode strict, son code d'échec est rendu à l'appelant ----

appliquer_version() {
    phase changement_de_version_impossible
    lancer git -C "$RACINE" checkout --quiet --detach "$COMMIT_CIBLE" >&2
    # Dossiers apparus avec les nouvelles versions (enregistrements des séances, modèles de transcription) : créés et
    # confiés aux services, sans toucher à ceux qui existent.
    preparer_dossiers
    phase construction_impossible
    compose build territoria >&2
    # Image de transcription et modèles (les fichiers présents et intacts ne sont pas retéléchargés).
    modeles_audio_facultatif
    # Migration (spécification, § 4.5) : credentials et workflows de la nouvelle version importés dans n8n, n8n arrêté
    # pendant l'import (provisionnement idempotent de l'étape n8n, depuis la nouvelle image). Le test de bout en bout joue
    # ainsi les nouveaux workflows ; en cas d'échec, la restauration remet la base n8n d'avant, donc les anciens workflows.
    provisionner_n8n
    phase demarrage_impossible
    compose up -d >&2
    attendre_sain n8n
    attendre_sain territoria
    phase verification_echouee
    outils_service verifier
}

# --- Retour automatique ----------------------------------------------------------------------------------------------

# territoria/ et n8n/ vont dans echec-<horodatage>/ (0700) par un conteneur jetable en root : sous Linux, un dossier
# appartenant à 10001 ou à 1000 ne peut pas changer de dossier parent sans droit d'écriture sur lui-même. Le conteneur
# part de l'image étiquetée avant la mise à jour : elle est présente à coup sûr, même sans réseau.
deplacer_vers_echec() {
    local horodatage nom n=1
    horodatage="$(date +%Y%m%d-%H%M%S)"
    nom="echec-$horodatage"
    if [ "$SIMULATION" != 1 ]; then
        while [ -e "$TERRITORIA_DONNEES_HOTE/$nom" ]; do
            n=$((n + 1))
            nom="echec-$horodatage-$n"
        done
    fi
    DOSSIER_ECHEC="$TERRITORIA_DONNEES_HOTE/$nom"
    phase deplacement_impossible
    lancer mkdir -m 700 "$DOSSIER_ECHEC"
    # shellcheck disable=SC2016  # script exécuté par le sh du conteneur
    moteur run --rm --network none --user 0:0 --entrypoint sh -v "$TERRITORIA_DONNEES_HOTE:/donnees-hote" "$IMAGE_AVANT" \
        -c 'set -e; for r in territoria n8n; do if [ -e "/donnees-hote/$r" ]; then mv "/donnees-hote/$r" "/donnees-hote/$1/$r"; fi; done' \
        sh "$nom" >&2
}

# Remet la branche d'origine si elle désigne toujours l'ancien commit, sinon l'ancien commit lui-même.
revenir_a_l_ancienne_version() {
    if [ -n "$ANCIENNE_BRANCHE" ] && [ "$(git_depot rev-parse --verify --quiet "refs/heads/$ANCIENNE_BRANCHE" || true)" = "$ANCIEN_COMMIT" ]; then
        lancer git -C "$RACINE" checkout --quiet "$ANCIENNE_BRANCHE" >&2
    else
        lancer git -C "$RACINE" checkout --quiet --detach "$ANCIEN_COMMIT" >&2
    fi
}

# Chaque étape échoue en mode strict : le piège de sortie (fin_mise_a_jour) dit alors où en sont les données.
retour_arriere() {
    ETAT=retour
    if [ "$SIMULATION" = 1 ]; then
        expliquer "si l'une de ces étapes échoue : retour automatique à la version précédente"
    else
        info "Échec de la mise à jour vers $VERSION ($CODE_MISE_A_JOUR) : retour automatique à la version précédente."
    fi
    ETAPE_RETOUR=arret
    phase arret_impossible
    compose stop >&2
    ETAPE_RETOUR=deplacement
    deplacer_vers_echec
    ETAPE_RETOUR=restauration
    sauvegarde_restaurer "$ARCHIVE" forcer "$MODE_MOT_DE_PASSE" "$IMAGE_AVANT"
    ETAPE_RETOUR=version
    phase retour_de_version_impossible
    revenir_a_l_ancienne_version
    ETAPE_RETOUR=demarrage
    phase construction_impossible
    compose build territoria >&2
    phase demarrage_impossible
    compose up -d >&2
    attendre_sain n8n
    attendre_sain territoria
    ETAPE_RETOUR=verification
    phase verification_echouee
    outils_service verifier
    motdepasse=""
    retirer_image_avant
    ETAT=termine
    if [ "$SIMULATION" = 1 ]; then return 0; fi
    info "Les données de la tentative ratée sont conservées dans $DOSSIER_ECHEC (accès réservé ; supprimez ce dossier vous-même quand vous n'en aurez plus besoin)."
    info "Sauvegarde d'avant la mise à jour : $ARCHIVE"
    CODE_ECHEC=mise_a_jour_annulee
    info "$MESSAGE_ANNULATION"
    exit 1
}

# --- Programme -------------------------------------------------------------------------------------------------------

# Tout le programme est dans cette fonction, lue en entier avant de s'exécuter : `git checkout` remplace ce fichier en
# cours de route, et bash ne doit plus avoir à le relire.
principal() {
    local statut code_interne
    lire_arguments "$@"
    debut_etape mettre-a-jour
    trap fin_mise_a_jour EXIT
    verifier_dossier_donnees
    preparer_version
    preparer_destination
    if [ "$SIMULATION" != 1 ] && { [ ! -d "$TERRITORIA_DONNEES_HOTE/territoria" ] || [ ! -f "$S/compose.env" ]; }; then
        echouer installation_absente "Aucune installation dans $TERRITORIA_DONNEES_HOTE."
    fi
    preparer_mot_de_passe
    assurer_moteur_actif
    demarrer_lmstudio
    sauvegarder_avant

    # Code d'échec de la mise à jour rendu par un fichier, pas par la sortie standard : sous bash 5, le piège de sortie
    # déclenché par set -e au milieu de `appliquer_version >&2` écrit encore dans la sortie d'erreur, et le code serait perdu.
    phase ecriture_impossible
    fichier_code="$(mktemp "${TMPDIR:-/tmp}/territoria-code.XXXXXX")"
    ETAT=mise_a_jour
    set +e
    (
        set -e
        trap 'printf "%s" "$CODE_ECHEC" > "$fichier_code"' EXIT
        appliquer_version >&2
    )
    statut=$?
    code_interne="$(cat "$fichier_code" 2>/dev/null)"
    rm -f "$fichier_code"
    set -e
    if [ "$statut" -eq 0 ] && [ "$SIMULATION" != 1 ]; then
        ETAT=termine
        motdepasse=""
        retirer_image_avant
        info "Mise à jour terminée : version $VERSION ($COMMIT_CIBLE)."
        return 0
    fi
    CODE_MISE_A_JOUR="$(code_propre "$code_interne")"
    retour_arriere
}

principal "$@"
exit $?
