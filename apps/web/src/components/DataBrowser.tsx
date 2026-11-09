import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";

import { accessIssues, accessMessage, collectionIssue } from "../lib/accessIssues";
import type { components } from "../lib/api/dataserver-schema";
import {
  apiErrorMessage,
  connectorClient,
  dataServerClient,
  webClientHeaders,
} from "../lib/api/client";
import {
  carrySelectionAcrossDatasets,
  clearCollectionPlace,
  collectionCarriesWellBetweenPlates,
  collectionEntryName,
  dataSelectionHref,
  initialCollection,
  pathIsInCollection,
  readCollectionPlace,
  readCollectionPreference,
  writeCollectionPlace,
  writeCollectionPreference,
  type DataCollection,
} from "../lib/collectionNavigation";
import {
  channelLabelsMatch,
  imageAxisSize,
  imageChannelLabels,
} from "../lib/imageSummary";
import {
  doseLabel,
  findWellMetadata,
  metadataLabels,
  type PlateMetadata,
} from "../lib/plateMetadata";
import { viewerQueryHref, writeViewerLink } from "../lib/viewerLink";
import PlateMetadataPanel from "./PlateMetadataPanel";
import PlateGrid, {
  plateGridKey,
  type PlateGridWell,
} from "./PlateGrid";

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
  collectionId: string | null;
  path: string;
  fields: Field[];
  fieldPath: string;
  channelLabels: string[];
  timepointCount: number;
};
type ViewerSource = {
  datasetId: string;
  imagePath: string;
};
type FocusedGridWell = { datasetId: string; path: string };
type WellHighlightMode = "gene" | "control";

const MAX_COMPARISON_SOURCES = 4;

const metadataColors = [
  "#3b82a0",
  "#b86b35",
  "#668f3d",
  "#9367a8",
  "#c24f65",
  "#9a812f",
];

function imageFromSummary(summary: Summary | null) {
  if (!summary) return null;
  return summary.kind === "image" ? summary : (summary.first_image ?? null);
}

function pluralizeFilterLabel(label: string): string {
  const lower = label.toLocaleLowerCase();
  if (lower.endsWith("y")) return lower.slice(0, -1) + "ies";
  if (lower.endsWith("s")) return lower;
  return lower + "s";
}

function viewerHref(
  primary: ViewerSource,
  comparisons: ViewerSource[] = [],
  collectionId: string | null = null,
  returnDatasetId: string | null = null,
): string {
  return viewerQueryHref(
    writeViewerLink(new URLSearchParams(), {
      primary: { datasetId: primary.datasetId, path: primary.imagePath },
      comparisons: comparisons.map((comparison) => ({
        datasetId: comparison.datasetId,
        path: comparison.imagePath,
      })),
      collectionId,
      returnDatasetId,
    }),
  );
}

function ComparisonTray({
  wells,
  busy,
  returnDatasetId,
  onClear,
  onSelectField,
  onRemoveWell,
}: {
  wells: WellSelection[];
  busy: boolean;
  returnDatasetId: string | null;
  onClear: () => void;
  onSelectField: (
    datasetId: string,
    wellPath: string,
    fieldPath: string,
  ) => void;
  onRemoveWell: (datasetId: string, wellPath: string) => void;
}) {
  if (wells.length === 0) return null;
  const singleTimepoint = wells.every((well) => well.timepointCount <= 1);
  const channelMismatch = wells
    .slice(1)
    .some((well) =>
      !channelLabelsMatch(wells[0].channelLabels, well.channelLabels),
    );
  return (
    <div className="subpanel well-selection-panel" data-testid="well-selection">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Comparison tray</p>
          <h3>
            {wells.length === 1
              ? "Choose a site for side A"
              : "Choose a site for each well"}
          </h3>
        </div>
        <button
          type="button"
          className="text-button"
          disabled={busy}
          onClick={onClear}
        >
          Clear tray
        </button>
      </div>

      <div
        className={
          "comparison-field-grid" +
          (wells.length === 1 ? " comparison-field-grid-single" : "")
        }
        data-count={wells.length}
      >
        {wells.map((well, index) => (
          <div
            className="comparison-source-card"
            key={well.datasetId + "\u0000" + well.path}
          >
            <div className="comparison-source-heading">
              <span className="comparison-index" aria-hidden="true">
                {"ABCD"[index]}
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
                disabled={busy}
                onClick={() => onRemoveWell(well.datasetId, well.path)}
              >
                Remove
              </button>
            </div>
            <label>
              <span>Site</span>
              <select
                aria-label={"Site for " + well.datasetName + " " + well.path}
                value={well.fieldPath}
                disabled={busy}
                onChange={(event) =>
                  onSelectField(well.datasetId, well.path, event.target.value)
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
        <p
          className="muted"
          data-testid={channelMismatch ? "comparison-guard" : undefined}
        >
          {channelMismatch
            ? "Comparison is unavailable because the selected fields have different channel labels."
            : wells.length === 1
              ? "Inspect side A now, or open another well to add side B."
              : singleTimepoint
                ? "Channels, pan, and zoom will stay synchronized."
                : "Time, channels, pan, and zoom will stay synchronized."}
        </p>
        {wells.length === 1 && wells[0].fieldPath && (
          <a
            className="button-link"
            data-testid="open-selected"
            href={viewerHref(
              {
                datasetId: wells[0].datasetId,
                imagePath: wells[0].fieldPath,
              },
              [],
              wells[0].collectionId,
              returnDatasetId,
            )}
          >
            {wells[0].timepointCount <= 1 ? "Open field" : "Open trajectory"}
          </a>
        )}
        {wells.length >= 2 &&
          wells.every((well) => well.fieldPath) &&
          (channelMismatch ? (
            <button
              type="button"
              className="button-link"
              data-testid="open-comparison"
              disabled
            >
              Compare {wells.length} {singleTimepoint ? "fields" : "trajectories"}
            </button>
          ) : (
            <a
              className="button-link"
              data-testid="open-comparison"
              href={viewerHref(
                {
                  datasetId: wells[0].datasetId,
                  imagePath: wells[0].fieldPath,
                },
                wells.slice(1).map((well) => ({
                  datasetId: well.datasetId,
                  imagePath: well.fieldPath,
                })),
                wells[0].collectionId,
                returnDatasetId,
              )}
            >
              Compare {wells.length}{" "}
              {singleTimepoint ? "fields" : "trajectories"}
            </a>
          ))}
      </div>
    </div>
  );
}

export default function DataBrowser() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [collections, setCollections] = useState<DataCollection[]>([]);
  const [profileId, setProfileId] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [activeCollectionId, setActiveCollectionId] = useState<string | null>(
    null,
  );
  const [collectionRootPath, setCollectionRootPath] = useState<string | null>(
    null,
  );
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([
    { label: "Data roots", path: null },
  ]);
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [metadata, setMetadata] = useState<PlateMetadata | null>(null);
  const [selectedWells, setSelectedWells] = useState<WellSelection[]>([]);
  const [focusedGridWell, setFocusedGridWell] =
    useState<FocusedGridWell | null>(null);
  const [wellHighlightMode, setWellHighlightMode] =
    useState<WellHighlightMode | null>(null);
  const [compoundFilter, setCompoundFilter] = useState("");
  const [moaFilter, setMoaFilter] = useState("");
  const [wellKind, setWellKind] = useState<"all" | "treatment" | "control">("all");
  const [entryFilter, setEntryFilter] = useState("");
  const [error, setError] = useState("");
  const [unavailable, setUnavailable] = useState("");
  const [busy, setBusy] = useState(false);

  const activeCollection =
    collections.find((collection) => collection.id === activeCollectionId) ?? null;
  const currentPath = breadcrumbs.at(-1)?.path ?? null;

  const loadListing = useCallback(async (path: string | null) => {
    const result = await dataServerClient.GET("/api/fs/list", {
      params: { query: path ? { path } : {} },
    });
    if (!result.data) {
      throw new Error(apiErrorMessage(result.error, "Could not list data folder"));
    }
    setEntries(result.data.entries);
    return result.data.path;
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
        const [profilesResult, sessionResult, linked] = await Promise.all([
          connectorClient.GET("/connector/profiles"),
          connectorClient.GET("/connector/session"),
          loadDatasets(),
        ]);
        if (!profilesResult.data) {
          throw new Error(
            apiErrorMessage(profilesResult.error, "Could not load connector profiles"),
          );
        }
        if (!sessionResult.data) {
          throw new Error(
            apiErrorMessage(sessionResult.error, "Could not load connector session"),
          );
        }
        const profile = profilesResult.data.find(
          (candidate) => candidate.id === sessionResult.data.profile_id,
        );
        const nextProfileId = profile?.id ?? "";
        const availableCollections = profile?.collections ?? [];
        const requested = new URLSearchParams(window.location.search);
        const rememberedCollectionId = nextProfileId
          ? readCollectionPreference(window.localStorage, nextProfileId)
          : null;
        const collection =
          availableCollections.length > 0
            ? initialCollection(
                availableCollections,
                requested.get("collection"),
                rememberedCollectionId,
              )
            : null;
        if (!active) return;
        setProfileId(nextProfileId);
        setCollections(availableCollections);
        setActiveCollectionId(collection?.id ?? null);
        // A dataset this account can't read is shown as unavailable, unopened.
        const blocked = collection
          ? collectionIssue(accessIssues(sessionResult.data.handshake), collection.path)
          : null;
        if (collection && blocked) {
          setUnavailable(accessMessage(blocked, availableCollections));
          setBreadcrumbs([{ label: collection.title, path: null }]);
          return;
        }
        const listedPath = await loadListing(collection?.path ?? null);
        const canonicalCollectionPath = collection
          ? (listedPath ?? collection.path)
          : null;
        setCollectionRootPath(canonicalCollectionPath);
        setBreadcrumbs(
          collection
            ? [{ label: collection.title, path: canonicalCollectionPath }]
            : [{ label: "Data roots", path: null }],
        );

        if (collection) {
          if (nextProfileId) {
            writeCollectionPreference(
              window.localStorage,
              nextProfileId,
              collection.id,
            );
          }
          const requestedDatasetId = requested.get("dataset");
          const rememberedPlace = nextProfileId
            ? readCollectionPlace(
                window.localStorage,
                nextProfileId,
                collection.id,
              )
            : null;
          const datasetId =
            requestedDatasetId ?? rememberedPlace?.datasetId ?? null;
          const reopened =
            linked.find(
              (dataset) =>
                dataset.id === datasetId &&
                pathIsInCollection(dataset.path, canonicalCollectionPath ?? collection.path),
            ) ?? null;
          window.history.replaceState(
            window.history.state,
            "",
            dataSelectionHref(collection.id, reopened?.id ?? null),
          );
          if (reopened) {
            const rememberedWellPath =
              rememberedPlace?.datasetId === reopened.id
                ? rememberedPlace.wellPath
                : null;
            try {
              const restoredWellPath = await selectDataset(
                reopened,
                rememberedWellPath,
                collection,
              );
              if (nextProfileId) {
                writeCollectionPlace(
                  window.localStorage,
                  nextProfileId,
                  collection.id,
                  {
                    datasetId: reopened.id,
                    wellPath: restoredWellPath,
                  },
                );
              }
            } catch {
              if (nextProfileId) {
                clearCollectionPlace(
                  window.localStorage,
                  nextProfileId,
                  collection.id,
                );
              }
              clearDatasetDetails();
              replaceDataUrl(collection.id);
            }
          } else if (
            rememberedPlace?.datasetId === datasetId &&
            nextProfileId
          ) {
            clearCollectionPlace(
              window.localStorage,
              nextProfileId,
              collection.id,
            );
          }
        } else if (linked.length > 0) {
          await selectDataset(linked[0]);
        }
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (active) {
          setBusy(false);
          setInitialized(true);
        }
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

  function replaceDataUrl(
    collectionId: string | null,
    datasetId: string | null = null,
  ) {
    window.history.replaceState(
      window.history.state,
      "",
      dataSelectionHref(collectionId, datasetId),
    );
  }

  function clearDatasetDetails() {
    setSelected(null);
    setSummary(null);
    setMetadata(null);
    setFocusedGridWell(null);
    setWellHighlightMode(null);
    setCompoundFilter("");
    setMoaFilter("");
    setWellKind("all");
  }

  function rememberPlace(datasetId: string, wellPath: string | null) {
    if (!profileId || !activeCollection) return;
    writeCollectionPlace(
      window.localStorage,
      profileId,
      activeCollection.id,
      { datasetId, wellPath },
    );
  }

  function forgetPlace() {
    if (!profileId || !activeCollection) return;
    clearCollectionPlace(
      window.localStorage,
      profileId,
      activeCollection.id,
    );
  }

  function currentDatasetWellPath(): string | null {
    if (!selected) return null;
    if (
      focusedGridWell?.datasetId === selected.id &&
      selectedWells.some(
        (well) =>
          well.datasetId === selected.id && well.path === focusedGridWell.path,
      )
    ) {
      return focusedGridWell.path;
    }
    return (
      selectedWells.find((well) => well.datasetId === selected.id)?.path ?? null
    );
  }

  async function browse(entry: Entry) {
    await run(async () => {
      await loadListing(entry.path);
      setEntryFilter("");
      setBreadcrumbs((current) => [
        ...current,
        {
          label: activeCollection ? collectionEntryName(entry.name) : entry.name,
          path: entry.path,
        },
      ]);
    });
  }

  async function browseCrumb(index: number) {
    await run(async () => {
      const target = breadcrumbs[index];
      await loadListing(target.path);
      setEntryFilter("");
      setBreadcrumbs((current) => current.slice(0, index + 1));
    });
  }

  async function link(entry: Entry) {
    await run(async () => {
      const restoreWellPath = collectionCarriesWellBetweenPlates(
        activeCollection?.id,
      )
        ? currentDatasetWellPath()
        : null;
      const carrySource =
        selected && restoreWellPath
          ? { datasetId: selected.id, path: restoreWellPath }
          : null;
      const result = await dataServerClient.POST("/api/datasets", {
        headers: webClientHeaders,
        body: { path: entry.path },
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Could not link dataset"));
      }
      await loadDatasets();
      const restoredWellPath = await selectDataset(
        result.data,
        restoreWellPath,
        activeCollection,
        carrySource,
      );
      if (activeCollection) {
        replaceDataUrl(activeCollection.id, result.data.id);
        rememberPlace(result.data.id, restoredWellPath);
      }
    });
  }

  async function unlink(dataset: Dataset) {
    await run(async () => {
      const result = await dataServerClient.DELETE("/api/datasets/{dataset_id}", {
        headers: webClientHeaders,
        params: { path: { dataset_id: dataset.id } },
      });
      if (result.response.status !== 204) {
        throw new Error(apiErrorMessage(result.error, "Could not unlink dataset"));
      }
      const linked = await loadDatasets();
      setSelectedWells((current) =>
        current.filter((well) => well.datasetId !== dataset.id),
      );
      if (focusedGridWell?.datasetId === dataset.id) {
        setFocusedGridWell(null);
        setWellHighlightMode(null);
      }
      if (selected?.id === dataset.id) {
        clearDatasetDetails();
        const next = activeCollection
          ? linked.find((candidate) =>
              pathIsInCollection(
                candidate.path,
                collectionRootPath ?? activeCollection.path,
              ),
            )
          : linked[0];
        if (next) {
          await selectDataset(next);
          if (activeCollection) {
            replaceDataUrl(activeCollection.id, next.id);
            rememberPlace(next.id, null);
          }
        } else if (activeCollection) {
          replaceDataUrl(activeCollection.id);
          forgetPlace();
        }
      }
    });
  }

  async function chooseDataset(dataset: Dataset) {
    await run(async () => {
      const restoreWellPath = collectionCarriesWellBetweenPlates(
        activeCollection?.id,
      )
        ? currentDatasetWellPath()
        : null;
      const carrySource =
        selected && restoreWellPath
          ? { datasetId: selected.id, path: restoreWellPath }
          : null;
      const restoredWellPath = await selectDataset(
        dataset,
        restoreWellPath,
        activeCollection,
        carrySource,
      );
      if (activeCollection) {
        replaceDataUrl(activeCollection.id, dataset.id);
        rememberPlace(dataset.id, restoredWellPath);
      }
    });
  }

  async function selectDataset(
    dataset: Dataset,
    restoreWellPath: string | null = null,
    collection: DataCollection | null = activeCollection,
    carrySource: FocusedGridWell | null = null,
  ): Promise<string | null> {
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

    if (
      !restoreWellPath ||
      summaryResult.data.kind !== "plate" ||
      !summaryResult.data.wells.some((well) => well.path === restoreWellPath)
    ) {
      setFocusedGridWell(null);
      setWellHighlightMode(null);
      return null;
    }
    const existing = selectedWells.find(
      (well) =>
        well.datasetId === dataset.id && well.path === restoreWellPath,
    );
    if (existing) {
      setFocusedGridWell({ datasetId: dataset.id, path: restoreWellPath });
      return restoreWellPath;
    }
    if (
      selectedWells.length >= MAX_COMPARISON_SOURCES &&
      (!carrySource ||
        !selectedWells.some(
          (well) =>
            well.datasetId === carrySource.datasetId &&
            well.path === carrySource.path,
        ))
    ) {
      setFocusedGridWell(null);
      setWellHighlightMode(null);
      return null;
    }
    try {
      const restored = await loadWellSelection(
        dataset,
        summaryResult.data,
        restoreWellPath,
        collection,
      );
      const nextSelections = carrySelectionAcrossDatasets(
        selectedWells,
        restored,
        carrySource,
        MAX_COMPARISON_SOURCES,
      );
      if (!nextSelections) {
        setFocusedGridWell(null);
        setWellHighlightMode(null);
        return null;
      }
      setSelectedWells(nextSelections);
      setFocusedGridWell({ datasetId: dataset.id, path: restoreWellPath });
      return restoreWellPath;
    } catch {
      setFocusedGridWell(null);
      setWellHighlightMode(null);
      return null;
    }
  }

  async function loadWellSelection(
    dataset: Dataset,
    datasetSummary: Summary,
    wellPath: string,
    collection: DataCollection | null,
  ): Promise<WellSelection> {
    const selectedImage = imageFromSummary(datasetSummary);
    const channelLabels = selectedImage ? imageChannelLabels(selectedImage) : [];
    const timepointCount = selectedImage
      ? imageAxisSize(selectedImage, "t")
      : 1;
    const result = await dataServerClient.GET(
      "/api/datasets/{dataset_id}/wells/{well_path}",
      {
        params: {
          path: { dataset_id: dataset.id, well_path: wellPath },
        },
      },
    );
    if (!result.data) {
      throw new Error(apiErrorMessage(result.error, "Could not load well fields"));
    }
    const fields = result.data.fields;
    return {
      datasetId: dataset.id,
      datasetName: dataset.name,
      collectionId: collection?.id ?? null,
      path: wellPath,
      fields,
      fieldPath: fields[0]?.path ?? "",
      channelLabels,
      timepointCount,
    };
  }

  async function toggleWell(wellPath: string) {
    if (!selected || !summary) return;
    if (
      selectedWells.some(
        (well) => well.datasetId === selected.id && well.path === wellPath,
      )
    ) {
      const remaining = selectedWells.filter(
        (well) => well.datasetId !== selected.id || well.path !== wellPath,
      );
      setSelectedWells(remaining);
      if (
        focusedGridWell?.datasetId === selected.id &&
        focusedGridWell.path === wellPath
      ) {
        const fallback = [...remaining]
          .reverse()
          .find((well) => well.datasetId === selected.id);
        setFocusedGridWell(
          fallback ? { datasetId: fallback.datasetId, path: fallback.path } : null,
        );
        if (!fallback) setWellHighlightMode(null);
        rememberPlace(selected.id, fallback?.path ?? null);
      }
      return;
    }
    if (selectedWells.length >= MAX_COMPARISON_SOURCES) return;
    await run(async () => {
      const selection = await loadWellSelection(
        selected,
        summary,
        wellPath,
        activeCollection,
      );
      setSelectedWells((current) => {
        if (
          current.length >= MAX_COMPARISON_SOURCES ||
          current.some(
            (well) => well.datasetId === selected.id && well.path === wellPath,
          )
        ) {
          return current;
        }
        return [...current, selection];
      });
      setFocusedGridWell({ datasetId: selected.id, path: wellPath });
      rememberPlace(selected.id, wellPath);
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
    const remaining = selectedWells.filter(
      (well) => well.datasetId !== datasetId || well.path !== wellPath,
    );
    setSelectedWells(remaining);
    if (
      focusedGridWell?.datasetId === datasetId &&
      focusedGridWell.path === wellPath
    ) {
      const fallback = [...remaining]
        .reverse()
        .find((well) => well.datasetId === datasetId);
      setFocusedGridWell(
        fallback ? { datasetId: fallback.datasetId, path: fallback.path } : null,
      );
      if (!fallback) setWellHighlightMode(null);
      if (selected?.id === datasetId) {
        rememberPlace(datasetId, fallback?.path ?? null);
      }
    }
  }

  function clearSelectedWells() {
    setSelectedWells([]);
    setFocusedGridWell(null);
    setWellHighlightMode(null);
    if (selected && activeCollection) {
      rememberPlace(selected.id, null);
    }
  }

  const metadataByWell = useMemo(
    () =>
      new Map(
        (metadata?.wells ?? []).map((well) => [well.well.toUpperCase(), well]),
      ),
    [metadata],
  );
  const labels = metadataLabels(metadata);
  const focusedWellPath =
    selected &&
    focusedGridWell?.datasetId === selected.id &&
    selectedWells.some(
      (well) =>
        well.datasetId === focusedGridWell.datasetId &&
        well.path === focusedGridWell.path,
    )
      ? focusedGridWell.path
      : selectedWells.find((well) => well.datasetId === selected?.id)?.path;
  const focusedWellMetadata = findWellMetadata(metadata, focusedWellPath);

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
          metadata?.category_colors?.[moa] ??
            metadataColors[index % metadataColors.length],
        ]),
      ),
    [metadata?.category_colors, moaOptions],
  );

  const filtersActive =
    compoundFilter !== "" || moaFilter !== "" || wellKind !== "all";
  const wellHighlightsActive = wellHighlightMode !== null;

  function clearMetadataFilters() {
    setCompoundFilter("");
    setMoaFilter("");
    setWellKind("all");
  }

  const plateGridWells = useMemo(() => {
    const result = new Map<string, PlateGridWell>();
    if (!summary || summary.kind !== "plate" || !selected) return result;
    for (const well of summary.wells) {
      const wellMetadata = metadataByWell.get(
        well.row.toUpperCase() + String(well.column).padStart(2, "0"),
      );
      const matchesMetadataFilters = Boolean(
        wellMetadata &&
          (!compoundFilter || wellMetadata.compound === compoundFilter) &&
          (!moaFilter || wellMetadata.moa === moaFilter) &&
          (wellKind !== "control" || wellMetadata.is_control) &&
          (wellKind !== "treatment" || !wellMetadata.is_control),
      );
      const matchesWellHighlight =
        wellHighlightMode === "gene"
          ? Boolean(
              focusedWellMetadata?.compound &&
                wellMetadata?.compound === focusedWellMetadata.compound,
            )
          : wellHighlightMode === "control"
            ? Boolean(wellMetadata?.is_control)
            : true;
      const matches = matchesMetadataFilters && matchesWellHighlight;
      const selectedIndex = selectedWells.findIndex(
        (selection) =>
          selection.datasetId === selected.id && selection.path === well.path,
      );
      const color = wellMetadata?.is_control
        ? "#7b8490"
        : wellMetadata?.moa
          ? moaColors.get(wellMetadata.moa)
          : undefined;
      const description = wellMetadata
        ? [
            wellMetadata.compound,
            wellMetadata.dose_um == null ? null : doseLabel(wellMetadata.dose_um),
            wellMetadata.is_control ? labels.control : wellMetadata.moa,
          ]
            .filter(Boolean)
            .join(" \u00b7 ")
        : undefined;
      result.set(plateGridKey(well.row, well.column), {
        id: well.path,
        row: well.row,
        column: well.column,
        color,
        selected: selectedIndex >= 0,
        control: wellMetadata?.is_control,
        highlighted: (filtersActive || wellHighlightsActive) && matches,
        dimmed: (filtersActive || wellHighlightsActive) && !matches,
        disabled:
          busy ||
          (selectedWells.length >= MAX_COMPARISON_SOURCES && selectedIndex < 0),
        title: description,
      });
    }
    return result;
  }, [
    busy,
    compoundFilter,
    filtersActive,
    focusedWellMetadata?.compound,
    labels.control,
    metadataByWell,
    moaColors,
    moaFilter,
    selected,
    selectedWells,
    summary,
    wellHighlightMode,
    wellHighlightsActive,
    wellKind,
  ]);

  const collectionEntries = activeCollection
    ? entries.filter((entry) => entry.kind !== "file" || Boolean(entry.ome_zarr))
    : entries;
  const showEntryFilter =
    activeCollection !== null && collectionEntries.length > 30;
  const normalizedEntryFilter = entryFilter.trim().toLowerCase();
  const visibleEntries =
    normalizedEntryFilter === ""
      ? collectionEntries
      : collectionEntries.filter((entry) => {
          const detail = entry.detail ?? "";
          return (
            collectionEntryName(entry.name)
              .toLowerCase()
              .includes(normalizedEntryFilter) ||
            detail.toLowerCase().includes(normalizedEntryFilter)
          );
        });
  const visibleDatasets = activeCollection
    ? datasets.filter((dataset) =>
        pathIsInCollection(
          dataset.path,
          collectionRootPath ?? activeCollection.path,
        ),
      )
    : datasets;

  if (!initialized) {
    return <p className="muted">Loading data browser...</p>;
  }

  return (
    <>
      {activeCollection ? (
        <header className="data-page-heading">
          <div>
            <h1>{activeCollection.title}</h1>
            {activeCollection.description && (
              <p className="lede">{activeCollection.description}</p>
            )}
          </div>
          <a className="change-dataset-link" href="/">
            Change dataset
          </a>
        </header>
      ) : (
        <>
          <h1>Browse datasets</h1>
          <p className="lede">
            Explore imaging datasets where they are stored, filter plate wells by
            experimental metadata, and open fields without copying the data.
          </p>
        </>
      )}
      <div className="page-grid">
      <section className="panel stack" aria-labelledby="browser-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">
              {activeCollection ? "Dataset" : "Filesystem"}
            </p>
            <h2 id="browser-heading">
              {activeCollection
                ? "Browse plates"
                : "Browse data roots"}
            </h2>
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
        {unavailable && (
          <div className="notice warning" role="status">
            <strong>{unavailable}</strong>
            <p>
              Choose another dataset on <a href="/">Connect</a>, or ask the data owner
              for access.
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

        {showEntryFilter && (
          <label className="entry-filter">
            <span>Filter this dataset</span>
            <input
              type="search"
              value={entryFilter}
              placeholder="Filter plates and images"
              onChange={(event) => setEntryFilter(event.target.value)}
            />
          </label>
        )}

        <ul className="file-list" aria-label="Folder contents">
          {visibleEntries.map((entry) => (
            <li key={entry.path}>
              <div>
                <strong>
                  {activeCollection
                    ? collectionEntryName(entry.name)
                    : entry.name}
                </strong>
                <span className="entry-kind">
                  {activeCollection && entry.detail
                    ? entry.detail
                    : entry.ome_zarr
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
                  {activeCollection ? "Open" : "Link"}
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
          {!busy && !unavailable && visibleEntries.length === 0 && (
            <li className="muted">
              {showEntryFilter && normalizedEntryFilter
                ? "No entries match this filter."
                : "Folder is empty."}
            </li>
          )}
        </ul>
      </section>

      <section className="panel stack" aria-labelledby="datasets-heading">
        <div>
          <p className="eyebrow">Registry</p>
          <h2 id="datasets-heading">
            {activeCollection ? "Opened plates" : "Linked datasets"}
          </h2>
        </div>
        {visibleDatasets.length === 0 ? (
          <p className="muted">
            {activeCollection
              ? "Open an OME-Zarr plate or image from the browser."
              : "Link an OME-Zarr plate or image from the browser."}
          </p>
        ) : (
          <ul className="dataset-list">
            {visibleDatasets.map((dataset) => (
              <li key={dataset.id} className={selected?.id === dataset.id ? "selected" : ""}>
                <button
                  type="button"
                  className="text-button dataset-name"
                  disabled={busy}
                  onClick={() => void chooseDataset(dataset)}
                >
                  {activeCollection
                    ? collectionEntryName(dataset.name)
                    : dataset.name}
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
                  {activeCollection ? "Remove" : "Unlink"}
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
              <h2 id="summary-heading">
                {activeCollection
                  ? collectionEntryName(selected.name)
                  : selected.name}
              </h2>
            </div>
            {summary.kind === "image" && (
              <a
                className="button-link"
                href={viewerHref(
                  {
                    datasetId: selected.id,
                    imagePath: summary.path,
                  },
                  [],
                  activeCollection?.id ?? null,
                  selected.id,
                )}
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
                      <h3>
                        {metadata.labels
                          ? "Find " + pluralizeFilterLabel(labels.compound)
                          : "Find treatments"}
                      </h3>
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
                      <span>
                        {metadata.labels ? labels.compound : "Compound and dose"}
                      </span>
                      <select
                        data-testid="compound-filter"
                        value={compoundFilter}
                        onChange={(event) => setCompoundFilter(event.target.value)}
                      >
                        <option value="">
                          {metadata.labels
                            ? "All " + pluralizeFilterLabel(labels.compound)
                            : "All compounds"}
                        </option>
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
                      <span>
                        {metadata.labels ? labels.moa : "Mechanism class"}
                      </span>
                      <select
                        data-testid="moa-filter"
                        value={moaFilter}
                        onChange={(event) => setMoaFilter(event.target.value)}
                      >
                        <option value="">
                          {metadata.labels
                            ? "All " + pluralizeFilterLabel(labels.moa)
                            : "All MOA classes"}
                        </option>
                        {moaOptions.map((moa) => (
                          <option key={moa} value={moa}>
                            {moa}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>{metadata.labels ? labels.control : "Well type"}</span>
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
                        <option value="control">
                          {metadata.labels ? labels.control : "Controls"}
                        </option>
                      </select>
                    </label>
                  </div>
                </div>
              )}

              <PlateMetadataPanel
                metadata={metadata}
                selectedWell={focusedWellPath}
              />

              {metadata?.labels && focusedWellPath && focusedWellMetadata && (
                <section
                  className="subpanel stack"
                  aria-label={"Well highlights for " + focusedWellPath}
                  data-testid="well-highlight-controls"
                >
                  <div>
                    <p className="eyebrow">Selected well</p>
                    <h3>Highlight matching wells</h3>
                  </div>
                  <div
                    className="button-row"
                    role="group"
                    aria-label="Highlight wells"
                  >
                    <button
                      type="button"
                      className={wellHighlightMode === "gene" ? "" : "secondary"}
                      disabled={busy || !focusedWellMetadata.compound}
                      aria-pressed={wellHighlightMode === "gene"}
                      onClick={() =>
                        setWellHighlightMode((current) =>
                          current === "gene" ? null : "gene",
                        )
                      }
                    >
                      Same gene
                    </button>
                    <button
                      type="button"
                      className={
                        wellHighlightMode === "control" ? "" : "secondary"
                      }
                      disabled={busy}
                      aria-pressed={wellHighlightMode === "control"}
                      onClick={() =>
                        setWellHighlightMode((current) =>
                          current === "control" ? null : "control",
                        )
                      }
                    >
                      Controls
                    </button>
                  </div>
                </section>
              )}

              <div className="selection-hint">
                <strong>
                  {selectedWells.length === 0
                    ? "Select a well"
                    : selectedWells.length === 1
                      ? "One well selected"
                      : String(selectedWells.length) + " wells selected"}
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
                    Choose one well to inspect it, or keep up to four wells in
                    the comparison tray while you move between linked
                    experiments.
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
                      {labels.control}
                    </span>
                  )}
                </div>
              )}

              <PlateGrid
                rows={summary.rows}
                columns={summary.columns}
                wells={plateGridWells}
                onSelect={(well) => void toggleWell(well.id)}
              />

              <ComparisonTray
                wells={selectedWells}
                busy={busy}
                returnDatasetId={selected.id}
                onClear={clearSelectedWells}
                onSelectField={selectField}
                onRemoveWell={removeWell}
              />
            </>
          )}
        </section>
      )}
      {collections.length > 0 &&
        selectedWells.length > 0 &&
        (!selected || summary?.kind !== "plate") && (
          <section
            className="panel summary-panel stack"
            aria-label="Comparison tray"
          >
            <ComparisonTray
              wells={selectedWells}
              busy={busy}
              returnDatasetId={selected?.id ?? null}
              onClear={clearSelectedWells}
              onSelectField={selectField}
              onRemoveWell={removeWell}
            />
          </section>
        )}
      </div>
    </>
  );
}
