"""Locked complete renderer sources for the normal host link-only build.

This enumerates the same original renderer/ADSP closure as native/CMakeLists;
it does not execute CMake fixtures, workers, an AudioUnit, or guest instructions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inventory(repository: Path, contract: dict) -> None:
    component = repository / contract["component"]
    expected = set(contract["files"])
    present = {str(p.relative_to(component)) for p in component.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    if expected != present:
        raise ValueError("audio component inventory changed")
    for relative, digest in contract["files"].items():
        path = component / relative
        if path.is_symlink() or sha(path) != digest:
            raise ValueError("audio component source lock mismatch: " + relative)


def original_units(prepared: Path) -> list[Path]:
    renderer = sorted((prepared / "src/audio_core/renderer").rglob("*.cpp"))
    adsp = sorted((prepared / "src/audio_core/adsp/apps/audio_renderer").glob("*.cpp"))
    if len(renderer) != 67 or len(adsp) != 2:
        raise ValueError("incomplete fixed renderer/ADSP source closure")
    other = [prepared / "src/audio_core/audio_render_manager.cpp",
             prepared / "src/audio_core/sink/sink_stream.cpp"]
    if any(not path.is_file() for path in other):
        raise ValueError("missing original renderer manager/sink stream")
    return renderer + adsp + other


def prepare(repository: Path, output: Path, contract: dict, run) -> dict:
    verify_inventory(repository, contract)
    component = repository / contract["component"]
    run("audio-native-source-verification", [sys.executable,
        str(component / "tools/verify_native.py")])
    prepared = output / "audio-staged"
    command = [sys.executable, str(component / "tools/prepare_eden.py"), str(prepared)]
    for patch in contract["patches"]:
        command += ["--patch", patch]
    run("audio-original-source-preparation", command)
    actual = {str(path.relative_to(prepared)): sha(path) for path in prepared.rglob("*")
              if path.is_file() and path.name != ".prepared.json"}
    if actual != contract["prepared_source_sha256"]:
        raise ValueError("prepared audio source differs from the reviewed lock")
    units = original_units(prepared)
    sources = [(str(path), "audio/" + str(path.relative_to(prepared))) for path in units]
    sources += [(str(component / relative), "audio/" + relative)
                for relative in contract["adapter_translation_units"]]
    if len(sources) != 79 or len(set(path for path, _ in sources)) != len(sources):
        raise ValueError("incomplete or duplicate complete audio translation units")
    # Every prepared dependency is tied to its original repository blob and its
    # deterministic reviewed patch output. Unknown includes remain forbidden.
    dependencies = {}
    for relative, digest in actual.items():
        original = str(Path(contract["component"]) / "vendor/eden" / relative)
        dependencies[(prepared / relative).resolve()] = {
            "repository_path": original, "compiled_sha256": digest,
            "role": "complete-audio-original-or-patched-source"}
    return {"sources": sources, "dependencies": dependencies,
            "includes": [prepared / "src", component / "native", component / "adapter",
                         component / "platform", component / "vendor/fmt/include"],
            "prepared": prepared}


def verify_prepared(plan: dict, contract: dict) -> None:
    prepared = plan["prepared"]
    actual = {str(path.relative_to(prepared)): sha(path) for path in prepared.rglob("*")
              if path.is_file() and path.name != ".prepared.json"}
    if actual != contract["prepared_source_sha256"] or any(
            path.is_symlink() for path in prepared.rglob("*")):
        raise ValueError("prepared audio source changed during build")
