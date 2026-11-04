import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type UIEvent,
} from "react";

export type QcTableColumn<Row> = {
  id: string;
  label: string;
  render: (row: Row) => ReactNode;
  sortable?: boolean;
};

type Props<Row> = {
  ariaLabel: string;
  rows: readonly Row[];
  columns: readonly QcTableColumn<Row>[];
  rowKey: (row: Row) => string;
  sortColumn: string | null;
  sortDirection: "asc" | "desc";
  onSort: (column: string) => void;
  onActivate?: (row: Row) => void;
  activateLabel?: (row: Row) => string;
};

const ROW_HEIGHT = 40;
const VIEWPORT_HEIGHT = 448;
const OVERSCAN = 8;

export default function QcVirtualTable<Row>({
  ariaLabel,
  rows,
  columns,
  rowKey,
  sortColumn,
  sortDirection,
  onSort,
  onActivate,
  activateLabel,
}: Props<Row>) {
  const [scrollTop, setScrollTop] = useState(0);
  const viewport = useRef<HTMLDivElement>(null);
  const window = useMemo(() => {
    const count = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
    const requested = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const first = Math.min(requested, Math.max(0, rows.length - count));
    const last = Math.min(rows.length, first + count);
    return { first, last, rows: rows.slice(first, last) };
  }, [rows, scrollTop]);

  useEffect(() => {
    const maximum = Math.max(0, rows.length * ROW_HEIGHT - VIEWPORT_HEIGHT);
    if (scrollTop <= maximum) return;
    setScrollTop(maximum);
    if (viewport.current) viewport.current.scrollTop = maximum;
  }, [rows.length, scrollTop]);

  function onScroll(event: UIEvent<HTMLDivElement>) {
    setScrollTop(event.currentTarget.scrollTop);
  }

  return (
    <div
      ref={viewport}
      className="qc-table-scroll"
      style={{ maxHeight: VIEWPORT_HEIGHT }}
      onScroll={onScroll}
      data-testid="qc-table-scroll"
    >
      <table className="qc-data-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.id}
                scope="col"
                aria-sort={
                  sortColumn === column.id
                    ? sortDirection === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
              >
                {column.sortable === false ? (
                  column.label
                ) : (
                  <button type="button" className="qc-sort-button" onClick={() => onSort(column.id)}>
                    {column.label}
                    {sortColumn === column.id ? (sortDirection === "asc" ? " \u2191" : " \u2193") : ""}
                  </button>
                )}
              </th>
            ))}
            {onActivate && <th scope="col">Open</th>}
          </tr>
        </thead>
        <tbody>
          {window.first > 0 && (
            <tr aria-hidden="true">
              <td colSpan={columns.length + (onActivate ? 1 : 0)} style={{ height: window.first * ROW_HEIGHT }} />
            </tr>
          )}
          {window.rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td key={column.id}>{column.render(row)}</td>
              ))}
              {onActivate && (
                <td>
                  <button type="button" className="text-button" onClick={() => onActivate(row)}>
                    {activateLabel?.(row) ?? "Open"}
                  </button>
                </td>
              )}
            </tr>
          ))}
          {window.last < rows.length && (
            <tr aria-hidden="true">
              <td
                colSpan={columns.length + (onActivate ? 1 : 0)}
                style={{ height: (rows.length - window.last) * ROW_HEIGHT }}
              />
            </tr>
          )}
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length + (onActivate ? 1 : 0)} className="muted">
                No rows match the current filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
