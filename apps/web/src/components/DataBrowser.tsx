import { useCallback, useEffect, useMemo, useState } from "react";

import type { components } from "../lib/api/dataserver-schema";
import { apiErrorMessage, dataServerClient } from "../lib/api/client";

type Dataset = components["schemas"]["DatasetRecord"];
type Entry = components["schemas"]["FilesystemEntry"];
type Summary =
  | components["schemas"]["ImageSummary"]
  | components["schemas"]["PlateSummary"];
type Field = components["schemas"]["FieldSummary"];
type Breadcrumb = { label: string; path: string | null };

function viewerHref(datasetId: string, imagePath: string): string {
  const query = new URLSearchParams({ dataset: datasetId, path: imagePath });
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
  const [fields, setFields] = useState<Field[]>([]);
  const [selectedWell, setSelectedWell] = useState("");
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
      if (selected?.id === dataset.id) {
        setSelected(null);
        setSummary(null);
        setFields([]);
        setSelectedWell("");
        if (linked.length > 0) await selectDataset(linked[0]);
      }
    });
  }

  async function selectDataset(dataset: Dataset) {
    const result = await dataServerClient.GET(
      "/api/datasets/{dataset_id}/summary",
      { params: { path: { dataset_id: dataset.id } } },
    );
    if (!result.data) {
      throw new Error(apiErrorMessage(result.error, "Could not load dataset summary"));
    }
    setSelected(dataset);
    setSummary(result.data);
    setFields([]);
    setSelectedWell("");
  }

  async function selectWell(wellPath: string) {
    if (!selected) return;
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
      setSelectedWell(wellPath);
      setFields(result.data.fields);
    });
  }

  const wells = useMemo(() => {
    if (!summary || summary.kind !== "plate") return new Map<string, string>();
    return new Map(
      summary.wells.map((well) => [well.row + "\u0000" + well.column, well.path]),
    );
  }, [summary]);

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
              <a className="button-link" href={viewerHref(selected.id, summary.path)}>
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
                    return path ? (
                      <button
                        type="button"
                        key={row + column}
                        className={selectedWell === path ? "well selected" : "well"}
                        disabled={busy}
                        aria-label={"Well " + row + column}
                        onClick={() => selectWell(path)}
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

              {selectedWell && (
                <div className="subpanel">
                  <h3>Fields in {selectedWell}</h3>
                  <ul className="field-list">
                    {fields.map((field, index) => (
                      <li key={field.path}>
                        <span>
                          Field {index + 1}
                          {field.acquisition != null
                            ? " · acquisition " + String(field.acquisition)
                            : ""}
                        </span>
                        <a
                          className="button-link"
                          href={viewerHref(selected.id, field.path)}
                        >
                          Open
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
