<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : INSTALL.md
Purpose : Step-by-step installation: Ubuntu 26.04 USB key (balenaEtcher) to a verified Jarvis
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Installing Jarvis

This procedure starts from a blank mini-PC and ends with a deployed, hardened and verified Jarvis.

- **Time:** about one hour, including 30 to 45 minutes of automatic downloads.
- **Before you start:** check the hardware and software against [REQUIREMENTS.md](REQUIREMENTS.md).
- **Playbook reference:** everything about the Ansible deployment (variables, TLS modes, roles,
  troubleshooting) is in [deploy/ansible/README.md](deploy/ansible/README.md).

| Step | Where | Time |
|---|---|---|
| 1. Prepare the USB key (balenaEtcher) | admin workstation | 10 min |
| 2. Set up the mini-PC firmware | mini-PC | 5 min |
| 3. Install Ubuntu Server 26.04 LTS | mini-PC | 15 min |
| 4. Set up SSH key access | admin workstation | 5 min |
| 5. Deploy Jarvis with Ansible | admin workstation | 30 to 45 min |
| 6. First steps (CA, password, camera) | browser | 10 min |

> **DANGER — door relay.** The relay **only** switches the dry contact of terminal F (external
> push-button input) of the Novomatic 200. **Never** connect 230 V to the relay or to the mini-PC.
> The cabling manual describes the wiring.

---

## 1. Prepare the installation USB key

1. **Download** Ubuntu Server 26.04 LTS (amd64) from https://ubuntu.com/download/server, for example
   `ubuntu-26.04.1-live-server-amd64.iso`.
2. **Verify** the image (integrity and authenticity):
   ```bash
   cd ~/Downloads
   wget https://releases.ubuntu.com/26.04/SHA256SUMS https://releases.ubuntu.com/26.04/SHA256SUMS.gpg
   gpg --keyid-format long --keyserver hkp://keyserver.ubuntu.com --recv-keys 0x843938DF228D22F7B3742BC0D94AA3F0EFE21092
   gpg --verify SHA256SUMS.gpg SHA256SUMS          # "Good signature from Ubuntu CD Image Automatic Signing Key"
   sha256sum -c SHA256SUMS --ignore-missing        # "ubuntu-26.04.1-live-server-amd64.iso: OK"
   ```
3. **Install balenaEtcher** (https://etcher.balena.io): AppImage on Linux, or the installer on Windows/macOS.
4. **Write the image to the key:**
   1. Plug in a USB key of 4 GB or more. **Its content will be erased.**
   2. Start balenaEtcher.
   3. **Flash from file**: select the ISO.
   4. **Select target**: select the USB key. Check its name and size so you do not erase another disk.
   5. **Flash!**, then wait for the automatic validation to finish ("Flash Complete!").
   6. Eject the key.

## 2. Set up the ThinkCentre M73 Tiny firmware

Power on the mini-PC and press **F1** to enter the setup utility.

| Menu | Setting |
|---|---|
| Security › Secure Boot | **Enabled** |
| Security › Password | set a **Supervisor password** (keep it in a safe place) |
| Power › After Power Loss | **Power On** (automatic restart after a power cut) |
| Devices › USB | **Enabled** (camera network adapter, microphone, relay) |
| Startup › Boot Priority | USB, then the internal disk; or press **F12** at power-on to pick the key once |

Save with **F10**, then boot from the key (**F12**, "USB HDD").

## 3. Install Ubuntu Server 26.04 LTS

Answer the installer as listed in
[REQUIREMENTS.md §1](REQUIREMENTS.md#answers-in-the-ubuntu-server-installer). The key points:

1. **Language and keyboard:** your choice, for example English with your keyboard layout.
2. **Type:** *Ubuntu Server* (not the minimized one).
3. **Network:** cable on the **built-in** RJ45 port (LAN), DHCP. Write down the address shown,
   for example `192.168.1.20`, and reserve it in your router.
4. **Storage:** *Use an entire disk* + *Set up this disk as an LVM group*.
5. **Profile:**
   - server name: `jarvis`;
   - user: `admin`;
   - a strong password.
6. **SSH:** tick **Install OpenSSH server**. You can import your public key from GitHub.
7. **Snaps:** select none.
8. **Finish:** remove the key when asked, and reboot.

**Check:** the console shows `jarvis login:`. Log in once as `admin` to confirm the password,
then run `ip -4 addr` to confirm the address.

## 4. Set up SSH key access

Run these commands on the **admin workstation** (Linux, macOS or WSL2):

```bash
ssh-keygen -t ed25519 -C "admin@jarvis"            # only if you have no key yet
ssh-copy-id admin@192.168.1.20                     # asks for the admin password one last time
ssh admin@192.168.1.20 'sudo -v && echo SUDO-OK'   # must print SUDO-OK
```

> **CAUTION.** The deployment **disables SSH password logins**. There is no lockout risk: the
> preflight refuses to continue when no key is installed, or when your workstation connects from
> a network that is not listed in `jarvis_admin_networks`.

## 5. Deploy Jarvis with Ansible

```bash
# 5.1 Get the project
git clone <repository-url> jarvis && cd jarvis/deploy/ansible
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv, if missing (provides Ansible through uvx)

# 5.2 Describe the target: pick ONE certificate mode
cp inventory/hosts.example.yml inventory/hosts.yml                  # local certificate (LAN, private CA)
# cp inventory/hosts.public-domain.example.yml inventory/hosts.yml  # public domain (Let's Encrypt)
$EDITOR inventory/hosts.yml                         # ansible_host: 192.168.1.20, ansible_user: admin

# 5.3 (Optional) secrets: camera, MQTT, Prometheus token, DNS API credentials
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
$EDITOR group_vars/all/vault.yml && ansible-vault encrypt group_vars/all/vault.yml

# 5.4 Check, then deploy
./jarvis-deploy.sh ping                                    # SSH connection + sudo
./jarvis-deploy.sh preflight                               # checks only, changes nothing
./jarvis-deploy.sh deploy                                  # add -K if sudo asks for a password, -J with vault.yml
```

**Certificate modes:**
- **Local mode** (default): a private Jarvis CA, for a LAN-only installation with no domain name.
  The CA must be imported once on each client device.
- **Public-domain mode:** a Let's Encrypt certificate for a domain you own, trusted everywhere.
  It uses the **DNS-01** challenge, so no port is opened on the Internet.

Both modes are detailed in [deploy/ansible/README.md](deploy/ansible/README.md).

| Phase | What happens |
|---|---|
| **Preflight** | Ansible ≥ 2.16, complete source tree, Ubuntu 26.04 x86_64, systemd and cgroup v2, RAM, CPU, disk, AVX2, sudo, SSH key, admin network, TLS settings, Internet access, free ports 80/443, clock, camera |
| Base | updates, packages, Intel VAAPI drivers, `jarvis` host name, `jarvis.local` mDNS, time zone |
| Hardening | sysctl, forbidden kernel modules, no core dumps, journald, auditd, AppArmor, automatic security updates, sudo, password quality, umask, cron, Ctrl+Alt+Del |
| Firewall | nftables: inbound denied by default, SSH from the admin networks, 80/443 from the LAN |
| SSH | keys only, no root, modern (post-quantum) algorithms, **legal banner before login** |
| Jarvis | service account, uv, Python 3.11, dependencies, package, configuration, secrets, systemd units, commands |
| TLS | local-CA or Let's Encrypt certificate, **automatic renewal** |
| Anubis, nginx | anti-bot, anti-AI-crawler and anti-search-engine protection; TLS reverse proxy with security headers |
| fail2ban | SSH, web login, scanners, repeat offenders |
| Models, MOTD, services | AI models; **MOTD** with the JARVIS ASCII art, metrics and services; start-up |
| **Postflight** | services active and enabled, ports listening (API on loopback only), HTTP → HTTPS redirect, TLS 1.2/1.3 only, HTTPS headers, robots blocked, certificate valid and served, **renewal drill**, **admin/admin login with forced change**, firewall, fail2ban, sshd, sysctl, AppArmor, MOTD |

The run ends with a summary: the URL, the certificate state and, in local mode, where the CA file is.
The deployment is **idempotent**: run `./jarvis-deploy.sh deploy` again to apply a change or a project
update, and `./jarvis-deploy.sh postflight` to repeat only the checks.

> **Alternative without Ansible**, directly on the mini-PC:
> `git clone … && cd jarvis && sudo ./scripts/jarvis-install.sh`.
> It skips the hardening, firewall, fail2ban, banner and MOTD.

## 6. First steps

1. **Trust the local CA** (local mode only, once per client device). The file is
   `deploy/ansible/artifacts/<host>/jarvis-ca.crt`, or run `ssh admin@jarvis.local sudo jarvis-cert export-ca`.
   - **Check:** compare the SHA-256 fingerprint printed by `jarvis-cert export-ca`.
   - **Firefox:** Settings › Privacy & Security › Certificates › Authorities › Import.
   - **Windows:** `certutil -addstore Root jarvis-ca.crt` (administrator prompt).
   - **macOS:** Keychain Access › System › import, then "Always Trust".
   - **Android / iOS:** install the certificate, then enable it (iOS: Settings › General › About ›
     Certificate Trust Settings).
2. **Open https://jarvis.local/**, the mini-PC IP address, or your public name in Let's Encrypt mode.
3. **Log in** with `admin` / `admin`. A **new password** is required immediately (at least 12
   characters, different from the user name). If it is lost: `jarvis-console`, option 3.
4. **Settings:**
   - camera: RTSP URL and credentials;
   - language;
   - idle logout delay;
   - recordings;
   - integrations (MQTT / Home Assistant);
   - monitoring.
5. **Enroll** the authorized people (face, voice) and define their access schedules.
6. **Test the relay** with no load before connecting it to terminal F: `jarvis test-hardware`.

### SSH login

```
********************************************************************************
*                  JARVIS - RESTRICTED AND SECURED ACCESS SYSTEM               *
...
```

After the banner, the MOTD shows:
- the JARVIS ASCII art;
- CPU, memory, disk, network and GPU;
- the detected hardware support;
- the state and consumption of every service;
- the certificate, the firewall and fail2ban;
- the useful commands.

| Command | Purpose |
|---|---|
| `jarvis-console` | local console (menu), admin password reset |
| `jarvis` | administration shell (`help`) |
| `jarvis-cert status` | certificate: creation, expiry, remaining time, revocation |
| `jarvis-motd` | show the dashboard again |
| `jarvis-hwinfo` (`scripts/jarvis-hwinfo.sh`) | hardware inventory and suitability verdict |
| `systemctl status jarvis.target` | service state |

## 7. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Preflight fails on "authorized key" | no SSH key installed | `ssh-copy-id admin@<ip>` |
| Preflight fails on "outside jarvis_admin_networks" | workstation outside the allowed networks (VPN…) | add its network to `jarvis_admin_networks` |
| Preflight fails on the URLs | no Internet access, or a proxy | fix the network or DNS; or use a mirror + `jarvis_offline: true` |
| Browser warning | local CA not imported | §6.1 |
| `jarvis-core` keeps restarting | camera unreachable, models missing | `journalctl -u jarvis-core -f`; `jarvis check-config` |
| No SSH access after a change | port or networks changed | local console (screen and keyboard), `sudo nft list ruleset` |
