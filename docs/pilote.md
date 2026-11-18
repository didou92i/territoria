# Pilote sur un poste : fiche de recette

Un ou deux agents essaient TerritorIA **0.2.2** pendant **2 à 4 semaines** sur le poste individuel retenu par la collectivité. La DSI consigne la recette technique et les agents valident les fichiers qu’ils ont réellement ouverts.

## Le poste

- Windows 10 ou 11 à jour ; droits d'administrateur pour l'installation (service informatique).
- **16 Go de mémoire au moins** : palier de modèle « moyen » (environ 12 milliards de paramètres) et transcription des
  séances. Sur 8 Go, le palier « petit » fonctionne pour les actes et les questions, mais la transcription (jusqu'à 3 Go
  pour son conteneur) est déconseillée.
- **25 Go de disque libre** : modèle d'IA (7,4 Go au palier moyen), images des conteneurs (environ 2,5 Go), modèles de
  transcription (1,7 Go), données et sauvegardes.
- Vérifier BitLocker sur le volume qui héberge `territoria-donnees`. Sous macOS, le diagnostic interroge FileVault sur ce volume. Sous Linux, il suit les périphériques du point de montage ext2/3/4 ou XFS. Si le résultat est `inconnu` (permissions, partage réseau, montage complexe), faire vérifier ce stockage par la DSI avant le premier import réel. Vérifier séparément la destination des sauvegardes.
- Prévoir Internet pour l’installation. Ensuite, les connexions dépendent du fournisseur IA et des connecteurs activés. Commencer avec des données fictives. Avant tout dossier réel, consigner avec le DPO le fournisseur et les destinataires, les personnes autorisées et la durée de conservation, sauvegardes comprises.
- Moteur de conteneurs : Podman, choisi par défaut (Docker Desktop est payant pour les administrations). LM Studio est
  installé par la collectivité, qui en accepte les conditions.
- Facultatif : identifiants PISTE de la collectivité pour les visas Légifrance, saisis par l'agent lui-même
  (Administration › Connecteurs).

## Installation

Suivre le [README](../README.md), « Installation pas à pas », étapes 0 à 10, à la main ou avec un assistant IA
([`AGENTS.md`](../AGENTS.md)). Garder :

- la sortie du diagnostic (étape 0 : onze lignes `clé=valeur`, aucune donnée personnelle) ;
- la dernière ligne de chaque étape (`ETAPE <nom> : OK` ou `ECHEC <code>`) ;
- la durée de la vérification de bout en bout (étape 8).

## Redémarrage et restauration sur le poste cible

1. Importer un Word fictif portant un repère unique. Relever la version du pied de page et vérifier avec la DSI le chiffrement du volume des données et la protection de la destination de sauvegarde.
2. Redémarrer le poste. Suivre le démarrage habituel, ouvrir TerritorIA et retrouver le document. Noter les interventions manuelles éventuelles.
3. Créer une sauvegarde chiffrée avec `install\sauvegarder.ps1 --destination E:\Sauvegardes` (adapter le lecteur). Conserver le chemin absolu de l’archive `.tsv1` et vérifier que le mot de passe est disponible pour la reprise.
4. Depuis la racine du dépôt, ouvrir **une nouvelle fenêtre PowerShell**. Choisir un dossier de recette neuf, sur un volume protégé, en dehors du dépôt et du dossier de données en service. Exemple à adapter :

   ```powershell
   $env:TERRITORIA_DONNEES_HOTE = 'D:\Recette-TerritorIA\restauration-001'
   if (Test-Path -LiteralPath $env:TERRITORIA_DONNEES_HOTE) { throw 'Choisir un chemin neuf.' }
   $archive = Read-Host 'Chemin absolu de la sauvegarde .tsv1'
   powershell -NoProfile -ExecutionPolicy Bypass -File install\restaurer.ps1 --archive $archive
   ```

5. Après `ETAPE restaurer : OK`, un agent ouvre **la copie restaurée** du Word dans le sous-dossier `territoria\documents` de la cible. Il vérifie le repère et le contenu. Fermer cette fenêtre PowerShell. La DSI consigne la date, le résultat et la durée de reprise.

## Essais des agents

Pour chaque essai, noter le résultat, la durée et toute correction nécessaire. Commencer avec des noms et des pièces fictifs.

| Parcours | Ce que l’agent vérifie |
|---|---|
| Arrêté | Rédiger un arrêté temporaire et poser une question de suivi. Ouvrir le Word et vérifier la collectivité, le signataire, les dates, les visas et le service chargé de l’exécution. |
| Délibération avec vote | Saisir par exemple 15 membres en exercice, 11 présents, 1 pouvoir, puis 8 pour, 3 contre et 1 abstention. Comparer chaque effectif et chaque voix au Word final. |
| Réponse sourcée | Importer des pièces de référence, poser une question dont la réponse est connue, ouvrir la source citée et retrouver le passage qui justifie la réponse. |
| Dossier anonymisé | Placer un nom fictif dans le corps et l’objet d’une délibération. Après masquage et validation, ouvrir le PDF, l’index HTML et le CSV du ZIP. Vérifier la disparition du nom et la conservation du dispositif et des annexes attendues. [Guide de publication](publication.md) |
| Registre | Attribuer un numéro à un acte et le retrouver dans l’export CSV. |
| Audio, si retenu | Traiter une réunion représentative, vérifier les décisions et les intervenants à l’écoute, puis ouvrir l’export Word. Noter la durée de traitement. |

## Limites du pilote individuel

- Un seul poste : le mode équipe (accès depuis plusieurs postes, HTTPS, annuaire) n'est pas encore livré.
- Séances de **2 heures au plus** ; au-delà, découper l'enregistrement. La séparation des voix crée souvent plus de
  groupes que d'intervenants : on les fusionne à la relecture (sur 2 h à trois voix : 15 groupes, 94 % de la parole bien
  attribuée après fusion).
- Tout acte rédigé est un projet : un agent compétent le relit et vérifie les textes visés.
- Temps de réponse selon le poste : 3 à 54 s par réponse avec le modèle moyen sur un Mac M1 Pro ; un PC sans carte
  graphique adaptée sera plus lent. Une séance de 2 h demande environ 2 h de calcul, pendant lesquelles le poste reste
  utilisable mais ralenti.
- Avant d'y mettre des données réelles : avis du délégué à la protection des données (registre des traitements, analyse
  d'impact pour les enregistrements de séances) et information des personnes enregistrées.

## À rapporter à la fin du pilote

- les lignes `ETAPE … : ECHEC <code>` rencontrées, avec le moment où elles sont apparues ;
- la sortie du diagnostic et les durées notées ;
- les retours 👍/👎 et leurs commentaires ;
- les actes ou fonctions qui ont manqué.

## Décision à consigner dans la collectivité

| Validation | Résultat, date et fonction du validateur |
|---|---|
| DSI — chiffrement, redémarrage, restauration séparée et ouverture de la pièce avec l’agent | À renseigner |
| DPO — fournisseur IA, accès et conservation avant les données réelles | À renseigner |
| Agents — Word, passage cité et dossier anonymisé relus | À renseigner |
| Audio — résultat ou hors périmètre | À renseigner |
| Responsable du pilote — ouverture, prolongation ou report et écarts restant à corriger | À renseigner |

Conserver les pièces de recette dans la collectivité. Un compte rendu anonymisé peut mentionner la version, les résultats et les écarts sans joindre de documents métier.
