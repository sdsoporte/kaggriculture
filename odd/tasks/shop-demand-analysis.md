# Shop-window demand analysis: do winners sell into the shop calendar?

## Question

The town shop system (shops unlock every 3 days with replacement, <= 8
instances, each consuming on its own 4-turn schedule; single-product shops
consume double; town appetite ~2x by day 10, ~4x by day 20) is PUBLIC
information in every observation (`obs.town.unlocked_shops`), yet the incumbent
tape detects exactly one shop (YARN_STORE, one route switch at step 226), the
v11 overlay is shop-blind, and the plan schema does not model shops. Before
making shop-awareness a first-class parameter of the future policy agent,
measure whether the shop calendar actually separates winners from losers in
the corpus.

## Method (one Kaggle Lab run, one daily dataset)

- Reuse the established single-day Lab pipeline (currently 09-23, 608 replays).
  The new analysis folds into the existing Tier-2 single full-parse pass - no
  second read of the replays.
- Per seat per episode:
  - shop timeline from the observed `town.unlocked_shops` each turn (instances,
    unlock day; duplicates allowed by the draw);
  - expected per-product shop demand per day from the mounted
    `town_shop_demand.csv` (engine tables), applying the single-product
    doubling rule from the rule sheet;
  - EXECUTED sold units per product per day via the holdings-drop method
    (income_breakdown's distinction: requested order quantities are NOT fills
    and must never be reported as sales).
- Metrics (all within-game, winner vs loser of the SAME replay):
  1. shop-window share of sales: fraction of each seat's executed units sold on
     days when at least one shop consuming that product is unlocked;
  2. winner-has-more share of that metric;
  3. sold units per shop-consuming-product vs non-shop products;
  4. the tape's own YARN gate observed: wool sales on YARN_STORE days vs not.
- Report bounded (< 20 kB), every row with its n, skipped reasons, and the
  within-game caveat (removes shared shops/market, not policy or selection).
  Descriptive only: no causal claim, one dataset day, repeated teams.

## Deliverables and gates

- [ ] D1 - Worker: `scripts/corpus_shop_windows.py` (stdlib, embeddable,
      CLI-marker cut pattern like corpus_economic_day.py) + synthetic tests +
      builder cell wired into the Tier-2 pass + regenerated notebook. No push.
- [x] D2 - Verdict recorded (Lab v13 COMPLETE, 608/608, 0 skipped; v11/v12 lost to namespace shadowing, both classes now guarded by tests). **VERDICT: NULL/WEAK - shop-awareness stays OUT of the policy agent's first design.** Winner-has-more share of shop-window selling: 345/608 = 56.7% (barely above coin flip; the corpus's strong separators run 60-65%). Median shop-window share winner 95.5% vs loser 95.0%, aggregate median 100.0%: the metric SATURATED by construction - shops accumulate and persist once unlocked, so nearly every sale of every seat falls inside a shop-active window after day ~6-15. Shop vs non-shop median executed units 1,303 vs 294 reflects product mix, not skill. YARN observable: 79.6% of wool (124,024/155,766) sold on YARN-active days, per-seat median 80 vs 28 - but YARN presence (65% of seats) and the tape's own route switch confound it; not field-wide evidence. Refined metrics (first-days-of-unlock spikes, per-instance 4-turn cycle alignment) remain open but are not justified by this null as a first-class parameter. Source: /tmp/kagg-shop-0923-v13/kaggriculture-lab.log.
- A null is a recorded result: if the shop calendar does NOT separate winners,
  shop-awareness stays out of the policy agent's first design.

## Constraints

- One daily dataset per run (current metadata: 09-23). No local daily download.
- Do not modify se_loop/roster_owner/agents or submissions.
- Keep the existing one-day guard and date-aware report labels intact.
