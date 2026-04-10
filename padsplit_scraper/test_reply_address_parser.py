from slack_reply_monitor import _build_address_matcher, _extract_completed_task_ids


def _sample_tasks():
    return {
        "Requests": [
            {
                "id": 101,
                "property_address": {
                    "street1": "1025 Broken Crest Rd",
                    "city": "Fort Worth",
                    "state": "TX",
                },
            },
            {
                "id": 102,
                "property_address": {
                    "street1": "3541 Parker Road East",
                    "city": "Haltom City",
                    "state": "TX",
                },
            },
        ],
        "Open": [
            {
                "id": 201,
                "property_address": {
                    "street1": "4100 N Main St",
                    "city": "Fort Worth",
                    "state": "TX",
                },
            }
        ],
    }


def run_tests() -> int:
    matcher = _build_address_matcher(_sample_tasks())

    cases = [
        ("1025 Broken Crest Complete", [101]),
        ("Complete 1025 broken crest rd", [101]),
        ("Please complete at 1025, broken crest road", [101]),
        ("3541 parker rd east complete", [102]),
        ("Complete 4100 N Main Street", [201]),
        ("4100 north main st complete", [201]),
        ("complete 999 fake st", []),
        ("1025 broken crest is done", []),
    ]

    failures = []
    for text, expected_task_ids in cases:
        found_task_ids = _extract_completed_task_ids(text, matcher)
        found_sorted = sorted(found_task_ids)
        expected_sorted = sorted(expected_task_ids)
        if found_sorted != expected_sorted:
            failures.append(
                {
                    "text": text,
                    "expected": expected_sorted,
                    "found": found_sorted,
                }
            )

    if failures:
        print("FAIL")
        for failure in failures:
            print(
                f"- text={failure['text']!r} expected={failure['expected']} found={failure['found']}"
            )
        return 1

    print("PASS")
    print(f"Validated {len(cases)} parser/address matching cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_tests())
