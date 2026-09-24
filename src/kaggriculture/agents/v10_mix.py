"""V10 screening overlays: play the V8 tape with a different roster MIX.

Why a mix swap and not more animals (V10 T3, measured):

  * The tape ends with 0 free structure slots (COOP 3/3, PASTURE 14/14).
  * Only 7.4% of worker turns are idle (529 of 7150).
  * Wheat in the shed stays at 0-47, so the feed budget has no slack.
  * Structures are built by WORKER actions, not market orders.

So the tape cannot host MORE animals without rescheduling the hand routes, and a
cheap version of that would be an unfair test (an animal added late, when cash is
plentiful but only ~10 days remain, measures timing rather than animal value).
What it CAN host is a different MIX at the same slots, turns and hand positions -
which is the conflict V10 exists to settle.

The transform is a pure payload swap on top of the V8 tape:

  * worker PLACE/PICKUP payloads: a source species -> the target species
  * worker BUILD_PASTURE/BUILD_COOP -> the structure the target needs, when the
    target lives on a different structure than the sources
  * market BUY_ANIMAL -> the target species

Nothing is rescheduled, so hand positions stay valid by construction.

Two engine facts make the swap safe:

  * PLACE requires the unit to stand on a tile whose ``kind`` equals
    ``ANIMALS[item]["structure"]`` (kaggriculture.py:377-395). A GOOSE placed on
    a PASTURE does not raise - it falls through to the shed-drop path and
    silently does nothing. That is the silent no-op ``scripts/assert_overlay.py``
    exists to catch, and the reason the structure swap is mandatory.
  * HARVEST is generic and yields ``ANIMALS[tile["animal"]]["product"]``
    (kaggriculture.py:446-472), so the product follows the species and no
    collect action needs rewriting.
"""

from pathlib import Path

from kaggriculture.loading import load_agent

ANIMALS = ("COW", "SHEEP", "GOOSE")
PASTURE_ANIMALS = ("COW", "SHEEP")
STRUCTURE = {"COW": "PASTURE", "SHEEP": "PASTURE", "GOOSE": "COOP"}
BUILDS = ("BUILD_PASTURE", "BUILD_COOP")
_BASELINE = Path(__file__).with_name("baseline_v8.py")


def _swap_worker(
    action: list | tuple | None,
    sources: tuple[str, ...],
    target: str,
    structure: str | None,
) -> list:
    """Rewrite one worker action into a fresh list the engine can execute."""
    if not isinstance(action, (list, tuple)) or not action:
        return list(action) if isinstance(action, (list, tuple)) else []
    verb = action[0]
    if verb in ("PLACE", "PICKUP") and len(action) > 1 and action[1] in sources:
        return [verb, target, *action[2:]]
    if structure is not None and verb in BUILDS:
        return [f"BUILD_{structure}", *action[1:]]
    return list(action)


def _swap_market(
    orders: list | tuple | None, sources: tuple[str, ...], target: str
) -> list:
    swapped = []
    for order in orders or []:
        if (
            isinstance(order, (list, tuple))
            and len(order) > 1
            and order[0] == "BUY_ANIMAL"
            and order[1] in sources
        ):
            swapped.append(["BUY_ANIMAL", target, *list(order[2:])])
        else:
            swapped.append(order)
    return swapped


def _current_step(obs):
    """Turn index, the same way the tape agents derive it."""
    raw = obs.get("step")
    if raw is not None:
        return int(raw)
    return int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))


def make_agent(
    target: str,
    sources: tuple[str, ...],
    structure: str | None = None,
    from_step: int = 0,
):
    """Build an ``agent(obs)`` that plays the V8 tape with a swapped roster.

    ``sources``   animal species in the tape that must become ``target``.
    ``structure`` when set, every BUILD_* becomes this structure (required when
                  the target lives on a different structure than the sources).
    ``from_step`` only swap from this turn onward. The tape's early game runs on
                  a cash knife-edge (day 0 leaves $24), so an early cost change
                  shifts the HIRE success pattern and desynchronises the whole
                  hand choreography. Late swaps avoid that, the same way the
                  V9.1 textile pivot from step 150 does.
    """
    unknown = set(sources) - set(ANIMALS)
    # Validate the target against STRUCTURE, not ANIMALS: STRUCTURE[target] is read
    # below, so a species present in one table and missing from the other must fail
    # as a clear ValueError rather than a KeyError.
    if target not in STRUCTURE or unknown:
        raise ValueError(f"bad target/sources: {target} / {sorted(unknown)}")
    if structure is not None and structure != STRUCTURE[target]:
        raise ValueError(
            f"{target} lives in {STRUCTURE[target]}, not {structure}"
        )

    # Each variant loads its OWN copy of the baseline: that module keeps agent
    # state in a module-level singleton, so sharing one import would let two
    # agents in the same process corrupt each other.
    base_agent = load_agent(str(_BASELINE))

    def agent(obs):
        action = base_agent(obs)
        if not isinstance(action, dict) or _current_step(obs) < from_step:
            return action
        return {
            "farmer": _swap_worker(action.get("farmer"), sources, target, structure),
            "hands": [
                _swap_worker(hand, sources, target, structure)
                for hand in (action.get("hands") or [])
            ],
            "market": _swap_market(action.get("market"), sources, target),
        }

    return agent
