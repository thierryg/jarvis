<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : deploy/ansible/README.md
Purpose : End-to-end Ansible deployment guide: before, during and after, both TLS modes
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Jarvis Ansible deployment

`deploy/ansible` turns a freshly installed **Ubuntu Server 26.04 LTS** into a complete,
hardened and verified Jarvis appliance, in one command:

- **Preflight:** sanity checks before any change.
- **Installation:** packages, Python environment, application, models.
- **Hardening:** kernel, SSH, firewall, fail2ban, auditd, AppArmor, automatic updates.
- **Web front and TLS:** Anubis anti-bot proxy, nginx, and a local or public TLS certificate.
- **Console experience:** SSH legal banner and dashboard MOTD.
- **Postflight:** checks after the deployment.

The operator-level procedure, from the USB key to the first login, is [INSTALL.md](../../INSTALL.md).
This document is the reference for the playbook itself.

```
deploy/ansible/
├── jarvis-deploy.sh                       wrapper: deps, ping, preflight, check, deploy, postflight, lint
├── site.yml                        main playbook (role order matters, see §5)
├── ansible.cfg                     settings (pipelining, fact cache, force_handlers, profiling)
├── requirements.yml                collections: ansible.posix, community.general
├── inventory/
│   ├── hosts.example.yml                 LOCAL certificate example (LAN only, private CA)
│   └── hosts.public-domain.example.yml   PUBLIC domain example (Let's Encrypt, DNS-01)
├── group_vars/all/
│   ├── main.yml                    every tunable, documented, with its default
│   └── vault.example.yml           template of the encrypted secrets (vault.yml, not versioned)
├── roles/                          preflight base hardening firewall ssh jarvis tls anubis nginx
│                                   fail2ban models monitoring motd services postflight
├── artifacts/<host>/jarvis-ca.crt  local CA fetched after the deployment (not versioned)
└── tests/                          end-to-end test against an Ubuntu 26.04 systemd container
```

---

## 1. Before the deployment

### 1.1 Target

| Requirement | How to check |
|---|---|
| Ubuntu **Server 26.04 LTS**, x86_64, fresh install with **OpenSSH server** | `lsb_release -d; uname -m` |
| Administrator account (e.g. `admin`) in the `sudo` group | `sudo -v` |
| Fixed or DHCP-reserved LAN address | router |
| At least 3.5 GB RAM, 2 CPU threads, 15 GB free on `/var` and `/opt` (8 GB and 4 cores recommended) | `scripts/jarvis-hwinfo.sh` |
| Internet access during the deployment (apt, PyPI, PyTorch, GitHub, model sites) | `curl -I https://pypi.org` |
| Correct clock (NTP) | `timedatectl` |

Hardware details and the installer answers are in [REQUIREMENTS.md](../../REQUIREMENTS.md).

### 1.2 Admin workstation

```bash
# uv provides ansible-core through uvx when it is missing or older than 2.16
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install rsync openssh-client        # or the macOS / WSL2 equivalent

# SSH key access to the target (password logins are disabled by the deployment)
ssh-keygen -t ed25519 -C "admin@jarvis"
ssh-copy-id admin@192.168.1.20
ssh admin@192.168.1.20 'sudo -v && echo SUDO-OK'
```

### 1.3 Choose the certificate mode

The same playbook deploys either mode: set `jarvis_tls_mode`. Switching later is supported
(re-run the deployment); the certificate is re-issued automatically.

| | **`local`** (default) | **`letsencrypt`** |
|---|---|---|
| Use case | LAN only, no domain name | you own a public domain (e.g. `jarvis.example.org`) |
| Issuer | private **Jarvis Local CA** (ECDSA P-384, 10 years), name-constrained to local names and private IPv4 | Let's Encrypt (ECDSA P-256, 90 days) |
| Server certificate | ECDSA P-256, 397 days | ECDSA P-256, 90 days |
| Names | `jarvis.local`, `jarvis`, LAN IPs (`jarvis_tls_names`, `jarvis_tls_ips`) | `jarvis_le_domain` |
| Client trust | import `artifacts/<host>/jarvis-ca.crt` once per device | trusted by every browser |
| Inbound Internet port | none | **none with DNS-01** (default); port 80 with HTTP-01 |
| Name resolution | mDNS `jarvis.local`, or a local DNS entry | local DNS entry: domain → LAN address of the mini-PC |
| Needs | nothing | email, DNS provider API token (DNS-01) |
| Renewal | `jarvis-cert-renew.timer`, 30 days before expiry | same timer (`certbot renew`) |
| Revocation | local CA database + CRL | Let's Encrypt (CRL / OCSP) |

> **Why DNS-01?** Let's Encrypt validates the domain through a TXT record created with your DNS
> provider API. No port is ever opened on the Internet, which respects the rule that Jarvis
> stays LAN-only. HTTP-01 requires port 80 reachable from the Internet; avoid it.

### 1.4 Write the inventory

**Local certificate** (`cp inventory/hosts.example.yml inventory/hosts.yml`):

```yaml
all:
  children:
    jarvis:
      hosts:
        jarvis-01:
          ansible_host: 192.168.1.20
          ansible_user: admin
          jarvis_tls_mode: local
          jarvis_tls_names: [jarvis.local, jarvis]
          jarvis_tls_ips: [192.168.1.20]
```

**Public domain** (`cp inventory/hosts.public-domain.example.yml inventory/hosts.yml`):

```yaml
all:
  children:
    jarvis:
      hosts:
        jarvis-01:
          ansible_host: 192.168.1.20
          ansible_user: admin
          jarvis_tls_mode: letsencrypt
          jarvis_le_domain: jarvis.example.org
          jarvis_le_email: admin@example.org
          jarvis_le_challenge: dns
          jarvis_le_dns_plugin: cloudflare     # any python3-certbot-dns-<name>: ovh, gandi, rfc2136, route53...
          jarvis_le_staging: true              # first runs on the staging CA, then false
```

For the public domain, also:
1. Create a DNS API token restricted to the zone (e.g. Cloudflare "Zone.DNS:Edit").
2. Put it in the vault (§1.5) as `jarvis_le_dns_credentials`.
3. Add a **local** DNS record `jarvis.example.org → 192.168.1.20` (router, Pi-hole, Unbound…),
   so that LAN clients reach the mini-PC. The preflight shows what the name resolves to.

### 1.5 Settings and secrets

- **Settings:** every tunable is documented in [group_vars/all/main.yml](group_vars/all/main.yml).
  Override it per host in the inventory, or per run with `-e`. The ones to review:

  | Variable | Default | Meaning |
  |---|---|---|
  | `jarvis_lan_networks` | RFC 1918 + ULA/link-local | clients allowed to reach the web UI (80/443); tighten to your LAN |
  | `jarvis_admin_networks` | = LAN networks | sources allowed to open SSH sessions |
  | `jarvis_hostname` | `jarvis` | host name, published as `jarvis.local` (mDNS) |
  | `jarvis_ssh_allow_users` | the Ansible user | only these accounts may log in over SSH |
  | `jarvis_anubis_enabled` | `true` | anti-bot proxy in front of the API |
  | `jarvis_install_models` | `true` | download and export the AI models (required by `jarvis-core`) |
  | `jarvis_with_console` | `true` | pfSense-style console on tty1 |
  | `jarvis_with_monitoring` | `false` | install node_exporter (switched on from the web UI) |
  | `jarvis_monitoring_server` | empty | address allowed to scrape `:9100` |
  | `jarvis_unattended_reboot` | `false` | automatic reboot after security updates |
  | `jarvis_env` | `{}` | non-secret settings written to `/etc/jarvis/jarvis.env` (`JARVIS__SECTION__KEY`) |

- **Secrets** (camera password, MQTT, Prometheus token, DNS API token) never go in `main.yml`:

  ```bash
  cp group_vars/all/vault.example.yml group_vars/all/vault.yml
  $EDITOR group_vars/all/vault.yml
  ansible-vault encrypt group_vars/all/vault.yml     # then use -J / --ask-vault-pass
  ```

  `vault.yml` is ignored by git. Its values are written to `/etc/jarvis/jarvis.env`
  (`root:jarvis 0640`) with `no_log`, so they never appear in the output.

### 1.6 Dry checks

```bash
cd deploy/ansible
./jarvis-deploy.sh lint          # syntax check + ansible-lint (production profile) + yamllint, no target needed
./jarvis-deploy.sh ping          # SSH + Python + sudo on the target
./jarvis-deploy.sh preflight     # every blocking check, changes nothing
./jarvis-deploy.sh check         # dry run of the whole playbook (--check --diff), optional
```

---

## 2. During the deployment

```bash
./jarvis-deploy.sh deploy                 # add -K if sudo asks for a password, -J when vault.yml is encrypted
./jarvis-deploy.sh deploy -t tls,nginx    # only some roles (tags)
./jarvis-deploy.sh deploy -e jarvis_le_staging=false -v
```

### 2.1 What runs, in order

| # | Role | Summary |
|---|---|---|
| 1 | **preflight** | blocking checks (§2.2); records whether this is a first installation |
| 2 | base | apt update/upgrade, runtime and build packages, Intel VAAPI drivers (`i965`, `iHD`), host name, `/etc/hosts`, mDNS, time zone, NTP |
| 3 | hardening | sysctl, forbidden kernel modules, no core dumps, persistent bounded journal, auditd rules, AppArmor, unattended-upgrades, sudo `use_pty` + log, pwquality, umask 027, cron permissions, Ctrl+Alt+Del masked, clear-text clients removed |
| 4 | firewall | ufw removed; nftables table `inet jarvis_filter`: inbound drop, SSH from admin networks (rate-limited), 80/443 from LAN, mDNS, DHCP, ICMP essentials, optional node_exporter; ruleset validated with `nft -c` before it is installed |
| 5 | ssh | legal banner `/etc/issue.net` + `/etc/issue`; `01-jarvis-hardening.conf`: keys only, no root, no forwarding, idle timeout, verbose logs; ML-KEM/NTRU/X25519 key exchange, AEAD ciphers, filtered against the installed OpenSSH; rolled back if `sshd -t` fails |
| 6 | jarvis | `jarvis` account and device groups, directories, pinned `uv` (checksum), sources (two-stage rsync), Python 3.11 venv, CPU PyTorch, insightface, `jarvis-home` package, version check, config and secrets, tmpfiles (`/run/jarvis`), udev, systemd units, commands |
| 7 | tls | `jarvis-cert` status, then issue only when missing, unusable, in another mode, when names changed, or when switching Let's Encrypt staging/production; renewal timer; local CA fetched to `artifacts/` |
| 8 | anubis | official `.deb` (pinned SHA-256), bot policy, instance env, signing key (root 0600, handed over with `LoadCredential`), hardened drop-in |
| 9 | nginx | default site removed, ACME webroot, log files, host snippets (upstream Anubis/API, server names), site, `nginx -t`, reload |
| 10 | fail2ban | jails `sshd` (journal), `jarvis-login` (401/429 on `/api/login`), `nginx-botsearch`, `recidive`; nftables bans |
| 11 | models | `jarvis setup-models` as the `jarvis` account, once |
| 12 | monitoring | optional node_exporter, left stopped (enabled from the web UI) |
| 13 | motd | neofetch (fastfetch when neofetch is not packaged), JARVIS ASCII art, `jarvis-motd`, update-motd hook, stock parts silenced |
| 14 | services | enable and start `jarvis.target`, API, core, timers, path unit, console |
| 15 | **postflight** | verification (§2.3) and summary |

**The run is idempotent.**
- **Re-running:** a second run only reports the changes you made, plus the renewal drill, which
  is designed to change the certificate.
- **Handlers:** `force_handlers` makes a failed run still apply the reloads and restarts that were due.

### 2.2 Preflight checks (nothing is changed before they pass)

| Area | Checks |
|---|---|
| Controller | ansible-core ≥ 2.16; complete source tree; deployed version (`jarvis/VERSION`) |
| System | Ubuntu 26.04 (24.04 only if allowed), x86_64, systemd, cgroup v2 |
| Resources | RAM, CPU threads, free disk on `/var` and `/opt`; AVX2 (warning) |
| Access | sudo gives root; **lockout protection**: an SSH key exists before passwords are disabled, the admin is in `AllowUsers`, and the controller address is inside `jarvis_admin_networks` |
| TLS | valid mode; local names only in local mode; Let's Encrypt domain, email, challenge, DNS plugin and credentials; domain resolution; warning for HTTP-01 |
| Network | reachability of apt, PyPI, PyTorch and GitHub (unless `jarvis_offline`); ports 80/443 not held by another web server; NTP sync (warning); camera RTSP port (warning) |

### 2.3 Postflight checks (the deployment fails if one fails)

| # | Check |
|---|---|
| 1 | every unit **active** and **enabled** (nginx, API, core, Anubis, fail2ban, nftables, ssh, auditd, timers); installed version = `jarvis/VERSION` |
| 2 | ports 80, 443, 8000, 8923 listening; **8000/8923/9091 bound to loopback only** |
| 3 | `http://` answers **301** to `https://<same host>/<same path>` |
| 4 | TLS 1.2 and 1.3 accepted, TLS 1.1 refused; certificate verified by curl; headers present: HSTS, nosniff, X-Frame-Options DENY, Referrer-Policy, CSP, Permissions-Policy, X-Robots-Tag noindex, Cache-Control no-store; no server version disclosed; `robots.txt` = `Disallow: /`; GPTBot, ClaudeBot, Googlebot, bingbot, CCBot, PerplexityBot → **403**; browsers get the Anubis challenge |
| 5 | `jarvis-cert status`: **VALID**, key matches, **served by nginx**; renewal timer scheduled; **renewal drill** (local mode): forced renewal, new serial served |
| 6 | first installation: **login `admin` / `admin` works**, `must_change_password` is true, other APIs answer 403 until the password is changed |
| 7 | nftables `jarvis_filter` loaded with policy drop; fail2ban jails active; the login filter matches a sample 401 line |
| 8 | `sshd -T`: no root, no password (unless allowed), no forwarding, banner; sysctl hardening; AppArmor enabled |
| 9 | the MOTD renders |

Run them alone at any time: `./jarvis-deploy.sh postflight`.

---

## 3. After the deployment

1. **Read the summary.** It shows the URL, the certificate state and days left, and in local
   mode where the CA file is.
2. **Local mode:** import `artifacts/<host>/jarvis-ca.crt` on each client, and compare the
   fingerprint shown by `ssh admin@jarvis.local sudo jarvis-cert export-ca`. The per-OS steps
   are in [INSTALL.md §6](../../INSTALL.md#6-first-steps).
3. **Public domain, first run on staging:** set `jarvis_le_staging: false` and re-run
   `./jarvis-deploy.sh deploy -t tls,nginx`. The certificate is re-issued from the production CA.
4. **Log in** at `https://jarvis.local/` (or your domain) with `admin` / `admin`, then set the
   **new password** that is required immediately.
5. **Configure** the camera, languages, recordings and integrations in **Settings**. Enroll
   people. Test the relay with no load: `jarvis test-hardware`.
6. **Check over SSH:**
   - the legal banner is shown before login, and the dashboard MOTD after it;
   - `jarvis-cert status` shows the certificate: creation date, expiry date, remaining time
     and revocation state;
   - `systemctl list-timers 'jarvis-*'` lists the backup and certificate-renewal timers;
   - `sudo fail2ban-client status` and `sudo nft list table inet jarvis_filter` show the
     security layers.
7. **Keep the controller side safe:** `inventory/hosts.yml`, `group_vars/all/vault.yml`
   (encrypted) and `artifacts/` stay on the admin workstation and out of git.

### 3.1 Day-2 operations

| Task | Command |
|---|---|
| Update Jarvis (new `jarvis/VERSION`) | `git pull && ./jarvis-deploy.sh deploy` (sources are re-synced and the package is reinstalled) |
| Re-check everything | `./jarvis-deploy.sh postflight` |
| Change a setting | edit the inventory or `main.yml`, then `./jarvis-deploy.sh deploy -t <role>` |
| Switch TLS mode | set `jarvis_tls_mode`, then `./jarvis-deploy.sh deploy -t preflight,tls,anubis,nginx,postflight` |
| Rotate the local CA | `ssh … sudo jarvis-cert issue --mode local --new-ca`, then re-import the CA on the clients |
| Revoke the certificate | `ssh … sudo jarvis-cert revoke --reason keyCompromise`, then `sudo jarvis-cert renew --force` |
| Reset the web admin password | on the mini-PC: `jarvis-console`, option 3 |

### 3.2 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ansible-core >= 2.16 is required` | old distribution Ansible | use `./jarvis-deploy.sh`: it falls back to `uvx` |
| `has no authorized key` | password login would be disabled with no key | `ssh-copy-id`, or set `jarvis_ssh_authorized_keys` |
| `outside jarvis_admin_networks` | controller on another network (VPN) | add its prefix to `jarvis_admin_networks` |
| `Let's Encrypt mode needs…` | incomplete public-domain settings | fill in `jarvis_le_*` and the vault token |
| certbot fails (DNS-01) | token scope, wrong plugin, DNS propagation | check the token, `jarvis_le_dns_plugin`, `/var/log/letsencrypt/letsencrypt.log`; use staging while testing |
| postflight: header missing | nginx site edited by hand | redeploy the nginx role: `-t nginx,postflight` |
| postflight: unit not active | e.g. camera unreachable for `jarvis-core` | `journalctl -u <unit> -e` on the target |
| `failed to validate` (nftables) | invalid extra rule | fix `jarvis_firewall_extra_rules`; the previous ruleset stays active |

---

## 4. Testing the playbook

```bash
tests/jarvis-container-test.sh            # Ubuntu 26.04 systemd container over SSH, deploy + idempotence re-run
tests/jarvis-container-test.sh --models   # also download the models and start jarvis-core (slow)
```

The container reproduces a fresh server install:
- systemd as PID 1, OpenSSH with socket activation;
- an `admin` sudoer;
- the cloud-init drop-in that enables password logins.

Tasks that cannot work in a container (sysctl, udev, AppArmor, auditd) are detected and skipped;
everything else runs for real, including the postflight.

## 5. Design notes

- **Role order:**
  - the firewall opens the SSH port before sshd is reconfigured;
  - nginx creates its log files before fail2ban watches them;
  - the services start once the models exist.
- **Single sources, no duplication:**
  - units, nginx site, Anubis policy, MOTD and scripts are copied from the repository (`deploy/`, `scripts/`);
  - only host-specific values are templated (nginx snippets, sshd drop-in, nftables, fail2ban, sysctl);
  - the version comes from `jarvis/VERSION`.
- **Safe changes:**
  - `nft -c`, `visudo -cf` and `sshd -t` validate each configuration before it is used;
  - the sshd drop-in is rolled back if the full configuration is invalid.
- **No secrets in logs:** vault values are written with `no_log`; the Anubis key and the TLS keys
  never leave the target.
