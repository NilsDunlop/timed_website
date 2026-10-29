import {
  startTransition,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { components as ConnectorComponents } from "../lib/api/connector-schema";
import type { components as DataServerComponents } from "../lib/api/dataserver-schema";
import { apiErrorMessage, connectorClient, dataServerClient } from "../lib/api/client";
import {
  initialCollection,
  readCollectionPreference,
  writeCollectionPreference,
  type DataCollection,
} from "../lib/collectionNavigation";
import {
  aggregateQcWells,
  applyQcFilters,
  buildQcModel,
  defaultQcFilterState,
  sortQcRows,
  sortQcWells,
  uniqueQcValues,
  type QcColumnDefinition,
  type QcFilterState,
  type QcModel,
  type QcScalar,
  type QcSliderState,
  type QcWellSummary,
} from "../lib/qcModel";
import {
  parseQcUrlState,
  qcUrlSearch,
  type QcTableMode,
  type QcUrlState,
} from "../lib/qcUrlState";
import QcOverview from "./QcOverview";
import QcPlateMaps, { type QcWellTarget } from "./QcPlateMaps";
import QcSliderPanel from "./QcSliderPanel";
import QcVirtualTable, { type QcTableColumn } from "./QcVirtualTable";

type Profile = ConnectorComponents["schemas"]["Profile"];
type QcStatus = DataServerComponents["schemas"]["QcStatusResponse"];
type QcFields = DataServerComponents["schemas"]["QcFieldsResponse"];
type QcRows = DataServerComponents["schemas"]["QcRowsResponse"];

type LoadedQc = {
  profileId: string;
  collection: DataCollection;
  status: QcStatus;
  fields: QcFields;
  dictionaryRows: Record<string, unknown>[];
  triageRows: Record<string, unknown>[];
  model: QcModel;
};

type CategoryDefinition = {
  column: string;
  label: string;
  values: readonly QcScalar[];
};

function humanize(value: string): string {
  return value
    .replace(/__/g, " \u00b7 ")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toLocaleUpperCase());
}

function displayScalar(value: QcScalar | readonly QcScalar[] | undefined): ReactNode {
  if (Array.isArray(value)) return value.map((item) => displayScalar(item)).join(", ");
  if (value === null || value === undefined || value === "") return <span className="muted">\u2014</span>;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return value;
}

function dictionaryLabel(
  definitions: readonly QcColumnDefinition[],
  column: string,
): string {
  const label = definitions.find((definition) => definition.column === column)?.label;
  return humanize(label || column);
}

function tableColumnLabel(
  definitions: readonly QcColumnDefinition[],
  column: string,
  displayedColumns: readonly string[],
): string {
  const definition = definitions.find((candidate) => candidate.column === column);
  const base = dictionaryLabel(definitions, column);
  const duplicates = displayedColumns.filter(
    (candidate) => dictionaryLabel(definitions, candidate) === base,
  );
  return duplicates.length > 1 && definition?.scale
    ? `${base} (${humanize(definition.scale)})`
    : base;
}

function semanticProtectionColumns(
  definitions: readonly QcColumnDefinition[],
): string[] {
  return definitions
    .filter(
      (definition) =>
        definition.layer === "context" &&
        /\bprotect(?:ed|ion)?\b/i.test(
          [definition.label, definition.metric, definition.question].join(" "),
        ),
    )
    .map((definition) => definition.column);
}

function collectionForQc(
  profiles: readonly Profile[],
  profileId: string | null,
  requestedCollection: string | null,
): { profile: Profile; collection: DataCollection } | null {
  const profile =
    profiles.find((candidate) => candidate.id === profileId) ?? profiles.at(0);
  if (!profile) return null;
  const remembered = readCollectionPreference(window.localStorage, profile.id);
  const collection = initialCollection(
    profile.collections ?? [],
    requestedCollection,
    remembered,
  );
  return collection ? { profile, collection } : null;
}

function categoryDefinitions(model: QcModel): CategoryDefinition[] {
  const required = ["cell_line", "plate", "well_type", "is_control", "gene"];
  const context = model.dictionary
    .filter((definition) => definition.file === "site_metrics.csv" && definition.layer === "context")
    .map((definition) => definition.column);
  const columns = [...new Set([...required, ...context])].filter(
    (column) => column in model.columns,
  );
  return columns.flatMap((column) => {
    const values = uniqueQcValues(model, column);
    const always = required.includes(column);
    if (!always && values.length > 50) return [];
    return [{ column, label: dictionaryLabel(model.dictionary, column), values }];
  });
}

function scalarFromUrl(values: readonly QcScalar[], requested: string): QcScalar | undefined {
  return values.find((value) => String(value) === requested);
}

function initialSliderStates(
  model: QcModel,
  parsed: QcUrlState,
): Record<string, QcSliderState> {
  const defaults = defaultQcFilterState(model).sliders ?? {};
  const result = Object.fromEntries(
    Object.entries(defaults).map(([column, state]) => [column, { ...state }]),
  );
  for (const slider of model.sliders) {
    const requested = parsed.sliders[slider.column];
    if (!requested) continue;
    const current = result[slider.column] ?? {};
    if (slider.gate === "review") {
      result[slider.column] = {
        ...current,
        flagLo: requested.lower,
        flagHi: requested.upper,
        flagActive: requested.flagActive,
      };
    } else {
      result[slider.column] = {
        ...current,
        filterLo: requested.lower,
        filterHi: requested.upper,
        flagActive: false,
      };
    }
  }
  return result;
}

function initialCategoryStates(
  categories: readonly CategoryDefinition[],
  parsed: QcUrlState,
): Record<string, QcScalar[]> {
  return Object.fromEntries(
    categories.flatMap((category) => {
      const requested = parsed.categories[category.column] ?? [];
      const values = requested
        .map((value) => scalarFromUrl(category.values, value))
        .filter((value): value is QcScalar => value !== undefined);
      return values.length > 0 ? [[category.column, values]] : [];
    }),
  );
}

function assertSameExport(
  status: QcStatus,
  payloads: readonly (QcFields | QcRows)[],
): void {
  const first = payloads[0];
  if (
    !first ||
    first.blinded ||
    payloads.some(
      (payload) =>
        payload.blinded ||
        payload.export_id !== first.export_id ||
        payload.registry_hash !== first.registry_hash,
    ) ||
    status.export_id !== first.export_id ||
    status.registry_hash !== first.registry_hash
  ) {
    throw new Error("The QC export changed while the page was loading. Reload to use one complete export.");
  }
}

export default function QcBrowser() {
  const [loaded, setLoaded] = useState<LoadedQc | null>(null);
  const [categories, setCategories] = useState<CategoryDefinition[]>([]);
  const [sliderStates, setSliderStates] = useState<Record<string, QcSliderState>>({});
  const [categoryStates, setCategoryStates] = useState<Record<string, QcScalar[]>>({});
  const [selectedPlate, setSelectedPlate] = useState<number | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [tableMode, setTableMode] = useState<QcTableMode>("sites");
  const [sortColumn, setSortColumn] = useState<string | null>("plate");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [sliderSearch, setSliderSearch] = useState("");
  const [newExport, setNewExport] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const requested = new URLSearchParams(window.location.search).get("collection");
    const [profilesResult, sessionResult] = await Promise.all([
      connectorClient.GET("/connector/profiles"),
      connectorClient.GET("/connector/session"),
    ]);
    if (!profilesResult.data) {
      throw new Error(apiErrorMessage(profilesResult.error, "Could not load connector profiles"));
    }
    if (!sessionResult.data) {
      throw new Error(apiErrorMessage(sessionResult.error, "Could not load connector session"));
    }
    const selection = collectionForQc(
      profilesResult.data,
      sessionResult.data.profile_id ?? null,
      requested,
    );
    if (!selection) throw new Error("This profile has no datasets.");
    if (!selection.collection.qc_available) {
      throw new Error(`${selection.collection.title} does not provide a QC review export.`);
    }
    writeCollectionPreference(
      window.localStorage,
      selection.profile.id,
      selection.collection.id,
    );
    const query = { path: selection.collection.path };
    const [statusResult, dictionaryResult, fieldsResult, triageResult] = await Promise.all([
      dataServerClient.GET("/api/qc/status", { params: { query } }),
      dataServerClient.GET("/api/qc/dictionary", { params: { query } }),
      dataServerClient.GET("/api/qc/fields", { params: { query } }),
      dataServerClient.GET("/api/qc/triage", { params: { query } }),
    ]);
    if (!statusResult.data) {
      throw new Error(apiErrorMessage(statusResult.error, "Could not load QC status"));
    }
    if (!statusResult.data.available || !statusResult.data.core_ready) {
      throw new Error(statusResult.data.error || "The QC export is not ready.");
    }
    if (!dictionaryResult.data || !fieldsResult.data || !triageResult.data) {
      throw new Error(
        apiErrorMessage(
          dictionaryResult.error ?? fieldsResult.error ?? triageResult.error,
          "Could not load the QC export",
        ),
      );
    }
    assertSameExport(statusResult.data, [
      dictionaryResult.data,
      fieldsResult.data,
      triageResult.data,
    ]);
    const dictionaryRows = dictionaryResult.data.rows as Record<string, unknown>[];
    const preliminary = buildQcModel(fieldsResult.data, dictionaryRows);
    const protectionColumns = semanticProtectionColumns(preliminary.dictionary);
    const model = buildQcModel(fieldsResult.data, dictionaryRows, {
      protectionColumns,
    });
    const nextCategories = categoryDefinitions(model);
    const parsed = parseQcUrlState(window.location.search, {
      sliderIds: model.sliders.map((slider) => slider.column),
      categoryIds: nextCategories.map((category) => category.column),
      sortableIds: [...Object.keys(model.columns), "flaggedSiteCount", "siteCount"],
      collectionIds: (selection.profile.collections ?? []).map((collection) => collection.id),
      channels: [
        ...new Set(
          (triageResult.data.rows as Record<string, unknown>[])
            .map((row) => String(row.channel ?? ""))
            .filter(Boolean),
        ),
      ],
      plates: uniqueQcValues(model, "plate").filter(
        (value): value is number => typeof value === "number",
      ),
    });
    setLoaded({
      profileId: selection.profile.id,
      collection: selection.collection,
      status: statusResult.data,
      fields: fieldsResult.data,
      dictionaryRows,
      triageRows: triageResult.data.rows as Record<string, unknown>[],
      model,
    });
    setCategories(nextCategories);
    setSliderStates(initialSliderStates(model, parsed));
    setCategoryStates(initialCategoryStates(nextCategories, parsed));
    setSelectedPlate(parsed.plate);
    setSelectedChannel(parsed.channel);
    setTableMode(parsed.tableMode);
    setSortColumn(parsed.sortColumn ?? "plate");
    setSortDirection(parsed.sortDirection);
    setSliderSearch(parsed.sliderSearch);
  }, []);

  useEffect(() => {
    let active = true;
    void load()
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (active) setInitialized(true);
      });
    return () => {
      active = false;
    };
  }, [load]);

  useEffect(() => {
    if (!loaded) return;
    const timer = window.setInterval(() => {
      void dataServerClient
        .GET("/api/qc/status", {
          params: { query: { path: loaded.collection.path } },
        })
        .then((result) => {
          if (
            result.data?.export_id &&
            (result.data.export_id !== loaded.status.export_id ||
              result.data.registry_hash !== loaded.status.registry_hash)
          ) {
            setNewExport(true);
          }
        });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [loaded]);

  const filterState = useMemo<QcFilterState>(() => {
    const selectedCategories: Record<string, readonly QcScalar[]> = { ...categoryStates };
    if (selectedPlate !== null) selectedCategories.plate = [selectedPlate];
    return {
      sliders: sliderStates,
      categories: selectedCategories,
      channel: selectedChannel,
    };
  }, [categoryStates, selectedChannel, selectedPlate, sliderStates]);

  const result = useMemo(
    () => (loaded ? applyQcFilters(loaded.model, filterState) : null),
    [filterState, loaded],
  );

  useEffect(() => {
    if (!loaded) return;
    const sliders: QcUrlState["sliders"] = {};
    const defaults = defaultQcFilterState(loaded.model).sliders ?? {};
    for (const slider of loaded.model.sliders) {
      const state = sliderStates[slider.column] ?? {};
      const serialized =
        slider.gate === "review"
          ? {
              lower: typeof state.flagLo === "number" ? state.flagLo : null,
              upper: typeof state.flagHi === "number" ? state.flagHi : null,
              flagActive: Boolean(state.flagActive),
            }
          : {
              lower: typeof state.filterLo === "number" ? state.filterLo : null,
              upper: typeof state.filterHi === "number" ? state.filterHi : null,
              flagActive: false,
            };
      const baseline = defaults[slider.column] ?? {};
      const serializedBaseline =
        slider.gate === "review"
          ? {
              lower: typeof baseline.flagLo === "number" ? baseline.flagLo : null,
              upper: typeof baseline.flagHi === "number" ? baseline.flagHi : null,
              flagActive: Boolean(baseline.flagActive),
            }
          : {
              lower: typeof baseline.filterLo === "number" ? baseline.filterLo : null,
              upper: typeof baseline.filterHi === "number" ? baseline.filterHi : null,
              flagActive: false,
            };
      if (JSON.stringify(serialized) !== JSON.stringify(serializedBaseline)) {
        sliders[slider.column] = serialized;
      }
    }
    const state: QcUrlState = {
      collection: loaded.collection.id,
      plate: selectedPlate,
      channel: selectedChannel,
      tableMode,
      sortColumn,
      sortDirection,
      sliderSearch,
      sliders,
      categories: Object.fromEntries(
        Object.entries(categoryStates).map(([column, values]) => [
          column,
          values.map(String),
        ]),
      ),
    };
    window.history.replaceState(window.history.state, "", `/qc${qcUrlSearch(state)}`);
  }, [
    categoryStates,
    loaded,
    selectedChannel,
    selectedPlate,
    sliderSearch,
    sliderStates,
    sortColumn,
    sortDirection,
    tableMode,
  ]);

  const contextColumns = useMemo(
    () =>
      loaded?.model.dictionary
        .filter(
          (definition) =>
            definition.file === "site_metrics.csv" && definition.layer === "context",
        )
        .map((definition) => definition.column) ?? [],
    [loaded],
  );
  const metadataColumns = useMemo(
    () => Object.keys(loaded?.fields.well_metadata ?? {}),
    [loaded],
  );
  const displayedSliders = useMemo(
    () =>
      loaded?.model.sliders.filter(
        (slider) => !selectedChannel || slider.channel === selectedChannel,
      ) ?? [],
    [loaded, selectedChannel],
  );
  const siteColumnIds = useMemo(() => {
    if (!loaded) return [];
    const requested = [
      "plate",
      "well",
      "site",
      ...metadataColumns,
      ...contextColumns,
      ...displayedSliders.flatMap((slider) =>
        slider.rawColumn && slider.rawColumn !== slider.column
          ? [slider.column, slider.rawColumn]
          : [slider.column],
      ),
    ];
    return [...new Set(requested)].filter((column) => column in loaded.model.columns);
  }, [contextColumns, displayedSliders, loaded, metadataColumns]);
  const wellSummaryColumns = useMemo(() => {
    if (!loaded) return [];
    return [
      ...new Set([
        ...metadataColumns,
        ...contextColumns,
      ]),
    ].filter((column) => column in loaded.model.columns);
  }, [contextColumns, loaded, metadataColumns]);
  const activeWellSliderColumns = useMemo(
    () =>
      displayedSliders
        .filter(
          (slider) =>
            slider.gate === "review" &&
            Boolean(sliderStates[slider.column]?.flagActive),
        )
        .map((slider) => slider.column),
    [displayedSliders, sliderStates],
  );
  const wellColumnIds = useMemo(
    () => [
      ...new Set([
        "plate",
        "well",
        "flaggedSiteCount",
        ...wellSummaryColumns,
        ...activeWellSliderColumns,
      ]),
    ],
    [activeWellSliderColumns, wellSummaryColumns],
  );

  useEffect(() => {
    if (!loaded || !sortColumn) return;
    const available = tableMode === "sites" ? siteColumnIds : wellColumnIds;
    if (available.includes(sortColumn)) return;
    setSortColumn("plate");
    setSortDirection("asc");
  }, [loaded, siteColumnIds, sortColumn, tableMode, wellColumnIds]);

  const sortedSites = useMemo(() => {
    if (!loaded || !result) return [];
    if (!sortColumn || !(sortColumn in loaded.model.columns)) return result.rowIndices;
    return sortQcRows(loaded.model, result.rowIndices, {
      column: sortColumn,
      direction: sortDirection,
    });
  }, [loaded, result, sortColumn, sortDirection]);

  const wells = useMemo(() => {
    if (!loaded || !result) return [];
    const aggregated = aggregateQcWells(
      loaded.model,
      result,
      filterState,
      wellSummaryColumns,
    );
    return sortQcWells(aggregated, {
      column: sortColumn ?? "plate",
      direction: sortDirection,
    });
  }, [filterState, loaded, result, sortColumn, sortDirection, wellSummaryColumns]);

  function updateSort(column: string) {
    if (sortColumn === column) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("asc");
    }
  }

  async function openTarget(target: QcWellTarget, scope: "field" | "well") {
    if (!loaded) return;
    setBusy(true);
    setError("");
    try {
      const root = loaded.collection.path.replace(/[\\/]+$/, "");
      const platePath = `${root}/${target.experiment}/${target.plate}`;
      const linked = await dataServerClient.POST("/api/datasets", {
        body: { path: platePath },
      });
      if (!linked.data) {
        throw new Error(apiErrorMessage(linked.error, "Could not open the QC plate"));
      }
      const query = new URLSearchParams({
        dataset: linked.data.id,
        path: `${target.well}_s${target.site}.zarr`,
        collection: loaded.collection.id,
        qc: "1",
        qcScope: scope,
        qcPlate: String(target.plate),
        qcWell: target.well,
        qcSite: String(target.site),
        qcExport: loaded.status.export_id ?? "",
        qcReturn: `${window.location.pathname}${window.location.search}`,
      });
      window.location.assign(`/view?${query.toString()}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  }

  if (!initialized) return <p className="muted">Loading QC review\u2026</p>;
  if (!loaded || !result) {
    return (
      <section className="panel stack">
        <h1>QC review</h1>
        <div className="notice error" role="alert">
          <strong>{error || "The QC review could not be loaded."}</strong>
          <p>Connect to the matching compute profile, then choose Cell Health on Connect.</p>
        </div>
      </section>
    );
  }

  const siteColumns: QcTableColumn<number>[] = siteColumnIds.map((column) => ({
    id: column,
    label: tableColumnLabel(loaded.model.dictionary, column, siteColumnIds),
    render: (rowIndex) => displayScalar(loaded.model.columns[column]?.[rowIndex]),
  }));
  const wellColumns: QcTableColumn<QcWellSummary>[] = wellColumnIds.map(
    (column) => ({
      id: column,
      label:
        column === "flaggedSiteCount"
          ? "Flagged sites"
          : tableColumnLabel(loaded.model.dictionary, column, wellColumnIds),
      render: (well) =>
        displayScalar(
          column === "flaggedSiteCount"
            ? well.flaggedSiteCount
            : well.worstValues[column] ?? well.values[column] ?? well.keyValues[column],
        ),
    }),
  );

  return (
    <div className="qc-browser stack">
      <header className="qc-page-heading">
        <div>
          <p className="eyebrow">Quality control</p>
          <h1>{loaded.collection.title}</h1>
          <p className="lede">Review the complete configured export with dictionary-defined metrics.</p>
        </div>
        <dl className="qc-export-identity">
          <div>
            <dt>Export</dt>
            <dd>{loaded.status.export_id}</dd>
          </div>
          <div>
            <dt>Registry</dt>
            <dd>{loaded.status.registry_hash?.slice(0, 12)}</dd>
          </div>
        </dl>
      </header>

      {newExport && (
        <div className="notice success qc-reload-notice" role="status">
          <strong>New export available</strong>
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      )}
      {loaded.status.unknown_statuses && loaded.status.unknown_statuses.length > 0 && (
        <div className="notice" role="status">
          {loaded.status.unknown_statuses.length} unrecognised dictionary status
          {loaded.status.unknown_statuses.length === 1 ? " is" : "es are"} hidden.
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      <QcOverview
        rows={loaded.triageRows}
        dictionary={loaded.model.dictionary}
        selectedPlate={selectedPlate}
        selectedChannel={selectedChannel}
        onSelect={(plate, channel) => {
          startTransition(() => {
            setSelectedPlate(plate);
            setSelectedChannel(channel);
            if (plate !== null) {
              setCategoryStates((current) => {
                if (!current.plate?.length) return current;
                const next = { ...current };
                delete next.plate;
                return next;
              });
            }
          });
        }}
      />

      <div className="qc-workspace">
        <QcSliderPanel
          model={loaded.model}
          result={result}
          state={sliderStates}
          selectedChannel={selectedChannel}
          search={sliderSearch}
          onSearch={setSliderSearch}
          onChange={(column, state) =>
            startTransition(() =>
              setSliderStates((current) => ({ ...current, [column]: state })),
            )
          }
        />

        <div className="qc-results stack">
          <section className="panel stack" aria-labelledby="qc-filters-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Current view</p>
                <h2 id="qc-filters-heading">Filters and counts</h2>
              </div>
              {(selectedPlate !== null ||
                selectedChannel !== null ||
                Object.values(categoryStates).some((values) => values.length > 0)) && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setSelectedPlate(null);
                    setSelectedChannel(null);
                    setCategoryStates({});
                  }}
                >
                  Clear categories
                </button>
              )}
            </div>
            <div className="qc-category-filters">
              {categories.map((category) => (
                <label key={category.column}>
                  <span>{category.label}</span>
                  <select
                    multiple
                    aria-label={category.label}
                    value={
                      category.column === "plate" && selectedPlate !== null
                        ? [String(selectedPlate)]
                        : (categoryStates[category.column] ?? []).map(String)
                    }
                    onChange={(event) => {
                      const requested = [...event.target.selectedOptions].map(
                        (option) => option.value,
                      );
                      const values = requested
                        .map((value) => scalarFromUrl(category.values, value))
                        .filter((value): value is QcScalar => value !== undefined);
                      startTransition(() => {
                        if (category.column === "plate") {
                          setSelectedPlate(null);
                          setCategoryStates((current) => ({
                            ...current,
                            [category.column]: values,
                          }));
                        } else {
                          setCategoryStates((current) => ({
                            ...current,
                            [category.column]: values,
                          }));
                        }
                      });
                    }}
                  >
                    {category.values.map((value) => (
                      <option key={`${typeof value}:${String(value)}`} value={String(value)}>
                        {displayScalar(value)}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            <dl className="qc-counts" data-testid="qc-counts">
              <div>
                <dt>Fields</dt>
                <dd>{result.counts.fields.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Wells</dt>
                <dd>{result.counts.wells.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Control fields</dt>
                <dd>{result.counts.controls.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Fields under protection</dt>
                <dd>{result.counts.fieldsUnderProtection.toLocaleString()}</dd>
              </div>
            </dl>
          </section>

          <QcPlateMaps
            model={loaded.model}
            result={result}
            selectedPlate={selectedPlate}
            onOpen={(target) => void openTarget(target, "well")}
          />

          <section className="panel stack" aria-labelledby="qc-table-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Review set</p>
                <h2 id="qc-table-heading">{tableMode === "sites" ? "Sites" : "Wells"}</h2>
              </div>
              <div className="qc-table-mode" role="group" aria-label="Table rows">
                <button
                  type="button"
                  className={tableMode === "wells" ? "" : "secondary"}
                  aria-pressed={tableMode === "wells"}
                  onClick={() => setTableMode("wells")}
                >
                  Wells
                </button>
                <button
                  type="button"
                  className={tableMode === "sites" ? "" : "secondary"}
                  aria-pressed={tableMode === "sites"}
                  onClick={() => setTableMode("sites")}
                >
                  Sites
                </button>
              </div>
            </div>
            {tableMode === "sites" ? (
              <QcVirtualTable
                ariaLabel="QC sites"
                rows={sortedSites}
                columns={siteColumns}
                rowKey={(rowIndex) => String(rowIndex)}
                sortColumn={sortColumn}
                sortDirection={sortDirection}
                onSort={updateSort}
                activateLabel={() => "Open site"}
                onActivate={(rowIndex) =>
                  void openTarget(
                    {
                      experiment: String(loaded.model.columns.experiment?.[rowIndex] ?? ""),
                      plate: Number(loaded.model.columns.plate?.[rowIndex]),
                      well: String(loaded.model.columns.well?.[rowIndex] ?? ""),
                      site: Number(loaded.model.columns.site?.[rowIndex]),
                    },
                    "field",
                  )
                }
              />
            ) : (
              <QcVirtualTable
                ariaLabel="QC wells"
                rows={wells}
                columns={wellColumns}
                rowKey={(well) => well.key}
                sortColumn={sortColumn}
                sortDirection={sortDirection}
                onSort={updateSort}
                activateLabel={() => "Open well"}
                onActivate={(well) => {
                  const first = well.rowIndices[0];
                  void openTarget(
                    {
                      experiment: String(loaded.model.columns.experiment?.[first] ?? ""),
                      plate: Number(well.keyValues.plate),
                      well: String(well.keyValues.well ?? ""),
                      site: 1,
                    },
                    "well",
                  );
                }}
              />
            )}
            {busy && <p className="muted">Opening viewer\u2026</p>}
          </section>
        </div>
      </div>
    </div>
  );
}
