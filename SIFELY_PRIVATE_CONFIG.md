# Private Sifely target configuration

The Sifely workflow reads `.private-config/sifely-target.json`, or the path in `PADSPLIT_SIFELY_TARGET_CONFIG`. Keep this file private and outside version control. A fictional example is in `tests/fixtures/sifely-target.json`; configure the actual label, Firestore property document slug, lowercase name tokens, and lowercase exclusion phrases locally. The label is restricted to letters and spaces because it appears in digit-free Discord status messages.

A missing or invalid configuration disables the lock automation, prevents Sifely code-page updates, and blocks leak supply order messages. The lockout path also stops before accessing the Sifely API. An explicitly configured Sifely lock ID still rejects front locks and excluded properties. Verify the local config and property profiles refer to the same document before enabling any live automation. Tests are offline and do not establish live-service readiness.
