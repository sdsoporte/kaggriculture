"""Baseline V9 Hybrid Agent:
- Phase 1 (Steps 0..265): Deterministic Tape Opening (Melons, Pastures, Cows, Sheep, Wool harvest).
- Phase 2 (Steps 266..719): Decoupled Role-Based Reactive Engine:
  - 2 Dedicated Feeders (Guaranteed Zero Starvation).
  - 3 Dedicated Livestock Tenders (Milk, Wool, Eggs, Fertilizer, Care).
  - 6 Dedicated Crop Farmers (Watering window, ripe harvesting, weed clearing, planting).
  - SE Quadrant Expansion ($4,000) at Day 10.
  - Continuous Market Liquidation & Anti-Starvation Wheat Floor (>= 15).
"""

import sys
from pathlib import Path

# Import base V8 agent
import submissions.main_v8 as v8
from kaggriculture.agents.v9_roles import (
    BOARD,
    SHED_TILE,
    SHED_TILES,
    CentralizedDispatcher,
    is_shed_adjacent,
    step_toward,
)

FINAL_EXECUTABLE_STEP = 718
PRODUCTS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]


class BaselineV9Agent:
    def __init__(self):
        self.v8_agent = v8.Agent()
        self.transition_step = 266
        self.unlocked_se = False
        self.dispatcher = CentralizedDispatcher()

    def act(self, obs):
        s = obs.get("step")
        step = int(s) if s is not None else int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))
        day = int(obs.get("day", step // 24))

        # --- Phase 1: Tape Playback (steps 0..265) ---
        if step < self.transition_step:
            return self.v8_agent.act(obs)

        # --- Phase 2: Decoupled Role Reactive Engine (steps 266..719) ---
        me = int(obs.get("player", 0))
        farm = obs["farms"][me]
        priv = obs["private"]
        tiles = farm["tiles"]
        money = farm.get("money", 0)
        shed = dict(priv.get("shed") or {})
        seeds = dict(priv.get("seeds") or {})
        prices = obs["market"]["prices"]
        quads = farm.get("unlocked_quadrants", [])
        inventories = priv.get("inventories", [])

        positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]
        n_workers = len(positions)

        # --- 1. Market & Land Expansion ---
        market = []
        hour = step % 24

        # Morning Labor Hiring (every day at hour 0 and 1, hire up to 11 hands)
        current_hands_count = len(farm.get("hands", []))
        if day < 29 and current_hands_count < 11:
            hires_needed = min(8, 11 - current_hands_count)
            for _ in range(hires_needed):
                market.append(["HIRE"])

        # Unlock Quadrant 4 (SE) if affordable
        if len(quads) == 3 and money >= 4500 and not self.unlocked_se:
            market.append(["BUY_LAND"])
            self.unlocked_se = True

        # Maintain Wheat Floor (>= 15 in shed)
        wheat_in_shed = shed.get("WHEAT", 0)
        if wheat_in_shed < 15 and money >= 100 and day < 28:
            buy_wheat = min(5, int(money // prices.get("WHEAT", 25)))
            if buy_wheat > 0:
                market.append(["BUY_PRODUCT", "WHEAT", buy_wheat])

        # Seed pipeline for open tiles
        if sum(seeds.values()) < 10 and money >= 500 and day <= 24:
            market.append(["BUY_SEED", "CARROT", 5])
            market.append(["BUY_SEED", "WHEAT", 5])

        # Continuous surplus liquidation (sell high-value products gradually)
        for prod in ["MELON", "WOOL", "MILK", "STRAWBERRY", "TOMATO", "EGG", "CARROT", "FERTILIZER"]:
            qty = shed.get(prod, 0)
            if qty > 0 and prices.get(prod, 0) > 1:
                # Sell in batches of up to 5 to avoid price cratering
                market.append(["SELL", prod, min(qty, 5)])

        # End of Season Terminal Settlement (Day 28-29)
        if day >= 28:
            if wheat_in_shed > 0:
                market.append(["SELL", "WHEAT", wheat_in_shed])

        # Cap market orders to MAX_ORDERS
        market = market[:10]

        # --- 2. Centralized Task Dispatcher (Greedy without replacement) ---
        actions = self.dispatcher.dispatch(
            positions, inventories, tiles, shed, seeds, day, hour
        )

        return {"farmer": actions[0], "hands": actions[1:], "market": market}


_A = None


def agent(obs):
    global _A
    s = obs.get("step")
    step = int(s) if s is not None else int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))
    if _A is None or step == 0:
        _A = BaselineV9Agent()
    return _A.act(obs)
