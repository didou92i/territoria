// Adapted subset of Hirael's MIT-licensed 21st.dev components. See LICENSE and upstream/.
import * as React from 'react';
import { cx as cn } from '../redrob/internal/cx';

export type MessageRole = 'user' | 'assistant' | 'system';
export type MessageToolCallStatus = 'running' | 'done' | 'error';

interface MessageThreadContextValue {
  atBottom: boolean;
  scrollToBottom: () => void;
}

const MessageThreadContext = React.createContext<MessageThreadContextValue | null>(null);

const useMessageThread = () => {
  const context = React.useContext(MessageThreadContext);
  if (!context) {
    throw new Error('MessageThread parts must be rendered inside <MessageThread>.');
  }

  return context;
};

const prefersReducedMotion = () => {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
};

interface MessageThreadProps extends React.ComponentProps<'div'> {
  /** Keep the newest message in view while content streams in. */
  follow?: boolean;
  /** How close to the bottom (px) still counts as "at the bottom". */
  threshold?: number;
}

const MessageThread = ({ follow = true, threshold = 48, className, children, ...props }: MessageThreadProps) => {
  const viewportRef = React.useRef<HTMLDivElement>(null);
  const contentRef = React.useRef<HTMLDivElement>(null);
  const pinnedRef = React.useRef(true);
  const autoScrollingRef = React.useRef(false);
  const [atBottom, setAtBottom] = React.useState(true);

  const scrollToBottom = React.useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    pinnedRef.current = true;
    autoScrollingRef.current = true;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
    });
  }, []);

  const onScroll = React.useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const pinned = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    // Ignore intermediate positions of a programmatic smooth scroll so the pill doesn't flicker.
    if (autoScrollingRef.current) {
      if (!pinned) return;
      autoScrollingRef.current = false;
    }
    pinnedRef.current = pinned;
    setAtBottom(pinned);
  }, [threshold]);

  // Streaming children grow without re-rendering this container, so watch
  // the content box instead of relying on a render.
  React.useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content || !follow) return;
    viewport.scrollTop = viewport.scrollHeight;
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) viewport.scrollTop = viewport.scrollHeight;
    });
    observer.observe(content);

    return () => observer.disconnect();
  }, [follow]);

  const context = React.useMemo(() => ({ atBottom, scrollToBottom }), [atBottom, scrollToBottom]);

  return (
    <MessageThreadContext.Provider value={context}>
      <div
        ref={viewportRef}
        data-slot="message-thread"
        role="log"
        aria-live="polite"
        onScroll={onScroll}
        className={cn('relative flex flex-col overflow-y-auto overscroll-contain', className)}
        {...props}
      >
        <div ref={contentRef} data-slot="message-thread-content" className="flex flex-col gap-6 p-4 sm:p-6">
          {children}
        </div>
      </div>
    </MessageThreadContext.Provider>
  );
};

const MessageRoleContext = React.createContext<MessageRole>('assistant');

interface MessageProps extends React.ComponentProps<'div'> {
  role?: MessageRole;
}

const Message = ({ role = 'assistant', className, ...props }: MessageProps) => {
  return (
    <MessageRoleContext.Provider value={role}>
      <div
        data-slot="message"
        data-role={role}
        className={cn(
          'group/message flex w-full items-start gap-3',
          role === 'user' && 'flex-row-reverse',
          role === 'system' && 'justify-center',
          className,
        )}
        {...props}
      />
    </MessageRoleContext.Provider>
  );
};

type MessageBodyProps = React.ComponentProps<'div'>;

const MessageBody = ({ className, ...props }: MessageBodyProps) => {
  const role = React.useContext(MessageRoleContext);

  return (
    <div
      data-slot="message-body"
      className={cn(
        'flex min-w-0 flex-col gap-1.5',
        role === 'user' && 'max-w-[85%] items-end',
        role === 'assistant' && 'flex-1 items-start',
        role === 'system' && 'items-center',
        className,
      )}
      {...props}
    />
  );
};

type MessageContentProps = React.ComponentProps<'div'>;

const MessageContent = ({ className, ...props }: MessageContentProps) => {
  const role = React.useContext(MessageRoleContext);

  return (
    <div
      data-slot="message-content"
      className={cn(
        'text-sm leading-relaxed break-words text-foreground [&_p+p]:mt-3',
        role === 'user' && 'rounded-2xl rounded-ee-sm bg-muted px-4 py-2.5',
        role === 'assistant' && 'bg-transparent py-1',
        role === 'system' &&
          'rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground uppercase',
        className,
      )}
      {...props}
    />
  );
};

interface MessageTypingProps extends React.ComponentProps<'div'> {
  label?: string;
}

const MessageTyping = ({ label = 'Assistant is typing', className, ...props }: MessageTypingProps) => {
  return (
    <div
      data-slot="message-typing"
      role="status"
      aria-label={label}
      className={cn('inline-flex items-center gap-1 py-2.5', className)}
      {...props}
    >
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          aria-hidden
          className="size-1.5 animate-bounce rounded-full bg-muted-foreground motion-reduce:animate-none"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </div>
  );
};


export { MessageThread, Message, MessageBody, MessageContent, MessageTyping };
