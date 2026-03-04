import unittest

from services_instance_manager.main import resolve_container_name


class MappingTests(unittest.TestCase):
    def test_employee_id_maps_to_deterministic_container(self):
        container = resolve_container_name(employee_id="u1001", user_sub=None, mapping={})
        self.assertEqual(container, "openclaw-u1001")

    def test_fallback_to_sub_when_employee_id_missing(self):
        container = resolve_container_name(employee_id=None, user_sub="abc-123", mapping={})
        self.assertEqual(container, "openclaw-abc-123")

    def test_reject_when_both_identity_fields_missing(self):
        with self.assertRaises(ValueError):
            resolve_container_name(employee_id=None, user_sub=None, mapping={})

    def test_mapping_override_has_priority(self):
        mapping = {"u2002": "openclaw-special-u2002"}
        container = resolve_container_name(employee_id="u2002", user_sub="ignored", mapping=mapping)
        self.assertEqual(container, "openclaw-special-u2002")


if __name__ == "__main__":
    unittest.main()
