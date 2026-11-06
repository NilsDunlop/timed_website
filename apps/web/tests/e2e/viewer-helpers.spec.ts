import { getChannelStats } from "@hms-dbmi/viv";
import { expect, test } from "@playwright/test";

import type { PlateMetadata } from "../../src/lib/plateMetadata";
import { channelStates } from "../../src/lib/viewerChannels";
import {
  sitePositionFixNote,
  viewerSourceIdentity,
} from "../../src/lib/viewerSourceIdentity";
import type {
  ImageSummary,
  Loader,
  ViewerSourceState,
} from "../../src/lib/viewerSources";

type RasterRequest = { selection: Record<string, number> };

function fakeLoader(
  shape: number[],
  getRaster: (request: RasterRequest) => Promise<{ data: Uint16Array }>,
): { loader: Loader; requests: RasterRequest[] } {
  const requests: RasterRequest[] = [];
  const source = {
    labels: ["t", "c", "y", "x"],
    shape,
    getRaster: (request: RasterRequest) => {
      requests.push(request);
      return getRaster(request);
    },
  };
  return { loader: [source] as unknown as Loader, requests };
}

function image(channels: ImageSummary["channels"]): ImageSummary {
  return {
    axes: [],
    channels,
    kind: "image",
    levels: [],
    name: "field",
    ome_zarr_version: "0.5",
    path: "A01_s1.zarr",
    zarr_format: 3,
  };
}

function viewerSource(path: string, metadata: PlateMetadata | null): ViewerSourceState {
  return {
    datasetId: "dataset",
    path,
    image: image([]),
    field: null,
    metadata,
    loader: [],
    readyKey: "",
  };
}

test("channelStates keeps a declared window and reads no pixels", async () => {
  const { loader, requests } = fakeLoader([1, 1, 4, 4], () =>
    Promise.reject(new Error("no pixels should be read")),
  );
  const states = await channelStates(
    loader,
    image([
      {
        name: "DNA",
        color: "0000FF",
        active: true,
        window: { min: 0, max: 27000, start: 101, end: 5250 },
      },
    ]),
  );

  expect(states).toEqual([
    { name: "DNA", color: [0, 0, 255], visible: true, domain: [0, 27000], contrast: [101, 5250] },
  ]);
  expect(requests).toEqual([]);
});

test("channelStates measures a channel without a window up to the domain maximum", async () => {
  // Distinct values, so the measured upper limit (a high percentile) is below the
  // maximum. getChannelStats reorders its input, so every call gets a copy.
  const pixels = Uint16Array.from({ length: 64 * 64 }, (_value, index) => index + 1);
  const { loader, requests } = fakeLoader([2, 2, 64, 64], () =>
    Promise.resolve({ data: pixels.slice() }),
  );
  const states = await channelStates(
    loader,
    image([{ name: "ER", color: null, active: false, window: null }]),
  );
  const statistics = getChannelStats(pixels.slice());
  const domain = [statistics.domain[0], statistics.domain[1]];
  const contrast = [statistics.contrastLimits[0], statistics.domain[1]];

  // Two source channels and one described channel: the second gets a fallback.
  expect(requests).toEqual([{ selection: { t: 0, c: 0 } }, { selection: { t: 0, c: 1 } }]);
  expect(states).toEqual([
    { name: "ER", color: [0, 0, 255], visible: false, domain, contrast },
    { name: "Channel 2", color: [0, 255, 0], visible: true, domain, contrast },
  ]);
  // The automatic path keeps its rule: contrast ends at the domain maximum, not at
  // the measured upper limit.
  expect(statistics.contrastLimits[1]).toBeLessThan(statistics.domain[1]);
});

test("channelStates falls back to the declared range when pixels cannot be read", async () => {
  const { loader } = fakeLoader([1, 1, 4, 4], () =>
    Promise.reject(new Error("tile failed")),
  );
  const states = await channelStates(
    loader,
    image([{ name: "AGP", color: "ff0019", active: true, window: { min: 100, max: 8000 } }]),
  );

  expect(states).toEqual([
    { name: "AGP", color: [255, 0, 25], visible: true, domain: [100, 8000], contrast: [100, 8000] },
  ]);
});

test("channelStates returns no channels for an empty loader", async () => {
  expect(await channelStates([], image([{ name: "DNA" }]))).toEqual([]);
});

test("viewerSourceIdentity describes legacy plate metadata", () => {
  const metadata: PlateMetadata = {
    available: true,
    plate: { plate_id: " PB100140_P2.1 " },
    wells: [
      { well: "C03", compound: " Staurosporine ", dose_um: 10, is_control: false },
      { well: "C04", is_control: true },
    ],
  };

  expect(viewerSourceIdentity(viewerSource("C03_s1.zarr", metadata))).toEqual({
    compoundLabel: "Compound",
    compound: "Staurosporine",
    concentration: "10 µM",
    well: "C03",
    site: "1",
    plate: "PB100140_P2.1",
  });
  expect(viewerSourceIdentity(viewerSource("C04_s2.zarr", metadata))).toEqual({
    compoundLabel: "Compound",
    compound: "Control",
    concentration: "Not specified",
    well: "C04",
    site: "2",
    plate: "PB100140_P2.1",
  });
  expect(viewerSourceIdentity(viewerSource("B/2/0", null))).toEqual({
    compoundLabel: "Compound",
    compound: "Not specified",
    concentration: "Not specified",
    well: "B02",
    site: "0",
    plate: "Not specified",
  });
});

test("viewerSourceIdentity uses labelled metadata and hides a missing dose", () => {
  const metadata: PlateMetadata = {
    available: true,
    labels: { compound: "Gene", moa: "Perturbation", control: "Control" },
    plate: { plate_id: "SQ00014610" },
    wells: [
      { well: "A01", compound: "EMPTY", is_control: true },
      { well: "A02", compound: "TP53", dose_um: 10, is_control: false },
    ],
  };

  expect(viewerSourceIdentity(viewerSource("A01_s1.zarr", metadata))).toEqual({
    compoundLabel: "Gene",
    compound: "EMPTY",
    concentration: null,
    well: "A01",
    site: "1",
    plate: "SQ00014610",
  });
  expect(viewerSourceIdentity(viewerSource("A02_s3.zarr", metadata)).concentration).toBe(
    "10 µM",
  );
});

test("sitePositionFixNote describes one or several repaired timepoints", () => {
  expect(sitePositionFixNote(null)).toBeNull();
  expect(sitePositionFixNote(undefined)).toBeNull();
  expect(sitePositionFixNote({ grid_x: 2, repaired_timepoints: [3] })).toBe(
    "Site position repaired at timepoint 3: this field holds grid position x = 2 for the whole run.",
  );
  expect(sitePositionFixNote({ grid_x: 0, repaired_timepoints: [3, 4] })).toBe(
    "Site position repaired at timepoints 3, 4: this field holds grid position x = 0 for the whole run.",
  );
});
