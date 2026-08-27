"""
test_role_engine.py
-------------------
Standalone smoke tests for the RoleEngine.
Run with:  python -m pytest tests/test_role_engine.py -v
   or:     python tests/test_role_engine.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kbs.role_engine import (
    RoleEngine, ObservationFact, ScoreAccumulator,
    MergeRequest, RoleDecision,
)


def run_single_cycle(observations, prior_accumulators=None, merges=None):
    """Helper: run one engine cycle and return (decisions, accumulators)."""
    engine = RoleEngine()
    engine.reset()

    # Re-inject prior accumulators (persistent state across cycles)
    if prior_accumulators:
        for gid, acc in prior_accumulators.items():
            engine.declare(ScoreAccumulator(
                global_id=gid,
                worker_score=acc["worker_score"],
                customer_score=acc["customer_score"],
                confirmed_role=acc["confirmed_role"],
                cycle_count=acc["cycle_count"],
                zone_history=acc.get("zone_history", ()),
            ))

    # Declare merge requests
    if merges:
        for old_gid, new_gid in merges:
            engine.declare(MergeRequest(old_global_id=old_gid, new_global_id=new_gid))

    # Declare observations
    for obs in observations:
        engine.declare(ObservationFact(**obs))

    engine.run()
    return engine.get_decisions(), engine.get_accumulators()


# ── Test 1: Sitting at table → customer ────────────────────────────────────

def test_sitting_at_table_builds_customer_score():
    """Sitting at a table should accumulate customer_score, not worker_score."""
    print("\n[Test 1] Sitting at table → customer score")

    obs = [{
        "track_id": 1,
        "global_id": "local_1",
        "action": "sitting",
        "zone_type": "table",
        "pose_frame_count": 5,
        "appearance_count": 0,
        "previous_role": "unknown",
    }]

    decisions, accumulators = run_single_cycle(obs)
    acc = accumulators["local_1"]

    print(f"  customer_score = {acc['customer_score']}")
    print(f"  worker_score   = {acc['worker_score']}")
    assert acc["customer_score"] > 0, "Customer score should be positive"
    assert acc["worker_score"] == 0, "Worker score should be zero"
    print("  ✅ PASSED")


# ── Test 2: Staff zone → worker ───────────────────────────────────────────

def test_staff_zone_builds_worker_score():
    """Being in a staff zone should accumulate worker_score."""
    print("\n[Test 2] Staff zone → worker score")

    obs = [{
        "track_id": 2,
        "global_id": "local_2",
        "action": "standing",
        "zone_type": "staff",
        "pose_frame_count": 10,
        "appearance_count": 0,
        "previous_role": "unknown",
    }]

    decisions, accumulators = run_single_cycle(obs)
    acc = accumulators["local_2"]

    print(f"  worker_score   = {acc['worker_score']}")
    print(f"  customer_score = {acc['customer_score']}")
    assert acc["worker_score"] > 0, "Worker score should be positive"
    assert acc["customer_score"] == 0, "Customer score should be zero"
    print("  ✅ PASSED")


# ── Test 3: Confirmation after multiple cycles ────────────────────────────

def test_customer_confirmation_over_time():
    """Repeatedly sitting at table across cycles should confirm customer."""
    print("\n[Test 3] Customer confirmation over multiple cycles")

    accumulators = None
    obs_template = {
        "track_id": 3,
        "global_id": "person_A",
        "action": "sitting",
        "zone_type": "table",
        "appearance_count": 0,
        "previous_role": "unknown",
    }

    for cycle in range(6):
        obs = [dict(obs_template, pose_frame_count=cycle * 5)]
        decisions, accumulators = run_single_cycle(
            obs, prior_accumulators=accumulators,
        )
        acc = accumulators["person_A"]
        print(f"  Cycle {cycle}: cs={acc['customer_score']:.1f}  ws={acc['worker_score']:.1f}  role={acc['confirmed_role']}")

    assert acc["confirmed_role"] == "customer", "Should be confirmed customer by now"
    print("  ✅ PASSED")


# ── Test 4: Worker confirmation via serving ───────────────────────────────

def test_worker_confirmation_via_serving():
    """Sustained serving should confirm worker."""
    print("\n[Test 4] Worker confirmation via sustained serving")

    accumulators = None
    obs_template = {
        "track_id": 4,
        "global_id": "person_B",
        "action": "serving",
        "zone_type": "walk",
        "appearance_count": 3,
        "previous_role": "unknown",
    }

    for cycle in range(6):
        obs = [dict(obs_template, pose_frame_count=cycle * 4)]
        decisions, accumulators = run_single_cycle(
            obs, prior_accumulators=accumulators,
        )
        acc = accumulators["person_B"]
        print(f"  Cycle {cycle}: ws={acc['worker_score']:.1f}  cs={acc['customer_score']:.1f}  role={acc['confirmed_role']}")

    assert acc["confirmed_role"] == "worker", "Should be confirmed worker by now"
    print("  ✅ PASSED")


# ── Test 5: Decay fades old evidence ──────────────────────────────────────

def test_decay():
    """Scores should shrink when no new evidence is added."""
    print("\n[Test 5] Decay fades scores")

    # Seed with existing scores
    accumulators = {
        "person_C": {
            "worker_score": 20.0,
            "customer_score": 5.0,
            "confirmed_role": "worker",
            "cycle_count": 10,
        }
    }

    # Observe with neutral action (walking in unknown zone → no rule fires much)
    obs = [{
        "track_id": 5,
        "global_id": "person_C",
        "action": "walking",
        "zone_type": "unknown",
        "pose_frame_count": 0,
        "appearance_count": 0,
        "previous_role": "worker",
    }]

    _, new_acc = run_single_cycle(obs, prior_accumulators=accumulators)
    acc = new_acc["person_C"]

    print(f"  Before: ws=20.0  cs=5.0")
    print(f"  After:  ws={acc['worker_score']:.1f}  cs={acc['customer_score']:.1f}")
    assert acc["worker_score"] < 20.0, "Worker score should have decayed"
    assert acc["customer_score"] < 5.0, "Customer score should have decayed"
    print("  ✅ PASSED")


# ── Test 6: Reversal ──────────────────────────────────────────────────────

def test_reversal():
    """A confirmed customer who enters staff zone repeatedly should reverse to worker."""
    print("\n[Test 6] Reversal from customer to worker")

    # Start with a confirmed customer
    accumulators = {
        "person_D": {
            "worker_score": 2.0,
            "customer_score": 16.0,
            "confirmed_role": "customer",
            "cycle_count": 5,
        }
    }

    obs_template = {
        "track_id": 6,
        "global_id": "person_D",
        "action": "serving",
        "zone_type": "staff",
        "appearance_count": 5,
        "previous_role": "customer",
    }

    for cycle in range(10):
        obs = [dict(obs_template, pose_frame_count=cycle * 5)]
        _, accumulators = run_single_cycle(obs, prior_accumulators=accumulators)
        acc = accumulators["person_D"]
        print(f"  Cycle {cycle}: ws={acc['worker_score']:.1f}  cs={acc['customer_score']:.1f}  role={acc['confirmed_role']}")

    assert acc["confirmed_role"] == "worker", "Should have reversed to worker"
    print("  ✅ PASSED")


# ── Test 7: Re-ID Merge ──────────────────────────────────────────────────

def test_merge():
    """MergeRequest should combine two accumulators into one."""
    print("\n[Test 7] Re-ID merge combines accumulators")

    accumulators = {
        "old_track": {
            "worker_score": 10.0,
            "customer_score": 0.0,
            "confirmed_role": "unknown",
            "cycle_count": 3,
        },
        "new_track": {
            "worker_score": 5.0,
            "customer_score": 0.0,
            "confirmed_role": "unknown",
            "cycle_count": 1,
        },
    }

    obs = [{
        "track_id": 7,
        "global_id": "new_track",
        "action": "walking",
        "zone_type": "walk",
        "pose_frame_count": 0,
        "appearance_count": 0,
        "previous_role": "unknown",
    }]

    merges = [("old_track", "new_track")]

    _, new_acc = run_single_cycle(obs, prior_accumulators=accumulators, merges=merges)

    assert "old_track" not in new_acc, "Old accumulator should be removed"
    merged = new_acc["new_track"]
    print(f"  Merged worker_score = {merged['worker_score']:.1f} (expected ~15 before decay+new evidence)")
    assert merged["worker_score"] > 12.0, "Merged score should combine both"
    print("  ✅ PASSED")


# ── Test 8: Zone history and carryover evidence ─────────────────────────

def test_zone_history_tracks_transitions_and_patterns():
    """Zone history should preserve transitions and drive the new evidence rules."""
    print("\n[Test 8] Zone history tracks transitions and patterns")

    accumulators = None
    obs_cycles = [
        [{
            "track_id": 8,
            "global_id": "person_hist",
            "action": "standing",
            "zone_type": "staff",
            "pose_frame_count": 2,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 0,
        }],
        [{
            "track_id": 8,
            "global_id": "person_hist",
            "action": "walking",
            "zone_type": "walk",
            "pose_frame_count": 1,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 2,
        }],
        [{
            "track_id": 8,
            "global_id": "person_hist",
            "action": "standing",
            "zone_type": "table",
            "pose_frame_count": 1,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 1,
        }],
    ]

    for obs in obs_cycles:
        decisions, accumulators = run_single_cycle(obs, prior_accumulators=accumulators)

    acc = accumulators["person_hist"]
    print(f"  zone_history = {acc['zone_history']}")
    assert len(acc["zone_history"]) >= 3, "History should capture the recent transitions"
    assert acc["zone_history"][-3][0] == "staff"
    assert acc["zone_history"][-2][0] == "walk"
    assert acc["zone_history"][-1][0] == "table"
    assert acc["worker_score"] > 5.0, "Pattern evidence should add worker score"
    print("  ✅ PASSED")


def test_recent_sit_carryover_evidence():
    """A long table sit should carry customer evidence into a standing transition."""
    print("\n[Test 9] Recent sit carryover adds customer evidence")

    accumulators = None
    obs_cycles = [
        [{
            "track_id": 9,
            "global_id": "person_sit",
            "action": "sitting",
            "zone_type": "table",
            "pose_frame_count": 95,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 0,
        }],
        [{
            "track_id": 9,
            "global_id": "person_sit",
            "action": "standing",
            "zone_type": "table",
            "pose_frame_count": 1,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 95,
        }],
    ]

    _, accumulators = run_single_cycle(obs_cycles[0], prior_accumulators=accumulators)
    first_customer_score = accumulators["person_sit"]["customer_score"]

    _, accumulators = run_single_cycle(obs_cycles[1], prior_accumulators=accumulators)
    acc = accumulators["person_sit"]

    print(f"  zone_history = {acc['zone_history']}")
    print(f"  customer_score before/after = {first_customer_score:.1f} -> {acc['customer_score']:.1f}")
    assert len(acc["zone_history"]) >= 2, "History should keep the recent table sit and standing transition"
    assert acc["zone_history"][-2][0] == "table"
    assert acc["zone_history"][-2][1] == "sitting"
    assert acc["zone_history"][-2][2] >= 90
    assert acc["customer_score"] > first_customer_score, "Carryover evidence should increase customer score"
    print("  ✅ PASSED")


def test_table_sit_history_is_very_strong_customer_evidence():
    """Recent long table sitting in history should heavily boost customer evidence."""
    print("\n[Test 10] Table-sit history is very strong customer evidence")

    accumulators = None
    obs_cycles = [
        [{
            "track_id": 10,
            "global_id": "person_table_hist",
            "action": "sitting",
            "zone_type": "table",
            "pose_frame_count": 95,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 0,
        }],
        [{
            "track_id": 10,
            "global_id": "person_table_hist",
            "action": "standing",
            "zone_type": "table",
            "pose_frame_count": 1,
            "appearance_count": 0,
            "previous_role": "unknown",
            "previous_duration_frames": 95,
        }],
    ]

    _, accumulators = run_single_cycle(obs_cycles[0], prior_accumulators=accumulators)
    first_customer_score = accumulators["person_table_hist"]["customer_score"]

    _, accumulators = run_single_cycle(obs_cycles[1], prior_accumulators=accumulators)
    acc = accumulators["person_table_hist"]

    print(f"  customer_score before/after = {first_customer_score:.1f} -> {acc['customer_score']:.1f}")
    assert acc["customer_score"] >= first_customer_score + 10.0, "Table-sit history should add a large customer boost"
    print("  ✅ PASSED")


# ── Test 8: Decision output has all fields ────────────────────────────────

def test_decision_output():
    """RoleDecision should contain track_id, global_id, role, confidence, scores."""
    print("\n[Test 8] Decision output completeness")

    obs = [{
        "track_id": 99,
        "global_id": "person_X",
        "action": "sitting",
        "zone_type": "table",
        "pose_frame_count": 20,
        "appearance_count": 0,
        "previous_role": "unknown",
    }]

    decisions, _ = run_single_cycle(obs)
    d = decisions[99]

    print(f"  Decision: {d}")
    assert "role" in d
    assert "confidence" in d
    assert "worker_score" in d
    assert "customer_score" in d
    assert "global_id" in d
    assert d["confidence"] > 0
    print("  ✅ PASSED")


# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  RoleEngine Smoke Tests")
    print("=" * 60)

    test_sitting_at_table_builds_customer_score()
    test_staff_zone_builds_worker_score()
    test_customer_confirmation_over_time()
    test_worker_confirmation_via_serving()
    test_decay()
    test_reversal()
    test_merge()
    test_decision_output()
    test_zone_history_tracks_transitions_and_patterns()
    test_recent_sit_carryover_evidence()
    test_table_sit_history_is_very_strong_customer_evidence()

    print("\n" + "=" * 60)
    print("  All 11 tests passed ✅")
    print("=" * 60)
