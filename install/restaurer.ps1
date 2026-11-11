# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Restaure une sauvegarde sous Windows dans territoria-donnees, services arrêtés, par un conteneur ponctuel (root, sans
# réseau) :
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\restaurer.ps1 --archive <fichier .tsv1 | dossier> [--forcer] [--auto] [--simulation]
# Un dossier désigne sa sauvegarde la plus récente. Un territoria-donnees non vide exige --forcer (territoria\ et n8n\
# remplacés, fichiers de secrets\ fusionnés ; faites d'abord une sauvegarde). Le mot de passe est saisi masqué, ou lu
# dans secrets\sauvegarde.motdepasse avec --auto. Les services restent arrêtés : relancez install\demarrer.ps1.
# Dernière ligne : « ETAPE restaurer : OK|ECHEC <code> ».
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

$archive = ''
$forcer = ''
$mode = ''
$mots = @($args | ForEach-Object { [string]$_ })
$i = 0
while ($i -lt $mots.Count) {
    $option = $mots[$i]
    if ($option -ceq '--archive') {
        if ($i + 1 -ge $mots.Count) { refuser_arguments 'restaurer' }
        $archive = $mots[$i + 1]
        $i += 2
    } elseif ($option -ceq '--forcer') {
        $forcer = 'forcer'
        $i++
    } elseif ($option -ceq '--auto') {
        $mode = 'auto'
        $i++
    } elseif ($option -ceq '--simulation') {
        activer_simulation
        $i++
    } elseif ($option -ceq '-h' -or $option -ceq '--help') {
        sortie 'Usage : install\restaurer.ps1 --archive <fichier|dossier> [--forcer] [--auto] [--simulation]'
        exit 0
    } else {
        refuser_arguments 'restaurer'
    }
}
if ($archive.Length -eq 0) { refuser_arguments 'restaurer' }

executer_etape 'restaurer' {
    sauvegarde_restaurer $archive $forcer $mode
    if (-not $script:Simulation) { info 'Restauration terminée : relancez Territoria avec install\demarrer.ps1.' }
}
exit $script:CodeSortie
