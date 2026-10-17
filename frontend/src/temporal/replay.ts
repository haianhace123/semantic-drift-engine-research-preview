import type {
  TemporalEvent,
  TemporalReplay,
  TemporalReplaySnapshot,
} from "./types";

export function replaySnapshotForEvent(
  replay: TemporalReplay,
  event: TemporalEvent,
): TemporalReplaySnapshot | null {
  return (
    replay.snapshots.find(
      (snapshot) => (
        snapshot.monitored_page_id
        === event.monitored_page_id
      ),
    )
    ?? null
  );
}