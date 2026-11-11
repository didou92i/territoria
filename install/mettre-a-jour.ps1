# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Met à jour Territoria sous Windows vers une version du dépôt (étiquette, branche ou commit), avec retour automatique en
# cas d'échec :
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\mettre-a-jour.ps1 --version <ref git> [--destination <dossier>] [--simulation]
# Même séquence que install/mettre-a-jour.sh (voir ses commentaires) :
# - refus avant tout changement : modifications locales dans le dépôt (fichiers en cause nommés ; ceux que le système
#   crée tout seul, .DS_Store, ._*, Thumbs.db, desktop.ini, sont écartés par .gitignore), version introuvable après
#   « git fetch --tags » (sans réseau, la version doit déjà être connue localement) ;
# - version actuelle notée, moteur et LM Studio démarrés, sauvegarde chiffrée dans territoria-donnees\mises-a-jour\ (ou
#   --destination) aussitôt vérifiée, passage à la version, reconstruction, credentials et workflows de la nouvelle
#   version importés dans n8n (provisionner_n8n), redémarrage, « outils verifier » ;
# - en cas d'échec : arrêt des conteneurs, territoria\ et n8n\ déplacés dans territoria-donnees\echec-<horodatage>\ (accès
#   réservé à l'utilisateur, jamais supprimé automatiquement), restauration avec l'image de la version qui a fait la
#   sauvegarde (gardée par l'étiquette <projet>-territoria:avant-mise-a-jour, retirée à la fin), retour à l'ancienne
#   version, reconstruction, redémarrage, « outils verifier », puis code 1 et le message
#   « Mise à jour annulée : version précédente rétablie. » ;
# - si le retour échoue à son tour, rien n'est réessayé : un message dit où sont les données et qu'une personne doit
#   intervenir (restauration_interrompue : données d'avant la restauration gardées dans territoria-donnees\.restauration-*).
# Mot de passe de la sauvegarde : secrets\sauvegarde.motdepasse s'il existe, sinon saisi deux fois, masqué, au début, et
# gardé en mémoire (octets) pour la restauration éventuelle ; jamais en argument, jamais écrit, jamais affiché.
# Dernière ligne : « ETAPE mettre-a-jour : OK|ECHEC <code> ».
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path -Path $PSScriptRoot -ChildPath 'commun.ps1')

$script:MessageAnnulation = 'Mise à jour annulée : version précédente rétablie.'
$script:DelaiRecuperation = 120  # secondes au plus pour « git fetch --tags »
$script:Version = ''
$script:DestinationMaj = ''
$script:ModeMotDePasse = ''       # auto (fichier sauvegarde.motdepasse) ou memoire (saisi au début)
$script:OctetsMotDePasse = $null
$script:AncienCommit = ''
$script:AncienneBranche = ''
$script:CommitCible = ''
$script:ArchiveMaj = ''
$script:ImageAvant = ''            # étiquette de l'image territoria de la version qui fait la sauvegarde : elle sert à la restaurer
$script:DossierEchec = ''
$script:CodeMiseAJour = ''
$script:EtatMaj = 'preparation'    # preparation, mise_a_jour, retour, termine
$script:EtapeRetour = ''           # arret, deplacement, restauration, version, demarrage, verification
$script:ConsigneDonnee = $false

$mots = @($args | ForEach-Object { [string]$_ })
$i = 0
while ($i -lt $mots.Count) {
    $option = $mots[$i]
    if ($option -ceq '--version') {
        if ($i + 1 -ge $mots.Count) { refuser_arguments 'mettre-a-jour' }
        $script:Version = $mots[$i + 1]
        $i += 2
    } elseif ($option -ceq '--destination') {
        if ($i + 1 -ge $mots.Count) { refuser_arguments 'mettre-a-jour' }
        $script:DestinationMaj = $mots[$i + 1]
        $i += 2
    } elseif ($option -ceq '--simulation') {
        activer_simulation
        $i++
    } elseif ($option -ceq '-h' -or $option -ceq '--help') {
        sortie 'Usage : install\mettre-a-jour.ps1 --version <ref git> [--destination <dossier>] [--simulation]'
        exit 0
    } else {
        refuser_arguments 'mettre-a-jour'
    }
}
# Une version qui commence par « - » serait lue comme une option de git.
if ($script:Version.Length -eq 0 -or $script:Version.StartsWith('-')) { refuser_arguments 'mettre-a-jour' }

# --- Consignes quand la mise à jour ou le retour s'arrête en route ----------------------------------------------------

function version_precedente {
    if ($script:AncienneBranche.Length -gt 0) { return ('{0} (branche {1})' -f $script:AncienCommit, $script:AncienneBranche) }
    return $script:AncienCommit
}

# Arrêt non rattrapé pendant la mise à jour elle-même (interruption) : aucun retour n'a été fait. Les trois commandes sont
# à recopier telles quelles dans PowerShell, une par ligne : chemins absolus et protégés (citer), même avec des espaces
# ou une apostrophe.
function consigne_interruption {
    info ("Mise à jour interrompue ({0}) : aucun retour automatique n'a été fait." -f (code_propre $script:CodeEchec))
    info ("Version précédente : {0}. Sauvegarde d'avant la mise à jour : {1}" -f (version_precedente), $script:ArchiveMaj)
    $cible = $script:AncienneBranche
    if ($cible.Length -eq 0) { $cible = $script:AncienCommit }
    info "Pour revenir en arrière, lancez ces trois commandes l'une après l'autre dans PowerShell (la restauration demande le mot de passe de la sauvegarde) :"
    info ('  git -C {0} checkout {1}' -f (citer $script:Racine), (citer $cible))
    info ('  powershell -NoProfile -ExecutionPolicy Bypass -File {0} --archive {1} --forcer' -f (citer (joindre $script:Racine @('install', 'restaurer.ps1'))), (citer $script:ArchiveMaj))
    info ('  powershell -NoProfile -ExecutionPolicy Bypass -File {0}' -f (citer (joindre $script:Racine @('install', 'demarrer.ps1'))))
}

# Le retour à la version précédente a échoué : rien n'est réessayé, une personne doit intervenir.
function consigne_retour_impossible {
    $script:ConsigneDonnee = $true
    if ($script:CodeEchec -ceq 'restauration_interrompue') {
        $restes = @(Get-ChildItem -LiteralPath $script:Donnees -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name.StartsWith('.restauration-') } | ForEach-Object { $_.FullName })
        info "Restauration interrompue pendant le retour à la version précédente : rien n'a été supprimé et rien ne sera réessayé."
        if ($restes.Count -gt 0) {
            info ('Les données présentes avant la restauration sont gardées dans : ' + ($restes -join ' '))
        } else {
            info 'Les données présentes avant la restauration sont gardées dans : territoria-donnees\.restauration-* (introuvable)'
        }
    } else {
        info ('Retour à la version précédente impossible (étape {0} : {1}).' -f $script:EtapeRetour, (code_propre $script:CodeEchec))
        $script:CodeEchec = 'retour_impossible'
    }
    if ($script:DossierEchec.Length -gt 0) { info ('Les données de la tentative ratée sont dans : ' + $script:DossierEchec) }
    info ("Sauvegarde d'avant la mise à jour : {0} ; version précédente : {1}." -f $script:ArchiveMaj, (version_precedente))
    info "Une intervention humaine est nécessaire : ne relancez ni la mise à jour ni la restauration avant d'avoir examiné ces dossiers."
}

# --- Préparation : rien n'est modifié tant qu'un contrôle échoue -----------------------------------------------------

# « git -C <dépôt> <arguments> » : rend @{ Code; Sortie } (sortie d'erreur écartée).
function git_depot {
    param([string[]]$arguments, [int]$delai = 0)
    return (executer 'git' (@('-C', $script:Racine) + $arguments) $null $delai $true)
}

# Dépôt propre, versions récupérées, version demandée résolue en commit ; version actuelle notée.
function preparer_version {
    if ($script:Simulation) {
        expliquer ('contrôle : aucune modification locale dans {0} (git status --porcelain)' -f $script:Racine)
        lancer git -C $script:Racine fetch --tags --quiet
        expliquer ('contrôle : la version {0} existe ; version actuelle notée (git rev-parse HEAD)' -f $script:Version)
        $script:AncienCommit = '<version actuelle>'
        $script:CommitCible = ('<commit de {0}>' -f $script:Version)
        return
    }
    if (-not (commande_presente 'git')) { echouer git_absent 'git est introuvable : la mise à jour suit les versions du dépôt.' }
    $r = git_depot @('rev-parse', '--is-inside-work-tree')
    if ($r.Code -ne 0 -or $r.Sortie.Trim() -cne 'true') {
        echouer depot_absent ("{0} n'est pas un dépôt git : la mise à jour suit les versions du dépôt." -f $script:Racine)
    }
    $r = git_depot @('-c', 'core.quotePath=false', 'status', '--porcelain')  # accents lisibles dans le message
    if ($r.Code -ne 0) { echouer depot_illisible ('État du dépôt illisible (git status) : ' + $script:Racine) }
    if ($r.Sortie.Trim().Length -gt 0) { refuser_modifications $r.Sortie }
    $recuperee = $true
    $invite = $env:GIT_TERMINAL_PROMPT
    $env:GIT_TERMINAL_PROMPT = '0'
    try {
        $r = git_depot @('fetch', '--tags', '--quiet') $script:DelaiRecuperation
    } finally {
        $env:GIT_TERMINAL_PROMPT = $invite
    }
    if ($r.Code -ne 0) {
        $recuperee = $false
        info ('Récupération des versions impossible (git fetch --tags ; réseau ?) : recherche de {0} parmi les versions déjà connues.' -f $script:Version)
    }
    $script:CommitCible = ''
    $r = git_depot @('rev-parse', '--verify', '--quiet', ($script:Version + '^{commit}'))
    if ($r.Code -eq 0) { $script:CommitCible = $r.Sortie.Trim() }
    if ($script:CommitCible.Length -eq 0) {
        if (-not $recuperee) {
            echouer recuperation_impossible ('Version {0} inconnue localement, et récupération des versions impossible.' -f $script:Version)
        }
        echouer version_introuvable ('Version introuvable : ' + $script:Version)
    }
    $r = git_depot @('rev-parse', '--verify', 'HEAD')
    if ($r.Code -ne 0) { echouer depot_illisible ('Version actuelle illisible (git rev-parse HEAD) : ' + $script:Racine) }
    $script:AncienCommit = $r.Sortie.Trim()
    $script:AncienneBranche = ''
    $r = git_depot @('symbolic-ref', '--quiet', '--short', 'HEAD')
    if ($r.Code -eq 0) { $script:AncienneBranche = $r.Sortie.Trim() }
    info ('Version actuelle : {0} ; version demandée : {1} ({2}).' -f (version_precedente), $script:Version, $script:CommitCible)
}

# Refus sur un dépôt modifié : les fichiers en cause sont nommés (10 au plus ; ce ne sont pas des secrets), avec ce
# qu'il faut faire. Les fichiers que le système crée tout seul (.DS_Store, ._*, Thumbs.db, desktop.ini) sont ignorés
# par .gitignore et n'arrivent jamais ici.
function refuser_modifications {
    param([string]$sortieGit)
    info ('Le dossier {0} contient des fichiers ajoutés ou modifiés : la mise à jour ne démarre pas.' -f $script:Racine)
    $lignes = @($sortieGit -split "\r?\n" | Where-Object { $_.Length -gt 3 })
    for ($n = 0; $n -lt $lignes.Count -and $n -lt 10; $n++) {
        $code = $lignes[$n].Substring(0, 2)
        $etat = 'modifié'
        if ($code.StartsWith('?')) { $etat = 'ajouté' } elseif ($code.Contains('D')) { $etat = 'supprimé' }
        info ('  {0} ({1})' -f $lignes[$n].Substring(3), $etat)
    }
    if ($lignes.Count -gt 10) { info ('  … et {0} autres' -f ($lignes.Count - 10)) }
    echouer modifications_locales "Déplacez hors de ce dossier les fichiers que vous y avez ajoutés, ou demandez à la personne qui gère l'installation d'annuler les modifications (git status), puis relancez la mise à jour."
}

# Dossier de la sauvegarde : celui de l'opérateur (contrôlé comme pour install\sauvegarder.ps1), sinon mises-a-jour\.
function preparer_destination {
    if ($script:DestinationMaj.Length -gt 0) {
        verifier_destination $script:DestinationMaj
        return
    }
    $script:DestinationMaj = Join-Path -Path $script:Donnees -ChildPath 'mises-a-jour'
    phase dossier_de_sauvegarde_impossible
    creer_dossier $script:DestinationMaj $true
}

function preparer_mot_de_passe {
    if ((Test-Path -LiteralPath $script:FichierMotDePasse -PathType Leaf) -and ((Get-Item -LiteralPath $script:FichierMotDePasse).Length -gt 0)) {
        $script:ModeMotDePasse = 'auto'
        return
    }
    $script:ModeMotDePasse = 'memoire'
    if ($script:Simulation) { return }
    info "Aucun mot de passe de sauvegarde enregistré : choisissez celui de la sauvegarde d'avant la mise à jour (il servira aussi à la restaurer en cas d'échec)."
    $script:OctetsMotDePasse = saisir_mot_de_passe_sauvegarde
}

# Nom de la plus récente archive territoria-AAAAMMJJ-HHMMSS.tsv1 du dossier (vide s'il n'y en a pas).
function derniere_archive {
    param([string]$dossier)
    if (-not (Test-Path -LiteralPath $dossier -PathType Container)) { return '' }
    $noms = @(Get-ChildItem -LiteralPath $dossier -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -cmatch '^territoria-\d{8}-\d{6}\.tsv1$' } | ForEach-Object { $_.Name } | Sort-Object)
    if ($noms.Count -eq 0) { return '' }
    return [string]$noms[-1]
}

# Sauvegarde d'avant la mise à jour, puis sa vérification complète : un retour arrière ne découvrira jamais une archive
# illisible ou un mot de passe faux.
function sauvegarder_avant {
    $avant = derniere_archive $script:DestinationMaj
    phase sauvegarde_impossible
    sauvegarde_ecrire $script:DestinationMaj $script:ModeMotDePasse $script:OctetsMotDePasse
    if ($script:Simulation) {
        $script:ArchiveMaj = Join-Path -Path $script:DestinationMaj -ChildPath 'territoria-AAAAMMJJ-HHMMSS.tsv1'
    } else {
        $apres = derniere_archive $script:DestinationMaj
        if ($apres.Length -eq 0 -or $apres -ceq $avant) {
            echouer sauvegarde_introuvable ('Archive de la sauvegarde introuvable dans {0} (horloge de la machine ?).' -f $script:DestinationMaj)
        }
        $script:ArchiveMaj = Join-Path -Path $script:DestinationMaj -ChildPath $apres
    }
    phase verification_de_la_sauvegarde
    sauvegarde_verifier $script:ArchiveMaj $script:ModeMotDePasse $script:OctetsMotDePasse
    info ("Sauvegarde d'avant la mise à jour : " + $script:ArchiveMaj)
    garder_image_avant
}

# L'image de la version actuelle reçoit une étiquette à elle : avec le magasin d'images de containerd (Docker récent), une
# image qui perd son nom et son conteneur est supprimée aussitôt, et la restauration d'un retour arrière en a besoin.
function garder_image_avant {
    $etiquette = '{0}-territoria:avant-mise-a-jour' -f $script:Projet
    phase etiquette_impossible
    moteur tag $script:Image $etiquette
    $script:ImageAvant = $etiquette
}

# Retire cette étiquette quand elle ne sert plus (mise à jour réussie, ou retour arrière réussi) ; sans conséquence si
# c'est impossible.
function retirer_image_avant {
    if ($script:ImageAvant.Length -eq 0) { return }
    if ($script:Simulation) {
        afficher_commande @($script:Moteur, 'image', 'rm', $script:ImageAvant)
        return
    }
    $resultat = executer $script:Moteur @('image', 'rm', $script:ImageAvant) $null 120 $true
    if ($resultat.Code -ne 0) { info ('Étiquette {0} laissée en place (sans conséquence).' -f $script:ImageAvant) }
}

# --- Mise à jour (étapes 3 à 5) ------------------------------------------------------------------------------------

function appliquer_version {
    phase changement_de_version_impossible
    lancer git -C $script:Racine checkout --quiet --detach $script:CommitCible
    # Dossiers apparus avec les nouvelles versions (enregistrements des séances, modèles de transcription) : créés et
    # confiés aux services, sans toucher à ceux qui existent.
    preparer_dossiers
    phase construction_impossible
    compose build territoria
    # Image de transcription et modèles (les fichiers présents et intacts ne sont pas retéléchargés).
    modeles_audio_facultatif
    # Migration (spécification, § 4.5) : credentials et workflows de la nouvelle version importés dans n8n, n8n arrêté
    # pendant l'import (provisionnement idempotent de l'étape n8n, depuis la nouvelle image). Le test de bout en bout joue
    # ainsi les nouveaux workflows ; en cas d'échec, la restauration remet la base n8n d'avant, donc les anciens workflows.
    provisionner_n8n
    phase demarrage_impossible
    compose up -d
    attendre_sain 'n8n'
    attendre_sain 'territoria'
    phase verification_echouee
    outils_service verifier
}

# --- Retour automatique ----------------------------------------------------------------------------------------------

# territoria\ et n8n\ vont dans echec-<horodatage>\ (accès réservé à l'utilisateur) par un conteneur jetable en root,
# comme la version POSIX (sous Linux, un dossier appartenant à 10001 ou à 1000 ne change pas de dossier parent sans
# droit d'écriture sur lui-même). Le conteneur part de l'image étiquetée avant la mise à jour : elle est présente à coup
# sûr, même sans réseau. Le script du conteneur n'a pas de guillemets : verifier_arguments les refuse, et les noms
# déplacés (territoria, n8n, echec-<chiffres>) n'en ont pas besoin.
function deplacer_vers_echec {
    $horodatage = [DateTime]::Now.ToString('yyyyMMdd-HHmmss')
    $nom = 'echec-' + $horodatage
    if (-not $script:Simulation) {
        $n = 1
        while (Test-Path -LiteralPath (Join-Path -Path $script:Donnees -ChildPath $nom)) {
            $n++
            $nom = 'echec-{0}-{1}' -f $horodatage, $n
        }
    }
    $script:DossierEchec = Join-Path -Path $script:Donnees -ChildPath $nom
    phase deplacement_impossible
    creer_dossier $script:DossierEchec $true
    moteur run --rm --network none --user 0:0 --entrypoint sh -v (montage $script:Donnees '/donnees-hote') $script:ImageAvant `
        -c 'set -e; for r in territoria n8n; do if [ -e /donnees-hote/$r ]; then mv /donnees-hote/$r /donnees-hote/$1/$r; fi; done' `
        sh $nom
}

# Remet la branche d'origine si elle désigne toujours l'ancien commit, sinon l'ancien commit lui-même.
function revenir_a_l_ancienne_version {
    $branche = $false
    if ($script:AncienneBranche.Length -gt 0 -and -not $script:Simulation) {
        $r = git_depot @('rev-parse', '--verify', '--quiet', ('refs/heads/' + $script:AncienneBranche))
        $branche = ($r.Code -eq 0 -and $r.Sortie.Trim() -ceq $script:AncienCommit)
    }
    if ($branche) {
        lancer git -C $script:Racine checkout --quiet $script:AncienneBranche
    } else {
        lancer git -C $script:Racine checkout --quiet --detach $script:AncienCommit
    }
}

function retour_arriere {
    $script:EtatMaj = 'retour'
    if ($script:Simulation) {
        expliquer "si l'une de ces étapes échoue : retour automatique à la version précédente"
    } else {
        info ('Échec de la mise à jour vers {0} ({1}) : retour automatique à la version précédente.' -f $script:Version, $script:CodeMiseAJour)
    }
    try {
        $script:EtapeRetour = 'arret'
        phase arret_impossible
        compose stop
        $script:EtapeRetour = 'deplacement'
        deplacer_vers_echec
        $script:EtapeRetour = 'restauration'
        sauvegarde_restaurer $script:ArchiveMaj 'forcer' $script:ModeMotDePasse $script:ImageAvant $script:OctetsMotDePasse
        $script:EtapeRetour = 'version'
        phase retour_de_version_impossible
        revenir_a_l_ancienne_version
        $script:EtapeRetour = 'demarrage'
        phase construction_impossible
        compose build territoria
        phase demarrage_impossible
        compose up -d
        attendre_sain 'n8n'
        attendre_sain 'territoria'
        $script:EtapeRetour = 'verification'
        phase verification_echouee
        outils_service verifier
    } catch {
        if ($_.Exception.Message -ne 'territoria-echec') { info ('Erreur : ' + $_.Exception.Message) }
        consigne_retour_impossible
        throw 'territoria-echec'
    }
    retirer_image_avant
    $script:EtatMaj = 'termine'
    if ($script:Simulation) { return }
    info ('Les données de la tentative ratée sont conservées dans {0} (accès réservé ; supprimez ce dossier vous-même quand vous n''en aurez plus besoin).' -f $script:DossierEchec)
    info ("Sauvegarde d'avant la mise à jour : " + $script:ArchiveMaj)
    echouer mise_a_jour_annulee $script:MessageAnnulation
}

# --- Programme -------------------------------------------------------------------------------------------------------

executer_etape 'mettre-a-jour' {
    try {
        verifier_dossier_donnees
        preparer_version
        preparer_destination
        if (-not $script:Simulation -and (-not (Test-Path -LiteralPath (Join-Path -Path $script:Donnees -ChildPath 'territoria') -PathType Container) -or
                -not (Test-Path -LiteralPath (Join-Path -Path $script:Secrets -ChildPath 'compose.env') -PathType Leaf))) {
            echouer installation_absente ('Aucune installation dans ' + $script:Donnees)
        }
        preparer_mot_de_passe
        assurer_moteur_actif
        demarrer_lmstudio
        sauvegarder_avant

        $script:EtatMaj = 'mise_a_jour'
        $reussie = $false
        try {
            appliquer_version
            $reussie = $true
        } catch {
            if ($_.Exception.Message -ne 'territoria-echec') { info ('Erreur : ' + $_.Exception.Message) }
            $script:CodeMiseAJour = code_propre $script:CodeEchec
        }
        if ($reussie -and -not $script:Simulation) {
            $script:EtatMaj = 'termine'
            retirer_image_avant
            info ('Mise à jour terminée : version {0} ({1}).' -f $script:Version, $script:CommitCible)
            return
        }
        retour_arriere
    } finally {
        # Une interruption (Ctrl+C) n'est pas rattrapée : seule cette clause s'exécute encore.
        if ($script:EtatMaj -ceq 'mise_a_jour') {
            consigne_interruption
        } elseif ($script:EtatMaj -ceq 'retour' -and -not $script:ConsigneDonnee) {
            consigne_retour_impossible
        }
        if ($null -ne $script:OctetsMotDePasse) { [Array]::Clear($script:OctetsMotDePasse, 0, $script:OctetsMotDePasse.Length) }
    }
}
exit $script:CodeSortie
