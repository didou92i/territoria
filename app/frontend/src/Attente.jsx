// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
// Message pendant la rédaction : il dit où partent les textes (démonstration et option Mistral : IA en ligne).
export function texteAttente(enLigne) {
  return enLigne
    ? {titre: 'L’IA en ligne rédige la réponse…',
       detail: 'Les textes sont envoyés au service d’IA en ligne. Comptez de quelques secondes à une minute, un peu plus pour un arrêté.'}
    : {titre: 'Le modèle local rédige la réponse…',
       detail: 'Tout reste sur ce poste. Comptez 30 secondes à 1 minute, un peu plus pour un arrêté.'};
}
