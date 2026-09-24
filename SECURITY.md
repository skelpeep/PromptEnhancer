# Security

## Data handling

The application sends selected text to the API service configured by the user.
Choose a trusted provider and use HTTPS for remote services. Provider processing,
retention and billing policies apply to those requests.

The desktop application stores settings and history under
`%APPDATA%\PromptEnhancer\`. API keys use Windows DPAPI under the current user;
history contains plain text. CLI and proxy configurations can contain plain-text
credentials in `config.env` or environment variables. These files and runtime
logs are excluded from version control by default.

DPAPI does not protect against software already running as the same user. Do not
attach configuration files, API keys, private prompts or unredacted logs to issues.

## Reporting a vulnerability

If the repository's Security tab offers **Report a vulnerability**, use its
private reporting flow. If it is unavailable, open an issue requesting a private
contact channel without including exploit details, credentials or user data.

Include the affected version, a minimal reproduction using sample data, and the
impact. Security fixes target the latest release; there is no guaranteed response
time or maintenance commitment for older versions.

## Release integrity

Release archives include a SHA256 checksum file. Verify downloads against the
checksum shown on the repository's Release page. A matching checksum verifies
consistency with that published file; it is not a code signature or an independent
guarantee of safety. Current Windows builds do not include a code-signing certificate.
