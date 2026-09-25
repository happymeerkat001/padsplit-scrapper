#!/usr/bin/env python3
"""Structure-only tests for the codes dashboard. Never print field values."""

import re
import subprocess
import unittest
from pathlib import Path


HTML_PATH = Path(__file__).resolve().parent / "docs" / "codes.html"
DOCS_DIR = HTML_PATH.parent
OCCUPANCY_PATH = Path(__file__).resolve().parent / "docs" / "data" / "occupancy.json"
DIGIT_RUN_RE = re.compile(r"\d{4,}")
VALUE_STRING_RE = re.compile(
    r"""\bvalue\s*:\s*(?P<q>["'])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)"""
)

HOUSE_SLUGS = [
    "leana_6623",
    "sylvia_2516",
    "ridge_oak_10235",
    "pebbleshores_3414",
    "greenhill_3406",
    "parker_4351",
    "pioneer_1404",
    "burton_5509",
    "broken_crest_1025",
]

ROOM_COUNTS = {
    "leana_6623": 5,
    "sylvia_2516": 6,
    "ridge_oak_10235": 6,
    "pebbleshores_3414": 7,
    "greenhill_3406": 7,
    "parker_4351": 8,
    "pioneer_1404": 7,
    "burton_5509": 7,
    "broken_crest_1025": 9,
}

LEGACY_LOCKBOX_COUNTS = {
    "parker_4351": 8,
    "burton_5509": 7,
}

CONTACT_OPS_KEYS = (
    "ac_filter_date",
    "ac_filter_size",
    "dryer_lint_date",
    "dryer_lint_notes",
)

HOUSE_NOTES_KEYS = (
    "garage_ac",
    "garage_ac_filter_date",
    "garage_ac_filter_size",
    "other_special",
)

EXTRA_LOCKBOX_COUNT = 2

SLUG_RE = re.compile(r'slug:\s*"([a-z0-9_]+)"')
FIELD_KEY_RE = re.compile(r'key:\s*"([a-z0-9_]+)"')
KEY_VALUE_RE = re.compile(r'key:\s*"([a-z0-9_]+)"[^\n]*value:\s*"([^"]*)"')


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _slugs(html: str) -> list[str]:
    return SLUG_RE.findall(html)


def _defaults_block(html: str) -> str:
    start = html.index("const DEFAULTS = [")
    end = html.index("];", start)
    return html[start:end]


def _house_blocks(html: str) -> dict[str, str]:
    block = _defaults_block(html)
    parts = re.split(r'slug:\s*"', block)
    houses = {}
    for part in parts[1:]:
        slug, rest = part.split('"', 1)
        houses[slug] = rest
    return houses


def _field_keys(block: str) -> list[str]:
    return FIELD_KEY_RE.findall(block)


def _key_values(block: str) -> dict[str, str]:
    return dict(KEY_VALUE_RE.findall(block))


def room_ops_keys(room_count: int) -> list[str]:
    keys = []
    for n in range(1, room_count + 1):
        keys.extend(
            [
                f"r{n}",
                f"lockbox_{n}",
                f"lockbox_{n}_location",
                f"lockbox_{n}_notes",
                f"r{n}_ac_filter_size",
            ]
        )
    return keys


def extra_lockbox_keys(n: int) -> list[str]:
    return [
        f"extra_lockbox_{n}_name",
        f"extra_lockbox_{n}_code",
        f"extra_lockbox_{n}_location",
        f"extra_lockbox_{n}_notes",
    ]


def expected_save_ops_keys(room_count: int) -> set[str]:
    keys = set(room_ops_keys(room_count))
    keys.update(CONTACT_OPS_KEYS)
    keys.update(HOUSE_NOTES_KEYS)
    for n in range(1, EXTRA_LOCKBOX_COUNT + 1):
        keys.update(extra_lockbox_keys(n))
    return keys


class CodesDashboardStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _html()
        cls.defaults = _defaults_block(cls.html)
        cls.houses = _house_blocks(cls.html)

    def test_rental_houses_exclude_spanish_moss(self):
        slugs = _slugs(self.defaults)
        self.assertEqual(slugs, HOUSE_SLUGS)
        self.assertEqual(len(slugs), 9)
        self.assertNotIn("spanish_moss", slugs)
        self.assertNotIn("spanish_moss", self.houses)
        self.assertNotIn("Spanish Moss", self.defaults)

    def test_room_counts_match_defaults(self):
        for slug, count in ROOM_COUNTS.items():
            keys = _field_keys(self.houses[slug])
            room_keys = [k for k in keys if re.fullmatch(r"r\d+", k)]
            self.assertEqual(
                room_keys,
                [f"r{n}" for n in range(1, count + 1)],
                msg=f"{slug} room keys",
            )

    def test_contact_ops_keys_on_every_house(self):
        for slug, block in self.houses.items():
            keys = set(_field_keys(block))
            for key in CONTACT_OPS_KEYS:
                self.assertIn(key, keys, msg=f"{slug} missing {key}")
            self.assertIn('label: "AC filter date"', block)
            self.assertIn('placeholder: "16x25x1"', block)
        self.assertEqual(set(self.houses), set(HOUSE_SLUGS))

    def test_house_notes_fields_on_every_house(self):
        self.assertIn("const HOUSE_NOTES_SECTION = {", self.html)
        self.assertIn("label: 'House notes'", self.html)
        self.assertIn("body.appendChild(renderLabelValueTable(property, HOUSE_NOTES_SECTION, savedValues))", self.html)
        for key in HOUSE_NOTES_KEYS:
            self.assertIn(f"key: '{key}'", self.html)
        self.assertIn("label: 'Garage AC'", self.html)
        self.assertIn("label: 'Garage AC filter change date'", self.html)
        self.assertIn("label: 'Garage AC filter size'", self.html)
        self.assertIn("label: 'Other special things'", self.html)
        self.assertIn("placeholder: '16x25x1'", self.html)
        self.assertIn("type: 'textarea'", self.html)
        self.assertIn('input[data-prop="${slug}"], textarea[data-prop="${slug}"]', self.html)
        for key in HOUSE_NOTES_KEYS:
            self.assertRegex(
                self.html,
                rf"key: '{key}'[^\n]*value: ''",
                msg=f"{key} must have an empty default",
            )

    def test_new_ops_fields_have_empty_defaults(self):
        for slug, block in self.houses.items():
            values = _key_values(block)
            for key in ("ac_filter_size", "dryer_lint_date", "dryer_lint_notes"):
                self.assertEqual(values.get(key), "", msg=f"{slug} {key} must be empty")

    def test_rooms_table_columns_exist(self):
        for header in (
            "Room",
            "Room code",
            "Lockbox code",
            "Lockbox location",
            "Lockbox notes",
            "AC filter size",
        ):
            self.assertIn(f"'{header}'", self.html)
        self.assertIn("function lockboxCodeKey(n) { return `lockbox_${n}`; }", self.html)
        self.assertIn("function lockboxLocationKey(n) { return `lockbox_${n}_location`; }", self.html)
        self.assertIn("function lockboxNotesKey(n) { return `lockbox_${n}_notes`; }", self.html)
        self.assertIn("function roomAcFilterSizeKey(n) { return `r${n}_ac_filter_size`; }", self.html)

    def test_save_payload_includes_new_ops_keys(self):
        self.assertIn("r${n}_ac_filter_size", self.html)
        self.assertIn("lockbox_${n}_location", self.html)
        self.assertIn("lockbox_${n}_notes", self.html)
        self.assertIn("extra_lockbox_${n}_name", self.html)
        self.assertIn("extra_lockbox_${n}_code", self.html)
        for slug, count in ROOM_COUNTS.items():
            expected = expected_save_ops_keys(count)
            self.assertTrue(
                expected.issuperset({"ac_filter_size", "dryer_lint_date", "extra_lockbox_1_code"}),
                msg=f"{slug} ops keys",
            )
            self.assertTrue(expected.issuperset({"r1", "lockbox_1"}))
            self.assertTrue(expected.issuperset(HOUSE_NOTES_KEYS))
            self.assertIn(f"lockbox_{count}", expected)
            self.assertIn(f"r{count}_ac_filter_size", expected)

    def test_legacy_lockbox_n_keys_still_in_defaults(self):
        for slug, count in LEGACY_LOCKBOX_COUNTS.items():
            keys = _field_keys(self.houses[slug])
            for n in range(1, count + 1):
                self.assertIn(f"lockbox_{n}", keys, msg=f"{slug} missing lockbox_{n}")

    def test_lockbox_n_still_maps_as_code_field(self):
        self.assertIn("const lockboxKey = lockboxCodeKey(n);", self.html)
        self.assertIn("defaultFieldValue(property, lockboxKey, '')", self.html)
        self.assertIn("if (section.label === 'Lockboxes') return;", self.html)

    def test_extra_lockboxes_on_every_house(self):
        self.assertIn("const EXTRA_LOCKBOX_COUNT = 2;", self.html)
        self.assertIn("Extra Lockboxes", self.html)
        for n in range(1, EXTRA_LOCKBOX_COUNT + 1):
            for key in extra_lockbox_keys(n):
                template = key.replace(str(n), "${n}")
                self.assertIn(template, self.html)

    def test_overdue_windows(self):
        self.assertIn("ac_filter_date: 90", self.html)
        self.assertIn("dryer_lint_date: 30", self.html)
        self.assertIn("garage_ac_filter_date: 90", self.html)

    def test_firestore_merge_and_gate_unchanged(self):
        self.assertIn("collection(db, 'property_codes')", self.html)
        self.assertIn("doc(db, 'property_codes', slug)", self.html)
        self.assertIn("{ merge: true }", self.html)
        self.assertIn("signInWithEmailAndPassword", self.html)
        self.assertIn("doc(db, 'notes', 'codes')", self.html)
        self.assertIn('id="gate-wrap"', self.html)

    def test_history_control_and_restore_overwrite(self):
        self.assertIn("code_versions", self.html)
        self.assertIn("history-btn", self.html)
        self.assertIn(".history-panel {", self.html)
        self.assertIn('id="page-history-btn"', self.html)
        self.assertIn('id="page-history-panel"', self.html)
        self.assertLess(
            self.html.index('id="page-history-btn"'),
            self.html.index("Values in parentheses indicate known conflicting entries"),
        )
        self.assertIn("startAfter", self.html)
        self.assertIn("history-older", self.html)
        self.assertIn("async function restoreLiveCodes", self.html)
        self.assertIn("8jOJNgLoxpfyseZ0RY1PDZ1DXbi2", self.html)
        self.assertIn("hashCodeFields", self.html)
        self.assertIn("SHA-256", self.html)
        self.assertIn("deletes live keys", self.html)
        self.assertIn("Unsaved edits for this house are lost", self.html)
        self.assertIn("window.confirm", self.html)
        self.assertIn("slug === 'spanish_moss'", self.html)
        restore_fn = self.html.split("async function restoreLiveCodes", 1)[1].split("function bindHouse", 1)[0]
        self.assertIn("setDoc(liveRef(slug)", restore_fn)
        self.assertNotIn("{ merge: true }", restore_fn)
        save_fn = self.html.split("btn.addEventListener('click', async (e) => {", 1)[1]
        self.assertIn("{ merge: true }", save_fn)

    def test_occupancy_json_has_no_codes_or_filter_sizes(self):
        occupancy = OCCUPANCY_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "ac_filter_size",
            "dryer_lint",
            "lockbox_",
            "room_code",
            "wifi_",
            "code_versions",
        ):
            self.assertNotIn(forbidden, occupancy)

    def test_renderer_payload_keys_with_dummy_house(self):
        result = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "test_codes_dashboard_render.mjs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg="renderer structure test failed")
        self.assertIn("codes dashboard renderer structure ok", result.stdout)

    def test_codes_config_has_no_values_or_digit_runs(self):
        values = re.findall(r'value:\s*"([^"]*)"', self.defaults)
        self.assertGreater(len(values), 0)
        for index, value in enumerate(values):
            self.assertEqual(value, "", msg=f"DEFAULTS value[{index}] must be empty")
            self.assertIsNone(DIGIT_RUN_RE.search(value), msg=f"DEFAULTS value[{index}] has a digit run")
        kept = []
        for line in self.defaults.splitlines():
            if re.search(r"\b(?:slug|address)\s*:", line):
                continue
            kept.append(line)
        self.assertIsNone(
            DIGIT_RUN_RE.search("\n".join(kept)),
            msg="digit run of 4+ in the codes config outside slug/address lines",
        )

    def test_docs_pages_have_no_nonempty_value_strings(self):
        files = [
            path
            for path in DOCS_DIR.rglob("*")
            if path.suffix in {".html", ".js"} and path.is_file()
        ]
        self.assertTrue(any(path.name == "codes.html" for path in files))
        for path in files:
            text = path.read_text(encoding="utf-8")
            for match in VALUE_STRING_RE.finditer(text):
                body = match.group("body")
                if body == "" and not DIGIT_RUN_RE.search(body):
                    continue
                rel = path.relative_to(DOCS_DIR.parent)
                self.fail(f"{rel} has a DEFAULTS value string at offset {match.start()}")

    def test_codes_page_state_copy(self):
        self.assertIn("<title>ops</title>", self.html)
        self.assertNotIn("PadSplit Codes", self.html)
        head = self.html.split("</head>", 1)[0]
        self.assertIsNone(
            re.search(
                r'<meta[^>]+(?:name="description"|property="og:|name="twitter:)',
                head,
                re.I,
            )
        )
        self.assertIn('id="app-wrap" class="hidden"', self.html)
        self.assertIn("loading codes…", self.html)
        self.assertIn("your account isn't approved for codes yet. ask ang.", self.html)
        self.assertIn("no codes saved for ${property.address} yet.", self.html)
        self.assertIn("couldn't load codes. refresh, or use the lockout ladder.", self.html)
        self.assertNotIn("sign in to load codes", self.html)
        self.assertNotIn("No codes stored for this house.", self.html)
        self.assertNotIn("renderAllHouses(null)", self.html)
        self.assertIn("getDocsFromServer", self.html)
        self.assertNotIn("copy-all", self.html.lower())
        self.assertNotIn("copy all", self.html.lower())
        self.assertNotRegex(self.html, r"(?i)\bexport\b")
        signed_out = self.html.split("function showSignedOut", 1)[1].split("function showLoading", 1)[0]
        self.assertNotIn("renderAllHouses", signed_out)
        self.assertIn("clearPropertyList", signed_out)
        loading = self.html.split("function showLoading", 1)[1].split("function showNotApproved", 1)[0]
        self.assertIn("loading codes…", loading)
        self.assertIn("clearPropertyList", loading)
        self.assertNotIn("renderAllHouses", loading)
        denied = self.html.split("function showNotApproved", 1)[1].split("function showLoadError", 1)[0]
        self.assertIn("your account isn't approved for codes yet. ask ang.", denied)
        self.assertIn("clearPropertyList", denied)
        self.assertNotIn("renderAllHouses", denied)
        failed = self.html.split("function showLoadError", 1)[1].split("function isPermissionDenied", 1)[0]
        self.assertIn("couldn't load codes. refresh, or use the lockout ladder.", failed)
        self.assertIn("clearPropertyList", failed)
        self.assertNotIn("renderAllHouses", failed)
        self.assertNotIn("localStorage", failed)
        self.assertEqual(self.html.count("onSnapshot(NOTES_DOC"), 1)
        start_notes = self.html.split("function startNotes()", 1)[1].split("function ", 1)[0]
        self.assertIn("onSnapshot(NOTES_DOC", start_notes)
        cleared = self.html.split("function clearPropertyList()", 1)[1].split("function ", 1)[0]
        self.assertIn("stopNotes()", cleared)
        init_codes = self.html.split("async function initCodes", 1)[1].split("showSignedOut();", 1)[0]
        self.assertIn("startNotes()", init_codes)

    def test_codes_page_has_no_client_value_fallback(self):
        self.assertIn("if (!currentHasLiveDoc) return '';", self.html)
        self.assertIn("user.uid !== CODES_UID", self.html)
        resolver = self.html.split("function resolveSavedOrDefault", 1)[1].split("function ", 1)[0]
        self.assertNotIn("field.value", resolver)
        self.assertNotIn("return fallback", resolver)
        self.assertNotIn("return _fallback", resolver)
        defaults_fn = self.html.split("function defaultFieldValue", 1)[1].split("let currentHasLiveDoc", 1)[0]
        self.assertNotIn("field.value", defaults_fn)
        self.assertNotIn("return fallback", defaults_fn)
        self.assertNotIn("return _fallback", defaults_fn)
        for banned in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "enableIndexedDbPersistence",
            "persistentLocalCache",
            "serviceWorker",
            "caches.open",
        ):
            self.assertNotIn(banned, self.html, msg=banned)


if __name__ == "__main__":
    unittest.main()
