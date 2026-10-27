import type {
  components,
  operations,
} from "../lib/api/dataserver-schema";

export type MatchMode =
  operations["dataset_matches_api_datasets__dataset_id__matches_get"]["parameters"]["query"]["by"];

export type PerturbationMatch =
  components["schemas"]["DatasetMatchSummary"];

export type PerturbationMatchesProps = {
  sourceWell: string;
  activeMode: MatchMode | null;
  matches: readonly PerturbationMatch[];
  busy: boolean;
  limitReached: boolean;
  addedKeys: ReadonlySet<string>;
  onChoose: (mode: MatchMode) => void;
  onAdd: (match: PerturbationMatch) => void;
};

type MatchGroup = {
  cellLine: string;
  matches: PerturbationMatch[];
};

const modes: readonly { mode: MatchMode; label: string }[] = [
  { mode: "well", label: "Same well on other plates" },
  { mode: "gene", label: "Same gene" },
  { mode: "control", label: "Controls on this plate" },
];

export function perturbationMatchKey(match: PerturbationMatch): string {
  return match.path + "\u0000" + match.well;
}

function groupMatches(matches: readonly PerturbationMatch[]): MatchGroup[] {
  const groups = new Map<string, MatchGroup>();
  for (const match of matches) {
    const existing = groups.get(match.cell_line);
    if (existing) {
      existing.matches.push(match);
    } else {
      groups.set(match.cell_line, {
        cellLine: match.cell_line,
        matches: [match],
      });
    }
  }
  return [...groups.values()];
}

export default function PerturbationMatches({
  sourceWell,
  activeMode,
  matches,
  busy,
  limitReached,
  addedKeys,
  onChoose,
  onAdd,
}: PerturbationMatchesProps) {
  const groups = groupMatches(matches);

  return (
    <section
      className="subpanel perturbation-matches stack"
      aria-label={"Perturbation matches for well " + sourceWell}
      aria-busy={busy}
      data-testid="perturbation-matches"
    >
      <div>
        <p className="eyebrow">Related fields</p>
        <h3>Compare {sourceWell} across the experiment</h3>
      </div>

      <div
        className="perturbation-match-modes"
        role="group"
        aria-label="Choose related fields"
      >
        {modes.map(({ mode, label }) => (
          <button
            type="button"
            key={mode}
            className={activeMode === mode ? "" : "secondary"}
            disabled={busy}
            aria-pressed={activeMode === mode}
            aria-controls={
              activeMode ? "perturbation-match-results" : undefined
            }
            data-testid={"match-mode-" + mode}
            onClick={() => onChoose(mode)}
          >
            {label}
          </button>
        ))}
      </div>

      {activeMode && (
        <div
          id="perturbation-match-results"
          className="perturbation-match-results stack"
          data-testid="perturbation-match-results"
        >
          {busy ? (
            <p className="muted" role="status">
              Working...
            </p>
          ) : matches.length === 0 ? (
            <p className="muted" role="status">
              No matches found.
            </p>
          ) : (
            groups.map((group) => (
              <section
                className="perturbation-match-group"
                key={group.cellLine}
                aria-label={group.cellLine}
                data-cell-line={group.cellLine}
              >
                <h4>{group.cellLine}</h4>
                <ul className="perturbation-match-list">
                  {group.matches.map((match) => {
                    const key = perturbationMatchKey(match);
                    const added = addedKeys.has(key);
                    return (
                      <li
                        className="perturbation-match-row"
                        key={key}
                        data-testid="perturbation-match-row"
                      >
                        <div className="perturbation-match-identity">
                          <strong>{match.plate_label}</strong>
                          <span>Well {match.well}</span>
                          <span>Guide {match.guide}</span>
                          <span>
                            {match.well_type ??
                              (match.is_control
                                ? "EMPTY control"
                                : "Type not specified")}
                          </span>
                          {match.is_control && (
                            <span className="perturbation-control-badge">
                              Control
                            </span>
                          )}
                        </div>
                        <button
                          type="button"
                          className="compact-button"
                          disabled={busy || added || limitReached}
                          aria-label={
                            added
                              ? match.plate_label + " " + match.well + " added"
                              : "Add " +
                                match.plate_label +
                                " " +
                                match.well +
                                " to comparison"
                          }
                          data-testid="add-perturbation-match"
                          onClick={() => onAdd(match)}
                        >
                          {added ? "Added" : "Add to comparison"}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))
          )}

          {limitReached && (
            <p
              className="perturbation-match-limit"
              role="status"
              data-testid="perturbation-match-limit"
            >
              The comparison tray is full; remove a field before adding another.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
