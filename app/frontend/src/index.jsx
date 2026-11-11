// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { MessageThread, Message, MessageBody, MessageContent, MessageTyping } from './vendor/hirael/MessageThread';
import { PromptInput, PromptInputTextarea, PromptInputToolbar, PromptInputActions } from './vendor/hirael/PromptInput';
import { ResponseContent } from './ResponseContent';
import { ArreteDocument } from './ArreteDocument';
import { texteAttente } from './Attente';
import { cibleDefilement } from './Defilement';

const roots = [];
const Icon = ({name}) => <svg aria-hidden="true"><use href={'#i-' + name}/></svg>;
// Début de l'attente en cours, hors de React : les îlots sont remontés à chaque affichage et le chrono ne doit pas repartir.
let attenteDepuis = 0;

function Attente({enLigne}) {
  const [, setTic] = useState(0);
  useEffect(() => {
    const minuterie = setInterval(() => setTic(t => t + 1), 1000);
    return () => clearInterval(minuterie);
  }, []);
  const secondes = Math.max(0, Math.round((Date.now() - attenteDepuis) / 1000));
  const {titre, detail} = texteAttente(enLigne);
  return <div className="message loading attente">
    <MessageTyping label="Rédaction en cours"/>
    <span><b>{titre} {secondes} s</b>
      <small>{detail}</small></span>
  </div>;
}

function Composer({draft, busy, tab, mode, space, spaces, onDraft, onSend, onMode, onSpace, onNotice}) {
  const [value, setValue] = useState(draft);
  const [selectedSpace, setSelectedSpace] = useState(space);
  const update = text => { setValue(text); onDraft(text); };
  const helper = tab === 'search' ? 'Recherche dans vos documents.' : mode === 'sources'
    ? 'Questions avec sources, ou rédaction d’arrêtés à partir des modèles d’actes, avec téléchargement Word.'
    : 'Rédaction libre. Les demandes d’arrêtés utilisent automatiquement les modèles et le workflow de rédaction.';
  return <>
    <PromptInput id="composer" className="composer" value={value} onValueChange={update} disabled={busy}
      onSubmit={text => { onDraft(text); onSend(); }}>
      <label className="sr-only" htmlFor="question">Votre question</label>
      <PromptInputTextarea id="question" name="question" minRows={2} maxRows={6}
        placeholder={tab === 'search' ? 'Rechercher par mots clés dans vos documents' : 'Poser votre question ou demander d’exécuter une tâche'}
        onPaste={event => { if (event.clipboardData?.files?.length) { event.preventDefault(); onNotice('Importez vos documents dans un espace.'); } }}/>
      <PromptInputToolbar className="composer-bottom">
        <PromptInputActions className="select-group">
          {tab==='chat'&&<button type="button" className="composer-identity" data-action="identity" disabled={busy}><Icon name="upload"/> Logo et collectivité</button>}
          <label className="select-wrap"><Icon name="book"/><span className="sr-only">Documents à interroger</span>
            <select id="space-select" value={selectedSpace} onChange={event => { setSelectedSpace(event.target.value); onSpace(event.target.value); }} disabled={busy}>
              <option value="all">Tous les documents</option>{spaces.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          {tab === 'chat' && <label className="select-wrap"><Icon name="spark"/><span className="sr-only">Mode de réponse</span>
            <select id="mode-select" value={mode} onChange={event => onMode(event.target.value)} disabled={busy}>
              <option value="sources">Avec sources</option><option value="libre">Rédaction libre</option>
            </select>
          </label>}
        </PromptInputActions>
        <button type="submit" className="send-btn" aria-label={tab === 'search' ? 'Lancer la recherche' : 'Envoyer'} disabled={busy || !value.trim()}><Icon name="arrow"/></button>
      </PromptInputToolbar>
    </PromptInput>
    <p className="helper">{helper}</p>
  </>;
}

// Note de la réponse et signalement : de simples boutons `data-action`, traités par app.js comme le reste de l'interface.
function Retour({message}) {
  if (!message.id) return null;
  const utile = message.note === 1, aAmeliorer = message.note === -1;
  return <div className="feedback" role="group" aria-label="Évaluer cette réponse">
    <button type="button" className={'feedback-btn' + (utile ? ' on' : '')} data-action="rate" data-id={message.id} data-note={utile ? 0 : 1}
      aria-pressed={utile} title="Réponse utile"><Icon name="up"/><span className="sr-only">Réponse utile</span></button>
    <button type="button" className={'feedback-btn' + (aAmeliorer ? ' on' : '')} data-action="rate" data-id={message.id} data-note={aAmeliorer ? 0 : -1}
      aria-pressed={aAmeliorer} title="Réponse à améliorer"><Icon name="down"/><span className="sr-only">Réponse à améliorer</span></button>
    {message.signale ? <span className="feedback-done"><Icon name="check"/> Signalée au référent</span>
      : aAmeliorer && <button type="button" className="text-btn feedback-report" data-action="report" data-id={message.id}>Signaler au référent</button>}
  </div>;
}

function Conversation({messages, busy, scrollLatest, enLigne}) {
  const threadRef = useRef(null);
  if (!busy) attenteDepuis = 0;
  else if (!attenteDepuis) attenteDepuis = Date.now();
  const estActe=artifact=>['arrete','deliberation','arrete_rh','arrete_police','arrete_delegation'].includes(artifact?.kind);
  const latestDocument=messages.reduce((last,message,index)=>estActe(message.artifact)?index:last,-1);
  useLayoutEffect(() => {
    const thread = threadRef.current;
    if (!thread || !scrollLatest) return;
    const message = Array.from(thread.querySelectorAll('[data-slot="message"]')).at(-1);
    const debut = message?.previousElementSibling?.classList.contains('message-label') ? message.previousElementSibling : message;
    const actions = message?.querySelector('.arrete-artifact > .arrete-actions');
    const saisie = document.getElementById('react-composer');
    const cible = debut && cibleDefilement({debutHaut: debut.getBoundingClientRect().top,
      actionsBas: actions ? actions.getBoundingClientRect().bottom : null,
      hauteurFenetre: window.innerHeight, hauteurSaisie: saisie ? saisie.getBoundingClientRect().height : 0}) === 'actions' ? actions : debut;
    if (cible) requestAnimationFrame(() => {
      if (cible.isConnected) cible.scrollIntoView({block:'start', behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    });
  }, [messages.length, busy, scrollLatest]);
  return <div ref={threadRef}><MessageThread follow={false} className="hirael-thread" aria-label="Conversation">
    {messages.map((message, index) => message.role === 'user'
      ? <Message key={index} role="user" className="message user"><MessageBody><MessageContent>{message.text}</MessageContent></MessageBody></Message>
      : <React.Fragment key={index}>
          <div className="message-label"><Icon name="spark"/> Territoria</div>
          <Message role="assistant" className="message assistant"><MessageBody><MessageContent><ResponseContent text={message.text} sources={message.sources || []}/>{estActe(message.artifact)&&(index===latestDocument?<ArreteDocument artifact={message.artifact}/>:<details className="arrete-previous"><summary>Consulter la version précédente</summary><ArreteDocument artifact={message.artifact}/></details>)}<Retour message={message}/></MessageContent></MessageBody></Message>
        </React.Fragment>)}
    {busy && <Attente enLigne={enLigne}/>}
  </MessageThread></div>;
}
function mount(id, Component, props) {
  const element = document.getElementById(id);
  if (!element) return;
  const root = createRoot(element);
  roots.push(root);
  flushSync(() => root.render(<Component {...props}/>));
}
window.Territoire21st = {
  mountComposer: props => mount('react-composer', Composer, props),
  mountConversation: props => mount('react-thread', Conversation, props),
  unmount: () => { while (roots.length) roots.pop().unmount(); },
};
