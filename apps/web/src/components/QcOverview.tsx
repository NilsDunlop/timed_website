import { useMemo, useState } from "react";

import { overviewChannels } from "../lib/qc/qcOverview";
import type { QcColumnDefinition } from "../lib/qcModel";
import { qcDisplayLabel, qcDisplayQuestion } from "../lib/qcViewer";

type QcRow = Record<string, unknown>;

type Props = {
  rows: readonly QcRow[];
  dictionary: readonly QcColumnDefinition[];
  selectedPlate: number | null;
  selectedChannel: string | null;
  onSelect: (plate: number | null, channel: string | null) => void;
};

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function displayValue(value: unknown, scale: string): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  const number = numeric(value);
  if (number === null) return value == null ? "\u2014" : String(value);
  if (/ratio|share|fraction|percent/i.test(scale)) {
    return `${(number * 100).toFixed(1)}%`;
  }
  return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

export default function QcOverview({
  rows,
  dictionary,
  selectedPlate,
  selectedChannel,
  onSelect,
}: Props) {
  const plates = useMemo(
    () =>
      [...new Set(rows.map((row) => Number(row.plate)).filter(Number.isFinite))].sort(
        (left, right) => left - right,
      ),
    [rows],
  );
  const channels = useMemo(() => overviewChannels(rows), [rows]);
  // A restored selection opens the overview. After that only the reader opens
  // or closes it, so clearing the selection leaves it open.
  const [open, setOpen] = useState(selectedPlate !== null || selectedChannel !== null);
  const definitions = dictionary.filter(
    (definition) =>
      definition.file === "triage_plate_channel.csv" && definition.metric !== "",
  );
  const [selectedMetric, setSelectedMetric] = useState("");
  const selectedDefinition =
    definitions.find((definition) => definition.column === selectedMetric) ??
    definitions.find(
      (definition) => qcDisplayLabel(definition.label) === "Automatic exclusion",
    ) ??
    definitions[0];
  const byKey = useMemo(
    () =>
      new Map(
        rows.map((row) => [`${String(row.plate)}\u0000${String(row.channel)}`, row]),
      ),
    [rows],
  );

  if (definitions.length === 0 || plates.length === 0 || channels.length === 0) {
    return (
      <section className="panel stack" aria-labelledby="qc-overview-heading">
        <h2 id="qc-overview-heading">Plate overview</h2>
        <p className="muted">No dictionary-labelled triage panel is available.</p>
      </section>
    );
  }

  return (
    <details
      className="panel qc-overview-disclosure"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary id="qc-overview-heading">
        <span>Plate overview</span>
        <small>Compare one measure across plates and channels</small>
      </summary>
      <div className="stack qc-overview-content">
      <div className="section-heading">
        <label className="qc-overview-metric">
          <span>Measure</span>
          <select
            value={selectedDefinition?.column ?? ""}
            onChange={(event) => setSelectedMetric(event.target.value)}
          >
            {definitions.map((definition) => (
              <option key={definition.column} value={definition.column}>
                {qcDisplayLabel(definition.label)}
              </option>
            ))}
          </select>
        </label>
        {(selectedPlate !== null || selectedChannel !== null) && (
          <button type="button" className="secondary" onClick={() => onSelect(null, null)}>
            Clear overview filter
          </button>
        )}
      </div>
      {selectedDefinition?.question && (
        <p className="muted qc-overview-question">
          {qcDisplayQuestion(selectedDefinition.question)}
        </p>
      )}
      <div className="qc-overview-scroll">
        <table className="qc-overview-table" data-testid="qc-overview">
          <caption className="visually-hidden">
            Dictionary-defined triage measures by plate and channel
          </caption>
          <thead>
            <tr>
              <th scope="col">Plate</th>
              {channels.map((channel) => (
                <th key={channel} scope="col">
                  {channel}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {plates.map((plate) => (
              <tr key={plate}>
                <th scope="row">{plate}</th>
                {channels.map((channel) => {
                  const row = byKey.get(`${plate}\u0000${channel}`);
                  const active = selectedPlate === plate && selectedChannel === channel;
                  const summary = selectedDefinition
                    ? `${qcDisplayLabel(selectedDefinition.label)} ${displayValue(
                        row?.[selectedDefinition.column],
                        selectedDefinition.scale,
                      )}`
                    : "No measure";
                  return (
                    <td key={channel}>
                      {row ? (
                        <button
                          type="button"
                          className={active ? "qc-triage-cell active" : "qc-triage-cell"}
                          aria-pressed={active}
                          aria-label={`Plate ${plate}, ${channel}: ${summary}`}
                          title={
                            selectedDefinition?.question
                              ? qcDisplayQuestion(selectedDefinition.question)
                              : summary
                          }
                          onClick={() => onSelect(active ? null : plate, active ? null : channel)}
                        >
                          {selectedDefinition && (
                            <span>
                              <strong>{qcDisplayLabel(selectedDefinition.label)}</strong>
                              <small>
                                {displayValue(
                                  row[selectedDefinition.column],
                                  selectedDefinition.scale,
                                )}
                              </small>
                            </span>
                          )}
                        </button>
                      ) : (
                        <span aria-label="No triage value">\u2014</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
    </details>
  );
}
