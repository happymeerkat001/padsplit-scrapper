#!/usr/bin/env python3
import unittest

import slack_task_digest


class SlackTaskDigestTests(unittest.TestCase):
    def test_compose_message_does_not_treat_vacancy_rooms_as_occupancy(self) -> None:
        latest = {
            "tasks": {
                "Requests": [
                    {
                        "property_address": {"street1": "1025 Broken Crest"},
                        "details": "Window stuck",
                        "room_number": 2,
                    }
                ],
                "Open": [],
            }
        }
        stale_kpis = {
            "vacancy_rooms": [
                {
                    "property": "1025 Broken Crest, DeSoto, TX",
                    "room_number": 3,
                    "days_listed": 51,
                    "base_price": 174,
                }
            ]
        }
        message = slack_task_digest.compose_message(latest, kpis=stale_kpis)
        self.assertIn("1025 Broken Crest", message)
        self.assertIn("Window stuck", message)
        self.assertNotIn("Vacancy Alert", message)
        self.assertNotIn("51 days", message)
        self.assertNotIn("listed $174", message)

    def test_load_data_returns_latest_without_merging_stats_kpis(self) -> None:
        source = slack_task_digest.load_data.__code__.co_names
        self.assertNotIn("STATS_PATH", source)
        digest_source = open(slack_task_digest.__file__).read()
        self.assertNotIn("format_vacancy_alert", digest_source)
        self.assertNotIn('merged["kpis"]', digest_source)


if __name__ == "__main__":
    unittest.main()
