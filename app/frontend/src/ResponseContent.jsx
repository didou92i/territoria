// SPDX-License-Identifier: EUPL-1.2
// SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Citation from './vendor/redrob/components/Citation/Citation';

function sourceHref(source) {
  return (source.kind==='arrete'?'/api/bibliotheque-arretes/':'/api/documents/') + encodeURIComponent(source.id) + '/file' + (source.page ? '#page=' + encodeURIComponent(source.page) : '');
}

// Turn only verified, numbered references into source links; code and links remain untouched.
function remarkSourceCitations({sources}) {
  return tree => {
    function visit(node) {
      if (!node.children || ['link', 'linkReference', 'code', 'inlineCode'].includes(node.type)) return;
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text') { visit(child); return [child]; }
        const text = child.value;
        const parts = [];
        let end = 0;
        for (const match of text.matchAll(/\[(\d+)\]/g)) {
          const number = Number(match[1]);
          const source = sources[number - 1];
          if (!source?.id) continue;
          if (match.index > end) parts.push({type:'text', value:text.slice(end, match.index)});
          parts.push({type:'sourceCitation', data:{hName:'source-citation', hProperties:{'data-source-index':number}, hChildren:[]}});
          end = match.index + match[0].length;
        }
        if (end === 0) return [child];
        if (end < text.length) parts.push({type:'text', value:text.slice(end)});
        return parts;
      });
    }
    visit(tree);
  };
}

export function ResponseContent({text, sources = []}) {
  const components = {
    'source-citation': props => {
      const number = Number(props['data-source-index']);
      const source = sources[number - 1];
      return source?.id ? <Citation compact index={number} title={`${source.title}${source.page ? ', page ' + source.page : ''}${source.extrait ? ', extrait ' + source.extrait : ''}`} href={sourceHref(source)}/> : null;
    },
    h1: ({children}) => <h2>{children}</h2>,
    h2: ({children}) => <h3>{children}</h3>,
    h3: ({children}) => <h4>{children}</h4>,
    table: ({children}) => <div className="response-table" role="region" aria-label="Tableau de la réponse" tabIndex={0}><table>{children}</table></div>,
    a: ({href, children}) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
    // A model-generated image URL must not trigger an external network request.
    img: ({alt}) => <span>{alt ? `Image : ${alt}` : 'Image non affichée'}</span>,
  };
  return <div className="assistant-answer"><Markdown skipHtml remarkPlugins={[remarkGfm, [remarkSourceCitations, {sources}]]} components={components}>{String(text ?? '')}</Markdown></div>;
}
