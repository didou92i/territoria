// Adapted subset of Hirael's MIT-licensed 21st.dev components. See LICENSE and upstream/.
import * as React from 'react';
import { cx as cn } from '../redrob/internal/cx';

export interface PromptAttachment {
  id: string;
  name: string;
  /** Bytes. */
  size: number;
  type?: string;
  /** Present when the attachment came from the file picker or a paste. */
  file?: File;
}

export interface PromptInputModel {
  id: string;
  label: string;
  hint?: string;
}

interface PromptInputContextValue {
  value: string;
  setValue: (next: string) => void;
  attachments: PromptAttachment[];
  addFiles: (files: Iterable<File>) => void;
  removeAttachment: (id: string) => void;
  disabled: boolean;
  isStreaming: boolean;
  canSubmit: boolean;
  submit: () => void;
  stop: () => void;
  textareaId: string;
}

const PromptInputContext = React.createContext<PromptInputContextValue | null>(null);

const usePromptInput = () => {
  const context = React.useContext(PromptInputContext);
  if (!context) {
    throw new Error('PromptInput parts must be rendered inside <PromptInput>.');
  }

  return context;
};

let attachmentSeq = 0;

const toAttachment = (file: File): PromptAttachment => {
  attachmentSeq += 1;

  return {
    id: `${file.name}-${file.size}-${attachmentSeq}`,
    name: file.name,
    size: file.size,
    type: file.type,
    file,
  };
};

interface PromptSubmitOptions {
  attachments: PromptAttachment[];
}

interface PromptInputProps extends Omit<React.ComponentProps<'form'>, 'onSubmit' | 'defaultValue' | 'value'> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  defaultAttachments?: PromptAttachment[];
  onAttachmentsChange?: (attachments: PromptAttachment[]) => void;
  /**
   * Called with the trimmed text and the current attachments. In
   * uncontrolled mode the text clears afterwards; when `value` is
   * controlled, clear it yourself here. Attachments always clear.
   */
  onSubmit?: (value: string, options: PromptSubmitOptions) => void;
  disabled?: boolean;
  /** Turns the submit button into a Stop button that calls `onStop`. */
  isStreaming?: boolean;
  onStop?: () => void;
}

const PromptInput = ({
  value: valueProp,
  defaultValue = '',
  onValueChange,
  defaultAttachments,
  onAttachmentsChange,
  onSubmit,
  disabled = false,
  isStreaming = false,
  onStop,
  className,
  children,
  ...props
}: PromptInputProps) => {
  const [internalValue, setInternalValue] = React.useState(defaultValue);
  const isControlled = valueProp !== undefined;
  const value = isControlled ? valueProp : internalValue;
  const [attachments, setAttachments] = React.useState<PromptAttachment[]>(() => defaultAttachments ?? []);
  const textareaId = React.useId();

  const setValue = React.useCallback(
    (next: string) => {
      if (!isControlled) setInternalValue(next);
      onValueChange?.(next);
    },
    [isControlled, onValueChange],
  );

  const addFiles = React.useCallback(
    (files: Iterable<File>) => {
      const added = Array.from(files).map(toAttachment);
      if (added.length === 0) return;
      const next = [...attachments, ...added];
      setAttachments(next);
      onAttachmentsChange?.(next);
    },
    [attachments, onAttachmentsChange],
  );

  const removeAttachment = React.useCallback(
    (id: string) => {
      const next = attachments.filter((a) => a.id !== id);
      setAttachments(next);
      onAttachmentsChange?.(next);
    },
    [attachments, onAttachmentsChange],
  );

  const canSubmit = !disabled && (value.trim().length > 0 || attachments.length > 0);

  const submit = React.useCallback(() => {
    if (isStreaming || !canSubmit) return;
    onSubmit?.(value.trim(), { attachments });
    if (!isControlled) setInternalValue('');
    if (attachments.length > 0) {
      setAttachments([]);
      onAttachmentsChange?.([]);
    }
  }, [isStreaming, canSubmit, onSubmit, value, attachments, isControlled, onAttachmentsChange]);

  const stop = React.useCallback(() => onStop?.(), [onStop]);

  const context = React.useMemo<PromptInputContextValue>(
    () => ({
      value,
      setValue,
      attachments,
      addFiles,
      removeAttachment,
      disabled,
      isStreaming,
      canSubmit,
      submit,
      stop,
      textareaId,
    }),
    [
      value,
      setValue,
      attachments,
      addFiles,
      removeAttachment,
      disabled,
      isStreaming,
      canSubmit,
      submit,
      stop,
      textareaId,
    ],
  );

  return (
    <PromptInputContext.Provider value={context}>
      <form
        data-slot="prompt-input"
        data-streaming={isStreaming || undefined}
        data-disabled={disabled || undefined}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className={cn(
          'flex w-full flex-col gap-2 rounded-xl border border-input bg-card p-2 text-card-foreground shadow-xs transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50 data-[disabled]:opacity-60 motion-reduce:transition-none',
          className,
        )}
        {...props}
      >
        {children}
      </form>
    </PromptInputContext.Provider>
  );
};

interface PromptInputTextareaProps extends Omit<
  React.ComponentProps<'textarea'>,
  'value' | 'defaultValue' | 'onChange'
> {
  /** Grows with the text up to this many rows, then scrolls. */
  maxRows?: number;
  minRows?: number;
}

const PromptInputTextarea = ({
  maxRows = 6,
  minRows = 1,
  placeholder = 'Ask anything',
  className,
  onKeyDown,
  onPaste,
  ...props
}: PromptInputTextareaProps) => {
  const { value, setValue, submit, addFiles, disabled, textareaId } = usePromptInput();
  const ref = React.useRef<HTMLTextAreaElement>(null);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    const styles = getComputedStyle(el);
    const lineHeight = parseFloat(styles.lineHeight) || 24;
    const padding = (parseFloat(styles.paddingTop) || 0) + (parseFloat(styles.paddingBottom) || 0);
    const min = lineHeight * minRows + padding;
    const max = lineHeight * maxRows + padding;
    el.style.height = `${Math.min(Math.max(el.scrollHeight, min), max)}px`;
    el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden';
  }, [value, maxRows, minRows]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    // An IME still composing (Japanese, Chinese, Korean) uses Enter to commit the candidate.
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    onPaste?.(event);
    if (event.defaultPrevented) return;
    const files = event.clipboardData?.files;
    if (files && files.length > 0) {
      event.preventDefault();
      addFiles(files);
    }
  };

  return (
    <textarea
      ref={ref}
      id={textareaId}
      data-slot="prompt-input-textarea"
      rows={minRows}
      value={value}
      disabled={disabled}
      placeholder={placeholder}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={handleKeyDown}
      onPaste={handlePaste}
      className={cn(
        'flex field-sizing-content min-h-0 w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
};

type PromptInputToolbarProps = React.ComponentProps<'div'>;

const PromptInputToolbar = ({ className, ...props }: PromptInputToolbarProps) => {
  return <div data-slot="prompt-input-toolbar" className={cn('flex items-center gap-1', className)} {...props} />;
};

type PromptInputActionsProps = React.ComponentProps<'div'>;

const PromptInputActions = ({ className, ...props }: PromptInputActionsProps) => {
  return (
    <div data-slot="prompt-input-actions" className={cn('me-auto flex items-center gap-1', className)} {...props} />
  );
};


export { PromptInput, PromptInputTextarea, PromptInputToolbar, PromptInputActions };
