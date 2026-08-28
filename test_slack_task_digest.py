#!/usr/bin/env python3
import unittest

import slack_task_digest


class SlackTaskDigestTests(unittest.TestCase):
    def test_digest_does_not_read_vacancy_rooms_as_occupancy(self) -> None:
        payload = {
            "kpis": {
                "vacancy_rooms": [
                    {
                        "property": "1025 Broken Crest",
                        "room_number": 3,
                        "days_listed": 60,
                        "base_price": 200,
                    }
                ]
            },
            "tasks": {
                "Requests": [
                    {
                        "property_address": {"street1": "5509 Burton Avenue"},
                        "details": "Fridge leak",
                        "room_number": 7,
                    }
                ],
                "Open": [],
            },
        }
        message = slack_task_digest.build_digest_message(payload)
        self.assertIn("Tasks Digest", message)
        self.assertIn("5509 Burton Avenue", message)
        self.assertIn("Fridge leak", message)
        self.assertNotIn("Vacancy Alert", message)
        self.assertNotIn("days_listed", message)
        self.assertNotIn("60 days", message)
        self.assertFalse(hasattr(slack_task_digest, "format_vacancy_alert"))
        self.assertFalse(hasattr(slack_task_digest, "STATS_PATH"))


if __name__ == "__main__":
    unittest.main()
