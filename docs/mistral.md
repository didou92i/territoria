# L'API de Mistral, option d'IA en ligne

TerritorIA est **100 % local par défaut** : l'IA tourne sur le poste (LM Studio) et rien ne sort. Une collectivité peut
choisir, **à l'installation**, l'API de Mistral au lieu de l'IA locale : Mistral est un fournisseur français, et un
poste modeste suffit alors, sans modèle de 5 à 17 Go à télécharger.

Ce document est écrit pour la personne qui décide (direction, élu) et pour le **délégué à la protection des données
(DPO)**. Lisez-le avant d'activer l'option.

## Ce qui part chez Mistral

Avec l'option active, TerritorIA envoie à l'API de Mistral (`https://api.mistral.ai/v1`) :

- les **questions des agents** et les **extraits de documents** retenus pour y répondre ;
- **tous les extraits des documents importés**, une fois, pour la recherche par le sens (modèle `mistral-embed`) ;
- les **données saisies** pour les arrêtés, les délibérations et les courriers, et les projets rédigés ;
- les **passages de transcription** de chaque point d'une séance, pour la teneur du procès-verbal et le compte rendu.

**Ne partent jamais** : le son des séances (la transcription reste sur le poste), les comptes et les mots de passe, les
journaux, les sauvegardes. Aucune adresse autre que celle de Mistral n'est joignable pour l'IA.

## Les démarches avant d'activer

1. Ouvrir un compte sur **La Plateforme** de Mistral (console.mistral.ai), créer une **clé API** et fixer un **plafond
   de dépense** : la facturation se fait à l'usage.
2. Lire et accepter le **contrat de sous-traitance (DPA)** de Mistral : Mistral agit comme sous-traitant de la
   collectivité.
3. Inscrire le traitement au **registre des traitements** de la collectivité.
4. Demander l'**avis du DPO**, et, si les documents traités le justifient, conduire une **analyse d'impact (AIPD)**.
5. Demander à Mistral la **zéro conservation** des données (« zero data retention »), accordée sur demande à son
   support : sans elle, les requêtes peuvent être conservées un temps pour la lutte contre les abus.

À vérifier directement chez Mistral (centre de confiance et conditions juridiques), TerritorIA ne pouvant l'affirmer à sa
place : le lieu d'hébergement des données, leur durée de conservation, leur non-utilisation pour l'entraînement des
modèles, la liste des sous-traitants de Mistral.

## Activer l'option

Pendant l'installation, l'étape `mistral` remplace l'étape `lmstudio` :

- Windows : `powershell -NoProfile -ExecutionPolicy Bypass -File install\installer.ps1 --etape mistral`
- Mac ou Linux : `install/installer.sh --etape mistral`

L'étape affiche ce qui part chez Mistral, puis pose la question « Utiliser l'API de Mistral au lieu de l'IA locale ?
[o/N] ». **Non** est la réponse par défaut : rien n'est alors écrit. Si vous répondez **o**, collez la clé API : elle ne
s'affiche pas, et elle est rangée dans les secrets de l'installation, lisibles par votre seul compte. Le choix prend
effet au démarrage suivant (étape `demarrer`) ; LM Studio n'est alors plus lancé.

Ensuite, dans **Administration › Intelligence artificielle** :

- le modèle de rédaction (par défaut `mistral-medium-latest`) se choisit dans la liste des modèles de Mistral. Évitez les
  petits modèles (`mistral-small`) : ils inventent des dates ou des numéros de rue, que TerritorIA refuse dans les
  arrêtés (« Le modèle a ajouté un nombre ou une date non fourni ») ;
- la clé peut y être remplacée, sans jamais être réaffichée ;
- la recherche par le sens utilise `mistral-embed`.

En haut de chaque page, « IA en ligne : Mistral » rappelle le choix fait.

Pour vérifier la clé, cliquez sur **Enregistrer et tester** dans le même écran : « Serveur joignable et modèle présent
dans le catalogue » confirme la clé et le modèle. Sinon, le message dit quoi corriger (« Clé Mistral refusée :
remplacez-la dans Administration › Intelligence artificielle. », « Crédits ou quota Mistral épuisés. », ou la raison
donnée par Mistral). Posez ensuite une question sur un document, puis rédigez un arrêté jusqu'au
fichier Word.

## Revenir à l'IA locale

Lancez l'étape `lmstudio` (`install/installer.sh --etape lmstudio`, ou `install\installer.ps1 --etape lmstudio` sous
Windows) : elle installe LM Studio et son modèle, puis retire l'option Mistral et efface la clé de l'installation.
Redémarrez ensuite TerritorIA (étape `demarrer`) : au démarrage, la clé saisie dans l'écran est effacée aussi. Plus rien
ne part.

---

TerritorIA — © 2026 Rhiad AZZABI (entreprise individuelle TerritorIA, SIREN 989 227 731). Licence EUPL 1.2 : voir
[`NOTICE.md`](../NOTICE.md).
