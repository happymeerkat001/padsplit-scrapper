#!/usr/bin/env node
/**
 * Firestore security-rules tests. Never prints document data or allowlist ids.
 *
 * Committed rules keep Ang's uid and leave approvedCodesUid() empty, so the
 * Joe slot matches nobody. A second emulator project loads a copy with that
 * slot set to a test uid.
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
import { collection, deleteDoc, doc, getDoc, getDocs, limit, query, setDoc } from "firebase/firestore";

const RULES_PATH = new URL("./firestore.rules", import.meta.url);
const rules = readFileSync(RULES_PATH, "utf8");
const TEST_UID = "test-codes-uid";
const OTHER_UID = "other-uid";
const JOE_SLOT = /function approvedCodesUid\(\) \{\n      return '';\n    \}/;
const ANG_SLOT = /function angCodesUid\(\) \{\n      return '([^']+)';\n    \}/;

const angMatch = rules.match(ANG_SLOT);
if (!angMatch) throw new Error("angCodesUid() missing from rules");
const ANG_UID = angMatch[1];
if (!JOE_SLOT.test(rules)) {
  throw new Error("committed rules must leave approvedCodesUid() empty");
}

const filledRules = rules.replace(JOE_SLOT, `function approvedCodesUid() {\n      return '${TEST_UID}';\n    }`);
if (filledRules === rules || !filledRules.includes(`return '${TEST_UID}'`)) {
  throw new Error("could not inject the test uid into a rules copy");
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

test("empty Joe slot matches nobody", async () => {
  const db = emptyEnv.authenticatedContext(TEST_UID);
  await assertCodesDenied(db);
  await assertFails(setDoc(doc(db.firestore(), "property_codes/example_house"), { front_door: "x" }));
  await assertFails(setDoc(doc(db.firestore(), "notes/codes"), { text: "n" }));
});

test("other uid cannot read codes or code_versions", async () => {
  const db = emptyEnv.authenticatedContext(OTHER_UID);
  await assertCodesDenied(db);
});

test("existing codes uid can read codes and code_versions while Joe's slot is empty", async () => {
  const db = emptyEnv.authenticatedContext(ANG_UID);
  await assertCodesAllowed(db);
  await assertSucceeds(setDoc(doc(db.firestore(), "property_codes/example_house"), { front_door: "x" }));
});

const NOTE_DOCS = ["notes/codes", "notes/templates", "notes/stats", "notes/dashboard", "notes/messages"];

function notesPayload(text) {
  return { text };
}

test("signed-out reads and writes of notes are denied", async () => {
  const db = emptyEnv.unauthenticatedContext();
  for (const path of NOTE_DOCS) {
    await assertFails(readDoc(db, path));
    await assertFails(setDoc(doc(db.firestore(), path), notesPayload("n")));
  }
});

test("approved user can read and save notes, and an oversize note is denied", async () => {
  const ang = emptyEnv.authenticatedContext(ANG_UID);
  const joe = filledEnv.authenticatedContext(TEST_UID);
  for (const path of ["notes/templates", "notes/stats", "notes/codes", "notes/dashboard", "notes/messages"]) {
    await assertSucceeds(readDoc(ang, path));
    await assertSucceeds(setDoc(doc(ang.firestore(), path), notesPayload("n"), { merge: true }));
    await assertSucceeds(readDoc(joe, path));
    await assertSucceeds(setDoc(doc(joe.firestore(), path), notesPayload(""), { merge: true }));
  }
  const oversize = "x".repeat(20000);
  await assertFails(setDoc(doc(ang.firestore(), "notes/templates"), notesPayload(oversize)));
  await assertFails(setDoc(doc(ang.firestore(), "notes/stats"), notesPayload(oversize)));
  await assertSucceeds(setDoc(doc(ang.firestore(), "notes/templates"), notesPayload("x".repeat(19999))));
  await assertFails(setDoc(doc(ang.firestore(), "notes/stats"), { text: "n", extra: "nope" }));

  const signedOut = emptyEnv.unauthenticatedContext();
  await assertFails(readDoc(signedOut, "notes/templates"));
  await assertFails(setDoc(doc(signedOut.firestore(), "notes/stats"), notesPayload("n")));
});

test("filled Joe slot is allowed and any other uid stays denied", async () => {
  const joe = filledEnv.authenticatedContext(TEST_UID);
  await assertCodesAllowed(joe);
  await assertSucceeds(setDoc(doc(joe.firestore(), "property_codes/example_house"), { front_door: "x" }));
  await assertSucceeds(setDoc(doc(joe.firestore(), "notes/codes"), { text: "n" }));
  await assertSucceeds(setDoc(doc(joe.firestore(), VERSION_PATH), {
    fields: { front_door: "x" },
    contentHash: "abc",
    createdAt: "t",
    expireAt: "e",
    source: "page",
  }));

  const ang = filledEnv.authenticatedContext(ANG_UID);
  await assertCodesAllowed(ang);

  const signedOut = filledEnv.unauthenticatedContext();
  await assertCodesDenied(signedOut);

  const other = filledEnv.authenticatedContext(OTHER_UID);
  await assertCodesDenied(other);
  await assertFails(setDoc(doc(other.firestore(), "property_codes/example_house"), { front_door: "y" }));
  await assertFails(setDoc(doc(other.firestore(), "notes/codes"), { text: "m" }));
});

const PRIVATE_PAGE = "private_pages/stats";
const PRIVATE_PART = "private_pages/stats/parts/0";

async function assertPrivatePagesDenied(context) {
  await assertFails(readDoc(context, PRIVATE_PAGE));
  await assertFails(readDoc(context, PRIVATE_PART));
  await assertFails(readDoc(context, "private_pages/templates"));
  await assertFails(readDoc(context, "private_pages/vendors"));
}

async function assertPrivatePagesReadable(context) {
  await assertSucceeds(readDoc(context, PRIVATE_PAGE));
  await assertSucceeds(readDoc(context, PRIVATE_PART));
  await assertSucceeds(readDoc(context, "private_pages/templates"));
  await assertSucceeds(readDoc(context, "private_pages/vendors"));
}

async function assertPrivatePagesNotWritable(context) {
  const db = context.firestore();
  await assertFails(setDoc(doc(db, PRIVATE_PAGE), { json: "{}" }));
  await assertFails(setDoc(doc(db, PRIVATE_PART), { index: 0, json: "{}" }));
  await assertFails(deleteDoc(doc(db, PRIVATE_PAGE)));
  await assertFails(deleteDoc(doc(db, PRIVATE_PART)));
}

test("signed-out reads and writes of private_pages are denied", async () => {
  const db = emptyEnv.unauthenticatedContext();
  await assertPrivatePagesDenied(db);
  await assertPrivatePagesNotWritable(db);
});

test("non-approved uid cannot read or write private_pages", async () => {
  const other = emptyEnv.authenticatedContext(OTHER_UID);
  await assertPrivatePagesDenied(other);
  await assertPrivatePagesNotWritable(other);
  const emptyJoe = emptyEnv.authenticatedContext(TEST_UID);
  await assertPrivatePagesDenied(emptyJoe);
  await assertPrivatePagesNotWritable(emptyJoe);
});

test("approved codes uid can read private_pages and cannot write them", async () => {
  const ang = emptyEnv.authenticatedContext(ANG_UID);
  await assertPrivatePagesReadable(ang);
  await assertPrivatePagesNotWritable(ang);

  const joe = filledEnv.authenticatedContext(TEST_UID);
  await assertPrivatePagesReadable(joe);
  await assertPrivatePagesNotWritable(joe);

  const signedOut = filledEnv.unauthenticatedContext();
  await assertPrivatePagesDenied(signedOut);

  const other = filledEnv.authenticatedContext(OTHER_UID);
  await assertPrivatePagesDenied(other);
  await assertPrivatePagesNotWritable(other);
});

test("templates/shared signed-out read is denied and an approved user can read it", async () => {
  const signedOut = emptyEnv.unauthenticatedContext();
  await assertFails(readDoc(signedOut, "templates/shared"));
  await assertFails(setDoc(doc(signedOut.firestore(), "templates/shared"), { t0: "x" }));

  const ang = emptyEnv.authenticatedContext(ANG_UID);
  await assertSucceeds(readDoc(ang, "templates/shared"));
  await assertSucceeds(setDoc(doc(ang.firestore(), "templates/shared"), { t0: "x" }));

  const joe = filledEnv.authenticatedContext(TEST_UID);
  await assertSucceeds(readDoc(joe, "templates/shared"));

  const other = emptyEnv.authenticatedContext(OTHER_UID);
  await assertFails(readDoc(other, "templates/shared"));
  await assertFails(setDoc(doc(other.firestore(), "templates/shared"), { t0: "y" }));

  const filledOut = filledEnv.unauthenticatedContext();
  await assertFails(readDoc(filledOut, "templates/shared"));
});

const TASK_COLLECTIONS = ["task_messages", "manual_tasks"];

test("signed-out reads and writes of task_messages and manual_tasks are denied", async () => {
  const db = emptyEnv.unauthenticatedContext();
  for (const name of TASK_COLLECTIONS) {
    await assertFails(readDoc(db, `${name}/example`));
    await assertFails(getDocs(collection(db.firestore(), name)));
    await assertFails(setDoc(doc(db.firestore(), `${name}/example`), { text: "x" }));
    await assertFails(deleteDoc(doc(db.firestore(), `${name}/example`)));
  }
});

test("approved user can read task_messages and manual_tasks", async () => {
  const ang = emptyEnv.authenticatedContext(ANG_UID);
  for (const name of TASK_COLLECTIONS) {
    await assertSucceeds(readDoc(ang, `${name}/example`));
    await assertSucceeds(getDocs(collection(ang.firestore(), name)));
    await assertSucceeds(setDoc(doc(ang.firestore(), `${name}/example`), { text: "x" }));
  }

  const joe = filledEnv.authenticatedContext(TEST_UID);
  await assertSucceeds(readDoc(joe, "task_messages/example"));
  await assertSucceeds(readDoc(joe, "manual_tasks/example"));
  await assertSucceeds(setDoc(doc(joe.firestore(), "manual_tasks/joe"), { notes: "x" }));

  const other = emptyEnv.authenticatedContext(OTHER_UID);
  await assertFails(readDoc(other, "task_messages/example"));
  await assertFails(readDoc(other, "manual_tasks/example"));
  await assertFails(setDoc(doc(other.firestore(), "task_messages/example"), { text: "x" }));
  await assertFails(setDoc(doc(other.firestore(), "manual_tasks/example"), { notes: "x" }));

  const emptyJoe = emptyEnv.authenticatedContext(TEST_UID);
  await assertFails(readDoc(emptyJoe, "task_messages/example"));
  await assertFails(readDoc(emptyJoe, "manual_tasks/example"));
  await assertFails(setDoc(doc(emptyJoe.firestore(), "task_messages/example"), { text: "x" }));

  const signedOutFilled = filledEnv.unauthenticatedContext();
  await assertFails(readDoc(signedOutFilled, "task_messages/example"));
  await assertFails(readDoc(signedOutFilled, "manual_tasks/example"));
});

test("signed-out reads of vendors and stats are denied", async () => {
  const db = emptyEnv.unauthenticatedContext();
  await assertFails(readDoc(db, "vendors/example"));
  await assertFails(getDocs(collection(db.firestore(), "vendors")));
  await assertFails(readDoc(db, "stats/latest"));
  await assertFails(readDoc(db, "stats/monthly_history"));
  await assertFails(setDoc(doc(db.firestore(), "vendors/example"), { name: "x" }));
  await assertFails(setDoc(doc(db.firestore(), "stats/latest"), { json: "{}" }));
  await assertFails(setDoc(doc(db.firestore(), "stats/monthly_history"), { json: "{}" }));
});

function vendorPayload(overrides = {}) {
  return {
    name: "x",
    specialty: "",
    contact: "",
    location: "",
    ...overrides,
  };
}

test("approved codes uid can read vendors and stats, and cannot write stats", async () => {
  const ang = emptyEnv.authenticatedContext(ANG_UID);
  await assertSucceeds(readDoc(ang, "vendors/example"));
  await assertSucceeds(getDocs(collection(ang.firestore(), "vendors")));
  await assertSucceeds(readDoc(ang, "stats/latest"));
  await assertSucceeds(readDoc(ang, "stats/monthly_history"));
  await assertSucceeds(setDoc(doc(ang.firestore(), "vendors/example"), vendorPayload(), { merge: true }));
  await assertSucceeds(deleteDoc(doc(ang.firestore(), "vendors/example")));
  await assertFails(setDoc(doc(ang.firestore(), "stats/latest"), { json: "{}" }));
  await assertFails(setDoc(doc(ang.firestore(), "stats/monthly_history"), { json: "{}" }));
  await assertFails(deleteDoc(doc(ang.firestore(), "stats/latest")));

  const joe = filledEnv.authenticatedContext(TEST_UID);
  await assertSucceeds(readDoc(joe, "vendors/example"));
  await assertSucceeds(readDoc(joe, "stats/latest"));
  await assertSucceeds(readDoc(joe, "stats/monthly_history"));
  await assertSucceeds(setDoc(doc(joe.firestore(), "vendors/joe"), vendorPayload({ name: "y" })));
  await assertFails(setDoc(doc(joe.firestore(), "stats/latest"), { json: "{}" }));

  const other = emptyEnv.authenticatedContext(OTHER_UID);
  await assertFails(readDoc(other, "vendors/example"));
  await assertFails(readDoc(other, "stats/latest"));
  await assertFails(readDoc(other, "stats/monthly_history"));
  await assertFails(setDoc(doc(other.firestore(), "vendors/example"), vendorPayload({ name: "z" })));

  const emptyJoe = emptyEnv.authenticatedContext(TEST_UID);
  await assertFails(readDoc(emptyJoe, "vendors/example"));
  await assertFails(readDoc(emptyJoe, "stats/latest"));
  await assertFails(readDoc(emptyJoe, "stats/monthly_history"));
});

test("vendor writes reject a bad type, an oversize string, or an unexpected key", async () => {
  const ang = emptyEnv.authenticatedContext(ANG_UID);
  const db = ang.firestore();
  await assertFails(setDoc(doc(db, "vendors/bad-type"), vendorPayload({ name: 1 })));
  await assertFails(setDoc(doc(db, "vendors/oversize"), vendorPayload({ contact: "x".repeat(500) })));
  await assertFails(setDoc(doc(db, "vendors/extra"), vendorPayload({ note: "nope" })));
  await assertSucceeds(setDoc(doc(db, "vendors/ok"), vendorPayload({ name: "x".repeat(499) })));
  await assertSucceeds(setDoc(doc(db, "vendors/blank"), {
    name: "",
    specialty: "",
    contact: "",
    location: "",
  }));
});
