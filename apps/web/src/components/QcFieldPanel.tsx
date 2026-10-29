import { useMemo, type ReactNode } from "react";

import {
  buildQcFieldView,
  qcDisplayLabel,
  type QcFieldMetric,
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

type Props = {
  payload: QcFieldPayloadLike;
  siteStepper?: QcSiteStepper | null;
  flaggedSites?: readonly number[];
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

function dictionaryBoolean(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes"].includes(String(value ?? "").trim().toLowerCase());
}

function metricOrder(metric: QcFieldMetric): number {
  const order = ["DNA", "ER", "AGP", "RNA", "Mito"];
  const index = order.indexOf(metric.channel);
  return index >= 0 ? index : metric.channel === "Site" ? order.length + 1 : order.length;
}

export default function QcFieldPanel({
  payload,
  siteStepper = null,
  flaggedSites = [],
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
  const reviewColumns = useMemo(
    () =>
      new Set(
        payload.dictionary.flatMap((definition) =>
          String(definition.gate ?? "").trim().toLowerCase() === "review" &&
          dictionaryBoolean(definition.primary)
            ? [String(definition.column ?? "")]
            : [],
        ),
      ),
    [payload.dictionary],
  );
  const reviewMetrics = view.metricGroups
    .flatMap((group) => group.metrics)
    .filter((metric) => reviewColumns.has(metric.column))
    .sort((left, right) => metricOrder(left) - metricOrder(right));
  const flaggedSiteOptions = siteStepper
    ? siteStepper.options.filter((option) => flaggedSites.includes(option.site))
    : [];
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
            Site {siteStepper.current.site} of {siteStepper.options.length}
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

      {siteStepper &&
        (flaggedSiteOptions.length > 0 ? (
          <div className="qc-flagged-sites" data-testid="qc-flagged-sites">
            <strong>Flagged sites</strong>
            <div>
              {flaggedSiteOptions.map((option) => (
                <button
                  key={option.site}
                  type="button"
                  className="secondary compact-button"
                  aria-current={option.site === siteStepper.current.site ? "true" : undefined}
                  onClick={() => onSiteChange?.(option)}
                >
                  {option.site}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <p className="muted qc-well-review-note">
            Well-level review; this queue does not identify individual flagged sites.
          </p>
        ))}

      {reviewMetrics.length > 0 && (
        <section className="qc-review-metrics" aria-labelledby="qc-review-metrics-heading">
          <div className="section-heading">
            <div>
              <h3 id="qc-review-metrics-heading">Review metrics</h3>
              <p className="muted">Only metrics registered as review rules are shown.</p>
            </div>
          </div>
          <div className="qc-review-metrics-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Channel</th>
                  <th scope="col">Metric</th>
                  <th scope="col">Values</th>
                </tr>
              </thead>
              <tbody>
                {reviewMetrics.map((metric) => {
                  const siteValue = metric.rawColumn ? metric.rawValue : metric.value;
                  const comparisonValue = metric.rawColumn ? metric.value : null;
                  const label = qcDisplayLabel(metric.label);
                  return (
                    <tr key={metric.column} data-qc-metric-column={metric.column}>
                      <th scope="row">
                        {metric.channel === "Site" ? "Whole field" : metric.channel}
                      </th>
                      <td>
                        <span className="qc-metric-name">
                          {label}
                          {metric.question && (
                            <button
                              type="button"
                              className="qc-info-button"
                              aria-label={`${label}: ${metric.question}`}
                              data-tooltip={metric.question}
                            >
                              i
                            </button>
                          )}
                        </span>
                      </td>
                      <td>
                        <span className="qc-metric-value-line">
                          <small>Site value</small>
                          <strong>{displayValue(siteValue)}</strong>
                        </span>
                        {comparisonValue !== null && (
                          <span className="qc-metric-value-line">
                            <small>{qcDisplayLabel(metric.scale || "Review value")}</small>
                            <strong>{displayValue(comparisonValue)}</strong>
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
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
