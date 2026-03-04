import unittest

from resource_controller.main import choose_resource_profile


class ResourcePolicyTests(unittest.TestCase):
    def test_boost_profile_when_low_concurrency(self):
        profile = choose_resource_profile(active_instances=4, boost_threshold=10)
        self.assertEqual(profile, "boost")

    def test_base_profile_when_high_concurrency(self):
        profile = choose_resource_profile(active_instances=18, boost_threshold=10)
        self.assertEqual(profile, "base")


if __name__ == "__main__":
    unittest.main()
