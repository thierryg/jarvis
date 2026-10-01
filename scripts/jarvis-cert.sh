#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : scripts/jarvis-cert.sh
# Purpose : TLS certificate manager: local CA or Let's Encrypt; issue, renew, status, revoke
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Manages the certificate served by nginx at the stable paths
#   /etc/jarvis/tls/jarvis.crt  (certificate + chain)   /etc/jarvis/tls/jarvis.key
# in one of two modes, remembered in /etc/jarvis/tls/jarvis-cert.conf:
#
#   local        LAN only, no public domain needed. A private "Jarvis Local CA"
#                (ECDSA P-384, 10 years, name-constrained to local names and
#                private IPv4 ranges) signs a server certificate (ECDSA P-256,
#                397 days) for .local/.lan/.home.arpa names and LAN IPs. Import
#                the CA once on the client devices (see "export-ca"): no more
#                browser warnings. Revocation uses a real CA database and CRL.
#   letsencrypt  Public domain name. certbot obtains an ECDSA certificate; the
#                default DNS-01 challenge needs NO inbound port open on the
#                Internet (Jarvis must stay LAN-only); HTTP-01 is available but
#                requires port 80 reachable from the Internet.
#
# Usage:
#   jarvis-cert issue --mode local [--name NAME]... [--ip IPV4]... [--days N] [--new-ca]
#   jarvis-cert issue --mode letsencrypt --domain FQDN --email ADDR
#                     [--challenge dns|http] [--dns-plugin NAME --dns-credentials FILE]
#                     [--staging] [--install-deps]
#   jarvis-cert renew [--force] [--days-before N]
#   jarvis-cert status [--cert FILE] [--warn-days N] [--remote HOST[:PORT]] [--json] [--no-revocation]
#   jarvis-cert revoke [--reason unspecified|keyCompromise|superseded|cessationOfOperation] [--yes]
#   jarvis-cert export-ca [--out FILE]
#   jarvis-cert deploy-hook          (internal: certbot --deploy-hook)
#   jarvis-cert --help | --version
#
# Global options:
#   --tls-dir DIR   certificate directory (default /etc/jarvis/tls, env JARVIS_TLS_DIR)
#   --no-reload     do not reload nginx after a change (env JARVIS_CERT_NO_RELOAD=1)
#   --yes           answer yes to confirmations (env ASSUME_YES=1)
#   --no-color      disable colors (also NO_COLOR, JARVIS_COLOR=never)
#
# Examples:
#   sudo jarvis-cert issue --mode local --name jarvis.local --ip 192.168.1.20
#   sudo jarvis-cert issue --mode letsencrypt --domain jarvis.example.org \
#        --email admin@example.org --dns-plugin cloudflare --dns-credentials /root/.cf.ini
#   jarvis-cert status                  # dates, remaining time, revocation, chain
#   jarvis-cert status --json --remote 127.0.0.1:443
#
# Prerequisites: bash >= 4.4, openssl >= 3.0, GNU coreutils; curl for revocation
# checks of public certificates; certbot (+ DNS plugin) for Let's Encrypt.
# Root is required for any change in /etc/jarvis/tls (status works read-only).
#
# Exit codes:
#   0  success / certificate valid
#   1  runtime failure (openssl, certbot, nginx reload...)
#   2  usage error
#   3  root privileges required
#   4  missing prerequisite (command, certificate, CA)
#   5  invalid configuration or option value
#   6  network failure (ACME, CRL/OCSP download)
#   7  certificate unusable: expired, not yet valid, revoked, key mismatch, bad chain
#   8  certificate valid but expires within --warn-days (default 30)
#   130 interrupted

# --- bootstrap: shared library (checkout: scripts/lib, installed: /usr/local/lib/jarvis) ---
_self="$(readlink -f -- "${BASH_SOURCE[0]}")"
for _lib in "$(dirname -- "$_self")/lib/jarvis-common.sh" /usr/local/lib/jarvis/jarvis-common.sh; do
  # shellcheck source=lib/jarvis-common.sh
  [[ -r "$_lib" ]] && { . "$_lib"; break; }
done
[[ -n "${_JARVIS_COMMON_SH:-}" ]] || { echo "jarvis-cert: jarvis-common.sh library not found" >&2; exit 4; }

# --- defaults -----------------------------------------------------------------
TLS_DIR="${JARVIS_TLS_DIR:-/etc/jarvis/tls}"
NO_RELOAD="${JARVIS_CERT_NO_RELOAD:-0}"
readonly CERT_NAME="jarvis"                       # certbot lineage name
readonly WEBROOT="/var/www/letsencrypt"           # HTTP-01 webroot served by nginx on port 80
readonly LEAF_DAYS_DEFAULT=397                    # browser maximum for server certificates
readonly CA_DAYS=3650
readonly RENEW_DAYS_DEFAULT=30
readonly LOCAL_DNS_SUFFIXES=(local lan home.arpa internal)
readonly PRIVATE_V4_NETS=("10.0.0.0/255.0.0.0" "172.16.0.0/255.240.0.0" "192.168.0.0/255.255.0.0" "127.0.0.0/255.0.0.0")

# --- paths --------------------------------------------------------------------
# set_paths: derive every path from TLS_DIR (called after option parsing).
set_paths() {
  CRT="$TLS_DIR/jarvis.crt"
  KEY="$TLS_DIR/jarvis.key"
  STATE="$TLS_DIR/jarvis-cert.conf"
  CA_DIR="$TLS_DIR/ca"
  CA_CRT="$CA_DIR/jarvis-ca.crt"
  CA_KEY="$CA_DIR/jarvis-ca.key"
  CA_CNF="$CA_DIR/ca.cnf"
  CA_CRL="$CA_DIR/crl.pem"
}

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "$_self" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# --- state file (key=value, parsed, never sourced) ---------------------------
declare -A CFG=()
# state_load: read the persisted mode and parameters into CFG.
state_load() {
  local k v
  [[ -r "$STATE" ]] || return 0
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^[A-Z_]+$ ]] && CFG[$k]="$v"
  done <"$STATE"
}
# state_save KEY=VALUE...: persist the given parameters (0600).
state_save() {
  local kv tmp
  tmp="$(mktemp "$TLS_DIR/.state.XXXXXX")"
  for kv in "$@"; do printf '%s\n' "$kv"; done >"$tmp"
  printf 'UPDATED=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$tmp"
  chmod 600 "$tmp" && mv -f "$tmp" "$STATE"
}

# --- helpers --------------------------------------------------------------------
# ensure_writable: changes need root unless the TLS directory is user-writable (tests).
ensure_writable() {
  install -d -m 755 "$TLS_DIR" 2>/dev/null || true
  [[ -w "$TLS_DIR" ]] || require_root
}

# reload_nginx: validate the nginx configuration and reload it when it is running.
reload_nginx() {
  if is_true "$NO_RELOAD"; then log_info "nginx reload skipped (--no-reload)"; return 0; fi
  if command -v systemctl >/dev/null && systemctl is-active --quiet nginx; then
    nginx -t -q || die "nginx configuration test failed; certificate installed but nginx not reloaded" "$E_CONFIG"
    systemctl reload nginx && log_ok "nginx reloaded"
  else
    log_warn "nginx is not running: it will use the new certificate at next start"
  fi
}

# cert_field FILE FIELD: startdate|enddate|serial|subject|issuer|fingerprint (value only).
cert_field() {
  local out
  case "$2" in
    fingerprint) out="$(openssl x509 -in "$1" -noout -fingerprint -sha256)" ;;
    *) out="$(openssl x509 -in "$1" -noout "-$2" -nameopt RFC2253)" ;;
  esac
  printf '%s\n' "${out#*=}"
}

# cert_sans FILE: comma-separated subjectAltName entries ("DNS:a, IP Address:b").
cert_sans() {
  { openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null || true; } | sed -n '2,$p' | tr -d '\n' | sed 's/^ *//'
}

# key_matches CERT KEY: success when the private key belongs to the certificate.
key_matches() {
  [[ "$(openssl x509 -in "$1" -noout -pubkey | openssl sha256)" == \
     "$(openssl pkey -in "$2" -pubout 2>/dev/null | openssl sha256)" ]]
}

# epoch DATE: seconds since the epoch for an openssl date ("Sep 30 12:00:00 2027 GMT").
epoch() { date -u -d "$1" +%s; }

# human_duration SECONDS: "123 d 04 h 05 min" (negative values prefixed with "-").
human_duration() {
  local s="$1" sign=""
  ((s < 0)) && { sign="-"; s=$((-s)); }
  printf '%s%d d %02d h %02d min' "$sign" $((s / 86400)) $((s % 86400 / 3600)) $((s % 3600 / 60))
}

# split_chain FILE DIR: write each PEM certificate of FILE as DIR/cert-N.pem, print the count.
split_chain() {
  awk -v dir="$2" '/-----BEGIN CERTIFICATE-----/{n++} n{print > (dir "/cert-" n ".pem")} END{print n+0}' "$1"
}

# json_str VALUE: JSON-escaped string literal.
json_str() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"; s="${s//$'\r'/}"
  printf '"%s"' "$s"
}

# is_private_v4 IP: success for RFC 1918 / loopback IPv4 addresses.
is_private_v4() {
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}" o
  for o in "${BASH_REMATCH[@]:1}"; do ((o <= 255)) || return 1; done
  ((a == 10 || a == 127)) || { ((a == 172)) && ((b >= 16 && b <= 31)); } || { ((a == 192)) && ((b == 168)); }
}

# is_local_name NAME: success for single-label names or local suffixes (.local, .lan...).
is_local_name() {
  local n="${1,,}" s
  [[ "$n" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$ ]] || return 1
  [[ "$n" != *.* ]] && return 0
  for s in "${LOCAL_DNS_SUFFIXES[@]}"; do [[ "$n" == *."$s" ]] && return 0; done
  return 1
}

# --- local CA -----------------------------------------------------------------
# ca_write_config NAMES...: openssl ca configuration (database, CRL, policies).
ca_write_config() {
  cat >"$CA_CNF" <<EOF
# Generated by jarvis-cert: Jarvis Local CA (do not edit by hand).
[ ca ]
default_ca = jarvis_ca

[ jarvis_ca ]
dir              = $CA_DIR
database         = \$dir/index.txt
new_certs_dir    = \$dir/newcerts
serial           = \$dir/serial
crlnumber        = \$dir/crlnumber
certificate      = $CA_CRT
private_key      = $CA_KEY
default_md       = sha256
default_crl_days = 400
unique_subject   = no
copy_extensions  = none
policy           = policy_any
email_in_dn      = no

[ policy_any ]
commonName = supplied
EOF
}

# ca_create NAMES...: create the private CA, name-constrained to local names and private IPv4.
ca_create() {
  local -a constraints=() n
  log_step "Creating the Jarvis Local CA"
  install -d -m 700 "$CA_DIR" "$CA_DIR/newcerts"
  : >"$CA_DIR/index.txt"
  printf 'unique_subject = no\n' >"$CA_DIR/index.txt.attr"
  openssl rand -hex 16 >"$CA_DIR/serial"
  echo 1000 >"$CA_DIR/crlnumber"
  for n in "${LOCAL_DNS_SUFFIXES[@]}"; do constraints+=("permitted;DNS:.$n"); done
  for n in "$@"; do [[ "$n" == *.* ]] || constraints+=("permitted;DNS:$n"); done
  for n in "${PRIVATE_V4_NETS[@]}"; do constraints+=("permitted;IP:$n"); done
  (umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-384 -out "$CA_KEY")
  openssl req -x509 -new -key "$CA_KEY" -sha384 -days "$CA_DAYS" \
    -subj "/O=Jarvis/OU=Local CA/CN=Jarvis Local CA $(hostname -s)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -addext "nameConstraints=critical,$(IFS=,; echo "${constraints[*]}")" \
    -out "$CA_CRT"
  chmod 644 "$CA_CRT"
  ca_write_config "$@"
  ca_gen_crl
  log_ok "CA created: $CA_CRT (valid ${CA_DAYS} days, constrained to local names/private IPs)"
}

# ca_gen_crl: (re)generate the certificate revocation list.
ca_gen_crl() {
  openssl ca -config "$CA_CNF" -gencrl -out "$CA_CRL" -batch 2>/dev/null ||
    die "CRL generation failed" "$E_RUNTIME"
  chmod 644 "$CA_CRL"
}

# local_issue DAYS NAMES_CSV IPS_CSV: issue and install a server certificate from the local CA.
local_issue() {
  local days="$1" names="$2" ips="$3" work san="" n first
  local -a _names=() _ips=()
  work="$(mktemp -d)"
  on_exit "rm -rf -- '$work'"
  IFS=, read -r -a _names <<<"$names"
  IFS=, read -r -a _ips <<<"$ips"
  first="${_names[0]}"
  for n in "${_names[@]}"; do san+="DNS:$n,"; done
  for n in "${_ips[@]}"; do [[ -n "$n" ]] && san+="IP:$n,"; done
  san="${san%,}"
  log_step "Issuing the server certificate for ${san}"
  (umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$work/key.pem")
  openssl req -new -key "$work/key.pem" -subj "/O=Jarvis/CN=$first" -out "$work/req.csr"
  cat >"$work/ext.cnf" <<EOF
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
subjectAltName = $san
EOF
  openssl ca -config "$CA_CNF" -batch -notext -days "$days" -in "$work/req.csr" \
    -extfile "$work/ext.cnf" -out "$work/crt.pem" 2>"$work/ca.log" ||
    { cat "$work/ca.log" >&2; die "signing failed (name outside the CA constraints?)" "$E_CONFIG"; }
  openssl verify -CAfile "$CA_CRT" "$work/crt.pem" >/dev/null ||
    die "issued certificate does not verify against the CA" "$E_RUNTIME"
  install_pair "$work/crt.pem" "$work/key.pem"
  state_save "MODE=local" "NAMES=$names" "IPS=$ips" "DAYS=$days"
  log_ok "Local certificate installed: $CRT (valid $days days)"
  log_info "Trust it on client devices once: jarvis-cert export-ca --out ~/jarvis-ca.crt"
}

# install_pair CERT KEY: atomically replace the served pair, keeping a backup of the previous one.
install_pair() {
  local cert="$1" key="$2"
  key_matches "$cert" "$key" || die "certificate and key do not match" "$E_STATE"
  [[ -f "$CRT" ]] && cp -a "$CRT" "$CRT.previous"
  [[ -f "$KEY" ]] && cp -a "$KEY" "$KEY.previous"
  install -m 644 "$cert" "$CRT.new" && mv -f "$CRT.new" "$CRT"
  install -m 600 "$key" "$KEY.new" && mv -f "$KEY.new" "$KEY"
  reload_nginx
}

# --- Let's Encrypt --------------------------------------------------------------
# le_issue: obtain a certificate with certbot (DNS-01 by default) and deploy it.
le_issue() {
  local domain="$1" email="$2" challenge="$3" plugin="$4" creds="$5" staging="$6" deps="$7"
  local -a args=(certonly --non-interactive --agree-tos --email "$email" -d "$domain"
    --cert-name "$CERT_NAME" --key-type ecdsa --elliptic-curve secp256r1
    --deploy-hook "$_self deploy-hook"
    # "issue" always issues: certbot would otherwise keep an existing lineage (e.g. staging).
    --force-renewal)
  [[ "$domain" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] || die "invalid public domain: $domain" "$E_CONFIG"
  is_local_name "$domain" && die "$domain is a local name: use --mode local" "$E_CONFIG"
  [[ "$email" == *@*.* ]] || die "--email is required for Let's Encrypt (expiry notices)" "$E_USAGE"
  if ! command -v certbot >/dev/null; then
    is_true "$deps" || die "certbot is not installed (apt install certbot, or use --install-deps)" "$E_DEPS"
    apt-get install -y --no-install-recommends certbot
  fi
  is_true "$staging" && args+=(--test-cert)
  case "$challenge" in
    dns)
      if [[ "$plugin" == manual ]]; then
        log_warn "manual DNS challenge: interactive and NOT renewable automatically"
        args=("${args[@]/--non-interactive/}" --manual --preferred-challenges dns)
      else
        [[ -n "$plugin" ]] || die "--dns-plugin is required for the DNS-01 challenge (cloudflare, ovh, rfc2136, route53, digitalocean, ... or manual)" "$E_USAGE"
        [[ "$plugin" =~ ^[a-z0-9-]+$ ]] || die "invalid DNS plugin name: $plugin" "$E_CONFIG"
        if ! certbot plugins 2>/dev/null | grep -q "^\* dns-$plugin\$"; then
          is_true "$deps" || die "certbot plugin dns-$plugin missing (apt install python3-certbot-dns-$plugin, or --install-deps)" "$E_DEPS"
          apt-get install -y --no-install-recommends "python3-certbot-dns-$plugin"
        fi
        args+=("--dns-$plugin")
        if [[ "$plugin" != route53 ]]; then
          require_file "$creds"
          [[ "$(stat -c %a "$creds")" =~ ^[4-7]00$ ]] || die "credentials file must be private (chmod 600 $creds)" "$E_CONFIG"
          args+=("--dns-$plugin-credentials" "$creds")
        fi
      fi
      ;;
    http)
      log_warn "HTTP-01: port 80 of this host must be reachable from the Internet during validation"
      install -d -m 755 "$WEBROOT"
      args+=(--webroot -w "$WEBROOT")
      ;;
    *) die "invalid --challenge: $challenge (dns|http)" "$E_USAGE" ;;
  esac
  log_step "Requesting a Let's Encrypt certificate for $domain ($challenge-01$(is_true "$staging" && echo ', staging'))"
  # Persist the mode first (the deploy hook run by certbot needs it); roll back on failure.
  [[ -f "$STATE" ]] && cp -a "$STATE" "$STATE.rollback"
  state_save "MODE=letsencrypt" "DOMAIN=$domain" "EMAIL=$email" "CHALLENGE=$challenge" \
    "DNS_PLUGIN=$plugin" "STAGING=$staging"
  if ! certbot "${args[@]}"; then
    if [[ -f "$STATE.rollback" ]]; then mv -f "$STATE.rollback" "$STATE"; else rm -f "$STATE"; fi
    die "certbot failed, previous certificate kept (see /var/log/letsencrypt/letsencrypt.log)" "$E_NETWORK"
  fi
  rm -f "$STATE.rollback"
  log_ok "Let's Encrypt certificate installed: $CRT (automatic renewal: jarvis-cert-renew.timer)"
}

# cmd_deploy_hook: certbot deploy hook, copy the renewed lineage to the stable nginx paths.
cmd_deploy_hook() {
  local live="${RENEWED_LINEAGE:-/etc/letsencrypt/live/$CERT_NAME}"
  require_file "$live/fullchain.pem" "$live/privkey.pem"
  install_pair "$live/fullchain.pem" "$live/privkey.pem"
  log_ok "deployed $(cert_field "$CRT" enddate | xargs -I{} echo "certificate valid until {}")"
}

# --- commands -------------------------------------------------------------------
# cmd_issue ARGS...: parse the issue options and dispatch to the selected mode.
cmd_issue() {
  local mode="" days="$LEAF_DAYS_DEFAULT" new_ca=0 domain="" email="" challenge=dns plugin="" creds="" staging=0 deps=0
  local -a names=() ips=()
  while (($#)); do
    case "$1" in
      --mode) mode="${2:?--mode needs a value}"; shift ;;
      --name) names+=("${2:?--name needs a value}"); shift ;;
      --ip) ips+=("${2:?--ip needs a value}"); shift ;;
      --days) days="${2:?--days needs a value}"; shift ;;
      --new-ca) new_ca=1 ;;
      --domain) domain="${2:?--domain needs a value}"; shift ;;
      --email) email="${2:?--email needs a value}"; shift ;;
      --challenge) challenge="${2:?--challenge needs a value}"; shift ;;
      --dns-plugin) plugin="${2:?--dns-plugin needs a value}"; shift ;;
      --dns-credentials) creds="${2:?--dns-credentials needs a value}"; shift ;;
      --staging) staging=1 ;;
      --install-deps) deps=1 ;;
      *) die "unknown option for issue: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
  ensure_writable
  case "$mode" in
    local)
      local n ip host
      host="$(hostname -s)"
      ((${#names[@]})) || names=(jarvis.local "$host" "$host.local")
      if ((${#ips[@]} == 0)); then
        # Auto-detection: keep the private IPv4 addresses only (skip CGNAT/VPN/public/IPv6).
        for ip in $(hostname -I 2>/dev/null || true); do
          if is_private_v4 "$ip"; then ips+=("$ip"); else log_warn "skipping non-private address $ip"; fi
        done
      fi
      if ! [[ "$days" =~ ^[0-9]+$ ]] || ((days < 1 || days > 825)); then die "--days must be 1..825" "$E_CONFIG"; fi
      for n in "${names[@]}"; do is_local_name "$n" || die "not a local name: $n (use .local/.lan/.home.arpa/.internal or --mode letsencrypt)" "$E_CONFIG"; done
      for ip in "${ips[@]}"; do is_private_v4 "$ip" || die "not a private IPv4 address: $ip" "$E_CONFIG"; done
      mapfile -t names < <(printf '%s\n' "${names[@],,}" | awk '!seen[$0]++')
      if ((new_ca)) || [[ ! -r "$CA_KEY" ]]; then
        [[ -r "$CA_KEY" ]] && { confirm "Replace the existing local CA (clients must re-import it)?" || die "aborted" "$E_USAGE"; }
        rm -rf -- "$CA_DIR"
        ca_create "${names[@]}"
      fi
      local_issue "$days" "$(IFS=,; echo "${names[*]}")" "$(IFS=,; echo "${ips[*]}")"
      ;;
    letsencrypt) le_issue "$domain" "$email" "$challenge" "$plugin" "$creds" "$staging" "$deps" ;;
    "") die "--mode local|letsencrypt is required" "$E_USAGE" ;;
    *) die "invalid --mode: $mode (local|letsencrypt)" "$E_USAGE" ;;
  esac
}

# cmd_renew ARGS...: renew when the certificate expires within N days (or --force).
cmd_renew() {
  local force=0 before="$RENEW_DAYS_DEFAULT"
  while (($#)); do
    case "$1" in
      --force) force=1 ;;
      --days-before) before="${2:?--days-before needs a value}"; shift ;;
      *) die "unknown option for renew: $1" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$before" =~ ^[0-9]+$ ]] || die "--days-before must be an integer" "$E_CONFIG"
  ensure_writable
  state_load
  case "${CFG[MODE]:-}" in
    letsencrypt)
      local -a a=(renew --cert-name "$CERT_NAME" --non-interactive --deploy-hook "$_self deploy-hook")
      ((force)) && a+=(--force-renewal)
      require_cmd certbot
      certbot "${a[@]}" || die "certbot renew failed" "$E_NETWORK"
      ;;
    local | "")
      if [[ ! -r "$CA_KEY" || -z "${CFG[MODE]:-}" ]]; then
        log_warn "no local CA (legacy self-signed certificate?): migrating to a local CA"
        local host; host="$(hostname -s)"
        cmd_issue --mode local --name jarvis.local --name "$host" --name "$host.local"
        return
      fi
      if ((force)) || ! openssl x509 -in "$CRT" -noout -checkend $((before * 86400)) >/dev/null 2>&1; then
        local_issue "${CFG[DAYS]:-$LEAF_DAYS_DEFAULT}" "${CFG[NAMES]}" "${CFG[IPS]:-}"
      else
        log_ok "certificate valid for more than $before days: nothing to do"
      fi
      openssl x509 -in "$CA_CRT" -noout -checkend $((365 * 86400)) >/dev/null ||
        log_warn "the local CA expires within a year: plan 'jarvis-cert issue --mode local --new-ca'"
      ;;
    *) die "unknown mode in $STATE: ${CFG[MODE]}" "$E_CONFIG" ;;
  esac
}

# revocation_check LEAF ISSUER: print "status|date|source" (good, revoked, unknown).
revocation_check() {
  local leaf="$1" issuer="$2" serial url tmp line date
  serial="$(cert_field "$leaf" serial)"
  state_load
  # Local CA: authoritative database.
  if [[ -r "$CA_DIR/index.txt" ]] && [[ "$(cert_field "$leaf" issuer)" == "$(cert_field "$CA_CRT" subject 2>/dev/null)" ]]; then
    line="$(awk -F'\t' -v s="${serial^^}" 'toupper($4)==s' "$CA_DIR/index.txt")"
    if [[ "$line" == R* ]]; then
      date="$(cut -f3 <<<"$line" | cut -d, -f1)"
      date="20${date:0:2}-${date:2:2}-${date:4:2} ${date:6:2}:${date:8:2}:${date:10:2} UTC"
      echo "revoked|$date|local CA database"
    else
      echo "good||local CA database"
    fi
    return
  fi
  [[ -n "$issuer" ]] || { echo "unknown||no issuer certificate in the chain"; return; }
  command -v curl >/dev/null || { echo "unknown||curl missing"; return; }
  tmp="$(mktemp -d)"
  on_exit "rm -rf -- '$tmp'"
  # Public CA: CRL distribution point (Let's Encrypt), then OCSP as a fallback.
  url="$({ openssl x509 -in "$leaf" -noout -ext crlDistributionPoints 2>/dev/null || true; } | { grep -oE 'URI:[^ ]+' || true; } | head -1 | cut -c5-)"
  if [[ -n "$url" ]]; then
    if curl -fsS --max-time 15 -o "$tmp/crl.der" "$url" &&
       openssl crl -inform DER -in "$tmp/crl.der" -CAfile "$issuer" -noout 2>&1 | grep -q "verify OK"; then
      date="$(openssl crl -inform DER -in "$tmp/crl.der" -noout -text |
        awk -v s="${serial^^}" '/Serial Number:/{hit=(toupper($3)==s)} hit && /Revocation Date:/{sub(/.*Revocation Date: /,""); print; exit}')"
      if [[ -n "$date" ]]; then echo "revoked|$date|CRL $url"; else echo "good||CRL $url"; fi
      return
    fi
  fi
  url="$(openssl x509 -in "$leaf" -noout -ocsp_uri 2>/dev/null || true)"
  if [[ -n "$url" ]]; then
    local resp
    if resp="$(openssl ocsp -issuer "$issuer" -cert "$leaf" -url "$url" -no_nonce -resp_text -CAfile "$issuer" 2>/dev/null)"; then
      if grep -q "Cert Status: revoked" <<<"$resp"; then
        echo "revoked|$(sed -n 's/.*Revocation Time: //p' <<<"$resp" | head -1)|OCSP $url"
      elif grep -q "Cert Status: good" <<<"$resp"; then echo "good||OCSP $url"
      else echo "unknown||OCSP $url"; fi
      return
    fi
  fi
  echo "unknown||revocation source unreachable"
}

# cmd_status ARGS...: creation/expiry dates, remaining time, revocation, chain and key checks.
cmd_status() {
  local cert="" warn="$RENEW_DAYS_DEFAULT" remote="" json=0 revocation=1
  while (($#)); do
    case "$1" in
      --cert) cert="${2:?--cert needs a value}"; shift ;;
      --warn-days) warn="${2:?--warn-days needs a value}"; shift ;;
      --remote) remote="${2:?--remote needs a value}"; shift ;;
      --json) json=1 ;;
      --no-revocation) revocation=0 ;;
      *) die "unknown option for status: $1" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$warn" =~ ^[0-9]+$ ]] || die "--warn-days must be an integer" "$E_CONFIG"
  cert="${cert:-$CRT}"
  [[ -r "$cert" ]] || die "certificate not found: $cert (run: jarvis-cert issue ...)" "$E_DEPS"
  state_load
  local work count leaf issuer="" start end now total left pct state code=$E_OK
  work="$(mktemp -d)"
  on_exit "rm -rf -- '$work'"
  count="$(split_chain "$cert" "$work")"
  ((count >= 1)) || die "no PEM certificate in $cert" "$E_CONFIG"
  leaf="$work/cert-1.pem"
  ((count >= 2)) && issuer="$work/cert-2.pem"
  [[ -z "$issuer" && -r "$CA_CRT" ]] && issuer="$CA_CRT"

  start="$(cert_field "$leaf" startdate)"; end="$(cert_field "$leaf" enddate)"
  now="$(date -u +%s)"
  total=$(($(epoch "$end") - $(epoch "$start"))); left=$(($(epoch "$end") - now))
  pct=$(((now - $(epoch "$start")) * 100 / (total > 0 ? total : 1)))
  ((pct > 100)) && pct=100
  ((pct < 0)) && pct=0
  local mode="${CFG[MODE]:-unknown}"
  [[ "$(cert_field "$leaf" subject)" == "$(cert_field "$leaf" issuer)" ]] && mode="self-signed (legacy)"

  # Checks, from the most severe down.
  local chain="skipped" keyok="skipped" rev="skipped|| " served="skipped"
  if [[ -r "$KEY" && "$cert" == "$CRT" ]]; then
    if key_matches "$leaf" "$KEY" 2>/dev/null; then keyok="ok"; else keyok="MISMATCH"; fi
  elif [[ "$cert" == "$CRT" ]]; then keyok="unreadable (run as root)"
  fi
  if [[ "$mode" == local && -r "$CA_CRT" ]]; then
    local -a crlopt=()
    [[ -r "$CA_CRL" ]] && crlopt=(-crl_check -CRLfile "$CA_CRL")
    if openssl verify -CAfile "$CA_CRT" "${crlopt[@]}" "$leaf" >/dev/null 2>&1; then chain="ok (Jarvis Local CA)"; else chain="FAILED"; fi
  elif [[ "$mode" != "self-signed (legacy)" ]]; then
    local -a untrusted=()
    ((count >= 2)) && { cat "$work"/cert-[2-9]*.pem >"$work/chain.pem" 2>/dev/null; untrusted=(-untrusted "$work/chain.pem"); }
    if openssl verify "${untrusted[@]}" "$leaf" >/dev/null 2>&1; then chain="ok (system trust store)"; else chain="FAILED"; fi
  fi
  ((revocation)) && rev="$(revocation_check "$leaf" "$issuer")"
  if [[ -n "$remote" ]]; then
    [[ "$remote" == *:* ]] || remote="$remote:443"
    local fp_remote
    fp_remote="$(openssl s_client -connect "$remote" -servername "${CFG[DOMAIN]:-jarvis.local}" </dev/null 2>/dev/null |
      openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 || true)"
    if [[ -z "$fp_remote" ]]; then served="UNREACHABLE"
    elif [[ "$fp_remote" == "$(cert_field "$leaf" fingerprint)" ]]; then served="ok (same certificate)"
    else served="DIFFERENT (reload nginx)"; fi
  fi

  if ((left <= 0)); then state="EXPIRED"; code=$E_STATE
  elif (($(epoch "$start") > now)); then state="NOT YET VALID"; code=$E_STATE
  elif [[ "${rev%%|*}" == revoked ]]; then state="REVOKED"; code=$E_STATE
  elif [[ "$keyok" == MISMATCH || "$chain" == FAILED ]]; then state="INVALID"; code=$E_STATE
  elif ((left < warn * 86400)); then state="EXPIRING SOON"; code=$E_WARN
  else state="VALID"; fi
  [[ "$served" == DIFFERENT* || "$served" == UNREACHABLE ]] && ((code == E_OK)) && code=$E_WARN

  local rev_status rev_date rev_src
  IFS='|' read -r rev_status rev_date rev_src <<<"$rev"
  if ((json)); then
    printf '{"file":%s,"mode":%s,"state":%s,"subject":%s,"issuer":%s,"san":%s,"serial":%s,' \
      "$(json_str "$cert")" "$(json_str "$mode")" "$(json_str "$state")" "$(json_str "$(cert_field "$leaf" subject)")" \
      "$(json_str "$(cert_field "$leaf" issuer)")" "$(json_str "$(cert_sans "$leaf")")" "$(json_str "$(cert_field "$leaf" serial)")"
    printf '"sha256":%s,"not_before":%s,"not_after":%s,"seconds_left":%d,"days_left":%d,"lifetime_used_pct":%d,' \
      "$(json_str "$(cert_field "$leaf" fingerprint)")" "$(json_str "$(date -u -d "$start" +%FT%TZ)")" \
      "$(json_str "$(date -u -d "$end" +%FT%TZ)")" "$left" $((left / 86400)) "$pct"
    printf '"revocation":{"status":%s,"date":%s,"source":%s},"chain":%s,"key":%s,"served":%s,"exit_code":%d}\n' \
      "$(json_str "$rev_status")" "$(json_str "$rev_date")" "$(json_str "$rev_src")" "$(json_str "$chain")" \
      "$(json_str "$keyok")" "$(json_str "$served")" "$code"
    return "$code"
  fi

  local color="$C_GREEN"
  ((code == E_WARN)) && color="$C_YELLOW"
  ((code == E_STATE)) && color="$C_RED"
  # row LABEL VALUE: aligned "label : value" line on stdout.
  row() { printf '  %s%-18s%s %s\n' "$C_DIM" "$1" "$C_RESET" "$2"; }
  printf '\n%sJarvis TLS certificate%s  %s%s%s\n' "$C_BOLD" "$C_RESET" "$color$C_BOLD" "$state" "$C_RESET"
  row "File" "$cert"
  row "Mode" "$mode${CFG[STAGING]:+$(is_true "${CFG[STAGING]}" && echo ' (STAGING, not trusted)')}"
  row "Subject" "$(cert_field "$leaf" subject)"
  row "Names (SAN)" "$(cert_sans "$leaf")"
  row "Issuer" "$(cert_field "$leaf" issuer)"
  row "Serial" "$(cert_field "$leaf" serial)"
  row "SHA-256" "$(cert_field "$leaf" fingerprint)"
  row "Key" "$(openssl x509 -in "$leaf" -noout -text | sed -n 's/^ *Public Key Algorithm: //p' | head -1), private key: $keyok"
  row "Created" "$(date -d "$start" '+%Y-%m-%d %H:%M:%S %Z')"
  row "Expires" "$(date -d "$end" '+%Y-%m-%d %H:%M:%S %Z')"
  if ((left > 0)); then
    row "Remaining" "$color$(human_duration "$left")$C_RESET  (${pct}% of $((total / 86400)) days used, warning below $warn days)"
  else
    row "Remaining" "${color}none: expired $(human_duration $((-left))) ago${C_RESET}"
  fi
  case "$rev_status" in
    revoked) row "Revocation" "${C_RED}REVOKED on $rev_date${C_RESET} ($rev_src)" ;;
    good) row "Revocation" "not revoked ($rev_src)" ;;
    *) row "Revocation" "${rev_status:-skipped} ${rev_src:+($rev_src)}" ;;
  esac
  row "Chain" "$chain"
  [[ -n "$remote" ]] && row "Served on $remote" "$served"
  if [[ -r "$CA_CRT" && "$mode" == local ]]; then
    row "Local CA expires" "$(date -d "$(cert_field "$CA_CRT" enddate)" '+%Y-%m-%d')"
  fi
  echo
  return "$code"
}

# cmd_revoke ARGS...: revoke the current certificate (local CA database or ACME).
cmd_revoke() {
  local reason=superseded
  while (($#)); do
    case "$1" in
      --reason) reason="${2:?--reason needs a value}"; shift ;;
      *) die "unknown option for revoke: $1" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$reason" =~ ^(unspecified|keyCompromise|superseded|cessationOfOperation)$ ]] || die "invalid --reason: $reason" "$E_USAGE"
  ensure_writable
  state_load
  require_file "$CRT"
  confirm "Revoke $(cert_field "$CRT" serial) ($reason)? Browsers may reject it immediately." || die "aborted" "$E_USAGE"
  case "${CFG[MODE]:-}" in
    local)
      local leaf; leaf="$(mktemp)"; on_exit "rm -f -- '$leaf'"
      awk '/BEGIN/{n++} n==1' "$CRT" >"$leaf"
      openssl ca -config "$CA_CNF" -revoke "$leaf" -crl_reason "$reason" -batch 2>/dev/null || die "revocation failed" "$E_RUNTIME"
      ca_gen_crl
      ;;
    letsencrypt)
      require_cmd certbot
      local r="$reason"
      [[ "$r" == keyCompromise ]] && r=keycompromise
      [[ "$r" == cessationOfOperation ]] && r=cessationofoperation
      certbot revoke --cert-name "$CERT_NAME" --reason "$r" --non-interactive --no-delete-after-revoke ||
        die "certbot revoke failed" "$E_NETWORK"
      ;;
    *) die "unknown certificate mode: cannot revoke" "$E_CONFIG" ;;
  esac
  log_ok "certificate revoked; issue a new one now: jarvis-cert renew --force"
}

# cmd_export_ca ARGS...: copy the local CA certificate for import on client devices.
cmd_export_ca() {
  local out=""
  while (($#)); do
    case "$1" in
      --out) out="${2:?--out needs a value}"; shift ;;
      *) die "unknown option for export-ca: $1" "$E_USAGE" ;;
    esac
    shift
  done
  require_file "$CA_CRT"
  if [[ -n "$out" ]]; then install -m 644 "$CA_CRT" "$out"; log_ok "CA certificate written to $out"; else cat "$CA_CRT"; fi
  log_info "SHA-256 fingerprint (compare on the client): $(cert_field "$CA_CRT" fingerprint)"
  log_info "Import as a trusted root: Firefox (Settings > Certificates > Authorities), Windows (certutil -addstore Root),"
  log_info "macOS (Keychain, 'Always trust'), Debian/Ubuntu (/usr/local/share/ca-certificates + update-ca-certificates),"
  log_info "Android (Security > Install CA certificate), iOS (profile + Settings > About > Certificate Trust Settings)."
}

# main ARGS...: global options, then the sub-command.
main() {
  local cmd=""
  while (($#)); do
    case "$1" in
      -h | --help) usage; exit "$E_OK" ;;
      --version) echo "jarvis-cert (jarvis-home $(jarvis_version))"; exit "$E_OK" ;;
      --tls-dir) TLS_DIR="${2:?--tls-dir needs a value}"; shift ;;
      --no-reload) NO_RELOAD=1 ;;
      --yes | -y) export ASSUME_YES=1 ;;
      --no-color) export JARVIS_COLOR=never; setup_colors ;;
      -*) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
      *) cmd="$1"; shift; break ;;
    esac
    shift
  done
  # Global options may also follow the sub-command.
  local -a rest=()
  while (($#)); do
    case "$1" in
      --tls-dir) TLS_DIR="${2:?}"; shift ;;
      --no-reload) NO_RELOAD=1 ;;
      --yes | -y) export ASSUME_YES=1 ;;
      --no-color) export JARVIS_COLOR=never; setup_colors ;;
      *) rest+=("$1") ;;
    esac
    shift
  done
  require_cmd openssl awk sed date
  # Private by default (keys, CA database); public files get an explicit mode.
  umask 077
  set_paths
  case "$cmd" in
    issue) cmd_issue "${rest[@]}" ;;
    renew) cmd_renew "${rest[@]}" ;;
    status)
      # The status code is the verdict (0 valid, 7 unusable, 8 expiring): not an error to trap.
      local rc=0
      cmd_status "${rest[@]}" || rc=$?
      exit "$rc"
      ;;
    revoke) cmd_revoke "${rest[@]}" ;;
    export-ca) cmd_export_ca "${rest[@]}" ;;
    deploy-hook) cmd_deploy_hook ;;
    "") usage; exit "$E_USAGE" ;;
    *) die "unknown command: $cmd (see --help)" "$E_USAGE" ;;
  esac
}

main "$@"
