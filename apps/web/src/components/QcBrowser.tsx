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
import {
  apiErrorMessage,
  connectorClient,
  dataServerClient,
  webClientHeaders,
} from "../lib/api/client";
import {
  initialCollection,
  readCollectionPreference,
  writeCollectionPreference,
  type DataCollection,
} from "../lib/collectionNavigation";
import {
  parseQcAnnotationLabels,
  parseQcAnnotations,
  qcAnnotationRowState,
  type QcAnnotationLabelDefinition,
  type QcAnnotationRowState,
  type QcCurrentAnnotation,
} from "../lib/qcAnnotations";
import {
  saveQcQueue,
  type QcQueueEntry,
  type QcReviewQueue,
} from "../lib/qcQueue";
import {
  aggregateQcWells,
  applyQcFilters,
  buildQcModel,
  defaultQcFilterState,
  qcFlaggedSlidersForRow,
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
import { qcDisplayLabel } from "../lib/qcViewer";
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
  reviewRows: Record<string, unknown>[];
  annotations: QcCurrentAnnotation[];
  annotationLabels: QcAnnotationLabelDefinition[];
  channels: string[];
  fieldAnnotationCategories: readonly (readonly QcScalar[])[];
  wellAnnotationCategories: readonly (readonly QcScalar[])[];
  model: QcModel;
};

type CategoryDefinition = {
  column: string;
  label: string;
  values: readonly QcScalar[];
  advanced: boolean;
};

function displayScalar(value: QcScalar | readonly QcScalar[] | undefined): ReactNode {
  if (Array.isArray(value)) return value.map((item) => displayScalar(item)).join(", ");
  if (value === null || value === undefined || value === "") return <span className="muted">\u2014</span>;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return value;
}

function flaggedReason(
  model: QcModel,
  state: QcFilterState,
  rowIndex: number,
): string {
  return qcFlaggedSlidersForRow(model, state, rowIndex)
    .map((slider) =>
      slider.channel
        ? `${qcDisplayLabel(slider.label)} · ${slider.channel}`
        : qcDisplayLabel(slider.label),
    )
    .join(", ");
}

function dictionaryLabel(
  definitions: readonly QcColumnDefinition[],
  column: string,
): string {
  const label = definitions.find((definition) => definition.column === column)?.label;
  return qcDisplayLabel(label || column);
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
    ? `${base} (${qcDisplayLabel(definition.scale)})`
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
  const required = [
    "cell_line",
    "plate",
    "is_control",
    "gene",
    "annotation_status",
  ];
  const columns = required.filter(
    (column) => column in model.columns || column in model.categoryColumns,
  );
  return columns.flatMap((column) => {
    const values = uniqueQcValues(model, column);
    const primary = column === "cell_line" || column === "plate";
    return [{
      column,
      label: column === "annotation_status"
        ? "Annotation status"
        : column === "is_control"
          ? "Control status"
        : dictionaryLabel(model.dictionary, column),
      values,
      advanced: !primary,
    }];
  });
}

function annotationDisplay(state: QcAnnotationRowState): string {
  return state.reviewed
    ? state.labels.map(qcDisplayLabel).join(", ") || "Reviewed"
    : "Not reviewed";
}

function annotationCategories(state: QcAnnotationRowState): readonly QcScalar[] {
  return [state.status, ...state.labels];
}

const EMPTY_ANNOTATION_STATE: QcAnnotationRowState = {
  reviewed: false,
  status: "not reviewed",
  labels: [],
  count: 0,
};

function annotationKey(
  scope: "field" | "well",
  plate: number,
  barcode: string,
  well: string,
  site: number | null,
): string {
  return `${scope}:${plate}:${barcode}:${well}:${site ?? ""}`;
}

function annotationStateIndex(
  annotations: readonly QcCurrentAnnotation[],
  labelDefinitions: readonly QcAnnotationLabelDefinition[],
): Map<string, QcAnnotationRowState> {
  const grouped = new Map<string, QcCurrentAnnotation[]>();
  for (const annotation of annotations) {
    const key = annotationKey(
      annotation.scope,
      annotation.plate,
      annotation.barcode,
      annotation.well,
      annotation.site,
    );
    const records = grouped.get(key);
    if (records) records.push(annotation);
    else grouped.set(key, [annotation]);
  }
  return new Map(
    [...grouped.entries()].map(([key, records]) => {
      const first = records[0] as QcCurrentAnnotation;
      return [
        key,
        qcAnnotationRowState(
          records,
          {
            scope: first.scope,
            plate: first.plate,
            barcode: first.barcode,
            well: first.well,
            site: first.site,
          },
          labelDefinitions,
        ),
      ];
    }),
  );
}

function reviewQueueEntry(
  row: Record<string, unknown>,
  model: QcModel,
  channels: readonly string[],
): QcQueueEntry | null {
  const scope = row.scope === "well" ? "well" : row.scope === "field" ? "field" : null;
  const plate = Number(row.plate);
  const well = String(row.well ?? "").toUpperCase();
  const site = scope === "well" ? 1 : Number(row.site);
  if (
    !scope ||
    !Number.isInteger(plate) ||
    plate <= 0 ||
    !/^[A-Z]+\d+$/.test(well) ||
    !Number.isInteger(site) ||
    site <= 0
  ) {
    return null;
  }
  let matchingRow = -1;
  for (let index = 0; index < model.rowCount; index += 1) {
    if (
      Number(model.columns.plate?.[index]) === plate &&
      String(model.columns.well?.[index] ?? "").toUpperCase() === well &&
      Number(model.columns.site?.[index]) === site
    ) {
      matchingRow = index;
      break;
    }
  }
  if (matchingRow < 0) return null;
  const plateGroup = String(model.columns.experiment?.[matchingRow] ?? "");
  const barcode = String(model.columns.plate_barcode?.[matchingRow] ?? "");
  const requestedChannel = String(row.channel ?? "");
  const requestedMetric = String(row.metric ?? "");
  const reasonDefinition = model.dictionary.find(
    (definition) =>
      definition.file === "site_metrics.csv" &&
      definition.metric === requestedMetric &&
      (definition.channel === requestedChannel || requestedChannel === "site") &&
      definition.primary,
  ) ?? model.dictionary.find(
    (definition) =>
      definition.file === "site_metrics.csv" && definition.metric === requestedMetric,
  );
  const reasonLabel = qcDisplayLabel(
    reasonDefinition?.label || requestedMetric || "Review queue",
  );
  return {
    scope,
    plateGroup,
    plate,
    barcode,
    well,
    site,
    channel: channels.includes(requestedChannel) ? requestedChannel : "all",
    reason:
      requestedChannel && requestedChannel !== "site"
        ? `${reasonLabel} · ${requestedChannel}`
        : reasonLabel,
  };
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
  const [showTableDetails, setShowTableDetails] = useState(false);
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
    const [
      statusResult,
      dictionaryResult,
      fieldsResult,
      triageResult,
      reviewResult,
    ] = await Promise.all([
      dataServerClient.GET("/api/qc/status", { params: { query } }),
      dataServerClient.GET("/api/qc/dictionary", { params: { query } }),
      dataServerClient.GET("/api/qc/fields", { params: { query } }),
      dataServerClient.GET("/api/qc/triage", { params: { query } }),
      dataServerClient.GET("/api/qc/review-list", { params: { query } }),
    ]);
    if (!statusResult.data) {
      throw new Error(apiErrorMessage(statusResult.error, "Could not load QC status"));
    }
    if (!statusResult.data.available || !statusResult.data.core_ready) {
      throw new Error(statusResult.data.error || "The QC export is not ready.");
    }
    if (
      !dictionaryResult.data ||
      !fieldsResult.data ||
      !triageResult.data ||
      !reviewResult.data
    ) {
      throw new Error(
        apiErrorMessage(
          dictionaryResult.error ??
            fieldsResult.error ??
            triageResult.error ??
            reviewResult.error,
          "Could not load the QC export",
        ),
      );
    }
    assertSameExport(statusResult.data, [
      dictionaryResult.data,
      fieldsResult.data,
      triageResult.data,
      reviewResult.data,
    ]);
    const triageRows = triageResult.data.rows as Record<string, unknown>[];
    const channels = [
      ...new Set(
        triageRows
          .map((row) => String(row.channel ?? ""))
          .filter((channel) => channel && channel.toLocaleLowerCase() !== "site"),
      ),
    ];
    const parsedAnnotations = parseQcAnnotations(
      fieldsResult.data.annotations,
      channels,
    );
    if (parsedAnnotations.invalidRecords > 0) {
      throw new Error("The QC annotation response is invalid.");
    }
    const parsedLabels = parseQcAnnotationLabels(fieldsResult.data.labels);
    if (parsedLabels.invalidRecords > 0) {
      throw new Error("The QC annotation label registry is invalid.");
    }
    const annotationStates = annotationStateIndex(
      parsedAnnotations.annotations,
      parsedLabels.labels,
    );
    const fieldAnnotationCategories: (readonly QcScalar[])[] = [];
    const wellAnnotationCategories: (readonly QcScalar[])[] = [];
    const annotationDisplayValues: string[] = [];
    const plates = fieldsResult.data.columns.plate ?? [];
    const barcodes = fieldsResult.data.columns.plate_barcode ?? [];
    const wells = fieldsResult.data.columns.well ?? [];
    const sites = fieldsResult.data.columns.site ?? [];
    for (let rowIndex = 0; rowIndex < fieldsResult.data.row_count; rowIndex += 1) {
      const plate = Number(plates[rowIndex]);
      const barcode = String(barcodes[rowIndex] ?? "");
      const well = String(wells[rowIndex] ?? "").toUpperCase();
      const site = Number(sites[rowIndex]);
      const fieldState =
        annotationStates.get(annotationKey("field", plate, barcode, well, site)) ??
        EMPTY_ANNOTATION_STATE;
      const wellState =
        annotationStates.get(annotationKey("well", plate, barcode, well, null)) ??
        EMPTY_ANNOTATION_STATE;
      fieldAnnotationCategories.push(annotationCategories(fieldState));
      wellAnnotationCategories.push(annotationCategories(wellState));
      annotationDisplayValues.push(annotationDisplay(fieldState));
    }
    const fieldsWithAnnotations: QcFields = {
      ...fieldsResult.data,
      well_metadata: {
        ...(fieldsResult.data.well_metadata ?? {}),
        annotation_status: annotationDisplayValues,
      },
    };
    const dictionaryRows = dictionaryResult.data.rows as Record<string, unknown>[];
    const combinedAnnotationCategories = fieldAnnotationCategories.map(
      (values, index) => [
        ...new Set([...values, ...(wellAnnotationCategories[index] ?? [])]),
      ],
    );
    const preliminary = buildQcModel(fieldsWithAnnotations, dictionaryRows, {
      categoryColumns: { annotation_status: combinedAnnotationCategories },
    });
    const protectionColumns = semanticProtectionColumns(preliminary.dictionary);
    const model = buildQcModel(fieldsWithAnnotations, dictionaryRows, {
      protectionColumns,
      categoryColumns: { annotation_status: combinedAnnotationCategories },
    });
    const nextCategories = categoryDefinitions(model);
    const parsed = parseQcUrlState(window.location.search, {
      sliderIds: model.sliders.map((slider) => slider.column),
      categoryIds: nextCategories.map((category) => category.column),
      sortableIds: [...Object.keys(model.columns), "flaggedSiteCount", "siteCount"],
      collectionIds: (selection.profile.collections ?? []).map((collection) => collection.id),
      channels,
      plates: uniqueQcValues(model, "plate").filter(
        (value): value is number => typeof value === "number",
      ),
    });
    setLoaded({
      profileId: selection.profile.id,
      collection: selection.collection,
      status: statusResult.data,
      fields: fieldsWithAnnotations,
      dictionaryRows,
      triageRows,
      reviewRows: reviewResult.data.rows as Record<string, unknown>[],
      annotations: parsedAnnotations.annotations,
      annotationLabels: parsedLabels.labels,
      channels,
      fieldAnnotationCategories,
      wellAnnotationCategories,
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
    setShowTableDetails(parsed.showDetails);
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

  const filteredModel = useMemo<QcModel | null>(() => {
    if (!loaded) return null;
    return {
      ...loaded.model,
      categoryColumns: {
        ...loaded.model.categoryColumns,
        annotation_status:
          tableMode === "sites"
            ? loaded.fieldAnnotationCategories
            : loaded.wellAnnotationCategories,
      },
    };
  }, [loaded, tableMode]);

  const result = useMemo(
    () => (filteredModel ? applyQcFilters(filteredModel, filterState) : null),
    [filterState, filteredModel],
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
      showDetails: showTableDetails,
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
    showTableDetails,
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
  const currentAnnotationStates = useMemo(
    () =>
      annotationStateIndex(
        loaded?.annotations ?? [],
        loaded?.annotationLabels ?? [],
      ),
    [loaded],
  );
  const displayedSliders = useMemo(
    () =>
      loaded?.model.sliders.filter(
        (slider) => !selectedChannel || slider.channel === selectedChannel,
      ) ?? [],
    [loaded, selectedChannel],
  );
  useEffect(() => {
    if (displayedSliders.length === 0) return;
    if (displayedSliders.some((slider) => slider.column === sliderSearch)) return;
    setSliderSearch(
      displayedSliders.find((slider) => slider.gate === "review")?.column ??
        displayedSliders[0]?.column ??
        "",
    );
  }, [displayedSliders, sliderSearch]);
  const essentialMetadataColumns = useMemo(
    () =>
      ["gene", "is_control", "annotation_status"].filter(
        (column) => column in (loaded?.model.columns ?? {}),
      ),
    [loaded],
  );
  const siteColumnIds = useMemo(() => {
    if (!loaded) return [];
    const requested = [
      "plate",
      "well",
      "site",
      ...essentialMetadataColumns,
      ...(showTableDetails
        ? [
            ...metadataColumns,
            ...contextColumns,
            ...displayedSliders.flatMap((slider) =>
              slider.rawColumn && slider.rawColumn !== slider.column
                ? [slider.column, slider.rawColumn]
                : [slider.column],
            ),
          ]
        : []),
    ];
    return [...new Set(requested)].filter((column) => column in loaded.model.columns);
  }, [
    contextColumns,
    displayedSliders,
    essentialMetadataColumns,
    loaded,
    metadataColumns,
    showTableDetails,
  ]);
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
        ...essentialMetadataColumns,
        ...(showTableDetails
          ? [...wellSummaryColumns, ...activeWellSliderColumns]
          : []),
      ]),
    ],
    [
      activeWellSliderColumns,
      essentialMetadataColumns,
      showTableDetails,
      wellSummaryColumns,
    ],
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
    if (!loaded || !result || !filteredModel) return [];
    const aggregated = aggregateQcWells(
      filteredModel,
      result,
      filterState,
      wellSummaryColumns,
    );
    const withAnnotationStatus = aggregated.map((well) => {
      const plate = Number(well.keyValues.plate);
      const wellName = String(well.keyValues.well ?? "");
      const first = well.rowIndices[0] ?? -1;
      const barcode = String(loaded.model.columns.plate_barcode?.[first] ?? "");
      const state =
        currentAnnotationStates.get(
          annotationKey("well", plate, barcode, wellName, null),
        ) ??
        EMPTY_ANNOTATION_STATE;
      return {
        ...well,
        values: {
          ...well.values,
          annotation_status: annotationDisplay(state),
        },
      };
    });
    return sortQcWells(withAnnotationStatus, {
      column: sortColumn ?? "plate",
      direction: sortDirection,
    });
  }, [
    filterState,
    filteredModel,
    currentAnnotationStates,
    loaded,
    result,
    sortColumn,
    sortDirection,
    wellSummaryColumns,
  ]);

  const fileQueueEntries = useMemo(
    () =>
      loaded?.reviewRows.map((row) =>
        reviewQueueEntry(row, loaded.model, loaded.channels),
      ) ?? [],
    [loaded],
  );
  const validFileQueueEntries = useMemo(
    () => fileQueueEntries.filter((entry): entry is QcQueueEntry => entry !== null),
    [fileQueueEntries],
  );
  const sessionQueueEntries = useMemo<QcQueueEntry[]>(() => {
    if (!loaded || !result) return [];
    if (tableMode === "sites") {
      return sortedSites.map((rowIndex) => ({
        scope: "field",
        plateGroup: String(loaded.model.columns.experiment?.[rowIndex] ?? ""),
        plate: Number(loaded.model.columns.plate?.[rowIndex]),
        barcode: String(loaded.model.columns.plate_barcode?.[rowIndex] ?? ""),
        well: String(loaded.model.columns.well?.[rowIndex] ?? "").toUpperCase(),
        site: Number(loaded.model.columns.site?.[rowIndex]),
        channel: "all",
        reason: flaggedReason(filteredModel ?? loaded.model, filterState, rowIndex) || "Current QC filters",
      }));
    }
    const flaggedRows = new Set(result.flaggedRowIndices);
    return wells.map((well) => {
      const first = well.rowIndices[0] ?? -1;
      return {
        scope: "well",
        plateGroup: String(loaded.model.columns.experiment?.[first] ?? ""),
        plate: Number(well.keyValues.plate),
        barcode: String(loaded.model.columns.plate_barcode?.[first] ?? ""),
        well: String(well.keyValues.well ?? "").toUpperCase(),
        site: 1,
        channel: "all",
        reason:
          [...new Set(
            well.rowIndices
              .map((rowIndex) =>
                flaggedReason(filteredModel ?? loaded.model, filterState, rowIndex),
              )
              .filter(Boolean),
          )].join(", ") || "Current QC filters",
        flaggedSites: well.rowIndices
          .filter((rowIndex) => flaggedRows.has(rowIndex))
          .map((rowIndex) => Number(loaded.model.columns.site?.[rowIndex]))
          .filter((site) => Number.isInteger(site) && site > 0)
          .sort((left, right) => left - right),
      };
    });
  }, [filterState, filteredModel, loaded, result, sortedSites, tableMode, wells]);

  function updateSort(column: string) {
    if (sortColumn === column) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("asc");
    }
  }

  async function openTarget(
    target: QcWellTarget,
    scope: "field" | "well",
    queue?: QcReviewQueue,
    queueIndex = 0,
  ) {
    if (!loaded) return;
    setBusy(true);
    setError("");
    try {
      const root = loaded.collection.path.replace(/[\\/]+$/, "");
      const platePath = `${root}/${target.experiment}/${target.plate}`;
      const linked = await dataServerClient.POST("/api/datasets", {
        headers: webClientHeaders,
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
      if (queue) {
        query.set("qcQueue", queue.id);
        query.set("qcIndex", String(queueIndex));
      }
      if (target.flaggedSites?.length) {
        query.set("qcFlaggedSites", target.flaggedSites.join(","));
      }
      window.location.assign(`/view?${query.toString()}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  }

  async function launchQueue(
    entries: readonly QcQueueEntry[],
    source: "review-list" | "session",
  ) {
    if (!loaded || !loaded.status.export_id || !loaded.status.registry_hash) return;
    if (
      entries.length === 0 ||
      (source === "review-list" && entries.length !== loaded.reviewRows.length)
    ) {
      setError("The review queue contains an invalid image target.");
      return;
    }
    const saved = saveQcQueue(
      window.sessionStorage,
      {
        source,
        exportId: loaded.status.export_id,
        registryHash: loaded.status.registry_hash,
        collectionId: loaded.collection.id,
        returnHref: `${window.location.pathname}${window.location.search}`,
        entries: [...entries],
      },
      loaded.channels,
    );
    if (!saved.ok) {
      setError("The review queue could not be stored in this browser session.");
      return;
    }
    const first = saved.queue.entries[0];
    if (!first) return;
    await openTarget(
      {
        experiment: first.plateGroup,
        plate: first.plate,
        well: first.well,
        site: first.site,
        flaggedSites: first.flaggedSites,
      },
      first.scope,
      saved.queue,
      0,
    );
  }

  function categoryControl(category: CategoryDefinition): ReactNode {
    return (
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
              if (category.column === "plate") setSelectedPlate(null);
              setCategoryStates((current) => ({
                ...current,
                [category.column]: values,
              }));
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
    );
  }

  if (!initialized) return <p className="muted">Loading QC review…</p>;
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

  const siteValueColumns: QcTableColumn<number>[] = siteColumnIds.map((column) => ({
    id: column,
    label: tableColumnLabel(loaded.model.dictionary, column, siteColumnIds),
    render: (rowIndex) => displayScalar(loaded.model.columns[column]?.[rowIndex]),
  }));
  const siteReasonColumn: QcTableColumn<number> = {
    id: "flaggedBy",
    label: "Flagged by",
    sortable: false,
    render: (rowIndex) => {
      const reason = flaggedReason(filteredModel ?? loaded.model, filterState, rowIndex);
      return reason || <span className="muted">Current filters</span>;
    },
  };
  const siteReasonIndex = Math.max(
    0,
    siteValueColumns.findIndex((column) => column.id === "site") + 1,
  );
  const siteColumns: QcTableColumn<number>[] = [
    ...siteValueColumns.slice(0, siteReasonIndex),
    siteReasonColumn,
    ...siteValueColumns.slice(siteReasonIndex),
  ];
  const wellValueColumns: QcTableColumn<QcWellSummary>[] = wellColumnIds.map(
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
  const wellReasonColumn: QcTableColumn<QcWellSummary> = {
    id: "flaggedBy",
    label: "Flagged by",
    sortable: false,
    render: (well) =>
      [...new Set(
        well.rowIndices
          .map((rowIndex) =>
            flaggedReason(filteredModel ?? loaded.model, filterState, rowIndex),
          )
          .filter(Boolean),
      )].join(", ") || <span className="muted">Current filters</span>,
  };
  const wellReasonIndex = Math.max(
    0,
    wellValueColumns.findIndex((column) => column.id === "flaggedSiteCount") + 1,
  );
  const wellColumns: QcTableColumn<QcWellSummary>[] = [
    ...wellValueColumns.slice(0, wellReasonIndex),
    wellReasonColumn,
    ...wellValueColumns.slice(wellReasonIndex),
  ];

  return (
    <div className="qc-browser stack">
      <header className="qc-page-heading">
        <div>
          <p className="eyebrow">Quality control</p>
          <h1>{loaded.collection.title}</h1>
          <p className="lede">Review flagged images and record decisions.</p>
        </div>
        <div className="qc-page-actions">
          <button
            type="button"
            disabled={busy || validFileQueueEntries.length === 0}
            onClick={() => void launchQueue(validFileQueueEntries, "review-list")}
          >
            Review queue
          </button>
          <details className="qc-export-details">
            <summary>Export details</summary>
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
          </details>
        </div>
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
      {loaded.status.malformed_annotation_lines > 0 && (
        <div className="notice" role="status">
          {loaded.status.malformed_annotation_lines.toLocaleString()} malformed annotation
          {loaded.status.malformed_annotation_lines === 1 ? " line was" : " lines were"} skipped.
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
          selectedMetric={sliderSearch}
          onSelectMetric={setSliderSearch}
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
              {categories.filter((category) => !category.advanced).map(categoryControl)}
            </div>
            {categories.some((category) => category.advanced) && (
              <details className="qc-filter-details">
                <summary>More filters</summary>
                <div className="qc-category-filters">
                  {categories.filter((category) => category.advanced).map(categoryControl)}
                </div>
              </details>
            )}
            <dl className="qc-counts" data-testid="qc-counts">
              <div>
                <dt>Sites</dt>
                <dd>{result.counts.fields.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Wells</dt>
                <dd>{result.counts.wells.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Control sites</dt>
                <dd>{result.counts.controls.toLocaleString()}</dd>
              </div>
            </dl>
          </section>

          <QcPlateMaps
            model={filteredModel ?? loaded.model}
            result={result}
            filterState={filterState}
            selectedPlate={selectedPlate}
            onOpen={(target) => void openTarget(target, "well")}
          />

          <section className="panel stack" aria-labelledby="qc-table-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Review set</p>
                <h2 id="qc-table-heading">{tableMode === "sites" ? "Sites" : "Wells"}</h2>
              </div>
              <div className="qc-table-actions">
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
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || sessionQueueEntries.length === 0}
                  onClick={() => void launchQueue(sessionQueueEntries, "session")}
                >
                  Review these
                </button>
              </div>
            </div>
            <details className="qc-table-details">
              <summary>Additional table columns</summary>
              <label>
                <input
                  type="checkbox"
                  checked={showTableDetails}
                  onChange={(event) => setShowTableDetails(event.target.checked)}
                />
                <span>Show context, attributes, and metric values</span>
              </label>
            </details>
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
            {busy && <p className="muted">Opening viewer…</p>}
          </section>
        </div>
      </div>
    </div>
  );
}
