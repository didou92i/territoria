import * as React from 'react';
import { cx } from '../../internal/cx';

export interface CitationProps {
  /** Where it came from, short: a publication, a file name. */
  source?: React.ReactNode;
  /** The full title. Becomes the tooltip and part of the accessible name. */
  title?: string;
  /** Its number in the answer's source list. */
  index?: number | string;
  /** Omit it and this renders as text, not a link. */
  href?: string;
  /** Keep the full title accessible while displaying only the source number. */
  compact?: boolean;
  className?: string;
}

/**
 * A reference to where something came from.
 *
 * No `href`, no link. A citation that jumps to the top of the page is worse than none: it teaches a reader
 * that the sources in this product do not go anywhere, and then they stop checking any of them.
 *
 * The accessible name is composed from the index and the title, so a screen reader announces "Source 3: the
 * Q3 filing" rather than "3".
 */
export function Citation(props: CitationProps): React.ReactElement {
  const label = props.compact ? null : (props.source || props.title);

  return React.createElement(
    props.href ? 'a' : 'span',
    {
      className: cx('rr-cite', !props.href && 'rr-cite--static', props.className),
      href: props.href || undefined,
      target: props.href ? '_blank' : undefined,
      rel: props.href ? 'noreferrer' : undefined,
      title: props.title,
      'aria-label': `Source ${props.index != null ? `${props.index}: ` : ''}${props.title || label || ''}`,
    },
    [
      props.index != null
        ? React.createElement('span', { className: 'rr-cite__index', key: 'i' }, props.index)
        : null,
      label ? React.createElement('span', { className: 'rr-cite__source', key: 's' }, label) : null,
    ],
  );
}

export default Citation;
