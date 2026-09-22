"""What the cap overlay will put in the shed, and when. For GPT / T08 schedulers.

The held failure at seed 9780119 was not lost throughput. Both arms sold 1,552
units over the whole game; the composition realised 112 less cash for exactly the
same units, 143 of it in 28 strawberries across two days. The cap overlay never
touches a crop, and with `one_way=True` its cargo stays CARRIED until the
end-of-day automatic deposit -- so it cannot move a same-day sale. What it moves is
the NEXT day's shed: extra animal product lands at the day boundary, the shed the
scheduler wakes up to is different, and its product ordering changes.

A downstream scheduler cannot see that coming from the observation, because the
units are still on a worker. This publishes it:

    facts = overlay.pending_arrivals(observation)
    # [{"product": "MILK", "units_total": 4, "units_incremental": 2,
    #   "arrival_step": 671, "arrival_kind": "eod_auto",
    #   "no_forced_sale_date": True, "competes_for_capacity": 4,
    #   "shed_room_now": 37, "incremental_confidence": "uncertain"}]

Field meanings, so nobody has to guess:

  units_total            what actually arrives and competes for shed capacity
  units_incremental      how much of it is genuinely NEW -- output the cap would
                         have destroyed. The rest is stock the incumbent route
                         collects anyway, arriving earlier.
  arrival_step           the step the units reach the shed
  arrival_kind           `eod_auto` (the automatic deposit, which is where a
                         one-way errand lands) or `worker_deposit`
  no_forced_sale_date    always True for cap-rescued units: nothing expires them,
                         so a scheduler must not liquidate them at the first
                         opportunity. Selling them early is a CHOICE with a price,
                         which is what cost 143 on seed 9780119.
  incremental_confidence `certain` when the incumbent tape has no HARVEST left this
                         day and so cannot collect the animal before it overflows;
                         `uncertain` when it might.

The measurement behind `units_incremental`: on seed 9780119 the overlay fired and
added ZERO net sale units over the game, because `at_risk` asked whether an animal
overflows at the next refresh without asking whether the route harvests it first.
"""

import arlene_plan
import native_motifs as NM
import run_cards

MOVES = {"NORTH": (0, -1), "SOUTH": (0, 1), "EAST": (1, 0), "WEST": (-1, 0)}


def tape_has_harvest_left_today(agent, step, tpd=24):
    """Any HARVEST at all left in the tape today.

    Kept only as the coarse fallback. It is far too weak to gate on: on seed
    9810001 it marked all 226 opportunities uncertain and the overlay never fired,
    because SOME worker almost always has a HARVEST left somewhere.
    """
    route = agent.R[agent.cur]
    end = min(len(route), (step // tpd + 1) * tpd)
    for t in range(step, end):
        entry = route[t] or {}
        units = [entry.get("farmer") or ["PASS"]] + list(entry.get("hands") or [])
        for op in units:
            if op and op[0] == "HARVEST":
                return True
    return False


def route_will_harvest(agent, obs, step, tile, seat=None, tpd=24):
    """Will the incumbent route itself harvest THIS tile before tonight's refresh?

    Exact rather than a proxy. A worker can only HARVEST the tile it stands on, the
    tape gives each unit's op at each step, and its moves are deterministic, so the
    baseline trajectory to the day boundary can simply be projected: walk each
    worker forward from where it stands now, applying only the tape's own moves,
    and look for a HARVEST while it is on the target tile.

    This is the baseline trajectory on purpose. "Would the route have collected
    it?" is a question about the route, not about the displaced worker.
    """
    if seat is None:
        seat = int(obs.get("player", 0))
    farm = obs["farms"][seat]
    board = len(farm["tiles"]) or 10
    pos = [list(farm["farmer"])] + [list(p) for p in (farm.get("hands") or [])]
    tile = (int(tile[0]), int(tile[1]))
    end = (step // tpd + 1) * tpd
    for t in range(step, end):
        for i in range(len(pos)):
            op = arlene_plan.tape_op(agent, t, i)
            if not op:
                continue
            if op[0] in MOVES:
                dx, dy = MOVES[op[0]]
                nx, ny = int(pos[i][0]) + dx, int(pos[i][1]) + dy
                if 0 <= nx < board and 0 <= ny < board:
                    pos[i] = [nx, ny]
            elif op[0] == "HARVEST" and (int(pos[i][0]), int(pos[i][1])) == tile:
                return True
    return False


def eod_step(step, tpd=24):
    return (step // tpd + 1) * tpd - 1


def facts_for(agent, obs, cfg, seat, targets, chooser, one_way=True):
    """Arrival facts for the errands this overlay would consider right now."""
    step = int(obs["day"]) * 24 + int(obs["hour"])
    cap = int(cfg.get("shedCapacity", 100) or 100)
    shed = obs["private"].get("shed") or {}
    room = max(0, cap - sum(int(v) for v in shed.values()))
    out = []
    for t in targets:
        if t["op"][0] != "HARVEST" or t.get("animal_meta") is None:
            continue
        inc = chooser.at_risk(t, obs) if hasattr(chooser, "at_risk") else 0
        covered = route_will_harvest(agent, obs, step, t["at"], seat)
        if covered:
            inc = 0          # the route collects it itself; nothing is rescued
        out.append({
            "product": t["product"],
            "units_total": int(t.get("units", 0)),
            "units_incremental": int(inc),
            "at": list(t["at"]),
            # a one-way errand never deposits mid-day: the cargo rides to the
            # automatic end-of-day drop
            "arrival_step": eod_step(step) if one_way else step + t["dist"] + 1,
            "arrival_kind": "eod_auto" if one_way else "worker_deposit",
            "no_forced_sale_date": True,
            "competes_for_capacity": int(t.get("units", 0)),
            "shed_room_now": room,
            "route_harvests_it_today": covered,
            "incremental_confidence": "certain",
        })
    return out


class RouteAwareCapChooser(arlene_plan.CapChooser):
    """CapChooser that asks whether the units were ever really at risk.

    `CapChooser.at_risk` asks only whether the animal overflows at tonight's
    refresh. It does not ask whether the incumbent route harvests that animal
    first, and on seed 9780119 that gap let the overlay fire on units the route
    was going to collect anyway: it added no sale units over the whole game and
    still moved arrival times enough to cost 112.

    This requires the overflow to be one the route cannot prevent -- no HARVEST
    left in the tape today -- unless the caller lowers the bar explicitly.
    """
    label = "hand cap-overflow harvest, route-aware"
    authored = "hand"

    def __init__(self, K, agent=None):
        super().__init__(K)
        self.agent = agent
        self.skipped_uncertain = 0     # errands the route would have covered

    def at_risk(self, t, obs):
        """Units genuinely rescued: overflow the route does not itself prevent."""
        lost = super().at_risk(t, obs)
        if not lost or self.agent is None:
            return lost
        step = int(obs["day"]) * 24 + int(obs["hour"])
        if route_will_harvest(self.agent, obs, step, t["at"],
                              int(obs.get("player", 0))):
            self.skipped_uncertain += 1
            return 0
        return lost

    def choose(self, card, window, obs=None):
        best, best_lost = None, 0
        for t in card["targets"]:
            if t["dist"] + 1 > window:
                continue
            lost = self.at_risk(t, obs) if obs is not None else 0
            if lost > best_lost:
                best, best_lost = t, lost
        return best
