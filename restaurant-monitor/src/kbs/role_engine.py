"""
role_engine.py
--------------
Point-based evidence engine (ExpertA) for classifying tracked persons
as Customer or Worker in a restaurant surveillance pipeline.

Architecture
------------
1. ObservationFact   — declared by pipeline each cycle (input)
2. ScoreAccumulator  — one per global_id, holds running worker/customer scores
3. EvidenceApplied   — sentinel fact preventing duplicate rule firing
4. DecayApplied      — sentinel fact preventing duplicate decay
5. RoleDecision      — output fact with final role + confidence

Scoring philosophy
------------------
Each rule awards positive points to ONE score channel based on
(action, zone_type, duration, appearances). Old evidence decays
each cycle via a decay rule. Confirmation fires when a score
crosses a threshold; reversal fires when the OTHER score overtakes.

All zone_type values come from the pipeline's ZONE_TYPE_MAP:
  table_area → "table"
  service_path/hallway/corridor/open_area/mixed_area/entrance/
  bathroom_entrance/prayer_room_entrance → "walk"
  staff_area/buffet/cashier → "staff"
"""


from src.kbs import _compat  # noqa: F401  (must precede `import experta` — see _compat.py)
from experta import (
    KnowledgeEngine, Fact, Field, Rule, DefFacts,
    AS, MATCH, NOT, TEST, OR,
)


# ── Facts ──────────────────────────────────────────────────────────────────

class ObservationFact(Fact):
    """Input fact declared by the pipeline each inference cycle.

    Fields
    ------
    track_id         : int   – DeepSORT camera-local track id
    global_id        : str   – Re-ID identity ("global_7") or fallback "local_<tid>"
    action           : str   – LSTM label: sitting|standing|walking|serving
    zone_type        : str   – normalized zone: table|walk|staff|unknown
    pose_frame_count : int   – consecutive pose-frames in current action+zone
    previous_duration_frames : int – duration of the prior action+zone before reset
    appearance_count : int   – Re-ID cross-camera appearances
    previous_role    : str   – role from last cycle (unknown|customer|worker)
    """
    track_id         = Field(int,    mandatory=True)
    global_id        = Field(str,    mandatory=True)
    action           = Field(str,    default="unknown")
    zone_type        = Field(str,    default="unknown")
    zone_id          = Field(str,    default="unknown")
    pose_frame_count = Field(int,    default=0)
    previous_duration_frames = Field(int, default=0)
    appearance_count = Field(int,    default=0)
    previous_role    = Field(str,    default="unknown")


class ScoreAccumulator(Fact):
    """Running evidence totals for one identity.

    Fields
    ------
    global_id      : str   – links to ObservationFact.global_id
    worker_score   : float – accumulated worker evidence
    customer_score : float – accumulated customer evidence
    confirmed_role : str   – "unknown"|"customer"|"worker"
    cycle_count    : int   – how many cycles this accumulator has lived
    zone_history   : tuple – last 5 (zone_type, action, duration_frames) states
    """
    global_id      = Field(str,   mandatory=True)
    worker_score   = Field(float, default=0.0)
    customer_score = Field(float, default=0.0)
    confirmed_role = Field(str,   default="unknown")
    cycle_count    = Field(int,   default=0)
    confirmed_at_cycle = Field(int,   default=-1)
    locked         = Field(bool,  default=False)
    visited_tables = Field(tuple, default=())
    visited_staff  = Field(bool,  default=False)
    zone_history   = Field(tuple, default=())


class EvidenceApplied(Fact):
    """Sentinel: evidence rule <rule_tag> already fired for <global_id>."""
    global_id = Field(str, mandatory=True)
    rule_tag  = Field(str, mandatory=True)


class ZoneHistoryApplied(Fact):
    """Sentinel: zone history already updated for <global_id> this cycle."""
    global_id = Field(str, mandatory=True)


class DecayApplied(Fact):
    """Sentinel: decay already applied for <global_id> this cycle."""
    global_id = Field(str, mandatory=True)


class MergeRequest(Fact):
    """Signals that two global_ids should be merged (Re-ID link)."""
    old_global_id = Field(str, mandatory=True)
    new_global_id = Field(str, mandatory=True)


class RoleDecision(Fact):
    """Output fact with the engine's verdict.

    Fields
    ------
    track_id       : int
    global_id      : str
    role           : str   – "unknown"|"customer"|"worker"
    active_state   : str   – "active"|"inactive"
    confidence     : float – 0.0 to 1.0
    worker_score   : float
    customer_score : float
    """
    track_id       = Field(int,   mandatory=True)
    global_id      = Field(str,   mandatory=True)
    role           = Field(str,   default="unknown")
    active_state   = Field(str,   default="active")
    confidence     = Field(float, default=0.0)
    worker_score   = Field(float, default=0.0)
    customer_score = Field(float, default=0.0)


# ── Constants ──────────────────────────────────────────────────────────────

# Thresholds & decay
CONFIRM_THRESHOLD   = 12.0   # min score to confirm a role
CONFIRM_MARGIN      = 8.0    # must lead the other score by this much
REVERSAL_MARGIN     = 12.0   # contrary score must exceed by this to reverse
DECAY_RATE          = 0.997  # 90-second half-life at 3fps

# Evidence weights  (rule_tag → points)
W_SIT_TABLE           = 6.0   # sitting + table  → very strong customer
W_SIT_TABLE_LONG      = 4.0   # bonus: sitting + table > 10 frames
W_STAFF_ZONE          = 5.5   # any action + staff zone → very strong worker
W_STAFF_ZONE_LONG     = 3.5   # bonus: staff zone > 8 frames
W_SERVING             = 3.0   # serving action anywhere
W_SERVING_SUSTAINED   = 4.0   # serving > 6 frames
W_WALK_SERVICE        = 1.0   # walking + walk zone (weak worker signal)
W_STAND_TABLE         = 1.5   # standing + table → mild customer
W_SIT_NON_TABLE       = 2.0   # sitting outside table → mild worker
W_REAPPEAR            = 2.5   # cross-camera reappearance > 2
W_REAPPEAR_HIGH       = 3.0   # cross-camera reappearance > 4
W_ENTRANCE_WALK       = 0.0   # entrance + walking → neutral (no points)
W_STAND_WALK_ZONE     = 0.5   # standing in walk zone → very weak worker

# Behavioral weights (macro-routing)
W_MULTI_TABLE         = 2.0   # Applied per frame if len(visited_tables) >= 2
W_STAFF_TO_TABLE      = 2.0   # Applied per frame if visited_staff=True AND currently at table
W_STAFF_WALK_TABLE_PATTERN = 5.0   # staff -> walk -> table transition pattern
W_RECENT_SIT_CARRYOVER    = 2.5   # table sitting carryover into standing/walking
W_TABLE_SIT_HISTORY_STRONG = 12.0  # strong customer evidence from recent table-sitting history

ZONE_HISTORY_LIMIT = 5
RECENT_SIT_CARRYOVER_FRAMES = 90  # 30s at the 3fps pose sampling rate


# ── Engine ─────────────────────────────────────────────────────────────────

class RoleEngine(KnowledgeEngine):
    """
    Point-based evidence engine for restaurant role classification.

    Usage
    -----
        engine = RoleEngine()
        engine.reset()
        # Optionally re-declare ScoreAccumulators from previous cycle
        engine.declare(ScoreAccumulator(global_id=gid, worker_score=ws, ...))
        # Declare one ObservationFact per tracked person
        engine.declare(ObservationFact(track_id=..., global_id=..., ...))
        engine.run()
        # Read RoleDecision facts from engine.facts
    """

    @staticmethod
    def _trim_zone_history(history: tuple) -> tuple:
        return tuple(history[-ZONE_HISTORY_LIMIT:])

    # ── Bootstrap: ensure every observation has an accumulator ──────────

    @Rule(
        AS.obs << ObservationFact(global_id=MATCH.gid),
        NOT(ScoreAccumulator(global_id=MATCH.gid)),
        salience=100,
    )
    def create_accumulator(self, obs, gid):
        """Create a fresh ScoreAccumulator when a new global_id appears."""
        self.declare(ScoreAccumulator(
            global_id=gid,
            worker_score=0.0,
            customer_score=0.0,
            confirmed_role=obs["previous_role"],
            cycle_count=0,
            confirmed_at_cycle=-1,
            locked=False,
            visited_tables=(),
            visited_staff=False,
            zone_history=(),
        ))

    # ── Decay: fade old evidence ───────────────────────────────────────

    @Rule(
        AS.acc << ScoreAccumulator(
            global_id=MATCH.gid,
            worker_score=MATCH.ws,
            customer_score=MATCH.cs,
            cycle_count=MATCH.cc,
        ),
        NOT(DecayApplied(global_id=MATCH.gid)),
        salience=90,
    )
    def apply_decay(self, acc, gid, ws, cs, cc):
        """Decay both scores and bump cycle_count."""
        self.modify(acc,
            worker_score=round(ws * DECAY_RATE, 3),
            customer_score=round(cs * DECAY_RATE, 3),
            cycle_count=cc + 1,
        )
        self.declare(DecayApplied(global_id=gid))

    # ── Evidence Rules (salience=50) ───────────────────────────────────
    # Each rule: match observation + accumulator, guard against duplicate
    # firing with EvidenceApplied sentinel, then modify accumulator.

    # --- BEHAVIORAL HISTORY UPDATES (salience=100) ---

    @Rule(
        ObservationFact(global_id=MATCH.gid, zone_type="table", zone_id=MATCH.zid),
        TEST(lambda zid: zid != "unknown"),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, visited_tables=MATCH.vt),
        TEST(lambda zid, vt: zid not in vt),
        salience=100,
    )
    def update_visited_tables(self, acc, gid, zid, vt):
        """Record unique table visits to identify table-hopping."""
        new_vt = tuple(list(vt) + [zid])
        self.modify(acc, visited_tables=new_vt)

    @Rule(
        ObservationFact(global_id=MATCH.gid, zone_type="staff"),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, visited_staff=False),
        salience=100,
    )
    def update_visited_staff(self, acc, gid):
        """Record that this person has entered a staff-only area."""
        self.modify(acc, visited_staff=True)

    @Rule(
        AS.obs << ObservationFact(
            global_id=MATCH.gid,
            action=MATCH.action,
            zone_type=MATCH.zone_type,
            pose_frame_count=MATCH.pfc,
            previous_duration_frames=MATCH.prev_pfc,
        ),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, zone_history=MATCH.history),
        NOT(ZoneHistoryApplied(global_id=MATCH.gid)),
        salience=99,
    )
    def update_zone_history(self, acc, gid, action, zone_type, pfc, prev_pfc, history):
        """Track the latest contiguous zone/action segment in a bounded history."""
        current_entry = (zone_type, action, pfc)
        current_key = current_entry[:2]

        new_history = list(history)

        if new_history and new_history[-1][:2] == current_key:
            last_zone, last_action, last_duration = new_history[-1]
            new_history[-1] = (last_zone, last_action, max(last_duration, pfc))
        else:
            if new_history and prev_pfc > 0:
                last_zone, last_action, last_duration = new_history[-1]
                new_history[-1] = (last_zone, last_action, max(last_duration, prev_pfc))
            new_history.append(current_entry)

        self.modify(acc, zone_history=self._trim_zone_history(tuple(new_history)))
        self.declare(ZoneHistoryApplied(global_id=gid))

    # --- CUSTOMER evidence ---

    @Rule(
        ObservationFact(global_id=MATCH.gid, action="sitting", zone_type="table"),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, customer_score=MATCH.cs),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="sit_table")),
        salience=50,
    )
    def ev_sit_table(self, acc, gid, cs):
        """Sitting at a table → strong customer signal."""
        self.modify(acc, customer_score=round(cs + W_SIT_TABLE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=sit_table (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="sit_table"))

    @Rule(
        ObservationFact(
            global_id=MATCH.gid, action="sitting",
            zone_type="table", pose_frame_count=MATCH.pfc,
        ),
        TEST(lambda pfc: pfc > 10),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, customer_score=MATCH.cs),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="sit_table_long")),
        salience=49,
    )
    def ev_sit_table_long(self, acc, gid, cs, pfc):
        """Sitting at table for extended time → bonus customer evidence."""
        self.modify(acc, customer_score=round(cs + W_SIT_TABLE_LONG, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=sit_table_long (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="sit_table_long"))

    @Rule(
        ObservationFact(global_id=MATCH.gid, action="standing", zone_type="table"),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, customer_score=MATCH.cs),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="stand_table")),
        salience=50,
    )
    def ev_stand_table(self, acc, gid, cs):
        """Standing near table → mild customer signal."""
        self.modify(acc, customer_score=round(cs + W_STAND_TABLE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=stand_table (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="stand_table"))

    # --- WORKER evidence ---

    @Rule(
        ObservationFact(global_id=MATCH.gid, zone_type="staff"),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="staff_zone")),
        salience=50,
    )
    def ev_staff_zone(self, acc, gid, ws):
        """Present in staff-only zone → strong worker signal."""
        self.modify(acc, worker_score=round(ws + W_STAFF_ZONE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=staff_zone (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="staff_zone"))

    @Rule(
        ObservationFact(
            global_id=MATCH.gid, zone_type="staff",
            pose_frame_count=MATCH.pfc,
        ),
        TEST(lambda pfc: pfc > 8),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="staff_zone_long")),
        salience=49,
    )
    def ev_staff_zone_long(self, acc, gid, ws, pfc):
        """Lingering in staff zone → bonus worker evidence."""
        self.modify(acc, worker_score=round(ws + W_STAFF_ZONE_LONG, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=staff_zone_long (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="staff_zone_long"))

    @Rule(
        ObservationFact(global_id=MATCH.gid, action="serving", pose_frame_count=MATCH.pfc),
        TEST(lambda pfc: pfc >= 4),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="serving")),
        salience=50,
    )
    def ev_serving(self, acc, gid, ws, pfc):
        """Sustained serving action detected -> moderate worker signal. (Filters kids playing)"""
        self.modify(acc, worker_score=round(ws + W_SERVING, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=serving (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="serving"))

    @Rule(
        ObservationFact(
            global_id=MATCH.gid, action="serving",
            pose_frame_count=MATCH.pfc,
        ),
        TEST(lambda pfc: pfc > 6),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="serving_sustained")),
        salience=49,
    )
    def ev_serving_sustained(self, acc, gid, ws, pfc):
        """Sustained serving action → strong worker evidence."""
        self.modify(acc, worker_score=round(ws + W_SERVING_SUSTAINED, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=serving_sustained (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="serving_sustained"))

    # NOTE: walking/standing in a walk zone is NOT a standalone worker signal —
    # customers walk through walk zones too. These rules now only REINFORCE
    # an identity that RoleEngine has already confirmed as "worker" (via
    # staff_zone/serving/reappear evidence). They cannot help an "unknown"
    # or "customer" person become a worker on their own.

    @Rule(
        ObservationFact(global_id=MATCH.gid, action="walking", zone_type="walk"),
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid, worker_score=MATCH.ws, confirmed_role="worker",
        ),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="walk_service")),
        salience=50,
    )
    def ev_walk_service(self, acc, gid, ws):
        """Walking in service/walk zone → weak reinforcement, workers only."""
        self.modify(acc, worker_score=round(ws + W_WALK_SERVICE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=walk_service (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="walk_service"))

    @Rule(
        ObservationFact(global_id=MATCH.gid, action="standing", zone_type="walk"),
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid, worker_score=MATCH.ws, confirmed_role="worker",
        ),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="stand_walk")),
        salience=50,
    )
    def ev_stand_walk_zone(self, acc, gid, ws):
        """Standing in walk zone → very weak reinforcement, workers only."""
        self.modify(acc, worker_score=round(ws + W_STAND_WALK_ZONE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=stand_walk (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="stand_walk"))

    @Rule(
        ObservationFact(
            global_id=MATCH.gid, action="sitting",
            zone_type=MATCH.zt,
        ),
        TEST(lambda zt: zt not in ("table", "unknown")),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="sit_nontable")),
        salience=50,
    )
    def ev_sit_non_table(self, acc, gid, ws, zt):
        """Sitting outside a table zone → mild worker signal (resting worker)."""
        self.modify(acc, worker_score=round(ws + W_SIT_NON_TABLE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=sit_nontable (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="sit_nontable"))

    # --- MACRO-BEHAVIOR evidence ---

    @Rule(
        ObservationFact(global_id=MATCH.gid),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, visited_tables=MATCH.vt, worker_score=MATCH.ws),
        TEST(lambda vt: len(vt) >= 2),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="multi_table")),
        salience=48,
    )
    def ev_multi_table(self, acc, gid, vt, ws):
        """Visited multiple tables -> definitive worker routing behavior."""
        self.modify(acc, worker_score=round(ws + W_MULTI_TABLE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=multi_table (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="multi_table"))

    @Rule(
        ObservationFact(global_id=MATCH.gid, zone_type="table"),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, visited_staff=True, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="staff_to_table")),
        salience=48,
    )
    def ev_staff_to_table(self, acc, gid, ws):
        """Transit from staff zone to table -> definitive worker routing behavior."""
        self.modify(acc, worker_score=round(ws + W_STAFF_TO_TABLE, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=staff_to_table (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="staff_to_table"))

    @staticmethod
    def _has_staff_walk_table_pattern(history: tuple) -> bool:
        filtered = [h[0] for h in history if h[0] != "unknown"]
        return len(filtered) >= 3 and filtered[-3:] == ["staff", "walk", "table"]

    @Rule(
        ObservationFact(global_id=MATCH.gid, zone_type="table"),
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            zone_history=MATCH.history,
            worker_score=MATCH.ws,
        ),
        TEST(lambda history: RoleEngine._has_staff_walk_table_pattern(history)),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="staff_walk_table_pattern")),
        salience=47,
    )
    def ev_staff_walk_table_pattern(self, acc, gid, ws, history):
        """staff -> walk -> table route strongly suggests a worker."""
        self.modify(acc, worker_score=round(ws + W_STAFF_WALK_TABLE_PATTERN, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=staff_walk_table_pattern (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="staff_walk_table_pattern"))

    @Rule(
        ObservationFact(global_id=MATCH.gid, action=MATCH.action, zone_type=MATCH.zt),
        TEST(lambda action, zt: action in ("standing", "walking") and zt != "table"),
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            zone_history=MATCH.history,
            customer_score=MATCH.cs,
        ),
        TEST(lambda history: any(h[0] == "table" and h[1] == "sitting" and h[2] >= RECENT_SIT_CARRYOVER_FRAMES for h in history[:-1])),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="recent_sit_carryover")),
        salience=47,
    )
    def ev_recent_sit_carryover(self, acc, gid, cs, action, zt, history):
        """A recent long sit at table should carry some customer evidence forward."""
        self.modify(acc, customer_score=round(cs + W_RECENT_SIT_CARRYOVER, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=recent_sit_carryover (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="recent_sit_carryover"))

    @Rule(
        ObservationFact(global_id=MATCH.gid, zone_type="table", action=MATCH.action),
        TEST(lambda action: action != "sitting"),
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            zone_history=MATCH.history,
            customer_score=MATCH.cs,
        ),
        TEST(lambda history: any(h[0] == "table" and h[1] == "sitting" and h[2] >= RECENT_SIT_CARRYOVER_FRAMES for h in history[:-1])),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="table_sit_history_strong")),
        salience=46,
    )
    def ev_table_sit_history_strong(self, acc, gid, cs, action, history):
        """Recent long table sitting in history is a very strong customer signal."""
        self.modify(acc, customer_score=round(cs + W_TABLE_SIT_HISTORY_STRONG, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=table_sit_history_strong (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="table_sit_history_strong"))

    # --- Re-ID reappearance evidence ---

    @Rule(
        ObservationFact(
            global_id=MATCH.gid,
            appearance_count=MATCH.ac,
        ),
        TEST(lambda ac: ac > 2),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="reappear")),
        salience=48,
    )
    def ev_reappear(self, acc, gid, ws, ac):
        """Appeared across cameras > 2 times → worker signal."""
        self.modify(acc, worker_score=round(ws + W_REAPPEAR, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=reappear (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="reappear"))

    @Rule(
        ObservationFact(
            global_id=MATCH.gid,
            appearance_count=MATCH.ac,
        ),
        TEST(lambda ac: ac > 4),
        AS.acc << ScoreAccumulator(locked=False, global_id=MATCH.gid, worker_score=MATCH.ws),
        NOT(EvidenceApplied(global_id=MATCH.gid, rule_tag="reappear_high")),
        salience=47,
    )
    def ev_reappear_high(self, acc, gid, ws, ac):
        """Appeared across cameras > 4 times → stronger worker signal."""
        self.modify(acc, worker_score=round(ws + W_REAPPEAR_HIGH, 3))
        print(f"[RoleEngine] EVIDENCE fired: gid={gid} rule_tag=reappear_high (will not re-fire until reset/decay episode)")
        self.declare(EvidenceApplied(global_id=gid, rule_tag="reappear_high"))

    # ── Confirmation: first-time role lock ─────────────────────────────

    @Rule(
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            customer_score=MATCH.cs,
            worker_score=MATCH.ws,
            confirmed_role="unknown",
            cycle_count=MATCH.cc,
        ),
        TEST(lambda cs, ws: cs >= CONFIRM_THRESHOLD and (cs - ws) >= CONFIRM_MARGIN),
        salience=30,
    )
    def confirm_customer(self, acc, gid, cs, ws, cc):
        """Customer score crossed threshold with sufficient margin."""
        print(f"[RoleEngine] CONFIRM gid={gid} → customer (cs={cs}, ws={ws})")
        self.modify(acc, confirmed_role="customer", confirmed_at_cycle=cc)

    @Rule(
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            worker_score=MATCH.ws,
            customer_score=MATCH.cs,
            confirmed_role="unknown",
            cycle_count=MATCH.cc,
        ),
        TEST(lambda ws, cs: ws >= CONFIRM_THRESHOLD and (ws - cs) >= CONFIRM_MARGIN),
        salience=30,
    )
    def confirm_worker(self, acc, gid, ws, cs, cc):
        """Worker score crossed threshold with sufficient margin."""
        print(f"[RoleEngine] CONFIRM gid={gid} → worker (ws={ws}, cs={cs})")
        self.modify(acc, confirmed_role="worker", confirmed_at_cycle=cc)

    # ── Reversal: override a sticky confirmation ───────────────────────

    @Rule(
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            confirmed_role="customer",
            worker_score=MATCH.ws,
            customer_score=MATCH.cs,
            cycle_count=MATCH.cc,
        ),
        TEST(lambda ws, cs: (ws - cs) >= REVERSAL_MARGIN),
        salience=25,
    )
    def reverse_to_worker(self, acc, gid, ws, cs, cc):
        """Strong worker evidence overturns a customer confirmation."""
        print(f"[RoleEngine] REVERSAL gid={gid} customer → worker (ws={ws}, cs={cs})")
        self.modify(acc, confirmed_role="worker", confirmed_at_cycle=cc)

    @Rule(
        AS.acc << ScoreAccumulator(locked=False,
            global_id=MATCH.gid,
            confirmed_role="worker",
            customer_score=MATCH.cs,
            worker_score=MATCH.ws,
            cycle_count=MATCH.cc,
        ),
        TEST(lambda cs, ws: (cs - ws) >= REVERSAL_MARGIN),
        salience=25,
    )
    def reverse_to_customer(self, acc, gid, cs, ws, cc):
        """Strong customer evidence overturns a worker confirmation."""
        print(f"[RoleEngine] REVERSAL gid={gid} worker → customer (cs={cs}, ws={ws})")
        self.modify(acc, confirmed_role="customer", confirmed_at_cycle=cc)

    # ── Lock Role: permanently freeze a role after 900 cycles ──────────

    @Rule(
        AS.acc << ScoreAccumulator(
            global_id=MATCH.gid,
            confirmed_role=MATCH.role,
            locked=False,
            confirmed_at_cycle=MATCH.cac,
            cycle_count=MATCH.cc,
            worker_score=MATCH.ws,
            customer_score=MATCH.cs,
        ),
        TEST(lambda role, cac, cc, ws, cs: (
            role != "unknown" and
            cac != -1 and
            (cc - cac) >= 900 and
            (max(ws, cs) / (ws + cs) if (ws + cs) > 0 else 0) >= 0.85
        )),
        salience=20,
    )
    def lock_role(self, acc, gid, role, cac, cc, ws, cs):
        """Permanently lock the confirmed role after 900 cycles with high confidence."""
        confidence = max(ws, cs) / (ws + cs) if (ws + cs) > 0 else 0
        print(f"[RoleEngine] LOCK gid={gid} as {role} (duration_cycles={cc-cac}, confidence={confidence:.2f})")
        self.modify(acc, locked=True)

    # ── Re-ID Merge: combine accumulators when track changes ───────────

    @Rule(
        AS.merge << MergeRequest(
            old_global_id=MATCH.old_gid,
            new_global_id=MATCH.new_gid,
        ),
        AS.old_acc << ScoreAccumulator(
            global_id=MATCH.old_gid,
            worker_score=MATCH.old_ws,
            customer_score=MATCH.old_cs,
            confirmed_at_cycle=MATCH.old_cac,
            locked=MATCH.old_locked,
        ),
        AS.new_acc << ScoreAccumulator(
            global_id=MATCH.new_gid,
            worker_score=MATCH.new_ws,
            customer_score=MATCH.new_cs,
            confirmed_at_cycle=MATCH.new_cac,
            locked=MATCH.new_locked,
        ),
        salience=95,
    )
    def merge_accumulators(self, merge, old_acc, new_acc,
                           old_gid, new_gid,
                           old_ws, old_cs, new_ws, new_cs,
                           old_cac, new_cac, old_locked, new_locked):
        """Merge old accumulator into the new one and remove old."""
        old_history = old_acc["zone_history"]
        new_history = new_acc["zone_history"]
        merged_locked = old_locked or new_locked
        merged_cac = max(old_cac, new_cac)
        self.modify(new_acc,
            worker_score=round(new_ws + old_ws, 3),
            customer_score=round(new_cs + old_cs, 3),
            zone_history=self._trim_zone_history(old_history + new_history),
            locked=merged_locked,
            confirmed_at_cycle=merged_cac,
        )
        self.retract(old_acc)
        self.retract(merge)

    # ── Emit RoleDecision output ───────────────────────────────────────

    @Rule(
        AS.obs << ObservationFact(
            track_id=MATCH.tid,
            global_id=MATCH.gid,
        ),
        AS.acc << ScoreAccumulator(
            global_id=MATCH.gid,
            worker_score=MATCH.ws,
            customer_score=MATCH.cs,
            confirmed_role=MATCH.role,
        ),
        NOT(RoleDecision(global_id=MATCH.gid)),
        salience=10,
    )
    def emit_decision(self, obs, acc, tid, gid, ws, cs, role):
        """Produce the final RoleDecision fact for pipeline consumption."""
        total = ws + cs
        confidence = 0.0
        if total > 0:
            dominant = max(ws, cs)
            confidence = round(dominant / total, 3)

        active_state = "active"
        if role == "worker" and obs["action"] == "sitting":
            active_state = "inactive"

        self.declare(RoleDecision(
            track_id=tid,
            global_id=gid,
            role=role,
            active_state=active_state,
            confidence=confidence,
            worker_score=ws,
            customer_score=cs,
        ))

    # ── Public helper: extract decisions after run() ───────────────────

    def get_decisions(self) -> dict:
        """Return {track_id: {role, active_state, confidence, worker_score, customer_score}}."""
        results = {}
        for fact in self.facts.values():
            if isinstance(fact, RoleDecision):
                results[fact["track_id"]] = {
                    "global_id":      fact["global_id"],
                    "role":           fact["role"],
                    "active_state":   fact["active_state"],
                    "confidence":     fact["confidence"],
                    "worker_score":   fact["worker_score"],
                    "customer_score": fact["customer_score"],
                }
        return results

    def get_accumulators(self) -> dict:
        """Return {global_id: {worker_score, customer_score, confirmed_role, cycle_count}}.
        
        Call after run() to persist accumulators across cycles.
        """
        results = {}
        for fact in self.facts.values():
            if isinstance(fact, ScoreAccumulator):
                results[fact["global_id"]] = {
                    "worker_score":   fact["worker_score"],
                    "customer_score": fact["customer_score"],
                    "confirmed_role": fact["confirmed_role"],
                    "cycle_count":    fact["cycle_count"],
                    "confirmed_at_cycle": fact["confirmed_at_cycle"],
                    "locked":         fact["locked"],
                    "visited_tables": fact["visited_tables"],
                    "visited_staff":  fact["visited_staff"],
                    "zone_history":   fact["zone_history"],
                }
        return results