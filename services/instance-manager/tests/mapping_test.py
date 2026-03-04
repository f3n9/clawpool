import unittest

from services_instance_manager.main import (
    classify_instance_lifecycle,
    normalize_identity,
    resolve_container_name,
)


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

    def test_email_identity_is_normalized_for_container_name(self):
        container = resolve_container_name(employee_id="fyue@yinxiang.com", user_sub=None, mapping={})
        self.assertEqual(container, "openclaw-fyue-yinxiang.com")

    def test_normalize_identity_rejects_invalid_symbols_only(self):
        with self.assertRaises(ValueError):
            normalize_identity("%%%%")

    def test_lifecycle_classification(self):
        self.assertEqual(classify_instance_lifecycle("created", "started"), "new")
        self.assertEqual(classify_instance_lifecycle("existing", "running"), "running")
        self.assertEqual(classify_instance_lifecycle("existing", "started"), "restart")


if __name__ == "__main__":
    unittest.main()
