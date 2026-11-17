import json
import unittest

from opportunity_planner.route_planning.itinerary import ItineraryPlanner
from opportunity_planner.route_planning.change_impact import ChangeImpactAnalyzer, ChangeImpactError
from opportunity_planner.scheduler.engine import ScheduleEngine

UTC = '+00:00'


def row(activity_id, start, end, location, **extra):
    value = {'id': activity_id, 'start': f'2026-11-17T{start}:00{UTC}',
             'end': f'2026-11-17T{end}:00{UTC}', 'location': location}
    value.update(extra)
    return value


class ChangeImpactTests(unittest.TestCase):
    def setUp(self):
        self.planner = ItineraryPlanner()
        self.analyzer = ChangeImpactAnalyzer(self.planner)
        self.matrix = {
            'home': {'school': 20, 'job': 10, 'center': 15},
            'school': {'job': 20, 'center': 30, 'school': 0},
            'job': {'school': 25, 'center': 10, 'job': 0},
            'center': {'job': 10, 'school': 30, 'center': 0},
        }
        self.context = {'travel_matrix': self.matrix, 'minimum_rest_minutes': 0,
                        'completed_courses': ['basic']}

    def baseline_rows(self):
        return [
            row('training', '09:00', '10:00', 'school', kind='training', mandatory=True,
                prerequisites=['basic']),
            row('gig', '11:00', '13:00', 'job', kind='gig', priority=2),
            row('class', '15:00', '16:00', 'school', kind='training', priority=1),
        ]

    # ---------------------------------------------------------- direct conflict
    def test_inserted_interview_excludes_direct_conflict(self):
        changes = [{'op': 'insert', 'activity': row(
            'interview', '12:00', '12:30', 'job', kind='interview', mandatory=True)}]
        report = self.analyzer.analyze(self.baseline_rows(), changes, self.context)

        self.assertEqual(report['baseline_plan']['chosen_ids'], ['training', 'gig', 'class'])
        self.assertEqual(report['revised_plan']['chosen_ids'], ['training', 'interview', 'class'])

        lost = report['change_impact']['lost_opportunities']
        self.assertEqual([item['id'] for item in lost], ['gig'])
        self.assertEqual(lost[0]['impact'], 'direct')
        self.assertIn('schedule_conflict', lost[0]['reasons'])
        self.assertEqual(lost[0]['blocking_activities'], ['interview'])
        conflict = lost[0]['conflicts'][0]
        self.assertEqual(conflict['location_from'], 'job')
        self.assertEqual(conflict['location_to'], 'job')
        self.assertFalse(conflict['unreachable'])

        comparison = report['opportunity_comparison']
        self.assertEqual(comparison['baseline_fulfillable'], 3)
        self.assertEqual(comparison['revised_fulfillable'], 3)
        self.assertEqual(comparison['lost_count'], 1)
        self.assertEqual(comparison['direct_conflict_count'], 1)
        self.assertEqual(comparison['cascade_count'], 0)

    def test_travel_time_comparison_keeps_direction(self):
        changes = [{'op': 'insert', 'activity': row(
            'interview', '12:00', '12:30', 'center', kind='interview', mandatory=True)}]
        report = self.analyzer.analyze(self.baseline_rows(), changes, self.context)
        travel = report['travel_comparison']
        # baseline leg training(school)->gig(job)=20; revised school->center=30, center->school=30
        self.assertEqual(travel['baseline_travel_minutes'], 20 + 25)
        self.assertEqual(travel['revised_travel_minutes'], 30 + 30)
        self.assertEqual(travel['delta_minutes'], 15)
        legs = {(leg['from_location'], leg['to_location']): leg['travel_minutes']
                for leg in travel['revised_legs']}
        self.assertEqual(legs[('school', 'center')], 30)
        self.assertEqual(legs[('center', 'school')], 30)
        for leg in travel['baseline_legs'] + travel['revised_legs']:
            self.assertFalse(leg['unreachable'])

    # --------------------------------------------------------------- cascade
    def test_cascade_impact_tracks_knockon_opportunity(self):
        rows = [
            row('gig', '09:00', '11:00', 'job', kind='gig', priority=3),
            row('tutorial', '11:15', '12:00', 'school', kind='training', priority=2),
            row('workshop', '11:45', '12:30', 'school', kind='training', priority=1),
        ]
        # Baseline: the gig is chosen; the tutorial cannot follow it (job->
        # school needs 25 minutes), so the workshop takes the freed slot.
        baseline = self.planner.plan(rows, self.context)
        self.assertEqual(baseline['chosen'], ['gig', 'workshop'])
        self.assertEqual([item['id'] for item in baseline['rejected']], ['tutorial'])

        changes = [{'op': 'insert', 'activity': row(
            'interview', '09:30', '10:30', 'center', kind='interview', mandatory=True)}]
        report = self.analyzer.analyze(rows, changes, self.context)
        impact = report['change_impact']

        lost = {item['id']: item for item in impact['lost_opportunities']}
        # The gig is directly blocked by the new interview; once it disappears
        # the tutorial becomes feasible and in turn displaces the workshop.
        self.assertEqual(lost['gig']['impact'], 'direct')
        self.assertEqual(lost['gig']['blocking_activities'], ['interview'])
        self.assertEqual(lost['workshop']['impact'], 'cascade')
        self.assertEqual(lost['workshop']['cascade_from'], ['tutorial'])
        self.assertEqual(report['opportunity_comparison']['direct_conflict_count'], 1)
        self.assertEqual(report['opportunity_comparison']['cascade_count'], 1)
        # The tutorial itself was infeasible before and is restored by the chain.
        self.assertEqual([item['id'] for item in impact['restored_opportunities']],
                         ['tutorial'])

    # ------------------------------------------------ mandatory decision basis
    def test_mandatory_activity_decision_is_recorded(self):
        changes = [{'op': 'insert', 'activity': row(
            'interview', '12:00', '12:30', 'job', kind='interview', mandatory=True)}]
        report = self.analyzer.analyze(self.baseline_rows(), changes, self.context)
        mandatory = [entry for entry in report['decision_basis']
                     if entry['kind'] == 'mandatory_activity' and entry['activity_id'] == 'interview']
        self.assertTrue(mandatory)
        self.assertTrue(all(entry['decision'] == 'chosen' for entry in mandatory))
        training = [entry for entry in report['decision_basis']
                    if entry['kind'] == 'mandatory_activity' and entry['activity_id'] == 'training']
        self.assertEqual({entry['plan'] for entry in training}, {'baseline', 'revised'})

    # ------------------------------------------------------------- unreachable
    def test_unreachable_route_keeps_explicit_decision_basis(self):
        # Only the school->job direction is mapped; the return leg job->school
        # is genuinely unreachable rather than merely slow.
        sparse_matrix = {'school': {'job': 20}}
        rows = [row('gig', '11:00', '13:00', 'job', kind='gig', priority=2),
                row('class', '13:10', '14:00', 'school', kind='training')]
        context = {'travel_matrix': sparse_matrix, 'minimum_rest_minutes': 0}
        plan = self.planner.plan_with_evidence(rows, context)
        rejected = {item['id']: item for item in plan['rejected']}
        self.assertIn('schedule_conflict', rejected['class']['reasons'])
        self.assertIn('route_unreachable', rejected['class']['reasons'])
        self.assertTrue(rejected['class']['evidence']['schedule_conflicts'][0]['unreachable'])
        basis = [entry for entry in plan['decision_basis'] if entry['kind'] == 'route_unreachable']
        self.assertTrue(basis)
        self.assertEqual(basis[0]['from_location'], 'job')
        self.assertEqual(basis[0]['to_location'], 'school')
        # Legacy projection keeps the historical reason vocabulary.
        legacy = self.planner.plan(rows, context)
        self.assertNotIn('route_unreachable', legacy['rejected'][0]['reasons'])

    # --------------------------------------------- cancellation recomputation
    def test_cancel_and_recompute_does_not_reuse_old_placeholder(self):
        rows = self.baseline_rows()
        report = self.analyzer.cancellation_impact(rows, 'gig', self.context)
        self.assertEqual(report['cancelled_ids'], ['gig'])
        self.assertNotIn('gig', report['revised_plan']['chosen_ids'])
        # The cancelled id must not survive as a blocking activity anywhere.
        for rejected in report['revised_plan']['rejected']:
            self.assertNotIn('gig', [c['blocking_activity_id']
                                     for c in rejected['evidence']['schedule_conflicts']])
        for entry in report['decision_basis']:
            self.assertNotEqual(entry.get('activity_id'), 'gig')
        cancelled = report['change_impact']['cancelled_activities']
        self.assertEqual(cancelled, [{'id': 'gig', 'outcome': 'chosen'}])

    def test_cancel_restores_opportunity_with_previous_reason(self):
        rows = [
            row('gig', '11:00', '13:00', 'job', kind='gig', priority=3),
            row('class', '13:10', '14:00', 'school', kind='training'),
        ]
        report = self.analyzer.cancellation_impact(rows, 'gig', self.context)
        restored = report['change_impact']['restored_opportunities']
        self.assertEqual([item['id'] for item in restored], ['class'])
        self.assertIn('schedule_conflict', restored[0]['previous_reasons'])
        self.assertEqual(report['opportunity_comparison']['restored_count'], 1)

    def test_recompute_without_cancelled_activity_is_stable(self):
        rows = self.baseline_rows()
        first = self.analyzer.cancellation_impact(rows, 'gig', self.context)
        second = self.analyzer.cancellation_impact([dict(r) for r in rows], 'gig',
                                                   dict(self.context))
        self.assertEqual(json.dumps(first, sort_keys=True, default=str),
                         json.dumps(second, sort_keys=True, default=str))
        # reschedule helper uses the same evaluation path
        self.assertEqual(self.planner.reschedule_after_cancellation(rows, 'gig', self.context),
                         self.planner.plan([r for r in rows if r['id'] != 'gig'], self.context))

    # ------------------------------------------------------------- validation
    def test_insert_duplicate_and_cancel_unknown_are_rejected(self):
        changes = [{'op': 'insert', 'activity': row('gig', '11:00', '12:00', 'job')}]
        with self.assertRaises(ChangeImpactError):
            self.analyzer.analyze(self.baseline_rows(), changes, self.context)
        with self.assertRaises(ChangeImpactError):
            self.analyzer.analyze(self.baseline_rows(),
                                  [{'op': 'cancel', 'id': 'ghost'}], self.context)
        with self.assertRaises(ChangeImpactError):
            self.analyzer.analyze(self.baseline_rows(), [{'op': 'replace', 'id': 'gig'}],
                                  self.context)

    def test_duplicate_activity_ids_are_rejected(self):
        rows = [row('dup', '09:00', '10:00', 'school'),
                row('dup', '11:00', '12:00', 'job')]
        with self.assertRaises(ChangeImpactError):
            self.analyzer.analyze(rows, [], self.context)

    # ------------------------------------------------------------- capacity
    def test_capacity_and_prerequisite_exclusions_keep_evidence(self):
        rows = [
            row('training', '09:00', '10:00', 'school', kind='training', mandatory=True),
            row('full_course', '11:00', '12:00', 'school', kind='training',
                capacity=10, booked=10),
            row('advanced', '13:00', '14:00', 'school', kind='training',
                prerequisites=['missing-course']),
        ]
        report = self.analyzer.analyze(rows, [], self.context)
        rejected = {item['id']: item for item in report['revised_plan']['rejected']}
        self.assertIn('capacity_exhausted', rejected['full_course']['reasons'])
        self.assertEqual(rejected['full_course']['evidence']['capacity'],
                         {'booked': 10, 'capacity': 10})
        self.assertIn('prerequisite_missing', rejected['advanced']['reasons'])
        self.assertEqual(rejected['advanced']['evidence']['prerequisite_missing'],
                         ['missing-course'])
        basis = {entry['activity_id']: entry for entry in report['decision_basis']
                 if entry['kind'] == 'mandatory_activity'}
        self.assertEqual(basis['training']['decision'], 'chosen')
        # With no changes, exclusions persist as still_rejected and nothing is
        # lost or restored.
        impact = report['change_impact']
        self.assertEqual(sorted(item['id'] for item in impact['still_rejected']),
                         ['advanced', 'full_course'])
        self.assertEqual(impact['lost_opportunities'], [])
        self.assertEqual(impact['restored_opportunities'], [])
        self.assertEqual(impact['retained_activities'], ['training'])
        self.assertEqual(report['recomputed_without_ids'], [])

    # ------------------------------------------------------------- engine wiring
    def test_engine_exposes_impact_alongside_single_plan(self):
        engine = ScheduleEngine()
        item = {'id': 'plan-change', 'activities': self.baseline_rows(),
                'schedule_changes': [{'op': 'insert', 'activity': row(
                    'interview', '12:00', '12:30', 'job', kind='interview', mandatory=True)}]}
        result = engine.evaluate(item, self.context)
        self.assertIn('change_impact', result.facts)
        # The top-level plan remains the legacy schedule; the impact analysis
        # is an addendum, not a competing schedule.
        self.assertEqual(set(result.facts) - {'change_impact'},
                         {'chosen', 'rejected', 'travel_minutes'})
        impact = result.facts['change_impact']
        self.assertEqual(impact['baseline_plan']['chosen_ids'], ['training', 'gig', 'class'])
        self.assertEqual([item['id'] for item in impact['change_impact']['lost_opportunities']],
                         ['gig'])

    def test_engine_without_changes_keeps_legacy_shape(self):
        engine = ScheduleEngine()
        result = engine.evaluate({'id': 'plain', 'activities': self.baseline_rows()},
                                 self.context)
        self.assertNotIn('change_impact', result.facts)
        self.assertEqual(result.facts['chosen'], ['training', 'gig', 'class'])


if __name__ == '__main__':
    unittest.main()
