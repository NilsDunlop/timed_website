import { useMemo } from "react";

import type {
  QcFilterResult,
  QcModel,
  QcSliderDefinition,
  QcSliderState,
} from "../lib/qcModel";

type Props = {
  model: QcModel;
  result: QcFilterResult;
  state: Readonly<Record<string, QcSliderState>>;
  selectedChannel: string | null;
  search: string;
  onSearch: (value: string) => void;
  onChange: (column: string, state: QcSliderState) => void;
};

function finite(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stepFor(slider: QcSliderDefinition): number {
  const span = slider.maximum - slider.minimum;
  return span > 0 ? span / 1000 : 1;
}

function formatNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function displayLabel(value: string): string {
  return value
    .replace(/__/g, " \u00b7 ")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toLocaleUpperCase());
}

function Histogram({ model, slider }: { model: QcModel; slider: QcSliderDefinition }) {
  const bins = useMemo(() => {
    const counts = Array.from({ length: 16 }, () => 0);
    const span = slider.maximum - slider.minimum;
    for (const value of model.columns[slider.column] ?? []) {
      if (typeof value !== "number" || !Number.isFinite(value)) continue;
      const position = span <= 0 ? 0 : (value - slider.minimum) / span;
      const index = Math.min(counts.length - 1, Math.max(0, Math.floor(position * counts.length)));
      counts[index] += 1;
    }
    return counts;
  }, [model, slider]);
  const maximum = Math.max(1, ...bins);
  const span = slider.maximum - slider.minimum;
  return (
    <span className="qc-histogram-wrap">
      <span className="qc-histogram" aria-hidden="true">
        {bins.map((count, index) => (
          <i key={index} style={{ height: `${Math.max(4, (count / maximum) * 100)}%` }} />
        ))}
      </span>
      {slider.markers.length > 0 && (
        <span className="qc-slider-markers" role="list" aria-label="Registered markers">
          {slider.markers.map((marker, index) => {
            const position =
              span <= 0 ? 0 : Math.max(0, Math.min(100, ((marker.value - slider.minimum) / span) * 100));
            const description = `${marker.label ? `${marker.label}: ` : ""}${formatNumber(marker.value)}`;
            return (
              <span
                className="qc-slider-marker"
                role="listitem"
                aria-label={description}
                title={description}
                style={{ left: `${position}%` }}
                key={`${marker.value}:${marker.label}:${index}`}
              />
            );
          })}
        </span>
      )}
    </span>
  );
}

function FilterBounds({
  slider,
  current,
  onChange,
}: {
  slider: QcSliderDefinition;
  current: QcSliderState;
  onChange: (state: QcSliderState) => void;
}) {
  const lower = finite(current.filterLo, slider.minimum);
  const upper = finite(current.filterHi, slider.maximum);
  const step = stepFor(slider);
  const label = displayLabel(slider.label);
  return (
    <div className="qc-slider-bounds">
      <label>
        <span>Filter minimum: {formatNumber(lower)}</span>
        <span className="qc-bound-editor">
          <input
            type="range"
            aria-label={`${label} filter minimum`}
            min={slider.minimum}
            max={slider.maximum}
            step={step}
            value={lower}
            onChange={(event) =>
              onChange({ ...current, filterLo: Math.min(Number(event.target.value), upper) })
            }
          />
          <input
            type="number"
            aria-label={`${label} filter minimum value`}
            min={slider.minimum}
            max={upper}
            step={step}
            value={lower}
            onChange={(event) =>
              onChange({ ...current, filterLo: Math.min(Number(event.target.value), upper) })
            }
          />
        </span>
      </label>
      <label>
        <span>Filter maximum: {formatNumber(upper)}</span>
        <span className="qc-bound-editor">
          <input
            type="range"
            aria-label={`${label} filter maximum`}
            min={slider.minimum}
            max={slider.maximum}
            step={step}
            value={upper}
            onChange={(event) =>
              onChange({ ...current, filterHi: Math.max(Number(event.target.value), lower) })
            }
          />
          <input
            type="number"
            aria-label={`${label} filter maximum value`}
            min={lower}
            max={slider.maximum}
            step={step}
            value={upper}
            onChange={(event) =>
              onChange({ ...current, filterHi: Math.max(Number(event.target.value), lower) })
            }
          />
        </span>
      </label>
    </div>
  );
}

function FlagBounds({
  slider,
  current,
  onChange,
}: {
  slider: QcSliderDefinition;
  current: QcSliderState;
  onChange: (state: QcSliderState) => void;
}) {
  const step = stepFor(slider);
  const showLower = slider.direction !== "high";
  const showUpper = slider.direction !== "low";
  const lower = finite(current.flagLo, slider.defaultFlagLo ?? slider.minimum);
  const upper = finite(current.flagHi, slider.defaultFlagHi ?? slider.maximum);
  const label = displayLabel(slider.label);
  return (
    <div className="qc-slider-bounds">
      {showLower && (
        <label>
          <span>Lower flag bound: {formatNumber(lower)}</span>
          <span className="qc-bound-editor">
            <input
              type="range"
              aria-label={`${label} lower flag bound`}
              min={slider.minimum}
              max={slider.maximum}
              step={step}
              value={lower}
              onChange={(event) => onChange({ ...current, flagLo: Number(event.target.value) })}
            />
            <input
              type="number"
              aria-label={`${label} lower flag bound value`}
              min={slider.minimum}
              max={slider.maximum}
              step={step}
              value={lower}
              onChange={(event) => onChange({ ...current, flagLo: Number(event.target.value) })}
            />
          </span>
        </label>
      )}
      {showUpper && (
        <label>
          <span>Upper flag bound: {formatNumber(upper)}</span>
          <span className="qc-bound-editor">
            <input
              type="range"
              aria-label={`${label} upper flag bound`}
              min={slider.minimum}
              max={slider.maximum}
              step={step}
              value={upper}
              onChange={(event) => onChange({ ...current, flagHi: Number(event.target.value) })}
            />
            <input
              type="number"
              aria-label={`${label} upper flag bound value`}
              min={slider.minimum}
              max={slider.maximum}
              step={step}
              value={upper}
              onChange={(event) => onChange({ ...current, flagHi: Number(event.target.value) })}
            />
          </span>
        </label>
      )}
    </div>
  );
}

export default function QcSliderPanel({
  model,
  result,
  state,
  selectedChannel,
  search,
  onSearch,
  onChange,
}: Props) {
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const visible = model.sliders.filter(
    (slider) =>
      (!selectedChannel || slider.channel === selectedChannel) &&
      (normalizedSearch
        ? [slider.label, slider.metric, slider.channel, slider.question]
            .join(" ")
            .toLocaleLowerCase()
            .includes(normalizedSearch)
        : true),
  );
  const channels = [...new Set(visible.map((slider) => slider.channel || "Site"))];

  return (
    <section className="panel stack qc-slider-panel" aria-labelledby="qc-sliders-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Rules and ranges</p>
          <h2 id="qc-sliders-heading">Metrics</h2>
        </div>
        <span className="muted">{model.sliders.length} primary sliders</span>
      </div>
      <label className="qc-slider-search">
        <span>Find a metric</span>
        <input
          type="search"
          value={search}
          placeholder="Search labels, channels, and questions"
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      {channels.map((channel) => (
        <section className="qc-slider-group stack" key={channel} aria-label={`${channel} metrics`}>
          <h3>{channel}</h3>
          {visible
            .filter((slider) => (slider.channel || "Site") === channel)
            .map((slider) => {
              const current = state[slider.column] ?? {};
              const flagged = result.flaggedBySlider[slider.column] ?? 0;
              const counts = result.countsBySlider[slider.column];
              return (
                <article className="qc-slider-card" key={slider.column} data-qc-slider-id={slider.column}>
                  <div className="qc-slider-heading">
                    <div>
                      <strong>{displayLabel(slider.label)}</strong>
                      <span className="muted">
                        {slider.scale || "value"} \u00b7 worse {slider.direction}
                      </span>
                    </div>
                    <span className={`qc-gate-badge qc-gate-${slider.gate}`}>{slider.gate}</span>
                    {slider.status === "pending_review" && (
                      <span className="qc-status-badge">Pending review</span>
                    )}
                  </div>
                  <p>{slider.question}</p>
                  <Histogram model={model} slider={slider} />
                  {slider.gate === "review" ? (
                    <>
                      <label className="qc-flag-toggle">
                        <input
                          type="checkbox"
                          checked={current.flagActive ?? true}
                          onChange={(event) =>
                            onChange(slider.column, {
                              ...current,
                              flagActive: event.target.checked,
                            })
                          }
                        />
                        Flag outside this rule
                      </label>
                      <FlagBounds
                        slider={slider}
                        current={current}
                        onChange={(next) => onChange(slider.column, next)}
                      />
                      <dl className="qc-slider-counts">
                        <div>
                          <dt>Fields</dt>
                          <dd>{flagged.toLocaleString()}</dd>
                        </div>
                        <div>
                          <dt>Wells</dt>
                          <dd>{(counts?.wells ?? 0).toLocaleString()}</dd>
                        </div>
                        <div>
                          <dt>Controls</dt>
                          <dd>{(counts?.controls ?? 0).toLocaleString()}</dd>
                        </div>
                        <div>
                          <dt>Protected</dt>
                          <dd>{(counts?.fieldsUnderProtection ?? 0).toLocaleString()}</dd>
                        </div>
                      </dl>
                    </>
                  ) : (
                    <FilterBounds
                      slider={slider}
                      current={current}
                      onChange={(next) => onChange(slider.column, next)}
                    />
                  )}
                  {slider.markers.length > 0 && (
                    <p className="qc-marker-line">
                      Registered markers:{" "}
                      {slider.markers
                        .map((marker) =>
                          `${marker.label ? `${marker.label} ` : ""}${formatNumber(marker.value)}`,
                        )
                        .join(", ")}
                    </p>
                  )}
                  {slider.rawColumn && slider.rawColumn !== slider.column && (
                    <p className="muted qc-raw-companion">Raw values are shown in the table.</p>
                  )}
                </article>
              );
            })}
        </section>
      ))}
      {visible.length === 0 && (
        <p className="muted">
          {selectedChannel
            ? `No primary sliders are registered for ${selectedChannel}.`
            : "No metrics match this search."}
        </p>
      )}
    </section>
  );
}
