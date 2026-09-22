# SPDX-License-Identifier: Apache-2.0
"""Compare active optimizer pruning with the preserved loop; no game execution."""
import ast
from pathlib import Path
import unittest

import selected_sell_core as core

ROOT=Path(core.__file__).resolve().parent


class SelectedPruningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The source tree imports a root copy; the archive maps the attributed
        # latest source to that same location. Require exact bytes, not a name.
        assert (ROOT/'selected_sell_core.py').read_bytes()==(
            ROOT/'reference/titan-current/latest/selected_sell_core.py').read_bytes()
        control=ROOT/'reference/titan-current/vendor/sell/scheduler.py'
        node=next(n for n in ast.parse(control.read_text()).body
                  if isinstance(n,ast.FunctionDef) and n.name=='optimize_lot')
        namespace={'MarketPath':core.MarketPath}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(control),'exec'),namespace)
        cls.old=staticmethod(namespace['optimize_lot'])

    def compare(self,args,capacity,*,same_calls=False):
        old_calls=[];new_calls=[]
        def check(plan,calls):
            calls.append(plan)
            return capacity(plan)
        expected=self.old(**args,capacity_ok=lambda p:check(p,old_calls))
        actual=core.optimize_lot(**args,capacity_ok=lambda p:check(p,new_calls))
        self.assertEqual(actual,expected)
        if same_calls:self.assertEqual(new_calls,old_calls)
        else:self.assertLessEqual(len(new_calls),len(old_calls))
        return expected,len(old_calls),len(new_calls)

    def args(self,**changes):
        args=dict(item='STRAWBERRY',quantity=8,inventory=10000,params=None,
                  shops=['SMOOTHIE_SHOP','YARN_STORE','FARMERS_MARKET'],config={},now=241,dates=[241,242,245],
                  reference=((241,8),),rival_quantity=8,minimum_now=0)
        args.update(changes)
        return args

    def test_feasible_economic_and_capacity_cases_keep_exact_result(self):
        for item in ('WOOL','MILK','STRAWBERRY','MELON','CARROT','TOMATO','EGG'):
            for stock in (9990,10000,10200):
                for capacity in (lambda p:True,lambda p:dict(p).get(241,0)>=4):
                    with self.subTest(item=item,inventory=stock,capacity=capacity):
                        self.compare(self.args(item=item,inventory=stock),capacity)
        improving,_,_=self.compare(self.args(item='WOOL',shops=['YARN_STORE']*4,
                                             rival_quantity=0),lambda p:True)
        self.assertGreater(improving[1]['worst_relative_gain'],0)

    def test_floor_ties_skip_expensive_capacity_calls(self):
        result,before,after=self.compare(self.args(inventory=20000),lambda p:True)
        self.assertEqual(result[0],((241,8),))
        self.assertEqual(after,1)
        self.assertGreater(before,after)

    def test_forced_feasibility_and_unavailable_capacity_keep_callback_order(self):
        for args,capacity in (
                (self.args(),lambda p:p!=((241,8),)),
                (self.args(reference=((241,0),(245,8)),minimum_now=3),lambda p:True),
                (self.args(),lambda p:False)):
            with self.subTest(args=args):self.compare(args,capacity,same_calls=True)

    def test_terminal_and_carry_boundaries_preserve_ties(self):
        for now,dates,last in ((717,[717,718],718),(241,[241],718),(0,[0,1,7],718)):
            with self.subTest(now=now):
                self.compare(self.args(now=now,dates=dates,last=last,
                                       reference=((now,8),)),lambda p:True)


if __name__=='__main__':unittest.main()
