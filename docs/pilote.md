# Pilote sur un poste : fiche de recette

But : faire vivre TerritorIA 0.1 sur un vrai poste Windows de la collectivité, pendant 2 à 4 semaines, avec un ou deux
agents, et rapporter de quoi décider de la suite (mode équipe, réglages, trames manquantes).

## Le poste

- Windows 10 ou 11 à jour ; droits d'administrateur pour l'installation (service informatique).
- **16 Go de mémoire au moins** : palier de modèle « moyen » (environ 12 milliards de paramètres) et transcription des
  séances. Sur 8 Go, le palier « petit » fonctionne pour les actes et les questions, mais la transcription (jusqu'à 3 Go
  pour son conteneur) est déconseillée.
- **25 Go de disque libre** : modèle d'IA (7,4 Go au palier moyen), images des conteneurs (environ 2,5 Go), modèles de
  transcription (1,7 Go), données et sauvegardes.
- BitLocker activé (le diagnostic signale son absence).
- Accès à Internet pendant l'installation ; ensuite, seule la connexion à Légifrance sert (facultative).
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

## À essayer pendant le pilote

Pour chaque essai : la durée, ce qu'il a fallu corriger, et un 👍 ou un 👎 dans l'application (Administration › Retours
les rassemble).

| Fonction | Essai |
|---|---|
| Questions sur des documents | importer 3 à 5 documents réels dans un espace, dont un PDF scanné ; poser 5 questions ; vérifier les sources citées |
| Arrêtés | un arrêté de police temporaire (travaux ou occupation du domaine public), un arrêté RH (échelon ou temps partiel), une délégation de signature ; vérifier les visas, puis l'export Word avec le papier à en-tête |
| Délibération | une délibération courante ; visas et structure |
| Registre des actes | attribuer un numéro à un acte ; export CSV du registre |
| Transcription des séances | une réunion courte (15 à 30 min), puis une séance d'1 à 2 h ; noter la durée de traitement, la qualité du texte, le nombre d'intervenants à fusionner ; valider, puis exporter en Word |
| Sauvegarde | une sauvegarde vers un disque externe (`install\sauvegarder.ps1 --destination E:\Sauvegardes`, ou l'étape 10 pour l'automatiser) ; elle se termine par `ETAPE sauvegarder : OK` |

## Limites connues de la version 0.1

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
