#!/usr/bin/env bash
set -euo pipefail

# Download fares data from National Rail Data Portal.
# Requires an account at https://opendata.nationalrail.co.uk
#
# Usage:
#   NRDP_USERNAME=you@example.com NRDP_PASSWORD=secret ./download_fares.sh
#   ./download_fares.sh                   # prompts for credentials
#
# Output: fares.zip in the current directory (or OUTPUT_FILE if set)

AUTHENTICATE_URL="https://opendata.nationalrail.co.uk/authenticate"
FARES_URL="https://opendata.nationalrail.co.uk/api/staticfeeds/2.0/fares"
OUTPUT_FILE="${OUTPUT_FILE:-fares.zip}"

# --- credentials ---
if [[ -z "${NRDP_USERNAME:-}" ]]; then
  read -rp "NRDP username (email): " NRDP_USERNAME
fi

if [[ -z "${NRDP_PASSWORD:-}" ]]; then
  read -rsp "NRDP password: " NRDP_PASSWORD
  echo
fi

# --- authenticate ---
echo "Authenticating as ${NRDP_USERNAME}..."

AUTH_RESPONSE=$(curl  --fail \
  --data-urlencode "username=${NRDP_USERNAME}" \
  --data-urlencode "password=${NRDP_PASSWORD}" \
  "${AUTHENTICATE_URL}")

# Extract token — works with or without jq
if command -v jq &>/dev/null; then
  TOKEN=$(echo "${AUTH_RESPONSE}" | jq -r '.token')
else
  TOKEN=$(echo "${AUTH_RESPONSE}" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
fi

if [[ -z "${TOKEN}" || "${TOKEN}" == "null" ]]; then
  echo "Error: authentication failed. Response was:" >&2
  echo "${AUTH_RESPONSE}" >&2
  exit 1
fi

echo "Authenticated. Downloading fares data..."

# --- download ---
curl  --fail --show-error \
  --progress-bar \
  -H "X-Auth-Token: ${TOKEN}" \
  --output "${OUTPUT_FILE}" \
  "${FARES_URL}"

echo "Saved to ${OUTPUT_FILE}"
