"""V10 variant: cow-heavy roster, swapped from step 150 (day 6).

The swap starts at step 150 on purpose. Measured: the tape's early game runs on a
cash knife-edge ($24 left at day 0) and HIRE success depends on money, so a cost
change in the first days shifts the hire pattern and desynchronises the entire
hand choreography - a full-roster overhaul stalls at ~7 animals with only 7 of 14
pastures built. From step 150 cash is plentiful ($610 and rising), so the swap
lands cleanly. This is the same threshold the V9.1 textile pivot uses.

Roster target: the tape's 9 cows stay cows, its late sheep purchases become cows.
"""

from kaggriculture.agents.v10_mix import PASTURE_ANIMALS, make_agent

agent = make_agent("COW", sources=PASTURE_ANIMALS, from_step=150)
