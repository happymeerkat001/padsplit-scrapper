#!/usr/bin/env node
/**
 * Firestore security-rules tests. Never prints document data or allowlist ids.
 *
 * The committed rules leave approvedCodesUid() empty, so every codes read
 * and write fails closed. A second emulator project loads a copy of those
 * rules with the slot set to a test uid and checks that only that uid is
 * allowed.
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
import { doc, getDoc, setDoc } from "firebase/firestore";

const RULES_PATH = new URL("./firestore.rules", import.meta.url);
const rules = readFileSync(RULES_PATH, "utf8");
const TEST_UID = "test-codes-uid";
const OTHER_UID = "other-uid";
const SLOT = /function approvedCodesUid\(\) \{\n      return '';\n    \}/;

if (!SLOT.test(rules)) {
  throw new Error("committed rules must leave approvedCodesUid() empty");
}

const filledRules = rules.replace(SLOT, `function approvedCodesUid() {\n      return '${TEST_UID}';\n    }`);
if (filledRules === rules || !filledRules.includes(`return '${TEST_UID}'`)) {
  throw new Error("could not inject the test uid into a rules copy");
}

let emptyEnv;
let filledEnv;

before(async () => {
  emptyEnv = await initializeTestEnvironment({
    projectId: "demo-padsplit-rules-empty",
    firestore: { rules },
  });
  filledEnv = await initializeTestEnvironment({
    projectId: "demo-padsplit-rules-filled",
    firestore: { rules: filledRules },
  });
});

after(async () => {
  if (emptyEnv) await emptyEnv.cleanup();
  if (filledEnv) await filledEnv.cleanup();
});

function readDoc(context, path) {
  return getDoc(doc(context.firestore(), path));
}

test("empty slot denies signed-out reads of notes/codes and property_codes", async () => {
  const db = emptyEnv.unauthenticatedContext();
  await assertFails(readDoc(db, "notes/codes"));
  await assertFails(readDoc(db, "property_codes/example_house"));
});

test("empty slot denies every signed-in uid", async () => {
  for (const uid of [TEST_UID, OTHER_UID]) {
    const db = emptyEnv.authenticatedContext(uid);
    await assertFails(readDoc(db, "notes/codes"));
    await assertFails(readDoc(db, "property_codes/example_house"));
    await assertFails(setDoc(doc(db.firestore(), "property_codes/example_house"), { front_door: "x" }));
    await assertFails(setDoc(doc(db.firestore(), "notes/codes"), { text: "n" }));
    await assertFails(setDoc(doc(db.firestore(), "property_codes/example_house/code_versions/v1"), {
      fields: { front_door: "x" },
      contentHash: "abc",
      createdAt: "t",
      expireAt: "e",
      source: "page",
    }));
  }
});

test("signed-out read of other notes documents stays allowed", async () => {
  const db = emptyEnv.unauthenticatedContext();
  await assertSucceeds(readDoc(db, "notes/dashboard"));
});

test("configured test uid can read and write codes; any other uid cannot", async () => {
  const ok = filledEnv.authenticatedContext(TEST_UID);
  await assertSucceeds(readDoc(ok, "notes/codes"));
  await assertSucceeds(readDoc(ok, "property_codes/example_house"));
  await assertSucceeds(setDoc(doc(ok.firestore(), "property_codes/example_house"), { front_door: "x" }));
  await assertSucceeds(setDoc(doc(ok.firestore(), "notes/codes"), { text: "n" }));
  await assertSucceeds(setDoc(doc(ok.firestore(), "property_codes/example_house/code_versions/v1"), {
    fields: { front_door: "x" },
    contentHash: "abc",
    createdAt: "t",
    expireAt: "e",
    source: "page",
  }));

  const signedOut = filledEnv.unauthenticatedContext();
  await assertFails(readDoc(signedOut, "notes/codes"));
  await assertFails(readDoc(signedOut, "property_codes/example_house"));

  const other = filledEnv.authenticatedContext(OTHER_UID);
  await assertFails(readDoc(other, "notes/codes"));
  await assertFails(readDoc(other, "property_codes/example_house"));
  await assertFails(setDoc(doc(other.firestore(), "property_codes/example_house"), { front_door: "y" }));
  await assertFails(setDoc(doc(other.firestore(), "notes/codes"), { text: "m" }));
});
