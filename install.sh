#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Raffaele Malagnini
#
# llama-lite-top installer
#
#   curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USER/llama-lite-top/main/install.sh | sudo bash
#
# With options (note the "-s --"):
#   curl -fsSL .../install.sh | sudo bash -s -- --url http://127.0.0.1:8080 --api-key KEY
#
# Options
#   --url URL          llama-server address saved in the config file
#   --api-key KEY      llama-server API key saved in the config file
#   --interval SEC     default refresh interval saved in the config file
#   --ref REF          git branch or tag to install (default: main)
#   --user             install for the current user only (~/.local/bin), no sudo needed
#   --yes              never ask questions (for scripts and automation)
#   --uninstall        remove llama-lite-top (add --purge to delete the config file too)
#   -h, --help         show this help
#
# Environment
#   LLT_REPO           GitHub "owner/repo" to install from (forks)
#   LLT_BASE_URL       full base URL to download from (overrides LLT_REPO and --ref)

# Everything runs inside main(), so a partially downloaded script never executes.
main() {
  set -euo pipefail

  local REPO="${LLT_REPO:-YOUR_GITHUB_USER/llama-lite-top}"
  local NAME="llama-lite-top"
  local REF="main" URL="" API_KEY="" INTERVAL="" USER_MODE=0 ASSUME_YES=0 UNINSTALL=0 PURGE=0

  usage() {
    cat <<'EOF'
llama-lite-top installer

  curl -fsSL https://raw.githubusercontent.com/OWNER/llama-lite-top/main/install.sh | sudo bash
  curl -fsSL .../install.sh | sudo bash -s -- [options]

Options
  --url URL        llama-server address saved in the config file
  --api-key KEY    llama-server API key saved in the config file
  --interval SEC   default refresh interval saved in the config file
  --ref REF        git branch or tag to install (default: main)
  --user           install for the current user only (~/.local/bin), no sudo needed
  --yes            never ask questions (for scripts and automation)
  --uninstall      remove llama-lite-top (add --purge to delete the config file too)
  -h, --help       show this help
EOF
  }
  info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
  ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
  warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
  die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --url)       URL="${2:?--url needs a value}"; shift 2 ;;
      --api-key)   API_KEY="${2:?--api-key needs a value}"; shift 2 ;;
      --interval)  INTERVAL="${2:?--interval needs a value}"; shift 2 ;;
      --ref)       REF="${2:?--ref needs a value}"; shift 2 ;;
      --user)      USER_MODE=1; shift ;;
      --yes|-y)    ASSUME_YES=1; shift ;;
      --uninstall) UNINSTALL=1; shift ;;
      --purge)     PURGE=1; shift ;;
      -h|--help)   usage; exit 0 ;;
      *)           die "unknown option: $1 (use --help)" ;;
    esac
  done

  [[ "$(uname -s)" == "Linux" ]] || die "llama-lite-top supports Linux only."

  # --- Where to install, and for which user -------------------------------
  local BIN_DIR TARGET_USER TARGET_HOME
  if [[ ${USER_MODE} -eq 1 ]]; then
    [[ ${EUID} -ne 0 ]] || die "--user installs for a normal user: run it without sudo."
    TARGET_USER="$(id -un)"
    TARGET_HOME="${HOME}"
    BIN_DIR="${HOME}/.local/bin"
  else
    [[ ${EUID} -eq 0 ]] || die "run with sudo, or add --user to install without root."
    BIN_DIR="/usr/local/bin"
    TARGET_USER="${SUDO_USER:-root}"
    TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
    [[ -n "${TARGET_HOME}" ]] || TARGET_HOME="/root"
  fi
  local CONFIG_DIR="${TARGET_HOME}/.config/${NAME}"
  local CONFIG_FILE="${CONFIG_DIR}/config"

  # --- Uninstall -----------------------------------------------------------
  if [[ ${UNINSTALL} -eq 1 ]]; then
    if [[ -e "${BIN_DIR}/${NAME}" ]]; then
      rm -f "${BIN_DIR}/${NAME}"
      ok "removed ${BIN_DIR}/${NAME}"
    else
      warn "${BIN_DIR}/${NAME} not found"
    fi
    if [[ ${PURGE} -eq 1 && -e "${CONFIG_FILE}" ]]; then
      rm -f "${CONFIG_FILE}"
      rmdir "${CONFIG_DIR}" 2>/dev/null || true
      ok "removed ${CONFIG_FILE}"
    elif [[ -e "${CONFIG_FILE}" ]]; then
      info "config kept at ${CONFIG_FILE} (add --purge to delete it)"
    fi
    exit 0
  fi

  # --- Python 3 with curses ------------------------------------------------
  if ! command -v python3 >/dev/null 2>&1; then
    if [[ ${EUID} -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
      info "installing python3"
      DEBIAN_FRONTEND=noninteractive apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 >/dev/null
    else
      die "python3 not found. Install it with your package manager (e.g. sudo apt install python3)."
    fi
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "python3 3.8 or newer is required (found $(python3 -V 2>&1))."
  python3 -c 'import curses' 2>/dev/null \
    || die "the Python curses module is missing (on Debian/Ubuntu it ships with the python3 package)."
  ok "$(python3 -V 2>&1) with curses"

  # --- Get the program: local copy next to this script, or download -------
  local SRC_DIR
  # Global on purpose: the EXIT trap runs after main() has returned.
  LLT_TMP="$(mktemp -d)"
  trap 'rm -rf "${LLT_TMP:-}"' EXIT
  local TMP="${LLT_TMP}"
  SRC_DIR=""
  # Local mode only when this installer is a real file (e.g. a cloned repo),
  # never when piped from curl.
  if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi

  if [[ -n "${SRC_DIR}" && -f "${SRC_DIR}/${NAME}.py" && -z "${LLT_BASE_URL:-}" ]]; then
    info "installing from local copy ${SRC_DIR}/${NAME}.py"
    cp "${SRC_DIR}/${NAME}.py" "${TMP}/${NAME}.py"
    [[ -f "${SRC_DIR}/SHA256SUMS" ]] && cp "${SRC_DIR}/SHA256SUMS" "${TMP}/SHA256SUMS"
  else
    command -v curl >/dev/null 2>&1 || die "curl is required (sudo apt install curl)."
    local BASE="${LLT_BASE_URL:-https://raw.githubusercontent.com/${REPO}/${REF}}"
    [[ "${BASE}" != *YOUR_GITHUB_USER* ]] \
      || die "this installer has no repository configured yet (set LLT_REPO=owner/repo)."
    info "downloading ${NAME} (${REF}) from ${BASE}"
    curl -fsSL "${BASE}/${NAME}.py" -o "${TMP}/${NAME}.py" || die "download failed: ${BASE}/${NAME}.py"
    curl -fsSL "${BASE}/SHA256SUMS" -o "${TMP}/SHA256SUMS" 2>/dev/null || rm -f "${TMP}/SHA256SUMS"
  fi

  # --- Integrity check ------------------------------------------------------
  if [[ -f "${TMP}/SHA256SUMS" ]] && command -v sha256sum >/dev/null 2>&1; then
    (cd "${TMP}" && grep " ${NAME}.py\$" SHA256SUMS | sha256sum -c --status) \
      || die "checksum mismatch: the downloaded file is corrupted or was modified."
    ok "checksum verified"
  else
    warn "no SHA256SUMS available: integrity not verified"
  fi
  python3 -m py_compile "${TMP}/${NAME}.py" 2>/dev/null || die "downloaded file is not valid Python."

  # --- Install --------------------------------------------------------------
  mkdir -p "${BIN_DIR}"
  install -m 755 "${TMP}/${NAME}.py" "${BIN_DIR}/${NAME}"
  ok "installed ${BIN_DIR}/${NAME} ($("${BIN_DIR}/${NAME}" --version))"

  # --- Optional configuration ----------------------------------------------
  local CAN_ASK=0
  if [[ ${ASSUME_YES} -eq 0 && -z "${URL}${API_KEY}${INTERVAL}" && ! -e "${CONFIG_FILE}" ]] \
     && { : </dev/tty; } 2>/dev/null; then
    CAN_ASK=1
  fi
  if [[ ${CAN_ASK} -eq 1 ]]; then
    echo
    info "quick setup (press Enter to accept the default)"
    read -r -p "    llama-server URL [http://127.0.0.1:8080]: " URL </dev/tty || true
    read -r -s -p "    API key (empty if the server has none): " API_KEY </dev/tty || true
    echo
  fi

  if [[ -n "${URL}${API_KEY}${INTERVAL}" ]]; then
    mkdir -p "${CONFIG_DIR}"
    ( umask 077
      {
        echo "# llama-lite-top configuration"
        if [[ -n "${URL}" ]]; then echo "url=${URL}"; fi
        if [[ -n "${API_KEY}" ]]; then echo "api_key=${API_KEY}"; fi
        if [[ -n "${INTERVAL}" ]]; then echo "interval=${INTERVAL}"; fi
      } > "${CONFIG_FILE}" )
    chmod 600 "${CONFIG_FILE}"
    if [[ ${EUID} -eq 0 && "${TARGET_USER}" != "root" ]]; then
      chown -R "${TARGET_USER}:" "${TARGET_HOME}/.config/${NAME}" 2>/dev/null || true
    fi
    ok "config saved to ${CONFIG_FILE} (readable only by ${TARGET_USER})"
  fi

  # --- Done -----------------------------------------------------------------
  echo
  if [[ ${USER_MODE} -eq 1 && ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
    warn "${BIN_DIR} is not in your PATH: add it, or run ${BIN_DIR}/${NAME}"
  fi
  ok "done. Start it with:  ${NAME}"
  echo "     Tip: start llama-server with --metrics to see throughput counters."
}

main "$@"
