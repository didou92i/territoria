# Installer TerritorIA : le guide pour débutant

Ce guide s'adresse à une personne qui n'a jamais installé de logiciel « technique » : secrétaire de mairie, agent
administratif, élu. Il suffit de savoir copier et coller une ligne. Comptez **une à deux heures**, dont une bonne partie
d'attente pendant les téléchargements.

TerritorIA s'installe **sur un poste** (Windows 10 ou 11, ou un Mac). Par défaut, tout reste sur cette machine : aucun
document, aucune question, aucune réponse ne part sur Internet. Seule exception, si vous la choisissez à l'étape 2 :
l'API de Mistral, un service d'IA en ligne (voir [`mistral.md`](mistral.md)).

Le [`README`](../README.md) reste la référence technique ; ce guide en suit les mêmes étapes, expliquées pas à pas.

## 1. Ce qu'il vous faut

- **Un ordinateur** sous Windows 10 ou 11, ou un Mac, avec **16 Go de mémoire** de préférence (8 Go fonctionnent avec
  un modèle d'IA plus petit) et **30 Go de place libre** sur le disque.
- **Les droits d'administrateur** de cet ordinateur, ou l'aide du service informatique pour la première étape.
- **Sur Mac, Homebrew** (<https://brew.sh>), à installer d'abord : l'installation s'en sert pour ajouter Podman,
  son module Compose et LM Studio s'ils manquent.
- **Une connexion à Internet** pendant l'installation (téléchargement du modèle d'IA, de 5 à 17 Go).
- **Le chiffrement du disque**, conseillé : BitLocker sous Windows, FileVault sur Mac.

## 2. Deux façons de faire

**A. Avec un assistant IA (le plus simple).** Un assistant comme Claude Code ou Codex suit pour vous le fichier
[`AGENTS.md`](../AGENTS.md) du dépôt. Il explique chaque étape, vous demande votre accord avant de lancer quoi que ce
soit, et **ne lit jamais vos données**. Les mots de passe et identifiants, c'est toujours vous qui les tapez.

1. Faites la partie 3 ci-dessous (récupérer TerritorIA).
2. Ouvrez l'assistant dans le dossier `territoria`.
3. Écrivez-lui : « Installe TerritorIA sur ce poste en suivant AGENTS.md. »

**B. Seul, une commande après l'autre.** Suivez les parties 3 à 6 de ce guide.

## 3. Récupérer TerritorIA

TerritorIA se récupère avec **git**, l'outil qui permettra aussi de le mettre à jour plus tard. Ne passez pas par le
bouton « Download ZIP » de GitHub : une copie en ZIP ne pourrait pas être mise à jour.

### Ouvrir une fenêtre de commandes

- **Windows** : menu Démarrer, tapez « PowerShell », ouvrez **Windows PowerShell**.
- **Mac** : ouvrez le Finder, puis Applications › Utilitaires › **Terminal**.

Dans cette fenêtre, on colle une ligne (clic droit sous Windows, Cmd + V sur Mac), puis on appuie sur Entrée.

### Installer git (une seule fois)

- **Windows** :

  ```powershell
  winget install --exact --id Git.Git
  ```

  Fermez ensuite la fenêtre PowerShell et ouvrez-en une nouvelle.
- **Mac** :

  ```sh
  xcode-select --install
  ```

  Une fenêtre d'Apple s'ouvre : cliquez sur « Installer ».

### Télécharger TerritorIA

Les deux lignes suivantes créent le dossier `territoria` dans votre dossier personnel (pas dans « Documents », souvent
synchronisé avec OneDrive ou iCloud), puis s'y placent :

```powershell
cd $HOME
git clone https://github.com/didou92i/territoria.git territoria
cd territoria
```

C'est la même chose sur Mac :

```sh
cd ~
git clone https://github.com/didou92i/territoria.git territoria
cd territoria
```

Vos données seront rangées plus tard dans un dossier `territoria-donnees`, créé **à côté** de `territoria`. Ne le
déplacez pas et ne le supprimez jamais.

**Chaque fois que vous rouvrez une fenêtre de commandes**, replacez-vous dans le dossier avec `cd $HOME\territoria`
(Windows) ou `cd ~/territoria` (Mac) avant de continuer.

## 4. Installer, étape par étape

Lancez les étapes **dans l'ordre**, une à la fois. Chacune se termine par une dernière ligne :

- `ETAPE … : OK` : c'est réussi, passez à la suivante ;
- `ETAPE … : ECHEC <code>` : regardez la partie 5, qui dit quoi faire pour les codes les plus fréquents.

Une étape peut toujours être relancée sans danger.

### Étape 0. Vérifier le poste (rien n'est modifié)

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\diagnostic.ps1`
- Mac : `install/diagnostic.sh`

Quelques secondes. Une liste s'affiche : `memoire_go` (la mémoire), `disque_libre_go` (la place libre), `port_8878`
(doit être `libre`), et `palier_modele`, la taille du modèle d'IA qui sera installé.

### Étape 1. Le moteur de conteneurs

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape moteur`
- Mac : `install/installer.sh --etape moteur`

De 5 à 15 minutes. Installe **Podman**, le logiciel qui fait tourner TerritorIA dans des « boîtes » isolées, s'il n'y a
pas déjà Podman ou Docker. Sous Windows, il a besoin de WSL 2 : si Windows demande de redémarrer, redémarrez, puis
relancez la même commande.

### Étape 2. L'IA : locale (recommandé) ou Mistral

Deux choix, un seul à faire :

- **IA locale** : rien ne sort du poste. C'est le choix recommandé, décrit juste en dessous.
- **Mistral (API française, en ligne)** : pour un poste modeste. Les textes partent chez Mistral, ce qui demande
  des démarches (registre, avis du DPO) : lisez d'abord [`docs/mistral.md`](mistral.md). Commande, à la place de
  celle de l'IA locale :
  - Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape mistral`
  - Mac : `install/installer.sh --etape mistral`

  Répondez `o` à la question, puis collez votre clé API (elle ne s'affiche pas). Passez ensuite à l'étape 3.

Pour l'IA locale :

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape lmstudio`
- Mac : `install/installer.sh --etape lmstudio`

De 15 minutes à plus d'une heure, selon votre connexion. Installe **LM Studio** s'il manque, puis télécharge le modèle
d'IA (5 à 17 Go), le règle pour qu'il ne serve que ce poste, et le relie à TerritorIA.

**Avant cette étape**, si LM Studio vient d'être installé : ouvrez-le une fois depuis le menu Démarrer ou le dossier
Applications, acceptez ses conditions d'utilisation, puis refermez-le. La collectivité accepte elle-même ces conditions.

### Étape 3. Les clés secrètes

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape secrets`
- Mac : `install/installer.sh --etape secrets`

Quelques minutes. Crée le dossier `territoria-donnees` et les clés qui protègent TerritorIA. Vous n'avez rien à noter.

### Étape 4. Les connecteurs publics (facultatif)

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape legifrance`
- Mac : `install/installer.sh --etape legifrance`

La question « Autoriser les connecteurs publics (Légifrance, data.gouv.fr), à activer ensuite par l'administration ? »
s'affiche. Ces connecteurs vont chercher des textes officiels à jour sur Légifrance (il faut alors un compte PISTE,
gratuit, au nom de la collectivité) et des jeux de données publics sur data.gouv.fr (sans compte). Aucun identifiant
n'est demandé à cette étape :

- tapez `o` pour pouvoir les utiliser. Rien ne part encore sur Internet : une fois TerritorIA installé, cochez
  « Activer le connecteur Légifrance » ou « Activer le connecteur data.gouv.fr » dans Administration › Connecteurs
  (voir le § 6) ;
- sinon, tapez `n` (ou Entrée) : TerritorIA passe en **mode hors ligne**. Il ne consulte ni Légifrance ni data.gouv.fr,
  et Administration › Connecteurs ne peut rien activer.

Sans Légifrance, les visas des actes viennent d'une copie datée des textes, livrée avec TerritorIA ; l'acte rédigé vous
demande de vérifier qu'ils sont toujours en vigueur. Pour changer d'avis plus tard, relancez cette étape et répondez
autrement, puis relancez TerritorIA (« Démarrer », § 7).

### Étape 5. Le premier démarrage

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape demarrer`
- Mac : `install/installer.sh --etape demarrer`

De 5 à 15 minutes. Démarre TerritorIA et télécharge les modèles de transcription des séances (environ 1,7 Go).

### Étape 6. L'orchestrateur n8n

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape n8n`
- Mac : `install/installer.sh --etape n8n`

Une à deux minutes. Prépare **n8n**, le logiciel qui enchaîne les demandes entre TerritorIA et l'IA. Rien à régler à la
main.

### Étape 7. Les exemples de démonstration

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape demo`
- Mac : `install/installer.sh --etape demo`

Quelques secondes. Ajoute des documents fictifs de la « Commune de Démoville » pour essayer TerritorIA sans vos propres
documents.

### Étape 8. La vérification complète

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape verifier`
- Mac : `install/installer.sh --etape verifier`

Jusqu'à une trentaine de minutes : TerritorIA s'essaie lui-même sur des données fictives (question à l'IA, arrêté
jusqu'au fichier Word), puis nettoie. Laissez la fenêtre ouverte jusqu'à la ligne `ETAPE verifier : OK`.

### Étape 9. Le raccourci sur le bureau

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape raccourci`
- Mac : `install/installer.sh --etape raccourci`

Quelques secondes. Un raccourci TerritorIA apparaît sur le bureau : un double-clic démarre TerritorIA et ouvre son
écran dans le navigateur.

### Étape 10. La sauvegarde automatique (conseillée)

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape sauvegarde-auto`
- Mac : `install/installer.sh --etape sauvegarde-auto`

On vous demande un dossier de destination qui existe déjà (de préférence un disque externe ou un dossier du serveur de
fichiers de la mairie), puis un mot de passe de 12 caractères au moins, à taper deux fois. Une sauvegarde chiffrée se
fera chaque jour à 12 h 30. **Notez ce mot de passe en lieu sûr** : sans lui, les sauvegardes sont illisibles.

## 5. Si une étape s'arrête sur un code

| Code affiché | Ce qu'il faut faire |
|---|---|
| `reconnexion_requise` | Normal sous Linux à la première installation : fermez votre session ou redémarrez, puis relancez la même étape. |
| `winget_absent` | Windows : installez « Programme d'installation d'application » depuis le Microsoft Store, puis relancez. |
| `homebrew_absent` | Mac : installez Homebrew (<https://brew.sh>), puis relancez la même étape. |
| `compose_absent` | Le module « Compose » du moteur de conteneurs manque. Mac : installez Homebrew (<https://brew.sh>), puis relancez l'étape 1, qui ajoute ce module. Autre système : voyez la ligne `compose_absent` d'[`AGENTS.md`](../AGENTS.md). |
| `podman_introuvable` | Windows : fermez la fenêtre PowerShell, ouvrez-en une nouvelle, replacez-vous dans le dossier, relancez. |
| `moteur_arrete` | Le moteur est installé mais arrêté : tapez `podman machine start` (ou ouvrez Docker Desktop si c'est lui qui est installé), puis relancez. |
| `lmstudio_dossier_introuvable` ou `lms_absent` | Ouvrez LM Studio une fois, refermez-le, puis relancez l'étape 2. |
| `journaux_lmstudio_introuvables` | LM Studio est tout neuf : suivez la consigne de l'étape 2 du README, ou demandez à l'assistant IA. |
| `chargement_impossible` | LM Studio n'a pas pu charger le modèle : fermez les autres logiciels gourmands en mémoire, puis relancez. |
| `terminal_requis` | Cette étape pose des questions : lancez-la vous-même dans la fenêtre de commandes. |
| `bibliotheque_etrangere` | Ce n'est pas une panne : vous avez déjà vos propres arrêtés, les exemples ne sont pas ajoutés. Passez à l'étape suivante. |

Pour tout autre code, consultez le tableau complet « Codes d'échec » d'[`AGENTS.md`](../AGENTS.md), ou demandez de
l'aide en donnant **le code seul** : n'envoyez jamais de copie de vos documents ni de vos conversations.

## 6. Premiers pas dans TerritorIA

1. Ouvrez TerritorIA par le raccourci du bureau, ou tapez `http://127.0.0.1:8878` dans votre navigateur. L'adresse ne
   fonctionne que sur ce poste : c'est voulu.
2. Cliquez sur **Administration**, en bas à gauche :
   - **Collectivité** : le nom de votre collectivité et votre **papier à en-tête** (un modèle Word), repris dans les
     arrêtés et les procès-verbaux ;
   - **Utilisateurs** : les comptes, si plusieurs personnes utilisent ce poste (dès qu'un compte existe, chacun doit se
     connecter) ;
   - **Connecteurs** : Légifrance et data.gouv.fr, désactivés par défaut. Pour avoir des visas à jour, saisissez vos
     identifiants PISTE et cochez « Activer le connecteur Légifrance ». Pour chercher des données publiques, cochez
     « Activer le connecteur data.gouv.fr » (aucun compte n'est nécessaire). En mode hors ligne (`n` à l'étape 4), les
     connecteurs restent coupés.
3. Essayez : posez une question sur un document de Démoville, ou rédigez un arrêté depuis l'écran **Arrêtés**. Relisez
   toujours ce que l'IA propose avant de signer : elle aide, elle ne décide pas.

## 7. Au quotidien

- **Démarrer** : le raccourci du bureau. Ou bien, dans le dossier `territoria` : `install/demarrer.sh --ouvrir` sur Mac,
  `powershell -NoProfile -ExecutionPolicy Bypass -File install\demarrer.ps1 --ouvrir` sous Windows.
- **Arrêter** : `install/arreter.sh` sur Mac, `powershell -NoProfile -ExecutionPolicy Bypass -File install\arreter.ps1`
  sous Windows. Rien n'est supprimé.
- **Sauvegarder à la demande** : voir « Au quotidien » dans le [`README`](../README.md) (`install/sauvegarder.sh`,
  `install\sauvegarder.ps1`).
- **Mettre à jour** : quand une nouvelle version est annoncée, lancez `install/mettre-a-jour.sh --version <version>`
  sur Mac, ou `powershell -NoProfile -ExecutionPolicy Bypass -File install\mettre-a-jour.ps1 --version <version>` sous
  Windows. Une sauvegarde est faite avant, et la version précédente est rétablie toute seule en cas d'échec.

---

TerritorIA — © 2026 Rhiad AZZABI (entreprise individuelle TerritorIA, SIREN 989 227 731). Licence EUPL 1.2 : voir
[`NOTICE.md`](../NOTICE.md) et [`EUPL-1.2-fr.txt`](../EUPL-1.2-fr.txt) (en anglais : [`LICENSE`](../LICENSE)).
