import type { CSSProperties, ReactNode } from "react";

export type PlateGridMarker = {
  label: string;
  color?: string;
};

export type PlateGridWell = {
  id: string;
  row: string;
  column: string;
  color?: string;
  badge?: ReactNode;
  markers?: readonly PlateGridMarker[];
  selected?: boolean;
  control?: boolean;
  highlighted?: boolean;
  dimmed?: boolean;
  disabled?: boolean;
  title?: string;
};

type Props = {
  rows: readonly string[];
  columns: readonly string[];
  wells: ReadonlyMap<string, PlateGridWell>;
  onSelect: (well: PlateGridWell) => void;
  ariaLabel?: string;
};

export function plateGridKey(row: string, column: string): string {
  return row + "\u0000" + column;
}

export default function PlateGrid({
  rows,
  columns,
  wells,
  onSelect,
  ariaLabel = "Plate wells",
}: Props) {
  return (
    <div
      className="well-grid"
      role="grid"
      aria-label={ariaLabel}
      style={{
        gridTemplateColumns:
          "minmax(2.5rem, auto) repeat(" +
          String(columns.length) +
          ", minmax(3rem, 1fr))",
      }}
    >
      <span aria-hidden="true" />
      {columns.map((column) => (
        <strong key={column} className="well-label">
          {column}
        </strong>
      ))}
      {rows.flatMap((row) => [
        <strong key={"row-" + row} className="well-label">
          {row}
        </strong>,
        ...columns.map((column) => {
          const key = plateGridKey(row, column);
          const well = wells.get(key);
          const label = row + column;
          if (!well) {
            return (
              <span
                key={key}
                className="well missing"
                aria-label={"Well " + label + " absent"}
              >
                {"\u2014"}
              </span>
            );
          }
          const classes = [
            "well",
            well.selected ? "selected" : "",
            well.control ? "well-control" : "",
            well.highlighted ? "well-highlighted" : "",
            well.dimmed ? "well-dimmed" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <button
              type="button"
              key={key}
              className={classes}
              style={
                well.color
                  ? ({ "--well-color": well.color } as CSSProperties)
                  : undefined
              }
              disabled={Boolean(well.disabled)}
              aria-label={"Well " + label}
              aria-pressed={Boolean(well.selected)}
              title={well.title}
              onClick={() => onSelect(well)}
            >
              {label}
              {well.badge != null && (
                <span className="well-badge">{well.badge}</span>
              )}
              {well.markers && well.markers.length > 0 && (
                <span className="well-markers" aria-hidden="true">
                  {well.markers.map((marker, index) => (
                    <i
                      key={marker.label + String(index)}
                      title={marker.label}
                      style={
                        marker.color
                          ? ({
                              "--well-marker-color": marker.color,
                            } as CSSProperties)
                          : undefined
                      }
                    />
                  ))}
                </span>
              )}
            </button>
          );
        }),
      ])}
    </div>
  );
}
