# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Arrête les services de Territoria sous Windows (n8n, territoria) sans rien supprimer : les données restent dans
# territoria-donnees, les conteneurs sont seulement arrêtés. LM Studio, application de l'utilisateur, reste ouvert.
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\arreter.ps1 [--simulation]
# Dernière ligne : « ETAPE arreter : OK|ECHEC <code> ».
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

foreach ($option in @($args | ForEach-Object { [string]$_ })) {
    if ($option -ceq '--simulation') {
        activer_simulation
    } elseif ($option -ceq '-h' -or $option -ceq '--help') {
        sortie 'Usage : install\arreter.ps1 [--simulation]'
        exit 0
    } else {
        refuser_arguments 'arreter'
    }
}

executer_etape 'arreter' {
    verifier_dossier_donnees
    if (-not $script:Simulation -and -not (Test-Path -LiteralPath (Join-Path -Path $script:Secrets -ChildPath 'compose.env') -PathType Leaf)) {
        info ('Territoria n''est pas installé dans {0} : rien à arrêter.' -f $script:Donnees)
        return
    }
    exiger_moteur
    phase arret_impossible
    compose stop
}
exit $script:CodeSortie
