# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Installation de Territoria sous Windows, une étape à la fois (miroir de installer.sh) :
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape <nom> [--simulation] [--auto] [--palier petit|moyen|grand]
# Étapes, dans l'ordre : moteur, lmstudio (ou mistral : API de Mistral au lieu de l'IA locale), secrets, legifrance,
# demarrer, n8n, demo, verifier, raccourci, sauvegarde-auto (en option). Chaque étape est idempotente et finit par « ETAPE <nom> : OK|ECHEC <code> ».
# --simulation affiche les commandes sans rien modifier ; --auto (raccourci) ajoute le démarrage à l'ouverture de
# session ; --palier (lmstudio) remplace le palier recommandé par le diagnostic.
# Les étapes mistral, legifrance et sauvegarde-auto posent des questions : l'utilisateur les lance lui-même dans sa fenêtre
# PowerShell (saisie masquée), jamais un assistant IA.
# Les options sont lues dans $args : « --etape » n'est pas un paramètre PowerShell.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

$script:Auto = $false
$script:Palier = ''
$script:Etape = ''

function usage {
    sortie 'Usage : install\installer.ps1 --etape <nom> [--simulation] [--auto] [--palier petit|moyen|grand]'
    sortie 'Étapes : moteur, lmstudio (ou mistral), secrets, legifrance, demarrer, n8n, demo, verifier, raccourci, sauvegarde-auto.'
}

# --- moteur : Docker ou Podman réutilisé ; sinon Podman installé par winget ------------------------------------------

# winget : 0 = installé, 0x8A15002B (-1978335189) = rien à faire, 0x8A150061 (-1978335135) = déjà installé.
function installer_avec_winget {
    param([string]$paquet)
    if (-not $script:Simulation -and -not (commande_presente 'winget')) {
        echouer winget_absent "winget est introuvable : installez « Programme d'installation d'application » depuis le Microsoft Store, ou installez ce logiciel à la main."
    }
    lancer_tableau 'winget' @('install', '--exact', '--id', $paquet, '--source', 'winget', '--silent',
        '--accept-package-agreements', '--accept-source-agreements') @(0, -1978335189, -1978335135)
    if (-not $script:Simulation) { actualiser_chemin }
}

function installer_podman {
    phase installation_podman_impossible
    installer_avec_winget 'RedHat.Podman'
    if (-not $script:Simulation -and -not (commande_presente 'podman')) {
        echouer podman_introuvable "Podman est installé mais introuvable : fermez cette fenêtre PowerShell, ouvrez-en une nouvelle, puis relancez l'étape."
    }
}

# Fournisseur de « podman compose » et machine virtuelle Podman.
function preparer_podman {
    $fournisseur = $false
    if (-not $script:Simulation) { $fournisseur = avec_delai 60 'podman' @('compose', 'version') }
    if ($script:Simulation -or -not $fournisseur) {
        expliquer "si « podman compose » n'a pas encore de fournisseur Compose :"
        phase compose_absent
        installer_avec_winget 'Docker.DockerCompose'
    }
    $machines = ''
    if (-not $script:Simulation) {
        $machines = (executer 'podman' @('machine', 'list', '--format', '{{.Name}}') $null 60 $true).Sortie.Trim()
    }
    if ($script:Simulation -or $machines.Length -eq 0) {
        expliquer "si aucune machine Podman n'existe encore :"
        phase machine_podman_impossible
        try {
            lancer_detache podman machine init
        } catch {
            info "Sous Windows, Podman a besoin de WSL 2 (« wsl --install » dans une fenêtre d'administrateur, puis redémarrage) ou de Hyper-V."
            throw
        }
    }
}

function etape_moteur {
    $present = moteur_present
    if ($present -eq 'aucun') {
        installer_podman
        $present = 'podman'
    } else {
        info ('Moteur de conteneurs déjà installé : {0} (réutilisé).' -f $present)
        expliquer ('moteur de conteneurs déjà installé : {0} (réutilisé, rien à installer)' -f $present)
    }
    if ($present -eq 'podman') { preparer_podman }
    assurer_moteur_actif
    if ($script:Moteur -eq 'docker' -and -not $script:Simulation) {
        $plateforme = (executer 'docker' @('version', '--format', '{{.Server.Platform.Name}}') $null 10 $true).Sortie
        if ($plateforme -like '*Docker Desktop*') {
            info "Docker Desktop : son abonnement est payant pour les administrations ; Podman est l'option par défaut."
        }
    }
    try {
        moteur compose version
    } catch {
        echouer compose_absent ("« {0} compose » est indisponible : installez le module Compose (Docker Desktop l'inclut ; avec Podman, Docker.DockerCompose par winget)." -f $script:Moteur)
    }
}

# --- lmstudio : LM Studio, modèle du palier, écoute limitée, journalisation coupée, compose.env ----------------------

function installer_lmstudio {
    phase installation_lmstudio_impossible
    installer_avec_winget 'ElementLabs.LMStudio'
}

# Dossier de LM Studio confirmé (pointeur .lmstudio-home-pointer ou emplacement documenté existant), sinon échec :
# jamais de réglage écrit ni de journal cherché dans un dossier que LM Studio n'utilise pas.
function exiger_dossier_lmstudio {
    $script:DossierLms = dossier_lmstudio
    if ($script:DossierLms.Length -gt 0) { return }
    if ($script:Simulation) {
        $script:DossierLms = '<dossier de LM Studio>'
        return
    }
    echouer lmstudio_dossier_introuvable "Dossier de LM Studio introuvable (ni .lmstudio-home-pointer valide, ni .lmstudio, ni lm-studio dans %LOCALAPPDATA%) : ouvrez LM Studio une première fois, puis relancez l'étape."
}

# Réglages du serveur LM Studio (clés de http-server-config.json) : écoute sur l'adresse choisie (networkInterface),
# aucun fichier de journal (fileLoggingMode=off), pas de journal détaillé ni des jetons générés, contenu masqué
# (logSensitiveData=false, c'est-à-dire « Redact Content » activé). Serveur arrêté pendant le réglage. Le fichier est lu
# et écrit en UTF-8 sans BOM (JSON.parse refuse un BOM).
function regler_journalisation_lms {
    param([string]$adresse)
    $fichier = joindre $script:DossierLms @('.internal', 'http-server-config.json')
    if ($script:Simulation) {
        expliquer ('réglages du serveur dans {0} : networkInterface={1}, fileLoggingMode=off, verbose=false, logIncomingTokens=false, logSensitiveData=false' -f $fichier, $adresse)
        return
    }
    phase journalisation_non_reglee
    if (-not (Test-Path -LiteralPath $fichier -PathType Leaf)) {
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $fichier) -Force
        # Réglages par défaut de LM Studio (emptyHttpServerSettings de l'application), journalisation coupée.
        $reglages = [ordered]@{
            autoStartOnLaunch = $false; port = 1234; cors = $false; logSensitiveData = $false; logIncomingTokens = $false
            verbose = $false; logLinesLimit = 500; networkInterface = $adresse; justInTimeModelLoading = $true; fileLoggingMode = 'off'
        }
        ecrire_octets $fichier ($script:Utf8.GetBytes((ConvertTo-Json -InputObject $reglages) + "`n")) $false
        return
    }
    $reglages = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($fichier, $script:Utf8))
    $valeurs = [ordered]@{
        networkInterface = $adresse; fileLoggingMode = 'off'; verbose = $false; logIncomingTokens = $false; logSensitiveData = $false
    }
    foreach ($cle in $valeurs.Keys) {
        Add-Member -InputObject $reglages -NotePropertyName $cle -NotePropertyValue $valeurs[$cle] -Force
    }
    ecrire_octets $fichier ($script:Utf8.GetBytes((ConvertTo-Json -InputObject $reglages -Depth 20) + "`n")) $false
}

# Requêtes HTTP vers LM Studio (fonctions à part : les essais les remplacent).
function http_get {
    param([string]$url, [int]$delai = 30)
    $ProgressPreference = 'SilentlyContinue'
    return [string](Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec $delai).Content
}

function http_post_json {
    param([string]$url, [string]$corps, [int]$delai = 600)
    $ProgressPreference = 'SilentlyContinue'
    $null = Invoke-WebRequest -UseBasicParsing -Uri $url -Method Post -ContentType 'application/json' -Body $script:Utf8.GetBytes($corps) -TimeoutSec $delai
}

function verifier_modele_servi {
    param([string]$base, [string]$cle)
    if ($script:Simulation) {
        afficher_commande @('Invoke-WebRequest', '-UseBasicParsing', '-Uri', "$base/models")
        expliquer ('le modèle {0} doit figurer dans la réponse' -f $cle)
        return
    }
    phase lmstudio_injoignable
    $modeles = http_get "$base/models" 30
    if (-not $modeles.Contains('"' + $cle + '"')) {
        echouer modele_introuvable ("Le modèle {0} n'apparaît pas dans {1}/models." -f $cle, $base)
    }
}

# Critère d'acceptation : LM Studio répond depuis un conteneur (même adresse que Territoria). Le script Python tient
# sur une ligne et n'a que des apostrophes droites : Windows PowerShell 5.1 altère les guillemets des arguments.
function verifier_acces_conteneur {
    param([string]$url)
    $programme = (@'
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + '/models', timeout=15).read()
print('LM Studio joignable depuis un conteneur :', sys.argv[1])
'@ -split "\r?\n" | Where-Object { $_.Length -gt 0 }) -join '; '
    $arguments = @('run', '--rm', '--user', '10001:10001', '--read-only')
    if ($script:Moteur -eq 'docker') { $arguments += @('--add-host', 'host.docker.internal:host-gateway') }
    $arguments += @($script:ImagePython, 'python', '-c', $programme, $url)
    phase lmstudio_injoignable_depuis_conteneur
    lancer_tableau $script:Moteur $arguments
}

# Nombre de fichiers du dossier qui contiennent le marqueur (en ASCII ou en UTF-16), lus même ouverts par LM Studio. Un
# fichier illisible arrête l'étape : on ne conclut jamais à l'absence de journal sans avoir tout lu.
function compter_fichiers_contenant {
    param([string]$dossier, [string]$marqueur)
    $utf16 = ($marqueur.ToCharArray() -join [string][char]0)
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)
    $trouves = 0
    foreach ($fichier in @(Get-ChildItem -LiteralPath $dossier -Recurse -File -ErrorAction SilentlyContinue)) {
        try {
            $flux = New-Object System.IO.FileStream($fichier.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]'ReadWrite,Delete')
            try {
                $octets = New-Object byte[] ([int]$flux.Length)
                $lu = 0
                while ($lu -lt $octets.Length) {
                    $n = $flux.Read($octets, $lu, $octets.Length - $lu)
                    if ($n -le 0) { break }
                    $lu += $n
                }
            } finally {
                $flux.Dispose()
            }
        } catch {
            echouer journaux_illisibles "Un fichier de journal de LM Studio n'a pas pu être lu : aucun contrôle n'est possible."
        }
        $texte = $latin1.GetString($octets)
        if ($texte.Contains($marqueur) -or $texte.Contains($utf16)) { $trouves++ }
    }
    return $trouves
}

# Une requête de test contenant un marqueur ne doit apparaître dans aucun journal de LM Studio (seul le nombre de
# fichiers concernés est imprimé, jamais leur contenu).
function verifier_journaux_lms {
    param([string]$base, [string]$cle)
    $journaux = joindre $script:DossierLms @('server-logs')
    $marqueur = 'marqueur-territoria-{0}-{1}' -f [DateTimeOffset]::UtcNow.ToUnixTimeSeconds(), $PID
    $corps = '{"model":"' + $cle + '","messages":[{"role":"user","content":"Répondez seulement OK. ' + $marqueur + '"}],"max_tokens":8,"stream":false}'
    if ($script:Simulation) {
        afficher_commande @('Invoke-WebRequest', '-UseBasicParsing', '-Method', 'Post', '-ContentType', 'application/json', '-Body', $corps, '-Uri', "$base/chat/completions")
        expliquer ('le marqueur ne doit apparaître dans aucun fichier de {0}' -f $journaux)
        return
    }
    phase requete_de_test_impossible
    http_post_json "$base/chat/completions" $corps 600
    Start-Sleep -Seconds 2
    if (-not (Test-Path -LiteralPath $journaux -PathType Container)) {
        # Sans ce dossier, aucun contrôle n'a eu lieu : on ne conclut pas à l'absence de journal.
        echouer journaux_lmstudio_introuvables ("Contrôle impossible : le dossier des journaux de LM Studio ({0}) est introuvable après la requête de test. Vérifiez que File Logging Mode est sur Off (Developer > Server Settings) ; si LM Studio n'a encore jamais écrit de journal, créez ce dossier vide, puis relancez l'étape." -f $journaux)
    }
    $trouves = compter_fichiers_contenant $journaux $marqueur
    info ('fichiers de journal de LM Studio contenant la requête de test : {0}' -f $trouves)
    if ($trouves -ne 0) {
        echouer journalisation_active "La requête de test apparaît dans les journaux de LM Studio. Dans LM Studio (Developer > Server Settings) : File Logging Mode sur Off, Verbose Logging et Log Incoming Tokens décochés, Redact Content coché ; puis relancez l'étape."
    }
}

function etape_lmstudio {
    exiger_moteur
    verifier_dossier_donnees
    $palier = $script:Palier
    if ($palier.Length -eq 0) { $palier = palier_modele (memoire_go) }
    $id = modele_du_palier $palier
    if ($id.Length -eq 0) { echouer modele_inconnu ("Aucun modèle pour le palier {0} dans install\modeles.json." -f $palier) }
    $cle = $id
    if ($id.Contains('@')) { $cle = $id.Substring(0, $id.LastIndexOf('@')) }  # identifiant du modèle dans l'API (TERRITORIA_MODELE)
    info ('Palier {0} : modèle {1}.' -f $palier, $id)
    $lms = chemin_lms
    if ($lms.Length -eq 0) {
        installer_lmstudio
        $lms = chemin_lms
        if ($lms.Length -eq 0) {
            if (-not $script:Simulation) {
                echouer lms_absent "Commande lms introuvable après l'installation de LM Studio : ouvrez LM Studio une première fois (il installe la commande lms), puis relancez l'étape."
            }
            $lms = 'lms'
        }
    }
    exiger_dossier_lmstudio
    $adresse = adresse_ecoute_lms
    $url = url_lms_conteneurs
    $base = 'http://{0}:{1}/v1' -f $adresse, $script:PortLms

    # 1. Modèle du palier (GGUF Q4_K_M), téléchargé une seule fois.
    $present = $false
    if (-not $script:Simulation) {
        $present = (executer $lms @('ls', '--json') $null 60 $true).Sortie.Contains('"' + $id + '"')
    }
    if (-not $present) {
        phase telechargement_impossible
        lancer_tableau $lms @('get', $id, '--gguf', '--yes')
    }
    # 2. Journalisation coupée, serveur arrêté pendant le réglage.
    if ($script:Simulation -or (lms_en_marche $lms)) {
        phase lmstudio_arret_impossible
        lancer_detache $lms server stop
    }
    regler_journalisation_lms $adresse
    # 3. Serveur sur l'adresse d'écoute limitée (docs/acces-lmstudio.md), contrôlée sur l'écoute réelle.
    phase lmstudio_demarrage_impossible
    lancer_detache $lms server start --port ([string]$script:PortLms) --bind $adresse
    verifier_ecoute_lms $adresse
    # 4. Modèle chargé sous un identifiant stable, celui que Territoria demande.
    charger_modele_lms $lms $id $cle
    # 5. Réglages de l'IA pour Compose (jamais dans secrets\territoria.env).
    creer_dossier $script:Donnees $true
    creer_dossier $script:Secrets $true
    $composeEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    ecrire_variable $composeEnv 'TERRITORIA_LLM_URL' $url
    ecrire_variable $composeEnv 'TERRITORIA_MODELE' $cle
    # 6. Contrôles : modèle servi, joignable depuis un conteneur, absent des journaux.
    verifier_modele_servi $base $cle
    verifier_acces_conteneur $url
    verifier_journaux_lms $base $cle
    retirer_mistral
}

# Retour à l'IA locale : l'option Mistral de l'étape mistral est retirée (une valeur vide vaut absence pour config.py).
function retirer_mistral {
    $composeEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    $territoriaEnv = Join-Path -Path $script:Secrets -ChildPath 'territoria.env'
    if (variable_renseignee $composeEnv 'TERRITORIA_IA_EN_LIGNE') { ecrire_variable $composeEnv 'TERRITORIA_IA_EN_LIGNE' '' }
    if (variable_renseignee $territoriaEnv 'TERRITORIA_MISTRAL_CLE') { ecrire_variable $territoriaEnv 'TERRITORIA_MISTRAL_CLE' '' 'litteral,secret' }
}

# --- mistral : l'API de Mistral au lieu de l'IA locale (docs/mistral.md) ----------------------------------------------

$script:AvertissementMistral = ("Avec l'API de Mistral, ces textes partent chez Mistral, hors de ce poste : les questions des agents et les " +
    "extraits de documents retenus, tous les extraits des documents importés (recherche par le sens), les données et projets " +
    "d'arrêtés, de délibérations et de courriers, les passages des séances pour le procès-verbal. Ne partent jamais : le son " +
    'des séances, les comptes, les journaux, les sauvegardes. Avant d''accepter : contrat de sous-traitance (DPA) de Mistral, ' +
    'inscription au registre des traitements, avis du DPO (docs/mistral.md).')

function etape_mistral {
    verifier_dossier_donnees
    $territoriaEnv = Join-Path -Path $script:Secrets -ChildPath 'territoria.env'
    $composeEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    if ($script:Simulation) {
        expliquer 'avertissement : ce qui part chez Mistral, démarches RGPD (docs/mistral.md)'
        expliquer "question dans la fenêtre PowerShell de l'utilisateur : utiliser l'API de Mistral au lieu de l'IA locale ? [o/N]"
        expliquer 'si oui : saisie masquée de la clé API'
        ecrire_variable $territoriaEnv 'TERRITORIA_MISTRAL_CLE' '' 'litteral,secret'
        ecrire_variable $composeEnv 'TERRITORIA_IA_EN_LIGNE' 'mistral'
        return
    }
    if (((lire_variable $composeEnv 'TERRITORIA_IA_EN_LIGNE') -ceq 'mistral') -and (variable_renseignee $territoriaEnv 'TERRITORIA_MISTRAL_CLE')) {
        info ('API de Mistral déjà choisie, clé enregistrée dans {0} : conservées.' -f $territoriaEnv)
        return
    }
    exiger_terminal
    info $script:AvertissementMistral
    $reponse = lire_reponse "Utiliser l'API de Mistral au lieu de l'IA locale ? [o/N]"
    if (-not (repondu_oui $reponse)) {
        info "IA locale conservée : rien n'est envoyé à Mistral. Pour l'installer : install\installer.ps1 --etape lmstudio."
        return
    }
    $cle = texte_du_secret (lire_secret 'Clé API de Mistral (saisie masquée)')
    if ($cle.Length -eq 0) { echouer saisie_vide "Clé vide : rien n'a été enregistré." }
    creer_dossier $script:Donnees $true
    creer_dossier $script:Secrets $true
    ecrire_variable $territoriaEnv 'TERRITORIA_MISTRAL_CLE' $cle 'litteral,secret'
    $cle = ''
    ecrire_variable $composeEnv 'TERRITORIA_IA_EN_LIGNE' 'mistral'
    info ('API de Mistral choisie ; clé enregistrée dans {0} (accès réservé à votre compte), jamais affichée. Elle sert au prochain démarrage.' -f $territoriaEnv)
}

# --- secrets, legifrance --------------------------------------------------------------------------------------------

$script:QuestionConnecteurs = "Autoriser les connecteurs publics (Légifrance, data.gouv.fr), à activer ensuite par l'administration ? [o/N]"

function etape_secrets {
    exiger_moteur
    preparer_dossiers
    phase construction_impossible
    compose build territoria
    outils_jetable secrets
    restreindre_secrets
}

function etape_legifrance {
    verifier_dossier_donnees
    $territoriaEnv = Join-Path -Path $script:Secrets -ChildPath 'territoria.env'
    $composeEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    if ($script:Simulation) {
        expliquer "identifiants PISTE d'une ancienne installation : retirés de territoria.env, que plus rien ne lit"
        retirer_variable $territoriaEnv 'PISTE_CLIENT_ID'
        retirer_variable $territoriaEnv 'PISTE_CLIENT_SECRET'
        expliquer ("question dans la fenêtre PowerShell de l'utilisateur : " + $script:QuestionConnecteurs)
        expliquer 'si oui : connecteurs autorisés, à activer dans Administration › Connecteurs'
        ecrire_variable $composeEnv 'TERRITORIA_HORS_LIGNE' '0'
        expliquer 'si non : mode hors ligne'
        ecrire_variable $composeEnv 'TERRITORIA_HORS_LIGNE' '1'
        return
    }
    exiger_terminal
    creer_dossier $script:Donnees $true
    creer_dossier $script:Secrets $true
    # Ancienne étape 4 : identifiants PISTE rangés ici, que plus rien ne lit (le connecteur a les siens).
    if ((variable_renseignee $territoriaEnv 'PISTE_CLIENT_ID') -or (variable_renseignee $territoriaEnv 'PISTE_CLIENT_SECRET')) {
        retirer_variable $territoriaEnv 'PISTE_CLIENT_ID'
        retirer_variable $territoriaEnv 'PISTE_CLIENT_SECRET'
        info ('Identifiants PISTE retirés de {0} : TerritorIA ne les lit plus. Pour Légifrance, saisissez-les dans Administration › Connecteurs.' -f $territoriaEnv)
    }
    $reponse = lire_reponse $script:QuestionConnecteurs
    if (-not (repondu_oui $reponse)) {
        ecrire_variable $composeEnv 'TERRITORIA_HORS_LIGNE' '1'
        info ('Mode hors ligne : TERRITORIA_HORS_LIGNE=1 dans {0} (connecteurs coupés ; les textes déjà en cache restent utilisables).' -f $composeEnv)
        return
    }
    ecrire_variable $composeEnv 'TERRITORIA_HORS_LIGNE' '0'
    info ("Connecteurs autorisés : TERRITORIA_HORS_LIGNE=0 dans {0}. Ils restent désactivés jusqu'à leur activation dans Administration › Connecteurs." -f $composeEnv)
}

# --- demarrer, n8n, demo, verifier ----------------------------------------------------------------------------------

function etape_demarrer {
    assurer_moteur_actif
    demarrer_lmstudio
    demarrer_services 'construire'
}

function etape_n8n {
    exiger_moteur
    exiger_secrets
    provisionner_n8n
    phase demarrage_impossible
    compose up -d
    attendre_sain 'n8n'
    attendre_sain 'territoria'
    outils_jetable proprietaire-n8n
}

# « outils demo » refuse d'installer le corpus fictif par-dessus une bibliothèque réelle (bibliotheque_etrangere).
function etape_demo {
    exiger_moteur
    outils_service demo
}

# Sur une installation réelle, verifier tourne sans --strict (un vrai modèle n'a pas à citer [1]).
function etape_verifier {
    exiger_moteur
    outils_service verifier
}

# --- raccourci ------------------------------------------------------------------------------------------------------

function programme_powershell {
    $racine = [string]$env:SystemRoot
    if ($racine.Length -gt 0) {
        $exe = joindre $racine @('System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        if (Test-Path -LiteralPath $exe -PathType Leaf) { return $exe }
    }
    return 'powershell.exe'
}

# Crée un raccourci .lnk (WScript.Shell) qui lance demarrer.ps1 ; fenêtre : 1 normale, 7 réduite.
function creer_raccourci {
    param([string]$chemin, [string]$arguments, [int]$fenetre)
    $programme = programme_powershell
    if ($script:Simulation) {
        sortie ('+ raccourci {0} : {1} {2} (dossier de travail {3})' -f (citer $chemin), (citer $programme), $arguments, (citer $script:Racine))
        return
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lien = $shell.CreateShortcut($chemin)
        $lien.TargetPath = $programme
        $lien.Arguments = $arguments
        $lien.WorkingDirectory = $script:Racine
        $lien.WindowStyle = $fenetre
        $lien.Description = 'Territoria : démarre les services puis ouvre l''interface dans le navigateur'
        $lien.Save()
    } catch {
        echouer raccourci_impossible ('Raccourci impossible à créer : ' + $chemin)
    }
}

function etape_raccourci {
    $demarrer = joindre $script:Racine @('install', 'demarrer.ps1')
    if ($demarrer.Contains('"')) { echouer chemin_non_pris_en_charge 'Le chemin du dépôt contient un caractère non pris en charge par les raccourcis.' }
    $base = '-NoProfile -ExecutionPolicy Bypass -File {0}' -f (citer_windows $demarrer $true)
    $bureau = dossier_bureau
    creer_dossier $bureau $false
    phase raccourci_impossible
    creer_raccourci (Join-Path -Path $bureau -ChildPath 'Territoria.lnk') ($base + ' --ouvrir') 1
    if ($script:Auto) {
        $demarrage = dossier_demarrage
        creer_dossier $demarrage $false
        creer_raccourci (Join-Path -Path $demarrage -ChildPath 'Territoria demarrage.lnk') $base 7
        info ('Démarrage à l''ouverture de session : {0} (actif à la prochaine session).' -f (Join-Path -Path $demarrage -ChildPath 'Territoria demarrage.lnk'))
    }
}

# --- sauvegarde-auto : mot de passe, destination, tâche planifiée tous les jours à 12 h 30 ---------------------------

$script:NomTache = 'Territoria-Sauvegarde'

# La tâche planifiée démarre avec l'environnement de l'utilisateur, pas celui de cette fenêtre : elle ne retrouve
# territoria-donnees que si c'est l'emplacement standard ou si TERRITORIA_DONNEES_HOTE est une variable permanente de
# l'utilisateur. (Fonction à part : les essais la remplacent.)
function donnees_planifiables {
    $comparaison = [System.StringComparison]::Ordinal
    if ($script:EstWindows) { $comparaison = [System.StringComparison]::OrdinalIgnoreCase }
    $actuel = [System.IO.Path]::GetFullPath($script:Donnees).TrimEnd('\', '/')
    $standard = [System.IO.Path]::GetFullPath((Join-Path -Path (dossier_parent $script:Racine) -ChildPath 'territoria-donnees')).TrimEnd('\', '/')
    if ($actuel.Equals($standard, $comparaison)) { return $true }
    $permanent = [Environment]::GetEnvironmentVariable('TERRITORIA_DONNEES_HOTE', 'User')
    if ([string]::IsNullOrEmpty($permanent)) { return $false }
    return $actuel.Equals([System.IO.Path]::GetFullPath($permanent).TrimEnd('\', '/'), $comparaison)
}

function ecrire_mot_de_passe {
    param([string]$motdepasse)
    ecrire_octets $script:FichierMotDePasse ($script:Utf8.GetBytes($motdepasse + "`n")) $true
}

# Définition de la tâche (XML du Planificateur de tâches) : tous les jours à 12 h 30, sous le compte de l'utilisateur,
# powershell.exe -File sauvegarder.ps1 --auto --destination <D>. Le XML n'a ni la limite de 261 caractères de /TR ni ses
# soucis de guillemets.
function xml_tache {
    param([string]$destination)
    $programme = joindre $script:Racine @('install', 'sauvegarder.ps1')
    # Chaque chemin est cité par citer_windows (toujours entre guillemets) : une destination qui finit par une barre
    # inverse (« E:\ », « D:\Sauvegardes\ ») verrait sinon sa dernière barre fusionner avec le guillemet fermant et
    # deviendrait « E:" ».
    $arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {0} --auto --destination {1}' -f (citer_windows $programme $true), (citer_windows $destination $true)
    $sid = "<SID de l'utilisateur>"
    if ($script:EstWindows) { $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
    $lignes = @(
        '<?xml version="1.0" encoding="UTF-16"?>',
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
        '  <RegistrationInfo><Description>Sauvegarde chiffrée quotidienne de Territoria</Description></RegistrationInfo>',
        '  <Triggers>',
        '    <CalendarTrigger>',
        '      <StartBoundary>2026-01-01T12:30:00</StartBoundary>',
        '      <Enabled>true</Enabled>',
        '      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>',
        '    </CalendarTrigger>',
        '  </Triggers>',
        '  <Principals>',
        '    <Principal id="Utilisateur">',
        ('      <UserId>{0}</UserId>' -f (echapper_xml $sid)),
        '      <LogonType>InteractiveToken</LogonType>',
        '      <RunLevel>LeastPrivilege</RunLevel>',
        '    </Principal>',
        '  </Principals>',
        '  <Settings>',
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>',
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>',
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>',
        '    <StartWhenAvailable>true</StartWhenAvailable>',
        '    <AllowStartOnDemand>true</AllowStartOnDemand>',
        '    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>',
        '    <Enabled>true</Enabled>',
        '  </Settings>',
        '  <Actions Context="Utilisateur">',
        '    <Exec>',
        '      <Command>powershell.exe</Command>',
        ('      <Arguments>{0}</Arguments>' -f (echapper_xml $arguments)),
        ('      <WorkingDirectory>{0}</WorkingDirectory>' -f (echapper_xml $script:Racine)),
        '    </Exec>',
        '  </Actions>',
        '</Task>'
    )
    return ($lignes -join "`n")
}

function planifier_windows {
    param([string]$destination)
    $xml = xml_tache $destination
    $arguments = @('/Create', '/TN', $script:NomTache, '/XML')
    if ($script:Simulation) {
        expliquer 'tous les jours à 12:30 (équivalent de /SC DAILY /ST 12:30), défini dans le fichier XML : /TR limite la commande à 261 caractères et altère ses guillemets'
        afficher_commande (@('schtasks') + $arguments + @('<fichier temporaire>', '/F'))
        foreach ($ligne in ($xml -split "\r?\n")) { sortie ('|   ' + $ligne) }
        return
    }
    phase planification_impossible
    $temporaire = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('territoria-tache-{0}.xml' -f [guid]::NewGuid().ToString('N'))
    try {
        [System.IO.File]::WriteAllText($temporaire, $xml, [System.Text.Encoding]::Unicode)  # UTF-16 avec BOM, comme l'exige schtasks
        lancer_tableau 'schtasks' ($arguments + @($temporaire, '/F'))
    } finally {
        Remove-Item -LiteralPath $temporaire -Force -ErrorAction SilentlyContinue
    }
}

function etape_sauvegarde_auto {
    verifier_dossier_donnees
    if ($script:Simulation) {
        expliquer 'question : dossier de destination des sauvegardes (disque externe, serveur de fichiers interne), déjà existant'
        if ((Test-Path -LiteralPath $script:FichierMotDePasse -PathType Leaf) -and ((Get-Item -LiteralPath $script:FichierMotDePasse).Length -gt 0)) {
            expliquer 'mot de passe de sauvegarde déjà enregistré : conservé'
        } else {
            expliquer 'saisie masquée du mot de passe de sauvegarde (deux fois, 12 caractères au moins)'
            sortie ('+ écrire le mot de passe (saisie masquée) dans {0} (accès : utilisateur courant)' -f (citer $script:FichierMotDePasse))
        }
        expliquer 'planification quotidienne à 12 h 30 : sauvegarder.ps1 --auto (sauvegarde puis élagage)'
        $destination = 'E:\Sauvegardes-Territoria'
    } else {
        exiger_terminal
        if (-not (Test-Path -LiteralPath $script:Secrets -PathType Container)) {
            echouer secrets_absents "Lancez d'abord install\installer.ps1 --etape secrets."
        }
        if (-not (donnees_planifiables)) {
            echouer donnees_non_standard "La tâche planifiée ne retrouverait pas territoria-donnees : placez-le à côté du dépôt, ou définissez TERRITORIA_DONNEES_HOTE comme variable d'environnement permanente de votre compte, puis relancez l'étape."
        }
        $destination = lire_reponse "Dossier de destination des sauvegardes (chemin absolu d'un dossier existant)"
        verifier_destination $destination
        if ((Test-Path -LiteralPath $script:FichierMotDePasse -PathType Leaf) -and ((Get-Item -LiteralPath $script:FichierMotDePasse).Length -gt 0)) {
            info ('Mot de passe de sauvegarde déjà enregistré ({0}) : conservé.' -f $script:FichierMotDePasse)
        } else {
            $motdepasse = texte_du_secret (lire_secret 'Mot de passe des sauvegardes (saisie masquée, 12 caractères au moins)')
            $confirmation = texte_du_secret (lire_secret 'Confirmez le mot de passe')
            if ($motdepasse.Length -lt 12) { echouer mot_de_passe_trop_court 'Le mot de passe doit compter au moins 12 caractères.' }
            if ($motdepasse -cne $confirmation) { echouer saisies_differentes 'Les deux saisies diffèrent : rien n''a été enregistré.' }
            $confirmation = ''
            ecrire_mot_de_passe $motdepasse
            $motdepasse = ''
            info ('Mot de passe enregistré dans {0} (accès réservé à votre compte). Gardez-en une copie ailleurs : sans lui, les sauvegardes sont illisibles.' -f $script:FichierMotDePasse)
        }
    }
    planifier_windows $destination
    if (-not $script:Simulation) {
        info ('Sauvegarde planifiée tous les jours à 12 h 30 vers {0} (tâche {1}).' -f $destination, $script:NomTache)
    }
}

# --- Arguments et lancement -----------------------------------------------------------------------------------------

# Lancé seulement quand le script est exécuté : les essais le chargent par « . installer.ps1 » pour appeler ses fonctions.
function principal {
    param([string[]]$arguments)
    $i = 0
    while ($i -lt $arguments.Count) {
        $option = $arguments[$i]
        if ($option -ceq '--etape') {
            if ($i + 1 -ge $arguments.Count) { refuser_arguments 'installer' }
            $script:Etape = $arguments[$i + 1]
            $i += 2
        } elseif ($option -ceq '--simulation') {
            activer_simulation
            $i++
        } elseif ($option -ceq '--auto') {
            $script:Auto = $true
            $i++
        } elseif ($option -ceq '--palier') {
            if ($i + 1 -ge $arguments.Count) { refuser_arguments 'installer' }
            $script:Palier = $arguments[$i + 1]
            $i += 2
        } elseif ($option -ceq '-h' -or $option -ceq '--help') {
            usage
            exit 0
        } else {
            refuser_arguments 'installer'
        }
    }

    switch -CaseSensitive ($script:Etape) {
        { $_ -cin @('moteur', 'lmstudio', 'mistral', 'secrets', 'legifrance', 'demarrer', 'n8n', 'demo', 'verifier', 'raccourci', 'sauvegarde-auto') } { break }
        '' { refuser_arguments 'installer' }
        default { refuser_arguments 'installer' 'etape_inconnue' }
    }
    if ($script:Auto -and $script:Etape -cne 'raccourci') { refuser_arguments $script:Etape }
    if ($script:Palier.Length -gt 0) {
        if (@('petit', 'moyen', 'grand') -cnotcontains $script:Palier) { refuser_arguments $script:Etape 'palier_inconnu' }
        if ($script:Etape -cne 'lmstudio') { refuser_arguments $script:Etape }
    }

    executer_etape $script:Etape {
        expliquer ("simulation de l'étape {0} : rien n'est modifié ; commandes qui seraient lancées :" -f $script:Etape)
        switch -CaseSensitive ($script:Etape) {
            'moteur' { etape_moteur }
            'lmstudio' { etape_lmstudio }
            'mistral' { etape_mistral }
            'secrets' { etape_secrets }
            'legifrance' { etape_legifrance }
            'demarrer' { etape_demarrer }
            'n8n' { etape_n8n }
            'demo' { etape_demo }
            'verifier' { etape_verifier }
            'raccourci' { etape_raccourci }
            'sauvegarde-auto' { etape_sauvegarde_auto }
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    principal @($args | ForEach-Object { [string]$_ })
    exit $script:CodeSortie
}
