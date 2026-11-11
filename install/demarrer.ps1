# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Démarre Territoria sous Windows : moteur de conteneurs (machine Podman ou Docker Desktop s'il est arrêté), serveur
# LM Studio (écoute limitée, voir docs/acces-lmstudio.md) puis les services n8n et territoria, jusqu'à leur bonne santé.
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\demarrer.ps1 [--ouvrir] [--simulation]
# --ouvrir ouvre ensuite http://127.0.0.1:8878 dans le navigateur (raccourci du bureau).
# Dernière ligne : « ETAPE demarrer : OK|ECHEC <code> ».
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

$ouvrir = $false
foreach ($option in @($args | ForEach-Object { [string]$_ })) {
    if ($option -ceq '--ouvrir') {
        $ouvrir = $true
    } elseif ($option -ceq '--simulation') {
        activer_simulation
    } elseif ($option -ceq '-h' -or $option -ceq '--help') {
        sortie 'Usage : install\demarrer.ps1 [--ouvrir] [--simulation]'
        exit 0
    } else {
        refuser_arguments 'demarrer'
    }
}

executer_etape 'demarrer' {
    verifier_dossier_donnees
    assurer_moteur_actif
    demarrer_lmstudio
    demarrer_services
    if ($ouvrir) {
        if ($script:Simulation) {
            afficher_commande @('Start-Process', $script:AdresseTerritoria)
        } else {
            try { Start-Process -FilePath $script:AdresseTerritoria } catch { info ('Ouvrez {0} dans le navigateur.' -f $script:AdresseTerritoria) }
        }
    }
    info ('Territoria est prêt : {0}' -f $script:AdresseTerritoria)
}
# Lancé par le raccourci du bureau, la fenêtre se fermerait avant que l'on ait lu l'erreur : on attend une touche.
if ($ouvrir -and $script:CodeSortie -ne 0 -and (terminal_disponible)) {
    $null = Read-Host -Prompt 'Appuyez sur Entrée pour fermer cette fenêtre'
}
exit $script:CodeSortie
