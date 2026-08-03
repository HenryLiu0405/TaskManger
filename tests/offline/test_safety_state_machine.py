from __future__ import annotations

import unittest

from phi_robot.robot_state import (
    ALLOWED_TRANSITIONS,
    RobotState,
    SafetyStateMachine,
)


class SafetyStateMachineContractTests(unittest.TestCase):
    def test_every_legal_and_illegal_transition_matches_the_frozen_matrix(self) -> None:
        fsm = SafetyStateMachine()
        for source in RobotState:
            for target in RobotState:
                with self.subTest(source=source.value, target=target.value):
                    fsm.force_state(source)
                    expected = target in ALLOWED_TRANSITIONS[source]
                    self.assertEqual(fsm.can_transition(target), expected)
                    self.assertEqual(fsm.transition(target), expected)
                    self.assertEqual(fsm.state, target if expected else source)

    def test_action_preconditions_are_explicit_for_every_state(self) -> None:
        fsm = SafetyStateMachine()
        walk_states = {RobotState.STANDING, RobotState.ARRIVED, RobotState.HOLDING}
        pick_states = {RobotState.ARRIVED}
        place_states = {RobotState.ARRIVED, RobotState.HOLDING}
        for state in RobotState:
            with self.subTest(state=state.value):
                fsm.force_state(state)
                self.assertEqual(fsm.can_walk(), state in walk_states)
                self.assertEqual(fsm.can_pick(), state in pick_states)
                self.assertEqual(fsm.can_place(), state in place_states)

    def test_bypass_is_explicit_and_reversible(self) -> None:
        fsm = SafetyStateMachine()
        fsm.force_state(RobotState.EMERGENCY)
        self.assertFalse(fsm.can_walk())
        fsm.set_bypass(True)
        self.assertTrue(fsm.can_walk())
        self.assertTrue(fsm.can_pick())
        self.assertTrue(fsm.can_place())
        self.assertTrue(fsm.transition(RobotState.HOLDING))
        fsm.set_bypass(False)
        self.assertEqual(fsm.state, RobotState.HOLDING)
        self.assertFalse(fsm.can_pick())
