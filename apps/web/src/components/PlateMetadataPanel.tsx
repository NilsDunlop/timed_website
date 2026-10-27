import type { components } from "../lib/api/dataserver-schema";

export type PlateMetadata = components["schemas"]["PlateMetadataResponse"];
export type WellMetadata = components["schemas"]["WellMetadataSummary"];
export type PlateMetadataLabels = components["schemas"]["PlateMetadataLabels"];

type Props = {
  metadata: PlateMetadata | null;
  selectedWell?: string;
  detailsOnly?: boolean;
};

const defaultLabels: PlateMetadataLabels = {
  compound: "Compound",
  moa: "MOA",
  control: "Control",
};

export function wellIdFromPath(path: string): string | undefined {
  const collection = /^([A-Za-z]+)(\d+)_s\d+\.zarr$/.exec(path);
  if (collection) {
    return collection[1].toUpperCase() + collection[2].padStart(2, "0");
  }
  const native = /^([A-Za-z]+)\/(\d+)(?:\/|$)/.exec(path);
  if (native) {
    return native[1].toUpperCase() + native[2].padStart(2, "0");
  }
  return undefined;
}

function shown(value: string | number | boolean | null | undefined): string {
  if (value == null || value === "") return "Not specified";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function hasValue(value: unknown): boolean {
  return value != null && value !== "";
}

export function metadataLabels(
  metadata: PlateMetadata | null | undefined,
): PlateMetadataLabels {
  return metadata?.labels ?? defaultLabels;
}

export function doseLabel(value: number | null | undefined): string {
  if (value == null) return "Not specified";
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 }) + " \u00b5M";
}

export function findWellMetadata(
  metadata: PlateMetadata | null,
  selectedWell: string | undefined,
): WellMetadata | undefined {
  if (!selectedWell) return undefined;
  const canonical = wellIdFromPath(selectedWell) ?? selectedWell.toUpperCase();
  return metadata?.wells?.find((well) => well.well.toUpperCase() === canonical);
}

export default function PlateMetadataPanel({
  metadata,
  selectedWell,
  detailsOnly = false,
}: Props) {
  if (!metadata?.available || !metadata.plate) return null;
  const plate = metadata.plate;
  const acquisition = plate.acquisition;
  const well = findWellMetadata(metadata, selectedWell);
  const labels = metadataLabels(metadata);
  const omitMissing = metadata.labels != null;
  const show = (value: unknown) => !omitMissing || hasValue(value);
  const controlName = metadata.labels?.control ?? "Solvent control";

  return (
    <section
      className={
        "subpanel metadata-panel stack" +
        (detailsOnly ? " metadata-panel-details-only" : "")
      }
      data-testid="plate-metadata"
      aria-label="Experiment metadata"
    >
      {!detailsOnly && (
        <div>
          <p className="eyebrow">Experiment metadata</p>
          <h3>{well ? "Plate and well information" : "Plate information"}</h3>
        </div>
      )}

      {well && !detailsOnly && (
        <dl className="status-grid well-metadata-grid">
          <div>
            <dt>Well</dt>
            <dd>{well.well}</dd>
          </div>
          {show(well.compound) && (
            <div>
              <dt>{labels.compound}</dt>
              <dd>{shown(well.compound)}</dd>
            </div>
          )}
          {show(well.dose_um) && (
            <div>
              <dt>Dose</dt>
              <dd>{doseLabel(well.dose_um)}</dd>
            </div>
          )}
          {show(well.well_type) && (
            <div>
              <dt>Type</dt>
              <dd>
                {well.is_control && !metadata.labels
                  ? controlName
                  : shown(well.well_type)}
              </dd>
            </div>
          )}
        </dl>
      )}

      {well &&
        (detailsOnly ||
          well.moa ||
          well.target ||
          well.pathway ||
          well.mechanism_of_action ||
          (well.details?.length ?? 0) > 0) && (
          <details>
            <summary>Biological annotation</summary>
            <dl className="status-grid metadata-annotation-grid">
              {detailsOnly && show(well.well_type) && (
                <div>
                  <dt>Treatment type</dt>
                  <dd>
                    {well.is_control && !metadata.labels
                      ? controlName
                      : shown(well.well_type)}
                  </dd>
                </div>
              )}
              {show(well.moa) && (
                <div>
                  <dt>{labels.moa}</dt>
                  <dd>{shown(well.moa)}</dd>
                </div>
              )}
              {show(well.target) && (
                <div>
                  <dt>Target</dt>
                  <dd>{shown(well.target)}</dd>
                </div>
              )}
              {show(well.pathway) && (
                <div>
                  <dt>Pathway</dt>
                  <dd>{shown(well.pathway)}</dd>
                </div>
              )}
              {show(well.mechanism_of_action) && (
                <div>
                  <dt>Mechanism</dt>
                  <dd>{shown(well.mechanism_of_action)}</dd>
                </div>
              )}
              {(well.details ?? []).map((detail) => (
                <div key={detail.label + "\u0000" + detail.value}>
                  <dt>{detail.label}</dt>
                  <dd>{detail.value}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}

      <details>
        <summary>Plate and experiment</summary>
        <dl className="status-grid metadata-details">
          <div>
            <dt>Plate</dt>
            <dd>{plate.plate_id}</dd>
          </div>
          {show(plate.project ?? acquisition?.project_name) && (
            <div>
              <dt>Project</dt>
              <dd>{shown(plate.project ?? acquisition?.project_name)}</dd>
            </div>
          )}
          {(!omitMissing ||
            hasValue(plate.plate_series) ||
            plate.plate_replicate != null) && (
            <div>
              <dt>Series and replicate</dt>
              <dd>
                {[
                  plate.plate_series,
                  plate.plate_replicate == null
                    ? null
                    : "replicate " + String(plate.plate_replicate),
                ]
                  .filter((value) => value != null)
                  .join(" \u00b7 ") || "Not specified"}
              </dd>
            </div>
          )}
          {show(plate.plate_barcode) && (
            <div>
              <dt>Barcode</dt>
              <dd>{shown(plate.plate_barcode)}</dd>
            </div>
          )}
          {show(plate.experiment_date) && (
            <div>
              <dt>Experiment date</dt>
              <dd>{shown(plate.experiment_date)}</dd>
            </div>
          )}
          {show(plate.cell_line) && (
            <div>
              <dt>Cell line</dt>
              <dd>{shown(plate.cell_line)}</dd>
            </div>
          )}
          {show(plate.cell_modification) && (
            <div>
              <dt>Cell modification</dt>
              <dd>{shown(plate.cell_modification)}</dd>
            </div>
          )}
          {show(plate.passage) && (
            <div>
              <dt>Passage</dt>
              <dd>{shown(plate.passage)}</dd>
            </div>
          )}
          {show(plate.seeding_density_cells_per_well) && (
            <div>
              <dt>Seeding density</dt>
              <dd>
                {plate.seeding_density_cells_per_well == null
                  ? "Not specified"
                  : plate.seeding_density_cells_per_well.toLocaleString() +
                    " cells/well"}
              </dd>
            </div>
          )}
          {show(plate.dye_program) && (
            <div>
              <dt>Dye program</dt>
              <dd>{shown(plate.dye_program)}</dd>
            </div>
          )}
        </dl>
      </details>

      {acquisition && (
        <details>
          <summary>Acquisition</summary>
          <dl className="status-grid metadata-details">
            {(!omitMissing ||
              hasValue(acquisition.plate_manufacturer) ||
              hasValue(acquisition.plate_model)) && (
              <div>
                <dt>Plate model</dt>
                <dd>
                  {[acquisition.plate_manufacturer, acquisition.plate_model]
                    .filter(Boolean)
                    .join(" \u00b7 ") || "Not specified"}
                </dd>
              </div>
            )}
            {show(acquisition.timepoint_count) && (
              <div>
                <dt>Time course</dt>
                <dd>
                  {acquisition.timepoint_count == null
                    ? "Not specified"
                    : String(acquisition.timepoint_count) +
                      (acquisition.timepoint_count === 1
                        ? " timepoint"
                        : " timepoints") +
                      (acquisition.interval_minutes == null
                        ? ""
                        : " \u00b7 " +
                          acquisition.interval_minutes.toLocaleString() +
                          " min interval")}
                </dd>
              </div>
            )}
            {show(acquisition.sites_per_well) && (
              <div>
                <dt>Sites per well</dt>
                <dd>{shown(acquisition.sites_per_well)}</dd>
              </div>
            )}
            {(!omitMissing ||
              acquisition.site_spacing_x_mm != null ||
              acquisition.site_spacing_y_mm != null) && (
              <div>
                <dt>Site spacing</dt>
                <dd>
                  {acquisition.site_spacing_x_mm == null
                    ? "Not specified"
                    : acquisition.site_spacing_x_mm.toLocaleString() +
                      " \u00d7 " +
                      shown(acquisition.site_spacing_y_mm) +
                      " mm"}
                </dd>
              </div>
            )}
            {show(acquisition.autofocus_enabled) && (
              <div>
                <dt>Autofocus</dt>
                <dd>{shown(acquisition.autofocus_enabled)}</dd>
              </div>
            )}
            {show(acquisition.plate_bottom_offset_mm) && (
              <div>
                <dt>Bottom offset</dt>
                <dd>
                  {acquisition.plate_bottom_offset_mm == null
                    ? "Not specified"
                    : acquisition.plate_bottom_offset_mm.toLocaleString() + " mm"}
                </dd>
              </div>
            )}
          </dl>

          {acquisition.channels && acquisition.channels.length > 0 && (
            <div>
              <h4>Enabled channels</h4>
              <ul className="metadata-channel-list">
                {acquisition.channels.map((channel) => (
                  <li key={channel.name}>{channel.name}</li>
                ))}
              </ul>
            </div>
          )}
        </details>
      )}
    </section>
  );
}
