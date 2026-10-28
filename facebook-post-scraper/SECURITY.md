# Security and login material

The repository must never contain real Facebook cookies, passwords, browser profile databases, API tokens, personal environment files, scraped media or private live-run records. Test cookie values are synthetic and exist only inside unit-test code. Persisted Facebook GraphQL document IDs in source are operation identifiers, not login credentials or API keys.

Use your own Facebook login. Prefer direct local browser-profile reading when possible, or keep your Netscape cookie file outside the checkout. Cookie files are equivalent to login credentials: restrict access, never upload them, and never include them in screenshots, logs or issue reports. The ignore rules help prevent common accidental additions; they cannot recognize every possible secret filename or undo a secret already committed.

Cookie-file loading filters for Facebook domains and nonexpired login cookies. It reads the file without saving it. Facebook cookies are not forwarded to photo CDN requests. The tool’s archive contains business/page data, not the cookie jar or browser database. Error messages for malformed cookie files deliberately omit the file contents. Always review output before sharing it, since source business details and reviews can contain personal information.

If you exposed a session, sign out/revoke the affected Facebook sessions and replace your cookies. Deleting a posted file or Git commit alone does not invalidate a stolen session.

To report a security issue, provide a minimal reproduction with synthetic data and redacted errors. Do not put a working cookie file or account details in a public GitHub issue. If GitHub private vulnerability reporting is enabled for the repository, use its Security tab for sensitive reports.
