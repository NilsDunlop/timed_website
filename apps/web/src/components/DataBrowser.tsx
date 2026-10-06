import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";

import type { components } from "../lib/api/dataserver-schema";
import { apiErrorMessage, dataServerClient } from "../lib/api/client";
import PlateMetadataPanel, {
  doseLabel,
  type PlateMetadata,
  type WellMetadata,
} from "./PlateMetadataPanel";

type Dataset = components["schemas"]["DatasetRecord"];
type Entry = components["schemas"]["FilesystemEntry"];
type Summary =
  | components["schemas"]["ImageSummary"]
  | components["schemas"]["PlateSummary"];
type Field = components["schemas"]["FieldSummary"];
type Breadcrumb = { label: string; path: string | null };
type WellSelection = {
  datasetId: string;
  datasetName: string;
  path: string;
  fields: Field[];
  fieldPath: string;
};
type ViewerSource = {
  datasetId: string;
  imagePath: string;
};

const metadataColors = [
  "#3b82a0",
  "#b86b35",
  "#668f3d",
  "#9367a8",
  "#c24f65",
  "#9a812f",
];

function viewerHref(primary: ViewerSource, comparison?: ViewerSource): string {
  const query = new URLSearchParams({
    dataset: primary.datasetId,
    path: primary.imagePath,
  });
  if (comparison) {
    query.set("compare", comparison.imagePath);
    if (comparison.datasetId !== primary.datasetId) {
      query.set("compareDataset", comparison.datasetId);
    }
  }
  return "/view?" + query.toString();
}

export default function DataBrowser() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([
    { label: "Data roots", path: null },
  ]);
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [metadata, setMetadata] = useState<PlateMetadata | null>(null);
  const [selectedWells, setSelectedWells] = useState<WellSelection[]>([]);
  const [compoundFilter, setCompoundFilter] = useState("");
  const [moaFilter, setMoaFilter] = useState("");
  const [wellKind, setWellKind] = useState<"all" | "treatment" | "control">("all");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const currentPath = breadcrumbs.at(-1)?.path ?? null;

  const loadListing = useCallback(async (path: string | null) => {
    const result = await dataServerClient.GET("/api/fs/list", {
      params: { query: path ? { path } : {} },
    });
    if (!result.data) {
      throw new Error(apiErrorMessage(result.error, "Could not list data folder"));
    }
    setEntries(result.data.entries);
  }, []);

  const loadDatasets = useCallback(async () => {
    const result = await dataServerClient.GET("/api/datasets");
    if (!result.data) {
      throw new Error(apiErrorMessage(result.error, "Could not list linked datasets"));
    }
    setDatasets(result.data.datasets);
    return result.data.datasets;
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      setBusy(true);
      try {
        const [, linked] = await Promise.all([loadListing(null), loadDatasets()]);
        if (active && linked.length > 0) {
          await selectDataset(linked[0]);
        }
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (active) setBusy(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [loadDatasets, loadListing]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function browse(entry: Entry) {
    await run(async () => {
      await loadListing(entry.path);
      setBreadcrumbs((current) => [
        ...current,
        { label: entry.name, path: entry.path },
      ]);
    });
  }

  async function browseCrumb(index: number) {
    await run(async () => {
      const target = breadcrumbs[index];
      await loadListing(target.path);
      setBreadcrumbs((current) => current.slice(0, index + 1));
    });
  }

  async function link(entry: Entry) {
    await run(async () => {
      const result = await dataServerClient.POST("/api/datasets", {
        body: { path: entry.path },
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Could not link dataset"));
      }
      await loadDatasets();
      await selectDataset(result.data);
    });
  }

  async function unlink(dataset: Dataset) {
    await run(async () => {
      const result = await dataServerClient.DELETE("/api/datasets/{dataset_id}", {
        params: { path: { dataset_id: dataset.id } },
      });
      if (result.response.status !== 204) {
        throw new Error(apiErrorMessage(result.error, "Could not unlink dataset"));
      }
      const linked = await loadDatasets();
      setSelectedWells((current) =>
        current.filter((well) => well.datasetId !== dataset.id),
      );
      if (selected?.id === dataset.id) {
        setSelected(null);
        setSummary(null);
        setMetadata(null);
        if (linked.length > 0) await selectDataset(linked[0]);
      }
    });
  }

  async function selectDataset(dataset: Dataset) {
    const [summaryResult, metadataResult] = await Promise.all([
      dataServerClient.GET("/api/datasets/{dataset_id}/summary", {
        params: { path: { dataset_id: dataset.id } },
      }),
      dataServerClient.GET("/api/datasets/{dataset_id}/metadata", {
        params: { path: { dataset_id: dataset.id } },
      }),
    ]);
    if (!summaryResult.data) {
      throw new Error(
        apiErrorMessage(summaryResult.error, "Could not load dataset summary"),
      );
    }
    if (!metadataResult.data && metadataResult.response.status !== 404) {
      throw new Error(
        apiErrorMessage(metadataResult.error, "Could not load plate metadata"),
      );
    }
    setSelected(dataset);
    setSummary(summaryResult.data);
    setMetadata(metadataResult.data ?? null);
    setCompoundFilter("");
    setMoaFilter("");
    setWellKind("all");
  }

  async function toggleWell(wellPath: string) {
    if (!selected) return;
    if (
      selectedWells.some(
        (well) => well.datasetId === selected.id && well.path === wellPath,
      )
    ) {
      setSelectedWells((current) =>
        current.filter(
          (well) => well.datasetId !== selected.id || well.path !== wellPath,
        ),
      );
      return;
    }
    if (selectedWells.length >= 2) return;
    await run(async () => {
      const result = await dataServerClient.GET(
        "/api/datasets/{dataset_id}/wells/{well_path}",
        {
          params: {
            path: { dataset_id: selected.id, well_path: wellPath },
          },
        },
      );
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Could not load well fields"));
      }
      const fields = result.data.fields;
      setSelectedWells((current) => {
        if (
          current.length >= 2 ||
          current.some(
            (well) => well.datasetId === selected.id && well.path === wellPath,
          )
        ) {
          return current;
        }
        return [
          ...current,
          {
            datasetId: selected.id,
            datasetName: selected.name,
            path: wellPath,
            fields,
            fieldPath: fields[0]?.path ?? "",
          },
        ];
      });
    });
  }

  function selectField(datasetId: string, wellPath: string, fieldPath: string) {
    setSelectedWells((current) =>
      current.map((well) =>
        well.datasetId === datasetId && well.path === wellPath
          ? { ...well, fieldPath }
          : well,
      ),
    );
  }

  function removeWell(datasetId: string, wellPath: string) {
    setSelectedWells((current) =>
      current.filter(
        (well) => well.datasetId !== datasetId || well.path !== wellPath,
      ),
    );
  }

  const wells = useMemo(() => {
    if (!summary || summary.kind !== "plate") return new Map<string, string>();
    return new Map(
      summary.wells.map((well) => [well.row + "\u0000" + well.column, well.path]),
    );
  }, [summary]);

  const metadataByWell = useMemo(
    () =>
      new Map(
        (metadata?.wells ?? []).map((well) => [well.well.toUpperCase(), well]),
      ),
    [metadata],
  );

  const compoundOptions = useMemo(() => {
    const compounds = new Map<string, Set<number>>();
    for (const well of metadata?.wells ?? []) {
      if (!well.compound || well.is_control) continue;
      const doses = compounds.get(well.compound) ?? new Set<number>();
      if (well.dose_um != null) doses.add(well.dose_um);
      compounds.set(well.compound, doses);
    }
    return Array.from(compounds, ([compound, doses]) => ({
      compound,
      doses: Array.from(doses).sort((left, right) => left - right),
    })).sort((left, right) => left.compound.localeCompare(right.compound));
  }, [metadata]);

  const moaOptions = useMemo(
    () =>
      Array.from(
        new Set(
          (metadata?.wells ?? [])
            .map((well) => well.moa)
            .filter((moa): moa is string => Boolean(moa)),
        ),
      ).sort(),
    [metadata],
  );

  const moaColors = useMemo(
    () =>
      new Map(
        moaOptions.map((moa, index) => [
          moa,
          metadataColors[index % metadataColors.length],
        ]),
      ),
    [moaOptions],
  );

  const filtersActive =
    compoundFilter !== "" || moaFilter !== "" || wellKind !== "all";

  function wellMatches(well: WellMetadata | undefined): boolean {
    if (!well) return false;
    if (compoundFilter && well.compound !== compoundFilter) return false;
    if (moaFilter && well.moa !== moaFilter) return false;
    if (wellKind === "control" && !well.is_control) return false;
    if (wellKind === "treatment" && well.is_control) return false;
    return true;
  }

  function clearMetadataFilters() {
    setCompoundFilter("");
    setMoaFilter("");
    setWellKind("all");
  }

  return (
    <div className="page-grid">
      <section className="panel stack" aria-labelledby="browser-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Filesystem</p>
            <h2 id="browser-heading">Browse data roots</h2>
          </div>
          {busy && <span className="muted">Loading…</span>}
        </div>

        {error && (
          <div className="notice error" role="alert">
            <strong>{error}</strong>
            <p>
              If the connector is disconnected, return to <a href="/">Connect</a>.
            </p>
          </div>
        )}

        <nav className="breadcrumbs" aria-label="Data path">
          {breadcrumbs.map((crumb, index) => (
            <span key={(crumb.path ?? "roots") + String(index)}>
              {index > 0 && <span aria-hidden="true"> / </span>}
              <button
                type="button"
                className="text-button"
                disabled={busy || index === breadcrumbs.length - 1}
                onClick={() => browseCrumb(index)}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </nav>
        {currentPath && <code className="path">{currentPath}</code>}

        <ul className="file-list" aria-label="Folder contents">
          {entries.map((entry) => (
            <li key={entry.path}>
              <div>
                <strong>{entry.name}</strong>
                <span className="entry-kind">
                  {entry.ome_zarr
                    ? "OME-Zarr " +
                      entry.ome_zarr.kind +
                      " · " +
                      entry.ome_zarr.version +
                      " · Zarr v" +
                      String(entry.ome_zarr.zarr_format)
                    : entry.kind}
                </span>
              </div>
              {entry.ome_zarr &&
              (entry.ome_zarr.kind === "plate" ||
                entry.ome_zarr.kind === "image") ? (
                <button type="button" disabled={busy} onClick={() => link(entry)}>
                  Link
                </button>
              ) : entry.kind === "dir" ? (
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => browse(entry)}
                >
                  Open folder
                </button>
              ) : null}
            </li>
          ))}
          {!busy && entries.length === 0 && <li className="muted">Folder is empty.</li>}
        </ul>
      </section>

      <section className="panel stack" aria-labelledby="datasets-heading">
        <div>
          <p className="eyebrow">Registry</p>
          <h2 id="datasets-heading">Linked datasets</h2>
        </div>
        {datasets.length === 0 ? (
          <p className="muted">Link an OME-Zarr plate or image from the browser.</p>
        ) : (
          <ul className="dataset-list">
            {datasets.map((dataset) => (
              <li key={dataset.id} className={selected?.id === dataset.id ? "selected" : ""}>
                <button
                  type="button"
                  className="text-button dataset-name"
                  disabled={busy}
                  onClick={() => run(() => selectDataset(dataset))}
                >
                  {dataset.name}
                </button>
                <span className="entry-kind">
                  {dataset.ome_zarr.kind} · OME-Zarr {dataset.ome_zarr.version}
                </span>
                <button
                  type="button"
                  className="danger-link"
                  disabled={busy}
                  onClick={() => unlink(dataset)}
                >
                  Unlink
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {selected && summary && (
        <section className="panel summary-panel stack" aria-labelledby="summary-heading">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{summary.kind}</p>
              <h2 id="summary-heading">{selected.name}</h2>
            </div>
            {summary.kind === "image" && (
              <a
                className="button-link"
                href={viewerHref({
                  datasetId: selected.id,
                  imagePath: summary.path,
                })}
              >
                Open image
              </a>
            )}
          </div>

          <dl className="status-grid">
            <div>
              <dt>Format</dt>
              <dd>
                OME-Zarr {summary.ome_zarr_version} · Zarr v{summary.zarr_format}
              </dd>
            </div>
            {summary.kind === "image" && summary.levels[0] && (
              <>
                <div>
                  <dt>Shape</dt>
                  <dd>{summary.levels[0].shape.join(" × ")}</dd>
                </div>
                <div>
                  <dt>Type</dt>
                  <dd>{summary.levels[0].dtype}</dd>
                </div>
              </>
            )}
            {summary.kind === "plate" && (
              <>
                <div>
                  <dt>Wells</dt>
                  <dd>{summary.wells.length}</dd>
                </div>
                <div>
                  <dt>Acquisitions</dt>
                  <dd>{summary.acquisitions.length || "Not specified"}</dd>
                </div>
              </>
            )}
          </dl>

          {summary.kind === "plate" && (
            <>
              {metadata?.available && (
                <div className="metadata-filter-panel stack">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Well highlighting</p>
                      <h3>Find treatments</h3>
                    </div>
                    {filtersActive && (
                      <button
                        type="button"
                        className="text-button"
                        onClick={clearMetadataFilters}
                      >
                        Clear filters
                      </button>
                    )}
                  </div>
                  <div className="metadata-filters">
                    <label>
                      <span>Compound and dose</span>
                      <select
                        data-testid="compound-filter"
                        value={compoundFilter}
                        onChange={(event) => setCompoundFilter(event.target.value)}
                      >
                        <option value="">All compounds</option>
                        {compoundOptions.map(({ compound, doses }) => (
                          <option key={compound} value={compound}>
                            {compound}
                            {doses.length > 0
                              ? " \u00b7 " + doses.map(doseLabel).join(", ")
                              : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>Mechanism class</span>
                      <select
                        data-testid="moa-filter"
                        value={moaFilter}
                        onChange={(event) => setMoaFilter(event.target.value)}
                      >
                        <option value="">All MOA classes</option>
                        {moaOptions.map((moa) => (
                          <option key={moa} value={moa}>
                            {moa}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>Well type</span>
                      <select
                        data-testid="well-kind-filter"
                        value={wellKind}
                        onChange={(event) =>
                          setWellKind(
                            event.target.value as
                              | "all"
                              | "treatment"
                              | "control",
                          )
                        }
                      >
                        <option value="all">All wells</option>
                        <option value="treatment">Treatments</option>
                        <option value="control">Controls</option>
                      </select>
                    </label>
                  </div>
                </div>
              )}

              <PlateMetadataPanel
                metadata={metadata}
                selectedWell={
                  selectedWells.find((well) => well.datasetId === selected.id)?.path
                }
              />

              <div className="selection-hint">
                <strong>
                  {selectedWells.length === 0
                    ? "Select a well"
                    : selectedWells.length === 1
                      ? "One well selected"
                      : "Two wells selected"}
                </strong>
                <span className="selection-help">
                  <button
                    type="button"
                    className="selection-info"
                    aria-label="Selection help"
                    aria-describedby="well-selection-help"
                  >
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <circle cx="10" cy="10" r="8" />
                      <path d="M10 9v5M10 6.2v.1" />
                    </svg>
                  </button>
                  <span
                    id="well-selection-help"
                    className="selection-tooltip"
                    role="tooltip"
                  >
                    Choose one well to inspect it, or keep it in the comparison
                    tray while you open another linked experiment and choose a
                    second well.
                  </span>
                </span>
              </div>

              {metadata?.available && (
                <div
                  className="metadata-legend well-selector-legend"
                  aria-label="Well color legend"
                >
                  {moaOptions.map((moa) => (
                    <span key={moa}>
                      <i
                        aria-hidden="true"
                        style={
                          {
                            "--legend-color": moaColors.get(moa),
                          } as CSSProperties
                        }
                      />
                      {moa}
                    </span>
                  ))}
                  {(metadata.wells ?? []).some((well) => well.is_control) && (
                    <span>
                      <i className="control" aria-hidden="true" />
                      Control
                    </span>
                  )}
                </div>
              )}

              <div
                className="well-grid"
                role="grid"
                aria-label="Plate wells"
                style={{
                  gridTemplateColumns:
                    "minmax(2.5rem, auto) repeat(" +
                    String(summary.columns.length) +
                    ", minmax(3rem, 1fr))",
                }}
              >
                <span aria-hidden="true" />
                {summary.columns.map((column) => (
                  <strong key={column} className="well-label">
                    {column}
                  </strong>
                ))}
                {summary.rows.flatMap((row) => [
                  <strong key={"row-" + row} className="well-label">
                    {row}
                  </strong>,
                  ...summary.columns.map((column) => {
                    const path = wells.get(row + "\u0000" + column);
                    const wellMetadata = metadataByWell.get(
                      row.toUpperCase() + String(column).padStart(2, "0"),
                    );
                    const matches = wellMatches(wellMetadata);
                    const color = wellMetadata?.is_control
                      ? "#7b8490"
                      : wellMetadata?.moa
                        ? moaColors.get(wellMetadata.moa)
                        : undefined;
                    const selectedIndex = path
                      ? selectedWells.findIndex(
                          (well) =>
                            well.datasetId === selected.id && well.path === path,
                        )
                      : -1;
                    const classes = [
                      "well",
                      selectedIndex >= 0 ? "selected" : "",
                      wellMetadata?.is_control ? "well-control" : "",
                      filtersActive && matches ? "well-highlighted" : "",
                      filtersActive && !matches ? "well-dimmed" : "",
                    ]
                      .filter(Boolean)
                      .join(" ");
                    const description = wellMetadata
                      ? [
                          wellMetadata.compound,
                          wellMetadata.dose_um == null
                            ? null
                            : doseLabel(wellMetadata.dose_um),
                          wellMetadata.is_control ? "Control" : wellMetadata.moa,
                        ]
                          .filter(Boolean)
                          .join(" \u00b7 ")
                      : undefined;
                    return path ? (
                      <button
                        type="button"
                        key={row + column}
                        className={classes}
                        style={
                          color
                            ? ({ "--well-color": color } as CSSProperties)
                            : undefined
                        }
                        disabled={
                          busy ||
                          (selectedWells.length >= 2 && selectedIndex < 0)
                        }
                        aria-label={"Well " + row + column}
                        aria-pressed={selectedIndex >= 0}
                        title={description}
                        onClick={() => toggleWell(path)}
                      >
                        {row + column}
                      </button>
                    ) : (
                      <span
                        key={row + column}
                        className="well missing"
                        aria-label={"Well " + row + column + " absent"}
                      >
                        —
                      </span>
                    );
                  }),
                ])}
              </div>

              {selectedWells.length > 0 && (
                <div
                  className="subpanel well-selection-panel"
                  data-testid="well-selection"
                >
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Comparison tray</p>
                      <h3>
                        {selectedWells.length === 1
                          ? "Choose a site for side A"
                          : "Choose a site from each well"}
                      </h3>
                    </div>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setSelectedWells([])}
                    >
                      Clear tray
                    </button>
                  </div>

                  <div
                    className={
                      "comparison-field-grid" +
                      (selectedWells.length === 1 ? " comparison-field-grid-single" : "")
                    }
                  >
                    {selectedWells.map((well, index) => (
                      <div
                        className="comparison-source-card"
                        key={well.datasetId + "\u0000" + well.path}
                      >
                        <div className="comparison-source-heading">
                          <span className="comparison-index" aria-hidden="true">
                            {index === 0 ? "A" : "B"}
                          </span>
                          <span className="comparison-source-identity">
                            <strong>{well.datasetName}</strong>
                            <small>{well.path}</small>
                          </span>
                          <button
                            type="button"
                            className="text-button"
                            aria-label={
                              "Remove " + well.datasetName + " " + well.path + " from tray"
                            }
                            onClick={() => removeWell(well.datasetId, well.path)}
                          >
                            Remove
                          </button>
                        </div>
                        <label>
                          <span>Site</span>
                          <select
                            aria-label={
                              "Site for " + well.datasetName + " " + well.path
                            }
                            value={well.fieldPath}
                            onChange={(event) =>
                              selectField(
                                well.datasetId,
                                well.path,
                                event.target.value,
                              )
                            }
                          >
                            {well.fields.map((field, fieldIndex) => (
                              <option key={field.path} value={field.path}>
                                Site {fieldIndex + 1}
                                {field.acquisition != null
                                  ? " · acquisition " + String(field.acquisition)
                                  : ""}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                    ))}
                  </div>

                  <div className="comparison-action">
                    <p className="muted">
                      {selectedWells.length === 1
                        ? "Inspect side A now, or open another well to add side B."
                        : "Time, channels, pan, and zoom will stay synchronized."}
                    </p>
                    {selectedWells.length === 1 && selectedWells[0].fieldPath && (
                      <a
                        className="button-link"
                        data-testid="open-selected"
                        href={viewerHref({
                          datasetId: selectedWells[0].datasetId,
                          imagePath: selectedWells[0].fieldPath,
                        })}
                      >
                        Open trajectory
                      </a>
                    )}
                    {selectedWells.length === 2 &&
                      selectedWells.every((well) => well.fieldPath) && (
                        <a
                          className="button-link"
                          data-testid="open-comparison"
                          href={viewerHref(
                            {
                              datasetId: selectedWells[0].datasetId,
                              imagePath: selectedWells[0].fieldPath,
                            },
                            {
                              datasetId: selectedWells[1].datasetId,
                              imagePath: selectedWells[1].fieldPath,
                            },
                          )}
                        >
                          Compare trajectories
                        </a>
                      )}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
