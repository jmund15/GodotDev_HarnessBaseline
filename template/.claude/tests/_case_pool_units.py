"""Unittest fixtures that test_case_pool.py runs through run_cases; underscore-named so pytest does not
collect the planted failure as a test of its own."""
import os
import unittest


class PlantedUnit(unittest.TestCase):
    def setUp(self):
        self.ready = True

    def test_passes(self):
        self.assertTrue(self.ready)

    def test_fails(self):
        self.fail("planted")


class FixtureUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = "planted"

    @classmethod
    def tearDownClass(cls):
        with open(os.path.join(os.environ["POOL_SPANS"], "teardown_class"), "a", encoding="utf-8") as fh:
            fh.write("x")

    def test_sees_the_class_fixture(self):
        self.assertEqual(self.shared, "planted")
