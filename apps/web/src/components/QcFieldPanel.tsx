import { useMemo, type ReactNode } from "react";

import {
  buildQcFieldView,
  qcDisplayLabel,
  type QcDisplayRange,
  type QcFieldPayloadLike,
  type QcSiteOption,
  type QcSiteStepper,
  type QcViewerScalar,
} from "../lib/qcViewer";
import type {
  QcAnnotationLabelDefinition,
  QcCurrentAnnotation,
} from "../lib/qcAnnotations";
import type { QcQueueScope } from "../lib/qcQueue";
import QcAnnotationPanel, {
  type QcAnnotationFormValue,
} from "./QcAnnotationPanel";

export type QcDisplayRangeMode = "qc" | "dataset";

type Props = {
  payload: QcFieldPayloadLike;
  ranges: readonly QcDisplayRange[];
  rangeMode: QcDisplayRangeMode;
  onRangeModeChange: (mode: QcDisplayRangeMode) => void;
  siteStepper?: QcSiteStepper | null;
  onSiteChange?: (site: QcSiteOption) => void;
  annotationScope: QcQueueScope;
  annotationChannels: readonly string[];
  annotationLabels: readonly QcAnnotationLabelDefinition[];
  annotations: readonly QcCurrentAnnotation[];
  annotationsWritable: boolean;
  annotationSubmitting: boolean;
  annotationDefaultChannel?: string;
  annotationError?: string;
  annotationStatus?: string;
  onSaveAnnotation: (value: QcAnnotationFormValue) => void;
  onRetractAnnotation: (id: string) => void;
};

function displayValue(value: QcViewerScalar): ReactNode {
  if (value === null || value === "") return <span className="muted">—</span>;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  return value;
}

function formatNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export default function QcFieldPanel({
  payload,
  ranges,
  rangeMode,
  onRangeModeChange,
  siteStepper = null,
  onSiteChange,
  annotationScope,
  annotationChannels,
  annotationLabels,
  annotations,
  annotationsWritable,
  annotationSubmitting,
  annotationDefaultChannel = "all",
  annotationError = "",
  annotationStatus = "",
  onSaveAnnotation,
  onRetractAnnotation,
}: Props) {
  const view = useMemo(() => buildQcFieldView(payload), [payload]);
  const plate = displayValue(
    typeof payload.field.plate === "number" || typeof payload.field.plate === "string"
      ? payload.field.plate
      : null,
  );
  const well = displayValue(
    typeof payload.field.well === "string" ? payload.field.well : null,
  );
  const site = displayValue(
    typeof payload.field.site === "number" || typeof payload.field.site === "string"
      ? payload.field.site
      : null,
  );

  return (
    <section
      className="panel stack qc-field-panel"
      data-testid="qc-viewer-panel"
      aria-labelledby="qc-field-panel-heading"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Quality control</p>
          <h2 id="qc-field-panel-heading">Field review</h2>
        </div>
        <dl className="qc-field-identity">
          <div>
            <dt>Plate</dt>
            <dd>{plate}</dd>
          </div>
          <div>
            <dt>Well</dt>
            <dd>{well}</dd>
          </div>
          <div>
            <dt>Site</dt>
            <dd>{site}</dd>
          </div>
        </dl>
      </div>

      {siteStepper && (
        <div
          className="qc-site-stepper"
          data-testid="qc-site-stepper"
          role="group"
          aria-label="QC site navigation"
        >
          <button
            type="button"
            className="secondary"
            disabled={!siteStepper.previous}
            onClick={() => siteStepper.previous && onSiteChange?.(siteStepper.previous)}
          >
            Previous site
          </button>
          <strong role="status" aria-live="polite">
            Site {siteStepper.index + 1} of {siteStepper.options.length}
          </strong>
          <button
            type="button"
            className="secondary"
            disabled={!siteStepper.next}
            onClick={() => siteStepper.next && onSiteChange?.(siteStepper.next)}
          >
            Next site
          </button>
        </div>
      )}

      <section
        className="qc-field-section"
        data-testid="qc-display-provenance"
        aria-labelledby="qc-display-provenance-heading"
      >
        <div className="section-heading">
          <div>
            <h3 id="qc-display-provenance-heading">
              QC display range · export {payload.export_id}
            </h3>
            <p className="muted">
              Plain linear plate-by-channel windows from the configured export.
            </p>
          </div>
          <label className="qc-range-toggle">
            <input
              type="checkbox"
              checked={rangeMode === "qc"}
              onChange={(event) =>
                onRangeModeChange(event.target.checked ? "qc" : "dataset")
              }
            />
            <span>Use QC display ranges</span>
          </label>
        </div>
        {rangeMode === "qc" && (
          <p id="qc-range-locked-hint" className="muted">
            Contrast controls are locked while exact QC display ranges are active.
          </p>
        )}
        <ul className="qc-range-list">
          {ranges.map((range) => (
            <li key={range.channel} data-qc-range-channel={range.channel}>
              <strong>{range.channel}</strong>
              <span>
                {formatNumber(range.low)}–{formatNumber(range.high)}
              </span>
              <small>
                Quantiles {formatNumber(range.quantileLow)}–
                {formatNumber(range.quantileHigh)} · {range.referenceFields.toLocaleString()} reference
                {range.referenceFields === 1 ? " field" : " fields"} · {range.displayMode}
              </small>
            </li>
          ))}
        </ul>
      </section>

      <section className="qc-field-section" aria-labelledby="qc-field-metrics-heading">
        <h3 id="qc-field-metrics-heading">Metrics</h3>
        <div className="qc-metric-groups">
          {view.metricGroups.map((group) => (
            <section key={group.channel} aria-label={`${group.channel} metrics`}>
              <h4>{group.channel}</h4>
              <dl className="qc-field-list">
                {group.metrics.map((metric) => (
                  <div key={metric.column} data-qc-metric-column={metric.column}>
                    <dt>{qcDisplayLabel(metric.label)}</dt>
                    <dd>
                      <span>
                        {metric.scale ? `${qcDisplayLabel(metric.scale)} ` : ""}
                        <strong>{displayValue(metric.value)}</strong>
                      </span>
                      {metric.rawColumn && (
                        <span className="qc-raw-value">
                          Raw <strong>{displayValue(metric.rawValue)}</strong>
                        </span>
                      )}
                      {metric.direction && <small>Worse {metric.direction}</small>}
                      {metric.question && <p>{metric.question}</p>}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </section>

      {view.context.length > 0 && (
        <section className="qc-field-section" aria-labelledby="qc-field-context-heading">
          <h3 id="qc-field-context-heading">Context</h3>
          <dl className="qc-field-list">
            {view.context.map((item) => (
              <div key={item.column} data-qc-context-column={item.column}>
                <dt>{qcDisplayLabel(item.label)}</dt>
                <dd>
                  <strong>{displayValue(item.value)}</strong>
                  {item.question && <p>{item.question}</p>}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {view.attributes.length > 0 && (
        <section className="qc-field-section" aria-labelledby="qc-field-attributes-heading">
          <h3 id="qc-field-attributes-heading">Attributes</h3>
          <dl className="qc-field-list">
            {view.attributes.map((item) => (
              <div key={item.column} data-qc-attribute={item.column}>
                <dt>{qcDisplayLabel(item.label)}</dt>
                <dd>{displayValue(item.value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <QcAnnotationPanel
        initialScope={annotationScope}
        channels={annotationChannels}
        labelDefinitions={annotationLabels}
        annotations={annotations}
        writable={annotationsWritable}
        submitting={annotationSubmitting}
        defaultChannel={annotationDefaultChannel}
        error={annotationError}
        status={annotationStatus}
        onSave={onSaveAnnotation}
        onRetract={onRetractAnnotation}
      />
    </section>
  );
}
