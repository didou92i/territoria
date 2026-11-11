# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
# Vérifications des scripts d'installation Windows (install/*.ps1) dans PowerShell 7, sous Linux, dans un conteneur
# jetable (voir tests/test_install_ps1.py). Rien n'est modifié hors des dossiers temporaires du conteneur ; le dépôt est
# monté en lecture seule. Chaque cas imprime une ligne « RESULTAT|<nom>|OK » ou « RESULTAT|<nom>|ECHEC|<détail> ».
# Ce que ces essais ne prouvent pas (icacls, COM, schtasks, winget, Get-NetTCPConnection, Windows PowerShell 5.1) est
# listé dans le rapport de la tâche 12b et reste à valider sur un vrai Windows.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$depot = '/repo'
$base = '/tmp/verif'
$null = New-Item -ItemType Directory -Path $base -Force
$cheminInitial = $env:PATH

function resultat {
    param([string]$nom, [bool]$ok, [string]$detail = '')
    $etat = 'ECHEC'
    if ($ok) { $etat = 'OK' }
    [Console]::Out.WriteLine(('RESULTAT|{0}|{1}|{2}' -f $nom, $etat, ($detail -replace '\r?\n', ' / ')))
}

function cas {
    param([string]$nom, [scriptblock]$corps)
    try {
        $null = & $corps
        resultat $nom $true
    } catch {
        resultat $nom $false ($_.Exception.Message + ' @' + $_.InvocationInfo.ScriptLineNumber)
    }
}

function verifie {
    param([bool]$condition, [string]$message)
    if (-not $condition) { throw $message }
}

function dossier_neuf {
    param([string]$nom)
    $chemin = Join-Path -Path $base -ChildPath $nom
    if (Test-Path -LiteralPath $chemin) { Remove-Item -LiteralPath $chemin -Recurse -Force }
    $null = New-Item -ItemType Directory -Path $chemin -Force
    return $chemin
}

# Dossier vide, au cache de l'émulateur Rosetta près (Docker Desktop sur Apple Silicon l'écrit dans $HOME pour tout
# programme amd64 ; ce n'est pas le fait des scripts).
function contenu_vide {
    param([string]$dossier)
    $trouves = @(Get-ChildItem -LiteralPath $dossier -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '/\.cache(/rosetta(/.*)?)?$' })
    return ($trouves.Count -eq 0)
}

# Programmes factices : ils consignent leurs arguments dans $JOURNAL_FACTICES et rendent 0 (ou ce que dit $corps).
function creer_factice {
    param([string]$dossier, [string]$nom, [string]$corps = '')
    $null = New-Item -ItemType Directory -Path $dossier -Force
    $chemin = Join-Path -Path $dossier -ChildPath $nom
    $entete = @'
#!/bin/sh
printf '%s\n' "$(basename "$0") $*" >>"$JOURNAL_FACTICES"
'@
    [System.IO.File]::WriteAllText($chemin, ($entete + "`n" + $corps + "`nexit 0`n"))
    & chmod 755 $chemin
}

function journal_factices {
    if (Test-Path -LiteralPath $env:JOURNAL_FACTICES) { return [System.IO.File]::ReadAllText($env:JOURNAL_FACTICES) }
    return ''
}

function nouvel_environnement {
    param([string]$nom, [string[]]$factices = @())
    $dossier = dossier_neuf $nom
    $script:tmp_maison = Join-Path -Path $dossier -ChildPath 'maison'
    $script:tmp_donnees = Join-Path -Path $dossier -ChildPath 'territoria donnees'
    $script:rep_factices = Join-Path -Path $dossier -ChildPath 'factices'
    $null = New-Item -ItemType Directory -Path $script:tmp_maison, $script:rep_factices -Force
    $env:HOME = $script:tmp_maison
    $env:USERPROFILE = $script:tmp_maison
    $env:LOCALAPPDATA = Join-Path -Path $script:tmp_maison -ChildPath 'AppData-Local'
    $env:TERRITORIA_DONNEES_HOTE = $script:tmp_donnees
    $env:JOURNAL_FACTICES = Join-Path -Path $dossier -ChildPath 'appels.log'
    foreach ($factice in $factices) { creer_factice $script:rep_factices $factice }
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
    return $dossier
}

# Exécute un script du dépôt dans un nouveau processus pwsh ; rend la sortie standard (lignes), le code et la sortie d'erreur.
function lancer_script {
    param([string]$nomScript, [string[]]$arguments = @())
    $erreur = Join-Path -Path $base -ChildPath 'stderr.txt'
    $sortie = @(& pwsh -NoProfile -File (Join-Path -Path $depot -ChildPath ('install/' + $nomScript)) @arguments 2>$erreur)
    return [pscustomobject]@{ Code = $LASTEXITCODE; Lignes = $sortie; Erreur = [System.IO.File]::ReadAllText($erreur) }
}

function securiser {
    param([string]$texte)
    $secret = New-Object System.Security.SecureString
    foreach ($c in $texte.ToCharArray()) { $secret.AppendChar($c) }
    $secret.MakeReadOnly()
    return $secret
}

# Analyse d'une ligne de commande comme CommandLineToArgvW (règles de la documentation de Microsoft : 2n barres inverses
# avant un guillemet donnent n barres et ouvrent ou ferment une citation ; 2n+1 donnent n barres et un guillemet
# littéral ; les autres barres inverses sont littérales). Indépendante de citer_windows.
function argv_windows {
    param([string]$ligne)
    $mots = New-Object System.Collections.Generic.List[string]
    $courant = New-Object System.Text.StringBuilder
    $citation = $false
    $present = $false
    $i = 0
    $n = $ligne.Length
    while ($i -lt $n) {
        $c = $ligne[$i]
        if ($c -eq '\') {
            $barres = 0
            while ($i -lt $n -and $ligne[$i] -eq '\') { $barres++; $i++ }
            $present = $true
            if ($i -lt $n -and $ligne[$i] -eq '"') {
                [void]$courant.Append('\' * [int][math]::Floor($barres / 2))
                if ($barres % 2 -eq 1) { [void]$courant.Append('"'); $i++ }
            } else {
                [void]$courant.Append('\' * $barres)
            }
            continue
        }
        if ($c -eq '"') { $citation = -not $citation; $present = $true; $i++; continue }
        if (($c -eq ' ' -or $c -eq "`t") -and -not $citation) {
            if ($present) { $mots.Add($courant.ToString()); [void]$courant.Clear(); $present = $false }
            $i++
            continue
        }
        [void]$courant.Append($c)
        $present = $true
        $i++
    }
    if ($present) { $mots.Add($courant.ToString()) }
    return , $mots.ToArray()
}

function capturer {
    param([scriptblock]$corps)
    $sortieAvant = [Console]::Out
    $erreurAvant = [Console]::Error
    $ecrit = New-Object System.IO.StringWriter
    $ecritErreur = New-Object System.IO.StringWriter
    [Console]::SetOut($ecrit)
    [Console]::SetError($ecritErreur)
    $exception = $null
    try { $null = & $corps } catch { $exception = $_ } finally {
        [Console]::SetOut($sortieAvant)
        [Console]::SetError($erreurAvant)
    }
    return [pscustomobject]@{ Sortie = $ecrit.ToString(); Erreur = $ecritErreur.ToString(); Exception = $exception }
}

function echec_attendu {
    param([string]$code, [scriptblock]$corps)
    $script:CodeEchec = ''
    $r = capturer $corps
    verifie ($null -ne $r.Exception) ('aucun échec ; attendu : ' + $code)
    verifie ($r.Exception.Exception.Message -eq 'territoria-echec') ('exception inattendue : ' + $r.Exception.Exception.Message)
    verifie ($script:CodeEchec -eq $code) ('code {0}, attendu {1}' -f $script:CodeEchec, $code)
    return $r
}

function fixer_donnees {
    param([string]$dossier)
    $script:Donnees = $dossier
    $env:TERRITORIA_DONNEES_HOTE = $dossier
    $script:Secrets = Join-Path -Path $dossier -ChildPath 'secrets'
    $script:FichierMotDePasse = Join-Path -Path $script:Secrets -ChildPath 'sauvegarde.motdepasse'
    $script:Simulation = $false
    $script:Moteur = ''
    $script:Auto = $false
}

function octets_de {
    param([string]$chemin)
    return , [System.IO.File]::ReadAllBytes($chemin)
}

$utf8 = New-Object System.Text.UTF8Encoding($false)

# =====================================================================================================================
# Chargement des scripts
# =====================================================================================================================

$fichiers = @(Get-ChildItem -LiteralPath (Join-Path -Path $depot -ChildPath 'install') -Filter '*.ps1' | Sort-Object Name)

cas 'analyse_syntaxique' {
    verifie ($fichiers.Count -eq 8) ('fichiers .ps1 : ' + $fichiers.Count)
    foreach ($fichier in $fichiers) {
        $erreurs = $null
        $jetons = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($fichier.FullName, [ref]$jetons, [ref]$erreurs)
        verifie ($erreurs.Count -eq 0) ($fichier.Name + ' : ' + ($erreurs | ForEach-Object { $_.Message } | Select-Object -First 1))
    }
}

cas 'aucun_alias_en_collision' {
    $noms = @()
    foreach ($fichier in $fichiers) {
        $arbre = [System.Management.Automation.Language.Parser]::ParseFile($fichier.FullName, [ref]$null, [ref]$null)
        foreach ($f in $arbre.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) { $noms += $f.Name }
    }
    verifie ($noms.Count -gt 60) ('fonctions trouvées : ' + $noms.Count)
    $alias = @(Get-Alias | ForEach-Object { $_.Name })
    foreach ($nom in $noms) {
        verifie ($alias -notcontains $nom) ('alias de même nom : ' + $nom)
        verifie ($null -eq (Get-Command -Name $nom -CommandType Cmdlet -ErrorAction SilentlyContinue)) ('applet de commande de même nom : ' + $nom)
    }
}

cas 'chargement_sans_effet' {
    $dossier = nouvel_environnement 'chargement'
    $erreur = Join-Path -Path $dossier -ChildPath 'stderr.txt'
    $sortie = @(& pwsh -NoProfile -Command ('. "{0}/install/commun.ps1"' -f $depot) 2>$erreur)
    verifie ($sortie.Count -eq 0) ('sortie : ' + ($sortie -join ' '))
    verifie ([System.IO.File]::ReadAllText($erreur).Length -eq 0) ('erreur : ' + [System.IO.File]::ReadAllText($erreur))
    verifie (contenu_vide $script:tmp_maison) 'HOME modifié'
    verifie (-not (Test-Path -LiteralPath $script:tmp_donnees)) 'territoria-donnees créé'
    verifie ((journal_factices).Length -eq 0) 'programme factice appelé'
}

# =====================================================================================================================
# Diagnostic
# =====================================================================================================================

cas 'diagnostic_treize_cles' {
    $null = nouvel_environnement 'diagnostic' @('docker', 'podman', 'lms')
    $r = lancer_script 'diagnostic.ps1'
    verifie ($r.Code -eq 0) ('code ' + $r.Code + ' ' + $r.Erreur)
    $lignes = @($r.Lignes | ForEach-Object { ([string]$_).TrimEnd("`r") })
    $cles = @('systeme', 'architecture', 'memoire_go', 'gpu', 'disque_libre_go', 'port_8878', 'moteur', 'docker_desktop', 'lms', 'chiffrement_disque', 'palier_modele', 'transcription', 'modeles_audio', 'ia_en_ligne')
    verifie ($lignes.Count -ge $cles.Count) ('lignes : ' + $lignes.Count)
    for ($i = 0; $i -lt $cles.Count; $i++) { verifie ($lignes[$i].StartsWith($cles[$i] + '=')) ('ligne {0} : {1}' -f $i, $lignes[$i]) }
    $valeurs = @{}
    foreach ($ligne in $lignes[0..($cles.Count - 1)]) { $parts = $ligne.Split('=', 2); $valeurs[$parts[0]] = $parts[1] }
    verifie ($valeurs['transcription'] -in @('en_marche', 'arrete', 'inconnu')) ('transcription ' + $valeurs['transcription'])
    verifie ($valeurs['modeles_audio'] -eq 'absents') ('modeles_audio ' + $valeurs['modeles_audio'])
    verifie ($valeurs['systeme'] -eq 'linux') ('systeme ' + $valeurs['systeme'])
    verifie ($valeurs['memoire_go'] -match '^\d+$') 'memoire_go'
    verifie ($valeurs['disque_libre_go'] -match '^(\d+|inconnu)$') 'disque_libre_go'
    verifie ($valeurs['gpu'] -match '^[a-z0-9_]+$') 'gpu'
    verifie ($valeurs['port_8878'] -in @('libre', 'occupe')) 'port_8878'
    verifie ($valeurs['moteur'] -eq 'docker') ('moteur ' + $valeurs['moteur'])
    verifie ($valeurs['docker_desktop'] -in @('oui', 'non')) 'docker_desktop'
    verifie ($valeurs['lms'] -eq 'present') ('lms ' + $valeurs['lms'])
    verifie ($valeurs['chiffrement_disque'] -in @('actif', 'inactif', 'inconnu')) 'chiffrement_disque'
    $memoire = [int]$valeurs['memoire_go']
    $attendu = 'grand'
    if ($memoire -lt 12) { $attendu = 'petit' } elseif ($memoire -lt 28) { $attendu = 'moyen' }
    verifie ($valeurs['palier_modele'] -eq $attendu) 'palier_modele'
    verifie ($valeurs['ia_en_ligne'] -eq 'aucune') ('ia_en_ligne ' + $valeurs['ia_en_ligne'])
    $reste = @($lignes | Select-Object -Skip $cles.Count)
    verifie ($reste.Count -eq 0) ('lignes en trop : ' + ($reste -join ' '))
    verifie (contenu_vide $script:tmp_maison) 'HOME modifié'
    verifie (-not (Test-Path -LiteralPath $script:tmp_donnees)) 'territoria-donnees créé'
    # Seules sondes qui lancent un programme : la plateforme de Docker et l'état du conteneur de transcription (lecture seule).
    foreach ($appel in @((journal_factices) -split "`n" | Where-Object { $_.Length -gt 0 })) {
        verifie ($appel.StartsWith('docker version') -or $appel.StartsWith('docker ps --filter')) ('programme factice appelé : ' + $appel)
    }
}

# =====================================================================================================================
# Simulation, script par script
# =====================================================================================================================

function verifier_simulation {
    param([string]$nomScript, [string[]]$arguments, [string]$nom, [string[]]$factices = @(), [string]$racine_sim = 'simulation')
    $dossier = nouvel_environnement $racine_sim $factices
    $r = lancer_script $nomScript ($arguments + @('--simulation'))
    $lignes = @($r.Lignes | ForEach-Object { ([string]$_).TrimEnd("`r") })
    verifie ($r.Code -eq 0) ('code {0} : {1} {2}' -f $r.Code, ($lignes -join ' / '), $r.Erreur)
    verifie ($lignes[-1] -eq "ETAPE $nom : OK") ('dernière ligne : ' + $lignes[-1])
    verifie (@($lignes | Where-Object { $_.StartsWith('ETAPE ') }).Count -eq 1) 'plusieurs lignes ETAPE'
    foreach ($ligne in ($lignes | Select-Object -SkipLast 1)) {
        verifie ($ligne -match '^(\+ |# |\|   )') ('ligne de simulation inattendue : ' + $ligne)
    }
    verifie ((journal_factices).Length -eq 0) ('commande factice appelée : ' + (journal_factices))
    verifie (contenu_vide $script:tmp_maison) ('HOME modifié : ' + ((Get-ChildItem -LiteralPath $script:tmp_maison -Recurse -Force | ForEach-Object { $_.FullName }) -join ' '))
    verifie (-not (Test-Path -LiteralPath $script:tmp_donnees)) 'territoria-donnees créé'
    return ($lignes -join "`n")
}

$etapes = @('moteur', 'lmstudio', 'mistral', 'secrets', 'legifrance', 'demarrer', 'n8n', 'demo', 'verifier', 'raccourci', 'sauvegarde-auto')
foreach ($etape in $etapes) {
    cas ('simulation_etape_' + $etape) {
        $null = verifier_simulation 'installer.ps1' @('--etape', $etape) $etape @('winget', 'lms', 'docker', 'podman', 'schtasks', 'icacls')
    }
}

cas 'simulation_legifrance_sans_saisie_piste' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'legifrance') 'legifrance' @('winget', 'lms', 'docker', 'podman', 'schtasks', 'icacls')
    verifie ($sortie.Contains('Autoriser les connecteurs publics (Légifrance, data.gouv.fr)')) ('question : ' + $sortie)
    verifie (-not $sortie.Contains('saisie masquée')) 'saisie masquée'
    verifie ($sortie -cmatch '(?m)^\+ retirer PISTE_CLIENT_ID de .*territoria\.env') 'retrait de PISTE_CLIENT_ID'
    verifie ($sortie -cmatch '(?m)^\+ retirer PISTE_CLIENT_SECRET de .*territoria\.env') 'retrait de PISTE_CLIENT_SECRET'
    verifie ($sortie -cmatch '(?m)^\+ écrire TERRITORIA_HORS_LIGNE=0 dans .*compose\.env') 'en ligne'
    verifie ($sortie -cmatch '(?m)^\+ écrire TERRITORIA_HORS_LIGNE=1 dans .*compose\.env') 'hors ligne'
}

cas 'simulation_demarrer_construit_la_transcription' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'demarrer') 'demarrer' @('winget', 'lms', 'docker', 'podman', 'schtasks', 'icacls')
    $ordre = @(' build territoria', ' build travaux', ' --profile outils run --rm -T travaux-modeles', ' up -d')
    $positions = @($ordre | ForEach-Object { $sortie.IndexOf($_) })
    verifie ($positions -notcontains -1) ('commande absente : ' + ($positions -join ','))
    verifie ((@($positions | Sort-Object) -join ',') -eq ($positions -join ',')) ('ordre : ' + ($positions -join ','))
}

cas 'simulation_moteur_sans_moteur' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'moteur') 'moteur' @('winget', 'schtasks')
    $ordre = @('winget install --exact --id RedHat.Podman --source winget', 'winget install --exact --id Docker.DockerCompose', 'podman machine init', 'podman machine start', 'podman compose version')
    $positions = @($ordre | ForEach-Object { $sortie.IndexOf($_) })
    verifie ($positions -notcontains -1) ('commande absente : ' + ($positions -join ','))
    verifie ((@($positions | Sort-Object) -join ',') -eq ($positions -join ',')) ('ordre : ' + ($positions -join ','))
    verifie ($sortie.Contains('--accept-package-agreements --accept-source-agreements')) 'accords winget'
}

cas 'simulation_moteur_docker_reutilise' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'moteur') 'moteur' @('winget', 'docker')
    verifie (-not $sortie.Contains('winget install')) 'installation inutile'
    verifie ($sortie.Contains('+ docker compose version')) 'docker compose version'
}

cas 'simulation_lmstudio_installe_puis_regle' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'lmstudio', '--palier', 'grand') 'lmstudio' @('winget', 'docker')
    verifie ($sortie.Contains('winget install --exact --id ElementLabs.LMStudio')) 'winget LM Studio'
    verifie ($sortie.Contains('google/gemma-4-26b-a4b@q4_k_m --gguf --yes')) 'lms get'
    verifie ($sortie.Contains('server start --port 1234 --bind 127.0.0.1')) 'server start'
    verifie ($sortie.Contains('networkInterface=127.0.0.1')) 'networkInterface'
    verifie ($sortie.Contains('Get-NetTCPConnection -LocalPort 1234 -State Listen')) 'écoute réelle'
    verifie ($sortie.Contains('TERRITORIA_LLM_URL=http://host.docker.internal:1234/v1')) 'URL docker'
    verifie ($sortie.Contains('TERRITORIA_MODELE=google/gemma-4-26b-a4b')) 'modèle'
    verifie ($sortie.Contains('--add-host host.docker.internal:host-gateway')) 'add-host'
    verifie ($sortie.Contains('marqueur-territoria-')) 'marqueur'
    verifie ($sortie.IndexOf('server stop') -lt $sortie.IndexOf('server start')) 'arrêt avant démarrage'
    verifie ($sortie.IndexOf('server start') -lt $sortie.IndexOf('lms load')) 'démarrage avant chargement'
}

cas 'simulation_lmstudio_podman_et_lms_present' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'lmstudio') 'lmstudio' @('lms', 'podman')
    verifie (-not $sortie.Contains('winget install')) 'installation inutile'
    verifie ($sortie.Contains('TERRITORIA_LLM_URL=http://host.containers.internal:1234/v1')) 'URL podman'
    verifie (-not $sortie.Contains('--add-host')) 'add-host avec Podman'
    verifie ($sortie -match 'Palier|lms get google/gemma-4-(e4b|12b|26b-a4b)@q4_k_m --gguf --yes') 'lms get du palier'
}

cas 'simulation_commandes_compose' {
    $sortie = verifier_simulation 'installer.ps1' @('--etape', 'n8n') 'n8n' @('docker')
    $ordre = @('outils provision-n8n --sortie /donnees-secrets/provision', ' stop n8n', 'import:credentials', 'import:workflow --separate',
        'publish:workflow --id=TerritoriaConv01', 'publish:workflow --id=TerritoriaArre01', 'Remove-Item', ' up -d', 'outils proprietaire-n8n')
    $positions = @($ordre | ForEach-Object { $sortie.IndexOf($_) })
    verifie ($positions -notcontains -1) ('absent : ' + ($positions -join ','))
    verifie ((@($positions | Sort-Object) -join ',') -eq ($positions -join ',')) ('ordre : ' + ($positions -join ','))
    foreach ($ligne in $sortie.Split("`n")) {
        if ($ligne.Contains(' compose ')) { verifie ($ligne.Contains('docker compose -p territoria --env-file ')) ('compose : ' + $ligne) }
    }
    verifie ($sortie.Contains("compose.env' -f /repo/conteneurs/compose.yaml")) 'fichiers compose'
    $v = verifier_simulation 'installer.ps1' @('--etape', 'verifier') 'verifier' @('docker')
    verifie ($v -match 'exec -T territoria python -m outils verifier(\n|$)') 'verifier sans --strict'
    verifie (-not $v.Contains('--strict')) '--strict'
    $s = verifier_simulation 'installer.ps1' @('--etape', 'secrets') 'secrets' @('docker')
    verifie ($s.Contains('-v ''') -or $s.Contains(':/caddy')) 'montage caddy'
    verifie ($s -match 'chown 10001:10001 /territoria /territoria/audio /modeles-audio && chown 1000:1000 /n8n && chown 10002:10002 /caddy') 'propriétaires'
    verifie ($s.Contains('/inheritance:r /grant:r')) 'accès réservé annoncé'
    verifie ($s.IndexOf('New-Item -ItemType Directory -Path') -lt $s.IndexOf('compose.env')) 'dossiers avant fichiers'
}

cas 'simulation_raccourci_et_planification' {
    $r = verifier_simulation 'installer.ps1' @('--etape', 'raccourci', '--auto') 'raccourci' @('winget')
    verifie ($r.Contains('Territoria.lnk')) 'raccourci du bureau'
    verifie ($r.Contains('-File "/repo/install/demarrer.ps1" --ouvrir')) 'arguments --ouvrir'
    verifie ($r.Contains('Territoria demarrage.lnk')) 'démarrage de session'
    $p = verifier_simulation 'installer.ps1' @('--etape', 'sauvegarde-auto') 'sauvegarde-auto' @('schtasks')
    verifie ($p.Contains('+ schtasks /Create /TN Territoria-Sauvegarde /XML')) 'schtasks /Create'
    verifie ($p.Contains('T12:30:00')) '12 h 30'
    verifie ($p.Contains('<DaysInterval>1</DaysInterval>')) 'tous les jours'
    verifie ($p.Contains('sauvegarder.ps1&quot; --auto --destination')) 'arguments de la tâche'
    foreach ($ligne in $p.Split("`n")) {
        if ($ligne.StartsWith('+ schtasks')) { verifie (-not $ligne.Contains('/TR')) ('commande avec /TR : ' + $ligne) }
    }
    verifie ($p.Contains('# tous les jours à 12:30 (équivalent de /SC DAILY /ST 12:30)')) 'explication de la planification'
}

cas 'simulation_autres_scripts' {
    $null = verifier_simulation 'demarrer.ps1' @('--ouvrir') 'demarrer' @('docker', 'lms')
    $d = verifier_simulation 'demarrer.ps1' @() 'demarrer' @('docker', 'lms')
    verifie ($d.Contains('lms server start --port 1234 --bind 127.0.0.1')) 'démarrage de LM Studio'
    $null = verifier_simulation 'arreter.ps1' @() 'arreter' @('docker')
    $s = verifier_simulation 'sauvegarder.ps1' @('--destination', '/mnt/sauvegardes') 'sauvegarder' @('docker')
    verifie ($s.Contains('-v /mnt/sauvegardes:/destination')) 'montage de la destination'
    verifie ($s.Contains(':/source:ro')) 'source en lecture seule'
    verifie (-not $s.Contains('--depuis-fichier')) 'mot de passe saisi'
    $a = verifier_simulation 'sauvegarder.ps1' @('--destination', '/mnt/sauvegardes', '--auto') 'sauvegarder' @('docker')
    verifie ($a.Contains('--depuis-fichier /source/secrets/sauvegarde.motdepasse')) 'mot de passe par fichier'
    verifie ($a.Contains('python -m outils elaguer')) 'élagage'
    verifie ($a.Contains('--network none --user 0:0 --read-only --tmpfs /tmp:mode=1777')) 'conteneur ponctuel'
    $r = verifier_simulation 'restaurer.ps1' @('--archive', '/mnt/s/territoria-20261030-123000.tsv1', '--forcer') 'restaurer' @('docker')
    verifie ($r.Contains('--archive /destination/territoria-20261030-123000.tsv1 --forcer')) 'archive'
    verifie ($r.Contains(' stop')) 'services arrêtés avant'
}

cas 'refus_des_arguments' {
    $null = nouvel_environnement 'refus' @('docker')
    $tests = @(
        @('installer.ps1', @(), 'installer', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'bidon'), 'installer', 'etape_inconnue'),
        @('installer.ps1', @('--etape'), 'installer', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'moteur', '--auto'), 'moteur', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'moteur', '--palier', 'petit'), 'moteur', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'lmstudio', '--palier', 'enorme'), 'lmstudio', 'palier_inconnu'),
        @('installer.ps1', @('--etape', 'moteur', 'secret-recopie'), 'installer', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'Moteur'), 'installer', 'etape_inconnue'),
        @('installer.ps1', @('--etape', 'MOTEUR', '--simulation'), 'installer', 'etape_inconnue'),
        @('installer.ps1', @('--etape', 'Sauvegarde-Auto', '--simulation'), 'installer', 'etape_inconnue'),
        @('installer.ps1', @('--ETAPE', 'moteur'), 'installer', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'moteur', '--SIMULATION'), 'installer', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'raccourci', '--Auto'), 'installer', 'arguments_invalides'),
        @('installer.ps1', @('--etape', 'lmstudio', '--palier', 'Petit', '--simulation'), 'lmstudio', 'palier_inconnu'),
        @('installer.ps1', @('--etape', 'lmstudio', '--Palier', 'petit'), 'installer', 'arguments_invalides'),
        @('demarrer.ps1', @('--OUVRIR', '--simulation'), 'demarrer', 'arguments_invalides'),
        @('sauvegarder.ps1', @('--Destination', '/mnt/x'), 'sauvegarder', 'arguments_invalides'),
        @('restaurer.ps1', @('--Archive', '/mnt/x'), 'restaurer', 'arguments_invalides'),
        @('demarrer.ps1', @('--x'), 'demarrer', 'arguments_invalides'),
        @('arreter.ps1', @('--x'), 'arreter', 'arguments_invalides'),
        @('sauvegarder.ps1', @(), 'sauvegarder', 'arguments_invalides'),
        @('restaurer.ps1', @('--forcer'), 'restaurer', 'arguments_invalides')
    )
    foreach ($t in $tests) {
        $r = lancer_script $t[0] $t[1]
        $attendu = 'ETAPE {0} : ECHEC {1}' -f $t[2], $t[3]
        verifie ($r.Code -eq 1) ('code {0} pour {1}' -f $r.Code, $t[0])
        verifie (@($r.Lignes).Count -eq 1 -and ([string]$r.Lignes[0]).TrimEnd("`r") -eq $attendu) ('{0} {1} : {2}' -f $t[0], ($t[1] -join ' '), ($r.Lignes -join ' / '))
        verifie (-not $r.Erreur.Contains('secret-recopie')) 'argument recopié'
    }
    verifie ((journal_factices).Length -eq 0) 'commande factice appelée'
}

cas 'etapes_interactives_sans_terminal' {
    $dossier = nouvel_environnement 'sans-terminal' @('docker')
    foreach ($etape in @('mistral', 'legifrance', 'sauvegarde-auto')) {
        $r = lancer_script 'installer.ps1' @('--etape', $etape)
        $derniere = ([string]$r.Lignes[-1]).TrimEnd("`r")
        verifie ($r.Code -eq 1 -and $derniere -eq "ETAPE $etape : ECHEC terminal_requis") ('{0} : {1}' -f $etape, $derniere)
    }
    verifie (contenu_vide $script:tmp_maison) 'HOME modifié'
    verifie (-not (Test-Path -LiteralPath $script:tmp_donnees)) 'territoria-donnees créé'
}

cas 'donnees_dans_le_depot_refusees' {
    $null = nouvel_environnement 'donnees-depot' @('docker')
    $env:TERRITORIA_DONNEES_HOTE = '/repo/territoria-donnees'
    $r = lancer_script 'installer.ps1' @('--etape', 'secrets')
    verifie (([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE secrets : ECHEC donnees_dans_le_depot') ($r.Lignes -join ' / ')
    $env:TERRITORIA_DONNEES_HOTE = 'relatif/donnees'
    $r = lancer_script 'installer.ps1' @('--etape', 'secrets')
    verifie (([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE secrets : ECHEC donnees_invalides') ($r.Lignes -join ' / ')
}

# =====================================================================================================================
# Fonctions de commun.ps1 et de installer.ps1
# =====================================================================================================================

$null = nouvel_environnement 'fonctions' @('docker', 'podman', 'lms', 'schtasks', 'icacls', 'winget')
. (Join-Path -Path $depot -ChildPath 'install/installer.ps1')

cas 'citation_des_arguments_windows' {
    $attendus = [ordered]@{
        'simple' = 'simple'; 'avec espace' = '"avec espace"'; 'C:\Mes dossiers\x' = '"C:\Mes dossiers\x"'
        'fin\' = 'fin\'; 'espace fin\' = '"espace fin\\"'; 'a"b' = '"a\"b"'; '' = '""'
    }
    foreach ($entree in $attendus.Keys) {
        $obtenu = citer_windows $entree
        verifie ($obtenu -eq $attendus[$entree]) ('{0} -> {1}, attendu {2}' -f $entree, $obtenu, $attendus[$entree])
    }
    verifie ((ligne_commande @('run', '-v', 'C:\a b:/x:ro', '--name', 'x')) -eq 'run -v "C:\a b:/x:ro" --name x') 'ligne_commande'
    # Aller-retour avec une analyse indépendante (CommandLineToArgvW) : ce que Windows lit est exactement ce qu'on a cité.
    $mots = @('E:\', 'D:\x\', 'D:\Mes sauvegardes\', 'C:\a b\c d', '\\serveur\partage\', 'a"b', '"', 'fin\\', 'x y\\"z', 'simple', 'é ç')
    foreach ($mot in $mots) {
        $lu = argv_windows (ligne_commande @('premier', $mot, 'dernier'))
        verifie ($lu.Count -eq 3 -and $lu[0] -ceq 'premier' -and $lu[1] -ceq $mot -and $lu[2] -ceq 'dernier') ('ligne_commande : {0} lu {1}' -f $mot, ($lu -join '|'))
        $seul = argv_windows (citer_windows $mot $true)
        verifie ($seul.Count -eq 1 -and $seul[0] -ceq $mot) ('citer_windows $true : {0} lu {1}' -f $mot, ($seul -join '|'))
    }
    # L'analyseur de l'essai voit bien le défaut qu'on évite : la citation à la main d'une destination qui finit par « \ ».
    $faux = argv_windows ('"E:\"')
    verifie ($faux.Count -eq 1 -and $faux[0] -ceq 'E:"') ('analyseur : ' + ($faux -join '|'))
    $refus = 0
    foreach ($mauvais in @('', 'a"b', 'espace \')) { try { verifier_arguments @($mauvais) } catch { $refus++ } }
    verifie ($refus -eq 3) 'arguments dangereux acceptés'
}

cas 'ecrire_variable' {
    $d = dossier_neuf 'variables'
    $f = Join-Path -Path $d -ChildPath 'a.env'
    [System.IO.File]::WriteAllText($f, "AUTRE=1`nCLE=ancienne`nCLE=doublon`nFIN=z`n", $utf8)
    ecrire_variable $f 'CLE' 'nouvelle'
    verifie ([System.IO.File]::ReadAllText($f) -ceq "AUTRE=1`nCLE=nouvelle`nFIN=z`n") ('contenu : ' + [System.IO.File]::ReadAllText($f))
    ecrire_variable $f 'AJOUT' 'é ç'
    $octets = octets_de $f
    verifie (-not ($octets[0] -eq 0xEF)) 'BOM'
    verifie (-not [System.IO.File]::ReadAllText($f).Contains("`r")) 'fin de ligne CR'
    verifie ([System.IO.File]::ReadAllText($f).EndsWith("AJOUT=é ç`n")) 'ajout'
    ecrire_variable $f 'PISTE' 'v$x ${y}' 'litteral,secret'
    verifie ([System.IO.File]::ReadAllText($f).Contains("PISTE='v`$x `${y}'`n")) 'littéral'
    verifie ((& stat -c %a $f) -eq '600') 'droits 600'
    verifie (@(Get-ChildItem -LiteralPath $d -Filter '*.tmp.*').Count -eq 0) 'fichier temporaire resté'
    verifie ((variable_renseignee $f 'CLE') -and -not (variable_renseignee $f 'ABSENTE')) 'variable_renseignee'
    $null = echec_attendu 'valeur_invalide' { ecrire_variable $f 'X' "a`nb" }
    $null = echec_attendu 'valeur_invalide' { ecrire_variable $f 'X' "a'b" 'litteral' }
}

cas 'retirer_variable' {
    $d = dossier_neuf 'retrait'
    $f = Join-Path -Path $d -ChildPath 'a.env'
    $absent = Join-Path -Path $d -ChildPath 'absent.env'
    [System.IO.File]::WriteAllText($f, "# commentaire`nTERRITORIA_SERVICE_TOKEN=jeton`nCLE='secret-essai'`nB=2`nCLE=doublon`nCLEF=garde`n", $utf8)
    $r = capturer { retirer_variable $f 'CLE'; retirer_variable $f 'ABSENTE'; retirer_variable $absent 'CLE' }
    verifie ($null -eq $r.Exception) ('échec : ' + $script:CodeEchec + $r.Erreur)
    verifie ([System.IO.File]::ReadAllText($f) -ceq "# commentaire`nTERRITORIA_SERVICE_TOKEN=jeton`nB=2`nCLEF=garde`n") ('contenu : ' + [System.IO.File]::ReadAllText($f))
    verifie ((& stat -c %a $f) -eq '600') 'droits 600'
    verifie (-not (Test-Path -LiteralPath $absent)) 'fichier absent créé'
    verifie (@(Get-ChildItem -LiteralPath $d -Filter '*.tmp.*').Count -eq 0) 'fichier temporaire resté'
    verifie (($r.Sortie + $r.Erreur).Length -eq 0) ('affichage : ' + $r.Sortie + $r.Erreur)
}

cas 'exiger_etape_derniere_ligne' {
    $r = capturer { exiger_etape 'demo' "ligne`nRAPPORT x`nETAPE demo : OK`n" }
    verifie ($null -eq $r.Exception) 'OK refusé'
    $r = capturer { exiger_etape 'demo' "ETAPE demo : OK`nautre chose" }
    verifie ($null -ne $r.Exception -and $script:CodeEchec -eq 'sortie_inattendue') 'sortie inattendue acceptée'
    $null = echec_attendu 'bibliotheque_etrangere' { exiger_etape 'demo' "ETAPE demo : ECHEC bibliotheque_etrangere`n" }
    $null = echec_attendu 'erreur_inattendue' { exiger_etape 'demo' "ETAPE demo : ECHEC Mauvais Code!`n" }
    $null = echec_attendu 'sortie_inattendue' { exiger_etape 'demo' '' }
}

cas 'dossier_lmstudio_resolution' {
    $d = dossier_neuf 'lmstudio-dossier'
    $maison = Join-Path -Path $d -ChildPath 'Frédéric'
    $null = New-Item -ItemType Directory -Path $maison -Force
    $env:USERPROFILE = $maison
    $env:HOME = $maison
    $env:LOCALAPPDATA = Join-Path -Path $maison -ChildPath 'Local'
    verifie ((dossier_lmstudio) -eq '') 'aucun dossier : doit être vide'
    $null = New-Item -ItemType Directory -Path (Join-Path -Path $env:LOCALAPPDATA -ChildPath 'lm-studio') -Force
    verifie ((dossier_lmstudio) -eq (Join-Path -Path $env:LOCALAPPDATA -ChildPath 'lm-studio')) 'repli sur LOCALAPPDATA'
    $null = New-Item -ItemType Directory -Path (Join-Path -Path $maison -ChildPath '.lmstudio') -Force
    verifie ((dossier_lmstudio) -eq (Join-Path -Path $maison -ChildPath '.lmstudio')) '.lmstudio avant LOCALAPPDATA'
    $cible = Join-Path -Path $maison -ChildPath 'Données LM'
    $null = New-Item -ItemType Directory -Path $cible -Force
    $pointeur = Join-Path -Path $maison -ChildPath '.lmstudio-home-pointer'
    [System.IO.File]::WriteAllBytes($pointeur, $utf8.GetBytes($cible + "`r`n"))
    verifie ((dossier_lmstudio) -eq $cible) 'pointeur UTF-8 accentué avec CRLF'
    [System.IO.File]::WriteAllBytes($pointeur, [byte[]](@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes($cible)))
    verifie ((dossier_lmstudio) -eq $cible) 'pointeur avec BOM'
    [System.IO.File]::WriteAllBytes($pointeur, $utf8.GetBytes((Join-Path -Path $maison -ChildPath 'absent')))
    verifie ((dossier_lmstudio) -eq '') 'pointeur vers un dossier absent : jamais deviné'
    $lms = Join-Path -Path $cible -ChildPath 'bin'
    [System.IO.File]::WriteAllBytes($pointeur, $utf8.GetBytes($cible))
    $null = New-Item -ItemType Directory -Path $lms -Force
    $env:PATH = $cheminInitial
    creer_factice $lms 'lms'
    verifie ((chemin_lms) -eq (Join-Path -Path $lms -ChildPath 'lms')) 'chemin_lms depuis le dossier pointé'
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
}

cas 'reglages_du_serveur_lm_studio' {
    $d = dossier_neuf 'reglages'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $script:DossierLms = Join-Path -Path $d -ChildPath 'LM Studio é'
    $fichier = Join-Path -Path $script:DossierLms -ChildPath '.internal/http-server-config.json'
    regler_journalisation_lms '127.0.0.1'
    $neuf = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($fichier))
    verifie ($neuf.networkInterface -eq '127.0.0.1' -and $neuf.fileLoggingMode -eq 'off' -and $neuf.port -eq 1234) 'fichier créé'
    verifie ($neuf.verbose -eq $false -and $neuf.logIncomingTokens -eq $false -and $neuf.logSensitiveData -eq $false) 'journalisation créée'
    [System.IO.File]::WriteAllText($fichier, '{"networkInterface":"0.0.0.0","fileLoggingMode":"full","verbose":true,"logIncomingTokens":true,"logSensitiveData":true,"cors":true,"port":4321,"dossierPerso":"C:\\Utilisateurs\\Frédéric"}', $utf8)
    regler_journalisation_lms '127.0.0.1'
    $octets = octets_de $fichier
    verifie (-not ($octets[0] -eq 0xEF)) 'BOM dans le JSON'
    $a = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($fichier))
    verifie ($a.networkInterface -eq '127.0.0.1' -and $a.fileLoggingMode -eq 'off') 'écoute et fichier de journal'
    verifie ($a.verbose -eq $false -and $a.logIncomingTokens -eq $false -and $a.logSensitiveData -eq $false) 'journalisation'
    verifie ($a.cors -eq $true -and $a.port -eq 4321 -and $a.dossierPerso -eq 'C:\Utilisateurs\Frédéric') 'autres clés conservées'
    [System.IO.File]::WriteAllText($fichier, '{ pas du json', $utf8)
    $script:CodeEchec = ''
    $r = capturer { regler_journalisation_lms '127.0.0.1' }
    verifie ($null -ne $r.Exception -and $script:CodeEchec -eq 'journalisation_non_reglee') 'JSON invalide accepté'
    verifie ([System.IO.File]::ReadAllText($fichier) -ceq '{ pas du json') 'fichier invalide réécrit'
}

cas 'ecoute_de_lm_studio' {
    fixer_donnees (Join-Path -Path (dossier_neuf 'ecoute') -ChildPath 'donnees')
    function Get-NetTCPConnection {
        [CmdletBinding()]
        param([int]$LocalPort, [string]$State)
        foreach ($adresse in $script:fausse_ecoute) { [pscustomobject]@{ LocalAddress = $adresse; LocalPort = $LocalPort; State = $State } }
    }
    foreach ($ok in @(@('127.0.0.1'), @('127.0.0.1', '::1'), @('::1'))) {
        $script:fausse_ecoute = $ok
        $r = capturer { verifier_ecoute_lms '127.0.0.1' }
        verifie ($null -eq $r.Exception) ('refusé : ' + ($ok -join ','))
    }
    foreach ($mauvais in @(@('0.0.0.0'), @('::'), @('192.168.0.10'), @('127.0.0.1', '192.168.0.10'), @('*'))) {
        $script:fausse_ecoute = $mauvais
        $r = echec_attendu 'lmstudio_expose_reseau' { verifier_ecoute_lms '127.0.0.1' }
        verifie ($r.Erreur.Contains('Serve on Local Network')) 'message sans consigne'
    }
    $script:fausse_ecoute = @()
    $null = echec_attendu 'lmstudio_injoignable' { verifier_ecoute_lms '127.0.0.1' }
}

cas 'requete_marquee_et_journaux' {
    $d = dossier_neuf 'journaux'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $script:DossierLms = Join-Path -Path $d -ChildPath 'lm'
    $null = New-Item -ItemType Directory -Path $script:DossierLms -Force
    function http_post_json {
        param([string]$url, [string]$corps, [int]$delai = 600)
        $script:dernier_corps = $corps
        $marqueur = [regex]::Match($corps, 'marqueur-territoria-\d+-\d+').Value
        if ($script:journaliser -ne '') {
            $dossier = Join-Path -Path $script:DossierLms -ChildPath 'server-logs'
            $null = New-Item -ItemType Directory -Path $dossier -Force
            if ($script:journaliser -eq 'ascii') { [System.IO.File]::WriteAllText((Join-Path -Path $dossier -ChildPath 'a.log'), "reçu $marqueur ok") }
            if ($script:journaliser -eq 'utf16') { [System.IO.File]::WriteAllText((Join-Path -Path $dossier -ChildPath 'b.log'), "reçu $marqueur ok", [System.Text.Encoding]::Unicode) }
            if ($script:journaliser -eq 'propre') { [System.IO.File]::WriteAllText((Join-Path -Path $dossier -ChildPath 'c.log'), 'rien à voir') }
        }
    }
    function Start-Sleep { param($Seconds) }
    $script:journaliser = ''
    $null = echec_attendu 'journaux_lmstudio_introuvables' { verifier_journaux_lms 'http://127.0.0.1:1234/v1' 'google/gemma-4-e4b' }
    verifie ($script:dernier_corps.Contains('"model":"google/gemma-4-e4b"') -and $script:dernier_corps.Contains('Répondez seulement OK. marqueur-territoria-')) 'corps de la requête'
    foreach ($mode in @('ascii', 'utf16')) {
        $script:journaliser = $mode
        Remove-Item -LiteralPath (Join-Path -Path $script:DossierLms -ChildPath 'server-logs') -Recurse -Force -ErrorAction SilentlyContinue
        $r = echec_attendu 'journalisation_active' { verifier_journaux_lms 'http://127.0.0.1:1234/v1' 'google/gemma-4-e4b' }
        verifie ($r.Erreur.Contains('contenant la requête de test : 1')) ('compte ' + $mode)
        verifie (-not $r.Erreur.Contains('reçu')) 'contenu du journal affiché'
    }
    $script:journaliser = 'propre'
    Remove-Item -LiteralPath (Join-Path -Path $script:DossierLms -ChildPath 'server-logs') -Recurse -Force -ErrorAction SilentlyContinue
    $r = capturer { verifier_journaux_lms 'http://127.0.0.1:1234/v1' 'google/gemma-4-e4b' }
    verifie ($null -eq $r.Exception -and $r.Erreur.Contains('contenant la requête de test : 0')) 'journal propre refusé'
}

cas 'modele_servi' {
    fixer_donnees (Join-Path -Path (dossier_neuf 'modele') -ChildPath 'donnees')
    function http_get { param([string]$url, [int]$delai = 30) return $script:reponse_modeles }
    $script:reponse_modeles = '{"data":[{"id":"google/gemma-4-e4b"}]}'
    $r = capturer { verifier_modele_servi 'http://127.0.0.1:1234/v1' 'google/gemma-4-e4b' }
    verifie ($null -eq $r.Exception) 'modèle présent refusé'
    $script:reponse_modeles = '{"data":[{"id":"autre/modele"}]}'
    $null = echec_attendu 'modele_introuvable' { verifier_modele_servi 'http://127.0.0.1:1234/v1' 'google/gemma-4-e4b' }
}

cas 'executer_octets_delai_et_code' {
    $octets = $utf8.GetBytes("Mot-de-passé-é\n-ç`n")
    $r = executer 'cat' @() $octets
    verifie ($r.Code -eq 0 -and $r.Sortie -ceq "Mot-de-passé-é\n-ç`n") ('écho exact : ' + $r.Sortie)
    $debut = [DateTime]::UtcNow
    $r = executer 'sleep' @('20') $null 1
    verifie ($r.Code -eq -1) ('code du délai ' + $r.Code)
    verifie (([DateTime]::UtcNow - $debut).TotalSeconds -lt 10) 'délai non respecté'
    $r = executer 'sh' @('-c', 'exit 3')
    verifie ($r.Code -eq 3) 'code de sortie'
    verifie ((avec_delai 5 'true' @()) -and -not (avec_delai 5 'false' @())) 'avec_delai'
    $r = executer 'sh' @('-c', 'echo "visible"; echo bruit >&2') $null 5 $true
    verifie ($r.Sortie.Trim() -eq 'visible') 'erreur standard écartée'
}

cas 'lancer_detache_et_lancer' {
    $debut = [DateTime]::UtcNow
    $r = capturer { lancer_detache sh -c 'sleep 25 & echo démarré; echo avertissement >&2' }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Exception)
    verifie (([DateTime]::UtcNow - $debut).TotalSeconds -lt 15) 'lancer_detache attend le démon'
    verifie ($r.Erreur.Contains('démarré') -and $r.Erreur.Contains('avertissement')) 'sortie recopiée sur stderr'
    $r = capturer { lancer sh -c 'echo normal; echo erreur >&2; exit 0' }
    verifie ($null -eq $r.Exception -and $r.Sortie -eq '' -and $r.Erreur.Contains('normal') -and $r.Erreur.Contains('erreur')) 'lancer : sortie sur stderr'
    $r = capturer { lancer sh -c 'exit 4' }
    verifie ($null -ne $r.Exception -and $r.Exception.Exception.Message.Contains('code 4')) 'échec de commande'
    $r = capturer { lancer_tableau 'sh' @('-c', 'exit 5') @(0, 5) }
    verifie ($null -eq $r.Exception) 'code accepté refusé'
}

cas 'lancer_detache_entree_standard' {
    # Hors Windows (ces essais), l'enfant garde l'entrée standard de l'appelant : Start-Process y créerait un tube qu'il
    # remplit après le démarrage, d'où « Broken pipe » quand le programme s'est déjà terminé.
    $fichier = Join-Path -Path (dossier_neuf 'detache') -ChildPath 'entree.txt'
    $r = capturer { lancer_detache sh -c 'readlink /proc/self/fd/0 >$1' sh $fichier }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Exception)
    $appelant = [string](& readlink ('/proc/{0}/fd/0' -f $PID))
    $enfant = [System.IO.File]::ReadAllText($fichier).Trim()
    verifie ($enfant -ceq $appelant) ('entrée de l''enfant : {0} ; celle de l''appelant : {1}' -f $enfant, $appelant)
    # Sous Windows, le fichier vide reste passé à Start-Process (sa poignée va à CreateProcess, sans tube) pour détacher
    # le démon de la console. Windows PowerShell 5.1 n'est pas testable ici : on vérifie les paramètres transmis.
    function Start-Process {
        param([string]$FilePath, [switch]$PassThru, [switch]$NoNewWindow, [string]$RedirectStandardInput,
            [string]$RedirectStandardOutput, [string]$RedirectStandardError, [string]$ArgumentList)
        $script:parametres_demarrage = @($PSBoundParameters.Keys)
        $script:entree_demarrage = ($RedirectStandardInput -ne '' -and [System.IO.File]::Exists($RedirectStandardInput))
        return [System.Diagnostics.Process]::Start('true')
    }
    $estWindows = $script:EstWindows
    try {
        foreach ($windows in @($true, $false)) {
            $script:EstWindows = $windows
            $script:parametres_demarrage = @()
            $script:entree_demarrage = $false
            $r = capturer { lancer_detache true }
            verifie ($null -eq $r.Exception) ('échec, Windows : {0} : {1}' -f $windows, $r.Exception)
            verifie (($script:parametres_demarrage -contains 'RedirectStandardOutput') -and ($script:parametres_demarrage -contains 'RedirectStandardError')) 'sorties non redirigées'
            verifie (($script:parametres_demarrage -contains 'RedirectStandardInput') -eq $windows) ('RedirectStandardInput, Windows : ' + $windows)
            verifie ($script:entree_demarrage -eq $windows) ('fichier d''entrée, Windows : ' + $windows)
        }
    } finally {
        $script:EstWindows = $estWindows
    }
}

cas 'sauvegarde_mot_de_passe_par_entree_standard' {
    $d = dossier_neuf 'sauvegarde'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees x')
    $null = New-Item -ItemType Directory -Path (Join-Path -Path $script:Donnees -ChildPath 'territoria'), (Join-Path -Path $script:Donnees -ChildPath 'secrets') -Force
    $destination = Join-Path -Path $d -ChildPath 'destination é'
    $null = New-Item -ItemType Directory -Path $destination -Force
    $env:JOURNAL_FACTICES = Join-Path -Path $d -ChildPath 'appels.log'
    $env:STDIN_FACTICE = Join-Path -Path $d -ChildPath 'entree.bin'
    $corps = @'
case "$1" in
  info) exit 0 ;;
  compose) case "$*" in *" images -q "*) echo sha256:imagefictive ;; esac ;;
  run) case "$*" in *elaguer*) cat >/dev/null; echo "ETAPE elaguer : OK" ;; *) cat >"$STDIN_FACTICE"; echo "ETAPE sauvegarder : OK" ;; esac ;;
esac
'@
    $factices = Join-Path -Path $d -ChildPath 'factices'
    creer_factice $factices 'docker' $corps
    $env:PATH = $factices + ':' + $cheminInitial
    function terminal_disponible { return $true }
    $script:saisies = New-Object System.Collections.Queue
    function lire_secret { param([string]$invite) return (securiser ([string]$script:saisies.Dequeue())) }
    $motdepasse = 'Mot-de-passé-é-ç-12345'
    $script:saisies.Enqueue($motdepasse); $script:saisies.Enqueue($motdepasse)
    $r = capturer { sauvegarde_creer $destination '' }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Exception + ' ' + $script:CodeEchec + ' ' + $r.Erreur)
    $recu = octets_de $env:STDIN_FACTICE
    $attendu = $utf8.GetBytes($motdepasse + "`n")
    verifie ([System.Convert]::ToBase64String($recu) -eq [System.Convert]::ToBase64String($attendu)) ('octets reçus : ' + [System.BitConverter]::ToString($recu))
    $appels = journal_factices
    verifie (-not $appels.Contains('Mot-de-pass')) 'mot de passe dans les arguments'
    verifie (-not ($r.Sortie + $r.Erreur).Contains('Mot-de-pass')) 'mot de passe affiché'
    verifie ($appels.Contains('run --rm -i --network none --user 0:0 --read-only --tmpfs /tmp:mode=1777')) 'conteneur ponctuel'
    verifie ($appels.Contains('sha256:imagefictive python -m outils sauvegarder')) 'image et commande'
    verifie ($appels.Contains($script:Donnees + ':/source:ro') -and $appels.Contains($destination + ':/destination')) 'montages'
    # saisies différentes, vide
    $script:saisies.Enqueue('un-mot-de-passe-long'); $script:saisies.Enqueue('un-autre-mot-de-passe')
    $null = echec_attendu 'saisies_differentes' { sauvegarde_creer $destination '' }
    $script:saisies.Enqueue(''); $script:saisies.Enqueue('')
    $null = echec_attendu 'mot_de_passe_absent' { sauvegarde_creer $destination '' }
    # destination absente : jamais créée
    $null = echec_attendu 'destination_absente' { sauvegarde_creer (Join-Path -Path $d -ChildPath 'disque-debranche') '' }
    verifie (-not (Test-Path -LiteralPath (Join-Path -Path $d -ChildPath 'disque-debranche'))) 'destination créée'
    # mode automatique : fichier, puis élagage
    $null = echec_attendu 'mot_de_passe_absent' { sauvegarde_creer $destination 'auto' }
    [System.IO.File]::WriteAllBytes($script:FichierMotDePasse, $utf8.GetBytes("Fichier-é-12345`n"))
    Remove-Item -LiteralPath $env:JOURNAL_FACTICES -Force
    $r = capturer { sauvegarde_creer $destination 'auto'; sauvegarde_elaguer $destination }
    verifie ($null -eq $r.Exception) ('auto : ' + $script:CodeEchec + $r.Erreur)
    $appels = journal_factices
    verifie ($appels.Contains('--depuis-fichier /source/secrets/sauvegarde.motdepasse')) 'mot de passe par fichier monté'
    verifie ($appels.Contains('python -m outils elaguer')) 'élagage'
    verifie (-not $appels.Contains('Fichier-é')) 'mot de passe du fichier dans les arguments'
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
}

cas 'restauration' {
    $d = dossier_neuf 'restauration'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $null = New-Item -ItemType Directory -Path (Join-Path -Path $script:Donnees -ChildPath 'secrets') -Force
    [System.IO.File]::WriteAllText((Join-Path -Path $script:Secrets -ChildPath 'compose.env'), "A=1`n")
    $archive = Join-Path -Path $d -ChildPath 'territoria-20261030-123000.tsv1'
    [System.IO.File]::WriteAllText($archive, 'x')
    $env:JOURNAL_FACTICES = Join-Path -Path $d -ChildPath 'appels.log'
    $env:STDIN_FACTICE = Join-Path -Path $d -ChildPath 'entree.bin'
    $factices = Join-Path -Path $d -ChildPath 'factices'
    creer_factice $factices 'docker' @'
case "$1" in
  info) exit 0 ;;
  compose) case "$*" in *" images -q "*) echo sha256:imagefictive ;; esac ;;
  run) cat >"$STDIN_FACTICE"; echo "ETAPE restaurer : OK" ;;
esac
'@
    $env:PATH = $factices + ':' + $cheminInitial
    $null = echec_attendu 'cible_non_vide' { sauvegarde_restaurer $archive '' 'auto' }
    $null = echec_attendu 'mot_de_passe_absent' { sauvegarde_restaurer $archive 'forcer' 'auto' }
    $contenu = $utf8.GetBytes("Restauration-é-12345`n")
    [System.IO.File]::WriteAllBytes($script:FichierMotDePasse, $contenu)
    $r = capturer { sauvegarde_restaurer $archive 'forcer' 'auto' }
    verifie ($null -eq $r.Exception) ('restauration : ' + $script:CodeEchec + $r.Erreur)
    verifie ([System.Convert]::ToBase64String((octets_de $env:STDIN_FACTICE)) -eq [System.Convert]::ToBase64String($contenu)) 'entrée standard du conteneur'
    $appels = journal_factices
    verifie ($appels.Contains(' stop')) 'arrêt des services'
    verifie ($appels.Contains('--archive /destination/territoria-20261030-123000.tsv1 --forcer')) 'archive et --forcer'
    verifie ($appels.IndexOf(' stop') -lt $appels.IndexOf('python -m outils restaurer')) 'arrêt avant restauration'
    verifie (-not $appels.Contains('Restauration-é')) 'mot de passe en argument'
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
}

cas 'mistral_saisie_masquee' {
    $d = dossier_neuf 'mistral'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    function terminal_disponible { return $true }
    $script:reponses = New-Object System.Collections.Queue
    $script:saisies = New-Object System.Collections.Queue
    function lire_reponse { param([string]$invite) $script:invites += $invite; return [string]$script:reponses.Dequeue() }
    function lire_secret { param([string]$invite) return (securiser ([string]$script:saisies.Dequeue())) }
    # refus par défaut : rien n'est écrit
    $script:invites = @()
    $script:reponses.Enqueue('')
    $r = capturer { etape_mistral }
    verifie ($null -eq $r.Exception) ('refus : ' + $script:CodeEchec + $r.Erreur)
    verifie (($script:invites -join '|').Contains("Utiliser l'API de Mistral au lieu de l'IA locale ? [o/N]")) ('question : ' + ($script:invites -join '|'))
    verifie ($r.Erreur.Contains('registre') -and $r.Erreur.Contains('--etape lmstudio')) 'avertissement ou rappel absent'
    verifie (-not (Test-Path -LiteralPath $script:Secrets)) 'secrets créés après un refus'
    # accepté : clé masquée, fichiers écrits
    $script:reponses.Enqueue('o'); $script:saisies.Enqueue('k-essai-mistral')
    $r = capturer { etape_mistral }
    verifie ($null -eq $r.Exception) ('échec : ' + $script:CodeEchec + $r.Erreur)
    verifie ([System.IO.File]::ReadAllText((Join-Path -Path $script:Secrets -ChildPath 'territoria.env')) -ceq "TERRITORIA_MISTRAL_CLE='k-essai-mistral'`n") 'territoria.env'
    verifie ([System.IO.File]::ReadAllText((Join-Path -Path $script:Secrets -ChildPath 'compose.env')) -ceq "TERRITORIA_IA_EN_LIGNE=mistral`n") 'compose.env'
    verifie ((& stat -c %a (Join-Path -Path $script:Secrets -ChildPath 'territoria.env')) -eq '600') 'droits du fichier'
    verifie (-not ($r.Sortie + $r.Erreur).Contains('k-essai-mistral')) 'clé affichée'
    # relance : conservé, aucune question
    function lire_reponse { param([string]$invite) throw 'question inattendue' }
    $r = capturer { etape_mistral }
    verifie ($null -eq $r.Exception -and $r.Erreur.Contains('conservées')) 'option à conserver'
    # clé vide : saisie_vide, rien d'écrit
    $d2 = dossier_neuf 'mistral-vide'
    fixer_donnees (Join-Path -Path $d2 -ChildPath 'donnees')
    function lire_reponse { param([string]$invite) return [string]$script:reponses.Dequeue() }
    $script:reponses.Enqueue('o'); $script:saisies.Enqueue('')
    $r = capturer { etape_mistral }
    verifie ($script:CodeEchec -eq 'saisie_vide') ('code : ' + $script:CodeEchec)
    verifie (-not (Test-Path -LiteralPath (Join-Path -Path $script:Secrets -ChildPath 'territoria.env'))) 'clé vide écrite'
}

cas 'lmstudio_retire_mistral' {
    $d = dossier_neuf 'retirer-mistral'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $null = New-Item -ItemType Directory -Path $script:Secrets -Force
    [System.IO.File]::WriteAllText((Join-Path -Path $script:Secrets -ChildPath 'compose.env'), "TERRITORIA_HORS_LIGNE=1`nTERRITORIA_IA_EN_LIGNE=mistral`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $script:Secrets -ChildPath 'territoria.env'), "PISTE_CLIENT_ID='x'`nTERRITORIA_MISTRAL_CLE='k-essai-mistral'`n")
    $r = capturer { retirer_mistral }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Erreur)
    verifie ([System.IO.File]::ReadAllText((Join-Path -Path $script:Secrets -ChildPath 'compose.env')) -ceq "TERRITORIA_HORS_LIGNE=1`nTERRITORIA_IA_EN_LIGNE=`n") 'compose.env'
    verifie ([System.IO.File]::ReadAllText((Join-Path -Path $script:Secrets -ChildPath 'territoria.env')) -ceq "PISTE_CLIENT_ID='x'`nTERRITORIA_MISTRAL_CLE=''`n") 'territoria.env'
}

cas 'mistral_lm_studio_non_demarre' {
    # Relecture finale (I2) : un poste passé à Mistral ne relance ni LM Studio ni son modèle.
    $d = dossier_neuf 'mistral-demarrer'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $null = New-Item -ItemType Directory -Path $script:Secrets -Force
    [System.IO.File]::WriteAllText((Join-Path -Path $script:Secrets -ChildPath 'compose.env'), "TERRITORIA_MODELE=google/gemma-4-12b`nTERRITORIA_IA_EN_LIGNE=mistral`n")
    $script:lmsCherche = $false
    function chemin_lms { $script:lmsCherche = $true; return 'C:\absent\lms.exe' }
    $r = capturer { demarrer_lmstudio }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Erreur)
    verifie (-not $script:lmsCherche) 'LM Studio cherché'
    verifie $r.Erreur.Contains('API de Mistral choisie') 'message'
}

cas 'legifrance_connecteurs_publics' {
    $d = dossier_neuf 'legifrance'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    function terminal_disponible { return $true }
    $script:reponses = New-Object System.Collections.Queue
    $script:invites = New-Object System.Collections.Generic.List[string]
    function lire_reponse { param([string]$invite) $script:invites.Add($invite); return [string]$script:reponses.Dequeue() }
    function lire_secret { param([string]$invite) throw 'saisie masquée inattendue' }
    $composeEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    $territoriaEnv = Join-Path -Path $script:Secrets -ChildPath 'territoria.env'
    # oui : connecteurs autorisés, aucun identifiant demandé (ils se saisissent dans Administration › Connecteurs)
    $script:reponses.Enqueue('o')
    $r = capturer { etape_legifrance }
    verifie ($null -eq $r.Exception) ('échec : ' + $script:CodeEchec + $r.Erreur + $r.Exception)
    verifie ($script:invites.Count -eq 1 -and $script:invites[0].Contains('Autoriser les connecteurs publics (Légifrance, data.gouv.fr)')) ('question : ' + ($script:invites -join ' | '))
    verifie ([System.IO.File]::ReadAllText($composeEnv) -ceq "TERRITORIA_HORS_LIGNE=0`n") 'compose.env'
    verifie (-not (Test-Path -LiteralPath $territoriaEnv)) 'territoria.env créé'
    verifie ($r.Erreur.Contains('Administration › Connecteurs')) ('renvoi vers l''administration : ' + $r.Erreur)
    verifie ((& stat -c %a $script:Secrets) -eq '700') 'droits du dossier secrets'
    # relance : la question revient ; réponse vide : mode hors ligne
    $script:reponses.Enqueue('')
    $r = capturer { etape_legifrance }
    verifie ($null -eq $r.Exception -and $script:invites.Count -eq 2) 'question non reposée'
    verifie ([System.IO.File]::ReadAllText($composeEnv) -ceq "TERRITORIA_HORS_LIGNE=1`n") 'hors ligne'
    # ancienne installation : identifiants PISTE retirés de territoria.env sans être affichés ; le jeton reste
    [System.IO.File]::WriteAllText($territoriaEnv, "TERRITORIA_SERVICE_TOKEN=jeton-essai`nPISTE_CLIENT_ID='identifiant-PISTE-abc'`nPISTE_CLIENT_SECRET='secret-PISTE-xyz'`n", $utf8)
    $script:reponses.Enqueue('oui')
    $r = capturer { etape_legifrance }
    verifie ($null -eq $r.Exception) ('migration : ' + $script:CodeEchec + $r.Erreur)
    verifie ([System.IO.File]::ReadAllText($territoriaEnv) -ceq "TERRITORIA_SERVICE_TOKEN=jeton-essai`n") ('territoria.env : ' + [System.IO.File]::ReadAllText($territoriaEnv))
    verifie ((& stat -c %a $territoriaEnv) -eq '600') 'droits du fichier'
    verifie ($r.Erreur.Contains('Identifiants PISTE retirés')) ('migration annoncée : ' + $r.Erreur)
    $affiche = $r.Sortie + $r.Erreur
    verifie (-not $affiche.Contains('identifiant-PISTE') -and -not $affiche.Contains('secret-PISTE') -and -not $affiche.Contains('jeton-essai')) 'secret affiché'
    verifie ([System.IO.File]::ReadAllText($composeEnv) -ceq "TERRITORIA_HORS_LIGNE=0`n") 'en ligne après la migration'
}

cas 'sauvegarde_auto_mot_de_passe_et_tache' {
    $d = dossier_neuf 'sauvegarde-auto'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $null = New-Item -ItemType Directory -Path $script:Secrets -Force
    $destination = Join-Path -Path $d -ChildPath 'externe'
    $null = New-Item -ItemType Directory -Path $destination -Force
    $env:JOURNAL_FACTICES = Join-Path -Path $d -ChildPath 'appels.log'
    $env:XML_COPIE = Join-Path -Path $d -ChildPath 'tache.xml'
    $factices = Join-Path -Path $d -ChildPath 'factices'
    creer_factice $factices 'schtasks' 'cp "$5" "$XML_COPIE"'
    $env:PATH = $factices + ':' + $cheminInitial
    function terminal_disponible { return $true }
    function donnees_planifiables { return $script:planifiables }
    $script:planifiables = $true
    $script:saisies = New-Object System.Collections.Queue
    $script:reponses = New-Object System.Collections.Queue
    function lire_reponse { param([string]$invite) return [string]$script:reponses.Dequeue() }
    function lire_secret { param([string]$invite) return (securiser ([string]$script:saisies.Dequeue())) }
    # refus : trop court, saisies différentes, destination absente, données non standard
    $script:reponses.Enqueue($destination); $script:saisies.Enqueue('court'); $script:saisies.Enqueue('court')
    $null = echec_attendu 'mot_de_passe_trop_court' { etape_sauvegarde_auto }
    $script:reponses.Enqueue($destination); $script:saisies.Enqueue('un-mot-de-passe-long'); $script:saisies.Enqueue('un-mot-de-passe-LONG')
    $null = echec_attendu 'saisies_differentes' { etape_sauvegarde_auto }
    $script:reponses.Enqueue((Join-Path -Path $d -ChildPath 'absent'))
    $null = echec_attendu 'destination_absente' { etape_sauvegarde_auto }
    $script:planifiables = $false
    $null = echec_attendu 'donnees_non_standard' { etape_sauvegarde_auto }
    $script:planifiables = $true
    verifie (-not (Test-Path -LiteralPath $script:FichierMotDePasse)) 'mot de passe écrit malgré les refus'
    verifie (-not (Test-Path -LiteralPath $env:XML_COPIE)) 'tâche créée malgré les refus'
    # accepté
    $motdepasse = 'Sauvegarde-é-çà-2026'
    $script:reponses.Enqueue($destination); $script:saisies.Enqueue($motdepasse); $script:saisies.Enqueue($motdepasse)
    $r = capturer { etape_sauvegarde_auto }
    verifie ($null -eq $r.Exception) ('échec : ' + $script:CodeEchec + $r.Erreur)
    verifie ([System.Convert]::ToBase64String((octets_de $script:FichierMotDePasse)) -eq [System.Convert]::ToBase64String($utf8.GetBytes($motdepasse + "`n"))) 'contenu du fichier de mot de passe'
    verifie ((& stat -c %a $script:FichierMotDePasse) -eq '600') 'droits du fichier de mot de passe'
    verifie (-not ($r.Sortie + $r.Erreur).Contains('Sauvegarde-é')) 'mot de passe affiché'
    verifie ((journal_factices).Contains('schtasks /Create /TN Territoria-Sauvegarde /XML')) 'schtasks'
    verifie (-not (journal_factices).Contains('/TR') -and -not (journal_factices).Contains('Sauvegarde-é')) 'schtasks : arguments'
    $xml = octets_de $env:XML_COPIE
    verifie ($xml[0] -eq 0xFF -and $xml[1] -eq 0xFE) 'XML en UTF-16 avec BOM'
    $texte = [System.Text.Encoding]::Unicode.GetString($xml, 2, $xml.Length - 2)
    verifie ($texte.Contains('T12:30:00') -and $texte.Contains('<DaysInterval>1</DaysInterval>')) 'tous les jours à 12 h 30'
    verifie ($texte.Contains('sauvegarder.ps1&quot; --auto --destination &quot;' + $destination + '&quot;')) 'commande de la tâche'
    $xmlDoc = New-Object System.Xml.XmlDocument
    $xmlDoc.LoadXml($texte.TrimStart([char]0xFEFF))
    verifie ($xmlDoc.Task.Actions.Exec.Command -eq 'powershell.exe') 'XML invalide'
    # La ligne de commande de la tâche, lue comme Windows la lit, redonne exactement la destination : y compris quand elle
    # finit par une barre inverse (« E:\ », « D:\x\ »), que le guillemet fermant avalerait si la ligne était cotée à la main.
    $attendu_script = joindre $script:Racine @('install', 'sauvegarder.ps1')
    foreach ($cible in @('E:\', 'D:\x\', 'D:\Mes sauvegardes\', 'D:\a b', '\\serveur\partage\')) {
        $doc = New-Object System.Xml.XmlDocument
        $doc.LoadXml((xml_tache $cible))
        $lu = argv_windows ([string]$doc.Task.Actions.Exec.Arguments)
        $voulu = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $attendu_script, '--auto', '--destination', $cible)
        verifie ($lu.Count -eq $voulu.Count) ('arguments de la tâche pour {0} : {1}' -f $cible, ($lu -join '|'))
        for ($k = 0; $k -lt $voulu.Count; $k++) { verifie ($lu[$k] -ceq $voulu[$k]) ('argument {0} pour {1} : lu {2}, attendu {3}' -f $k, $cible, $lu[$k], $voulu[$k]) }
    }
    # même chose de bout en bout : un dossier dont le nom finit par une barre inverse (permis sous Linux), tâche créée par schtasks
    $barre = Join-Path -Path $d -ChildPath 'externe-barre\'
    $null = New-Item -ItemType Directory -Path $barre -Force
    Remove-Item -LiteralPath $env:XML_COPIE -Force
    $script:reponses.Enqueue($barre)
    $r = capturer { etape_sauvegarde_auto }
    verifie ($null -eq $r.Exception) ('destination finissant par une barre inverse : ' + $script:CodeEchec + $r.Erreur)
    $xml2 = [System.Text.Encoding]::Unicode.GetString((octets_de $env:XML_COPIE)).TrimStart([char]0xFEFF)
    $doc = New-Object System.Xml.XmlDocument
    $doc.LoadXml($xml2)
    $lu = argv_windows ([string]$doc.Task.Actions.Exec.Arguments)
    verifie ($lu[-1] -ceq $barre -and $lu[-2] -ceq '--destination') ('destination lue : ' + $lu[-1] + ' attendue ' + $barre)
    # relance : mot de passe conservé
    $avant = octets_de $script:FichierMotDePasse
    $script:reponses.Enqueue($destination)
    $r = capturer { etape_sauvegarde_auto }
    verifie ($null -eq $r.Exception -and $r.Erreur.Contains('conservé')) 'relance'
    verifie ([System.Convert]::ToBase64String((octets_de $script:FichierMotDePasse)) -eq [System.Convert]::ToBase64String($avant)) 'mot de passe réécrit'
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
}

cas 'raccourcis' {
    $d = dossier_neuf 'raccourcis'
    $script:tmp_maison = Join-Path -Path $d -ChildPath 'maison'
    $env:HOME = $script:tmp_maison
    $env:USERPROFILE = $script:tmp_maison
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $script:crees = New-Object System.Collections.ArrayList
    function creer_raccourci { param([string]$chemin, [string]$arguments, [int]$fenetre) [void]$script:crees.Add(@($chemin, $arguments, $fenetre)) }
    $script:Auto = $false
    $r = capturer { etape_raccourci }
    verifie ($null -eq $r.Exception) ('échec ' + $r.Exception)
    verifie ($script:crees.Count -eq 1) 'un seul raccourci sans --auto'
    verifie ($script:crees[0][0] -eq (Join-Path -Path $script:tmp_maison -ChildPath 'Desktop/Territoria.lnk')) ('chemin : ' + $script:crees[0][0])
    verifie ($script:crees[0][1] -ceq '-NoProfile -ExecutionPolicy Bypass -File "/repo/install/demarrer.ps1" --ouvrir') ('arguments : ' + $script:crees[0][1])
    verifie ($script:crees[0][2] -eq 1) 'fenêtre normale'
    $script:crees.Clear()
    $script:Auto = $true
    $r = capturer { etape_raccourci }
    verifie ($script:crees.Count -eq 2) 'deux raccourcis avec --auto'
    verifie ($script:crees[1][0].EndsWith('Territoria demarrage.lnk') -and -not $script:crees[1][1].Contains('--ouvrir') -and $script:crees[1][2] -eq 7) 'démarrage de session'
    # dépôt dans un dossier dont le nom a une espace : l'argument -File, lu comme Windows le lit, redonne le chemin
    $racine_initiale = $script:Racine
    try {
        $script:Racine = '/tmp/x y/depot'
        $script:Auto = $true
        $script:crees.Clear()
        $r = capturer { etape_raccourci }
        verifie ($null -eq $r.Exception -and $script:crees.Count -eq 2) 'raccourcis'
        foreach ($k in 0, 1) {
            $lu = argv_windows ([string]$script:crees[$k][1])
            $voulu = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '/tmp/x y/depot/install/demarrer.ps1')
            if ($k -eq 0) { $voulu += '--ouvrir' }
            verifie (($lu -join '|') -ceq ($voulu -join '|')) ('arguments du raccourci {0} : {1}' -f $k, ($lu -join '|'))
        }
    } finally {
        $script:Racine = $racine_initiale
    }
    $script:Auto = $false
}

cas 'lmstudio_demarrer_refuse_une_ecoute_sur_le_reseau' {
    $d = dossier_neuf 'demarrage-lms'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $factices = Join-Path -Path $d -ChildPath 'factices'
    creer_factice $factices 'lms' 'case "$1 $2" in "server status") echo "{\"running\":true}" ;; esac'
    $env:JOURNAL_FACTICES = Join-Path -Path $d -ChildPath 'appels.log'
    $env:PATH = $factices + ':' + $cheminInitial
    function Get-NetTCPConnection {
        [CmdletBinding()]
        param([int]$LocalPort, [string]$State)
        [pscustomobject]@{ LocalAddress = '0.0.0.0'; LocalPort = $LocalPort; State = $State }
    }
    $r = echec_attendu 'lmstudio_expose_reseau' { demarrer_lmstudio }
    verifie (-not (journal_factices).Contains('server start')) 'serveur relancé alors qu''il tournait déjà'
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
}

cas 'chargement_du_modele_apres_le_demarrage' {
    $d = dossier_neuf 'chargement-modele'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $null = New-Item -ItemType Directory -Path $script:Secrets -Force
    $composeEnv = Join-Path -Path $script:Secrets -ChildPath 'compose.env'
    $factices = Join-Path -Path $d -ChildPath 'factices'
    creer_factice $factices 'lms' 'case "$1" in server) [ "$2" = status ] && echo "{\"running\":true}" ;; ps) printf "%s\n" "$MODELES_CHARGES" ;; load) exit "${CODE_CHARGEMENT:-0}" ;; esac'
    $env:JOURNAL_FACTICES = Join-Path -Path $d -ChildPath 'appels.log'
    $env:PATH = $factices + ':' + $cheminInitial
    function Get-NetTCPConnection {
        [CmdletBinding()]
        param([int]$LocalPort, [string]$State)
        [pscustomobject]@{ LocalAddress = '127.0.0.1'; LocalPort = $LocalPort; State = $State }
    }
    verifie ((modele_de_la_cle 'google/gemma-4-12b') -eq 'google/gemma-4-12b@q4_k_m') 'identifiant du palier moyen'
    verifie ((modele_de_la_cle 'autre-modele') -eq 'autre-modele') 'modèle hors de modeles.json'
    # Modèle absent : chargé après le démarrage, sous la clé de compose.env.
    [System.IO.File]::WriteAllText($composeEnv, "TERRITORIA_MODELE=google/gemma-4-12b`n", $utf8)
    $env:MODELES_CHARGES = '[]'
    $r = capturer { demarrer_lmstudio }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Exception)
    verifie ((journal_factices).Contains('lms load google/gemma-4-12b@q4_k_m --identifier google/gemma-4-12b --yes')) ('chargement : ' + (journal_factices))
    # Déjà chargé (JSON indenté) : rien n'est rechargé.
    Remove-Item -LiteralPath $env:JOURNAL_FACTICES
    $env:MODELES_CHARGES = "[`n  {`n    `"identifier`": `"google/gemma-4-12b`"`n  }`n]"
    $r = capturer { demarrer_lmstudio }
    verifie ($null -eq $r.Exception -and -not (journal_factices).Contains('lms load')) ('rechargé : ' + (journal_factices))
    # Sans TERRITORIA_MODELE : rien n'est chargé.
    Remove-Item -LiteralPath $env:JOURNAL_FACTICES
    [System.IO.File]::WriteAllText($composeEnv, "TERRITORIA_HORS_LIGNE=1`n", $utf8)
    $r = capturer { demarrer_lmstudio }
    verifie ($null -eq $r.Exception -and -not (journal_factices).Contains(' ps ')) ('appel de lms ps sans modèle : ' + (journal_factices))
    # Chargement refusé : la commande lms échoue pendant la phase chargement_impossible (code de la ligne ETAPE).
    [System.IO.File]::WriteAllText($composeEnv, "TERRITORIA_MODELE=modele-absent`n", $utf8)
    $env:MODELES_CHARGES = '[]'
    $env:CODE_CHARGEMENT = '1'
    $script:CodeEchec = ''
    $r = capturer { demarrer_lmstudio }
    verifie ($null -ne $r.Exception -and $script:CodeEchec -eq 'chargement_impossible') ('code : ' + $script:CodeEchec)
    verifie ((journal_factices).Contains('lms load modele-absent --identifier modele-absent --yes')) 'chargement tenté'
    Remove-Item -LiteralPath 'Env:\CODE_CHARGEMENT', 'Env:\MODELES_CHARGES'
    $env:PATH = $script:rep_factices + ':' + $cheminInitial
}

cas 'dossiers_prives_avant_les_secrets' {
    $d = dossier_neuf 'prive'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    $script:ImagePython = 'python:3.12-slim-bookworm@sha256:x'
    function attribuer_proprietaires { }
    $r = capturer { preparer_dossiers }
    verifie ($null -eq $r.Exception) ('échec : ' + $r.Exception)
    foreach ($nom in @('', 'caddy', 'secrets')) {
        $dossier = Join-Path -Path $script:Donnees -ChildPath $nom
        verifie ((& stat -c %a $dossier) -eq '700') ('droits de ' + $dossier)
    }
    foreach ($nom in @('territoria.env', 'n8n.env', 'compose.env')) {
        $fichier = Join-Path -Path $script:Secrets -ChildPath $nom
        verifie ((Test-Path -LiteralPath $fichier) -and (& stat -c %a $fichier) -eq '600' -and (Get-Item -LiteralPath $fichier).Length -eq 0) ('fichier ' + $nom)
    }
}

cas 'verification_du_dossier_de_donnees_et_de_la_destination' {
    $d = dossier_neuf 'chemins'
    fixer_donnees (Join-Path -Path $d -ChildPath 'donnees')
    verifie ((dans_dossier '/repo/a' '/repo') -and -not (dans_dossier '/repository' '/repo') -and (dans_dossier '/repo' '/repo')) 'dans_dossier'
    $null = echec_attendu 'destination_invalide' { verifier_destination 'relatif' }
    $null = echec_attendu 'destination_invalide' { verifier_destination ($script:Donnees + '/sauvegardes') }
    $null = echec_attendu 'destination_invalide' { verifier_destination '/repo/sauvegardes' }
    $null = echec_attendu 'archive_invalide' { sauvegarde_restaurer 'relatif.tsv1' '' '' }
}

# =====================================================================================================================
# Scripts complets en exécution réelle, avec un faux Docker
# =====================================================================================================================

function installation_factice {
    param([string]$nom)
    $dossier = nouvel_environnement $nom
    $donnees = Join-Path -Path $dossier -ChildPath 'donnees'
    $secrets = Join-Path -Path $donnees -ChildPath 'secrets'
    $null = New-Item -ItemType Directory -Path (Join-Path -Path $donnees -ChildPath 'territoria'), $secrets -Force
    [System.IO.File]::WriteAllText((Join-Path -Path $secrets -ChildPath 'n8n.env'), "N8N_ENCRYPTION_KEY=cle`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $secrets -ChildPath 'territoria.env'), "TERRITORIA_SERVICE_TOKEN=jeton`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $secrets -ChildPath 'compose.env'), "TERRITORIA_HORS_LIGNE=1`n")
    $env:TERRITORIA_DONNEES_HOTE = $donnees
    $env:STDIN_FACTICE = Join-Path -Path $dossier -ChildPath 'entree.bin'
    $factices = Join-Path -Path $dossier -ChildPath 'docker-factice'
    creer_factice $factices 'docker' @'
case "$1" in
  info) exit 0 ;;
  inspect) echo healthy ;;
  image) exit 1 ;;
  compose)
    case "$*" in
      *" ps -q "*) echo conteneur123 ;;
      *" images -q "*) echo sha256:imagefictive ;;
      *" exec "*demo*) echo "ETAPE demo : ${DEMO_FACTICE:-OK}" ;;
      *" exec "*verifier*) echo "RAPPORT x"; echo "ETAPE verifier : OK" ;;
    esac ;;
  run)
    case "$*" in
      *elaguer*) cat >/dev/null; echo "ETAPE elaguer : OK" ;;
      *sauvegarder*) cat >"$STDIN_FACTICE"; echo "ETAPE sauvegarder : OK" ;;
    esac ;;
esac
'@
    $env:PATH = $factices + ':' + $cheminInitial
    return $donnees
}

cas 'demarrer_et_arreter_reels' {
    $donnees = installation_factice 'reel-demarrer'
    $r = lancer_script 'demarrer.ps1'
    verifie ($r.Code -eq 0 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE demarrer : OK') ('demarrer : ' + ($r.Lignes -join ' / ') + $r.Erreur)
    $appels = journal_factices
    verifie ($appels.Contains('compose -p territoria --env-file') -and $appels.Contains(' up -d')) 'compose up -d'
    verifie ($appels.Contains('inspect --format {{if .State.Health}}')) 'attente de la bonne santé'
    verifie ($r.Erreur.Contains('n8n : sain') -and $r.Erreur.Contains('territoria : sain')) 'services sains'
    $r = lancer_script 'arreter.ps1'
    verifie ($r.Code -eq 0 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE arreter : OK') 'arreter'
    verifie ((journal_factices).Contains(' stop')) 'compose stop'
    # secrets absents : arrêt avant tout démarrage
    Remove-Item -LiteralPath (Join-Path -Path $donnees -ChildPath 'secrets/n8n.env')
    $r = lancer_script 'demarrer.ps1'
    verifie ($r.Code -eq 1 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE demarrer : ECHEC secrets_absents') ('sans secrets : ' + ($r.Lignes -join ' / '))
    # rien d'installé : rien à arrêter
    $null = installation_factice 'reel-arreter-vide'
    Remove-Item -LiteralPath (Join-Path -Path $env:TERRITORIA_DONNEES_HOTE -ChildPath 'secrets/compose.env')
    Remove-Item -LiteralPath $env:JOURNAL_FACTICES -Force -ErrorAction SilentlyContinue
    $r = lancer_script 'arreter.ps1'
    verifie ($r.Code -eq 0 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE arreter : OK' -and (journal_factices).Length -eq 0) 'arreter sans installation'
}

cas 'demo_et_verifier_reprennent_le_code_d_outils' {
    $null = installation_factice 'reel-demo'
    $r = lancer_script 'installer.ps1' @('--etape', 'demo')
    verifie ($r.Code -eq 0 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE demo : OK') ('demo : ' + ($r.Lignes -join ' / ') + $r.Erreur)
    $env:DEMO_FACTICE = 'ECHEC bibliotheque_etrangere'
    $r = lancer_script 'installer.ps1' @('--etape', 'demo')
    verifie ($r.Code -eq 1 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE demo : ECHEC bibliotheque_etrangere') ('demo refusé : ' + ($r.Lignes -join ' / '))
    verifie (@($r.Lignes | Where-Object { ([string]$_).StartsWith('ETAPE ') }).Count -eq 1) 'plusieurs lignes ETAPE'
    Remove-Item Env:\DEMO_FACTICE
    $r = lancer_script 'installer.ps1' @('--etape', 'verifier')
    verifie ($r.Code -eq 0 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE verifier : OK') ('verifier après la ligne RAPPORT : ' + ($r.Lignes -join ' / '))
    verifie (-not (journal_factices).Contains('--strict')) '--strict'
}

cas 'sauvegarder_auto_script_complet_et_journal' {
    $donnees = installation_factice 'reel-sauvegarde'
    $destination = Join-Path -Path (Split-Path -Parent $donnees) -ChildPath 'externe'
    $null = New-Item -ItemType Directory -Path $destination -Force
    [System.IO.File]::WriteAllBytes((Join-Path -Path $donnees -ChildPath 'secrets/sauvegarde.motdepasse'), $utf8.GetBytes("Mot-de-passe-é-1234`n"))
    $r = lancer_script 'sauvegarder.ps1' @('--destination', $destination, '--auto')
    verifie ($r.Code -eq 0 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE sauvegarder : OK') ('sauvegarder : ' + ($r.Lignes -join ' / ') + $r.Erreur)
    verifie ((journal_factices).Contains('python -m outils elaguer')) 'élagage après la sauvegarde'
    $journal = Join-Path -Path $donnees -ChildPath 'sauvegarde-auto.log'
    verifie ([System.IO.File]::ReadAllText($journal) -match '^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ETAPE sauvegarder : OK\n$') ('journal : ' + [System.IO.File]::ReadAllText($journal))
    $r = lancer_script 'sauvegarder.ps1' @('--destination', (Join-Path -Path $destination -ChildPath 'absente'), '--auto')
    verifie ($r.Code -eq 1 -and ([string]$r.Lignes[-1]).TrimEnd("`r") -eq 'ETAPE sauvegarder : ECHEC destination_absente') ($r.Lignes -join ' / ')
    $lignes = @([System.IO.File]::ReadAllLines($journal))
    verifie ($lignes.Count -eq 2 -and $lignes[1].EndsWith(' ETAPE sauvegarder : ECHEC destination_absente')) ('journal après échec : ' + ($lignes -join ' / '))
    verifie (-not ([System.IO.File]::ReadAllText($journal)).Contains('Mot-de-passe')) 'secret dans le journal'
    # sans --auto, pas de journal
    Remove-Item -LiteralPath $journal -Force
    $r = lancer_script 'sauvegarder.ps1' @('--destination', $destination)
    verifie (-not (Test-Path -LiteralPath $journal)) 'journal écrit hors tâche planifiée'
}

cas 'aucun_secret_ni_asplaintext' {
    foreach ($fichier in $fichiers) {
        $texte = [System.IO.File]::ReadAllText($fichier.FullName)
        verifie (-not ($texte -match '(?i)-AsPlainText')) ($fichier.Name + ' : -AsPlainText')
        verifie (-not ($texte -match '(?i)ConvertTo-SecureString')) ($fichier.Name + ' : ConvertTo-SecureString')
        verifie (-not ($texte -match '(?i)\bGet-Content\b|\bSet-Content\b|\bOut-File\b|\bAdd-Content\b')) ($fichier.Name + ' : cmdlet de contenu sans encodage explicite')
    }
}

# =====================================================================================================================
# Mise à jour avec retour automatique (install/mettre-a-jour.ps1) : faux git et faux Docker
# =====================================================================================================================

# L'image PowerShell n'a pas git : un git factice tient la version extraite (HEAD), la branche, les références connues,
# l'état « git status » et le code de « git fetch » dans $GIT_ETAT. Le faux Docker imite les réponses utiles (image,
# santé, archive créée, restauration, déplacement vers echec-*) et consigne chaque appel avec la version extraite.
$script:corps_git = @'
if [ "$1" = -C ]; then shift 2; fi
if [ "$1" = -c ]; then shift 2; fi
commande="$1"
shift
case "$commande" in
  rev-parse)
    if [ "$1" = --is-inside-work-tree ]; then echo true; exit 0; fi
    if [ "$1 $2" = "--verify HEAD" ]; then cat "$GIT_ETAT/HEAD"; exit 0; fi
    ref="$3"
    ref="${ref%'^{commit}'}"
    ref="${ref#refs/heads/}"
    ligne="$(grep "^$ref " "$GIT_ETAT/refs")" || exit 1
    echo "${ligne#* }"
    ;;
  status) cat "$GIT_ETAT/STATUT" ;;
  fetch) exit "$(cat "$GIT_ETAT/FETCH_CODE")" ;;
  symbolic-ref)
    branche="$(cat "$GIT_ETAT/BRANCHE")"
    if [ -z "$branche" ]; then exit 1; fi
    echo "$branche"
    ;;
  checkout)
    if [ "$1" = --quiet ]; then shift; fi
    if [ "$1" = --detach ]; then
      echo "$2" >"$GIT_ETAT/HEAD"
      : >"$GIT_ETAT/BRANCHE"
    else
      ligne="$(grep "^$1 " "$GIT_ETAT/refs")"
      echo "${ligne#* }" >"$GIT_ETAT/HEAD"
      echo "$1" >"$GIT_ETAT/BRANCHE"
    fi
    ;;
esac
'@
$script:corps_docker_maj = @'
version="$(cat "$GIT_ETAT/HEAD")"
printf '%s @%s\n' "$*" "$version" >>"$DOCKER_ETAT/evenements"
tout=" $* "
precedent=""
for mot in "$@"; do
  if [ "$precedent" = -v ]; then
    case "$mot" in
      *:/destination | *:/destination:ro) m_destination="${mot%:/destination*}" ;;
      *:/cible) m_cible="${mot%:/cible}" ;;
      *:/donnees-hote) m_hote="${mot%:/donnees-hote}" ;;
      *:/donnees-secrets) m_secrets="${mot%:/donnees-secrets}" ;;
      *:/provision:ro) m_provision="${mot%:/provision:ro}" ;;
    esac
  fi
  precedent="$mot"
  dernier="$mot"
done
case "$tout" in
  *" run "*" -i "*) { printf '== %s\n' "$*"; cat; printf '\n'; } >>"$DOCKER_ETAT/entrees" ;;
esac
case "$1" in
  info) exit 0 ;;
  inspect) echo healthy; exit 0 ;;
  image) cat "$DOCKER_ETAT/image"; exit 0 ;;
esac
case "$tout" in
  *" images -q territoria "*) cat "$DOCKER_ETAT/image" ;;
  *" ps -q "*) echo conteneur ;;
  *" build territoria "*)
    n=$(($(cat "$DOCKER_ETAT/n") + 1))
    echo "$n" >"$DOCKER_ETAT/n"
    echo "sha256:image-$n" >"$DOCKER_ETAT/image"
    ;;
  *" run --rm -T travaux-modeles"*) echo "ETAPE modeles-audio : OK" ;;
  *" python -m outils verifier-sauvegarde "*) echo "ETAPE verifier-sauvegarde : OK" ;;
  *" python -m outils provision-n8n "*)
    mkdir -p "$m_secrets/provision" && echo '[]' >"$m_secrets/provision/credentials.json" && echo '{}' >"$m_secrets/provision/conversation.json"
    echo "ETAPE provision-n8n : OK"
    ;;
  *" import:workflow "*) ls "$m_provision" >"$DOCKER_ETAT/importes" ;;
  *" python -m outils sauvegarder "*)
    : >"$m_destination/territoria-$(date +%Y%m%d-%H%M%S).tsv1"
    echo "ETAPE sauvegarder : OK"
    ;;
  *" python -m outils restaurer "*)
    if [ -n "$RESTAURATION_EN_ECHEC" ]; then
      if [ "$RESTAURATION_EN_ECHEC" = restauration_interrompue ]; then mkdir -p "$m_cible/.restauration-essai"; fi
      echo "ETAPE restaurer : ECHEC $RESTAURATION_EN_ECHEC"
    else
      mkdir -p "$m_cible/territoria" "$m_cible/n8n"
      echo "ETAPE restaurer : OK"
    fi
    ;;
  *" python -m outils verifier "*)
    if [ "$version" = "$VERIFICATION_EN_ECHEC" ]; then echo "ETAPE verifier : ECHEC reponse_sourcee"; else echo "ETAPE verifier : OK"; fi
    ;;
  *" --entrypoint sh "*)
    for r in territoria n8n; do
      if [ -e "$m_hote/$r" ]; then mv "$m_hote/$r" "$m_hote/$dernier/$r"; fi
    done
    ;;
esac
'@
$script:commit_a = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$script:commit_b = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

function mise_a_jour_factice {
    param([string]$nom)
    $dossier = nouvel_environnement $nom
    $donnees = Join-Path -Path $dossier -ChildPath 'donnees maj'
    foreach ($sous in @('territoria', 'n8n', 'secrets')) { $null = New-Item -ItemType Directory -Path (Join-Path -Path $donnees -ChildPath $sous) -Force }
    [System.IO.File]::WriteAllText((Join-Path -Path $donnees -ChildPath 'territoria/territoire.sqlite3'), "avant`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $donnees -ChildPath 'secrets/compose.env'), "TERRITORIA_HORS_LIGNE=1`n")
    [System.IO.File]::WriteAllBytes((Join-Path -Path $donnees -ChildPath 'secrets/sauvegarde.motdepasse'), $utf8.GetBytes("Mot-de-passe-maj-é-1234`n"))
    $env:TERRITORIA_DONNEES_HOTE = $donnees
    $env:GIT_ETAT = Join-Path -Path $dossier -ChildPath 'git'
    $env:DOCKER_ETAT = Join-Path -Path $dossier -ChildPath 'docker'
    $null = New-Item -ItemType Directory -Path $env:GIT_ETAT, $env:DOCKER_ETAT -Force
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'HEAD'), ($script:commit_a + "`n"))
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'BRANCHE'), "principale`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'refs'), ("principale {0}`nv-b {1}`n" -f $script:commit_a, $script:commit_b))
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'STATUT'), '')
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'FETCH_CODE'), "0`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $env:DOCKER_ETAT -ChildPath 'n'), "1`n")
    [System.IO.File]::WriteAllText((Join-Path -Path $env:DOCKER_ETAT -ChildPath 'image'), "sha256:image-1`n")
    creer_factice $script:rep_factices 'git' $script:corps_git
    creer_factice $script:rep_factices 'docker' $script:corps_docker_maj
    foreach ($variable in @('VERIFICATION_EN_ECHEC', 'RESTAURATION_EN_ECHEC')) { Remove-Item -LiteralPath ('Env:\' + $variable) -ErrorAction SilentlyContinue }
    return $donnees
}

function etat_git {
    param([string]$fichier)
    return ([System.IO.File]::ReadAllText((Join-Path -Path $env:GIT_ETAT -ChildPath $fichier))).Trim()
}

# Étapes de la séquence vues par le faux Docker, avec la version extraite à ce moment (a ou b).
function evenements_maj {
    $chemin = Join-Path -Path $env:DOCKER_ETAT -ChildPath 'evenements'
    if (-not (Test-Path -LiteralPath $chemin)) { return '' }
    $motifs = [ordered]@{
        'verifier-sauvegarde' = ' python -m outils verifier-sauvegarde '; 'sauvegarder' = ' python -m outils sauvegarder';
        'restaurer' = ' python -m outils restaurer '; 'verifier' = ' python -m outils verifier @'; 'build' = ' build territoria @';
        'up' = ' up -d @'; 'stop' = ' stop @'; 'proprietaires' = 'chown 10001:10001 /territoria ';
        'deplacement' = ' --entrypoint sh '; 'etiquette' = ' tag sha256:';
        'retrait' = ' image rm '; 'provision' = ' python -m outils provision-n8n '; 'arret_n8n' = ' stop n8n @';
        'credentials' = ' import:credentials '; 'workflows' = ' import:workflow '; 'publication' = ' publish:workflow --id=TerritoriaArre01 '
    }
    $resultat = @()
    foreach ($ligne in [System.IO.File]::ReadAllLines($chemin)) {
        $version = $ligne.Substring($ligne.LastIndexOf(' @') + 2).Substring(0, 1)
        foreach ($nom in $motifs.Keys) {
            if ((' ' + $ligne + ' ').Contains($motifs[$nom])) { $resultat += ('{0}@{1}' -f $nom, $version); break }
        }
    }
    return ($resultat -join ' ')
}

function derniere_ligne_maj {
    param($r)
    return ([string]@($r.Lignes)[-1]).TrimEnd("`r")
}

cas 'simulation_mise_a_jour' {
    $sortie = verifier_simulation 'mettre-a-jour.ps1' @('--version', 'v2.0') 'mettre-a-jour' @('docker', 'git')
    $ordre = @('fetch --tags', 'python -m outils sauvegarder', 'outils verifier-sauvegarde', 'checkout --quiet --detach',
        ' build territoria', ' build travaux', ' --profile outils run --rm -T travaux-modeles',
        'outils provision-n8n --sortie /donnees-secrets/provision', ' stop n8n', 'import:workflow --separate',
        'publish:workflow --id=TerritoriaArre01', ' up -d', "python -m outils verifier`n", " stop`n", 'echec-', '--entrypoint sh',
        'python -m outils restaurer', ' checkout --quiet', ' build territoria', ' up -d', 'python -m outils verifier')
    $position = 0
    foreach ($morceau in $ordre) {
        $trouve = $sortie.IndexOf($morceau, $position)
        verifie ($trouve -ge 0) ('absent ou hors ordre : ' + $morceau)
        $position = $trouve
    }
    verifie ($sortie.Contains('--forcer')) 'restauration forcée'
    verifie ($sortie.Contains('mises-a-jour')) 'dossier mises-a-jour'
    verifie (-not $sortie.Contains('--strict')) '--strict'
    foreach ($ligne in $sortie.Split("`n")) {
        if ($ligne.Contains(' compose ')) { verifie ($ligne.Contains('docker compose -p territoria --env-file ')) ('compose : ' + $ligne) }
    }
}

cas 'mise_a_jour_reussie' {
    $donnees = mise_a_jour_factice 'maj-reussie'
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    verifie ($r.Code -eq 0 -and (derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : OK') ('code {0} : {1} {2}' -f $r.Code, ($r.Lignes -join ' / '), $r.Erreur)
    verifie (@($r.Lignes).Count -eq 1) ('sortie standard : ' + ($r.Lignes -join ' / '))
    verifie ((etat_git 'HEAD') -eq $script:commit_b) 'version extraite'
    # Workflows de la nouvelle version importés dans n8n après la construction, avant le redémarrage et le test.
    $attendu = 'sauvegarder@a verifier-sauvegarde@a etiquette@a proprietaires@b build@b provision@b arret_n8n@b credentials@b workflows@b publication@b up@b verifier@b retrait@b'
    verifie ((evenements_maj) -eq $attendu) ('séquence : ' + (evenements_maj))
    $importes = [System.IO.File]::ReadAllText((Join-Path -Path $env:DOCKER_ETAT -ChildPath 'importes'))
    verifie ($importes -eq "conversation.json`ncredentials.json`n") ('fichiers importés : ' + $importes)
    verifie (-not (Test-Path -LiteralPath (Join-Path -Path $donnees -ChildPath 'secrets/provision'))) 'provision/ supprimé'
    $archives = @(Get-ChildItem -LiteralPath (Join-Path -Path $donnees -ChildPath 'mises-a-jour') -Filter 'territoria-*.tsv1')
    verifie ($archives.Count -eq 1) 'archive dans mises-a-jour'
    $evenements = [System.IO.File]::ReadAllText((Join-Path -Path $env:DOCKER_ETAT -ChildPath 'evenements'))
    verifie ($evenements.Contains('--depuis-fichier /source/secrets/sauvegarde.motdepasse')) 'mot de passe par fichier'
    verifie ($evenements.Contains('--archive /destination/' + $archives[0].Name)) 'vérification de cette archive'
    verifie ([System.IO.File]::ReadAllText((Join-Path -Path $env:DOCKER_ETAT -ChildPath 'entrees')).Contains('Mot-de-passe-maj')) 'mot de passe sur l''entrée standard'
    verifie (-not ($evenements + $r.Erreur).Contains('Mot-de-passe-maj')) 'mot de passe en argument ou affiché'
}

cas 'mise_a_jour_ratee_puis_retour' {
    $donnees = mise_a_jour_factice 'maj-retour'
    $env:VERIFICATION_EN_ECHEC = $script:commit_b
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    Remove-Item -LiteralPath 'Env:\VERIFICATION_EN_ECHEC'
    verifie ($r.Code -eq 1 -and (derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC mise_a_jour_annulee') ('code {0} : {1} {2}' -f $r.Code, ($r.Lignes -join ' / '), $r.Erreur)
    $erreurs = @($r.Erreur.TrimEnd("`r", "`n") -split "\r?\n")
    verifie ($erreurs[-1] -eq 'Mise à jour annulée : version précédente rétablie.') ('dernière ligne d''erreur : ' + $erreurs[-1])
    verifie ((etat_git 'HEAD') -eq $script:commit_a -and (etat_git 'BRANCHE') -eq 'principale') 'retour sur la branche d''origine'
    $attendu = 'sauvegarder@a verifier-sauvegarde@a etiquette@a proprietaires@b build@b provision@b arret_n8n@b credentials@b workflows@b publication@b up@b verifier@b stop@b deplacement@b stop@b restaurer@b build@a up@a verifier@a retrait@a'
    verifie ((evenements_maj) -eq $attendu) ('séquence : ' + (evenements_maj))
    $evenements = [System.IO.File]::ReadAllText((Join-Path -Path $env:DOCKER_ETAT -ChildPath 'evenements'))
    verifie ($evenements.Contains('tag sha256:image-1 territoria-territoria:avant-mise-a-jour @a')) 'étiquette de l''image d''avant'
    verifie ($evenements.Contains(':/donnees-hote territoria-territoria:avant-mise-a-jour -c ')) 'déplacement avec l''image d''avant'
    verifie ($evenements -match ' territoria-territoria:avant-mise-a-jour python -m outils restaurer --archive /destination/territoria-\d{8}-\d{6}\.tsv1 --forcer @') 'restauration avec l''image d''avant'
    $echecs = @(Get-ChildItem -LiteralPath $donnees -Directory | Where-Object { $_.Name -cmatch '^echec-\d{8}-\d{6}$' })
    verifie ($echecs.Count -eq 1) 'un dossier echec-*'
    $contenu = @(Get-ChildItem -LiteralPath $echecs[0].FullName | ForEach-Object { $_.Name } | Sort-Object)
    verifie (($contenu -join ',') -eq 'n8n,territoria') ('contenu de echec-* : ' + ($contenu -join ','))
    verifie ([System.IO.File]::ReadAllText((Join-Path -Path $echecs[0].FullName -ChildPath 'territoria/territoire.sqlite3')) -eq "avant`n") 'données de la tentative gardées'
    $mode = (& stat -c '%a' $echecs[0].FullName).Trim()
    verifie ($mode -eq '700') ('droits de echec-* : ' + $mode)
    verifie ($r.Erreur.Contains($echecs[0].FullName)) 'le message dit où est echec-*'
    verifie ($r.Erreur.Contains('reponse_sourcee')) 'cause de l''échec'
}

cas 'mise_a_jour_consigne_interruption_citee' {
    # Ctrl+C ne se rattrape pas dans un essai : les deux fonctions sont lues dans le script et jouées ici, avec un dépôt et
    # une archive dont les chemins ont des espaces, une virgule, des accents et des apostrophes (droite et typographique).
    $arbre = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path -Path $depot -ChildPath 'install/mettre-a-jour.ps1'), [ref]$null, [ref]$null)
    foreach ($nom in @('version_precedente', 'consigne_interruption')) {
        $definition = $arbre.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $nom }, $true)
        verifie ($null -ne $definition) ('fonction absente : ' + $nom)
        . ([scriptblock]::Create($definition.Extent.Text))
    }
    $racineAvant = $script:Racine
    try {
        $script:Racine = "/tmp/Dupont, Jean/l'été ‘2026’/territoria"
        $script:ArchiveMaj = "/tmp/Dupont, Jean/l'été ‘2026’/territoria-donnees/mises-a-jour/territoria-20261031-120000.tsv1"
        $script:AncienneBranche = 'principale'
        $script:AncienCommit = $script:commit_a
        $script:CodeEchec = 'interrompu'
        $r = capturer { consigne_interruption }
        verifie ($null -eq $r.Exception) ('exception : ' + $r.Exception)
        $lignes = @($r.Erreur -split "\r?\n")
        $i = [array]::IndexOf($lignes, "Pour revenir en arrière, lancez ces trois commandes l'une après l'autre dans PowerShell (la restauration demande le mot de passe de la sauvegarde) :")
        verifie ($i -ge 0) ('consigne absente : ' + $r.Erreur)
        $attendus = @(
            , @('git', '-C', $script:Racine, 'checkout', 'principale')
            , @('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (joindre $script:Racine @('install', 'restaurer.ps1')), '--archive', $script:ArchiveMaj, '--forcer')
            , @('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (joindre $script:Racine @('install', 'demarrer.ps1')))
        )
        for ($k = 0; $k -lt 3; $k++) {
            $ligne = $lignes[$i + 1 + $k]
            verifie ($ligne.StartsWith('  ')) ('ligne non indentée : ' + $ligne)
            # L'analyseur de PowerShell découpe la ligne comme le ferait la console : chaque chemin reste un seul argument.
            $erreurs = $null
            $analyse = [System.Management.Automation.Language.Parser]::ParseInput($ligne.Trim(), [ref]$null, [ref]$erreurs)
            verifie ($erreurs.Count -eq 0) ('ligne illisible : ' + $ligne)
            $commande = $analyse.Find({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)
            $mots = @($commande.CommandElements | ForEach-Object {
                    if ($_ -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $_.Value }
                    elseif ($_ -is [System.Management.Automation.Language.CommandParameterAst]) { $_.Extent.Text }
                    else { '<' + $_.GetType().Name + '>' }
                })
            verifie (($mots -join '|') -ceq ($attendus[$k] -join '|')) ('ligne {0} : {1} => {2}' -f $k, $ligne, ($mots -join '|'))
        }
    } finally {
        $script:Racine = $racineAvant
        Remove-Variable -Scope Script -Name ArchiveMaj, AncienneBranche, AncienCommit -ErrorAction SilentlyContinue
    }
}

cas 'mise_a_jour_restauration_interrompue' {
    $donnees = mise_a_jour_factice 'maj-interrompue'
    $env:VERIFICATION_EN_ECHEC = $script:commit_b
    $env:RESTAURATION_EN_ECHEC = 'restauration_interrompue'
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    Remove-Item -LiteralPath 'Env:\VERIFICATION_EN_ECHEC', 'Env:\RESTAURATION_EN_ECHEC'
    verifie ($r.Code -eq 1 -and (derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC restauration_interrompue') ('code {0} : {1}' -f $r.Code, ($r.Lignes -join ' / '))
    verifie ((evenements_maj).EndsWith('restaurer@b')) ('rien après la restauration : ' + (evenements_maj))
    verifie ((etat_git 'HEAD') -eq $script:commit_b) 'aucun retour de version'
    verifie (-not $r.Erreur.Contains('Mise à jour annulée')) 'message d''annulation'
    verifie ($r.Erreur.Contains((Join-Path -Path $donnees -ChildPath '.restauration-essai'))) 'dossier .restauration-* cité'
    verifie ($r.Erreur.Contains('intervention humaine')) 'intervention humaine'
    # Une autre panne de la restauration : retour_impossible, avec le code d'origine dans le message.
    $null = mise_a_jour_factice 'maj-retour-impossible'
    $env:VERIFICATION_EN_ECHEC = $script:commit_b
    $env:RESTAURATION_EN_ECHEC = 'archive_illisible'
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    Remove-Item -LiteralPath 'Env:\VERIFICATION_EN_ECHEC', 'Env:\RESTAURATION_EN_ECHEC'
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC retour_impossible') ($r.Lignes -join ' / ')
    verifie ($r.Erreur.Contains('archive_illisible')) 'code d''origine'
}

cas 'mise_a_jour_refus_avant_tout_changement' {
    $null = mise_a_jour_factice 'maj-refus'
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'STATUT'), " M install/commun.sh`n?? réunion.txt`n")
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC modifications_locales') ($r.Lignes -join ' / ')
    # Fichiers en cause nommés, avec ce qu'il faut faire.
    $erreurs = @($r.Erreur.TrimEnd("`r", "`n") -split "\r?\n")
    verifie ($erreurs -contains '  install/commun.sh (modifié)') ('fichier modifié non cité : ' + $r.Erreur)
    verifie ($erreurs -contains '  réunion.txt (ajouté)') ('fichier ajouté non cité : ' + $r.Erreur)
    verifie ($erreurs[-1].EndsWith('puis relancez la mise à jour.')) ('consigne : ' + $erreurs[-1])
    verifie ((journal_factices).Contains('-c core.quotePath=false status --porcelain')) 'git status sans échappement des accents'
    # Au-delà de dix fichiers : les dix premiers, puis le compte des autres.
    $statut = (@(0..11 | ForEach-Object { '?? ajout-{0:D2}.txt' -f $_ }) -join "`n") + "`n"
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'STATUT'), $statut)
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    $erreurs = @($r.Erreur.TrimEnd("`r", "`n") -split "\r?\n")
    $cites = @($erreurs | Where-Object { $_.StartsWith('  ajout-') })
    verifie ($cites.Count -eq 10 -and $cites[0] -eq '  ajout-00.txt (ajouté)' -and $cites[9] -eq '  ajout-09.txt (ajouté)') ('liste : ' + ($cites -join ' / '))
    verifie ($erreurs -contains '  … et 2 autres') ('reste non compté : ' + $r.Erreur)
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'STATUT'), '')
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-inconnue')
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC version_introuvable') ($r.Lignes -join ' / ')
    [System.IO.File]::WriteAllText((Join-Path -Path $env:GIT_ETAT -ChildPath 'FETCH_CODE'), "1`n")
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-inconnue')
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC recuperation_impossible') ($r.Lignes -join ' / ')
    verifie (-not (Test-Path -LiteralPath (Join-Path -Path $env:DOCKER_ETAT -ChildPath 'evenements'))) 'Docker appelé avant le refus'
    verifie ((etat_git 'HEAD') -eq $script:commit_a) 'version changée'
    # Récupération impossible mais version connue localement : la mise à jour se fait.
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : OK') (($r.Lignes -join ' / ') + $r.Erreur)
    verifie ($r.Erreur.Contains('git fetch --tags')) 'avertissement'
    # Sans mot de passe enregistré ni console : refus avant toute sauvegarde.
    $null = mise_a_jour_factice 'maj-sans-mot-de-passe'
    Remove-Item -LiteralPath (Join-Path -Path $env:TERRITORIA_DONNEES_HOTE -ChildPath 'secrets/sauvegarde.motdepasse')
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b')
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC terminal_requis') ($r.Lignes -join ' / ')
    verifie (-not (Test-Path -LiteralPath (Join-Path -Path $env:DOCKER_ETAT -ChildPath 'evenements'))) 'Docker appelé sans mot de passe'
    # Arguments refusés, sans rien lancer.
    foreach ($arguments in @(@(), @('--version'), @('--version', '-x'), @('--Version', 'v-b'), @('--version', 'v-b', '--destination'))) {
        $r = lancer_script 'mettre-a-jour.ps1' $arguments
        verifie ($r.Code -eq 1 -and @($r.Lignes).Count -eq 1 -and (derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC arguments_invalides') (($arguments -join ' ') + ' : ' + ($r.Lignes -join ' / '))
    }
    $r = lancer_script 'mettre-a-jour.ps1' @('--version', 'v-b', '--destination', 'relatif')
    verifie ((derniere_ligne_maj $r) -eq 'ETAPE mettre-a-jour : ECHEC destination_invalide') ($r.Lignes -join ' / ')
}
