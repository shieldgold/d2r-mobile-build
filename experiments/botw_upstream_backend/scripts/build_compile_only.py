#!/usr/bin/env python3
"""Verify, compile, link without running the backend, and run only its host CPU probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    component = Path(__file__).resolve().parents[1]
    repository = component.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--sdk", choices=("macosx", "iphoneos"), default="macosx")
    args = parser.parse_args()
    output = (args.output or repository / ".tools" / ("botw-backend-build-" + time.strftime("%Y%m%d-%H%M%S"))).resolve()
    if output == component or component in output.parents:
        parser.error("build output must be outside the tracked experiment component")
    output.mkdir(parents=True, exist_ok=False)
    lock = json.loads((component / "SOURCE.lock.json").read_text())
    before = {}
    for relative, expected in lock["files"].items():
        path = component / relative
        actual = sha(path)
        if actual != expected["sha256"]:
            raise RuntimeError("source lock mismatch: " + relative)
        before[relative] = actual
    for relative, expected in lock["external_compile_declarations"].items():
        if sha(repository / relative) != expected["sha256"]:
            raise RuntimeError("external declaration changed: " + relative)
    env = os.environ.copy()
    # This is an intended runtime path, not a fabricated valid manifest. The
    # link-only entry is never executed; actual activation must parse it first.
    manifest = (args.manifest or output / "validated-shaders" / "manifest-build.json").resolve()
    env["DM_BOTW_SHADER_MANIFEST"] = str(manifest)
    commands = []

    def run(label: str, argv: list[str], expected: int = 0) -> subprocess.CompletedProcess:
        started = time.monotonic()
        result = subprocess.run(argv, cwd=repository, env=env, capture_output=True, text=True)
        (output / (label + ".stdout")).write_text(result.stdout)
        (output / (label + ".stderr")).write_text(result.stderr)
        commands.append({"label": label, "argv": argv, "exit": result.returncode, "expected_exit": expected,
                         "elapsed_seconds": time.monotonic() - started})
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode != expected:
            raise RuntimeError(label + " failed; see " + str(output / (label + ".stderr")))
        return result

    clang = run("find-clang", ["xcrun", "--find", "clang"]).stdout.strip()
    sdk = run("find-" + args.sdk + "-sdk", ["xcrun", "--sdk", args.sdk, "--show-sdk-path"]).stdout.strip()
    target = lock["iphoneos_target"] if args.sdk == "iphoneos" else lock["target"]
    base = [clang, "-target", target, "-isysroot", sdk,
            "-O1", "-g", "-Wall", "-Wno-unused-function", "-DDM_NVN_M3=1", "-fblocks"]
    if args.sdk == "iphoneos":
        base += ["-Werror=unguarded-availability", "-Werror=unguarded-availability-new"]
    staged = output / "staged"
    shutil.copytree(component / "upstream", staged)
    run("diagnostic-routing-patch", ["/usr/bin/patch", "--batch", "--forward", "-p1",
                                    "-d", str(staged), "-i", str(component / "patches" / "diagnostic-routing.patch")])
    run("ios-vm-interface-patch", ["/usr/bin/patch", "--batch", "--forward", "-p1",
                                  "-d", str(staged), "-i", str(component / "patches" / "ios-vm-interface.patch")])
    for relative, expected in lock["patched_source_sha256"].items():
        if sha(staged / relative) != expected:
            raise RuntimeError("diagnostic routing patch result mismatch: " + relative)
    objdir = output / "objects"
    objdir.mkdir()
    objects = []
    includes = ["-I", str(staged / "dm/host"), "-I", str(staged / "dm/gfx/nvn/include"),
                "-I", str(component / "adapter")]
    for relative in lock["translation_units"]:
        name = relative.replace("/", "__")
        obj = objdir / (name + ".o")
        command = base + includes
        if relative.endswith(".m"):
            command += ["-fobjc-arc", "-fobjc-arc-exceptions"]
        elif relative.endswith(".c"):
            command += ["-std=c11"]
        if not relative.endswith(".S"):
            command += ["-include", str(component / "adapter" / "compat_policy.h")]
        run(name, command + ["-c", str(staged / relative), "-o", str(obj)])
        objects.append(obj)
    adapter = component / "adapter"
    adapter_object = objdir / "host_adapter.o"
    run("host-adapter-object", base + ["-std=c11", "-Wall", "-Wextra", "-Werror", "-c",
                                     str(adapter / "host_adapter.c"), "-o", str(adapter_object)])
    # This only verifies the real current runtime declaration mapping. Its two
    # runtime implementations are deliberately not in the backend link below.
    run("runtime-binding-compile-only", base + ["-std=c11", "-Wall", "-Wextra", "-Werror", "-c",
                                             str(adapter / "runtime_binding.c"), "-o", str(objdir / "runtime_binding.o")])
    for obj in objects:
        text = run(obj.name + "-undefined", ["/usr/bin/nm", "-u", str(obj)]).stdout
        symbols = {line.split()[-1] for line in text.splitlines() if line.split()}
        forbidden = symbols.intersection(lock["forbidden_backend_references"])
        if forbidden:
            raise RuntimeError("unrouted backend diagnostic functions: " + repr(sorted(forbidden)))
    executable = output / "complete-backend-LINK-ONLY-DO-NOT-RUN"
    link = base + [str(x) for x in objects] + [str(adapter_object), str(component / "tests/link_only_entry.c")]
    for framework in lock["frameworks"]:
        link += ["-framework", framework]
    run("complete-backend-link-only", link + ["-o", str(executable)])
    if args.sdk == "macosx":
        # CPU executable contains exactly host_adapter + owned service callbacks.
        # No backend object, GPU device or guest entry is in this probe link.
        probe = output / "host-adapter-CPU-probe"
        run("host-adapter-cpu-compile", base + ["-std=c11", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                                              "-fno-omit-frame-pointer", str(adapter / "host_adapter.c"),
                                              str(component / "tests/host_adapter_probe.c"), "-o", str(probe)])
        for mode in lock["cpu_modes"]:
            run("cpu-" + mode, [str(probe), mode], lock["cpu_expected_exits"].get(mode, 0))
        audit_object = objdir / "cmd-audit-CPU-only.o"
        run("cmd-audit-CPU-only-compile", base + includes + ["-std=c11", "-fsanitize=address,undefined",
             "-fno-omit-frame-pointer", "-Ddlsym=botw_test_dlsym", "-include", str(adapter / "compat_policy.h"),
             "-c", str(staged / "dm/gfx/nvn/nvn_cmd_audit.c"), "-o", str(audit_object)])
        audit_probe = output / "cmd-audit-disabled-CPU-probe"
        run("cmd-audit-CPU-probe-link", base + includes + ["-std=c11", "-fsanitize=address,undefined",
             "-fno-omit-frame-pointer", str(audit_object), str(adapter / "host_adapter.c"),
             str(component / "tests/cmd_audit_disabled_probe.c"), "-o", str(audit_probe)])
        run("cmd-audit-disabled-CPU-probe", [str(audit_probe)])
    after = {relative: sha(component / relative) for relative in before}
    if after != before:
        raise RuntimeError("source changed during build")
    receipt = {"complete_translation_units": len(objects), "compile_exit": 0, "link_exit": 0,
               "link_executable_sha256": sha(executable), "backend_executed": False,
               "real_runtime_linked": False, "runtime_binding_compile_only": True,
               "ios_compiled": args.sdk == "iphoneos", "sdk": args.sdk, "target": target,
               "CPU_probes_executed": args.sdk == "macosx", "GPU_or_guest_or_device_executed": False,
               "cpu_modes_passed": len(lock["cpu_modes"]) if args.sdk == "macosx" else 0,
               "cmd_audit_CPU_probe_passed": True if args.sdk == "macosx" else None,
               "CPU_probe_renderer_objects_linked": False,
               "manifest_environment": str(manifest), "manifest_exists": manifest.is_file(),
               "manifest_validated_by_runtime": False, "source_sha256": before,
               "source_lock_sha256": sha(component / "SOURCE.lock.json"),
               "commands_sha256": sha(output / "commands.json")}
    (output / "final-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"receipt": str(output / "final-receipt.json"), "sha256": sha(output / "final-receipt.json"),
                      "compile_units": len(objects), "cpu_modes": receipt["cpu_modes_passed"],
                      "backend_run": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
