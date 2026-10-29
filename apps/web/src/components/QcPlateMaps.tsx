import { useMemo } from "react";

import type { QcFilterResult, QcModel } from "../lib/qcModel";
import PlateGrid, { plateGridKey, type PlateGridWell } from "./PlateGrid";

export type QcWellTarget = {
  experiment: string;
  plate: number;
  well: string;
  site: number;
};

type Props = {
  model: QcModel;
  result: QcFilterResult;
  selectedPlate: number | null;
  onOpen: (target: QcWellTarget) => void;
};

const PLATE_ROWS = "ABCDEFGHIJKLMNOP".split("");
const PLATE_COLUMNS = Array.from({ length: 24 }, (_, index) => String(index + 1));

function numberAt(model: QcModel, column: string, rowIndex: number): number | null {
  const value = model.columns[column]?.[rowIndex];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringAt(model: QcModel, column: string, rowIndex: number): string {
  const value = model.columns[column]?.[rowIndex];
  return value == null ? "" : String(value);
}

function shade(flagged: number): string {
  if (flagged <= 0) return "#dce8dd";
  const lightness = Math.max(36, 88 - Math.min(9, flagged) * 5.5);
  return `hsl(8 68% ${lightness}%)`;
}

function displayLabel(value: string): string {
  return value
    .replace(/__/g, " \u00b7 ")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toLocaleUpperCase());
}

export default function QcPlateMaps({ model, result, selectedPlate, onOpen }: Props) {
  const plates = useMemo(
    () =>
      [...new Set((model.columns.plate ?? []).map(Number).filter(Number.isFinite))].sort(
        (left, right) => left - right,
      ),
    [model],
  );
  const included = useMemo(() => new Set(result.baseRowIndices), [result.baseRowIndices]);
  const flagged = useMemo(() => new Set(result.flaggedRowIndices), [result.flaggedRowIndices]);
  const activeFlagColumns = Object.keys(result.flaggedBySlider);

  const maps = useMemo(() => {
    const byPlate = new Map<number, Map<string, PlateGridWell & { experiment: string }>>();
    const rowsByWell = new Map<string, number[]>();
    for (let rowIndex = 0; rowIndex < model.rowCount; rowIndex += 1) {
      const plate = numberAt(model, "plate", rowIndex);
      const well = stringAt(model, "well", rowIndex);
      if (plate === null || !well) continue;
      const key = `${plate}\u0000${well}`;
      const rows = rowsByWell.get(key);
      if (rows) rows.push(rowIndex);
      else rowsByWell.set(key, [rowIndex]);
    }

    for (const [key, rowIndices] of rowsByWell) {
      const separator = key.indexOf("\u0000");
      const plate = Number(key.slice(0, separator));
      const well = key.slice(separator + 1);
      const first = rowIndices[0];
      const row = stringAt(model, "row", first) || well.replace(/\d+$/, "");
      const numericColumn = numberAt(model, "column", first);
      const column = numericColumn === null ? String(Number(well.replace(/^\D+/, ""))) : String(numericColumn);
      const relevant = rowIndices.filter((rowIndex) => included.has(rowIndex));
      const flaggedRows = relevant.filter((rowIndex) => flagged.has(rowIndex));
      const flaggedSites = flaggedRows.map((rowIndex) => {
        const site = stringAt(model, "site", rowIndex);
        const values = activeFlagColumns
          .map((columnName) => {
            const definition = model.sliderByColumn[columnName];
            const value = model.columns[columnName]?.[rowIndex];
            return definition && value != null
              ? `${displayLabel(definition.label)} ${String(value)}`
              : null;
          })
          .filter(Boolean)
          .join(", ");
        return values ? `site ${site}: ${values}` : `site ${site}`;
      });
      const title =
        relevant.length === 0
          ? `${well}: excluded by current filters`
          : flaggedRows.length === 0
            ? `${well}: no flagged sites`
            : `${well}: ${flaggedSites.join("; ")}`;
      const wells = byPlate.get(plate) ?? new Map();
      wells.set(plateGridKey(row, column), {
        id: well,
        row,
        column,
        color: shade(flaggedRows.length),
        badge: flaggedRows.length > 0 ? flaggedRows.length : undefined,
        dimmed: relevant.length === 0,
        title,
        experiment: stringAt(model, "experiment", first),
      });
      byPlate.set(plate, wells);
    }
    return byPlate;
  }, [activeFlagColumns, flagged, included, model]);

  return (
    <section className="panel stack" aria-labelledby="qc-maps-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Spatial review</p>
          <h2 id="qc-maps-heading">Plate maps</h2>
        </div>
        <span className="muted">Shade = flagged sites per well</span>
      </div>
      <div className="qc-plate-maps" data-testid="qc-plate-maps">
        {plates.map((plate) => {
          const wells = maps.get(plate) ?? new Map();
          return (
            <article
              className={
                selectedPlate !== null && selectedPlate !== plate
                  ? "qc-plate-map qc-plate-map-dimmed"
                  : "qc-plate-map"
              }
              key={plate}
              data-qc-plate={plate}
            >
              <h3>Plate {plate}</h3>
              <PlateGrid
                density="compact"
                rows={PLATE_ROWS}
                columns={PLATE_COLUMNS}
                wells={wells}
                ariaLabel={`QC map for plate ${plate}`}
                onSelect={(well) => {
                  const target = well as PlateGridWell & { experiment: string };
                  onOpen({
                    experiment: target.experiment,
                    plate,
                    well: target.id,
                    site: 1,
                  });
                }}
              />
            </article>
          );
        })}
      </div>
    </section>
  );
}
