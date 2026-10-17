import assert from 'node:assert/strict';
import test from 'node:test';
import { operatorFocusVerdict } from '../src/workspace/operatorFocus.ts';

const event = {
  id:'event-1', old_snapshot_id:'old-1', new_snapshot_id:'new-1',
  entity_name:'Synthetic policy', detected_at:'2026-10-15T09:00:00Z',
};
const target = { source_external_id:'s', asset_external_id:'registered-target', revision_external_id:'r' };
const decision = (control_state) => ({ control_state, decision: { target } });
const ready = (...states) => ({
  status:'ready', eventId:'event-1',
  resolution: {
    event_id:'event-1', old_snapshot_id:'old-1', new_snapshot_id:'new-1',
    decisions:states.map(decision),
  },
});

test('missing event and loading never infer safety', () => {
  assert.equal(operatorFocusVerdict(null, ready('verified')).kind,'unknown');
  assert.equal(operatorFocusVerdict(event, {status:'loading',eventId:event.id}).kind,'unknown');
  assert.equal(operatorFocusVerdict(event,{status:'not_found', eventId:event.id}).decisionCount,null);
  assert.equal(operatorFocusVerdict(event,{status:'error', eventId:event.id}).kind,'unknown');
});
test('mismatched event ID and snapshot pair cannot produce a control conclusion', () => {
  assert.equal(operatorFocusVerdict(event,{...ready('verified'),eventId:'other'}).kind,'unknown');
  assert.equal(operatorFocusVerdict(event,{...ready('verified'),resolution:{...ready('verified').resolution,new_snapshot_id:'other'}}).kind,'unknown');
});
test('empty decisions do not imply consumers are safe', () => {
  const result=operatorFocusVerdict(event,ready());
  assert.equal(result.kind,'unknown');
  assert.match(result.explanation,/Unregistered consumers/);
});
test('stale evidence outranks verified evidence', () => {
  const result=operatorFocusVerdict(event,ready('verified','stale_dependency_present'));
  assert.equal(result.kind,'attention');
  assert.equal(result.outstandingCount,1);
  assert.match(result.title,/Stale/);
});
test('failed and awaiting evidence are not resolved', () => {
  assert.match(operatorFocusVerdict(event,ready('execution_failed')).title,/failed/);
  assert.match(operatorFocusVerdict(event,ready('awaiting_evidence')).title,/outstanding/);
  assert.match(operatorFocusVerdict(event,ready('execution_succeeded_unverified')).title,/outstanding/);
});
test('narrow verification never claims global safety', () => {
  const result=operatorFocusVerdict(event,ready('verified'));
  assert.equal(result.kind,'limited');
  assert.match(result.explanation,/do not establish global knowledge safety/);
  assert.equal(result.target,'registered-target');
});
test('historical no-action does not assert unregistered consumers were assessed', () => {
  const result=operatorFocusVerdict(event,ready('historical_no_action'));
  assert.equal(result.kind,'limited');
  assert.match(result.explanation,/Unregistered consumers/);
});
