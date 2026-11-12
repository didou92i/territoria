# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Fonctions communes des scripts d'installation de Territoria pour Windows (install/*.ps1), miroir de commun.sh.
# Ce fichier est chargé par « . commun.ps1 », jamais exécuté seul ; le charger ne crée rien, n'affiche rien.
#
# Contrat de chaque script : la dernière ligne de la sortie standard est « ETAPE <nom> : OK » ou
# « ETAPE <nom> : ECHEC <code> » (code en minuscules, chiffres et tirets bas), et le code de sortie vaut 0 ou 1.
# La progression va sur la sortie d'erreur ; elle ne montre ni secret ni contenu de la collectivité.
# Avec --simulation, chaque commande qui modifierait la machine est affichée (« + commande ») sans être lancée,
# aucune question n'est posée et rien n'est créé.
#
# Compatibilité : Windows PowerShell 5.1 (celui de Windows 10 et 11) et PowerShell 7. Ni « ?? », ni opérateur
# ternaire, ni « && » / « || » entre commandes, ni $IsWindows, ni Join-Path à trois arguments, ni ArgumentList de
# ProcessStartInfo (absents de la version 5.1). Ce fichier contient des accents : il est enregistré en UTF-8 avec
# BOM, sans quoi Windows PowerShell 5.1 le lirait en ANSI.
#
# Les fonctions portent le nom de leur équivalent de commun.sh. Les fonctions appelées avec des arguments qui
# ressemblent à des options (-p, -f, -v, --rm…) n'ont pas de bloc param : elles lisent $args, sans quoi PowerShell
# chercherait un paramètre du même nom.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:Utf8 = New-Object System.Text.UTF8Encoding($false)
# Sorties et entrées des programmes natifs en UTF-8 sans BOM (Windows PowerShell 5.1 prend sinon l'ASCII pour les
# entrées de programmes : un mot de passe accentué serait altéré).
try { [Console]::OutputEncoding = $script:Utf8 } catch { $null = $_ }
$OutputEncoding = $script:Utf8

$script:EstWindows = ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT)

# --- Chemins -------------------------------------------------------------------------------------------------------

# Assemble un chemin par Join-Path à deux arguments (Windows PowerShell 5.1 n'en accepte pas davantage).
function joindre {
    param([string]$base, [string[]]$suite)
    if ([string]::IsNullOrEmpty($base)) { return '' }
    $chemin = $base
    foreach ($element in $suite) { $chemin = Join-Path -Path $chemin -ChildPath $element }
    return $chemin
}

function dossier_personnel {
    $dossier = $env:USERPROFILE
    if ([string]::IsNullOrEmpty($dossier)) { $dossier = $env:HOME }
    if ([string]::IsNullOrEmpty($dossier)) { $dossier = [Environment]::GetFolderPath('UserProfile') }
    return $dossier
}

function dossier_bureau {
    if ($script:EstWindows) { return [Environment]::GetFolderPath('Desktop') }
    return (joindre (dossier_personnel) @('Desktop'))
}

function dossier_demarrage {
    if ($script:EstWindows) { return [Environment]::GetFolderPath('Startup') }
    return (joindre (dossier_personnel) @('Startup'))
}

$script:Racine = Split-Path -Parent $PSScriptRoot

# Dossier qui contient $chemin (le chemin lui-même à la racine d'un lecteur).
function dossier_parent {
    param([string]$chemin)
    $parent = [System.IO.Path]::GetDirectoryName($chemin.TrimEnd('\', '/'))
    if ([string]::IsNullOrEmpty($parent)) { return $chemin }
    return $parent
}

# Projet Compose et fichiers : une installation réelle garde toujours « -p territoria ».
$script:Projet = 'territoria'
$script:FichiersCompose = @('-f', (joindre $script:Racine @('conteneurs', 'compose.yaml')))
$script:AdresseTerritoria = 'http://127.0.0.1:8878'
$script:PortLms = 1234
$script:AttenteMax = 300  # secondes d'attente de la bonne santé d'un service

# Dossier de données : territoria-donnees, à côté du dépôt (jamais dedans). Une valeur déjà exportée l'emporte
# (dossier temporaire des essais).
if ([string]::IsNullOrEmpty($env:TERRITORIA_DONNEES_HOTE)) {
    $script:Donnees = Join-Path -Path (dossier_parent $script:Racine) -ChildPath 'territoria-donnees'
} else {
    $script:Donnees = $env:TERRITORIA_DONNEES_HOTE
}
$env:TERRITORIA_DONNEES_HOTE = $script:Donnees
$script:Secrets = Join-Path -Path $script:Donnees -ChildPath 'secrets'
$script:FichierMotDePasse = Join-Path -Path $script:Secrets -ChildPath 'sauvegarde.motdepasse'

# Image Python épinglée de territoria.Dockerfile : conteneurs jetables en root (propriétaires des dossiers, accès réseau).
$script:ImagePython = ''
$dockerfile = joindre $script:Racine @('conteneurs', 'territoria.Dockerfile')
if (Test-Path -LiteralPath $dockerfile -PathType Leaf) {
    foreach ($ligneDockerfile in [System.IO.File]::ReadAllLines($dockerfile, $script:Utf8)) {
        $trouve = [regex]::Match($ligneDockerfile, '^FROM (python:3\S*)')
        if ($trouve.Success) {
            $script:ImagePython = $trouve.Groups[1].Value
            break
        }
    }
}

# Emplacements habituels de Podman, de Docker Desktop, de winget et de la commande lms, ajoutés au PATH du processus
# (les tâches planifiées et les raccourcis démarrent avec un environnement réduit ; une installation par winget ne
# modifie pas le PATH de la session en cours).
function ajouter_au_chemin {
    param([string]$dossier)
    if ([string]::IsNullOrEmpty($dossier)) { return }
    if (-not (Test-Path -LiteralPath $dossier -PathType Container)) { return }
    $separateur = [System.IO.Path]::PathSeparator
    $actuel = $env:PATH
    if ($null -eq $actuel) { $actuel = '' }
    foreach ($element in $actuel.Split($separateur)) {
        if ($element.TrimEnd('\', '/') -eq $dossier.TrimEnd('\', '/')) { return }
    }
    $env:PATH = $actuel + $separateur + $dossier
}

function actualiser_chemin {
    if ($script:EstWindows) {
        foreach ($cible in @('Machine', 'User')) {
            $valeur = [Environment]::GetEnvironmentVariable('Path', $cible)
            if ($null -ne $valeur) {
                foreach ($element in $valeur.Split(';')) { ajouter_au_chemin ([Environment]::ExpandEnvironmentVariables($element)) }
            }
        }
    }
    ajouter_au_chemin (joindre ([string]$env:ProgramFiles) @('RedHat', 'Podman'))
    ajouter_au_chemin (joindre ([string]$env:ProgramFiles) @('Docker', 'Docker', 'resources', 'bin'))
    ajouter_au_chemin (joindre ([string]$env:LOCALAPPDATA) @('Microsoft', 'WinGet', 'Links'))
    ajouter_au_chemin (joindre (dossier_personnel) @('.lmstudio', 'bin'))
}
if ($script:EstWindows) { actualiser_chemin }

$script:Simulation = $false
$script:Moteur = ''        # docker ou podman, fixé par choisir_moteur
$script:NomEtape = ''
$script:CodeEchec = ''
$script:CodeSortie = 0
$script:DerniereLigne = ''
$script:DossierLms = ''
$script:Image = ''
$global:LASTEXITCODE = 0

# --- Sorties, codes d'échec et fin d'étape -------------------------------------------------------------------------

# Progression : sortie d'erreur. Sortie standard : lignes ETAPE, simulation, diagnostic (jamais par le pipeline, pour
# qu'aucune valeur de retour ne s'y glisse).
function info {
    [Console]::Error.WriteLine(($args -join ' '))
}

function sortie {
    [Console]::Out.WriteLine(($args -join ' '))
}

# Code imprimé si la suite échoue ; à poser avant chaque opération qui peut échouer.
function phase {
    param([string]$code)
    $script:CodeEchec = $code
}

function echouer {
    param([string]$code, [string]$message = '')
    $script:CodeEchec = $code
    if ($message.Length -gt 0) { info $message }
    throw 'territoria-echec'
}

# Code d'échec rendu tel quel s'il n'a que des minuscules, des chiffres et des tirets bas (n8n_pas_sain,
# n8n_provisionnement) ; sinon erreur_inattendue.
function code_propre {
    param([string]$code)
    if ($code -cmatch '^[a-z0-9_]+$') { return $code }
    return 'erreur_inattendue'
}

# Exécute le corps d'une étape et imprime toujours la ligne ETAPE finale, quoi qu'il arrive (échec, exception,
# interruption par Ctrl+C). Le code de sortie est rangé dans $script:CodeSortie.
function executer_etape {
    param([string]$nom, [scriptblock]$corps)
    $script:NomEtape = $nom
    $script:CodeEchec = ''
    $reussi = $false
    $exception = $false
    try {
        $null = & $corps
        $reussi = $true
    } catch {
        $exception = $true
        $message = $_.Exception.Message
        if ($message -ne 'territoria-echec') {
            info ('Erreur : ' + $message)
        }
    } finally {
        if ($reussi) {
            $script:DerniereLigne = "ETAPE $nom : OK"
            $script:CodeSortie = 0
        } else {
            $code = 'interrompu'
            if ($exception) { $code = code_propre $script:CodeEchec }
            $script:DerniereLigne = "ETAPE $nom : ECHEC $code"
            $script:CodeSortie = 1
        }
        sortie $script:DerniereLigne
    }
}

# Échec d'arguments, avant toute étape : même contrat, sans rien lancer.
function refuser_arguments {
    param([string]$nom, [string]$code = 'arguments_invalides')
    sortie "ETAPE $nom : ECHEC $code"
    exit 1
}

# --- Simulation ----------------------------------------------------------------------------------------------------

function activer_simulation {
    $script:Simulation = $true
}

# Mot d'une commande affichée ou à recopier dans PowerShell, protégé comme le ferait PowerShell : tel quel s'il n'a que
# des caractères sans effet (ni virgule, qui ferait un tableau, ni @ en tête, qui ferait une projection), sinon entre
# apostrophes droites. PowerShell lit aussi les apostrophes typographiques (‘ ’ ‚ ‛) comme des apostrophes : chacune
# est doublée, comme l'apostrophe droite.
function citer {
    param([string]$mot)
    if ($mot -cmatch '^[A-Za-z0-9_%+=:./\\-][A-Za-z0-9_@%+=:./\\-]*$') { return $mot }
    return "'" + ($mot -replace "(['\u2018\u2019\u201A\u201B])", '$1$1') + "'"
}

function afficher_commande {
    param([object[]]$mots)
    $rendu = @()
    foreach ($mot in $mots) { $rendu += (citer ([string]$mot)) }
    sortie ('+ ' + ($rendu -join ' '))
}

# Ligne d'explication en simulation (rien sinon).
function expliquer {
    if ($script:Simulation) { sortie ('# ' + ($args -join ' ')) }
}

# --- Lancement des programmes natifs ---------------------------------------------------------------------------------

# Chemin d'un programme (jamais d'alias ni de fonction) ; les appels se font par le nom nu (schtasks, icacls,
# winget, manage-bde, docker…), jamais « .exe » : les essais placent des programmes factices dans le PATH.
function resoudre_exe {
    param([string]$nom)
    $trouve = Get-Command -Name $nom -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $trouve) { throw ('Commande introuvable : ' + $nom) }
    return $trouve.Source
}

function commande_presente {
    param([string]$nom)
    return ($null -ne (Get-Command -Name $nom -CommandType Application -ErrorAction SilentlyContinue))
}

# Un argument vide ou contenant une double apostrophe serait altéré par Windows PowerShell 5.1.
function verifier_arguments {
    param([string[]]$arguments)
    foreach ($argument in $arguments) {
        if ($argument.Length -eq 0 -or $argument.Contains('"') -or ($argument -match '\s' -and $argument.EndsWith('\'))) {
            throw 'Argument de commande non pris en charge.'
        }
    }
}

# Mot d'une ligne de commande Windows (règles de CommandLineToArgvW) : les barres inverses qui précèdent un guillemet
# (ou la fin du mot) sont doublées. Avec $toujours, le mot est entre guillemets même sans espace (raccourcis, tâche
# planifiée : lignes de commande lues par un humain).
function citer_windows {
    param([string]$mot, [bool]$toujours = $false)
    if (-not $toujours -and $mot.Length -gt 0 -and $mot -notmatch '[\s"]') { return $mot }
    $texte = New-Object System.Text.StringBuilder
    [void]$texte.Append('"')
    $barres = 0
    foreach ($c in $mot.ToCharArray()) {
        if ($c -eq '\') {
            $barres++
        } elseif ($c -eq '"') {
            [void]$texte.Append('\' * ($barres * 2 + 1))
            [void]$texte.Append('"')
            $barres = 0
        } else {
            if ($barres -gt 0) { [void]$texte.Append('\' * $barres) }
            $barres = 0
            [void]$texte.Append($c)
        }
    }
    if ($barres -gt 0) { [void]$texte.Append('\' * ($barres * 2)) }
    [void]$texte.Append('"')
    return $texte.ToString()
}

function ligne_commande {
    param([string[]]$arguments)
    $rendu = @()
    foreach ($argument in $arguments) { $rendu += (citer_windows $argument) }
    return ($rendu -join ' ')
}

# Lance un programme et rend @{ Code; Sortie } : sortie standard capturée (UTF-8), octets $entree envoyés sur l'entrée
# standard (aucun octet si $null : elle est fermée aussitôt), délai en secondes (0 = sans limite), erreurs
# standard laissées à la console, ou écartées avec $taire. Le mot de passe d'une sauvegarde passe par ici : octets
# exacts, jamais un argument ni un fichier.
function executer {
    param([string]$nom, [string[]]$arguments, [byte[]]$entree = $null, [int]$delai = 0, [bool]$taire = $false)
    $infos = New-Object System.Diagnostics.ProcessStartInfo
    $infos.FileName = resoudre_exe $nom
    $infos.Arguments = ligne_commande $arguments
    $infos.UseShellExecute = $false
    $infos.CreateNoWindow = $true
    $infos.RedirectStandardInput = $true
    $infos.RedirectStandardOutput = $true
    $infos.StandardOutputEncoding = $script:Utf8
    if ($taire) {
        $infos.RedirectStandardError = $true
        $infos.StandardErrorEncoding = $script:Utf8
    }
    $processus = [System.Diagnostics.Process]::Start($infos)
    $lecture = $processus.StandardOutput.ReadToEndAsync()
    $lectureErreur = $null
    if ($taire) { $lectureErreur = $processus.StandardError.ReadToEndAsync() }
    # Les octets vont directement dans le flux sous-jacent, qui est fermé lui-même : fermer le StreamWriter de
    # StandardInput ajouterait le BOM de l'encodage de la console si celle-ci est en UTF-8.
    $flux = $processus.StandardInput.BaseStream
    try {
        if ($null -ne $entree -and $entree.Length -gt 0) {
            $flux.Write($entree, 0, $entree.Length)
            $flux.Flush()
        }
    } catch { $null = $_ }
    try { $flux.Close() } catch { $null = $_ }
    $code = 0
    if ($delai -gt 0) {
        if (-not $processus.WaitForExit($delai * 1000)) {
            try { $processus.Kill() } catch { $null = $_ }
            $code = -1
        }
    }
    $processus.WaitForExit()
    if ($code -eq 0) { $code = $processus.ExitCode }
    $texte = ''
    if ($lecture.Wait(5000)) { $texte = $lecture.Result }
    if ($null -ne $lectureErreur) { [void]$lectureErreur.Wait(5000) }
    $processus.Dispose()
    return [pscustomobject]@{ Code = $code; Sortie = $texte }
}

# Commande avec un délai maximal : vrai si elle se termine par 0.
function avec_delai {
    param([int]$delai, [string]$nom, [string[]]$arguments)
    try {
        return ((executer $nom $arguments $null $delai $true).Code -eq 0)
    } catch {
        return $false
    }
}

# Lance un programme en recopiant sa sortie sur la sortie d'erreur ; un code autre que ceux de $codes arrête l'étape.
# Simulation : la commande est affichée, jamais lancée.
function lancer_tableau {
    param([string]$nom, [string[]]$arguments, [int[]]$codes = @(0))
    if ($script:Simulation) {
        afficher_commande (@($nom) + $arguments)
        return
    }
    $chemin = resoudre_exe $nom
    verifier_arguments $arguments
    $ErrorActionPreference = 'Continue'  # sans quoi Windows PowerShell 5.1 lève une erreur dès la première ligne d'erreur d'un programme
    $global:LASTEXITCODE = 0
    & $chemin @arguments 2>&1 | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
    $code = $LASTEXITCODE
    if ($codes -notcontains $code) {
        throw ('La commande {0} a échoué (code {1}).' -f [System.IO.Path]::GetFileNameWithoutExtension($chemin), $code)
    }
}

function lancer {
    $mots = @($args | ForEach-Object { [string]$_ })
    lancer_tableau $mots[0] @($mots | Select-Object -Skip 1)
}

# Commande qui peut laisser un démon derrière elle (serveur LM Studio, machine Podman, application) : ses entrées et
# sorties ne restent pas attachées à la console de l'appelant (sinon l'attente de sa fin ne finirait jamais) ; sa
# sortie est recopiée ensuite sur la sortie d'erreur.
# L'entrée standard n'est redirigée (fichier vide) que sous Windows, où Start-Process passe la poignée du fichier à
# CreateProcess. Ailleurs (les essais sous Linux), Start-Process recopie le fichier dans un tube après le démarrage et
# lève « Broken pipe » si le programme s'est déjà terminé, alors qu'il a bien tourné : l'enfant y garde l'entrée de
# l'appelant.
# Hors Windows encore, Start-Process recopie aussi les sorties des tubes vers les fichiers de façon asynchrone, et
# WaitForExit avec un délai n'attend pas la fin de cette recopie (qui n'arrive qu'à la fermeture des tubes, jamais tant
# qu'un démon les garde) : lue aussitôt, la sortie pouvait manquer. La commande y écrit donc directement dans ses fichiers
# (redirection par sh), comme sous Windows où l'enfant reçoit les poignées des fichiers.
function lancer_detache {
    $mots = @($args | ForEach-Object { [string]$_ })
    if ($script:Simulation) {
        afficher_commande $mots
        return
    }
    $suite = @($mots | Select-Object -Skip 1)
    $chemin = resoudre_exe $mots[0]
    $entree = [System.IO.Path]::GetTempFileName()
    $sortieFichier = [System.IO.Path]::GetTempFileName()
    $erreurFichier = [System.IO.Path]::GetTempFileName()
    $fichiers = @($sortieFichier, $erreurFichier)
    try {
        $demarrage = @{
            FilePath = $chemin; PassThru = $true; NoNewWindow = $true
            RedirectStandardOutput = $sortieFichier; RedirectStandardError = $erreurFichier
        }
        if ($script:EstWindows) {
            $demarrage['RedirectStandardInput'] = $entree
            if ($suite.Count -gt 0) { $demarrage['ArgumentList'] = (ligne_commande $suite) }
        } else {
            # Sorties de la commande, lues en premier ; celles de Start-Process ne gardent que les messages de sh.
            $fichiers = @([System.IO.Path]::GetTempFileName(), [System.IO.Path]::GetTempFileName()) + $fichiers
            $demarrage['FilePath'] = '/bin/sh'
            $demarrage['ArgumentList'] = (ligne_commande (@('-c', 'f=$1; e=$2; shift 2; exec "$@" >"$f" 2>"$e"', 'sh',
                $fichiers[0], $fichiers[1], $chemin) + $suite))
        }
        $processus = Start-Process @demarrage
        $null = $processus.Handle  # sans lui, Windows PowerShell 5.1 ne retrouve pas le code de sortie
        if (-not $processus.WaitForExit(600000)) {
            try { $processus.Kill() } catch { $null = $_ }
            throw ('La commande {0} ne répond plus.' -f [System.IO.Path]::GetFileNameWithoutExtension($chemin))
        }
        foreach ($fichier in $fichiers) {
            $texte = [System.IO.File]::ReadAllText($fichier, $script:Utf8)
            if ($texte.Length -gt 0) { [Console]::Error.Write($texte) }
        }
        if ($processus.ExitCode -ne 0) {
            throw ('La commande {0} a échoué (code {1}).' -f [System.IO.Path]::GetFileNameWithoutExtension($chemin), $processus.ExitCode)
        }
    } finally {
        Remove-Item -LiteralPath (@($entree) + $fichiers) -Force -ErrorAction SilentlyContinue
    }
}

# --- Machine -------------------------------------------------------------------------------------------------------

function systeme {
    if ($script:EstWindows) { return 'windows' }
    if (Test-Path -LiteralPath '/System/Library/CoreServices') { return 'macos' }
    return 'linux'
}

# Mémoire vive en Go, arrondie au plus proche (0 si elle ne peut pas être lue).
function memoire_go {
    $octets = [long]0
    try {
        if ($script:EstWindows) {
            $octets = [long](Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory
        } elseif (Test-Path -LiteralPath '/proc/meminfo') {
            foreach ($ligne in [System.IO.File]::ReadAllLines('/proc/meminfo')) {
                $trouve = [regex]::Match($ligne, '^MemTotal:\s+(\d+)')
                if ($trouve.Success) { $octets = [long]$trouve.Groups[1].Value * 1024; break }
            }
        } else {
            $octets = [long][GC]::GetGCMemoryInfo().TotalAvailableMemoryBytes
        }
    } catch { $octets = 0 }
    return [int][math]::Floor(($octets + 536870912) / 1073741824)
}

# Palier du modèle selon la mémoire (seuils indicatifs de la spécification, à calibrer en recette).
function palier_modele {
    param([int]$memoire)
    if ($memoire -lt 12) { return 'petit' }
    if ($memoire -lt 28) { return 'moyen' }
    return 'grand'
}

# Identifiant « lms get » du palier, lu dans install/modeles.json (vide s'il manque).
function modele_du_palier {
    param([string]$palier)
    $fichier = joindre $script:Racine @('install', 'modeles.json')
    $modeles = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($fichier, $script:Utf8))
    $propriete = $modeles.PSObject.Properties[$palier]
    if ($null -eq $propriete) { return '' }
    return [string]$propriete.Value
}

# Dossier de LM Studio, résolu comme le fait son installateur officiel : le chemin écrit dans
# %USERPROFILE%\.lmstudio-home-pointer s'il existe, sinon %USERPROFILE%\.lmstudio, sinon %LOCALAPPDATA%\lm-studio (autre
# emplacement documenté par l'installateur). Vide si aucun n'existe, ou si le pointeur désigne un dossier absent :
# le dossier n'est alors jamais deviné. Le pointeur est lu en UTF-8 (un nom de profil accentué reste lisible).
function dossier_lmstudio {
    $maison = dossier_personnel
    $pointeur = Join-Path -Path $maison -ChildPath '.lmstudio-home-pointer'
    if (Test-Path -LiteralPath $pointeur -PathType Leaf) {
        $contenu = [System.IO.File]::ReadAllText($pointeur, $script:Utf8)
        $dossier = ($contenu -split "\r?\n")[0].Trim()
        if ($dossier.Length -gt 0 -and [System.IO.Path]::IsPathRooted($dossier) -and (Test-Path -LiteralPath $dossier -PathType Container)) {
            return $dossier
        }
        return ''
    }
    $candidats = @((Join-Path -Path $maison -ChildPath '.lmstudio'))
    if (-not [string]::IsNullOrEmpty($env:LOCALAPPDATA)) { $candidats += (Join-Path -Path $env:LOCALAPPDATA -ChildPath 'lm-studio') }
    foreach ($candidat in $candidats) {
        if (Test-Path -LiteralPath $candidat -PathType Container) { return $candidat }
    }
    return ''
}

# Chemin de la commande lms de LM Studio (vide si absente).
function chemin_lms {
    $trouve = Get-Command -Name 'lms' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $trouve) { return $trouve.Source }
    $nom = 'lms'
    if ($script:EstWindows) { $nom = 'lms.exe' }
    $candidats = @()
    $dossier = dossier_lmstudio
    if ($dossier.Length -gt 0) { $candidats += (joindre $dossier @('bin', $nom)) }
    if (-not [string]::IsNullOrEmpty($env:LOCALAPPDATA)) {
        $candidats += (joindre $env:LOCALAPPDATA @('Programs', 'LM Studio', 'resources', 'app', '.webpack', $nom))
    }
    foreach ($candidat in $candidats) {
        if (Test-Path -LiteralPath $candidat -PathType Leaf) { return $candidat }
    }
    return ''
}

# Moteur installé, qu'il tourne ou non : docker, podman ou aucun.
function moteur_present {
    if (commande_presente 'docker') { return 'docker' }
    if (commande_presente 'podman') { return 'podman' }
    return 'aucun'
}

function moteur_repond {
    param([string]$nom)
    return (avec_delai 30 $nom @('info'))
}

# Moteur utilisé : Docker s'il répond, sinon Podman s'il répond, sinon celui qui est installé (arrêté), sinon aucun.
# En simulation, aucun moteur n'est interrogé ; sans moteur installé, on affiche celui que l'étape moteur installerait
# (Podman).
function choisir_moteur {
    if ($script:Simulation) {
        $script:Moteur = moteur_present
        if ($script:Moteur -eq 'aucun') { $script:Moteur = 'podman' }
        return
    }
    $installe = ''
    if (commande_presente 'docker') {
        if (moteur_repond 'docker') { $script:Moteur = 'docker'; return }
        $installe = 'docker'
    }
    if (commande_presente 'podman') {
        if (moteur_repond 'podman') { $script:Moteur = 'podman'; return }
        if ($installe.Length -eq 0) { $installe = 'podman' }
    }
    if ($installe.Length -eq 0) { $installe = 'aucun' }
    $script:Moteur = $installe
}

# Moteur choisi et en marche, ou échec explicite (sans le démarrer).
function exiger_moteur {
    choisir_moteur
    if ($script:Moteur -eq 'aucun') {
        echouer moteur_absent "Aucun moteur de conteneurs : lancez d'abord install\installer.ps1 --etape moteur."
    }
    if ($script:Simulation) { return }
    if (moteur_repond $script:Moteur) { return }
    echouer moteur_arrete ('Le moteur de conteneurs ({0}) ne répond pas : démarrez-le (install\demarrer.ps1 le fait).' -f $script:Moteur)
}

function docker_desktop_installe {
    $dossier = [string]$env:ProgramFiles
    if ($dossier.Length -eq 0) { return '' }
    $exe = joindre $dossier @('Docker', 'Docker', 'Docker Desktop.exe')
    if (Test-Path -LiteralPath $exe -PathType Leaf) { return $exe }
    return ''
}

# Démarre le moteur s'il est installé mais arrêté : machine Podman, Docker Desktop.
function assurer_moteur_actif {
    choisir_moteur
    if ($script:Moteur -eq 'aucun') {
        echouer moteur_absent "Aucun moteur de conteneurs : lancez d'abord install\installer.ps1 --etape moteur."
    }
    if ($script:Simulation) {
        if ($script:Moteur -eq 'podman') {
            expliquer 'si la machine Podman est arrêtée :'
            lancer podman machine start
        }
        return
    }
    if (moteur_repond $script:Moteur) { return }
    if ($script:Moteur -eq 'podman') {
        info 'Démarrage de la machine Podman…'
        try { lancer_detache podman machine start } catch { info $_.Exception.Message }
    } elseif ($script:Moteur -eq 'docker') {
        $application = docker_desktop_installe
        if ($application.Length -gt 0) {
            info 'Démarrage de Docker Desktop…'
            try { Start-Process -FilePath $application } catch { info $_.Exception.Message }
        }
    }
    $debut = [DateTime]::UtcNow
    while (-not (moteur_repond $script:Moteur)) {
        if (([DateTime]::UtcNow - $debut).TotalSeconds -ge 180) {
            echouer moteur_arrete ('Le moteur de conteneurs ({0}) ne répond toujours pas après 180 s.' -f $script:Moteur)
        }
        Start-Sleep -Seconds 5
    }
}

# Adresse des conteneurs vers LM Studio sur la machine (docs/acces-lmstudio.md).
function url_lms_conteneurs {
    if ($script:Moteur -eq 'podman') { return ('http://host.containers.internal:{0}/v1' -f $script:PortLms) }
    return ('http://host.docker.internal:{0}/v1' -f $script:PortLms)
}

# Adresse d'écoute de LM Studio : 127.0.0.1 sous Windows (Docker Desktop et la machine Podman relaient
# host.docker.internal / host.containers.internal vers la boucle locale de la machine).
function adresse_ecoute_lms {
    return '127.0.0.1'
}

# --- Compose et conteneurs -----------------------------------------------------------------------------------------

# Arguments de « <moteur> compose » pour ce projet ; en simulation, MOTEUR peut ne pas être encore installé : la
# commande affichée est celle qui serait lancée.
function arguments_compose {
    param([string[]]$suite)
    $fichiers = $script:FichiersCompose
    $fichierEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    return (@('compose', '-p', $script:Projet, '--env-file', $fichierEnv) + $fichiers + $suite)
}

function compose {
    $mots = @($args | ForEach-Object { [string]$_ })
    lancer_tableau $script:Moteur (arguments_compose $mots)
}

function moteur {
    $mots = @($args | ForEach-Object { [string]$_ })
    lancer_tableau $script:Moteur $mots
}

# Utilisateur des commandes « outils » jetables : root dans le conteneur. Sous Windows les fichiers créés dans un
# dossier partagé appartiennent à l'utilisateur de la machine, quel que soit l'identifiant vu du conteneur.
function utilisateur_outils {
    return '0:0'
}

# Contrôle la sortie d'une commande « outils » : sa dernière ligne doit être « ETAPE <commande> : OK ». La sortie est
# recopiée sur la sortie d'erreur ; en cas d'échec, CodeEchec reçoit le code d'outils et l'étape s'arrête.
function exiger_etape {
    param([string]$commande, [string]$sortieOutils)
    $texte = $sortieOutils.TrimEnd("`r", "`n")
    $derniere = ($texte -split "\r?\n")[-1]
    if ($texte.Length -gt 0) { [Console]::Error.WriteLine($texte) }
    if ($derniere -eq "ETAPE $commande : OK") { return }
    $debut = "ETAPE $commande : ECHEC "
    if ($derniere.StartsWith($debut)) {
        $script:CodeEchec = code_propre $derniere.Substring($debut.Length)
    } else {
        $script:CodeEchec = 'sortie_inattendue'
    }
    info "Attendu : ETAPE $commande : OK"
    throw 'territoria-echec'
}

# Lance « <moteur> <arguments> » et rend sa sortie standard ; en simulation, l'affiche seulement.
function sortie_du_moteur {
    param([string[]]$arguments, [byte[]]$entree = $null)
    if ($script:Simulation) {
        afficher_commande (@($script:Moteur) + $arguments)
        return ''
    }
    return (executer $script:Moteur $arguments $entree).Sortie
}

# « outils <commande> » dans un conteneur jetable de l'image territoria, sur le dossier des secrets (sans réseau
# requis, sans dépendances démarrées).
function outils_jetable {
    $mots = @($args | ForEach-Object { [string]$_ })
    $arguments = arguments_compose (@('run', '--rm', '--no-deps', '-T', '--user', (utilisateur_outils), '-v',
        ('{0}:/donnees-secrets' -f $script:Secrets), 'territoria', 'python', '-m', 'outils') + $mots)
    $resultat = sortie_du_moteur $arguments
    if (-not $script:Simulation) { exiger_etape $mots[0] $resultat }
}

# « outils <commande> » dans le service territoria démarré (il voit /donnees et joint n8n).
function outils_service {
    $mots = @($args | ForEach-Object { [string]$_ })
    $arguments = arguments_compose (@('exec', '-T', 'territoria', 'python', '-m', 'outils') + $mots)
    $resultat = sortie_du_moteur $arguments
    if (-not $script:Simulation) { exiger_etape $mots[0] $resultat }
}

# Conteneur ponctuel de l'image Territoria pour les sauvegardes et restaurations : root, sans réseau, racine en
# lecture seule. Le clair déchiffré reste dans /tmp, en mémoire (tmpfs). Les montages et l'image sont passés en
# arguments ; le mot de passe arrive par l'entrée standard (octets) ou par un fichier monté, jamais en argument.
function ponctuel {
    param([byte[]]$entree, [string[]]$suite)
    $arguments = @('run', '--rm', '-i', '--network', 'none', '--user', '0:0', '--read-only', '--tmpfs', '/tmp:mode=1777') + $suite
    return (sortie_du_moteur $arguments $entree)
}

# Image territoria construite pour ce projet (vide si absente). « compose images » ne voit que les images des
# conteneurs créés : après « run --rm », après « down » ou sur une machine où l'on restaure, on la cherche par son nom
# de construction, <projet>-territoria (Compose v2 ; <projet>_territoria pour podman-compose, à valider en recette).
function image_territoria {
    if ($script:Simulation) { return ('<image territoria du projet {0}>' -f $script:Projet) }
    $image = ''
    try {
        $resultat = executer $script:Moteur (arguments_compose @('images', '-q', 'territoria')) $null 60 $true
        $image = (($resultat.Sortie -split "\r?\n") | Where-Object { $_.Trim().Length -gt 0 } | Select-Object -First 1)
        if ($null -eq $image) { $image = '' }
    } catch { $image = '' }
    foreach ($nom in @(($script:Projet + '-territoria'), ($script:Projet + '_territoria'))) {
        if ($image.Length -gt 0) { break }
        $resultat = executer $script:Moteur @('image', 'inspect', '--format', '{{.Id}}', $nom) $null 60 $true
        if ($resultat.Code -eq 0) { $image = $resultat.Sortie.Trim() }
    }
    return $image
}

function attendre_sain {
    param([string]$service)
    if ($script:Simulation) {
        expliquer ('attente de la bonne santé du service {0} (au plus {1} s)' -f $service, $script:AttenteMax)
        return
    }
    $debut = [DateTime]::UtcNow
    while ($true) {
        $etat = ''
        $conteneur = ''
        $resultat = executer $script:Moteur (arguments_compose @('ps', '-q', $service)) $null 60 $true
        if ($resultat.Code -eq 0) { $conteneur = ($resultat.Sortie -split "\r?\n")[0].Trim() }
        if ($conteneur.Length -gt 0) {
            $formule = '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
            $inspection = executer $script:Moteur @('inspect', '--format', $formule, $conteneur) $null 60 $true
            if ($inspection.Code -eq 0) { $etat = $inspection.Sortie.Trim() }
        }
        if ($etat -eq 'healthy') {
            info ('{0} : sain' -f $service)
            return
        }
        if (([DateTime]::UtcNow - $debut).TotalSeconds -ge $script:AttenteMax) {
            $script:CodeEchec = ($service -replace '-', '_') + '_pas_sain'
            if ($etat.Length -eq 0) { $etat = 'absent' }
            info ('{0} : toujours « {1} » après {2} s' -f $service, $etat, $script:AttenteMax)
            throw 'territoria-echec'
        }
        Start-Sleep -Seconds 3
    }
}

# --- Dossiers, fichiers et accès -----------------------------------------------------------------------------------

function est_absolu {
    param([string]$chemin)
    if ($script:EstWindows) { return ($chemin -match '^([A-Za-z]:[\\/]|\\\\[^\\/])') }
    return $chemin.StartsWith('/')
}

# Vrai si $chemin est $parent ou se trouve dedans (Windows ne distingue pas les majuscules des minuscules).
function dans_dossier {
    param([string]$chemin, [string]$parent)
    $separateur = [System.IO.Path]::DirectorySeparatorChar
    $a = [System.IO.Path]::GetFullPath($chemin).TrimEnd('\', '/') + $separateur
    $b = [System.IO.Path]::GetFullPath($parent).TrimEnd('\', '/') + $separateur
    $comparaison = [System.StringComparison]::Ordinal
    if ($script:EstWindows) { $comparaison = [System.StringComparison]::OrdinalIgnoreCase }
    return $a.StartsWith($b, $comparaison)
}

function verifier_dossier_donnees {
    if (-not (est_absolu $script:Donnees)) {
        echouer donnees_invalides 'TERRITORIA_DONNEES_HOTE doit être un chemin absolu.'
    }
    if (dans_dossier $script:Donnees $script:Racine) {
        echouer donnees_dans_le_depot 'territoria-donnees doit être à côté du dépôt, jamais dedans.'
    }
}

# Réserve un fichier ou un dossier à l'utilisateur courant : héritage coupé, un seul droit accordé (icacls, par SID :
# pas de nom de compte à deviner, pas de langue à gérer). Équivalent du chmod 600 / 700 de la version POSIX.
function restreindre_acces {
    param([string]$chemin, [bool]$dossier = $false)
    if (-not $script:EstWindows) {
        $mode = '600'
        if ($dossier) { $mode = '700' }
        $global:LASTEXITCODE = 0
        & chmod $mode $chemin
        if ($LASTEXITCODE -ne 0) { echouer acces_impossible ('Droits impossibles à régler sur ' + $chemin) }
        return
    }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $droit = '(R,W,D)'
    if ($dossier) { $droit = '(OI)(CI)(F)' }
    $global:LASTEXITCODE = 0
    $null = & icacls $chemin /inheritance:r /grant:r ('*{0}:{1}' -f $sid, $droit)
    if ($LASTEXITCODE -ne 0) { echouer acces_impossible ('Droits d''accès impossibles à régler sur ' + $chemin) }
}

# Crée un dossier absent, réservé à l'utilisateur courant avec $prive, avant que rien n'y soit écrit ; un dossier
# existant n'est pas touché.
function creer_dossier {
    param([string]$chemin, [bool]$prive = $false)
    if (Test-Path -LiteralPath $chemin -PathType Container) { return }
    if ($script:Simulation) {
        afficher_commande @('New-Item', '-ItemType', 'Directory', '-Path', $chemin)
        if ($prive) { expliquer ('accès réservé à l''utilisateur courant : icacls {0} /inheritance:r /grant:r *<SID>:(OI)(CI)(F)' -f (citer $chemin)) }
        return
    }
    $null = New-Item -ItemType Directory -Path $chemin -Force
    if ($prive) { restreindre_acces $chemin $true }
}

# Écrit des octets dans un fichier par remplacement atomique ; le fichier temporaire est créé à côté de la cible (dans
# secrets\ pour un secret, jamais dans %TEMP%). Avec $prive, l'accès est réservé à l'utilisateur avant le remplacement.
function ecrire_octets {
    param([string]$fichier, [byte[]]$octets, [bool]$prive = $false)
    $temporaire = '{0}.tmp.{1}' -f $fichier, $PID
    try {
        [System.IO.File]::WriteAllBytes($temporaire, $octets)
        if ($prive) { restreindre_acces $temporaire $false }
        Move-Item -LiteralPath $temporaire -Destination $fichier -Force
    } catch {
        Remove-Item -LiteralPath $temporaire -Force -ErrorAction SilentlyContinue
        if ($_.Exception.Message -eq 'territoria-echec') { throw }
        echouer ecriture_impossible ('Écriture impossible dans ' + $fichier)
    }
}

function creer_fichier_vide {
    param([string]$fichier)
    if (Test-Path -LiteralPath $fichier) { return }
    if ($script:Simulation) {
        afficher_commande @('New-Item', '-ItemType', 'File', '-Path', $fichier)
        expliquer 'accès réservé à l''utilisateur courant'
        return
    }
    ecrire_octets $fichier ([byte[]]@()) $true
}

function lire_lignes {
    param([string]$fichier)
    if (-not (Test-Path -LiteralPath $fichier -PathType Leaf)) { return @() }
    return @([System.IO.File]::ReadAllLines($fichier, $script:Utf8))
}

# Donne territoria/ à l'uid 10001, n8n/ à l'uid 1000 et caddy/ à l'uid 10002 (utilisateurs des services) par un
# conteneur jetable en root, comme la version POSIX. À valider en recette sous Windows : le comportement de chown sur un
# dossier partagé de Docker Desktop ou de la machine Podman (WSL) dépend du système de fichiers partagé.
function attribuer_proprietaires {
    if ($script:ImagePython.Length -eq 0) { echouer image_python_introuvable 'Image Python introuvable dans territoria.Dockerfile.' }
    phase proprietaires_impossibles
    moteur run --rm --network none --user 0:0 --entrypoint sh `
        -v ('{0}:/territoria' -f (Join-Path -Path $script:Donnees -ChildPath 'territoria')) `
        -v ('{0}:/n8n' -f (Join-Path -Path $script:Donnees -ChildPath 'n8n')) `
        -v ('{0}:/caddy' -f (Join-Path -Path $script:Donnees -ChildPath 'caddy')) `
        -v ('{0}:/modeles-audio' -f (Join-Path -Path $script:Donnees -ChildPath 'modeles-audio')) $script:ImagePython `
        -c 'chown 10001:10001 /territoria /territoria/audio /modeles-audio && chown 1000:1000 /n8n && chown 10002:10002 /caddy && chmod 0700 /caddy'
}

# Dossier de données complet avant tout démarrage : territoria\, n8n\, caddy\, secrets\ (réservé à l'utilisateur dès
# sa création, avant qu'un secret n'y soit écrit), territoria\audio\ (enregistrements des séances, partagés avec le
# conteneur de transcription), modeles-audio\ (modèles de transcription) et les fichiers d'environnement que Compose
# exige, même vides.
function preparer_dossiers {
    verifier_dossier_donnees
    phase dossiers_impossibles
    creer_dossier $script:Donnees $true
    creer_dossier (Join-Path -Path $script:Donnees -ChildPath 'territoria') $false
    creer_dossier (Join-Path -Path $script:Donnees -ChildPath 'n8n') $false
    creer_dossier (Join-Path -Path $script:Donnees -ChildPath 'caddy') $true
    creer_dossier (Join-Path -Path (Join-Path -Path $script:Donnees -ChildPath 'territoria') -ChildPath 'audio') $true
    creer_dossier (Join-Path -Path $script:Donnees -ChildPath 'modeles-audio') $false
    creer_dossier $script:Secrets $true
    foreach ($nom in @('territoria.env', 'n8n.env', 'compose.env')) {
        creer_fichier_vide (Join-Path -Path $script:Secrets -ChildPath $nom)
    }
    attribuer_proprietaires
}

# Réserve à l'utilisateur les fichiers que « outils secrets » a créés dans secrets\ (pas de chmod sous NTFS).
function restreindre_secrets {
    if ($script:Simulation) {
        expliquer 'accès réservé à l''utilisateur courant sur les fichiers de secrets\ (icacls)'
        return
    }
    foreach ($fichier in @(Get-ChildItem -LiteralPath $script:Secrets -File -ErrorAction SilentlyContinue)) {
        restreindre_acces $fichier.FullName $false
    }
}

# Vrai si le fichier d'environnement contient CLE avec une valeur non vide (la valeur n'est jamais imprimée).
function variable_renseignee {
    param([string]$fichier, [string]$cle)
    foreach ($ligne in (lire_lignes $fichier)) {
        if ($ligne.StartsWith($cle + '=') -and $ligne.Length -gt $cle.Length + 1) { return $true }
    }
    return $false
}

# Valeur d'une variable non secrète d'un fichier d'environnement (apostrophes droites retirées) ; vide si absente.
function lire_variable {
    param([string]$fichier, [string]$cle)
    $valeur = ''
    foreach ($ligne in (lire_lignes $fichier)) {
        if ($ligne.StartsWith($cle + '=')) { $valeur = $ligne.Substring($cle.Length + 1) }
    }
    if ($valeur.Length -ge 2 -and $valeur.StartsWith("'") -and $valeur.EndsWith("'")) { $valeur = $valeur.Substring(1, $valeur.Length - 2) }
    return $valeur
}

# Écrit CLE=valeur dans un fichier d'environnement (remplace la première ligne CLE=, retire les doublons, garde les
# autres lignes), UTF-8 sans BOM, fins de ligne LF, accès réservé à l'utilisateur, par remplacement atomique. Le fichier
# est lu en UTF-8 (Windows PowerShell 5.1 lirait sinon l'ANSI). Option « litteral » : valeur entre apostrophes droites
# (Compose ne l'interpole pas) ; « secret » : la valeur est masquée en simulation.
function ecrire_variable {
    param([string]$fichier, [string]$cle, [string]$valeur, [string]$options = '')
    if ($valeur.Contains("`n") -or $valeur.Contains("`r")) {
        echouer valeur_invalide 'Une valeur ne peut pas contenir de fin de ligne.'
    }
    $rendu = $valeur
    if ($options.Contains('litteral')) {
        if ($valeur.Contains("'")) { echouer valeur_invalide 'Une valeur ne peut pas contenir d''apostrophe droite.' }
        $rendu = "'" + $valeur + "'"
    }
    if ($script:Simulation) {
        if ($options.Contains('secret')) {
            sortie ('+ écrire {0}=(saisie masquée) dans {1} (accès : utilisateur courant)' -f $cle, (citer $fichier))
        } else {
            sortie ('+ écrire {0}={1} dans {2} (accès : utilisateur courant)' -f $cle, $rendu, (citer $fichier))
        }
        return
    }
    $nouvelles = New-Object System.Collections.Generic.List[string]
    $ecrite = $false
    foreach ($ligne in (lire_lignes $fichier)) {
        if ($ligne.StartsWith($cle + '=')) {
            if (-not $ecrite) {
                $nouvelles.Add($cle + '=' + $rendu)
                $ecrite = $true
            }
        } else {
            $nouvelles.Add($ligne)
        }
    }
    if (-not $ecrite) { $nouvelles.Add($cle + '=' + $rendu) }
    $texte = (($nouvelles.ToArray()) -join "`n") + "`n"
    ecrire_octets $fichier ($script:Utf8.GetBytes($texte)) $true
}

# Retire CLE d'un fichier d'environnement (toutes ses lignes CLE=), garde les autres lignes, accès réservé à
# l'utilisateur courant, par remplacement ; sans effet si le fichier ou la clé est absent. La valeur n'est jamais affichée.
function retirer_variable {
    param([string]$fichier, [string]$cle)
    if ($script:Simulation) {
        sortie ('+ retirer {0} de {1} (accès : utilisateur courant)' -f $cle, (citer $fichier))
        return
    }
    $lignes = @(lire_lignes $fichier)
    $gardees = New-Object System.Collections.Generic.List[string]
    foreach ($ligne in $lignes) {
        if (-not $ligne.StartsWith($cle + '=')) { $gardees.Add($ligne) }
    }
    if ($gardees.Count -eq $lignes.Count) { return }
    $texte = ''
    if ($gardees.Count -gt 0) { $texte = (($gardees.ToArray()) -join "`n") + "`n" }
    ecrire_octets $fichier ($script:Utf8.GetBytes($texte)) $true
}

# Écrit un fichier de texte non secret (UTF-8 sans BOM) ; affiché en simulation.
function ecrire_fichier {
    param([string]$fichier, [string]$contenu, [System.Text.Encoding]$encodage = $null)
    if ($script:Simulation) {
        sortie ('+ écrire {0} :' -f (citer $fichier))
        foreach ($ligne in ($contenu -split "\r?\n")) { sortie ('|   ' + $ligne) }
        return
    }
    if ($null -eq $encodage) { $encodage = $script:Utf8 }
    ecrire_octets $fichier ($encodage.GetPreamble() + $encodage.GetBytes($contenu)) $false
}

function echapper_xml {
    param([string]$texte)
    return [System.Security.SecurityElement]::Escape($texte)
}

# --- Saisie de l'utilisateur, dans sa console ----------------------------------------------------------------------

function terminal_disponible {
    if (-not [Environment]::UserInteractive) { return $false }
    try { return (-not [Console]::IsInputRedirected) } catch { return $false }
}

function exiger_terminal {
    if (-not (terminal_disponible)) {
        echouer terminal_requis 'Cette étape pose des questions : lancez-la vous-même dans une fenêtre PowerShell.'
    }
}

# Réponse visible. (Fonction à part : les essais la remplacent.)
function lire_reponse {
    param([string]$invite)
    return [string](Read-Host -Prompt $invite)
}

# Saisie masquée : rien ne s'affiche ; la valeur reste un SecureString jusqu'à son emploi.
function lire_secret {
    param([string]$invite)
    return (Read-Host -Prompt $invite -AsSecureString)
}

# Texte d'un SecureString, uniquement en mémoire, le temps de l'écrire dans un fichier ou de le comparer.
function texte_du_secret {
    param([System.Security.SecureString]$secret)
    $bstr = [IntPtr]::Zero
    try {
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        if ($bstr -ne [IntPtr]::Zero) { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }
}

function repondu_oui {
    param([string]$reponse)
    return ($reponse -cmatch '^(o|O|oui|Oui|OUI)$')
}

# --- Démarrage et provisionnement (séquence vérifiée de la tâche 8) --------------------------------------------------

function exiger_secrets {
    if ($script:Simulation) { return }
    if (-not (variable_renseignee (Join-Path -Path $script:Secrets -ChildPath 'n8n.env') 'N8N_ENCRYPTION_KEY') -or
        -not (variable_renseignee (Join-Path -Path $script:Secrets -ChildPath 'territoria.env') 'TERRITORIA_SERVICE_TOKEN')) {
        # n8n inventerait sa propre clé de chiffrement s'il démarrait sans elle.
        echouer secrets_absents "Secrets absents : lancez d'abord install\installer.ps1 --etape secrets."
    }
}

function lms_en_marche {
    param([string]$lms)
    $resultat = executer $lms @('server', 'status', '--json') $null 30 $true
    return ($resultat.Sortie -match '"running"\s*:\s*true')
}

# Adresses locales sur lesquelles le port de LM Studio est en écoute, une par élément (sans le port). 0.0.0.0 et ::
# désignent toutes les interfaces. Fonction à part : les essais la remplacent.
function adresses_ecoute_lms {
    $connexions = Get-NetTCPConnection -LocalPort $script:PortLms -State Listen -ErrorAction SilentlyContinue
    if ($null -eq $connexions) { return @() }
    $adresses = @()
    foreach ($connexion in @($connexions)) { $adresses += [string]$connexion.LocalAddress }
    return $adresses
}

# L'écoute réelle de LM Studio ne doit viser que la boucle locale ou l'adresse choisie : une écoute sur toutes les
# interfaces ou sur une adresse du réseau local arrête l'étape.
function verifier_ecoute_lms {
    param([string]$attendue)
    if ($script:Simulation) {
        afficher_commande @('Get-NetTCPConnection', '-LocalPort', [string]$script:PortLms, '-State', 'Listen')
        expliquer ('LM Studio ne doit écouter que sur {0} ou la boucle locale, jamais sur toutes les interfaces ni sur le réseau local' -f $attendue)
        return
    }
    if ($null -eq (Get-Command -Name 'Get-NetTCPConnection' -ErrorAction SilentlyContinue)) {
        echouer ecoute_non_verifiable "Impossible de vérifier l'adresse d'écoute de LM Studio (Get-NetTCPConnection absent)."
    }
    $adresses = @(adresses_ecoute_lms)
    if ($adresses.Count -eq 0) {
        echouer lmstudio_injoignable ("Aucun serveur n'écoute sur le port {0} : LM Studio n'a pas démarré son serveur." -f $script:PortLms)
    }
    foreach ($adresse in $adresses) {
        $nette = $adresse.Trim('[', ']')
        if (@($attendue, '127.0.0.1', '::1', 'localhost') -notcontains $nette) {
            echouer lmstudio_expose_reseau ('LM Studio écoute sur {0}:{1}, donc au-delà de cette machine. Dans LM Studio (Developer > Server Settings), décochez « Serve on Local Network », puis relancez l''étape.' -f $adresse, $script:PortLms)
        }
    }
    info ("LM Studio n'écoute que sur : " + ($adresses -join ' '))
}

# Démarre LM Studio (serveur local, écoute limitée) s'il est installé sur la machine.
function demarrer_lmstudio {
    if ((lire_variable (Join-Path -Path $script:Secrets -ChildPath 'compose.env') 'TERRITORIA_IA_EN_LIGNE') -eq 'mistral') {
        info "API de Mistral choisie (étape mistral) : LM Studio n'est pas démarré."
        return
    }
    $lms = chemin_lms
    if ($lms.Length -eq 0) {
        info 'LM Studio (lms) introuvable : serveur IA non démarré.'
        return
    }
    $adresse = adresse_ecoute_lms
    if ($script:Simulation -or -not (lms_en_marche $lms)) {
        phase lmstudio_arrete
        lancer_detache $lms server start --port ([string]$script:PortLms) --bind $adresse
    }
    # Un serveur déjà ouvert au réseau local (« Serve on Local Network ») n'est jamais accepté.
    verifier_ecoute_lms $adresse
    assurer_modele_charge $lms
}

# Charge le modèle dans LM Studio sous l'identifiant que Territoria demande (TERRITORIA_MODELE), s'il n'y est pas déjà :
# « lms load <id> --identifier <clé> ». Sans lui, la première demande après un redémarrage de LM Studio chargerait le
# modèle à la volée (plusieurs minutes sur un PC sans carte graphique), et le test de bout en bout pourrait échouer.
function charger_modele_lms {
    param([string]$lms, [string]$id, [string]$cle)
    if (-not $script:Simulation) {
        $charges = (executer $lms @('ps', '--json') $null 60 $true).Sortie
        if ($charges -match ('"identifier"\s*:\s*"' + [regex]::Escape($cle) + '"')) { return }
    }
    phase chargement_impossible
    lancer_tableau $lms @('load', $id, '--identifier', $cle, '--yes')
}

# Identifiant « lms get » d'une clé de modèle (TERRITORIA_MODELE) : celui de install\modeles.json dont la clé
# (l'identifiant sans sa variante @…) correspond, sinon la clé elle-même (modèle choisi par la personne qui gère
# l'installation).
function modele_de_la_cle {
    param([string]$cle)
    foreach ($palier in @('petit', 'moyen', 'grand')) {
        $id = modele_du_palier $palier
        $sansVariante = $id
        if ($id.LastIndexOf('@') -ge 0) { $sansVariante = $id.Substring(0, $id.LastIndexOf('@')) }
        if ($id.Length -gt 0 -and $sansVariante -ceq $cle) { return $id }
    }
    return $cle
}

# Après le démarrage du serveur de LM Studio : le modèle de compose.env (TERRITORIA_MODELE, écrit par l'étape lmstudio)
# est chargé s'il ne l'est pas. Sans ce réglage (autre serveur IA), rien n'est chargé.
function assurer_modele_charge {
    param([string]$lms)
    $cle = lire_variable (Join-Path -Path $script:Secrets -ChildPath 'compose.env') 'TERRITORIA_MODELE'
    if ($cle.Length -eq 0) {
        expliquer "modèle de compose.env (TERRITORIA_MODELE) chargé dans LM Studio s'il ne l'est pas : lms load <id> --identifier <clé> --yes"
        return
    }
    charger_modele_lms $lms (modele_de_la_cle $cle) $cle
}

# Modèles de transcription des séances : image du conteneur de travaux, puis téléchargement vérifié (empreintes SHA-256
# du manifeste conteneurs\modeles-audio.json) par le service ponctuel travaux-modeles, seul moment où cette image a un
# réseau. Environ 1,7 Go la première fois ; ensuite, les fichiers présents et intacts ne sont pas retéléchargés.
function modeles_audio {
    phase construction_impossible
    compose build travaux
    phase modeles_audio
    $resultat = sortie_du_moteur (arguments_compose @('--profile', 'outils', 'run', '--rm', '-T', 'travaux-modeles'))
    if (-not $script:Simulation) { exiger_etape 'modeles-audio' $resultat }
}

# Les modèles ne conditionnent que la transcription des séances : leur absence (proxy, coupure, disque) n'empêche ni
# l'installation ni la mise à jour. La transcription attend alors (« Modèles absents » dans Santé), et l'étape demarrer
# relancée reprend le téléchargement là où il s'était arrêté.
function modeles_audio_facultatif {
    try {
        modeles_audio
    } catch {
        info ('Modèles de transcription non téléchargés (code {0}) : Territoria démarre sans eux et la transcription des séances attend. Pour réessayer : install\installer.ps1 --etape demarrer' -f $script:CodeEchec)
    }
}

# Démarre les services ; avec « construire », reconstruit d'abord les images territoria et travaux, et vérifie les
# modèles de transcription.
function demarrer_services {
    param([string]$mode = '')
    exiger_secrets
    if ($mode -ceq 'construire') {
        phase construction_impossible
        compose build territoria
        modeles_audio_facultatif
    }
    phase demarrage_impossible
    compose up -d
    attendre_sain 'n8n'
    attendre_sain 'territoria'
}

function nettoyer_provision {
    if (-not $script:Simulation) {
        Remove-Item -LiteralPath (Join-Path -Path $script:Secrets -ChildPath 'provision') -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Provisionnement de n8n (credentials et workflows), n8n arrêté pendant l'import. Les jetons en clair de
# secrets\provision\ sont supprimés en sortie, que la séquence réussisse ou non. Le démarrage et le compte
# propriétaire viennent ensuite (voir l'appelant). Sous NTFS il n'y a ni chmod 0755 ni 0644 à poser : n8n (uid 1000)
# lit le dossier partagé par le moteur.
function provisionner_n8n {
    phase n8n_provisionnement
    $provision = Join-Path -Path $script:Secrets -ChildPath 'provision'
    try {
        outils_jetable provision-n8n --sortie /donnees-secrets/provision
        expliquer 'pas de chmod sous Windows : le dossier partagé est lisible par n8n'
        compose stop n8n  # sans effet à la première installation
        compose run --rm --no-deps -T -v ('{0}:/provision:ro' -f $provision) n8n import:credentials --input=/provision/credentials.json
        compose run --rm --no-deps -T -v ('{0}:/provision:ro' -f $provision) n8n import:workflow --separate --input=/provision/
        # Un import désactive les workflows ; publish:workflow les réactive et rend 1 si l'identifiant est inconnu
        # (import:workflow rend 0 même sur un dossier vide : c'est la publication qui signale l'échec).
        compose run --rm --no-deps -T n8n publish:workflow --id=TerritoriaConv01
        compose run --rm --no-deps -T n8n publish:workflow --id=TerritoriaArre01
        if ($script:Simulation) {
            afficher_commande @('Remove-Item', '-Recurse', '-Force', $provision)
        } else {
            Remove-Item -LiteralPath $provision -Recurse -Force
            if (Test-Path -LiteralPath $provision) { throw 'Le dossier provision n''a pas pu être supprimé.' }
        }
    } finally {
        nettoyer_provision
    }
}

# --- Sauvegardes et restaurations (conteneur ponctuel) ---------------------------------------------------------------

function verifier_destination {
    param([string]$destination)
    if (-not (est_absolu $destination)) { echouer destination_invalide 'La destination doit être un chemin absolu.' }
    if ((dans_dossier $destination $script:Donnees) -or (dans_dossier $destination $script:Racine)) {
        echouer destination_invalide 'La destination ne peut être ni dans territoria-donnees ni dans le dépôt.'
    }
    if (-not $script:Simulation -and -not (Test-Path -LiteralPath $destination -PathType Container)) {
        # Jamais créée : un disque externe débranché ne doit pas être remplacé par un dossier vide.
        echouer destination_absente ('Dossier de destination introuvable : ' + $destination)
    }
}

# Montage « hôte:conteneur » : le dossier d'un lecteur n'a pas de barre oblique inverse finale.
function montage {
    param([string]$hote, [string]$conteneur)
    $nette = $hote
    if ($nette.Length -gt 3 -and ($nette.EndsWith('\') -or $nette.EndsWith('/'))) { $nette = $nette.TrimEnd('\', '/') }
    return ('{0}:{1}' -f $nette, $conteneur)
}

function exiger_image_territoria {
    $image = image_territoria
    if ($image.Length -eq 0) {
        echouer image_absente "Image territoria absente : lancez d'abord install\installer.ps1 --etape secrets."
    }
    $script:Image = $image
}

# Mot de passe des sauvegardes saisi deux fois, masqué, dans la console. Rend ses octets exacts (UTF-8, une seule fin de
# ligne LF), à transmettre par l'entrée standard d'un conteneur ; l'appelant les efface ([Array]::Clear) dès qu'il n'en a
# plus besoin.
function saisir_mot_de_passe_sauvegarde {
    exiger_terminal
    $motdepasse = texte_du_secret (lire_secret 'Mot de passe de la sauvegarde (saisie masquée)')
    $confirmation = texte_du_secret (lire_secret 'Confirmez le mot de passe')
    if ($motdepasse.Length -eq 0) { echouer mot_de_passe_absent 'Mot de passe vide.' }
    if ($motdepasse -cne $confirmation) { echouer saisies_differentes 'Les deux saisies diffèrent.' }
    $confirmation = ''
    $octets = $script:Utf8.GetBytes($motdepasse + "`n")
    $motdepasse = ''
    return , $octets
}

# Archive chiffrée de territoria-donnees (monté en lecture seule) dans la destination, services en marche. Avec
# « auto », le mot de passe vient de secrets\sauvegarde.motdepasse ; sinon il est saisi deux fois, masqué.
function sauvegarde_creer {
    param([string]$destination, [string]$mode = '')
    verifier_dossier_donnees
    verifier_destination $destination
    sauvegarde_ecrire $destination $mode
}

# Écrit l'archive dans une destination déjà contrôlée par l'appelant. Mot de passe : « auto » (fichier
# secrets\sauvegarde.motdepasse), « memoire » (octets déjà saisis, passés en troisième argument et effacés par l'appelant :
# mise à jour), sinon saisi ici deux fois, masqué. L'image utilisée reste dans $script:Image.
function sauvegarde_ecrire {
    param([string]$destination, [string]$mode = '', [byte[]]$memoire = $null)
    if (-not $script:Simulation -and -not (Test-Path -LiteralPath (Join-Path -Path $script:Donnees -ChildPath 'territoria') -PathType Container)) {
        echouer installation_absente ('Aucune installation dans ' + $script:Donnees)
    }
    exiger_moteur
    exiger_image_territoria
    $montages = @('-v', (montage $script:Donnees '/source:ro'), '-v', (montage $destination '/destination'), $script:Image)
    $commande = @('python', '-m', 'outils', 'sauvegarder')
    if ($mode -ceq 'auto') {
        if (-not $script:Simulation -and -not ((Test-Path -LiteralPath $script:FichierMotDePasse -PathType Leaf) -and ((Get-Item -LiteralPath $script:FichierMotDePasse).Length -gt 0))) {
            echouer mot_de_passe_absent "Mot de passe de sauvegarde absent : lancez install\installer.ps1 --etape sauvegarde-auto."
        }
        $resultat = ponctuel $null ($montages + $commande + @('--depuis-fichier', '/source/secrets/sauvegarde.motdepasse'))
        if (-not $script:Simulation) { exiger_etape 'sauvegarder' $resultat }
        return
    }
    if ($script:Simulation) {
        expliquer "saisie masquée du mot de passe de la sauvegarde (deux fois), transmis par l'entrée standard"
        $null = ponctuel $null ($montages + $commande)
        return
    }
    if ($mode -ceq 'memoire') {
        $resultat = ponctuel $memoire ($montages + $commande)
    } else {
        # Octets exacts (UTF-8, une seule fin de ligne LF) sur l'entrée standard du conteneur : ni argument, ni fichier.
        $entree = saisir_mot_de_passe_sauvegarde
        try {
            $resultat = ponctuel $entree ($montages + $commande)
        } finally {
            [Array]::Clear($entree, 0, $entree.Length)
        }
    }
    exiger_etape 'sauvegarder' $resultat
}

# Vérifie une archive précise (déchiffrement complet, manifeste, intégrité des bases) avec l'image $script:Image ; mot de
# passe comme pour sauvegarde_ecrire (« auto » ou « memoire »), transmis par l'entrée standard.
function sauvegarde_verifier {
    param([string]$archive, [string]$mode = '', [byte[]]$memoire = $null)
    $commande = @('-v', (montage (Split-Path -Parent $archive) '/destination:ro'), $script:Image, 'python', '-m', 'outils',
        'verifier-sauvegarde', '--archive', ('/destination/' + (Split-Path -Leaf $archive)))
    if ($script:Simulation) {
        $null = ponctuel $null $commande
        return
    }
    if ($mode -ceq 'auto') {
        $entree = [System.IO.File]::ReadAllBytes($script:FichierMotDePasse)
        try {
            $resultat = ponctuel $entree $commande
        } finally {
            [Array]::Clear($entree, 0, $entree.Length)
        }
    } else {
        $resultat = ponctuel $memoire $commande
    }
    exiger_etape 'verifier-sauvegarde' $resultat
}

# Garde 7 archives quotidiennes et 4 hebdomadaires dans la destination.
function sauvegarde_elaguer {
    param([string]$destination)
    verifier_destination $destination
    exiger_moteur
    exiger_image_territoria
    $resultat = ponctuel $null @('-v', (montage $destination '/destination'), $script:Image, 'python', '-m', 'outils', 'elaguer')
    if (-not $script:Simulation) { exiger_etape 'elaguer' $resultat }
}

# Restaure une archive (fichier, ou dossier dont la plus récente est prise) dans territoria-donnees, services arrêtés.
# Un territoria-donnees non vide exige « forcer ». Mot de passe : « auto » (sauvegarde.motdepasse), « memoire » (octets
# déjà saisis, cinquième argument : mise à jour), sinon saisi ici, masqué. Une image peut être imposée (quatrième
# argument : la mise à jour restaure avec l'image de la version qui a fait la sauvegarde) ; sinon, celle du projet.
function sauvegarde_restaurer {
    param([string]$archive, [string]$forcer = '', [string]$mode = '', [string]$image = '', [byte[]]$memoire = $null)
    verifier_dossier_donnees
    if (-not (est_absolu $archive)) { echouer archive_invalide "L'archive doit être désignée par un chemin absolu." }
    $nom = ''
    if (Test-Path -LiteralPath $archive -PathType Container) {
        $dossier = $archive
    } elseif ((Test-Path -LiteralPath $archive -PathType Leaf) -or $script:Simulation) {
        $dossier = Split-Path -Parent $archive
        $nom = Split-Path -Leaf $archive
    } else {
        echouer archive_absente ('Archive introuvable : ' + $archive)
    }
    $options = @()
    if ($forcer -ceq 'forcer') {
        $options = @('--forcer')
    } elseif (-not $script:Simulation -and (Test-Path -LiteralPath $script:Donnees -PathType Container) -and
        (@(Get-ChildItem -LiteralPath $script:Donnees -Force -ErrorAction SilentlyContinue).Count -gt 0)) {
        echouer cible_non_vide "territoria-donnees n'est pas vide : après une sauvegarde, relancez avec --forcer pour remplacer territoria\, n8n\ et les fichiers de secrets\."
    }
    if ($mode -ceq 'auto' -and -not $script:Simulation -and -not ((Test-Path -LiteralPath $script:FichierMotDePasse -PathType Leaf) -and ((Get-Item -LiteralPath $script:FichierMotDePasse).Length -gt 0))) {
        echouer mot_de_passe_absent ('Mot de passe de sauvegarde absent de ' + $script:FichierMotDePasse)
    }
    exiger_moteur
    if ($image.Length -gt 0) { $script:Image = $image } else { exiger_image_territoria }
    $entree = $null
    if ($mode -ceq 'memoire' -and -not $script:Simulation) {
        $entree = [byte[]]$memoire.Clone()
    } elseif ($mode -cne 'auto' -and -not $script:Simulation) {
        exiger_terminal
        $motdepasse = texte_du_secret (lire_secret 'Mot de passe de la sauvegarde à restaurer (saisie masquée)')
        if ($motdepasse.Length -eq 0) { echouer mot_de_passe_absent 'Mot de passe vide.' }
        $entree = $script:Utf8.GetBytes($motdepasse + "`n")
        $motdepasse = ''
    }
    if ($script:Simulation -or (Test-Path -LiteralPath (Join-Path -Path $script:Secrets -ChildPath 'compose.env') -PathType Leaf)) {
        phase arret_impossible
        compose stop
    }
    creer_dossier $script:Donnees $true
    $cheminArchive = '/destination'
    if ($nom.Length -gt 0) { $cheminArchive = '/destination/' + $nom }
    $commande = @('-v', (montage $dossier '/destination:ro'), '-v', (montage $script:Donnees '/cible'), $script:Image,
        'python', '-m', 'outils', 'restaurer', '--archive', $cheminArchive) + $options
    if ($script:Simulation) {
        $origine = 'saisie masquée'
        if ($mode -ceq 'auto') { $origine = 'fichier sauvegarde.motdepasse' }
        expliquer "mot de passe transmis par l'entrée standard ($origine)"
        $null = ponctuel $null $commande
        return
    }
    if ($mode -ceq 'auto') { $entree = [System.IO.File]::ReadAllBytes($script:FichierMotDePasse) }
    try {
        $resultat = ponctuel $entree $commande
    } finally {
        if ($null -ne $entree) { [Array]::Clear($entree, 0, $entree.Length) }
    }
    exiger_etape 'restaurer' $resultat
}
