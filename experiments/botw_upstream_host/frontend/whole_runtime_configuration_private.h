/* SPDX-License-Identifier: MIT */
#pragma once
#include "../adapter/whole_shader_bundle_publication.h"
/* Optional, strong publication implementation, referenced only with explicit
 * BOTW_WHOLE_SHADER_PUBLICATION=1. It must verify the receipt's declared static
 * catalog coverage, identities, hashes, established facts or explicit
 * conditional constraints and reflection through the canonical manifest
 * validator, and publish an absolute path into the
 * caller-owned buffer only on success. No Metal device/library/compiler or
 * guest execution is permitted here. Source publication acceptance is not
 * successful runtime compilation or gameplay; unexpected programs must retain
 * the original strict rejection. Failure leaves manifest_path unchanged.
 * No default implementation is supplied. */
