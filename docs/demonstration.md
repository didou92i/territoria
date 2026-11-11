# Serveur de démonstration de TerritorIA

Ce guide installe une **démonstration en ligne** de TerritorIA sur un serveur loué (VPS). Elle permet de faire essayer
la plateforme à distance, avant qu'une collectivité l'installe chez elle, tout en local, avec le
[guide pour débutant](guide-debutant.md).

Sur ce serveur seulement :

- **L'IA passe par OpenRouter**, un service d'IA en ligne. C'est la seule installation de TerritorIA où des textes
  quittent la machine. Chaque requête demande à OpenRouter d'écarter les fournisseurs qui conservent ou réutilisent les
  données, mais elles partent quand même sur Internet, souvent hors de l'Union européenne.
- **Données fictives uniquement.** Un bandeau le rappelle sur chaque page, l'écran de connexion le dit avant
  d'entrer (avec le nom du service d'IA et un lien vers les mentions légales), et chaque Word produit porte la
  mention « Document de démonstration — sans valeur juridique ».
- **Accès visiteur sans compte** : le bouton « Essayer sans compte, en visiteur » ouvre un espace d'essai séparé,
  plafonné à 15 demandes à l'IA par visiteur, 300 par jour pour l'ensemble des visiteurs et 30 nouveaux visiteurs
  par heure. Ces plafonds tiennent aussi quand plusieurs demandes arrivent en même temps, et avec n8n comme sans.
  Le premier administrateur se crée sur le serveur. Les identifiants qui commencent par « visiteur- » sont réservés
  à ces essais : l'administration ne peut pas créer de compte sous ce nom.
- **Ce qu'un visiteur dépose reste petit et ne dure pas** :
  - 3 documents importés et 3 actes à publier au plus (déposés, ou repris du registre), de 5 Mo et
    300 000 caractères chacun, dans un espace qu'il a créé ;
  - 150 documents au plus pour l'ensemble des visiteurs ;
  - 5 espaces, 5 assistants personnels et 80 messages au plus par visiteur ;
  - aucun partage, et aucun nom des autres comptes ;
  - réservés aux comptes : la transcription des séances (une séance accepte 1 Gio d'audio), les connecteurs
    Légifrance et data.gouv.fr, et le signalement d'une réponse à l'administration ;
  - il cherche par mots-clés : ni ses questions de recherche ni les extraits de ses documents ne sont envoyés au
    service d'IA pour la recherche par le sens, qui se montre avec un compte ;
  - son compte et tout ce qu'il contient sont **effacés une fois passées 24 heures** après sa création : au
    premier passage de l'application ensuite (une fois par heure au plus, ou à son réveil si elle était en veille).
    Au registre des actes, le numéro reste et l'objet saisi est remplacé par « Acte d'essai effacé ». Une
    sauvegarde faite entre-temps garde ces contenus : ne conservez pas les sauvegardes d'un serveur de démonstration.
- **Hors des moteurs de recherche, et en HTTPS seulement** : chaque réponse du serveur de démonstration porte
  `X-Robots-Tag: noindex, nofollow` et, en HTTPS, `Strict-Transport-Security`.

## 1. Ce qu'il faut

- Un **VPS Linux** (Ubuntu 24.04 ou Debian 12), avec un accès SSH et les droits d'administrateur (`sudo`) :
  - 4 Go de mémoire suffisent, 8 Go si vous voulez faire essayer la transcription des séances ;
  - 30 Go de disque.
- Un **nom de domaine** dont vous gérez la zone DNS, par exemple `demo.votre-domaine.fr`, avec un enregistrement `A`
  qui pointe vers l'adresse IP du VPS. Attendez que le nom réponde avant l'installation.
- Un **compte OpenRouter** (openrouter.ai) et une **clé API à plafond de crédits** : dans OpenRouter, Keys › Create
  Key, fixez une limite de crédits. Les testeurs ne pourront jamais dépenser plus que ce plafond.

## 2. Préparer le serveur

Connecté en SSH, ouvrez seulement les ports utiles, SSH, HTTP (pour le certificat) et HTTPS, puis installez git :

```sh
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo apt-get update
sudo apt-get install -y git
```

## 3. Installer

Chaque commande se termine par une ligne `ETAPE … : OK`. En cas d'échec, le code affiché est expliqué dans le tableau
« Codes d'échec » d'[`AGENTS.md`](../AGENTS.md).

```sh
git clone https://github.com/didou92i/territoria.git territoria
cd territoria
install/installer.sh --etape moteur
```

La première fois, l'étape `moteur` se termine par `ECHEC reconnexion_requise` : c'est normal. Fermez la session SSH,
reconnectez-vous, revenez dans le dossier `territoria` et relancez la même commande.

```sh
install/installer.sh --etape secrets
install/installer.sh --etape legifrance
install/demonstration.sh --domaine demo.votre-domaine.fr
install/installer.sh --etape demarrer
install/installer.sh --etape n8n
install/installer.sh --etape demo
```

- À l'étape `legifrance`, répondez `n` si vous n'avez pas d'identifiants PISTE.
- `install/demonstration.sh --domaine` active la démonstration pour votre nom de domaine. Elle doit précéder
  `demarrer`, qui lance alors aussi Caddy : il obtient seul le certificat HTTPS de Let's Encrypt.
- L'étape `lmstudio` est inutile ici : l'IA vient d'OpenRouter.
- L'étape `demo` installe les documents fictifs de la « Commune de Démoville ».

Créez ensuite **votre compte administrateur**. Le mot de passe (12 caractères au moins) est demandé en saisie masquée :

```sh
install/demonstration.sh --premier-administrateur admin --nom "Prénom Nom"
```

## 4. Régler OpenRouter

1. Ouvrez `https://demo.votre-domaine.fr` et connectez-vous avec le compte `admin`.
2. Allez dans **Administration › Intelligence artificielle**.
3. Choisissez le fournisseur **OpenRouter (en ligne, démonstration)**, collez votre clé API et enregistrez. La clé est
   gardée sur le serveur et ne sera plus jamais affichée.
4. La liste **Modèles disponibles** montre alors les modèles d'OpenRouter qui produisent les réponses structurées dont
   TerritorIA a besoin, et vos crédits restants. Cliquez sur **Choisir** à côté du modèle de rédaction voulu, puis
   enregistrez. Conseil tiré de la recette : **`mistralai/mistral-medium-3-5`** respecte les garde-fous des arrêtés ;
   les petits modèles (par exemple `mistral-small`) inventent des dates ou des numéros de rue, que TerritorIA refuse
   (« Le modèle a ajouté un nombre ou une date non fourni »).
5. Dans **Recherche par le sens**, sur la même page, le modèle de plongements est choisi automatiquement
   (`mistralai/mistral-embed-2312` s'il est proposé) ; vous pouvez en choisir un autre.
6. Testez : posez une question sur un document de Démoville, puis rédigez un arrêté jusqu'au fichier Word.

## 5. Ouvrir la démonstration aux testeurs

- **Administration › Utilisateurs** : créez un compte par testeur (rôle **Agent**). Le mot de passe provisoire n'est
  affiché qu'une fois : transmettez-le au testeur, qui en choisira un autre à sa première connexion.
- Rappelez à chaque testeur la règle : **données fictives seulement**. Ni nom réel, ni document réel de collectivité.
- Pour retirer un accès, désactivez le compte dans la même page.
- Surveillez les crédits restants dans Administration › Intelligence artificielle.

## 6. Au quotidien

- **Mettre à jour** : `install/mettre-a-jour.sh --version <version>`, depuis le dossier `territoria`. Une sauvegarde
  est faite avant, et la version précédente revient seule en cas d'échec.
- **Arrêter** : `install/arreter.sh`. **Redémarrer** : `install/demarrer.sh`.
- **Mot de passe administrateur perdu ou compte bloqué** : la commande de secours demande un nouveau mot de passe, en
  saisie masquée, et débloque le compte :

  ```sh
  sudo docker exec -it territoria-territoria-1 python -m outils reinitialiser-acces admin
  ```
- **Système** : gardez Ubuntu à jour (`sudo apt-get upgrade`), et préférez une connexion SSH par clé plutôt que par
  mot de passe.

## 7. Et ensuite

Une collectivité convaincue installe TerritorIA **chez elle**, sans OpenRouter : sur un poste, avec le
[guide pour débutant](guide-debutant.md). L'IA tourne alors sur sa machine, et plus aucun texte ne la quitte.

---

TerritorIA — © 2026 Rhiad AZZABI (entreprise individuelle TerritorIA, SIREN 989 227 731). Licence EUPL 1.2 : voir
[`NOTICE.md`](../NOTICE.md).
