# Firestore security-rule tests

These tests run against the local Firestore emulator and a `demo-padsplit` project. They read the repository `firestore.rules`; they do not connect to or deploy rules to the live project. All sample data is fictional.

Requirements: Node.js 20 or later, npm, and Java 17 or later. Firebase CLI 14 is pinned to support Java 17; Java 21 is needed for Firebase CLI 15 or later.

```sh
cd tests/firestore
npm ci
npm test
```

If Java is installed but not on the shell path, prefix the final command with an appropriate `PATH`, e.g. `PATH="/opt/homebrew/opt/openjdk@17/bin:$PATH" npm test`. The first run downloads the emulator. The suite verifies history requires all five expected fields and the proper map, string, and timestamp types, including the browser’s `serverTimestamp()` write. These tests cover checked-in rules, not deployed rules.

The suite also exercises the real private snapshot reader against emulator data and owner, other-user, and anonymous rule contexts. It checks incomplete snapshots, owner-only task discussion and manual-task mutations, and listener detach/reattach on simulated sign-out and sign-in. Those owner and other-user rule contexts are mocked by `@firebase/rules-unit-testing`. A separate test uses the Firebase Authentication emulator and real SDK-created fictional credentials to verify sign-in, sign-out, sign-in again, and Firestore denial for a non-owner. These tests do not verify browser session restoration, token expiry, an owner browser session, or production authorization.
