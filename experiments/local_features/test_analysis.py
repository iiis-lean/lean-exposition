"""Small statistical and lexical edge-case checks (no real model requests)."""
import unittest
from analyze import agreement, quantile
from prepare import strip_comments


class DescriptiveChecks(unittest.TestCase):
    def test_constant_kappa_is_missing(self):
        row=agreement(['no']*4,['no']*4)
        self.assertIsNone(row['cohen_kappa'])
        self.assertIn('degenerate',row['kappa_null_reason'])
        self.assertEqual(row['agreement'],1)
    def test_exact_and_opposite_kappa(self):
        self.assertEqual(agreement(['yes','no'],['yes','no'])['cohen_kappa'],1)
        self.assertEqual(agreement(['yes','no'],['no','yes'])['cohen_kappa'],-1)
    def test_uncertainty_is_a_third_category(self):
        row=agreement(['uncertain','yes'],['uncertain','no'])
        self.assertEqual(row['agreement'],.5)
        self.assertEqual(row['definite_pairs'],1)
        self.assertEqual(row['definite_agreement'],0)
        self.assertEqual(row['both_uncertain_rate'],.5)
    def test_fixed_quartile_interpolation(self):
        self.assertEqual(quantile([0,10,20,30],.25),7.5)
        self.assertEqual(quantile([3]*10,.75),3)
    def test_nested_comments_and_string_literals(self):
        text='def x := "-- keep /- this -/" /- outer /- inner -/ -/\n-- remove\ndef y := 2'
        cleaned=strip_comments(text)
        self.assertIn('"-- keep /- this -/"',cleaned)
        self.assertNotIn('inner',cleaned)
        self.assertNotIn('remove',cleaned)
        self.assertIn('def y := 2',cleaned)

if __name__=='__main__':unittest.main()
