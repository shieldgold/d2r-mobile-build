#!/usr/bin/env python3
"""Compile the complete pinned host and link it without executing the result."""
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


def verify_sources(component: Path, lock: dict) -> dict[str, str]:
    """Verify the entire component, including patches and the build driver."""
    expected = set(lock["files"])
    present = {str(p.relative_to(component)) for p in component.rglob("*")
               if p.is_file() and p.name != "SOURCE.lock.json" and
               "__pycache__" not in p.parts}
    if present != expected:
        raise ValueError("component inventory changed: " + repr({
            "added": sorted(present - expected), "missing": sorted(expected - present)}))
    result = {}
    for relative, entry in lock["files"].items():
        path = component / relative
        if path.is_symlink() or not path.is_file() or sha(path) != entry["sha256"]:
            raise ValueError("source lock mismatch: " + relative)
        result[relative] = entry["sha256"]
    return result


def parse_dependencies(path: Path) -> list[Path]:
    # clang -MMD make syntax escapes spaces and joins continued lines.
    import shlex
    content = path.read_text().replace("\\\n", "")
    if ":" not in content:
        raise ValueError("invalid compiler dependency record")
    return [Path(p).resolve() for p in shlex.split(content.split(":", 1)[1])]


def classify_dependencies(dependencies: set[Path], staged: Path, component: Path,
                          sdk: Path, locked: dict, patched: dict,
                          repository: Path | None = None, external: dict | None = None) -> tuple[list, list]:
    provenance, sdk_inputs = [], []
    staged, component, sdk = staged.resolve(), component.resolve(), sdk.resolve()
    for recorded in sorted(dependencies):
        path = recorded.resolve()
        if staged in path.parents:
            original = "upstream/" + str(path.relative_to(staged))
            role = "patched-upstream" if str(path.relative_to(staged)) in patched else "original-upstream"
        elif component in path.parents:
            original = str(path.relative_to(component))
            role = "thin-platform-adapter"
        elif repository and repository.resolve() in path.parents and external and \
                str(path.relative_to(repository.resolve())) in external:
            relative = str(path.relative_to(repository.resolve()))
            if sha(path) != external[relative]["sha256"]:
                raise ValueError("external source lock mismatch: " + relative)
            provenance.append({"repository_path": relative, "compiled_sha256": sha(path),
                               "role": "existing-complete-resource-provider"})
            continue
        elif sdk in path.parents:
            # Apple clang includes SDKSettings.json in -MMD output. Retain its
            # real hash separately; it is selected SDK metadata, not our code.
            sdk_inputs.append({"sdk_relative_path": str(path.relative_to(sdk)), "sha256": sha(path)})
            continue
        else:
            raise ValueError("unlocked non-system compiler dependency: " + str(path))
        if original not in locked:
            raise ValueError("compiler dependency absent from source lock: " + original)
        expected = patched.get(str(path.relative_to(staged)), locked[original]) if staged in path.parents else locked[original]
        actual = sha(path)
        if actual != expected:
            raise ValueError("compiled source lock mismatch: " + original)
        provenance.append({"component_path": original, "compiled_sha256": actual, "role": role})
    return provenance, sdk_inputs


def verify_staged(staged: Path, before: dict, patched: dict) -> None:
    for path, digest in before.items():
        if path.startswith("upstream/"):
            relative = path.removeprefix("upstream/")
            source = staged / relative
            if source.is_symlink() or sha(source) != patched.get(relative, digest):
                raise ValueError("staged source lock mismatch: " + relative)


def main() -> int:
    component = Path(__file__).resolve().parents[1]
    repository = component.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sdk", choices=("macosx", "iphoneos"), default="macosx")
    args = parser.parse_args()
    output = args.output.resolve()
    if output == component or component in output.parents:
        parser.error("output must be outside the component")
    output.mkdir(parents=True, exist_ok=False)
    lock_path = component / "SOURCE.lock.json"
    commands: list[dict] = []
    receipt: dict = {"schema": "botw-whole-host-link-only-v1", "sdk": args.sdk,
                     "host_or_guest_or_GPU_executed": False, "runtime_configured": False,
                     "installable_candidate": False, "compile_exit": None, "link_exit": None,
                     "source_stable": False}
    before: dict[str, str] = {}

    def save() -> None:
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        receipt["commands_sha256"] = sha(output / "commands.json")
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(label: str, argv: list[str]) -> subprocess.CompletedProcess:
        started = time.monotonic()
        result = subprocess.run(argv, cwd=repository, env=os.environ.copy(),
                                capture_output=True, text=True)
        (output / (label + ".stdout")).write_text(result.stdout)
        (output / (label + ".stderr")).write_text(result.stderr)
        commands.append({"label": label, "argv": argv, "exit": result.returncode,
                         "seconds": time.monotonic() - started})
        save()
        if result.returncode:
            raise RuntimeError(label + " failed: " + str(output / (label + ".stderr")))
        return result

    try:
        lock = json.loads(lock_path.read_text())
        before = verify_sources(component, lock)
        external = lock.get("external_files", {})
        for relative, entry in external.items():
            path = repository / relative
            if path.is_symlink() or sha(path) != entry["sha256"]:
                raise ValueError("external source lock mismatch: " + relative)
        receipt["source_lock_sha256"] = sha(lock_path)
        receipt["source_sha256"] = before
        receipt["external_source_sha256"] = {p: row["sha256"] for p, row in external.items()}
        clang = run("find-clang", ["xcrun", "--find", "clang"]).stdout.strip()
        sdk = run("find-sdk", ["xcrun", "--sdk", args.sdk, "--show-sdk-path"]).stdout.strip()
        sdk_version = run("sdk-version", ["xcrun", "--sdk", args.sdk, "--show-sdk-version"]).stdout.strip()
        target = lock["targets"][args.sdk]
        receipt.update({"target": target, "actual_sdk_version": sdk_version})
        staged = output / "staged"
        shutil.copytree(component / "upstream", staged)
        for patch in lock["patches"]:
            run(Path(patch).stem, ["/usr/bin/patch", "--batch", "--forward", "-p1",
                "-d", str(staged), "-i", str(component / patch)])
        patched = {p: sha(staged / p) for p in lock["patched_source_sha256"]}
        if patched != lock["patched_source_sha256"]:
            raise ValueError("patched source bytes differ from the reviewed lock")
        verify_staged(staged, before, patched)
        receipt["patched_source_sha256"] = patched
        units = json.loads((component / "UPSTREAM_UNITS.json").read_text())
        upstream = units["ios_translation_units" if args.sdk == "iphoneos" else "macos_translation_units"]
        adapter = component / "adapter"
        frontend = component / "frontend"
        common = [clang, "-target", target, "-isysroot", sdk, "-O1", "-g", "-Wall",
                  "-Wno-unused-function", "-fblocks", "-DDM_NVN_M3=1",
                  "-Werror=unguarded-availability", "-Werror=unguarded-availability-new",
                  "-I", str(staged / "dm/host"), "-I", str(staged / "dm/gfx/nvn/include"),
                  "-I", str(adapter), "-I", str(frontend),
                  "-I", str(repository / "native/botw_resources")]
        if args.sdk == "iphoneos":
            common += ["-DDM_SHELL=1"]
        objects = []
        dependencies: set[Path] = set()
        object_dir = output / "objects"
        object_dir.mkdir()
        sources = [(str(staged / p), p) for p in upstream]
        sources += [(str(component / p), p) for p in lock["adapter_translation_units"]]
        sources += [(str(repository / p), p) for p in lock["external_translation_units"]]
        if args.sdk == "iphoneos":
            sources += [(str(frontend / p), "frontend/" + p) for p in
                        ("whole_main.m", "whole_frontend.m", "whole_front_state.c")]
        receipt["expected_upstream_translation_units"] = len(upstream)
        receipt["expected_total_translation_units"] = len(sources)
        for source, relative in sources:
            label = relative.replace("/", "__")
            obj = object_dir / (label + ".o")
            dep = object_dir / (label + ".d")
            argv = common.copy()
            if relative.endswith(".m"):
                argv += ["-fobjc-arc", "-fobjc-arc-exceptions"]
            elif relative.endswith(".c"):
                argv += ["-std=gnu11"]
            if not relative.endswith(".S"):
                argv += ["-include", str(adapter / "whole_policy.h")]
            if relative == "dm/host/main.c" and args.sdk == "iphoneos":
                argv += ["-Dmain=botw_whole_host_main"]
            run(label, argv + ["-MMD", "-MF", str(dep), "-c", source, "-o", str(obj)])
            dependencies.update(parse_dependencies(dep))
            objects.append(obj)
            receipt["compiled_translation_units"] = len(objects)
        receipt["compile_exit"] = 0
        provenance, sdk_inputs = classify_dependencies(dependencies, staged, component,
                                                       Path(sdk), before, patched, repository, external)
        receipt["compiler_dependency_manifest"] = provenance
        receipt["actual_compiler_dependency_count"] = len(provenance)
        receipt["selected_sdk_dependencies"] = sdk_inputs
        for obj in objects:
            undefined = run(obj.stem + "-undefined", ["/usr/bin/nm", "-u", str(obj)]).stdout
            symbols = {line.split()[-1] for line in undefined.splitlines() if line.split()}
            forbidden = symbols.intersection(lock["forbidden_external_references"])
            if forbidden:
                raise ValueError("unrouted host diagnostic: " + repr(sorted(forbidden)))
        executable = output / "whole-host-LINK-ONLY-DO-NOT-RUN"
        argv = common + [str(obj) for obj in objects]
        for framework in lock["frameworks"][args.sdk]:
            argv += ["-framework", framework]
        run("whole-host-link-only", argv + ["-o", str(executable)])
        receipt.update({"link_exit": 0, "link_executable_sha256": sha(executable)})
        # Inspect the actual linked file for this target. iOS conditional
        # compilation may change registration; a Mac table cannot prove it.
        registry_path = output / "swforce-registry.json"
        run("swforce-static-audit", [sys.executable,
            str(component / "scripts/read_swforce_registry.py"),
            "--input", str(executable), "--output", str(registry_path),
            "--expect-metadata-sha256", lock["swforce_metadata_sha256"]])
        registry_bytes = registry_path.read_bytes()
        registry = json.loads(registry_bytes)
        if (registry["binary_sha256"] != receipt["link_executable_sha256"] or
                sha(executable) != receipt["link_executable_sha256"] or
                registry["expected_registry_verified"] is not True or
                registry["canonical_metadata_sha256"] != lock["swforce_metadata_sha256"] or
                registry["count"] != lock["swforce_record_count"]):
            raise ValueError("linked executable/static registry identity changed")
        receipt["swforce_registry"] = {
            "report_sha256": hashlib.sha256(registry_bytes).hexdigest(), "count": registry["count"],
            "canonical_metadata_sha256": registry["canonical_metadata_sha256"],
            "actual_linked_binary_verified": True}
        if sha(lock_path) != receipt["source_lock_sha256"] or verify_sources(component, lock) != before:
            raise ValueError("source changed during build")
        if any((repository / p).is_symlink() or sha(repository / p) != row["sha256"]
               for p, row in external.items()):
            raise ValueError("external source changed during build")
        verify_staged(staged, before, patched)
        receipt["source_stable"] = True
    except Exception as error:
        receipt["error"] = str(error)
        if before:
            try:
                stable = (sha(lock_path) == receipt["source_lock_sha256"] and
                          verify_sources(component, lock) == before and
                          all(not (repository / p).is_symlink() and sha(repository / p) == row["sha256"]
                              for p, row in external.items()))
                if "staged" in locals() and staged.exists() and "patched" in locals():
                    verify_staged(staged, before, patched)
                receipt["source_stable"] = stable
            except (OSError, ValueError, KeyError):
                receipt["source_stable"] = False
        save()
        print(str(error), file=sys.stderr)
        return 1
    save()
    print(json.dumps({"receipt": str(output / "receipt.json"), "sha256": sha(output / "receipt.json"),
                      "compiled": receipt["compiled_translation_units"], "linked": True,
                      "executed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
