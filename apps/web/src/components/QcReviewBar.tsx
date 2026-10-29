type Props = {
  index: number;
  total: number;
  annotated: number;
  reason?: string;
  busy?: boolean;
  warning?: string;
  onPrevious: () => void;
  onNext: () => void;
};

export default function QcReviewBar({
  index,
  total,
  annotated,
  reason = "",
  busy = false,
  warning = "",
  onPrevious,
  onNext,
}: Props) {
  const totalCount = Math.max(total, 0);
  const current =
    totalCount === 0 ? 0 : Math.min(Math.max(index + 1, 1), totalCount);
  const annotatedCount = Math.min(Math.max(annotated, 0), totalCount);
  const previousDisabled = busy || totalCount === 0 || index <= 0;
  const nextDisabled = busy || totalCount === 0 || index >= totalCount - 1;

  return (
    <nav
      className="panel qc-review-bar"
      aria-label="Review queue"
      aria-busy={busy}
      data-testid="qc-review-bar"
    >
      <div className="qc-review-progress" role="status" aria-live="polite">
        <strong>
          Review {current} of {totalCount}
        </strong>
        <span>
          {annotatedCount} of {totalCount} annotated
        </span>
      </div>
      <div className="qc-review-reason" data-testid="qc-review-reason">
        <span>Flagged by</span>
        <strong>{reason || "Current QC filters"}</strong>
      </div>
      <div className="qc-review-actions">
        <button
          type="button"
          className="secondary"
          disabled={previousDisabled}
          onClick={onPrevious}
        >
          ← Previous
        </button>
        <button
          type="button"
          className="secondary"
          disabled={nextDisabled}
          onClick={onNext}
        >
          Next →
        </button>
      </div>
      {warning && (
        <p className="notice error qc-review-warning" role="alert">
          {warning}
        </p>
      )}
    </nav>
  );
}
