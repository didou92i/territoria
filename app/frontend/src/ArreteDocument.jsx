// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import React from 'react';

export function ArreteDocument({artifact}) {
  const d=artifact?.document;
  if(!d || !Array.isArray(d.articles))return null;
  const download=/^\/api\/arretes\/[a-f0-9]{32}\/file$/.test(artifact.download||'')?artifact.download:'';
  const logo=/^\/api\/identite\/logo\/[a-f0-9]{32}$/.test(d.logo?.url||'')?d.logo.url:'';
  const deliberation=d.kind==='deliberation';
  return <section className="arrete-artifact" aria-label={deliberation?'Projet de délibération complet':'Projet d’arrêté complet'}>
    <div className="arrete-actions"><span>{d.articles.length} articles · {deliberation?'Projet non adopté':'Projet non signé'}</span>
      {download&&<a className="primary-btn" href={download}>Télécharger le Word</a>}
      {artifact.id&&(d.title||'').includes('[à attribuer]')&&<button type="button" className="outline-btn" data-action="numeroter" data-id={artifact.id}>Attribuer un numéro</button>}
      <button type="button" className="outline-btn" data-action="identity">Logo et collectivité</button>
    </div>
    <article className="arrete-sheet">
      <header className="arrete-letterhead">{logo&&<img src={logo} alt={'Logo de '+d.collectivite}/>}<span>{d.collectivite}</span><small>PROJET — NON SIGNÉ</small></header>
      <h2>{d.title}</h2>
      {!!d.preambule?.length&&<div className="arrete-preambule">{d.preambule.map((v,i)=><p key={i}>{v}</p>)}</div>}
      <p className="arrete-object">{d.objet}</p>
      <p>{d.autorite},</p>
      <div className="arrete-visas">{d.visas.map((v,i)=><p key={i}>{v}</p>)}</div>
      {d.considerants.map((v,i)=><p key={i}>{v}</p>)}
      {d.vote&&<p>{d.vote}</p>}
      <p className="arrete-enact">{d.decision||'ARRÊTE'}</p>
      {d.articles.map(a=><section key={a.number} className="arrete-article"><h3>Article {a.number}{a.title?' — '+a.title:''}</h3><p>{a.texte}</p></section>)}
      <footer className="arrete-signature"><p>{d.lieu_date}</p>{(d.signatures||[d.autorite,d.signature]).map((v,i)=><p key={i}>{v}</p>)}</footer>
      {d.certification&&<p className="arrete-certification">{d.certification}</p>}
    </article>
    {!!artifact.vigilances?.length&&<details className="arrete-review" open><summary>Points à compléter ou à vérifier</summary><ul>{artifact.vigilances.map((v,i)=><li key={i}>{v}</li>)}</ul></details>}
    <p className="arrete-disclaimer">{artifact.warning}</p>
    {!!artifact.legal?.sources?.length&&<details className="arrete-legal"><summary>Textes publics consultés le {artifact.legal.as_of}</summary><ul>{artifact.legal.sources.map(s=><li key={s.article}><a href={s.url} target="_blank" rel="noopener noreferrer">{s.code} · {s.article}</a></li>)}</ul><p>L’actualité des textes est vérifiée à cette date. Leur application au dossier et les textes particuliers restent à contrôler.</p></details>}
  </section>;
}
