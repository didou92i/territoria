# Accès de TerritorIA à LM Studio

LM Studio est installé directement sur la machine, hors des conteneurs. Le conteneur `territoria` doit pouvoir
l'interroger, et aucune autre machine du réseau local ne doit l'atteindre (spécification, §3.1 et §3.3).

L'étape `install/installer.sh --etape lmstudio` (macOS, Linux) ou `install\installer.ps1 --etape lmstudio` (Windows)
applique les réglages de cette page. Elle :

- choisit l'adresse d'écoute (`adresse_ecoute_lms` dans `install/commun.sh`) ;
- démarre le serveur avec `lms server start --port 1234 --bind <adresse>` ;
- écrit `TERRITORIA_LLM_URL` et `TERRITORIA_MODELE` dans `territoria-donnees/secrets/compose.env`, que Compose lit avec
  `--env-file` (jamais dans `secrets/territoria.env`) ;
- vérifie trois choses :
  - le modèle figure dans `/v1/models` ;
  - un conteneur jetable joint l'URL écrite dans `compose.env` ;
  - une requête de test marquée (`marqueur-territoria-…`) n'apparaît dans aucun journal de LM Studio.

`install/demarrer.sh` (comme la mise à jour) relance le serveur avec la même adresse s'il est arrêté, puis y charge le
modèle de `compose.env` s'il ne l'est pas (`lms load <id> --identifier <clé>`, `ECHEC chargement_impossible` si LM Studio
refuse) : la première demande, ou le test de bout en bout, ne le charge pas à la volée.

**Réseaux des conteneurs.** Seul `territoria` joint LM Studio, par son réseau `sortie` (`host-gateway`) ; n8n n'est que
sur le réseau `interne` (`internal: true`), sans sortie vers la machine ni vers Internet. Les appels de TerritorIA à
LM Studio ne passent jamais par un proxy, et `NO_PROXY` couvre `host.docker.internal` et `host.containers.internal`.

**Écoute réelle contrôlée.** L'étape `lmstudio` et `install/demarrer.sh` relèvent les adresses réellement en écoute sur
le port 1234 : `lsof -nP -iTCP:1234 -sTCP:LISTEN` sur macOS, `ss -ltn` sous Linux, `Get-NetTCPConnection -LocalPort 1234
-State Listen` sous Windows. Seules la boucle locale et
l'adresse choisie (passerelle de `docker0` sous Linux) sont acceptées. Une écoute sur toutes les interfaces (`*`,
`0.0.0.0`, `[::]`, `::`) ou sur une adresse du réseau local arrête l'étape avec `ECHEC lmstudio_expose_reseau`, par exemple
si « Serve on Local Network » avait été activé dans LM Studio. L'étape `lmstudio` écrit aussi l'adresse choisie dans
le réglage `networkInterface` de LM Studio.

## Critères d'acceptation

1. `curl -s $TERRITORIA_LLM_URL/models`, lancé **depuis le conteneur `territoria`**, répond.
2. Une autre machine du réseau local n'obtient **aucune** réponse sur le port 1234.

## Réglage par système

| Système et moteur | Écoute de LM Studio (`--bind`) | `TERRITORIA_LLM_URL` | État |
| --- | --- | --- | --- |
| macOS, Docker Desktop | `127.0.0.1` | `http://host.docker.internal:1234/v1` | **vérifié** (30 octobre 2026, ci-dessous) |
| macOS, Podman (machine) | `127.0.0.1` | `http://host.containers.internal:1234/v1` | à valider en recette |
| Windows, Podman ou Docker Desktop | `127.0.0.1` | `http://host.containers.internal:1234/v1` (Podman) ou `http://host.docker.internal:1234/v1` (Docker) | scripts `.ps1` écrits et vérifiés en simulation ; à valider en recette sur un Windows réel |
| Linux, Docker Engine (installé par l'étape moteur si aucun moteur n'est présent) | passerelle du pont `docker0` (en général `172.17.0.1`) | `http://host.docker.internal:1234/v1` | à valider en recette |
| Linux, Podman déjà installé (réutilisé) | `127.0.0.1` | `http://host.containers.internal:1234/v1` | à valider en recette ; si le contrôle échoue, l'étape s'arrête en `ECHEC` |

### macOS et Windows

Docker Desktop relaie `host.docker.internal` vers la boucle locale de la machine. Le service `territoria` le déclare
dans `compose.yaml` avec `extra_hosts: host.docker.internal:host-gateway`. La machine virtuelle de Podman fait de même
avec `host.containers.internal`, que Podman ajoute de lui-même.

LM Studio peut donc rester sur `127.0.0.1`, son réglage par défaut. Il reste ainsi invisible du réseau local.

### Windows : ce que font les scripts `.ps1`

`install\installer.ps1 --etape lmstudio` applique le même réglage que sur macOS : LM Studio écoute sur `127.0.0.1`, et le
conteneur l'atteint par `host.docker.internal` (Docker Desktop) ou `host.containers.internal` (Podman). Lancement :
`powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape lmstudio` (Windows PowerShell 5.1 ou
PowerShell 7).

- **Installation.** `winget install --exact --id ElementLabs.LMStudio --source winget --silent
  --accept-package-agreements --accept-source-agreements`. L'identifiant a été relevé dans le dépôt `microsoft/winget-pkgs`
  (`manifests/e/ElementLabs/LMStudio`). Le code de sortie « rien à faire » (`0x8A15002B`) et « déjà installé »
  (`0x8A150061`) sont acceptés : l'étape reste relançable.
- **Commande `lms`.** Cherchée dans le `PATH`, puis dans `<dossier de LM Studio>\bin\lms.exe`. LM Studio n'installe cette
  commande qu'à sa première ouverture : sans elle, l'étape s'arrête avec `ECHEC lms_absent` et demande d'ouvrir
  l'application une fois.
- **Dossier de LM Studio.** Résolu comme le fait l'installateur officiel pour Windows (`https://lmstudio.ai/install.ps1`,
  relu sans l'exécuter) : le chemin écrit dans `%USERPROFILE%\.lmstudio-home-pointer` s'il existe, sinon
  `%USERPROFILE%\.lmstudio`, sinon `%LOCALAPPDATA%\lm-studio` (l'autre emplacement que cet installateur documente).
  Rien n'existe, ou le pointeur désigne un dossier absent : `ECHEC lmstudio_dossier_introuvable`, sans rien créer. Le
  pointeur est lu en UTF-8 : un nom de profil accentué reste lisible.
- **Réglages du serveur.** `<dossier de LM Studio>\.internal\http-server-config.json`, lu et écrit en UTF-8 sans BOM
  (`JSON.parse` refuse un BOM) avec `ConvertFrom-Json` et `ConvertTo-Json` : `networkInterface=127.0.0.1`,
  `fileLoggingMode=off`, `verbose=false`, `logIncomingTokens=false`, `logSensitiveData=false`. Les autres clés sont
  gardées. Un fichier illisible arrête l'étape (`ECHEC journalisation_non_reglee`), il n'est jamais réécrit.
- **Serveur.** `lms server start --port 1234 --bind 127.0.0.1`, lancé détaché (la commande peut laisser un démon qui garderait
  une console ouverte).
- **Écoute réelle.** `Get-NetTCPConnection -LocalPort 1234 -State Listen` : seules `127.0.0.1`, `::1` et l'adresse choisie
  passent. `0.0.0.0` ou `::` (toutes les interfaces) ou une adresse du réseau local donnent `ECHEC lmstudio_expose_reseau`.
- **Requête marquée.** `Invoke-WebRequest` (corps en UTF-8) envoie la requête de test ; les fichiers de
  `<dossier de LM Studio>\server-logs` sont lus en octets, même ouverts par LM Studio, et cherchés en ASCII et en UTF-16. Un
  fichier illisible arrête l'étape (`ECHEC journaux_illisibles`) : on ne conclut jamais à l'absence de journal sans avoir
  tout lu.
- **Moteur.** Docker ou Podman déjà présent est réutilisé. Sinon `winget install --exact --id RedHat.Podman`
  (installation pour tous les utilisateurs : Windows demande une autorisation), `winget install --exact --id
  Docker.DockerCompose` pour le fournisseur de `podman compose`, puis `podman machine init` et `podman machine start`.
  Le paquet winget de Podman n'installe pas WSL 2 : sans lui (ni Hyper-V), `podman machine init` échoue et le script
  rappelle la commande `wsl --install` (fenêtre d'administrateur, puis redémarrage).
- **Secrets et accès.** Les dossiers `territoria-donnees` et `secrets` sont réservés à l'utilisateur dès leur création, par
  `icacls <chemin> /inheritance:r /grant:r *<SID>:(OI)(CI)(F)` (dossier) ou `(R,W,D)` (fichier), avant qu'un secret n'y soit
  écrit. Les fichiers temporaires d'écriture sont créés dans le dossier de destination, jamais dans `%TEMP%`. Les mots de
  passe sont saisis par `Read-Host -AsSecureString` ; ils ne sont convertis en texte qu'en mémoire, puis écrits en UTF-8
  sans BOM, avec une seule fin de ligne `LF`, ou envoyés tels quels sur l'entrée standard du conteneur. Jamais en argument.
- **Raccourci.** `Territoria.lnk` sur le bureau (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  install\demarrer.ps1 --ouvrir`) ; avec `--auto`, `TerritorIA demarrage.lnk` dans le dossier Démarrage de l'utilisateur.
- **Sauvegarde automatique.** Tâche planifiée `Territoria-Sauvegarde`, tous les jours à 12 h 30, créée avec `schtasks /Create
  /TN Territoria-Sauvegarde /XML <fichier> /F`. Le fichier XML (UTF-16) évite la limite de 261 caractères de `/TR` et ses
  guillemets imbriqués. La tâche s'exécute sous le compte de l'utilisateur, session ouverte. Elle retrouve
  `territoria-donnees` à l'emplacement standard, ou par une variable `TERRITORIA_DONNEES_HOTE` permanente du compte, sinon
  l'étape s'arrête (`ECHEC donnees_non_standard`).

À valider sur un Windows réel (recette, tâche 15) :

- **Accès des conteneurs.** `curl` depuis le conteneur `territoria` vers `host.docker.internal` (Docker Desktop) et
  `host.containers.internal` (machine Podman) alors que LM Studio n'écoute que sur `127.0.0.1` ; aucune réponse depuis une autre
  machine du réseau local (critères d'acceptation ci-dessus).
- **Propriétaires.** `chown 10001`, `1000` et `10002` par le conteneur jetable sur un dossier de `C:\` partagé avec Docker Desktop
  ou la machine Podman (WSL) : l'opération peut être refusée ou sans effet selon le système de fichiers partagé.
- **Chemin de `TERRITORIA_DONNEES_HOTE`.** Forme `C:\Users\…\territoria-donnees` dans les volumes de Compose
  (`${TERRITORIA_DONNEES_HOTE}/territoria`), avec un nom de profil accentué ou une espace.
- **Destinations de sauvegarde.** Une lettre de lecteur externe ou un partage réseau (`\\serveur\partage`) monté dans le conteneur
  ponctuel, avec Docker Desktop comme avec Podman.
- **`manage-bde -status C:`** exige les droits d'administrateur : sans eux, `chiffrement_disque=inconnu`.
- **Commande `lms`** après `winget install` (voir plus haut) et lecture du fichier de réglages par LM Studio au redémarrage de son
  serveur, comme sur macOS.
- **Tâche planifiée.** Exécution réelle à 12 h 30, session ouverte, fenêtre masquée ; `sauvegarder.ps1 --auto` ajoute une
  ligne (date et ligne `ETAPE sauvegarder : OK|ECHEC <code>`, jamais de secret) à `territoria-donnees\sauvegarde-auto.log`.

### Linux, Docker Engine : écoute sur la passerelle de `docker0`

Sous Linux, un service qui écoute sur `127.0.0.1` n'est pas joignable depuis un conteneur.

`lms server start --help` (CLI `lms`, commit `dcefd5a`) propose `--bind <address>`. Le texte de l'aide : « Network
address to bind the server to. Use "0.0.0.0" to accept connections from the local network, or "127.0.0.1" (default)
for localhost only. Can also be set via the LMS_SERVER_HOST environment variable. »

Le réglage retenu fait écouter LM Studio sur **l'adresse de la passerelle du pont `docker0`**. Elle est lue par
`docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'`, et à défaut par
`ip -4 addr show docker0`.

- `host-gateway` (dans `extra_hosts`) désigne justement cette adresse. Elle est locale à la machine : un conteneur de
  n'importe quel réseau pont de Docker l'atteint, y compris le réseau `territoria_interne` de Compose.
- Les autres machines du réseau local n'ont pas de route vers `172.17.0.0/16`. Elles n'obtiennent donc aucune réponse,
  sans qu'aucune règle de pare-feu soit nécessaire.
- On n'écoute pas sur `0.0.0.0`, qui exposerait LM Studio au réseau local.

À savoir pour la recette (non vérifié sur une machine Linux) :

- **Ordre de démarrage.** LM Studio doit démarrer après Docker, sinon l'adresse de `docker0` n'existe pas encore.
  `install/demarrer.sh` démarre le moteur, puis LM Studio, puis les services.
- **Pare-feu qui refuse par défaut les entrées** (ufw, firewalld). Les conteneurs peuvent être bloqués : il faut une
  règle qui autorise le port 1234 depuis les réseaux des conteneurs seulement. Par exemple :
  - `sudo ufw allow from 172.16.0.0/12 to any port 1234 proto tcp` ;
  - ou, avec firewalld, la zone `docker`.
- **Durcissement facultatif.** Une machine du réseau local qui ajouterait à la main une route vers `172.17.0.0/16`
  par cette machine pourrait atteindre la passerelle. Une règle qui n'accepte le port 1234 que depuis
  `172.16.0.0/12`, par exemple `iptables -I INPUT -p tcp --dport 1234 ! -s 172.16.0.0/12 -j DROP`, ferme ce cas.
  Elle n'est pas posée par le script, car elle exige les droits d'administrateur et sa conservation après un
  redémarrage dépend de la distribution.
- **Docker Desktop pour Linux.** `host-gateway` y désigne la machine virtuelle de Docker Desktop, pas la machine :
  cas non pris en charge.

### Linux : moteur installé par l'étape moteur (Docker Engine)

Sur une machine Linux sans moteur de conteneurs, l'étape `moteur` installe **Docker Engine** depuis les paquets de la
distribution, comme le veut la spécification (§3.1), et jamais par un script distant :

- Debian et Ubuntu : `apt-get update`, puis `apt-get install -y docker.io` avec le module Compose v2 que la
  distribution propose, `docker-compose-v2` ou à défaut `docker-compose-plugin` (choix d'après `apt-cache policy`) ;
- Fedora : `dnf install -y moby-engine docker-compose` ;
- ensuite : `systemctl enable --now docker`.

Sans paquet convenable, l'étape s'arrête avec `ECHEC moteur_paquet_indisponible` et renvoie à la documentation
officielle (https://docs.docker.com/engine/install/). Un Docker ou un Podman déjà présent est réutilisé.

**Groupe docker.** Pour lancer Docker sans `sudo`, y compris depuis la sauvegarde planifiée (cron, sans mot de passe),
l'étape ajoute le compte au groupe `docker` (`usermod -aG docker <compte>`). Ce groupe donne des droits équivalents à
ceux d'administrateur sur la machine : c'est dit à l'utilisateur. L'appartenance ne vaut qu'après une nouvelle ouverture
de session. L'étape s'arrête donc avec `ECHEC reconnexion_requise` ; une fois la session rouverte, la relancer termine
sans rien réinstaller.

### Linux, Podman déjà installé

Podman sans droits root, avec le réseau pasta, résout `host.containers.internal` vers une adresse qu'il relaie à la
machine. Selon la version, la boucle locale de la machine est joignable ou non par ce relais.

Le script garde LM Studio sur `127.0.0.1` : il n'expose jamais LM Studio au réseau. Son contrôle par un conteneur
jetable fait échouer l'étape (`ECHEC lmstudio_injoignable_depuis_conteneur`) si l'accès ne fonctionne pas. Dans ce
cas, Docker Engine, recommandé sous Linux par la spécification, est le recours. Ce cas est à valider en recette.

## Preuve sur la machine de développement (macOS, Docker Desktop 4.97.0, Docker 29.9.2)

Les essais suivants n'ont rien modifié sur la machine.

Essai depuis un conteneur jetable, avec l'image Python épinglée de `territoria.Dockerfile`, l'utilisateur 10001 et la
racine en lecture seule. Le conteneur est branché sur un réseau pont défini par l'utilisateur, comme ceux de Compose,
et lancé avec `--add-host host.docker.internal:host-gateway` :

```
host.docker.internal -> 192.168.65.254
GET http://host.docker.internal:1234/v1/models depuis le conteneur : HTTP 200, 5 modèles listés
```

Écoute de LM Studio (`lsof -nP -iTCP:1234 -sTCP:LISTEN`) : uniquement la boucle locale.

```
LM\x20Stu 38911 vous   80u  IPv4 0x99cfc3ecec1f6503      0t0  TCP 127.0.0.1:1234 (LISTEN)
```

Depuis l'adresse de la machine sur le réseau local, `GET http://192.168.0.10:1234/v1/models` ne reçoit aucune réponse
(curl, délai de 5 s dépassé, code 28). Aucun port 1234 n'est ouvert sur l'interface du réseau local.

## Journalisation de LM Studio

L'aide de `lms server start` n'a pas d'option de journalisation. Les réglages du serveur sont dans
`<dossier de LM Studio>/.internal/http-server-config.json`. Le dossier de LM Studio est résolu comme le fait son
installateur officiel : le chemin écrit dans `~/.lmstudio-home-pointer` s'il existe, sinon `~/.lmstudio`, sinon
`~/.cache/lm-studio`. Si aucun n'existe, ou si le pointeur désigne un dossier absent, l'étape s'arrête avec
`ECHEC lmstudio_dossier_introuvable`, sans rien créer. La commande `lms` est cherchée de la même façon (`<dossier>/bin/lms`). Leurs clés et leurs valeurs ont été relevées dans l'application
LM Studio 0.4.26 :

- `fileLoggingMode` : `off`, `succinct` ou `full` (« Don't create log files » pour `off`) ;
- `verbose` ;
- `logIncomingTokens` (« Whether to log each token as they are being generated ») ;
- `logSensitiveData` : l'interface affiche son contraire sous le nom « Redact Content » (« prevent sensitive data,
  such as the content of the requests and responses from being logged »).

L'étape `lmstudio` arrête le serveur, puis règle `fileLoggingMode=off`, `verbose=false`, `logIncomingTokens=false` et
`logSensitiveData=false`, c'est-à-dire masquage du contenu actif :

- sur macOS, avec `plutil -replace`, qui garde le format JSON ;
- sous Linux, avec `python3` ;
- sous Windows, avec `ConvertFrom-Json` et `ConvertTo-Json` (voir plus haut) ;
- si le fichier est absent, il est créé avec les valeurs par défaut de l'application (`emptyHttpServerSettings`),
  journalisation coupée.

Elle redémarre ensuite le serveur et envoie une requête de test contenant `marqueur-territoria-<horodatage>-<pid>`.
Seul le nombre de fichiers de `<dossier de LM Studio>/server-logs` qui la contiennent est imprimé, jamais leur contenu.
Ce nombre doit valoir 0, sinon l'étape échoue avec `journalisation_active` et indique les réglages à faire dans
l'interface : Developer > Server Settings.

Si ce dossier de journaux est introuvable après la requête, l'étape échoue avec `journaux_lmstudio_introuvables` :
aucun contrôle n'a eu lieu, donc rien n'est conclu. Sur une installation neuve où LM Studio n'a encore jamais écrit de
journal, le message propose de créer ce dossier vide puis de relancer l'étape : le contrôle porte alors sur le dossier
réel de LM Studio. À valider en recette.

À valider en recette :

- **Lecture des réglages.** Il faut vérifier que LM Studio relit le fichier au redémarrage du serveur, et non
  seulement au lancement de l'application. Si ce n'est pas le cas, la requête marquée le révèle.
- **`lms log stream`.** Ce flux en direct pour les développeurs n'est pas un journal enregistré : il n'est pas
  contrôlé.

## Modèles (`install/modeles.json`)

| Palier | Mémoire | Identifiant `lms get` | `TERRITORIA_MODELE` | Fichier GGUF |
| --- | --- | --- | --- | --- |
| petit | moins de 12 Go | `google/gemma-4-e4b@q4_k_m` | `google/gemma-4-e4b` | `gemma-4-E4B-it-Q4_K_M.gguf` (5,34 Go) |
| moyen | de 12 à 27 Go | `google/gemma-4-12b@q4_k_m` | `google/gemma-4-12b` | `gemma-4-12B-it-Q4_K_M.gguf` (7,38 Go) |
| grand | 28 Go et plus | `google/gemma-4-26b-a4b@q4_k_m` | `google/gemma-4-26b-a4b` | `gemma-4-26B-A4B-it-Q4_K_M.gguf` (16,8 Go) |

Ce qui a été vérifié le 30 octobre 2026, sans rien télécharger :

- **Catalogue LM Studio.** Les pages `lmstudio.ai/models/google/gemma-4-e4b`, `…/gemma-4-12b`, `…/gemma-4-26b-a4b` et
  `…/gemma-4-31b` existent (HTTP 200). Chacune a pour source GGUF le dépôt
  `lmstudio-community/gemma-4-<taille>-it-GGUF`. Le cache local de LM Studio
  (`~/.lmstudio/hub/models/google/gemma-4-e4b/model.yaml`) confirme le cas du palier petit.
- **Quantification Q4_K_M.** L'API de Hugging Face liste bien les fichiers `…-Q4_K_M.gguf` de ces dépôts, avec les
  tailles ci-dessus.
- **Syntaxe de l'identifiant.** L'aide de `lms get` donne la forme `nom@quantification` (exemple :
  `qwen/qwen3.5-9b@q8_0`). Le cache local nomme les variantes GGUF en minuscules, par exemple
  `nomic-ai/nomic-embed-text-v1.5@q4_k_m`.
- **Nom dans l'API.** Le nom servi par `/v1/models` est la clé du modèle, sans la variante : le serveur de cette machine
  liste `google/gemma-4-e4b`. L'étape charge le modèle avec `lms load <id> --identifier <clé>`, puis contrôle que la
  clé figure dans `/v1/models`.

À confirmer en recette :

- **Résolution des variantes.** Que `lms get <id> --gguf --yes` résout bien la variante `@q4_k_m`, que la CLI ne
  permet pas de chercher sans télécharger.
- **Palier grand.** Le choix du modèle 26B-A4B plutôt que du 31B dense : le 26B-A4B est le plus proche des « environ
  27 milliards » de la spécification, et sa structure à 4 milliards de paramètres actifs promet une latence plus
  faible. Le 31B (`google/gemma-4-31b@q4_k_m`, 18,69 Go) reste l'autre choix.
- **Seuils des paliers.** Seuils de 12 et 28 Go, à ajuster selon la latence mesurée.
