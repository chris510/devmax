import unittest
from datetime import UTC, datetime, timedelta

from backup import expired_keys


class RetentionTests(unittest.TestCase):
    now = datetime(2026, 9, 9, tzinfo=UTC)

    def backup(self, age, suffix=0):
        timestamp = self.now - timedelta(days=age)
        return {
            "Key": f"postgres/daily/{timestamp:%Y%m%dT%H%M%SZ}-{suffix:032x}.dump",
            "LastModified": timestamp.isoformat(),
        }

    def test_retains_three_copies_even_after_a_long_outage(self):
        objects = [self.backup(age) for age in [0, 40, 41, 42]]
        self.assertEqual(expired_keys(objects, self.now), [objects[-1]["Key"]])

    def test_never_deletes_recent_or_foreign_objects(self):
        objects = [self.backup(age) for age in [0, 1, 2, 30, 31, 32]]
        objects += [
            {
                "Key": "postgres/daily/manual-before-upgrade.dump",
                "LastModified": "2000-01-01T00:00:00Z",
            },
            {"Key": "other/project.dump", "LastModified": "2000-01-01T00:00:00Z"},
        ]
        self.assertEqual(expired_keys(objects, self.now), [objects[5]["Key"]])

    def test_empty_bucket_and_unknown_timestamps_fail_safely(self):
        self.assertEqual(expired_keys([], self.now), [])
        item = self.backup(40)
        item["LastModified"] = "2026-01-01T00:00:00"
        with self.assertRaises(ValueError):
            expired_keys([item], self.now)


if __name__ == "__main__":
    unittest.main()
