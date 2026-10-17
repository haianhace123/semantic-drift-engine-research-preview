import type { TemporalEvent } from "../temporal/types";
import type { TemporalIntegrityState } from "../integrity/useTemporalIntegrity";
import { operatorFocusVerdict } from "./operatorFocus";

interface OperatorFocusProps {
  selectedEvent: TemporalEvent | null;
  state: TemporalIntegrityState;
}

export function OperatorFocus({ selectedEvent, state }: OperatorFocusProps) {
  const focus = operatorFocusVerdict(selectedEvent, state);
  return (
    <section className="operator-focus" aria-labelledby="operator-focus-title" data-control-kind={focus.kind}>
      <div className="operator-focus-identity">
        <span className="operator-focus-kicker">Selected event / recorded control evidence</span>
        <h3 id="operator-focus-title">{selectedEvent?.entity_name ?? "No event selected"}</h3>
        <p>
          {selectedEvent === null
            ? "No event identity is available."
            : `Event ${selectedEvent.id} / detected ${selectedEvent.detected_at ?? "time unavailable"}`}
        </p>
      </div>
      <div className="operator-focus-outcome" role="status">
        <span className="operator-focus-kicker">Scoped downstream interpretation</span>
        <strong>{focus.title}</strong>
        <p>{focus.explanation}</p>
        {focus.decisionCount !== null ? (
          <p className="operator-focus-counts">
            {focus.decisionCount} recorded decisions
            {focus.verifiedCount !== null ? ` / ${focus.verifiedCount} direct-predecessor checks verified` : ""}
            {focus.outstandingCount !== null ? ` / ${focus.outstandingCount} still awaiting or requiring control` : ""}
          </p>
        ) : null}
        {focus.target !== null ? (
          <p className="operator-focus-target"><span>Example registered target</span> <code>{focus.target}</code></p>
        ) : null}
      </div>
    </section>
  );
}
