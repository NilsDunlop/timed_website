import { useMemo } from "react";

import type { QcColumnDefinition } from "../lib/qcModel";

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

function displayLabel(value: string): string {
  return value
    .replace(/__/g, " \u00b7 ")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toLocaleUpperCase());
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
  const channels = useMemo(
    () => [...new Set(rows.map((row) => String(row.channel ?? "")))].filter(Boolean),
    [rows],
  );
  const definitions = dictionary.filter(
    (definition) =>
      definition.file === "triage_plate_channel.csv" && definition.layer === "panel",
  );
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
    <section className="panel stack" aria-labelledby="qc-overview-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Triage</p>
          <h2 id="qc-overview-heading">Plate overview</h2>
        </div>
        {(selectedPlate !== null || selectedChannel !== null) && (
          <button type="button" className="secondary" onClick={() => onSelect(null, null)}>
            Clear overview filter
          </button>
        )}
      </div>
      <ul className="qc-overview-legend" aria-label="Triage measures">
        {definitions.map((definition) => (
          <li key={definition.column}>
            <strong>{displayLabel(definition.label)}</strong>
            <span>{definition.question}</span>
          </li>
        ))}
      </ul>
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
                  const summary = definitions
                    .map(
                      (definition) =>
                        `${displayLabel(definition.label)} ${displayValue(
                          row?.[definition.column],
                          definition.scale,
                        )}`,
                    )
                    .join(", ");
                  return (
                    <td key={channel}>
                      {row ? (
                        <button
                          type="button"
                          className={active ? "qc-triage-cell active" : "qc-triage-cell"}
                          aria-pressed={active}
                          aria-label={`Plate ${plate}, ${channel}: ${summary}`}
                          title={definitions
                            .map(
                              (definition) =>
                                `${displayLabel(definition.label)}: ${definition.question}`,
                            )
                            .join("\n")}
                          onClick={() => onSelect(active ? null : plate, active ? null : channel)}
                        >
                          {definitions.map((definition) => (
                            <span key={definition.column}>
                              <strong>{displayLabel(definition.label)}</strong>
                              <small>{displayValue(row[definition.column], definition.scale)}</small>
                            </span>
                          ))}
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
    </section>
  );
}
