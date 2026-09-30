"""The source baseline obeys accepted hard relations and ignores soft preference."""
import unittest
from lean_exposition.structure.order import OrderProblem, OrderEdge
from census import compare

class CensusTests(unittest.TestCase):
    def problem(self):
        atoms=('a','b','c','d')
        return OrderProblem('s','p',atoms,(OrderEdge('b','c',1),),
            {a:(i,) for i,a in enumerate(atoms)}, {a:'path_fallback_source_position' for a in atoms},
            {a:None for a in atoms},{a:None for a in atoms},protected_sequences=(('d','a'),))

    def test_hard_constraint_overrides_source_priority(self):
        p=self.problem()
        f,b,row=compare(p)
        self.assertEqual(b.order,('b','c','d','a'))
        self.assertEqual(set(b.order),set(p.atoms))
        self.assertEqual(row['legal_violations'],{'fused':0,'kahn':0})
        self.assertIsNone(row['budget_exhausted'])

    def test_empty_and_identical_scopes_stay_in_denominator(self):
        p=OrderProblem('s','p',(),(),{},{},{},{})
        _,_,row=compare(p)
        self.assertEqual(row['size'],0)
        self.assertTrue(row['same_order'])
        self.assertFalse(row['candidate'])

if __name__=='__main__':unittest.main()
