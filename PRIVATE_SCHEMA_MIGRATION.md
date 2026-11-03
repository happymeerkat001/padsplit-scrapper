# Private property configuration migration

Real property identities and device schedules are private operational data. Do not commit the private configuration files, copy them into public assets, or publish them in screenshots or logs. The local migration preserves the existing codes presentation schema in a restricted backup outside the repository; it contains property identifiers but no access-code values. Preserve that backup securely.

The access-codes page now starts without embedded properties and reads the `private_configuration/codes_schema` document only after the designated owner signs in. The proposed Firestore rules permit that owner to get this document; all client writes and list operations are denied. Existing code values remain in their separately protected documents. If the schema is unavailable or invalid, the page reports that configuration is required rather than exposing a fallback. This change must not be released before the configuration and rules are coordinated; otherwise the access-codes page will be unavailable.

For a future authorized production migration, review the privately backed-up JSON and the proposed rules first. With properly scoped Firebase Admin credentials, the correct project, and the private file outside the public tree, an operator can explicitly run:

```sh
PADSPLIT_PRIVATE_SCHEMA_ENABLE=1 PADSPLIT_COLLECTION_ONLY=0 \
  python -m padsplit_scraper.private_codes_schema --source /secure/path/codes-schema.json
```

This command is disabled in CI and by default, checks the project, validates the schema, and uses an atomic create that will not overwrite an existing document. It does not provision or rotate access-code values. No production publication or rules deployment was performed as part of this migration. Test the full owner workflow and authorization rules in an isolated environment before arranging the live change.

Thermostat, SmartHome, and property alias configuration also require private local configuration; see the relevant module documentation and examples. The low-temperature alert target is read from ignored `.private-config/temp-alert.json`, or the file named by `PADSPLIT_TEMP_ALERT_CONFIG`; a missing or invalid target disables that alert. Preserve the ignored file securely when moving the automation to another machine. The protected backup and ignored local configuration must be retained for operators before any release.
