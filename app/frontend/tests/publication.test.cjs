// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const application = fs.readFileSync(path.resolve(__dirname, '../../public/app.js'), 'utf8');
const acte = {id:'fic1', cree_par:'v1', type:'deliberation', numero:'D-07', date_acte:'2026-11-18',
  objet:'🌿 Kalune Testive', paragraphes:['Petitville et Petit.'], zones_relecture:[[],[[14,19,true]]],
  statut:'a_relire', revision:7, passages:[]};
function interfacePublication() {
  const ecoutes = {}, controles = new Map(), requetes = [], erreurs = [], fichiers = [];
  const controle = selecteur => {
    if (!controles.has(selecteur)) controles.set(selecteur, {focus(){this.focusRecu=true;}});
    return controles.get(selecteur);
  };
  const document = {
    addEventListener(type, listener){(ecoutes[type] ??= []).push(listener);},
    querySelector:controle,
    createElement(){return {click(){fichiers.push(this.download);},remove(){}};},
    body:{append(){}}
  };
  const ctx = vm.createContext({document, window:{}, URL:{createObjectURL:()=>'/fictif', revokeObjectURL(){}},
    avis:msg=>erreurs.push(msg), fetch:(url,options)=>{
      if (url==='/api/session') return new Promise(()=>{}); // pas d'initialisation réseau
      const corps=JSON.parse(options.body);
      requetes.push([url,corps]);
      return Promise.resolve({ok:true,blob:async()=>({}),json:async()=>({...acte,revision:corps.revision+1,passages:corps.passages,zones_relecture:[[[2,Array.from(acte.objet).length,corps.passages[0].coche]],acte.zones_relecture[1]]})});
    }});
  const run = s=>vm.runInContext(s,ctx);
  vm.runInContext(application,ctx);
  ctx.fiche={...acte};run("state.view='publication';state.publication=fiche;render=()=>{};toast=avis");
  const click = action => ecoutes.click[0]({target:{closest:()=>({dataset:{action}})}});
  const submit = passage => ecoutes.submit[0]({preventDefault(){},target:{id:'publication-passage-form',elements:{passage:{value:passage}}}});
  return {run,ctx,click,submit,ecoutes,controle,requetes,erreurs,fichiers};
}
test('masquer au clavier un nom uniquement dans l’objet et afficher la nouvelle relecture',async()=>{
  const ui=interfacePublication();
  assert.match(ui.run('publicationDetail()'), /<label[^>]*for="pub-passage-manuel"[^>]*>[^<]+/);
  await ui.submit('Kalune Testive');
  assert.equal(ui.requetes[0][0],'/api/publications/fic1/passages');
  assert.equal(ui.requetes[0][1].revision,7);
  assert.deepEqual(ui.requetes[0][1].passages,[{texte:'Kalune Testive',categorie:'manuel',origine:'manuel',coche:true}]);
  assert.match(ui.run('publicationDetail()'),/🌿 <mark class="pub-mark">Kalune Testive<\/mark>/);
  assert.match(ui.run('publicationDetail()'),/Petitville et <mark class="pub-mark">Petit<\/mark>/);
  assert.ok(ui.controle('#pub-passage-manuel').focusRecu);
  await ui.ecoutes.change[0]({target:{dataset:{change:'passage-coche',index:'0'},checked:false}});
  assert.equal(ui.requetes.at(-1)[1].passages[0].coche,false);
  assert.equal(ui.requetes.at(-1)[1].revision,8);
  assert.match(ui.run('publicationDetail()'),/🌿 <mark class="pub-mark decoche">Kalune Testive<\/mark>/);
  assert.ok(ui.controle('[data-change="passage-coche"][data-index="0"]').focusRecu);
});
test('une validation périmée envoie la révision affichée et demande une nouvelle relecture',async()=>{
  const ui=interfacePublication();
  let demande;
  ui.ctx.fetch=async(url,opt)=>{
    demande={url,corps:JSON.parse(opt.body)};
    return {ok:false,status:409,json:async()=>({error:'Version périmée. Rouvrez la fiche.'})};
  };
  await ui.click('pub-valider');
  assert.deepEqual(demande,{url:'/api/publications/fic1/valider',corps:{revision:7}});
  assert.match(ui.erreurs.at(-1),/Rouvrez la fiche/);
  assert.equal(ui.run('state.publication.statut'),'a_relire');
});
test('valider une séance fonctionne sans publication ouverte',async()=>{
  const ui=interfacePublication();
  ui.ctx.confirm=()=>true;
  let demande;
  ui.ctx.fetch=async(url,opt)=>{
    demande={url,corps:JSON.parse(opt.body)};
    return {ok:true,json:async()=>({})};
  };
  ui.run("state.publication=null;state.seance={id:'seance-fictive'};openSeance=async()=>{}");
  await ui.click('valider-seance');
  assert.deepEqual(demande,{url:'/api/seances/seance-fictive/valider',corps:{}});
});
test('le ZIP utilise les révisions de la liste affichée pour les seuls actes cochés',async()=>{
  const ui=interfacePublication();
  ui.ctx.document.querySelectorAll=()=>[{value:'fic1'}];
  ui.run("state.publications=[{id:'fic1',revision:7},{id:'autre',revision:2}];openPublications=async()=>{}");
  await ui.ecoutes.submit[0]({preventDefault(){},target:{id:'publication-dossier-form',date:{value:'2026-11-18'}}});
  assert.deepEqual(ui.requetes,[['/api/publications/dossier',{ids:['fic1'],date:'2026-11-18',revisions:{fic1:7}}]]);
  assert.deepEqual(ui.fichiers,['publication-2026-11-18.zip']);
});
test('masquer une sélection dans le même bloc et refuser une sélection à cheval',async()=>{
  const ui=interfacePublication();
  const fin={};const bloc={contains:n=>n===fin};
  const debut={nodeType:3,parentElement:{closest:()=>bloc}};
  ui.ctx.window.getSelection=()=>({anchorNode:debut,focusNode:fin,toString:()=>'Kalune Testive'});
  await ui.click('pub-ajouter');
  assert.equal(ui.requetes.length,1);
  ui.ctx.window.getSelection=()=>({anchorNode:debut,focusNode:{},toString:()=>'Testive Texte'});
  await ui.click('pub-ajouter');
  assert.equal(ui.requetes.length,1);
  assert.match(ui.erreurs.at(-1),/saisissez/);
});
test('l’index seul suit les droits et les statuts et télécharge le ZIP sans aucun nouvel acte',async()=>{
  const ui=interfacePublication();
  function bouton(role,statut,{visiteur=false,proprietaire='v1'}={}){
    ui.ctx.session={demonstration:visiteur,utilisateur:{id:'v1',role,identifiant:visiteur?'visiteur-v1':'agent'}};
    ui.ctx.liste=[{...acte,statut,cree_par:proprietaire}];
    return ui.run('state.session=session;state.publications=liste;publicationListe()').includes('data-action="pub-index"');
  }
  for(const role of ['administrateur','referent'])for(const statut of ['publie','retire'])assert.ok(bouton(role,statut));
  assert.equal(bouton('agent','retire'),false);
  assert.equal(bouton('referent','valide'),false);
  assert.equal(bouton('agent','retire',{visiteur:true,proprietaire:'autre'}),false);
  assert.ok(bouton('agent','retire',{visiteur:true}));
  await ui.click('pub-index');
  assert.deepEqual(ui.requetes,[['/api/publications/dossier',{ids:[]}]]);
  assert.deepEqual(ui.fichiers,['index-publication.zip']);
});
