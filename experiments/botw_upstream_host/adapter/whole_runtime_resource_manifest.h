/* SPDX-License-Identifier: MIT
 * Hash-only authority from the independently verified base-v0/v4 normal-allocation-guard local build.
 * Source: outputs/botw-guest-framework-host39-v4-normal-allocation-guard-v1-six-final-ios18
 * Report remains unsigned/phone_ready=false; this does not grant execution.
 * Codesigning changes the whole Mach-O hash. Signed section payloads are checked
 * separately against base_v0_force_manifest; no unsigned whole-file comparison.
 */
#pragma once
#include "whole_runtime_resource_checks.h"
static const whole_runtime_file_identity whole_runtime_pinned_metadata[] = {
    {"main.fixups.tsv", 277u, "d16b439edde318861a04ac1e9641a273110fbcd890275d5863f0acc21f8d20f3"},
    {"main.gates.tsv", 2536u, "b57066cfcfa977fa7dc259e5335783ba2df639faf7fe6e949d7f654310fc52ea"},
    {"sdk.fixups.tsv", 272u, "823a9767ed8cc65eff71de120259e8e224b0335d2a9f644c8fe47e113561fdc0"},
    {"sdk.gates.tsv", 3382u, "45103842df78b89bf8585f32c36069f460a4b704adfaf5a14122297574438716"},
    {"subsdk0.fixups.tsv", 270u, "7d719c76ead04e9b1e914a0313535e1c2cc84196f755d54c9d510c4cc91f8c6b"},
    {"subsdk0.gates.tsv", 0u, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    {"subsdk1.fixups.tsv", 266u, "7bac1f14131607a43eaf863312d54fdbfffe0f749dde31fa68d4f00d15501b1d"},
    {"subsdk1.gates.tsv", 0u, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    {"subsdk2.fixups.tsv", 267u, "61c8501727c52bc5b6286555837f546b35897c583f62b12e110ea4193b9fa044"},
    {"subsdk2.gates.tsv", 0u, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    {"subsdk3.fixups.tsv", 272u, "5fe979f844eaa9a2ba0b9539a19fb106c3b9773e9b356c1a46525646d4e04942"},
    {"subsdk3.gates.tsv", 0u, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    {"base-profile.json", 1547u, "edd5fb5314ad8313e18d7aee0ed381cb590c4acd63e23568b9929a91df84c462"},
    {"guest-framework-report.json", 13298u, "1ef1e776387b6152403c57d53499c58da1249c7daa0d5a01868345c87daed8bc"},
};
