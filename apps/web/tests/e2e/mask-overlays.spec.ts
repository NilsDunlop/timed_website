import { expect, test } from "@playwright/test";

import {
  chooseMaskModel,
  formatMaskCounts,
  maskCountParts,
  maskKindColor,
  maskKindLabel,
  maskOutlineUrl,
  sharedMaskModels,
  type MaskModel,
} from "../../src/lib/maskOverlays";
import {
  parseOutlinePreset,
  viewerQueryHref,
  writeOutlinePreset,
} from "../../src/lib/viewerLink";

function model(tag: string, changes: Partial<MaskModel> = {}): MaskModel {
  return {
    model_tag: tag,
    recipe: null,
    kinds: ["nuclei", "cells", "cytoplasm"],
    frozen: false,
    created_at: null,
    available: true,
    default: false,
    ...changes,
  };
}

const B90 = model("idr0080_cpsam_B90", { default: true });
const STOCK = model("cpsam_stock_R2_top999");

test("kinds have the contract's colours and readable labels", () => {
  expect(["nuclei", "cells", "cytoplasm"].map(maskKindColor)).toEqual([
    "FFD400",
    "FFFFFF",
    "FF8C00",
  ]);
  expect(maskKindColor("membranes")).toBe("00E5FF");
  expect(["nuclei", "cells", "cytoplasm", "membranes"].map(maskKindLabel)).toEqual([
    "Nuclei",
    "Cells",
    "Cytoplasm",
    "Membranes",
  ]);
});

test("the count line follows the model's kind order", () => {
  expect(
    formatMaskCounts(B90.kinds, { cells: 616, nuclei: 731, cytoplasm: 616 }),
  ).toBe("731 nuclei · 616 cells · 616 cytoplasm");
  expect(formatMaskCounts(B90.kinds, { nuclei: 1, cells: 1 })).toBe(
    "1 nucleus · 1 cell",
  );
  expect(formatMaskCounts(["nuclei"], { nuclei: 12_345, membranes: 2 })).toBe(
    "12,345 nuclei · 2 membranes",
  );
});

test("each kind's count is its own phrase, so a narrow line breaks only between kinds", () => {
  expect(maskCountParts(B90.kinds, { cells: 616, nuclei: 731, cytoplasm: 616 })).toEqual([
    "731 nuclei",
    "616 cells",
    "616 cytoplasm",
  ]);
  expect(maskCountParts(["cells"], { cells: 1 })).toEqual(["1 cell"]);
  expect(maskCountParts(["cells"], {})).toEqual([]);
});

test("only models every source's plate covers are offered, the default first", () => {
  const plate14610 = [STOCK, B90];
  const plate14611 = [B90, model(STOCK.model_tag, { available: false })];
  expect(sharedMaskModels([plate14610]).map((item) => item.model_tag)).toEqual([
    B90.model_tag,
    STOCK.model_tag,
  ]);
  expect(sharedMaskModels([plate14610, plate14611]).map((item) => item.model_tag)).toEqual([
    B90.model_tag,
  ]);
  expect(sharedMaskModels([plate14610, null])).toEqual([]);
  expect(sharedMaskModels([[], plate14610])).toEqual([]);
  expect(sharedMaskModels([])).toEqual([]);
});

test("the requested model is used only when offered", () => {
  const offered = sharedMaskModels([[STOCK, B90]]);
  expect(chooseMaskModel(offered, STOCK.model_tag)).toBe(STOCK);
  expect(chooseMaskModel(offered, "unknown")).toBe(B90);
  expect(chooseMaskModel(offered, null)).toBe(B90);
  expect(chooseMaskModel([STOCK], null)).toBe(STOCK);
  expect(chooseMaskModel([], STOCK.model_tag)).toBeNull();
});

test("outline URLs are same-origin and encode every path part", () => {
  expect(maskOutlineUrl("d1", B90.model_tag, { well: "A01", site: 1 }, "nuclei", 2)).toBe(
    "/backend/api/datasets/d1/masks/idr0080_cpsam_B90/A01/1/nuclei.png?color=FFD400&width=2",
  );
  expect(maskOutlineUrl("a/b", "m 1", { well: "P24", site: 9 }, "cells", 4)).toBe(
    "/backend/api/datasets/a%2Fb/masks/m%201/P24/9/cells.png?color=FFFFFF&width=4",
  );
});

test("a time-resolved source's outline URL names its timepoint", () => {
  const field = { well: "A01", site: 1 };
  expect(maskOutlineUrl("d1", "masks_raw", field, "cells", 2, 0)).toBe(
    "/backend/api/datasets/d1/masks/masks_raw/A01/1/cells.png?color=FFFFFF&width=2&t=0",
  );
  expect(maskOutlineUrl("d1", "masks_raw", field, "cells", 2, 47)).toBe(
    "/backend/api/datasets/d1/masks/masks_raw/A01/1/cells.png?color=FFFFFF&width=2&t=47",
  );
  expect(maskOutlineUrl("d1", "masks_raw", field, "cells", 2, null)).toBe(
    maskOutlineUrl("d1", "masks_raw", field, "cells", 2),
  );
});

test("the outline preset reads and writes outlines= and model=", () => {
  expect(parseOutlinePreset(new URLSearchParams("outlines=nuclei,cells&model=m"))).toEqual({
    kinds: ["nuclei", "cells"],
    model: "m",
  });
  expect(
    parseOutlinePreset(new URLSearchParams("outlines=%20Nuclei,,nuclei,cells%20&model=")),
  ).toEqual({ kinds: ["nuclei", "cells"], model: null });
  expect(parseOutlinePreset(new URLSearchParams("dataset=d1"))).toEqual({
    kinds: [],
    model: null,
  });

  const query = new URLSearchParams("dataset=d1&path=A01_s1.zarr&debug=viewer");
  expect(
    viewerQueryHref(writeOutlinePreset(query, { kinds: ["nuclei", "cells"], model: "m" })),
  ).toBe("/view?dataset=d1&path=A01_s1.zarr&debug=viewer&outlines=nuclei%2Ccells&model=m");
  expect(viewerQueryHref(writeOutlinePreset(query, { kinds: ["cells"], model: null }))).toBe(
    "/view?dataset=d1&path=A01_s1.zarr&debug=viewer&outlines=cells",
  );
  expect(viewerQueryHref(writeOutlinePreset(query, { kinds: [], model: "m" }))).toBe(
    "/view?dataset=d1&path=A01_s1.zarr&debug=viewer",
  );
});
