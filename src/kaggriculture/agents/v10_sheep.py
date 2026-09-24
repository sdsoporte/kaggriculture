"""V10 variant: sheep-heavy roster, swapped from step 150 (day 6).

The step-150 threshold is required, not cosmetic: an early cost change breaks the
HIRE pattern and desynchronises the hand choreography (see v10_mix.make_agent).

Roster target: the tape's 5 sheep stay sheep, its late cow purchases become sheep.
"""

from kaggriculture.agents.v10_mix import PASTURE_ANIMALS, make_agent

agent = make_agent("SHEEP", sources=PASTURE_ANIMALS, from_step=150)
