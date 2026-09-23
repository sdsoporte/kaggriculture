"""Centralized Multi-Worker Task Dispatcher for V9 Hybrid.

Assigns tasks without replacement using greedy Manhattan distance:
- Emergency Feeding (animals unfed today)
- Emergency Watering (plants unwatered today)
- Milking / Shearing / Egg collection (animals with yield > 0)
- Ripe crop harvesting
- Weed clearing (DIG)
- Planting on open tiles
"""

BOARD = 10
SHED_TILES = [(4, 4), (5, 4), (4, 5), (5, 5)]
SHED_TILE = (4, 4)

MOVE_OP = {(0, -1): "NORTH", (0, 1): "SOUTH", (1, 0): "EAST", (-1, 0): "WEST"}


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def step_toward(pos, target):
    px, py = pos
    tx, ty = target
    if px != tx:
        return [MOVE_OP[(1, 0)] if tx > px else MOVE_OP[(-1, 0)]]
    if py != ty:
        return [MOVE_OP[(0, 1)] if ty > py else MOVE_OP[(0, -1)]]
    return ["PASS"]


def is_shed_adjacent(pos):
    return tuple(pos) in SHED_TILES


class CentralizedDispatcher:
    def __init__(self):
        pass

    def dispatch(self, positions, inventories, tiles, shed, seeds, day, hour):
        n_workers = len(positions)
        actions = [["PASS"] for _ in range(n_workers)]
        busy = [False] * n_workers

        # --- Rule 1: Workers carrying 2+ harvested items must drop at shed ---
        for i in range(n_workers):
            inv = inventories[i] if i < len(inventories) else {}
            # Ignore wheat held for feeding
            carry = sum(v for k, v in inv.items() if k != "WHEAT")
            if carry >= 2:
                busy[i] = True
                actions[i] = ["DROP"] if is_shed_adjacent(positions[i]) else step_toward(positions[i], SHED_TILE)

        # --- Rule 2: Dedicated Feeders (Workers 0 and 1) ---
        # Feeders handle animal feeding exclusively
        for feeder_id in (0, 1):
            if feeder_id >= n_workers or busy[feeder_id]:
                continue
            pos = positions[feeder_id]
            inv = inventories[feeder_id] if feeder_id < len(inventories) else {}
            wheat = inv.get("WHEAT", 0)

            # If no wheat, go to shed and pickup
            if wheat <= 0:
                busy[feeder_id] = True
                if not is_shed_adjacent(pos):
                    actions[feeder_id] = step_toward(pos, SHED_TILE)
                else:
                    avail = shed.get("WHEAT", 0)
                    if avail > 0:
                        actions[feeder_id] = ["PICKUP", "WHEAT", min(3, avail)]
                    else:
                        actions[feeder_id] = ["PASS"]
                continue

            # Have wheat: find unfed animal
            unfed = []
            for y in range(BOARD):
                for x in range(BOARD):
                    t = tiles[y][x]
                    if isinstance(t, dict) and "animal" in t and not t.get("fed_today", False):
                        unfed.append((x, y))

            if unfed:
                busy[feeder_id] = True
                target = min(unfed, key=lambda p: dist(pos, p))
                actions[feeder_id] = ["FEED"] if pos == target else step_toward(pos, target)

        # --- Rule 3: Collect All Other Tasks for Remaining Workers ---
        # Priority order:
        # P0: Animal products (milk, wool, egg ready)
        # P1: Unwatered crops
        # P2: Weeds
        # P3: Ripe crops
        # P4: Animal care & fertilizer collection
        # P5: Planting on open tiles

        p0_tasks = []
        p1_tasks = []
        p2_tasks = []
        p3_tasks = []
        p4_tasks = []
        p5_tasks = []

        for y in range(BOARD):
            for x in range(BOARD):
                p = (x, y)
                t = tiles[y][x]

                if t is None:
                    p5_tasks.append((p, "PLANT"))
                    continue

                if not isinstance(t, dict):
                    continue

                if "animal" in t:
                    if t.get("yield_units", 0) > 0:
                        p0_tasks.append((p, ["HARVEST"]))
                    elif t.get("fertilizer_available", False):
                        p4_tasks.append((p, ["COLLECT_FERTILIZER"]))
                    elif not t.get("cared_today", False):
                        p4_tasks.append((p, ["CARE"]))

                elif t.get("kind") == "WEED":
                    p2_tasks.append((p, ["DIG"]))

                elif t.get("kind") == "PLANT":
                    crop = t.get("crop", "WHEAT")
                    age = day - t.get("planted_day", day)
                    ongoing = crop in ("TOMATO", "STRAWBERRY")
                    max_day = {"WHEAT": 4, "CARROT": 3, "MELON": 12, "TOMATO": 8, "STRAWBERRY": 10}.get(crop, 4)
                    first_day = {"TOMATO": 8, "STRAWBERRY": 10}.get(crop, 2)
                    ready = (age >= first_day if ongoing else age >= max_day) and t.get("yield_units", 0) > 0

                    if ready:
                        p3_tasks.append((p, ["HARVEST"]))
                    elif not t.get("watered_today", False):
                        p1_tasks.append((p, ["WATER"]))

        # Combine all tasks in strict priority order
        all_task_groups = [p0_tasks, p1_tasks, p2_tasks, p3_tasks, p4_tasks]

        for task_list in all_task_groups:
            # Assign tasks greedily without replacement
            for target_pos, op in task_list:
                free = [i for i, b in enumerate(busy) if not b]
                if not free:
                    break
                best_i = min(free, key=lambda i: dist(positions[i], target_pos))
                busy[best_i] = True
                actions[best_i] = op if positions[best_i] == target_pos else step_toward(positions[best_i], target_pos)

        # Planting on empty tiles if seeds and free workers exist
        free = [i for i, b in enumerate(busy) if not b]
        has_seeds = any(v > 0 for v in seeds.values())
        if free and has_seeds and day <= 25 and p5_tasks:
            seed_choice = "CARROT" if seeds.get("CARROT", 0) > 0 else ("WHEAT" if seeds.get("WHEAT", 0) > 0 else "TOMATO")
            for target_pos, _ in p5_tasks:
                if not free:
                    break
                best_i = min(free, key=lambda i: dist(positions[i], target_pos))
                free.remove(best_i)
                busy[best_i] = True
                actions[best_i] = ["PLANT", seed_choice] if positions[best_i] == target_pos else step_toward(positions[best_i], target_pos)

        return actions
