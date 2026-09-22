# SPDX-License-Identifier: MIT
"""Causal public market-flow inference; no opponent private state or seed input."""
from collections import defaultdict, deque
from dataclasses import dataclass

OPERATING = frozenset(('WHEAT', 'FERTILIZER'))

@dataclass(frozen=True)
class FlowInterval:
    step: int
    product: str
    lower: int
    upper: int
    admitted_lower: int
    admitted_upper: int
    reason: str

    @property
    def exact(self):
        return self.reason == 'identified' and self.lower == self.upper

    def as_dict(self):
        return dict(self.__dict__, exact=self.exact)


def infer_flow(previous, current, own_sales, product, configuration, mechanics, absorption):
    """Identify a previous turn's rival SALES, not current/future hidden actions.

    BUY_PRODUCT only accepts operating products in the pinned engine. For other
    products, market supply is monotone inside the market phase. Floor sales do
    not admit supply, so their quantity and own/rival admission split are censored.
    Town consumption uses the old observation's shops, before any EOD new draw.
    """
    if product in OPERATING:
        return None  # net buys/sells are not separately identified from inventory
    step = int(previous['step'])
    if int(current['step']) != step + 1:
        return None
    if previous['market'].get('params') != current['market'].get('params'):
        return None
    old = int(previous['market']['inventory'][product])
    new = int(current['market']['inventory'][product])
    consumed = absorption(product, step, previous.get('town', {}).get('unlocked_shops', []), configuration)
    admitted = new - old + consumed
    own = max(0, int(own_sales.get(product, 0)))
    if admitted < 0:
        return None
    params = previous['market'].get('params')
    # Before-consumption end inventory, not the possibly recovered next quote.
    at_floor = mechanics.market_price(product, old + admitted, params) <= 1
    if not at_floor:
        rival = admitted - own
        if rival < 0:
            return None
        return FlowInterval(step, product, rival, rival, rival, rival, 'identified')
    lo = max(0, admitted - own)
    hi = admitted
    # Non-buyable stock can enter only before this turn's market and is bounded
    # by shedCapacity. Retain inconsistent evidence rather than narrowing it.
    upper = max(lo, int(configuration.get('shedCapacity', 100)))
    return FlowInterval(step, product, lo, upper, lo, hi, 'floor_censored')


class FlowHistory:
    """Finite-memory, same-hour seasonal predictor from this match only.

    min/max are empirical scenario bounds, NOT confidence intervals. Censored
    observations remain inspectable but do not train a fictitious exact sale.
    """
    def __init__(self, period=24, window=5, minimum=3):
        self.period = max(1, int(period))
        self.window = max(1, int(window))
        self.minimum = max(1, int(minimum))
        self.bins = defaultdict(lambda: deque(maxlen=self.window))
        self.last = {}
        self.records = defaultdict(dict)
        self.identified = self.censored = 0

    def add(self, interval):
        if interval is None:
            return
        product = interval.product
        if interval.step <= self.last.get(product, -1):
            return
        self.last[product] = interval.step
        self.records[product][interval.step] = interval
        cutoff = interval.step-self.period*self.window
        for t in list(self.records[product]):
            if t < cutoff:
                del self.records[product][t]
        if interval.exact:
            self.bins[product, interval.step % self.period].append((interval.step, interval.lower))
            self.identified += 1
        else:
            self.censored += 1

    def predict(self, product, step, *, now):
        # Cutoff is explicit: future observations can never train an earlier call.
        rows = [(t, n) for t, n in self.bins[product, step % self.period]
                if now - self.period * self.window <= t < now]
        values = sorted(n for _, n in rows)
        if len(values) < self.minimum:
            return {'step': step, 'support': len(values), 'lower': 0, 'point': 0,
                    'upper': None, 'ready': False, 'latest_training_step': max((t for t, _ in rows), default=None)}
        return {'step': step, 'support': len(values), 'lower': values[0],
                'point': values[len(values)//2], 'upper': values[-1], 'ready': True,
                'latest_training_step': max(t for t, _ in rows)}

    def window_prediction(self, product, now, end):
        """Same-phase COMPLETE prior windows, retaining within-window batch timing.

        No independent-hour quantiles are composed into a fictitious joint path.
        A missing/censored sample excludes that historical window, not a zero.
        All training timestamps precede now, including a window crossing midnight.
        """
        if end < now or end-now >= self.period:
            raise ValueError('Use an ordered window shorter than one day')
        records = self.records[product]
        windows = []
        for lag in range(1, self.window+1):
            prior = list(range(now-lag*self.period, end-lag*self.period+1))
            if not all(t < now and t in records and records[t].exact for t in prior):
                continue
            stream = tuple((t+lag*self.period, records[t].lower) for t in prior if records[t].lower)
            windows.append({'lag': lag, 'stream': stream, 'total': sum(q for _, q in stream),
                            'training_start': prior[0], 'training_end': prior[-1]})
        totals = sorted(w['total'] for w in windows)
        ready = len(windows) >= self.minimum
        return {'now': now, 'end': end, 'support': len(windows), 'ready': ready,
                'lower': min(totals) if ready else 0,
                'point': totals[len(totals)//2] if ready else 0,
                'upper': max(totals) if ready else None, 'windows': windows,
                'interpretation': 'empirical conditional range, not calibrated probability'}

    def scenarios(self, product, now, end, capacity=100):
        prediction = self.window_prediction(product, now, end)
        if not prediction['ready'] or prediction['point'] == 0:
            return [], prediction
        streams = []
        seen = set()
        for window in prediction['windows']:
            for shift in (-1, 0, 1):
                moved = defaultdict(int)
                for t, q in window['stream']:
                    moved[min(end, max(now, t+shift))] += q
                # A scenario posits a single unknown full shed, not invented future
                # production. Requests exhaust that stock chronologically.
                stock = max(0, int(capacity))
                bounded = []
                for t, q in sorted(moved.items()):
                    q = min(stock, q); stock -= q
                    if q: bounded.append((t, q))
                bounded = tuple(bounded)
                for alignment in ('paired', 'before', 'after'):
                    if (bounded, alignment) not in seen:
                        seen.add((bounded, alignment))
                        streams.append(('day_%s_shift_%s_%s' % (window['lag'], shift, alignment), bounded, alignment))
        return streams, prediction
