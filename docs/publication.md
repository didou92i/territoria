# Publier les actes : le guide de l'agent

Depuis le 1er juillet 2022, les actes de la collectivité sont rendus publics **sur son site internet**, en entier, dans
un format non modifiable, avec leur date de mise en ligne. Avant cela, il faut retirer les données personnelles qui n'ont
pas à être diffusées. La page **Publication** de TerritorIA prépare tout cela, **sans rien envoyer sur Internet** : vous
déposez ensuite vous-même le dossier sur le site.

Ce guide n'est pas un avis juridique.

## Une fois pour toutes : les réglages

Dans **Administration › Collectivité** (administrateur) :
- **SIRET de la collectivité** : obligatoire pour le fichier national des délibérations ;
- **Adresse des actes sur votre site** (par exemple `https://www.mairie-exemple.fr/actes/`) : facultative, elle sert aux
  liens de la page d'index et du fichier national.

## Le parcours

1. **Ajouter un acte**, page Publication :
   - **un acte du registre** : son numéro (celui attribué par TerritorIA), la date de l'acte et, pour une délibération,
     sa matière ;
   - ou **la version signée** : le fichier (Word, ou PDF texte), le type, le numéro, la date, l'objet et la matière.
2. **Repérer les données personnelles** : TerritorIA propose les passages à masquer :
   - par des règles sûres : courriels, téléphones, adresses, dates de naissance, n° de sécurité sociale, IBAN,
     immatriculations. Une adresse n'est cochée d'office que si elle suit une personne (« demeurant », « M. ») : celle
     d'un arrêté de police (« au droit du 4 rue de la République ») reste visible, sauf si vous la cochez ;
   - par l'IA : les noms de personnes, gardés seulement s'ils figurent dans l'acte, et le nom de famille seul (« M.
     MARTIN » plus bas). Les particuliers sont cochés ; les élus et les agents, dont le nom se publie en principe, sont
     décochés. Si l'IA n'a pas pu chercher les noms, un avertissement reste affiché jusqu'à la validation.
3. **Relire** : en jaune, ce qui sera masqué ; en gris, ce qui restera visible. Cochez, décochez, ou sélectionnez un passage
   dans le texte puis cliquez **Masquer la sélection**. Un passage coché est masqué **partout** dans l'acte, objet compris.
   **Lisez l'acte en entier** : l'IA peut oublier un nom.
4. **Valider** : réservé au référent juridique et à l'administrateur, après le repérage. Un passage coché introuvable
   dans le texte bloque la validation. Une modification après validation renvoie l'acte en relecture. Un acte saisi par
   erreur se supprime tant qu'il n'est pas publié.
5. **Préparer le dossier** : cochez les actes validés, choisissez la date de mise en ligne, et téléchargez le ZIP :
   - un **PDF anonymisé** par acte, avec en pied de page « Acte publié sous forme électronique le … — document anonymisé
     (RGPD) » ;
   - **`index.html`** : la liste de tous vos actes publiés, à mettre sur le site ;
   - **`deliberations.csv`** : le fichier national des délibérations (schéma « Délibérations » 2.1.6), pour votre espace
     open data ou data.gouv.fr ;
   - **`LISEZ-MOI.txt`** : où déposer chaque fichier.
6. **Déposer** les fichiers sur le site, le jour choisi, puis cliquer **Mis en ligne** sur chaque acte : la date retenue
   est celle du dossier, imprimée sur le PDF. Un acte publié ne se modifie plus. **Avant de déposer, ouvrez chaque PDF**
   et vérifiez qu'aucune donnée personnelle ne reste visible : le repérage peut manquer un nom écrit « NOM Prénom » ou
   des mots collés par un saut de ligne du Word.

**Retirer un acte publié par erreur** (donnée personnelle restée visible, mauvais fichier…) : ouvrez l'acte, cliquez
**Retirer de la publication** et indiquez le motif (référent juridique et administrateur). L'acte sort de l'index et du
fichier national des **prochains** dossiers. TerritorIA ne touche pas à votre site : **retirez vous-même le PDF** et
redéposez l'index et le fichier national à jour. L'acte retiré reste dans la liste, avec son motif ; son PDF et son
empreinte restent conservés, et il ne peut plus être modifié ni supprimé.

TerritorIA garde chaque PDF publié et son empreinte : on peut prouver plus tard que le fichier en ligne est celui qui a été
validé. Le même acte, à la même date, donne toujours le même fichier.

## Limites

- Le PDF reprend le **texte intégral** de l'acte avec une mise en page simple ; le papier à en-tête d'origine n'est pas
  reproduit.
- Un **PDF scanné** n'est accepté que si la lecture optique (OCR) de l'installation sait le lire ; sinon, déposez la version
  Word ou un PDF texte.
- Les votes des délibérations ne se saisissent pas encore : les colonnes de votes du fichier national restent vides
  (elles sont facultatives).
- TerritorIA ne dépose rien lui-même sur le site ni sur data.gouv.fr.

---

TerritorIA — © 2026 Rhiad AZZABI (entreprise individuelle TerritorIA, SIREN 989 227 731). Licence EUPL 1.2 : voir
[`NOTICE.md`](../NOTICE.md).
