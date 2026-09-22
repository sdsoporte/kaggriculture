"""Bounded worker-reallocation continuations over the intact Arlene baseline.

A free slot can only touch the tile beneath the worker, and that lane measured out
at zero. A worker free for a RUN of turns can be walked somewhere that matters and
back. `free_runs.py` measured the room: 107 runs of four or more consecutive free
turns across two games, 104 of them able to reach collectable yield and act on it,
56 able to carry it to the depot as well.

A continuation is one worker, one target tile, one op there, and optionally a
return to the depot to deposit. It is executed ONLY on turns Arlene's own `_noop`
predicate marks free, one turn at a time. The route can reclaim the worker at any
point -- that is not knowable in advance, so a plan is suspended rather than
cancelled, and a plan that leaves goods stranded away from the depot costs the
seat. That cost is left in the paired comparison instead of being gated out.

Target choice is the decision. `GreedyChooser` is a HAND-AUTHORED control that
takes the best value-per-step target; it is not model output and is never reported
as such. `TableChooser` replays choices the model made on development states.
"""

import copy

import native_motifs as NM
import run_cards

MOVES = {"NORTH": (0, -1), "SOUTH": (0, 1), "EAST": (1, 0), "WEST": (-1, 0)}
MAX_PLAN_STEPS = 14


def tape_op(agent, step, unit):
    route = agent.R[agent.cur]
    if step >= len(route):
        return None
    entry = route[step] or {}
    units = [entry.get("farmer") or ["PASS"]] + list(entry.get("hands") or [])
    return list(units[unit]) if unit < len(units) else None


def tape_pass_window(agent, step, unit, horizon=64):
    """How many consecutive turns from `step` the tape plays literal PASS here.

    This is the only position-independent freedom there is. `_noop` answers "does
    the engine ignore this op ON THE TILE THE WORKER IS STANDING ON", so once a
    worker is walked away the answer is about the wrong tile: a route WATER at the
    home tile reads as free from three tiles off, and the excursion silently eats
    it. That is what -105,072 and then -34,576 own cash on one seed were.

    A literal PASS is a no-op wherever the worker stands, so a window of PASSes is
    a window in which nothing of the route's can be skipped by moving.
    """
    n = 0
    for t in range(step, min(len(agent.R[agent.cur]), step + horizon)):
        op = tape_op(agent, t, unit)
        if op is not None and op != ["PASS"]:
            break
        n += 1
    return n


def errand_id(started, worker, target):
    """Stable identity for a committed errand: when it started, whose worker, which
    tile. Two errands cannot share it, and it survives across the turns the errand
    lives for, which is what a consumer reserves against."""
    return f"cap-{int(started)}-w{int(worker)}-t{int(target[0])}{int(target[1])}"


def turns_until_tape_move(agent, step, unit):
    """How long this worker's POSITION is genuinely free, read off the tape.

    A free slot is not a free worker. `_noop` says the engine will ignore the op
    the tape plays this turn; it says nothing about the tape's LATER turns, every
    one of which assumes the worker is where the route left it. Walking a worker
    away and not bringing it back breaks each of those -- measured, at -105,072 own
    cash on one seed with 1,571 such steps.

    A MOVE is never a no-op unless it would leave the board, so the first MOVE the
    tape plays for this unit is a hard end to the window in which its position is
    ours. Everything up to there is a tile op, which the return trip restores.
    """
    route = agent.R[agent.cur]
    limit = min(len(route), step + 1 + 64)
    for t in range(step + 1, limit):
        entry = route[t] or {}
        units = [entry.get("farmer") or ["PASS"]] + list(entry.get("hands") or [])
        if unit < len(units) and units[unit] and units[unit][0] in MOVES:
            return t - step
    return limit - step


def step_toward(pos, dest):
    """One Manhattan step. x first, then y -- deterministic, so a replay matches."""
    x, y = int(pos[0]), int(pos[1])
    tx, ty = int(dest[0]), int(dest[1])
    if x != tx:
        return "EAST" if tx > x else "WEST"
    if y != ty:
        return "SOUTH" if ty > y else "NORTH"
    return None


class GreedyChooser:
    """HAND-AUTHORED engine-derived control, not a model decision.

    Takes the target with the best value-per-step, requiring the round trip to the
    depot to be shorter than the turns left in the day, so goods are not stranded
    by the end-of-day reset. Value is units x the current quoted price.
    """
    label = "hand greedy value-per-step"
    authored = "hand"

    def choose(self, card, window):
        best, best_score = None, 0.0
        for t in card["targets"]:
            trip = 2 * t["dist"] + 1          # out, act, and back to the start tile
            if trip > window:
                continue
            score = t["value_now"] / max(1, trip)
            if score > best_score:
                best, best_score = t, score
        return best


class CapChooser:
    """HAND-AUTHORED control aimed at the one loss no later harvest can recover.

    `_daily_refresh_animals` writes yield_units = min(max_held, yield + base +
    bonus) (827), so a unit produced onto an animal already at its cap is
    discarded at that refresh. Measured on the intact baseline: 42 units on seed
    9600011 (38 EGG, 4 MILK) over 24 refreshes, about $1,732 at final prices, and
    4 units on 9600029. A coverage test that asks "does the route harvest this
    tile eventually" cannot see any of it -- the units are destroyed first.

    This takes the reachable animal that is closest to overflowing, preferring the
    one that would lose the most. HARVEST leaves the animal in place (469-472), so
    nothing is forfeited by taking it early.
    """
    label = "hand cap-overflow harvest"
    authored = "hand"

    def __init__(self, K):
        self.K = K

    def at_risk(self, t, obs):
        if t["op"][0] != "HARVEST" or t.get("animal_meta") is None:
            return 0
        m = t["animal_meta"]
        a = self.K.ANIMALS[m["animal"]]
        since = (int(obs["day"]) + 1) - m["placed_day"] - a["first_yield_day"]
        if since < 0 or since % a["interval"] != 0:
            return 0                      # not a production day, nothing to lose
        add = 1 + (m["pending_care_bonus"] if m["fed_today"] else 0)
        return max(0, (m["yield_units"] + add) - a["max_held"])

    def choose(self, card, window, obs=None):
        best, best_lost = None, 0
        for t in card["targets"]:
            if t["dist"] + 1 > window:
                continue
            lost = self.at_risk(t, obs) if obs is not None else 0
            if lost > best_lost:
                best, best_lost = t, lost
        return best


class TableChooser:
    """Replays the target the model chose on a development state of this shape.

    Keyed by the situation the model actually saw -- what is on the target tile,
    its product, and the distance band -- never by seed or step, so a choice can
    only fire on a state of the same shape.
    """
    authored = "model"

    def __init__(self, table):
        self.rules = table.get("rules", [])
        self.label = table.get("meta", {}).get("label", "model target table")

    @staticmethod
    def key(t):
        return (t["op"][0], t["product"], min(3, (t["dist"] + 2) // 3),
                min(3, (t["depot_dist"] + 2) // 3), t["persists"])

    def choose(self, card, window):
        allowed = {tuple(r["key"]) for r in self.rules if r["take"]}
        best, best_score = None, 0.0
        for t in card["targets"]:
            trip = 2 * t["dist"] + 1
            if trip > window or self.key(t) not in allowed:
                continue
            score = t["value_now"] / max(1, trip)
            if score > best_score:
                best, best_score = t, score
        return best


class PlanOverlay:
    def __init__(self, arlene_mod, chooser, max_steps=MAX_PLAN_STEPS,
                 deposit=True, last_day=29, min_value=0.0, one_way=False,
                 storage_aware=False, min_incremental=0.0):
        self.A = arlene_mod
        self.agent = arlene_mod.Agent()
        self.chooser = chooser
        self.max_steps = max_steps
        self.deposit = deposit
        # One-way mode. The end-of-day refresh drops EVERY carried inventory into
        # the shed regardless of where the worker stands, respawns the farmer and
        # disbands the hands (873-882), so a collection that finishes inside the
        # day needs no depot trip and no return to the tile the route left the
        # worker on. What it does need is for the tape to want nothing from that
        # worker for the REST of the day, since its position stays displaced until
        # the reset. Goods dropped at the final day's close have no turn left to
        # sell in, so the last day is excluded.
        self.one_way = one_way
        # Storage-aware admission. The frozen candidate accepts a plan on the gross
        # output the cap would destroy; that is not the cash it earns. Measured on
        # 9600011, its +2 own cash is +2 EGG (+84) less 2 WHEAT (-82) -- the cargo
        # competed with the incumbent's cargo and with shed capacity. When this is
        # on, a plan must first clear an incremental settlement of the seat's own
        # economy: ordered transfers, the capacity-bounded end-of-day deposit with
        # its discarded overflow, the route's own remaining sale reservations, and
        # dated marginal receipts for both what it adds and what it displaces.
        self.storage_aware = storage_aware
        self.min_incremental = min_incremental
        self.assessments = []
        # Committed errands, keyed by a stable id. This is BOOKKEEPING only -- it
        # never influences action selection, so the frozen candidate's play is
        # byte-for-byte what it was. `self.plans` drives behaviour; this mirrors
        # what has actually been committed so a consumer can reserve against it.
        self.errands = {}
        self._step = 0
        # Composition inputs, empty by default so the frozen behaviour is unchanged.
        self.excluded_workers = set()
        self.blocked_targets = set()
        self.last_day = last_day
        self.min_value = min_value
        self.K = NM.engine()
        self.plans = {}
        self.fills = []
        self.stranded = 0
        self.completed = 0
        self.abandoned = 0

    def act(self, obs, selected_action=None, excluded_workers=(), blocked_targets=()):
        """One turn.

        `selected_action` injects an already-chosen base action instead of calling
        the parent, so a composition never runs two parent controllers.
        `excluded_workers` and `blocked_targets` keep another producer's hands and
        tiles disjoint from this one's. All three default to the frozen behaviour.
        """
        if excluded_workers:
            self.excluded_workers = {int(w) for w in excluded_workers}
        if blocked_targets:
            self.blocked_targets = {tuple(t) for t in blocked_targets}
        self._step = int(obs["day"]) * 24 + int(obs["hour"])
        base = self.agent.act(obs) if selected_action is None else selected_action
        step = int(obs["day"]) * 24 + int(obs["hour"])
        seat = int(obs.get("player", 0))
        farm, priv = obs["farms"][seat], obs["private"]
        tiles = farm["tiles"]
        board = len(tiles) or self.A.BOARD
        seeds = priv.get("seeds") or {}
        invs = priv.get("inventories") or []
        day = int(obs["day"])
        tpd = 24
        turns_left_today = tpd - int(obs["hour"])
        units = [list(base["farmer"])] + [list(h) for h in base["hands"]]
        pos = [farm["farmer"]] + list(farm.get("hands", []))
        # The tape's hands list can be longer than the farm's actual hands. The
        # engine only applies ops for units that exist, so the simulation must see
        # the same set; the surplus entries are carried through untouched.
        tail = units[len(pos):]
        units = units[:len(pos)]
        cfg = {"boardSize": board, "turnsPerDay": tpd,
               "shedCapacity": self.A.SHED_CAP}
        sim_obs = {"farms": obs["farms"], "private": priv, "day": day}
        before = NM.simulate(self.K, sim_obs, cfg, seat, units)
        changed = False

        for i in range(len(units)):
            if i >= len(pos):
                continue
            x, y = int(pos[i][0]), int(pos[i][1])
            if not (0 <= x < board and 0 <= y < board):
                continue
            inv = invs[i] if i < len(invs) else {}
            plan = self.plans.get(i)
            if plan is None:
                if i in self.excluded_workers:
                    continue          # another producer owns this worker
                # Starting a plan: the slot must be free by the baseline's own test,
                # evaluated where the route actually left the worker.
                if not self.A._noop(units[i], tiles[y][x], inv, seeds, x, y, board):
                    continue
            else:
                # CONTINUING one: `_noop` is now being asked about the displaced
                # tile, which is the wrong question and stalls the plan -- 209 of
                # 217 excursions were abandoned mid-flight that way, each leaving a
                # worker away from the route's position. While a plan runs the only
                # position-independent gate is the tape itself, checked below.
                pass
            if plan is None:
                if day >= self.last_day:
                    continue
                card = {"targets": run_cards.reachable_targets(
                    obs, seat, (x, y), self.K, board, obs["market"]["prices"])}
                # The window is the SHORTER of what the tape leaves this worker's
                # position free for and what is left of the day. The day boundary
                # is a hard reset -- _end_of_day respawns the farmer, disbands the
                # hands and drops every carried inventory into the shed -- so goods
                # carried at day close are banked, not lost, and position after it
                # is not ours to protect.
                pw = tape_pass_window(self.agent, step, i)
                if self.one_way:
                    # the tape must be done with this worker until the reset
                    window = turns_left_today if pw >= turns_left_today else 0
                else:
                    window = min(pw, turns_left_today)
                if window <= 0:
                    continue
                try:
                    t = self.chooser.choose(card, window, obs)
                except TypeError:
                    t = self.chooser.choose(card, window)
                if t is not None and tuple(t["at"]) in self.blocked_targets:
                    continue          # another producer owns this tile
                if t is None or t["value_now"] < self.min_value:
                    continue
                if self.storage_aware:
                    import storage_value
                    # credit only the units the output cap would have destroyed;
                    # the whole harvest still competes for shed capacity
                    credited = (self.chooser.at_risk(t, obs)
                                if hasattr(self.chooser, "at_risk") else
                                int(t.get("units", 0)))
                    va = storage_value.assess(self.K, obs, cfg, seat, self.agent,
                                              step, i, t, credited)
                    self.assessments.append(dict(va, step=step, accepted=None))
                    if va["incremental"] <= self.min_incremental:
                        self.assessments[-1]["accepted"] = False
                        continue
                    self.assessments[-1]["accepted"] = True
                plan = {"target": t, "phase": "go", "steps": 0, "home": (x, y),
                        "started": step, "unit": i, "window": window,
                        "errand_id": errand_id(step, i, t["at"])}
                self.plans[i] = plan
                self._commit(plan, t, obs, i, step, seat)
            # Re-read the LIVE tape every turn: a checkpoint switch at 226, 360 or
            # 433 replaces the suffix, so a window computed before it is stale. If
            # the route wants this worker back mid-excursion the plan stops here and
            # the displacement is recorded rather than papered over.
            live = tape_op(self.agent, step, i)
            if live is not None and live != ["PASS"]:
                gone = self.plans.pop(i, None)
                if gone:
                    self._abort(gone.get("errand_id"))
                self.abandoned += 1
                self.stranded += run_cards._dist((x, y), plan["home"])
                continue
            if plan["steps"] >= self.max_steps:
                self._abort(plan.get("errand_id"))
                self.plans.pop(i, None)
                self.abandoned += 1
                continue
            op = self._next_op(plan, (x, y), inv, board)
            if op is None:
                self._abort(plan.get("errand_id"))
                self.plans.pop(i, None)
                continue
            trial = list(units)
            trial[i] = op
            after = NM.simulate(self.K, sim_obs, cfg, seat, trial)
            if after["slots"][i]["kind"] == "none":
                self.plans.pop(i, None)       # the engine would ignore it: drop the plan
                self.abandoned += 1
                continue
            if any((after["slots"][j]["kind"], after["slots"][j]["pos"],
                    after["slots"][j]["carry"], after["slots"][j]["tile"]) !=
                   (before["slots"][j]["kind"], before["slots"][j]["pos"],
                    before["slots"][j]["carry"], before["slots"][j]["tile"])
                   for j in range(len(units)) if j != i):
                self.plans.pop(i, None)
                self.abandoned += 1
                continue
            if plan["phase"] == "act":
                # Capture the lot BEFORE the state advances: `before` is the
                # pre-harvest simulation, and after the reassignment below its
                # slot tile shows yield_units already zeroed.
                tb = before["slots"][i].get("tile") if i < len(before["slots"]) else None
                plan["_lifted"] = (int(tb.get("yield_units", 0))
                                   if isinstance(tb, dict) else 0)
            units[i] = op
            before = after
            plan["steps"] += 1
            changed = True
            self.fills.append({"step": step, "day": day, "hour": int(obs["hour"]),
                               "unit": i, "op": op, "phase": plan["phase"],
                               "target": plan["target"]["at"],
                               "product": plan["target"]["product"],
                               "value_now": plan["target"]["value_now"],
                               "displaced": list(units[i]) if False else
                               (list(base["farmer"]) if i == 0
                                else list(base["hands"][i - 1])),
                               "effect": after["slots"][i]["kind"],
                               "authored": self.chooser.authored})
            if plan["phase"] == "act":
                # The lot is now on the worker. Attribute it from the tile this
                # errand actually lifted -- the animal's yield before the op --
                # not from whatever the worker happens to be holding.
                self._realize(plan.get("errand_id"), plan.get("_lifted", 0))
                if self.one_way:
                    self.plans.pop(i, None)
                    self.completed += 1
                    continue
                plan["phase"] = "back"
            elif plan["phase"] == "back" and tuple(after["slots"][i]["pos"]) == \
                    tuple(plan["home"]):
                self.plans.pop(i, None)
                self.completed += 1
        if not changed:
            return base
        out = dict(base)
        out["farmer"] = units[0]
        out["hands"] = units[1:] + tail
        return out

    def _next_op(self, plan, pos, inv, board):
        """Out to the target, act, then back to the tile the route left it on.

        The goods are carried home rather than walked to the depot: the end-of-day
        drop banks every carried inventory into the shed anyway, so a depot detour
        buys nothing and costs the position guarantee.
        """
        t = plan["target"]
        if plan["phase"] == "go":
            mv = step_toward(pos, t["at"])
            if mv is None:
                plan["phase"] = "act"
            else:
                return [mv]
        if plan["phase"] == "act":
            return list(t["op"])
        if plan["phase"] == "back":
            mv = step_toward(pos, plan["home"])
            return [mv] if mv else None
        return None

    def _commit(self, plan, target, obs, unit, step, seat):
        """Record a COMMITTED errand. Bookkeeping only; play is unaffected."""
        inc = 0
        if hasattr(self.chooser, "at_risk"):
            try:
                inc = int(self.chooser.at_risk(target, obs))
            except Exception:
                inc = 0
        tpd = 24
        self.errands[plan["errand_id"]] = {
            "errand_id": plan["errand_id"],
            "worker_index": int(unit),
            "target": [int(target["at"][0]), int(target["at"][1])],
            "product": target["product"],
            # the WHOLE lot the harvest will lift, which is what competes for
            # capacity; the incremental part is what the output cap would destroy
            "units_total": int(target.get("units", 0)),
            "units_incremental": int(inc),
            "arrival_step": (step // tpd + 1) * tpd - 1,
            "arrival_kind": "eod_auto" if self.one_way else "worker_deposit",
            "no_forced_sale_date": True,
            "status": "pending",
            "observed_carried_units": 0,
            "started": int(step),
            "day": int(obs["day"]),
            "realized_at_step": None,
        }

    def _realize(self, errand_id_, units):
        """The harvest executed: the lot is now carried, attributed from the tile
        this errand actually lifted rather than from whatever the worker holds."""
        e = self.errands.get(errand_id_)
        if e is None or e["status"] != "pending":
            return
        e["observed_carried_units"] = int(units)
        e["units_total"] = int(units)
        e["units_incremental"] = min(e["units_incremental"], int(units))
        e["status"] = "carried"
        e["realized_at_step"] = int(self._step)

    def _abort(self, errand_id_):
        """Abandoned before the harvest: the reservation is released, not retained
        as a ghost lot. A lot already carried stays carried."""
        e = self.errands.get(errand_id_)
        if e is not None and e["status"] == "pending":
            e["status"] = "aborted"

    def producer_snapshot(self, obs, selected_action=None, owner="cloud-model-lab-cap"):
        """COMMITTED errands only, in the arrival-contract producer shape.

        This is not the opportunity list. Every row here corresponds to an errand
        this overlay has actually committed to and is executing, identified by a
        stable `errand_id`, with its own lifecycle and its realisation attributed
        from the tile the harvest lifted. `opportunity_facts` remains separately
        callable for candidates the overlay merely *would* consider; those are not
        reservable and can name the same animal more than once.

        A one-way errand keeps its worker after the harvest: the tape plays PASS
        for the rest of the day by admission, and the cargo rides to the automatic
        end-of-day deposit. The row therefore stays `carried` until that close,
        which is exactly the interval a consumer must not double-book the worker in.
        """
        seat = int(obs.get("player", 0))
        step = int(obs["day"]) * 24 + int(obs["hour"])
        day = int(obs["day"])
        invs = obs["private"].get("inventories") or []
        plans = []
        for e in self.errands.values():
            if e["day"] != day:
                continue          # a previous day's lot has already been deposited
            row = {k: e[k] for k in ("errand_id", "worker_index", "target",
                                     "product", "units_total", "units_incremental",
                                     "arrival_step", "arrival_kind",
                                     "no_forced_sale_date", "status",
                                     "observed_carried_units")}
            if row["status"] == "aborted":
                plans.append({"errand_id": row["errand_id"], "status": "aborted"})
                continue
            # Realisation is reported against the observation the caller passed.
            # A PRE-unit observation for the turn the harvest runs cannot show the
            # lot yet, and reporting it carried there would claim stock the caller's
            # own state does not hold; the lot is still pending capacity in that
            # observation. A POST-unit observation shows it, and then the claim is
            # checked against what that worker actually holds.
            w = row["worker_index"]
            ra = e.get("realized_at_step")
            held_now = (int(invs[w].get(row["product"], 0))
                        if w < len(invs) else 0)
            # On the very turn the harvest runs, the caller may hand us either the
            # pre-unit observation (the lot has not been lifted in it yet) or the
            # post-unit one (it has). Distinguish by what that worker actually
            # holds, rather than assuming which one arrived: a pre-unit view of the
            # realisation turn is `pending` capacity, not an abort.
            if ra == step and held_now < row["observed_carried_units"]:
                ra = step + 1
            if ra is None or ra > step:
                row["status"] = "pending"
                row["observed_carried_units"] = 0
                row["units_total"] = e["units_total"]
                row["units_incremental"] = e["units_incremental"]
            else:
                held = int(invs[w].get(row["product"], 0)) if w < len(invs) else 0
                row["observed_carried_units"] = min(row["observed_carried_units"],
                                                    held)
                row["units_total"] = row["observed_carried_units"]
                row["units_incremental"] = min(row["units_incremental"],
                                               row["units_total"])
                row["status"] = ("carried" if row["observed_carried_units"]
                                 else "aborted")
                if row["status"] == "aborted":
                    plans.append({"errand_id": row["errand_id"],
                                  "status": "aborted"})
                    continue
            plans.append(row)
        return {"owner": owner, "observed_step": step, "plans": plans}

    def opportunity_facts(self, obs):
        """CANDIDATE errands the overlay would consider -- NOT commitments.

        These rows are not reservable: they enumerate what is reachable from each
        worker's current tile, so the same animal can appear more than once and
        nothing here is owned. Use `producer_snapshot` for committed output.
        """
        import arrival_facts
        seat = int(obs.get("player", 0))
        farm, priv = obs["farms"][seat], obs["private"]
        board = len(farm["tiles"]) or self.A.BOARD
        out = []
        pos = [farm["farmer"]] + list(farm.get("hands", []))
        for i, p in enumerate(pos):
            x, y = int(p[0]), int(p[1])
            if not (0 <= x < board and 0 <= y < board):
                continue
            tg = run_cards.reachable_targets(obs, seat, (x, y), self.K, board,
                                             obs["market"]["prices"])
            for f in arrival_facts.facts_for(self.agent, obs,
                                             {"shedCapacity": self.A.SHED_CAP},
                                             seat, tg, self.chooser, self.one_way):
                out.append(dict(f, unit=i))
        return out

    def pending_arrivals(self, obs):
        raise AttributeError(
            "pending_arrivals returned candidate opportunities, which are not "
            "reservable and can repeat an animal. Use producer_snapshot(obs) for "
            "committed errands, or opportunity_facts(obs) for the candidate list.")

    def report(self):
        acc = [a for a in self.assessments if a.get("accepted")]
        rej = [a for a in self.assessments if a.get("accepted") is False]
        return {"fills": len(self.fills), "completed": self.completed,
                "storage_aware": self.storage_aware,
                "plans_priced": len(self.assessments),
                "plans_accepted_on_value": len(acc),
                "plans_rejected_on_value": len(rej),
                "incremental_accepted": round(sum(a["incremental"] for a in acc), 1),
                "incremental_rejected": round(sum(a["incremental"] for a in rej), 1),
                "abandoned": self.abandoned,
                "displacement_left_unrejoined": self.stranded,
                "open_at_end": len(self.plans),
                "chooser": self.chooser.label,
                "skipped_uncertain": getattr(self.chooser, "skipped_uncertain", 0),
                "authored": self.chooser.authored}
