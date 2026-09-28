#!/usr/bin/env node
/**
 * Firestore security-rules tests. Never prints document data or allowlist ids.
 *
 * Committed rules keep Ang's uid and leave approvedCodesUids() empty, so the
 * list matches nobody. A second emulator project loads a copy with two test
 * uids in that list.
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
import { collection, doc, getDoc, getDocs, limit, query, setDoc } from "firebase/firestore";

const RULES_PATH = new URL("./firestore.rules", import.meta.url);
const rules = readFileSync(RULES_PATH, "utf8");
const TEST_UID = "test-codes-uid";
const TEST_UID_2 = "test-codes-uid-2";
const OTHER_UID = "other-uid";
const LIST_SLOT = /function approvedCodesUids\(\) \{\n      return \[\];\n    \}/;
const ANG_SLOT = /function angCodesUid\(\) \{\n      return '([^']+)';\n    \}/;

const angMatch = rules.match(ANG_SLOT);
if (!angMatch) throw new Error("angCodesUid() missing from rules");
const ANG_UID = angMatch[1];
if (!LIST_SLOT.test(rules)) {
  throw new Error("committed rules must leave approvedCodesUids() empty");
}

const filledRules = rules.replace(
  LIST_SLOT,
  `function approvedCodesUids() {\n      return ['${TEST_UID}', '${TEST_UID_2}'];\n    }`,
);
if (filledRules === rules || !filledRules.includes(`'${TEST_UID}'`) || !filledRules.includes(`'${TEST_UID_2}'`)) {
  throw new Error("could not inject the test uids into a rules copy");
}
if (!filledRules.includes(ANG_UID)) {
  throw new Error("filled rules copy dropped the existing codes uid");
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

function listVersions(context) {
  const versions = collection(context.firestore(), "property_codes/example_house/code_versions");
  return getDocs(query(versions, limit(1)));
}

const VERSION_PATH = "property_codes/example_house/code_versions/v1";

async function assertCodesDenied(context) {
  await assertFails(readDoc(context, "notes/codes"));
  await assertFails(readDoc(context, "property_codes/example_house"));
  await assertFails(readDoc(context, VERSION_PATH));
  await assertFails(listVersions(context));
}

async function assertCodesAllowed(context) {
  await assertSucceeds(readDoc(context, "notes/codes"));
  await assertSucceeds(readDoc(context, "property_codes/example_house"));
  await assertSucceeds(readDoc(context, VERSION_PATH));
  await assertSucceeds(listVersions(context));
}

test("signed-out reads of codes and code_versions are denied", async () => {
  const db = emptyEnv.unauthenticatedContext();
  await assertCodesDenied(db);
});

test("empty approved list matches nobody", async () => {
  const db = emptyEnv.authenticatedContext(TEST_UID);
  await assertCodesDenied(db);
  const second = emptyEnv.authenticatedContext(TEST_UID_2);
  await assertCodesDenied(second);
  await assertFails(setDoc(doc(db.firestore(), "property_codes/example_house"), { front_door: "x" }));
  await assertFails(setDoc(doc(db.firestore(), "notes/codes"), { text: "n" }));
});

test("other uid cannot read codes or code_versions", async () => {
  const db = emptyEnv.authenticatedContext(OTHER_UID);
  await assertCodesDenied(db);
});

test("owner can read codes and code_versions while the approved list is empty", async () => {
  const db = emptyEnv.authenticatedContext(ANG_UID);
  await assertCodesAllowed(db);
  await assertSucceeds(setDoc(doc(db.firestore(), "property_codes/example_house"), { front_door: "x" }));
});

test("signed-out read of other notes documents stays allowed", async () => {
  const db = emptyEnv.unauthenticatedContext();
  await assertSucceeds(readDoc(db, "notes/dashboard"));
});

test("two approved uids can read codes and code_versions", async () => {
  for (const uid of [TEST_UID, TEST_UID_2]) {
    const approved = filledEnv.authenticatedContext(uid);
    await assertCodesAllowed(approved);
    await assertSucceeds(setDoc(doc(approved.firestore(), "property_codes/example_house"), { front_door: "x" }));
    await assertSucceeds(setDoc(doc(approved.firestore(), "notes/codes"), { text: "n" }));
    await assertSucceeds(setDoc(doc(approved.firestore(), `property_codes/example_house/code_versions/${uid}`), {
      fields: { front_door: "x" },
      contentHash: "abc",
      createdAt: "t",
      expireAt: "e",
      source: "page",
    }));
  }

  const ang = filledEnv.authenticatedContext(ANG_UID);
  await assertCodesAllowed(ang);

  const signedOut = filledEnv.unauthenticatedContext();
  await assertCodesDenied(signedOut);

  const other = filledEnv.authenticatedContext(OTHER_UID);
  await assertCodesDenied(other);
  await assertFails(setDoc(doc(other.firestore(), "property_codes/example_house"), { front_door: "y" }));
  await assertFails(setDoc(doc(other.firestore(), "notes/codes"), { text: "m" }));
});
