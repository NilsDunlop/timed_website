import {
  maskCountParts,
  maskKindColor,
  maskKindLabel,
  MAX_OUTLINE_WIDTH,
  MIN_OUTLINE_WIDTH,
  type MaskCounts,
} from "../../lib/maskOverlays";
import type { MaskOverlays } from "./useMaskOverlays";

type SegmentationPanelProps = {
  masks: MaskOverlays;
  /** The comparison label of each source ("A" to "D"), or null for one source. */
  sources: readonly { key: string; label: string | null }[];
  kinds: readonly string[];
  width: number;
  opacity: number;
  onModelChange: (model: string) => void;
  onKindChange: (kind: string, visible: boolean) => void;
  onWidthChange: (width: number) => void;
  onOpacityChange: (opacity: number) => void;
};

/** One count per kind; a narrow column breaks the line between kinds, never inside one. */
function CountLine({ kinds, counts }: { kinds: readonly string[]; counts: MaskCounts }) {
  const parts = maskCountParts(kinds, counts);
  return (
    <span className="segmentation-count-line">
      {parts.map((part, index) => (
        <span key={index}>
          {index > 0 && " "}
          <span className="segmentation-count">
            {index < parts.length - 1 ? part + " ·" : part}
          </span>
        </span>
      ))}
    </span>
  );
}

/** Outline toggles for the segmentation masks; shown only when masks exist. */
export default function SegmentationPanel({
  masks,
  sources,
  kinds,
  width,
  opacity,
  onModelChange,
  onKindChange,
  onWidthChange,
  onOpacityChange,
}: SegmentationPanelProps) {
  const { model, models } = masks;
  if (!model || models.length === 0) return null;

  return (
    <fieldset className="segmentation-control" data-testid="segmentation-panel">
      <legend>Segmentation</legend>
      <div className="segmentation-model">
        <label>
          <span>Model</span>
          <select
            value={model.model_tag}
            title={model.recipe ?? undefined}
            onChange={(event) => onModelChange(event.target.value)}
          >
            {models.map((option) => (
              <option
                key={option.model_tag}
                value={option.model_tag}
                title={option.recipe ?? undefined}
              >
                {option.model_tag}
              </option>
            ))}
          </select>
        </label>
        {!model.frozen && (
          <span
            className="segmentation-badge"
            title="This model's masks are not frozen yet"
            data-testid="segmentation-unfrozen"
          >
            May still change
          </span>
        )}
        {model.recipe && (
          <small className="segmentation-recipe" data-testid="segmentation-recipe">
            {model.recipe}
          </small>
        )}
      </div>

      <ul className="segmentation-counts" data-testid="segmentation-counts">
        {sources.map((source) => {
          const counts = masks.counts.get(source.key);
          return (
            <li key={source.key}>
              {source.label && <span className="comparison-index">{source.label}</span>}
              {counts?.status === "ready" ? (
                <CountLine kinds={model.kinds} counts={counts.value} />
              ) : (
                <span>
                  {counts?.status === "error"
                    ? `Counts unavailable: ${counts.message}`
                    : "Counting objects…"}
                </span>
              )}
            </li>
          );
        })}
      </ul>

      <div className="segmentation-kinds" role="group" aria-label="Outlines">
        {model.kinds.map((kind) => (
          <label key={kind}>
            <input
              type="checkbox"
              checked={kinds.includes(kind)}
              onChange={(event) => onKindChange(kind, event.target.checked)}
            />
            <span
              className="channel-swatch"
              style={{ backgroundColor: `#${maskKindColor(kind)}` }}
            />
            {maskKindLabel(kind)}
          </label>
        ))}
      </div>

      <label>
        <span>Outline width: {width} px</span>
        <input
          type="range"
          aria-label="Outline width"
          min={MIN_OUTLINE_WIDTH}
          max={MAX_OUTLINE_WIDTH}
          step={1}
          value={width}
          onChange={(event) => onWidthChange(Number(event.target.value))}
        />
      </label>
      <label>
        <span>Outline opacity: {Math.round(opacity * 100)}%</span>
        <input
          type="range"
          aria-label="Outline opacity"
          min={0.1}
          max={1}
          step={0.05}
          value={opacity}
          onChange={(event) => onOpacityChange(Number(event.target.value))}
        />
      </label>

      {masks.outlinesLoading && (
        <small className="segmentation-status" data-testid="segmentation-loading">
          Loading outlines…
        </small>
      )}
      {masks.outlineErrors.map((message) => (
        <p key={message} className="notice error" role="alert">
          {message}
        </p>
      ))}
    </fieldset>
  );
}
