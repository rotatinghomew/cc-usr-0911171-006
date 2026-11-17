from __future__ import annotations
import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

UNREACHABLE_TRAVEL_MINUTES = 999


@dataclass(frozen=True)
class Activity:
    id: str
    kind: str
    start: dt.datetime
    end: dt.datetime
    location: str
    mandatory: bool = False


class ItineraryPlanner:
    """Single scheduling evaluation path.

    ``plan_with_evidence`` produces the full decision record (conflict blockers,
    prerequisite/capacity evidence, directional travel legs, unreachable routes
    and mandatory-activity decisions). ``plan`` is a stable projection of the
    same record, so callers never get two schedules computed by two algorithms.
    """

    def activity(self, row):
        start = dt.datetime.fromisoformat(str(row['start']))
        end = dt.datetime.fromisoformat(str(row['end']))
        if end <= start:
            raise ValueError('invalid_interval')
        return Activity(str(row['id']), str(row.get('kind', 'event')), start, end,
                        str(row.get('location', 'unknown')), bool(row.get('mandatory', False)))

    def travel(self, left, right, matrix):
        return int(matrix.get(left, {}).get(right, UNREACHABLE_TRAVEL_MINUTES))

    def is_reachable(self, left, right, matrix):
        return right in matrix.get(left, {})

    def conflict(self, left, right, matrix, rest_minutes):
        return right.start < left.end + dt.timedelta(
            minutes=self.travel(left.location, right.location, matrix) + rest_minutes)

    def prerequisites(self, row, completed):
        return sorted(set(row.get('prerequisites', ())) - set(completed))

    def feasibility(self, row, chosen, context):
        """Return the activity plus every piece of evidence behind the decision."""
        activity = self.activity(row)
        matrix = context.get('travel_matrix', {})
        rest_minutes = int(context.get('minimum_rest_minutes', 0))
        missing = self.prerequisites(row, context.get('completed_courses', ()))
        reasons = []
        evidence = {
            'prerequisite_missing': list(missing),
            'schedule_conflicts': [],
            'capacity': None,
        }
        if missing:
            reasons.append('prerequisite_missing')
        for other in sorted(chosen, key=lambda item: (item.start, item.id)):
            if not self.conflict(other, activity, matrix, rest_minutes):
                continue
            minutes = self.travel(other.location, activity.location, matrix)
            reachable = self.is_reachable(other.location, activity.location, matrix)
            reasons.append('schedule_conflict')
            evidence['schedule_conflicts'].append({
                'blocking_activity_id': other.id,
                'location_from': other.location,
                'location_to': activity.location,
                'travel_minutes': minutes,
                'required_gap_minutes': minutes + rest_minutes,
                'unreachable': not reachable,
            })
            if not reachable:
                reasons.append('route_unreachable')
        capacity = row.get('capacity')
        if capacity is not None:
            booked = int(row.get('booked', 0))
            evidence['capacity'] = {'booked': booked, 'capacity': int(capacity)}
            if booked >= int(capacity):
                reasons.append('capacity_exhausted')
        # Reasons stay de-duplicated and in a fixed order for stable outputs.
        ordered = []
        for reason in reasons:
            if reason not in ordered:
                ordered.append(reason)
        return activity, tuple(ordered), evidence

    # Backwards-compatible wrapper used by earlier callers.
    def feasible(self, row, chosen, context):
        activity, reasons, _ = self.feasibility(row, chosen, context)
        return activity, tuple(r for r in reasons if r != 'route_unreachable')

    def _activity_record(self, activity, row):
        return {
            'id': activity.id,
            'kind': activity.kind,
            'start': activity.start.isoformat(),
            'end': activity.end.isoformat(),
            'location': activity.location,
            'mandatory': activity.mandatory,
            'priority': int(row.get('priority', 0)),
        }

    def plan_with_evidence(self, rows, context):
        """Evaluate every activity once and record the full decision basis."""
        scored = sorted(
            rows,
            key=lambda row: (not bool(row.get('mandatory')), -int(row.get('priority', 0)),
                             str(row.get('start')), str(row.get('id'))))
        matrix = context.get('travel_matrix', {})
        chosen: list[Activity] = []
        chosen_rows: dict[str, Mapping[str, Any]] = {}
        rejected = []
        decision_basis = []
        for row in scored:
            activity, reasons, evidence = self.feasibility(row, chosen, context)
            record = self._activity_record(activity, row)
            if reasons:
                rejected.append({**record, 'reasons': list(reasons), 'evidence': evidence})
            else:
                chosen.append(activity)
                chosen_rows[activity.id] = row
            if activity.mandatory:
                decision_basis.append({
                    'kind': 'mandatory_activity',
                    'activity_id': activity.id,
                    'decision': 'rejected' if reasons else 'chosen',
                    'reasons': list(reasons),
                })
            for conflict in evidence['schedule_conflicts']:
                if conflict['unreachable']:
                    decision_basis.append({
                        'kind': 'route_unreachable',
                        'context': 'blocked_attempt',
                        'activity_id': activity.id,
                        'blocking_activity_id': conflict['blocking_activity_id'],
                        'from_location': conflict['location_from'],
                        'to_location': conflict['location_to'],
                        'travel_minutes': conflict['travel_minutes'],
                    })
        ordered = sorted(chosen, key=lambda item: (item.start, item.id))
        travel_legs = []
        for previous, current in zip(ordered, ordered[1:]):
            minutes = self.travel(previous.location, current.location, matrix)
            reachable = self.is_reachable(previous.location, current.location, matrix)
            travel_legs.append({
                'from_activity_id': previous.id,
                'to_activity_id': current.id,
                'from_location': previous.location,
                'to_location': current.location,
                'travel_minutes': minutes,
                'unreachable': not reachable,
            })
            if not reachable:
                decision_basis.append({
                    'kind': 'route_unreachable',
                    'context': 'chosen_leg',
                    'from_activity_id': previous.id,
                    'to_activity_id': current.id,
                    'from_location': previous.location,
                    'to_location': current.location,
                    'travel_minutes': minutes,
                })
        return {
            'chosen': [self._activity_record(activity, chosen_rows[activity.id]) for activity in ordered],
            'chosen_ids': [activity.id for activity in ordered],
            'rejected': rejected,
            'travel_minutes': sum(leg['travel_minutes'] for leg in travel_legs),
            'travel_legs': travel_legs,
            'decision_basis': self._dedupe_basis(decision_basis),
        }

    def _dedupe_basis(self, entries):
        seen = set()
        result = []
        for entry in entries:
            key = json.dumps(entry, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            result.append(entry)
        return sorted(result, key=lambda entry: json.dumps(entry, sort_keys=True, default=str))

    def plan(self, rows, context):
        """Legacy projection of :meth:`plan_with_evidence`."""
        evidence = self.plan_with_evidence(rows, context)
        return {
            'chosen': list(evidence['chosen_ids']),
            'rejected': [
                {'id': row['id'],
                 'reasons': [reason for reason in row['reasons'] if reason != 'route_unreachable']}
                for row in evidence['rejected']
            ],
            'travel_minutes': evidence['travel_minutes'],
        }

    def reschedule_after_cancellation(self, rows, cancelled_id, context):
        remaining = [row for row in rows if str(row['id']) != str(cancelled_id)]
        return self.plan(remaining, context)
