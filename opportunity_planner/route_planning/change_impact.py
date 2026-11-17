from __future__ import annotations
import json
from typing import Any, Mapping, Sequence

from .itinerary import ItineraryPlanner


class ChangeImpactError(ValueError):
    pass


class ChangeImpactAnalyzer:
    """Compare a plan before and after schedule changes.

    The analyzer never invents a second scheduling algorithm: both the baseline
    and the revised itinerary are produced by the same :class:`ItineraryPlanner`
    against the same context (prerequisites, capacity, travel matrix, rest).
    Consequently date order, commuting direction and capacity outcomes are
    identical for identical inputs, and a cancelled activity is removed from
    the candidate set before recomputation, so an old placeholder can never be
    reported as a new conflict.
    """

    def __init__(self, planner: ItineraryPlanner | None = None):
        self.planner = planner or ItineraryPlanner()

    # ------------------------------------------------------------------ input
    def _index(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            identifier = str(row['id'])
            if identifier in result:
                raise ChangeImpactError(f'duplicate activity id {identifier}')
            result[identifier] = dict(row)
        return result

    def apply_changes(self, rows: Sequence[Mapping[str, Any]],
                      changes: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
        """Return (revised rows, inserted ids, cancelled ids)."""
        revised = self._index(rows)
        inserted: list[str] = []
        cancelled: list[str] = []
        for change in changes:
            op = str(change.get('op', '')).strip().lower()
            if op == 'insert':
                activity = change.get('activity')
                if not isinstance(activity, Mapping) or 'id' not in activity:
                    raise ChangeImpactError('insert change requires an activity with an id')
                identifier = str(activity['id'])
                if identifier in revised:
                    raise ChangeImpactError(f'inserted activity id already exists: {identifier}')
                revised[identifier] = dict(activity)
                inserted.append(identifier)
            elif op == 'cancel':
                identifier = change.get('id')
                if identifier is None:
                    raise ChangeImpactError('cancel change requires an id')
                identifier = str(identifier)
                if identifier not in revised:
                    raise ChangeImpactError(f'cannot cancel unknown activity: {identifier}')
                del revised[identifier]
                cancelled.append(identifier)
            else:
                raise ChangeImpactError(f'unsupported change op: {op}')
        return revised, inserted, cancelled

    # --------------------------------------------------------------- comparison
    def _plan_summary(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        return {
            'chosen_ids': list(plan['chosen_ids']),
            'chosen': list(plan['chosen']),
            'rejected_ids': [row['id'] for row in plan['rejected']],
            'rejected': list(plan['rejected']),
            'fulfillable_count': len(plan['chosen_ids']),
            'travel_minutes': plan['travel_minutes'],
            'travel_legs': list(plan['travel_legs']),
        }

    def _rejected_index(self, plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        return {row['id']: row for row in plan['rejected']}

    def _find_blockers(self, rejected_row: Mapping[str, Any], inserted_ids: set[str]) -> tuple[str, list[dict[str, Any]], list[str]]:
        """Split conflict blockers into direct (new/changed activities) and cascade."""
        blockers = rejected_row['evidence']['schedule_conflicts']
        direct = [item for item in blockers if item['blocking_activity_id'] in inserted_ids]
        cascade = [item for item in blockers if item['blocking_activity_id'] not in inserted_ids]
        reasons = list(rejected_row['reasons'])
        return ('direct' if direct else 'cascade'), blockers, reasons

    def analyze(self, baseline_rows: Sequence[Mapping[str, Any]],
                changes: Sequence[Mapping[str, Any]],
                context: Mapping[str, Any]) -> dict[str, Any]:
        baseline_map = self._index(baseline_rows)
        revised_map, inserted, cancelled = self.apply_changes(baseline_rows, changes)
        baseline = self.planner.plan_with_evidence(list(baseline_map.values()), context)
        # The revised candidate set no longer contains cancelled activities:
        # recomputation therefore cannot treat an old placeholder as a blocker.
        revised = self.planner.plan_with_evidence(list(revised_map.values()), context)

        base_chosen = set(baseline['chosen_ids'])
        new_chosen = set(revised['chosen_ids'])
        base_rejected = self._rejected_index(baseline)
        new_rejected = self._rejected_index(revised)
        inserted_set, cancelled_set = set(inserted), set(cancelled)

        lost_opportunities: list[dict[str, Any]] = []
        restored_opportunities: list[dict[str, Any]] = []
        still_rejected: list[dict[str, Any]] = []
        retained: list[str] = []

        for identifier in sorted(base_chosen - new_chosen - cancelled_set):
            row = new_rejected[identifier]
            classification, blockers, reasons = self._find_blockers(row, inserted_set)
            entry = {
                'id': identifier,
                'impact': classification,
                'reasons': reasons,
                'blocking_activities': sorted(item['blocking_activity_id'] for item in blockers),
                'conflicts': list(blockers),
                'cascade_from': sorted(
                    item['blocking_activity_id'] for item in blockers
                    if item['blocking_activity_id'] not in inserted_set),
            }
            if 'prerequisite_missing' in reasons:
                entry['missing_prerequisites'] = list(row['evidence']['prerequisite_missing'])
            if 'capacity_exhausted' in reasons:
                entry['capacity'] = row['evidence']['capacity']
            lost_opportunities.append(entry)

        for identifier in sorted(base_rejected.keys() & new_chosen):
            restored_opportunities.append({
                'id': identifier,
                'previous_reasons': list(base_rejected[identifier]['reasons']),
            })

        for identifier in sorted(base_rejected.keys() & new_rejected.keys()):
            still_rejected.append({
                'id': identifier,
                'reasons': list(new_rejected[identifier]['reasons']),
            })

        retained.extend(identifier for identifier in baseline['chosen_ids'] if identifier in new_chosen)

        inserted_activities = [
            self._cancelled_or_inserted_entry(identifier, revised) for identifier in inserted
        ]
        cancelled_activities = [
            self._cancelled_or_inserted_entry(identifier, baseline) for identifier in cancelled
        ]

        travel_delta = revised['travel_minutes'] - baseline['travel_minutes']
        return {
            'changes': [self._change_record(change) for change in changes],
            'inserted_ids': inserted,
            'cancelled_ids': cancelled,
            'baseline_plan': self._plan_summary(baseline),
            'revised_plan': self._plan_summary(revised),
            'change_impact': {
                'inserted_activities': inserted_activities,
                'cancelled_activities': cancelled_activities,
                'retained_activities': retained,
                'lost_opportunities': lost_opportunities,
                'restored_opportunities': restored_opportunities,
                'still_rejected': still_rejected,
            },
            'opportunity_comparison': {
                'baseline_fulfillable': len(base_chosen),
                'revised_fulfillable': len(new_chosen),
                'lost_count': len(lost_opportunities),
                'direct_conflict_count': sum(1 for item in lost_opportunities if item['impact'] == 'direct'),
                'cascade_count': sum(1 for item in lost_opportunities if item['impact'] == 'cascade'),
                'restored_count': len(restored_opportunities),
            },
            'travel_comparison': {
                'baseline_travel_minutes': baseline['travel_minutes'],
                'revised_travel_minutes': revised['travel_minutes'],
                'delta_minutes': travel_delta,
                'baseline_legs': list(baseline['travel_legs']),
                'revised_legs': list(revised['travel_legs']),
            },
            'decision_basis': self._merge_basis(baseline, revised),
            'recomputed_without_ids': cancelled,
        }

    def _cancelled_or_inserted_entry(self, identifier, plan):
        if identifier in plan['chosen_ids']:
            status = 'chosen'
        elif identifier in self._rejected_index(plan):
            status = 'rejected'
        else:
            status = 'removed'
        return {'id': identifier, 'outcome': status}

    def _change_record(self, change: Mapping[str, Any]) -> dict[str, Any]:
        op = str(change.get('op', '')).strip().lower()
        if op == 'insert':
            return {'op': 'insert', 'id': str(change['activity']['id'])}
        return {'op': 'cancel', 'id': str(change['id'])}

    def _merge_basis(self, baseline, revised) -> list[dict[str, Any]]:
        entries = []
        for plan_name, plan in (('baseline', baseline), ('revised', revised)):
            for entry in plan['decision_basis']:
                merged = {'plan': plan_name, **dict(entry)}
                entries.append(merged)
        return sorted(entries, key=lambda entry: json.dumps(entry, sort_keys=True, default=str))

    def cancellation_impact(self, baseline_rows: Sequence[Mapping[str, Any]],
                            cancelled_id: str,
                            context: Mapping[str, Any]) -> dict[str, Any]:
        """Cancel one activity and recompute on the set that remains."""
        return self.analyze(baseline_rows, [{'op': 'cancel', 'id': cancelled_id}], context)
