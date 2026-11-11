// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ResponseContent} from '../src/ResponseContent';
import {ArreteDocument} from '../src/ArreteDocument';
import {texteAttente} from '../src/Attente';
import {cibleDefilement} from '../src/Defilement';

const sources=[{id:'document-test',title:'Fichier test.pdf',page:2}];
const render=(text,refs=sources)=>renderToStaticMarkup(<ResponseContent text={text} sources={refs}/>);

test('met en forme les réponses et préserve les citations liées aux pages',()=>{
  const html=render('Introduction\n\n**Actions :**\n\n* **Premier point :** contenu [1].\n\n| Mesure | Nombre |\n| --- | --- |\n| Exemple | 3 |');
  assert.match(html,/<strong>Actions :<\/strong>/);
  assert.match(html,/<ul>/);assert.match(html,/<li>/);assert.match(html,/<table>/);
  assert.match(html,/href="\/api\/documents\/document-test\/file#page=2"/);
  assert.match(html,/aria-label="Source 1: Fichier test.pdf, page 2"/);
  assert.doesNotMatch(html,/\*\*Actions/);
});

test('ne transforme pas une référence absente et ne crée pas de lien imbriqué',()=>{
  const html=render('Référence absente [9]. [texte [1]](https://example.org) et `[1]`.');
  assert.match(html,/\[9\]/);assert.match(html,/<code>\[1\]<\/code>/);
  assert.doesNotMatch(html,/href="\/api\/documents\/document-test/);
});

test('ne charge pas les images externes et neutralise le HTML ou les liens dangereux',()=>{
  const html=render('<img src=x onerror=alert(1)>\n\n![externe](https://example.org/image.png)\n\n[dangereux](javascript:alert%281%29)');
  assert.doesNotMatch(html,/<img\b|onerror|href="javascript:/i);
  assert.match(html,/Image : externe/);
});

test('affiche tous les articles et le Word du même arrêté sans Markdown brut',()=>{
  const artifact={download:'/api/arretes/'+'a'.repeat(32)+'/file',document:{title:'Arrêté du maire',collectivite:'Senlis',objet:'Stationnement',autorite:'Le Maire',visas:['Vu les textes fournis'],considerants:['Considérant les faits.'],articles:Array.from({length:6},(_,i)=>({number:i+1,title:'Disposition',texte:'Texte complet '+(i+1)})),lieu_date:'À compléter',signature:'Non signé',logo:{url:'https://example.org/logo.png'}}};
  const html=renderToStaticMarkup(<ArreteDocument artifact={artifact}/>);
  assert.match(html,/Article 6/);assert.match(html,/Texte complet 6/);assert.match(html,/Télécharger le Word/);
  assert.doesNotMatch(html,/<img/);
});

test('le message d’attente dit où partent les textes',()=>{
  // Test de la démonstration : « Le modèle local rédige… Tout reste sur ce poste » s'affichait alors que l'IA est en ligne.
  const enLigne=texteAttente(true), local=texteAttente(false);
  assert.match(enLigne.titre,/en ligne/);assert.doesNotMatch(enLigne.titre+enLigne.detail,/poste|local/);
  assert.match(enLigne.detail,/envoyés/);
  assert.match(local.titre,/modèle local/);assert.match(local.detail,/Tout reste sur ce poste/);
});

test('les boutons d’un projet cachés sous la zone de saisie deviennent la cible du défilement',()=>{
  // Fenêtre de 700 px, zone de saisie de 220 px : il reste 700 − 90 − 220 = 390 px sous l’en-tête.
  assert.equal(cibleDefilement({debutHaut:1000,actionsBas:1300,hauteurFenetre:700,hauteurSaisie:220}),'debut');
  assert.equal(cibleDefilement({debutHaut:1000,actionsBas:1420,hauteurFenetre:700,hauteurSaisie:220}),'actions');
  assert.equal(cibleDefilement({debutHaut:1000,actionsBas:null,hauteurFenetre:700,hauteurSaisie:220}),'debut');
});
