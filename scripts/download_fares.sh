#!/usr/bin/env bash
set -euo pipefail

# Download the RSP Fares Data Feed (RJFAF*) from our Rail Data Marketplace
# feeds bucket.
#
# RDM's "Fares - Full Refresh - Weekly" product
# (P-03ed70e9-cc9d-45f3-949b-45adffc70791) has no download API; instead RDM
# copies each new release into our own S3 bucket as fares_full.zip. Needs the
# AWS CLI with credentials that can read the bucket -- a local `aws configure`
# profile, or the OIDC role the GitHub Actions workflow assumes.
#
# Usage:
#   ./download_fares.sh
#   RDM_BUCKET=other-bucket ./download_fares.sh
#
# Output: fares.zip in the current directory (or OUTPUT_FILE if set)

RDM_BUCKET="${RDM_BUCKET:-rdm-feeds}"
RDM_FARES_KEY="${RDM_FARES_KEY:-fares_full.zip}"
OUTPUT_FILE="${OUTPUT_FILE:-fares.zip}"

echo "Downloading s3://${RDM_BUCKET}/${RDM_FARES_KEY}..."
aws s3 cp --only-show-errors "s3://${RDM_BUCKET}/${RDM_FARES_KEY}" "${OUTPUT_FILE}"

echo "Saved to ${OUTPUT_FILE}"
