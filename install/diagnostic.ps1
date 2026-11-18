# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Diagnostic de la machine, sans aucune modification : imprime uniquement des lignes « cle=valeur », dans cet ordre :
# systeme, architecture, memoire_go, gpu, disque_libre_go, port_8878 (libre|occupe), moteur (docker|podman|aucun),
# docker_desktop (oui|non), lms (present|absent), chiffrement_disque (actif|inactif|inconnu), palier_modele
# (petit|moyen|grand), transcription (en_marche|arrete|inconnu), modeles_audio (presents|absents) ; puis, si Docker Desktop est présent, la ligne d'avertissement sur sa licence.
# Chaque sonde est protégée : une commande absente donne « inconnu », jamais une erreur.
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\diagnostic.ps1
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

# Minuscules, tout autre caractère que [a-z0-9] devient « _ » (valeur d'une seule chaîne, sans espace).
function normaliser {
    param([string]$texte)
    $rendu = ($texte.ToLowerInvariant() -replace '[^a-z0-9]+', '_').Trim('_')
    return $rendu
}

function architecture {
    $nom = ''
    if ($script:EstWindows) {
        $nom = [string]$env:PROCESSOR_ARCHITEW6432
        if ($nom.Length -eq 0) { $nom = [string]$env:PROCESSOR_ARCHITECTURE }
    } else {
        try { $nom = ([string](& uname -m)).Trim() } catch { $nom = '' }
    }
    switch ($nom.ToLowerInvariant()) {
        { $_ -in @('arm64', 'aarch64') } { return 'arm64' }
        { $_ -in @('x64', 'amd64', 'x86_64') } { return 'x86_64' }
        '' { return 'inconnue' }
        default { return (normaliser $nom) }
    }
}

# Premier processeur graphique réel (hors « Microsoft Basic Display Adapter »).
function gpu {
    $nom = ''
    try {
        if ($script:EstWindows) {
            foreach ($carte in @(Get-CimInstance -ClassName Win32_VideoController)) {
                $candidat = [string]$carte.Name
                if ($candidat.Length -gt 0 -and $candidat -notmatch 'Basic Display|Remote Display|Virtual') {
                    $nom = $candidat
                    break
                }
            }
        }
    } catch { $nom = '' }
    $nom = normaliser $nom
    if ($nom.Length -eq 0) { return 'inconnu' }
    return $nom
}

# Espace libre là où territoria-donnees sera rangé (son premier dossier parent existant).
function disque_libre_go {
    $dossier = $script:Donnees
    while (-not (Test-Path -LiteralPath $dossier -PathType Container)) {
        $parent = Split-Path -Parent $dossier
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $dossier) { break }
        $dossier = $parent
    }
    try {
        $lecteur = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($dossier))
        return [string][long][math]::Floor($lecteur.AvailableFreeSpace / 1GB)
    } catch {
        return 'inconnu'
    }
}

function port_8878 {
    if ($null -ne (Get-Command -Name 'Get-NetTCPConnection' -ErrorAction SilentlyContinue)) {
        try {
            $ecoute = Get-NetTCPConnection -LocalPort 8878 -State Listen -ErrorAction SilentlyContinue
            if ($null -ne $ecoute) { return 'occupe' }
            return 'libre'
        } catch { $null = $_ }
    }
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connexion = $client.ConnectAsync('127.0.0.1', 8878)
        if ($connexion.Wait(1000) -and $client.Connected) { return 'occupe' }
    } catch { $null = $_ } finally {
        $client.Dispose()
    }
    return 'libre'
}

function docker_desktop {
    $plateforme = ''
    if (commande_presente 'docker') {
        try { $plateforme = (executer 'docker' @('version', '--format', '{{.Server.Platform.Name}}') $null 10 $true).Sortie } catch { $plateforme = '' }
    }
    if ($plateforme -like '*Docker Desktop*') { return 'oui' }
    if ((docker_desktop_installe).Length -gt 0) { return 'oui' }
    return 'non'
}

function etat_lms {
    if ((chemin_lms).Length -gt 0) { return 'present' }
    return 'absent'
}

# Identifier le volume du dossier, puis interroger BitLocker pour cette seule lettre de lecteur.
function chiffrement_disque {
    if (-not $script:EstWindows -or $script:Donnees.StartsWith('\\')) { return 'inconnu' }
    try {
        $dossier = $script:Donnees
        while ($true) {
            try {
                $objet = Get-Item -LiteralPath $dossier -Force -ErrorAction Stop
                break
            } catch [System.Management.Automation.ItemNotFoundException] {
                $parent = Split-Path -Parent $dossier
                if ([string]::IsNullOrEmpty($parent) -or $parent -eq $dossier) { return 'inconnu' }
                $dossier = $parent
            }
        }
        if (-not $objet.PSIsContainer) { return 'inconnu' }
        # Une jonction ou un lien peut mener à une autre machine ou à un autre lecteur.
        for ($ancetre = $objet; $null -ne $ancetre; $ancetre = $ancetre.Parent) {
            if ($ancetre.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return 'inconnu' }
        }
        $volumes = @(Get-Volume -FilePath $objet.FullName -ErrorAction Stop)
        if ($volumes.Count -ne 1 -or [string]$volumes[0].DriveLetter -notmatch '^[A-Z]$') { return 'inconnu' }
        $montage = '{0}:' -f $volumes[0].DriveLetter
        $etats = @(Get-BitLockerVolume -MountPoint $montage -ErrorAction Stop)
        if ($etats.Count -ne 1 -or $etats[0].MountPoint -ne $montage) { return 'inconnu' }
        $etat = $etats[0]
        if ($etat.VolumeStatus -eq 'FullyEncrypted' -and $etat.ProtectionStatus -eq 'On') { return 'actif' }
        if ($etat.VolumeStatus -eq 'FullyDecrypted' -or
            ($etat.VolumeStatus -eq 'FullyEncrypted' -and $etat.ProtectionStatus -eq 'Off')) { return 'inactif' }
    } catch { return 'inconnu' }
    return 'inconnu'
}

# Conteneur de transcription des séances (projet Compose « territoria ») : en marche, arrêté, ou inconnu sans moteur.
function etat_transcription {
    $moteur = moteur_present
    if ($moteur -ne 'docker' -and $moteur -ne 'podman') { return 'inconnu' }
    try {
        $resultat = executer $moteur @('ps', '--filter', 'name=territoria[-_]travaux', '--format', '{{.Names}}') $null 10 $true
    } catch { return 'inconnu' }
    if ($resultat.Code -ne 0) { return 'inconnu' }
    if ($resultat.Sortie.Trim().Length -gt 0) { return 'en_marche' }
    return 'arrete'
}

# Modèles de transcription téléchargés (modèle par défaut, découpage des tours de parole, empreintes de voix).
function presence_modeles_audio {
    $dossier = Join-Path -Path $script:Donnees -ChildPath 'modeles-audio'
    $modele = Join-Path -Path (Join-Path -Path $dossier -ChildPath 'large-v3-turbo') -ChildPath 'model.bin'
    $decoupage = Join-Path -Path (Join-Path -Path $dossier -ChildPath 'segmentation') -ChildPath 'model.onnx'
    $empreintes = Join-Path -Path $dossier -ChildPath 'empreintes'
    if ((Test-Path -LiteralPath $modele -PathType Leaf) -and (Test-Path -LiteralPath $decoupage -PathType Leaf) -and
        (Test-Path -LiteralPath $empreintes -PathType Container) -and @(Get-ChildItem -LiteralPath $empreintes -File).Count -gt 0) {
        return 'presents'
    }
    return 'absents'
}

$memoire = memoire_go
$dockerDesktop = docker_desktop
sortie "systeme=$(systeme)"
sortie "architecture=$(architecture)"
sortie "memoire_go=$memoire"
sortie "gpu=$(gpu)"
sortie "disque_libre_go=$(disque_libre_go)"
sortie "port_8878=$(port_8878)"
sortie "moteur=$(moteur_present)"
sortie "docker_desktop=$dockerDesktop"
sortie "lms=$(etat_lms)"
sortie "chiffrement_disque=$(chiffrement_disque)"
sortie "palier_modele=$(palier_modele $memoire)"
sortie "transcription=$(etat_transcription)"
sortie "modeles_audio=$(presence_modeles_audio)"
# IA en ligne choisie par l'étape mistral, sinon IA locale.
$iaEnLigne = 'aucune'
if ((lire_variable (Join-Path -Path $script:Secrets -ChildPath 'compose.env') 'TERRITORIA_IA_EN_LIGNE') -ceq 'mistral') { $iaEnLigne = 'mistral' }
sortie "ia_en_ligne=$iaEnLigne"
if ($dockerDesktop -eq 'oui') {
    sortie 'avertissement=licence_docker_desktop_payante_pour_les_administrations'
}
exit 0
