#!/usr/bin/env node
/**
 * Firestore security-rules tests. Never prints document data or allowlist ids.
 *
 * Requires Java and the Firestore emulator:
 *   npm install
 *   npm run test:rules
 *
 * That runs `firebase emulators:exec --only firestore` so the emulator
 * starts, the test runs, and the emulator stops. Rules are not deployed.
 */
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import { assertFails, assertSucceeds, initializeTestEnvironment } from "@firebase/rules-unit-testing";
import { doc, getDoc } from "firebase/firestore";

const RULES_PATH = new URL("./firestore.rules", import.meta.url);
const rules = readFileSync(RULES_PATH, "utf8");

function codesUid() {
  const start = rules.indexOf("match /property_codes/{slug}");
  const end = rules.indexOf("match /code_versions/{id}", start);
  const block = rules.slice(start, end);
  const match = block.match(/request\.auth\.uid == '([^']+)'/);
  if (!match) throw new Error("property_codes allowlist uid not found in rules");
  return match[1];
}

const CODES_UID = codesUid();
const OTHER_UID = "other-uid";

let testEnv;

before(async () => {
  testEnv = await initializeTestEnvironment({
    projectId: "demo-padsplit-rules",
    firestore: { rules },
  });
});

after(async () => {
  if (testEnv) await testEnv.cleanup();
});

function readDoc(context, path) {
  return getDoc(doc(context.firestore(), path));
}

test("signed-out read of notes/codes is denied", async () => {
  const db = testEnv.unauthenticatedContext();
  await assertFails(readDoc(db, "notes/codes"));
});

test("signed-out read of property_codes is denied", async () => {
  const db = testEnv.unauthenticatedContext();
  await assertFails(readDoc(db, "property_codes/example_house"));
});

test("other uid cannot read notes/codes or property_codes", async () => {
  const db = testEnv.authenticatedContext(OTHER_UID);
  await assertFails(readDoc(db, "notes/codes"));
  await assertFails(readDoc(db, "property_codes/example_house"));
});

test("allowlisted uid can read notes/codes and property_codes", async () => {
  const db = testEnv.authenticatedContext(CODES_UID);
  await assertSucceeds(readDoc(db, "notes/codes"));
  await assertSucceeds(readDoc(db, "property_codes/example_house"));
});

test("signed-out read of other notes documents stays allowed", async () => {
  const db = testEnv.unauthenticatedContext();
  await assertSucceeds(readDoc(db, "notes/dashboard"));
});
