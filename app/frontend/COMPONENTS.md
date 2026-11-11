# Composants 21st.dev utilisés

Les composants sont compilés dans `public/21st-components.js` et exécutés sans accès réseau.

- **Prompt Input** et **Message Thread** — bibliothèque Hirael de Mohammad Shehadeh, licence MIT. Le code d'origine est conservé dans `src/vendor/hirael/upstream/` et des sous-ensembles adaptés sont utilisés dans `src/vendor/hirael/` : classes raccordées à la charte locale, traduction des libellés, suppression des fonctions de démonstration non utilisées. Source : https://21st.dev/@mohammadshehadeh/components/message-thread et dépôt https://github.com/MohammadShehadeh/hirael/tree/306000d7b017d766615beb85edbabd9adf146126
- **Citation** — Redrob Labs, licence MIT ; code d'origine et utilitaire conservés dans `src/vendor/redrob/`. Une variante compacte affiche le numéro de source et ouvre la copie locale du document à la page indiquée. Source : https://21st.dev/@redrob-labs/components/citation et dépôt https://github.com/redrob-labs/redrob-ui/tree/89fc3c93011eae90f41c75919e8ed09fed548f95

Les réponses utilisent aussi `react-markdown` et `remark-gfm` pour afficher les titres, listes et tableaux sans exposer leur syntaxe ; le texte enregistré reste inchangé. Les citations sont reliées aux sources correspondantes. Les images distantes ne sont pas chargées.

Les textes des licences des composants figurent dans chaque dossier `vendor/` ; les notices des dépendances sont rassemblées dans `THIRD_PARTY_LICENSES.txt`. Les versions sont épinglées dans `package-lock.json`.

## Recompiler

```sh
cd frontend
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm run test:ui
```

L'authentification du serveur MCP `magic` a échoué au moment de cette intégration. Les composants ont donc été récupérés depuis les dépôts publics indiqués par 21st.dev, et non au moyen d'une session MCP authentifiée.
