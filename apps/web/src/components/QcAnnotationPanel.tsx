import { useEffect, useId, useMemo, useState } from "react";

import {
  QC_ANNOTATION_CONFIDENCES,
  type QcAnnotationConfidence,
  type QcAnnotationLabelDefinition,
  type QcCurrentAnnotation,
} from "../lib/qcAnnotations";
import type { QcQueueScope } from "../lib/qcQueue";

export type QcAnnotationFormValue = {
  scope: QcQueueScope;
  channel: string;
  label: string;
  confidence: QcAnnotationConfidence;
  note: string;
};

type Props = {
  initialScope: QcQueueScope;
  channels: readonly string[];
  labelDefinitions: readonly QcAnnotationLabelDefinition[];
  annotations: readonly QcCurrentAnnotation[];
  writable: boolean;
  submitting: boolean;
  defaultChannel?: string;
  error?: string;
  status?: string;
  onSave: (value: QcAnnotationFormValue) => void;
  onRetract: (id: string) => void;
};

function displayToken(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toLocaleUpperCase());
}

export default function QcAnnotationPanel({
  initialScope,
  channels,
  labelDefinitions,
  annotations,
  writable,
  submitting,
  defaultChannel = "all",
  error = "",
  status = "",
  onSave,
  onRetract,
}: Props) {
  const id = useId();
  const channelOptions = useMemo(
    () => [...new Set(channels.filter((channel) => channel && channel !== "all"))],
    [channels],
  );
  const initialChannel =
    defaultChannel === "all" || channelOptions.includes(defaultChannel)
      ? defaultChannel
      : "all";
  const [scope, setScope] = useState<QcQueueScope>(initialScope);
  const [channel, setChannel] = useState(initialChannel);
  const [label, setLabel] = useState(labelDefinitions[0]?.label ?? "");
  const [confidence, setConfidence] =
    useState<QcAnnotationConfidence>("medium");
  const [note, setNote] = useState("");
  const selectedLabel = labelDefinitions.find(
    (definition) => definition.label === label,
  );
  const disabled = !writable || submitting || labelDefinitions.length === 0;
  const unavailableId = `${id}-unavailable`;

  useEffect(() => {
    if (!labelDefinitions.some((definition) => definition.label === label)) {
      setLabel(labelDefinitions[0]?.label ?? "");
    }
  }, [label, labelDefinitions]);

  return (
    <section
      className="qc-field-section qc-annotation-panel"
      aria-labelledby={`${id}-heading`}
      data-testid="qc-annotation-panel"
    >
      <div className="section-heading">
        <div>
          <h3 id={`${id}-heading`}>Annotations</h3>
          <p className="muted">Record a review without changing the source data.</p>
        </div>
      </div>

      {!writable && (
        <p id={unavailableId} className="notice" role="status">
          Annotations are read-only until the configured annotations folder is writable.
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {status && (
        <p className="notice success" role="status" aria-live="polite">
          {status}
        </p>
      )}

      <form
        className="qc-annotation-form stack"
        data-testid="qc-annotation-form"
        aria-describedby={!writable ? unavailableId : undefined}
        onSubmit={(event) => {
          event.preventDefault();
          if (disabled) return;
          onSave({ scope, channel, label, confidence, note });
        }}
      >
        <fieldset disabled={disabled}>
          <legend>Scope</legend>
          <div className="qc-annotation-options">
            <label>
              <input
                type="radio"
                name={`${id}-scope`}
                value="field"
                checked={scope === "field"}
                onChange={() => setScope("field")}
              />
              <span>Current field</span>
            </label>
            <label>
              <input
                type="radio"
                name={`${id}-scope`}
                value="well"
                checked={scope === "well"}
                onChange={() => setScope("well")}
              />
              <span>Whole well</span>
            </label>
          </div>
        </fieldset>

        <label htmlFor={`${id}-channel`}>
          <span>Channel</span>
          <select
            id={`${id}-channel`}
            value={channel}
            disabled={disabled}
            onChange={(event) => setChannel(event.target.value)}
          >
            <option value="all">All</option>
            {channelOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>

        <label htmlFor={`${id}-label`}>
          <span>Label</span>
          <select
            id={`${id}-label`}
            value={label}
            disabled={disabled}
            onChange={(event) => setLabel(event.target.value)}
          >
            {labelDefinitions.map((option) => (
              <option key={option.label} value={option.label}>
                {displayToken(option.label)}
              </option>
            ))}
          </select>
        </label>
        {selectedLabel && <small className="muted">{selectedLabel.description}</small>}

        <label htmlFor={`${id}-confidence`}>
          <span>Confidence</span>
          <select
            id={`${id}-confidence`}
            value={confidence}
            disabled={disabled}
            onChange={(event) =>
              setConfidence(event.target.value as QcAnnotationConfidence)
            }
          >
            {QC_ANNOTATION_CONFIDENCES.map((option) => (
              <option key={option} value={option}>
                {displayToken(option)}
              </option>
            ))}
          </select>
        </label>

        <label htmlFor={`${id}-note`}>
          <span>Note (optional)</span>
          <textarea
            id={`${id}-note`}
            value={note}
            maxLength={2000}
            disabled={disabled}
            aria-describedby={`${id}-note-count`}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        <small id={`${id}-note-count`} className="muted">
          {note.length.toLocaleString()} / 2,000 characters
        </small>

        <button type="submit" disabled={disabled}>
          {submitting ? "Saving\u2026" : "Save annotation"}
        </button>
      </form>

      <section
        className="qc-current-annotations stack"
        aria-labelledby={`${id}-current-heading`}
        data-testid="qc-current-annotations"
      >
        <h4 id={`${id}-current-heading`}>Current annotations</h4>
        {annotations.length === 0 ? (
          <p className="muted">No current annotations.</p>
        ) : (
          <ul>
            {annotations.map((annotation) => (
              <li key={annotation.id}>
                <div>
                  <strong>{displayToken(annotation.label)}</strong>
                  <span>
                    {annotation.scope === "field" ? "Field" : "Whole well"}
                    {" · "}
                    {annotation.channel === "all" ? "All channels" : annotation.channel}
                    {" · "}
                    {displayToken(annotation.confidence)} confidence
                  </span>
                  <span>
                    {annotation.reviewer}
                    {" · "}
                    <time dateTime={annotation.time}>{annotation.time}</time>
                  </span>
                  <span>
                    Export {annotation.exportId}
                    {" · "}
                    {annotation.blinded ? "Blinded" : "Core"}
                  </span>
                  {annotation.note && <p>{annotation.note}</p>}
                </div>
                <button
                  type="button"
                  className="secondary"
                  disabled={disabled}
                  aria-label={`Retract ${displayToken(annotation.label)} annotation for ${
                    annotation.channel === "all" ? "all channels" : annotation.channel
                  } by ${annotation.reviewer}`}
                  onClick={() => onRetract(annotation.id)}
                >
                  Retract
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
