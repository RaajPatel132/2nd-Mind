import { forwardRef, useId, type TextareaHTMLAttributes } from "react";
import { cx } from "./cx";

type Props = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string;
  /** A plain-words hint under the field. */
  hint?: string;
};

/** A labelled several-line input, like TextField: radius-sm, a line-control border, scrolls past its height. */
export const TextAreaField = forwardRef<HTMLTextAreaElement, Props>(function TextAreaField(
  { label, hint, className, id, rows = 6, ...rest },
  ref,
) {
  const auto = useId();
  const fieldId = id ?? auto;
  return (
    <div className={cx("grid gap-1", className)}>
      <label htmlFor={fieldId} className="text-label text-fg-2">
        {label}
      </label>
      <textarea
        ref={ref}
        id={fieldId}
        rows={rows}
        aria-describedby={hint ? `${fieldId}-hint` : undefined}
        className="scroll-thin min-h-24 min-w-0 resize-y rounded-sm border border-line-control bg-surface-2 px-3 py-2 text-body text-fg placeholder:text-fg-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg"
        {...rest}
      />
      {hint && (
        <p id={`${fieldId}-hint`} className="m-0 text-label font-normal text-fg-3">
          {hint}
        </p>
      )}
    </div>
  );
});
