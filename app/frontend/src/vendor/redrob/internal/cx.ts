/**
 * Joins class names, skipping anything falsy and taking the truthy keys of an object.
 *
 *   cx('rr-btn', `rr-btn--${variant}`, { 'rr-btn--full': fullWidth })
 *
 * Deliberately not `clsx`: the whole behaviour is eight lines, and a component library that pulls a
 * dependency for it makes every consumer carry that dependency too.
 */
export function cx(
  ...parts: Array<string | false | null | undefined | Record<string, boolean | undefined>>
): string {
  const out: string[] = [];
  for (const part of parts) {
    if (!part) continue;
    if (typeof part === 'string') {
      out.push(part);
    } else {
      for (const key of Object.keys(part)) {
        if (part[key]) out.push(key);
      }
    }
  }
  return out.join(' ');
}

export default cx;
