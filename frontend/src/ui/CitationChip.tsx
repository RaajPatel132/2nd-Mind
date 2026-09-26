import { cx } from "./cx";

type Props = {
  marker: number;
  /** What it points to, for the accessible name and the tooltip ("Lives in Pune"). */
  title: string;
  kind: "item" | "turn";
  onClick: () => void;
  className?: string;
};

/** An inline `[n]` citation in an answer: radius-xs, Machine type, opens what it cites. */
export function CitationChip({
  marker,
  title,
  kind,
  onClick,
  className,
}: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={`Source ${String(marker)}: ${kind === "turn" ? "a chat turn, " : ""}${title}`}
      data-testid="citation"
      data-kind={kind}
      className={cx(
        "mx-0.5 inline-flex h-5 min-w-5 cursor-pointer items-center justify-center rounded-xs bg-surface-2 px-1 align-baseline font-machine text-mono-sm text-fg-2 ring-1 ring-inset ring-line transition-colors dur-1 hover:bg-surface-3 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg",
        className,
      )}
    >
      {marker}
    </button>
  );
}
