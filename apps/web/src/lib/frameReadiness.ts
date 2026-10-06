export type FrameReadinessSnapshot = {
  active: boolean;
  frameKey: string;
  paths: string[];
};

/**
 * Tracks Viv viewport-load callbacks by frame.
 *
 * A prefetched raster can resolve during React's commit phase, before the
 * parent's frame-start effect runs. Keeping readiness keyed by frame lets the
 * effect adopt that early callback without allowing an older frame to mark the
 * current image as ready.
 */
export class FrameReadinessTracker {
  private active = "";
  private readonly pathsByFrame = new Map<string, Set<string>>();

  get activeFrame(): string {
    return this.active;
  }

  prepare(frameKey: string): void {
    this.pathsByFrame.delete(frameKey);
  }

  activate(frameKey: string): FrameReadinessSnapshot {
    this.active = frameKey;
    return this.snapshot(frameKey);
  }

  record(frameKey: string, path: string): FrameReadinessSnapshot {
    let paths = this.pathsByFrame.get(frameKey);
    if (!paths) {
      paths = new Set<string>();
      this.pathsByFrame.set(frameKey, paths);
    }
    paths.add(path);
    return this.snapshot(frameKey);
  }

  pathsFor(frameKey: string): string[] {
    return Array.from(this.pathsByFrame.get(frameKey) ?? []);
  }

  clear(): void {
    this.active = "";
    this.pathsByFrame.clear();
  }

  private snapshot(frameKey: string): FrameReadinessSnapshot {
    return {
      active: frameKey === this.active,
      frameKey,
      paths: this.pathsFor(frameKey),
    };
  }
}
