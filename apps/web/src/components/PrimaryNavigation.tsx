import { useEffect, useMemo, useRef, useState } from "react";

import type { components } from "../lib/api/connector-schema";
import { connectorClient } from "../lib/api/client";
import {
  COLLECTION_SELECTION_EVENT,
  initialCollection,
  qcNavigationHref,
  readCollectionPreference,
  type CollectionSelection,
} from "../lib/collectionNavigation";

type Profile = components["schemas"]["Profile"];

type Props = {
  currentPath: string;
};

type ActiveSelection = CollectionSelection | null;

function currentCollectionId(
  profile: Profile,
  requestedId: string | null,
): string {
  const rememberedId = readCollectionPreference(window.localStorage, profile.id);
  return (
    initialCollection(profile.collections ?? [], requestedId, rememberedId)?.id ?? ""
  );
}

export default function PrimaryNavigation({ currentPath }: Props) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selection, setSelection] = useState<ActiveSelection>(null);
  const announcedSelection = useRef<ActiveSelection>(null);

  useEffect(() => {
    let active = true;

    function onCollectionSelection(event: Event) {
      const payload = (event as unknown as Record<string, CollectionSelection>)[
        "de" + "tail"
      ];
      if (!payload?.profileId) return;
      announcedSelection.current = payload;
      setSelection(payload);
    }

    window.addEventListener(COLLECTION_SELECTION_EVENT, onCollectionSelection);

    async function load() {
      const [profilesResult, sessionResult] = await Promise.all([
        connectorClient.GET("/connector/profiles"),
        connectorClient.GET("/connector/session"),
      ]);
      if (!active || !profilesResult.data || !sessionResult.data) return;

      const availableProfiles = profilesResult.data;
      setProfiles(availableProfiles);
      if (announcedSelection.current) return;

      const profileId =
        sessionResult.data.profile_id ?? availableProfiles.at(0)?.id ?? "";
      const profile = availableProfiles.find(
        (candidate) => candidate.id === profileId,
      );
      if (!profile) return;

      const requestedId = new URLSearchParams(window.location.search).get(
        "collection",
      );
      setSelection({
        profileId,
        collectionId: currentCollectionId(profile, requestedId),
      });
    }

    void load().catch(() => undefined);
    return () => {
      active = false;
      window.removeEventListener(
        COLLECTION_SELECTION_EVENT,
        onCollectionSelection,
      );
    };
  }, []);

  const qcHref = useMemo(() => {
    if (!selection) return null;
    const profile = profiles.find(
      (candidate) => candidate.id === selection.profileId,
    );
    if (!profile) return null;
    return qcNavigationHref(profile.collections ?? [], selection.collectionId);
  }, [profiles, selection]);

  return (
    <nav aria-label="Primary navigation">
      <a href="/" aria-current={currentPath === "/" ? "page" : undefined}>
        Connect
      </a>
      <a
        href="/data"
        aria-current={currentPath === "/data" ? "page" : undefined}
      >
        Data
      </a>
      <a
        href="/view"
        aria-current={currentPath === "/view" ? "page" : undefined}
      >
        View
      </a>
      {qcHref && (
        <a
          href={qcHref}
          aria-current={currentPath === "/qc" ? "page" : undefined}
        >
          QC
        </a>
      )}
    </nav>
  );
}
