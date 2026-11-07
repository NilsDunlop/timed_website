import {
  formatMaskCounts,
  maskKindColor,
  maskKindLabel,
  MAX_OUTLINE_WIDTH,
  MIN_OUTLINE_WIDTH,
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
            onChange={(event) => onModelChange(event.target.value)}
          >
            {models.map((option) => (
              <option key={option.model_tag} value={option.model_tag}>
                {option.recipe ? `${option.model_tag} (${option.recipe})` : option.model_tag}
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
      </div>

      <ul className="segmentation-counts" data-testid="segmentation-counts">
        {sources.map((source) => {
          const counts = masks.counts.get(source.key);
          const text =
            counts?.status === "ready"
              ? formatMaskCounts(model.kinds, counts.value)
              : counts?.status === "error"
                ? `Counts unavailable: ${counts.message}`
                : "Counting objects…";
          return (
            <li key={source.key}>
              {source.label && <span className="comparison-index">{source.label}</span>}
              <span>{text}</span>
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
