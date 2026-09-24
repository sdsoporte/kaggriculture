"""V10 variant: goose-heavy roster, swapped from step 150 (day 6).

Every pasture purchase after step 150 becomes a GOOSE, so the late BUILD_PASTURE
actions become BUILD_COOP: PLACE requires the tile to match the species' structure
(kaggriculture.py:377-395) and a misplaced PLACE silently does nothing.

The step-150 threshold is required, not cosmetic: an early cost change breaks the
HIRE pattern and desynchronises the hand choreography (see v10_mix.make_agent).

Roster target: the early 4 cows / 2 sheep stay, the late purchases become geese.
"""

from kaggriculture.agents.v10_mix import PASTURE_ANIMALS, make_agent

agent = make_agent("GOOSE", sources=PASTURE_ANIMALS, structure="COOP", from_step=150)
