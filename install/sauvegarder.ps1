# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Sauvegarde chiffrée de territoria-donnees sous Windows (monté en lecture seule, services en marche) dans un dossier
# existant :
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\sauvegarder.ps1 --destination <dossier> [--auto] [--simulation]
# Sans --auto, le mot de passe est saisi deux fois, masqué, dans la fenêtre PowerShell. Avec --auto (tâche planifiée), il
# est lu dans territoria-donnees\secrets\sauvegarde.motdepasse, puis les archives sont élaguées (7 quotidiennes et
# 4 hebdomadaires gardées). Dernière ligne : « ETAPE sauvegarder : OK|ECHEC <code> ».
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

$destination = ''
$mode = ''
$mots = @($args | ForEach-Object { [string]$_ })
$i = 0
while ($i -lt $mots.Count) {
    $option = $mots[$i]
    if ($option -ceq '--destination') {
        if ($i + 1 -ge $mots.Count) { refuser_arguments 'sauvegarder' }
        $destination = $mots[$i + 1]
        $i += 2
    } elseif ($option -ceq '--auto') {
        $mode = 'auto'
        $i++
    } elseif ($option -ceq '--simulation') {
        activer_simulation
        $i++
    } elseif ($option -ceq '-h' -or $option -ceq '--help') {
        sortie 'Usage : install\sauvegarder.ps1 --destination <dossier> [--auto] [--simulation]'
        exit 0
    } else {
        refuser_arguments 'sauvegarder'
    }
}
if ($destination.Length -eq 0) { refuser_arguments 'sauvegarder' }

executer_etape 'sauvegarder' {
    sauvegarde_creer $destination $mode
    if ($mode -ceq 'auto') {
        phase elagage_impossible
        sauvegarde_elaguer $destination
    }
}
# Tâche planifiée : personne ne regarde la fenêtre. Une ligne par exécution (date et ligne ETAPE : jamais de secret ni de
# contenu) dans territoria-donnees\sauvegarde-auto.log, comme le journal des tâches launchd et cron de la version POSIX.
if ($mode -ceq 'auto' -and -not $script:Simulation) {
    try {
        $horodatage = [DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss')
        [System.IO.File]::AppendAllText((Join-Path -Path $script:Donnees -ChildPath 'sauvegarde-auto.log'), ($horodatage + ' ' + $script:DerniereLigne + "`n"), $script:Utf8)
    } catch { $null = $_ }
}
exit $script:CodeSortie
