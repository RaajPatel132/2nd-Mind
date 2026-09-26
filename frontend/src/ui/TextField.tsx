import { forwardRef, useId, type InputHTMLAttributes } from "react";
import { cx } from "./cx";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "size"> & {
  label: string;
  /** A plain-words hint under the field ("Say it like you would: Friday, 3 October"). */
  hint?: string;
};

/** A labelled one-line input: 40px, radius-sm, a line-control border (rulebook §2, §11). */
export const TextField = forwardRef<HTMLInputElement, Props>(function TextField(
  { label, hint, className, id, ...rest },
  ref,
) {
  const auto = useId();
  const inputId = id ?? auto;
  return (
    <div className={cx("grid gap-1", className)}>
      <label htmlFor={inputId} className="text-label text-fg-2">
        {label}
      </label>
      <input
        ref={ref}
        id={inputId}
        aria-describedby={hint ? `${inputId}-hint` : undefined}
        className="h-10 min-w-0 rounded-sm border border-line-control bg-surface-2 px-3 text-body text-fg placeholder:text-fg-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg"
        {...rest}
      />
      {hint && (
        <p
          id={`${inputId}-hint`}
          className="m-0 text-label font-normal text-fg-3"
        >
          {hint}
        </p>
      )}
    </div>
  );
});
