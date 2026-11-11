// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
// Après une réponse, la page défile jusqu'à son début. Sur un écran étroit ou peu haut, la zone de saisie fixée en bas
// cacherait alors les boutons d'un projet d'acte (« Télécharger le Word ») : ce sont eux qui deviennent la cible.
// Positions mesurées avant le défilement, dans le même repère ; `marge` reprend le scroll-margin-top des messages.
export function cibleDefilement({debutHaut, actionsBas, hauteurFenetre, hauteurSaisie, marge = 90}) {
  if (actionsBas == null) return 'debut';
  return actionsBas - debutHaut > hauteurFenetre - marge - hauteurSaisie ? 'actions' : 'debut';
}
