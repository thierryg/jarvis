# Security policy

Jarvis controls a physical access point (a garage door) and processes biometric data (faces,
voices). Security issues are handled with priority.

## Supported versions

| Version | Supported |
|---|---|
| 0.9.x | ✅ security fixes |
| < 0.9 | ❌ upgrade to the latest release |

## Reporting a vulnerability

**Do not open a public issue.** Report privately to **Thierry Gayet — thierry.gayet@labworks.fr**
(or through a GitHub private security advisory when the repository is hosted on GitHub), with:

- the affected version or commit, and the component (core, API, web UI, console, MQTT bridge, installer);
- a description, the impact and reproduction steps (a proof of concept is welcome, never against a
  third-party installation);
- whether you want to be credited.

You will receive an acknowledgement within **72 hours**, an assessment within **7 days**, and a fix or a
mitigation plan according to severity (CVSS 3.1): critical ≤ 7 days, high ≤ 30 days, medium ≤ 90 days.
Disclosure is coordinated: please wait for the fix before publishing.

## Security design (summary)

- **Local only**: no cloud dependency at run time; the camera sits on an isolated network segment.
- **Privilege separation**: the web API has no device access (`PrivateDevices=yes`, empty capability
  set, W^X) and can only send a closed list of commands to the core over a `0660` Unix socket; root actions
  (monitoring agents) go through a systemd `.path` unit and a minimal oneshot service.
- **Authentication**: Argon2id password hashes, 256-bit session tokens stored as SHA-256, `HttpOnly` +
  `Secure` + `SameSite=Strict` cookie, `X-Jarvis` anti-CSRF header, login rate limiting, idle and absolute
  session timeouts, every session traced (source IP, sign-in, sign-out, reason).
- **Secrets**: centralized in the `secrets` section, write-only in the UI/API/shell, masked in the audit
  log, never in logs (`repr=False`).
- **Audit**: every administrative action is recorded with its author, source IP and before → after diff,
  in a SHA-256 hash chain (`GET /api/audit/verify`, console option 11) that reveals any altered or deleted entry.
- **Default account**: `admin` / `admin` exists only until the first login; every API except
  `/api/me`, `/api/logout` and `/api/account/password` answers 403 until the password is changed
  (at least 12 characters, not trivial). The local console resets it (option 3).
- **Web front**:
  - nginx with a strict CSP, HSTS, TLS 1.2/1.3 only and `Cache-Control: no-store`;
  - login rate limiting (429);
  - **Anubis** proof-of-work proxy for browsers;
  - AI crawlers, AI agents and search engines refused with 403 (user-agent map and Anubis policy);
  - `robots.txt` `Disallow: /` and `X-Robots-Tag: noindex, nofollow, noarchive…`.
- **TLS**: `jarvis-cert` manages the certificate.
  - **Local mode:** a private CA, name-constrained to local names and private IPv4, with a real
    revocation database and CRL.
  - **Let's Encrypt mode:** DNS-01 by default, so no inbound Internet port.
  - **Automatic renewal:** a timer renews the certificate 30 days before expiry.
  - **Key permissions:** keys are `root 0600`; the Anubis key reaches its `DynamicUser` service
    through `LoadCredential`.
- **Host hardening** (applied by `deploy/ansible`):
  - nftables default-deny inbound (SSH from the admin networks, 80/443 from the LAN only);
  - fail2ban jails `sshd`, `jarvis-login`, `nginx-botsearch` and `recidive`;
  - sshd keys only, no root, no forwarding, post-quantum key exchange, legal banner;
  - sysctl hardening, forbidden kernel modules, no core dumps;
  - auditd rules, AppArmor, unattended security upgrades;
  - sudo `use_pty` with a log file.
- **License plates** (optional, off by default):
  - a plate is **not** an authenticator, since it can be copied; plate-based opening is a
    convenience, bounded by the plate's enabled flag, its expiration date and the linked
    person's access rules;
  - opening requires the door sensor to read "closed", because the single-button motor would
    otherwise close on the car;
  - automatic closing is opt-in (a hazard): it needs the sensor to read "open" and a camera
    scene that stays clear;
  - every read, decision and refusal is in the event log, and registry changes are audited
    with diffs.
- **Supply chain**:
  - pinned SHA-256 of the downloaded binaries (Anubis `.deb`, uv checksum file);
  - CycloneDX SBOM scanned in CI;
  - systemd sandboxing of every service.
- **Known limitation**: no presentation-attack (liveness) detection — see `docs/SOFTWARE.md`, "Known limitations".

## DevSecOps controls

| Control | Tool | Where |
|---|---|---|
| Secret scanning | gitleaks | pre-commit, CI |
| Static analysis (SAST) | ruff, bandit, CodeQL | pre-commit, CI |
| Dependency vulnerabilities (SCA) | pip-audit, Trivy/Grype on the SBOM | CI, `make audit` |
| SBOM | CycloneDX 1.6 (`scripts/jarvis-regen-sbom.sh`) | CI artifact, `docs/sbom/` |
| Container image | Trivy (image + config) | CI |
| Shell scripts | shellcheck | pre-commit, CI |
| Dependency updates | Dependabot (pip, GitHub Actions, Docker) | `.github/dependabot.yml` |
