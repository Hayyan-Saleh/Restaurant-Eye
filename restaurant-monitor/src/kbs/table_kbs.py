"""
table_kbs.py
------------
Determines the state of each table zone based on customer presence
and serving events.

Table states
------------
free     : no customer in zone
occupied : ≥1 customer present + table not yet served
dirty    : table is free but was served (needs cleaning)

Serving flag lifecycle
----------------------
When a Worker's serving action is detected near an occupied table,
live_pipeline sets TableFact.was_served = True.
This flag is cleared (reset to False) once the table returns to free
AND has been cleaned (external signal, or timer — future work).
"""


from src.kbs import _compat  # noqa: F401  (must precede `import experta` — see _compat.py)

from experta import KnowledgeEngine, Fact, Field, Rule, AS, MATCH, TEST


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

class TableFact(Fact):
    """
    One fact per table zone, per camera.

    Fields
    ------
    zone_id          : str  - unique zone identifier, e.g. "cam1_table_3"
    customer_count   : int  - number of confirmed customers currently in zone
    was_served       : bool - True if a serving event occurred while occupied
    state            : str  - output: "free"|"occupied"|"dirty"
    """
    zone_id        = Field(str, mandatory=True)
    customer_count = Field(int, default=0)
    was_served     = Field(bool, default=False)
    state          = Field(str, default="free")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TableKBS(KnowledgeEngine):
    """
    Classifies table state each inference cycle.
    Call reset() + declare TableFacts + run() per cycle.
    """

    # -- Free ----------------------------------------------------------------
    # No customers in zone → free.
    # was_served is NOT cleared here; dirty rule handles that transition.

    @Rule(
        AS.table << TableFact(
            customer_count=0,
            was_served=False,
            state=MATCH.current_state,
        ),
        TEST(lambda current_state: current_state != "free"),
        salience=10,
    )
    def set_state_free(self, table, current_state):
        self.modify(table, state="free")

    # -- Occupied ------------------------------------------------------------
    # At least 1 customer present and table has not been served yet.

    @Rule(
        AS.table << TableFact(
            customer_count=MATCH.count,
            was_served=False,
            state=MATCH.current_state,
        ),
        TEST(lambda count, current_state: count > 0 and current_state != "occupied"),
        salience=20,
    )
    def set_state_occupied(self, table, count, current_state):
        self.modify(table, state="occupied")

    # -- Occupied + served ---------------------------------------------------
    # Customers still present but table was already served.
    # Stays occupied (not dirty) until customers leave.

    @Rule(
        AS.table << TableFact(
            customer_count=MATCH.count,
            was_served=True,
            state=MATCH.current_state,
        ),
        TEST(lambda count, current_state: count > 0 and current_state != "occupied"),
        salience=20,
    )
    def set_state_occupied_served(self, table, count, current_state):
        self.modify(table, state="occupied")

    # -- Dirty ---------------------------------------------------------------
    # No customers left but table was served → needs cleaning.

    @Rule(
        AS.table << TableFact(
            customer_count=0,
            was_served=True,
            state=MATCH.current_state,
        ),
        TEST(lambda current_state: current_state != "dirty"),
        salience=30,
    )
    def set_state_dirty(self, table, current_state):
        self.modify(table, state="dirty")