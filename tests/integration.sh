#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Parcours d'intégration de Territoria avec une fausse IA : build, secrets, provisionnement de n8n, corpus de
# démonstration, test de bout en bout, sauvegarde et restaurations, contrôle des journaux et du réseau. Il tourne à
# l'identique sur un Mac (Docker Desktop) et sur ubuntu-latest (job `integration` de .github/workflows/ci.yml).
#
# - Les séquences de conteneurs sont celles des scripts d'installation (install/commun.sh : propriétaires des dossiers,
#   commandes `outils`, provisionnement de n8n, conteneur ponctuel des sauvegardes, sauvegarde et restauration).
# - Les données vont dans des dossiers temporaires (jamais territoria-donnees/), le projet Compose s'appelle
#   `territoria-ci` : une installation réelle (`-p territoria`) et les autres conteneurs ne sont jamais touchés.
# - Le piège EXIT supprime les conteneurs, le réseau, secrets/provision/ (jetons en clair) et les dossiers temporaires,
#   que le script réussisse ou non.
# - Aucun secret n'est affiché ni passé en argument : `outils` les génère dans secrets/.
set -euo pipefail

DEPOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DONNEES="$(mktemp -d "${TMPDIR:-/tmp}/territoria-integration.XXXXXX")"
DONNEES="$(cd "$DONNEES" && pwd -P)"
SAUVEGARDES="$(mktemp -d "${TMPDIR:-/tmp}/territoria-sauvegardes.XXXXXX")"
SAUVEGARDES="$(cd "$SAUVEGARDES" && pwd -P)"
export TERRITORIA_DONNEES_HOTE="$DONNEES"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../install/commun.sh
. "$DEPOT/install/commun.sh"
# Après commun.sh : projet dédié, surcharge de la CI (fausse IA, réseau interne) et moteur Docker.
PROJET="territoria-ci"
FICHIERS_COMPOSE+=(-f "$RACINE/conteneurs/compose.ci.yaml")
MOTEUR=docker

etape() { printf '\n== %s\n' "$*"; }

# L'image Python épinglée (IMAGE_PYTHON, celle de territoria.Dockerfile et de faux-llm) sert à supprimer, en root, les
# fichiers que les conteneurs ont créés avec leurs propres identifiants (10001 pour Territoria, 1000 pour n8n, root pour
# les archives) : sous Linux, l'utilisateur de la machine ne peut pas les effacer lui-même.
supprimer_dossier() {
    rm -rf "$1" 2>/dev/null && return 0
    if [ -n "$IMAGE_PYTHON" ]; then
        docker run --rm --user 0:0 --entrypoint sh -v "$1:/nettoyage" "$IMAGE_PYTHON" \
            -c 'find /nettoyage -mindepth 1 -delete' >/dev/null 2>&1 || true
    fi
    rm -rf "$1" 2>/dev/null || echo "Dossier temporaire à supprimer à la main : $1" >&2
}

# Scénario de mise à jour : commande(s) lancées après avoir sourcé le commun.sh du clone temporaire, dans un sous-shell
# (projet territoria-ci-maj, et son territoria-donnees à côté du clone, que le clone calcule lui-même).
MAJ=""
CLONE=""
dans_le_clone() {
    (
        unset TERRITORIA_DONNEES_HOTE
        # shellcheck source=/dev/null  # copie de install/commun.sh, déjà analysée (sinon ses variables masquent celles d'ici)
        . "$CLONE/install/commun.sh"
        MOTEUR=docker
        "$@"
    )
}

nettoyer() {
    local code=$?
    trap - EXIT
    set +e
    if [ "$code" -ne 0 ]; then
        printf '\n!! Échec (code %s, %s) : état des services et fin des journaux (sans contenu utilisateur).\n' \
            "$code" "${CODE_ECHEC:-sans code}" >&2
        compose ps -a >&2
        compose logs --no-color --tail 40 >&2
        if [ -f "$CLONE/install/commun.sh" ]; then
            dans_le_clone compose ps -a >&2
            dans_le_clone compose logs --no-color --tail 40 >&2
        fi
    fi
    rm -rf "$S/provision"  # jetons en clair : supprimés dans tous les cas
    compose down -v --remove-orphans >/dev/null 2>&1
    if [ -n "$MAJ" ]; then
        if [ -f "$CLONE/install/commun.sh" ]; then
            dans_le_clone compose down -v --remove-orphans --rmi local >/dev/null 2>&1
            dans_le_clone moteur image rm territoria-ci-maj-territoria:avant-mise-a-jour >/dev/null 2>&1
        fi
        supprimer_dossier "$MAJ"
    fi
    supprimer_dossier "$DONNEES"
    supprimer_dossier "$SAUVEGARDES"
    exit "$code"
}
trap nettoyer EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v docker >/dev/null || { echo "docker est absent." >&2; exit 1; }
docker compose version >/dev/null

etape "Dossier de données temporaire (${DONNEES##*/}) et réglages Compose"
# territoria/ à l'uid 10001, n8n/ à l'uid 1000 (conteneur jetable en root, sans sudo), secrets/ en 0700 et les fichiers
# d'environnement que Compose exige : la préparation des scripts d'installation.
preparer_dossiers
# Propriétaires vus des conteneurs : territoria/ (10001), n8n/ (1000), caddy/ (10002, 0700, proxy du mode équipe).
proprietaires="$(docker run --rm --network none --user 0:0 --entrypoint sh -v "$DONNEES:/d:ro" "$IMAGE_PYTHON" \
    -c 'stat -c "%n %u:%g" /d/territoria /d/n8n && stat -c "%n %u:%g %a" /d/caddy' </dev/null | tr '\n' ' ')"
echo "propriétaires : $proprietaires"
[ "$proprietaires" = "/d/territoria 10001:10001 /d/n8n 1000:1000 /d/caddy 10002:10002 700 " ]
# Réglages de l'IA : ils viennent de compose.env (voir les scripts d'installation) ; compose.ci.yaml les impose de toute façon.
printf '%s\n' 'TERRITORIA_LLM_URL=http://faux-llm:1234/v1' 'TERRITORIA_MODELE=faux-modele' 'TERRITORIA_HORS_LIGNE=1' \
    >"$S/compose.env"
compose down -v --remove-orphans >/dev/null 2>&1 || true  # reste éventuel d'une exécution interrompue

etape "Construction de l'image Territoria"
compose build territoria

etape "Secrets (avant tout démarrage de n8n, qui sinon inventerait sa propre clé)"
outils_jetable secrets

etape "Provisionnement de n8n : credentials et workflows (n8n arrêté, provision/ supprimé ensuite)"
provisionner_n8n
test ! -e "$S/provision"

etape "Démarrage : fausse IA, n8n, Territoria"
compose up -d
attendre_sain faux-llm
attendre_sain n8n
attendre_sain territoria
outils_jetable proprietaire-n8n

etape "Corpus de démonstration"
outils_service demo

etape "Test de bout en bout (--strict : fausse IA, la citation [1] est exigée)"
outils_service verifier --strict
# Relancé, puis sans --strict : l'espace d'essai (que l'application ne sait pas supprimer) est réutilisé, il n'en reste qu'un.
outils_service verifier --strict
# Mode des installations réelles (sans --strict : la citation [1] n'est pas exigée), joué aussi contre la fausse IA.
outils_service verifier
compter_espaces() {
    compose exec -T territoria python -c 'import sqlite3, sys; print(sqlite3.connect("/donnees/territoire.sqlite3").execute("select count(*) from spaces where title = ?", (sys.argv[1],)).fetchone()[0])' \
        "Essai de bout en bout" </dev/null
}
espaces="$(compter_espaces)"
echo "espaces « Essai de bout en bout » après trois exécutions : $espaces"
[ "$espaces" = "1" ]

etape "Sauvegarde chiffrée pendant que les services écrivent, vérification, restauration"
IMAGE_TERRITORIA="$(compose images -q territoria)"
IMAGE_N8N="$(compose images -q n8n)"
[ -n "$IMAGE_TERRITORIA" ]
[ -n "$IMAGE_N8N" ]
RESTAURATION="$DONNEES/restauration"
mkdir "$RESTAURATION"
# Mot de passe d'essai rangé en 0600 dans secrets/, comme le fait l'étape sauvegarde-auto ; jamais affiché ni en argument.
(umask 077 && od -An -tx1 -N24 /dev/urandom | tr -d ' \n' >"$S/sauvegarde.motdepasse")
# Le cas du WAL est réel : au moins une base a des écritures qui ne sont pas encore reportées dans son fichier principal.
wal="$(ponctuel -v "$DONNEES:/source:ro" "$IMAGE_TERRITORIA" \
    python -c 'import pathlib; print(sum(1 for p in pathlib.Path("/source").glob("*/*-wal") if p.stat().st_size))' </dev/null)"
echo "bases dont le fichier -wal n'est pas vide : $wal"
[ "$wal" -ge 1 ]
# Sauvegarde planifiée (install/sauvegarder.sh --auto) : territoria-donnees monté en lecture seule sur /source, mot de
# passe lu dans le fichier (outils sauvegarder --depuis-fichier /source/secrets/sauvegarde.motdepasse).
sauvegarde_creer "$SAUVEGARDES" auto
archives="$(find "$SAUVEGARDES" -mindepth 1 | wc -l | tr -d ' ')"
privees="$(find "$SAUVEGARDES" -mindepth 1 -maxdepth 1 -type f -name 'territoria-*.tsv1' -perm 600 | wc -l | tr -d ' ')"
echo "fichiers dans la destination : $archives, dont archives en 0600 : $privees"
[ "$archives" = "1" ]
[ "$privees" = "1" ]
# Vérification et restauration dans un dossier vide : le mot de passe passe par l'entrée standard.
exiger_etape verifier-sauvegarde "$(ponctuel -v "$SAUVEGARDES:/destination:ro" "$IMAGE_TERRITORIA" \
    python -m outils verifier-sauvegarde <"$S/sauvegarde.motdepasse")"
exiger_etape restaurer "$(ponctuel -v "$SAUVEGARDES:/destination:ro" -v "$RESTAURATION:/cible" "$IMAGE_TERRITORIA" \
    python -m outils restaurer <"$S/sauvegarde.motdepasse")"
restaure="$(ponctuel -v "$RESTAURATION:/restauration:ro" "$IMAGE_TERRITORIA" python -c '
import os, sqlite3, sys
base = sqlite3.connect("file:/restauration/territoria/territoire.sqlite3?mode=ro&immutable=1", uri=True)
espaces = base.execute("select count(*) from spaces where title = ?", (sys.argv[1],)).fetchone()[0]
print(espaces, " ".join(sorted(os.listdir("/restauration/secrets"))))
' "Essai de bout en bout" </dev/null)"
# Ni sauvegarde.motdepasse ni provision/ dans la restauration.
echo "restauration : espace d'essai et fichiers de secrets/ : $restaure"
[ "$restaure" = "1 compose.env n8n-admin.txt n8n.env territoria.env" ]
# n8n démarre sur sa base et sa clé restaurées (une clé différente de celle du fichier config l'arrêterait) et y
# déchiffre les credentials ; rien de déchiffré n'est écrit ni affiché.
workflows="$(docker run --rm --network none -v "$RESTAURATION/n8n:/home/node/.n8n" \
    --env-file "$RESTAURATION/secrets/n8n.env" "$IMAGE_N8N" list:workflow </dev/null)"
trouves="$(printf '%s\n' "$workflows" | grep -c -e '^TerritoriaConv01|' -e '^TerritoriaArre01|' || true)"
echo "workflows Territoria dans la base n8n restaurée : $trouves"
[ "$trouves" = "2" ]
docker run --rm --network none -v "$RESTAURATION/n8n:/home/node/.n8n" --env-file "$RESTAURATION/secrets/n8n.env" \
    "$IMAGE_N8N" export:credentials --all --decrypted --output=/dev/null </dev/null >/dev/null
echo "credentials de la base n8n restaurée : déchiffrés avec la clé restaurée"
# Élagage de la tâche planifiée (outils elaguer).
sauvegarde_elaguer "$SAUVEGARDES"
[ "$(find "$SAUVEGARDES" -mindepth 1 -name 'territoria-*.tsv1' | wc -l | tr -d ' ')" = "1" ]

etape "Journaux : aucun contenu de question, de réponse ou de document"
journaux="$(compose logs --no-color territoria n8n 2>&1)"
# Garde-fou : le journal de Territoria doit bien contenir les lignes d'étape du parcours, sinon le contrôle ne prouve rien.
vues="$(printf '%s\n' "$journaux" | grep -c -e '"etape": "post:/api/chat"' || true)"
if [ "$vues" -lt 3 ]; then
    echo "Le journal de Territoria ne montre pas le parcours (lignes post:/api/chat : $vues)." >&2
    exit 1
fi
fuites="$(printf '%s\n' "$journaux" | grep -c \
    -e 'archives municipales fictives' -e 'rue des Essais' -e 'Tranquillit' -e 'Démoville' || true)"
echo "lignes de journal contenant du contenu : $fuites"
[ "$fuites" = "0" ]

etape "Réseaux : aucune sortie vers Internet, aucun port publié"
# En production, seul `interne` (n8n et territoria) est internal ; compose.ci.yaml rend aussi `sortie` internal.
for reseau in interne sortie; do
    [ "$(docker network inspect "${PROJET}_$reseau" --format '{{.Internal}}')" = "true" ]
    echo "réseau ${PROJET}_$reseau : internal"
done
[ "$(docker inspect "$(compose ps -q n8n)" --format '{{range $nom, $r := .NetworkSettings.Networks}}{{$nom}} {{end}}')" = "${PROJET}_interne " ]
echo "n8n : sur ${PROJET}_interne seulement"
if compose exec -T n8n wget -q -T 5 -O /dev/null https://example.com </dev/null 2>/dev/null; then
    echo "https://example.com : JOIGNABLE depuis le conteneur n8n, le réseau n'est pas isolé" >&2
    exit 1
fi
echo "https://example.com : injoignable depuis le conteneur n8n"
compose exec -T territoria python -c "
import urllib.request
# Contrôle positif : la fausse IA, sur le réseau interne, répond.
urllib.request.urlopen('http://faux-llm:1234/v1/models', timeout=8).read()
try:
    urllib.request.urlopen('https://example.com', timeout=8)
except Exception:
    print('https://example.com : injoignable depuis le conteneur territoria')
    raise SystemExit(0)
print('https://example.com : JOIGNABLE, le réseau n est pas isolé')
raise SystemExit(1)
" </dev/null
publies="$(docker ps --filter "label=com.docker.compose.project=$PROJET" --format '{{.Ports}}' | grep -c -e '->' || true)"
echo "ports publiés sur la machine : $publies"
[ "$publies" = "0" ]

etape "Restauration forcée dans l'installation (install/restaurer.sh --forcer --auto), puis redémarrage"
# Sans aucun conteneur (comme sur une machine où l'on restaure) : l'image est retrouvée par son nom de construction.
# Services arrêtés par la restauration, territoria/ et n8n/ remplacés, secrets/ fusionné ; n8n redémarre sur la clé
# et la base restaurées.
compose down --remove-orphans
sauvegarde_restaurer "$SAUVEGARDES" forcer auto
compose up -d
attendre_sain faux-llm
attendre_sain n8n
attendre_sain territoria
espaces="$(compter_espaces)"
echo "espaces « Essai de bout en bout » après la restauration forcée : $espaces"
[ "$espaces" = "1" ]

etape "Mise à jour ratée et retour automatique (install/mettre-a-jour.sh dans un clone temporaire du dépôt)"
# Le script de mise à jour fait `git checkout` : il ne tourne jamais dans ce dépôt (R25), mais dans un clone temporaire,
# avec son propre territoria-donnees à côté du clone. Les modifications non enregistrées de ce dépôt y sont recopiées
# (rien en CI) : le scénario joue le code tel qu'il est. Un commit de câblage, dans le clone seulement, fixe le projet
# territoria-ci-maj, ajoute la surcharge compose.ci.yaml (fausse IA, réseau interne, aucun port publié) et vide
# chemin_lms (LM Studio n'est jamais lancé). La version « essai-echec », étiquetée dans le clone seulement, fait renvoyer
# 500 à /healthz : la nouvelle image n'est jamais saine, et la mise à jour doit revenir seule à la version d'origine.
compose down --remove-orphans  # parcours principal terminé : ses conteneurs laissent la place
etat_du_depot() {
    git -C "$DEPOT" for-each-ref --format='%(refname) %(objectname)'
    git -C "$DEPOT" rev-parse HEAD
    git -C "$DEPOT" symbolic-ref --quiet HEAD || true
    git -C "$DEPOT" status --porcelain --untracked-files=all
}
git_essai() {
    git -C "$CLONE" -c user.name=Essai -c user.email=essai@exemple.invalid -c commit.gpgsign=false \
        -c core.hooksPath=/dev/null "$@"
}
depot_avant="$(etat_du_depot)"
if [ -n "$(git -C "$DEPOT" status --porcelain)" ]; then
    echo "Modifications non enregistrées dans le dépôt : elles sont recopiées dans le clone d'essai."
fi
MAJ="$(mktemp -d "${TMPDIR:-/tmp}/territoria-mise-a-jour.XXXXXX")"
MAJ="$(cd "$MAJ" && pwd -P)"
CLONE="$MAJ/territoria"
git clone --quiet --no-hardlinks "$DEPOT" "$CLONE"
# Arbre de travail du dépôt (fichiers suivis et non suivis, hors fichiers ignorés et .superpowers/) à la place de HEAD.
(cd "$CLONE" && git ls-files -z | xargs -0 rm -f)
(
    cd "$DEPOT"
    git ls-files -co --exclude-standard -z | while IFS= read -r -d '' fichier; do
        if [ -e "$fichier" ] || [ -L "$fichier" ]; then printf '%s\0' "$fichier"; fi
    done | tar --null -T - -cf -
) | tar -xf - -C "$CLONE"
git_essai add -A
if ! git_essai diff --cached --quiet; then git_essai commit --quiet -m "essai : arbre de travail du dépôt"; fi
# Câblage du clone (projet, surcharge de la CI, sans LM Studio), contrôlé en sourçant le commun.sh obtenu.
# shellcheck disable=SC2016  # motifs littéraux
sed -e 's/^PROJET=territoria$/PROJET=territoria-ci-maj/' \
    -e 's|^FICHIERS_COMPOSE=(-f "$RACINE/conteneurs/compose.yaml")$|FICHIERS_COMPOSE=(-f "$RACINE/conteneurs/compose.yaml" -f "$RACINE/conteneurs/compose.ci.yaml")|' \
    "$CLONE/install/commun.sh" >"$MAJ/commun.sh"
printf '%s\n' '' '# Essai de mise à jour (tests/integration.sh) : LM Studio n est jamais lancé.' 'chemin_lms() { :; }' >>"$MAJ/commun.sh"
cat "$MAJ/commun.sh" >"$CLONE/install/commun.sh"
# shellcheck disable=SC2016  # développé dans le sous-shell du clone, pas ici
cablage="$(dans_le_clone eval 'printf "%s|%s|%s" "$PROJET" "${FICHIERS_COMPOSE[*]}" "$(chemin_lms)"')"
echo "câblage du clone : ${cablage//$CLONE/<clone>}"
[ "$cablage" = "territoria-ci-maj|-f $CLONE/conteneurs/compose.yaml -f $CLONE/conteneurs/compose.ci.yaml|" ]
git_essai commit --quiet -am "essai : projet territoria-ci-maj, surcharge de la CI, sans LM Studio"
ORIGINE_MAJ="$(git -C "$CLONE" rev-parse HEAD)"
# shellcheck disable=SC2016
DONNEES_MAJ="$(dans_le_clone eval 'printf "%s" "$TERRITORIA_DONNEES_HOTE"')"
case "$DONNEES_MAJ" in
    "$MAJ"/*) echo "territoria-donnees du clone : <temporaire>/${DONNEES_MAJ##*/}, à côté du clone" ;;
    *) echo "territoria-donnees du clone hors du dossier temporaire : $DONNEES_MAJ" >&2; exit 1 ;;
esac

MARQUEUR="Marqueur avant la mise à jour"
installer_le_clone() {
    preparer_dossiers
    printf '%s\n' 'TERRITORIA_LLM_URL=http://faux-llm:1234/v1' 'TERRITORIA_MODELE=faux-modele' 'TERRITORIA_HORS_LIGNE=1' \
        >"$S/compose.env"
    compose build territoria
    outils_jetable secrets
    provisionner_n8n
    compose up -d
    attendre_sain faux-llm
    attendre_sain n8n
    attendre_sain territoria
    outils_jetable proprietaire-n8n
    outils_service demo
    outils_service verifier
    # Mot de passe des sauvegardes rangé comme le fait l'étape sauvegarde-auto : la mise à jour le lit dans ce fichier.
    (umask 077 && od -An -tx1 -N24 /dev/urandom | tr -d ' \n' >"$S/sauvegarde.motdepasse")
    compose exec -T territoria python -c '
import sys
import e2e
from config import REGLAGES
statut, _ = e2e._Essai(e2e.CIBLE, REGLAGES.origine).json("POST", "/api/spaces", {"title": sys.argv[1]})
raise SystemExit(0 if statut == 201 else 1)
' "$MARQUEUR" </dev/null
}
# Comptes des tables et présence du marqueur, lus par le service territoria (aucun contenu affiché).
resume_des_donnees() {
    compose exec -T territoria python -c '
import sqlite3, sys
base = sqlite3.connect("file:/donnees/territoire.sqlite3?mode=ro", uri=True)
comptes = [base.execute("select count(*) from " + table).fetchone()[0] for table in ("spaces", "documents", "conversations", "messages")]
print(*comptes, base.execute("select count(*) from spaces where title = ?", (sys.argv[1],)).fetchone()[0])
' "$MARQUEUR" </dev/null
}
dans_le_clone installer_le_clone
donnees_avant="$(dans_le_clone resume_des_donnees)"
echo "données avant la mise à jour (espaces, documents, conversations, messages, marqueur) : $donnees_avant"
[ "${donnees_avant##* }" = "1" ]

# Version qui échoue : /healthz renvoie 500 ; commit étiqueté essai-echec, puis le clone revient à la version d'origine.
# Son workflow de conversation porte un nom marqué : la mise à jour doit l'importer dans n8n avant son test (migration,
# spécification § 4.5), et le retour doit remettre la base n8n d'avant, avec le nom d'origine.
sed "s/return _json(200, {'ready': True}), None/return _json(500, {'ready': False}), None/" "$CLONE/app/web.py" >"$MAJ/web.py"
cat "$MAJ/web.py" >"$CLONE/app/web.py"
[ "$(grep -c "return _json(500, {'ready': False}), None" "$CLONE/app/web.py")" = "1" ]
sed 's/^  "name": "Territoria — conversation",$/  "name": "Territoria — conversation (essai-echec)",/' \
    "$CLONE/n8n/workflows/conversation.json" >"$MAJ/conversation.json"
cat "$MAJ/conversation.json" >"$CLONE/n8n/workflows/conversation.json"
[ "$(grep -c '"name": "Territoria — conversation (essai-echec)",' "$CLONE/n8n/workflows/conversation.json")" = "1" ]
git_essai commit --quiet -am "essai : /healthz renvoie 500, workflow de conversation marqué"
git_essai tag essai-echec
git_essai reset --quiet --hard HEAD^
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$ORIGINE_MAJ" ]
[ -z "$(git -C "$CLONE" status --porcelain)" ]

set +e
env -u TERRITORIA_DONNEES_HOTE bash "$CLONE/install/mettre-a-jour.sh" --version essai-echec \
    </dev/null >"$MAJ/sortie.txt" 2>"$MAJ/erreurs.txt"
code_maj=$?
set -e
echo "install/mettre-a-jour.sh --version essai-echec : code $code_maj ; fin de sa sortie d'erreur :"
tail -n 12 "$MAJ/erreurs.txt" | sed -e "s|$MAJ|<temporaire>|g" -e 's/^/  | /'
echo "dernière ligne de sa sortie standard : $(tail -n 1 "$MAJ/sortie.txt")"
if [ "$code_maj" != 1 ]; then sed -e 's/^/  | /' "$MAJ/erreurs.txt" >&2; fi
[ "$code_maj" = "1" ]
[ "$(tail -n 1 "$MAJ/sortie.txt")" = "ETAPE mettre-a-jour : ECHEC mise_a_jour_annulee" ]
[ "$(tail -n 1 "$MAJ/erreurs.txt")" = "Mise à jour annulée : version précédente rétablie." ]
# La cause est bien la version ratée : son service territoria n'est jamais devenu sain. Sinon, toute la sortie d'erreur
# de la mise à jour (données fictives) est recopiée pour qu'on voie l'étape qui a échoué.
if ! grep -F "Échec de la mise à jour vers essai-echec (territoria_pas_sain)" "$MAJ/erreurs.txt" >/dev/null; then
    echo "cause inattendue ; sortie d'erreur complète de la mise à jour :" >&2
    sed -e "s|$MAJ|<temporaire>|g" -e 's/^/  | /' "$MAJ/erreurs.txt" >&2
    exit 1
fi
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$ORIGINE_MAJ" ]
[ -z "$(git -C "$CLONE" status --porcelain)" ]
echo "clone revenu au commit d'origine, arbre propre"
dans_le_clone outils_service verifier
donnees_apres="$(dans_le_clone resume_des_donnees)"
echo "données après le retour automatique : $donnees_apres"
[ "$donnees_apres" = "$donnees_avant" ]
# Données de la tentative ratée : un seul echec-*, privé (0700), avec territoria/ et n8n/, marqueur compris.
echecs="$(find "$DONNEES_MAJ" -mindepth 1 -maxdepth 1 -name 'echec-*' | wc -l | tr -d ' ')"
ECHEC_MAJ="$(find "$DONNEES_MAJ" -mindepth 1 -maxdepth 1 -name 'echec-*' -type d -perm 700)"
echo "dossiers echec-* : $echecs ; en 0700 : ${ECHEC_MAJ##*/}"
[ "$echecs" = "1" ]
[ -n "$ECHEC_MAJ" ]
[ -d "$ECHEC_MAJ/territoria" ] && [ -d "$ECHEC_MAJ/n8n" ]
grep -F "$ECHEC_MAJ" "$MAJ/erreurs.txt" >/dev/null  # le message dit où il est
lire_marqueur_de_l_echec() {
    ponctuel -v "$ECHEC_MAJ/territoria:/echec" "$(image_territoria)" python -c '
import sqlite3, sys
base = sqlite3.connect("file:/echec/territoire.sqlite3?mode=ro", uri=True)
print(base.execute("select count(*) from spaces where title = ?", (sys.argv[1],)).fetchone()[0])
' "$MARQUEUR" </dev/null
}
marqueur_echec="$(dans_le_clone lire_marqueur_de_l_echec)"
echo "marqueur dans les données de la tentative ratée : $marqueur_echec"
[ "$marqueur_echec" = "1" ]
# Workflows : la base n8n de la tentative ratée contient ceux de la version essai-echec (importés par la mise à jour avant
# son test) ; la base rétablie, ceux d'origine. Rien de déchiffré n'est lu : seulement les identifiants et les noms.
workflows_echec="$(docker run --rm --network none -v "$ECHEC_MAJ/n8n:/home/node/.n8n" \
    --env-file "$DONNEES_MAJ/secrets/n8n.env" "$IMAGE_N8N" list:workflow </dev/null)"
printf '%s\n' "$workflows_echec" | grep -Fx 'TerritoriaConv01|Territoria — conversation (essai-echec)' >/dev/null
echo "base n8n de la tentative ratée : workflow de conversation de la version essai-echec (importé par la mise à jour)"
workflows_apres="$(dans_le_clone compose exec -T n8n n8n list:workflow </dev/null)"
printf '%s\n' "$workflows_apres" | grep -Fx 'TerritoriaConv01|Territoria — conversation' >/dev/null
printf '%s\n' "$workflows_apres" | grep '^TerritoriaArre01|Territoria — rédaction' >/dev/null
[ "$(printf '%s\n' "$workflows_apres" | grep -c 'essai-echec' || true)" = "0" ]
echo "base n8n rétablie : workflows d'origine, aucun nom essai-echec"
# Sauvegarde d'avant la mise à jour : une archive en 0600 dans territoria-donnees/mises-a-jour/ (0700).
[ -n "$(find "$DONNEES_MAJ" -mindepth 1 -maxdepth 1 -name 'mises-a-jour' -type d -perm 700)" ]
[ "$(find "$DONNEES_MAJ/mises-a-jour" -mindepth 1 -name 'territoria-*.tsv1' -perm 600 | wc -l | tr -d ' ')" = "1" ]
echo "sauvegarde d'avant la mise à jour : une archive en 0600 dans mises-a-jour/ (0700)"
# L'image d'avant la mise à jour, étiquetée pour la restauration, n'est plus étiquetée une fois le retour réussi.
[ -z "$(docker images -q territoria-ci-maj-territoria:avant-mise-a-jour)" ]
# Ce dépôt n'a pas bougé : mêmes références (aucune étiquette essai-echec), même HEAD, aucun fichier modifié.
[ "$(etat_du_depot)" = "$depot_avant" ]
[ -z "$(git -C "$DEPOT" tag -l essai-echec)" ]
echo "dépôt de travail inchangé (références, HEAD, fichiers)"
dans_le_clone compose down -v --remove-orphans --rmi local

etape "Intégration : OK"
