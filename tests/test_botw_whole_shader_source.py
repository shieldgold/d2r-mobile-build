"""Portable hashed source packages; no real Metal device/GPU/guest execution."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'

@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'macOS Foundation and owned compiler port')
class WholeShaderSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='botw-source-package-')
        cls.addClassCleanup(cls.tmp.cleanup)
        work = Path(cls.tmp.name)
        shutil.copytree(COMPONENT / 'upstream', work / 'upstream')
        patch = COMPONENT / 'patches/portable-shader-source.patch'
        if patch.exists():
            run = subprocess.run(['patch','--fuzz=0','-p1','-i',str(patch)],cwd=work/'upstream',capture_output=True,text=True)
            if run.returncode: raise AssertionError(run.stdout+run.stderr)
        nvn = work / 'upstream/dm/gfx/nvn'
        cls.binary = work / 'probe'
        command = ['clang','-fobjc-arc','-fblocks','-Werror','-Wno-deprecated-declarations',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(nvn),'-I'+str(COMPONENT/'adapter'),
            str(COMPONENT/'tests/whole_shader_source_probe.m'),str(nvn/'botw_shader_manifest.m'),
            str(nvn/'botw_shader_runtime.m'),str(COMPONENT/'adapter/whole_shader_publication.m'),
            '-framework','Foundation','-framework','Metal','-o',str(cls.binary)]
        run = subprocess.run(command,capture_output=True,text=True)
        if run.returncode: raise AssertionError(run.stdout+run.stderr)

    def case(self,name):
        run = subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_owned_utf8_source_uses_actual_compiler_port(self): self.case('valid')
    def test_actual_compile_error_is_not_a_function(self): self.case('compile-error')
    def test_missing_entry_is_rejected(self): self.case('function-missing')
    def test_wrong_function_stage_is_rejected(self): self.case('function-type')
    def test_relative_escape_is_rejected(self): self.case('traversal')
    def test_absolute_host_path_is_rejected(self): self.case('absolute-path')
    def test_source_digest_is_checked(self): self.case('hash-mismatch')
    def test_source_symlink_is_rejected(self): self.case('symlink')
    def test_reflection_unknowns_are_rejected(self): self.case('reflection-unknown')
    def test_result_unknowns_are_rejected(self): self.case('result-unknown')
    def test_missing_facts_digest_is_rejected(self): self.case('missing-facts')
    def test_reflection_identity_is_checked(self): self.case('wrong-identity')
    def test_source_package_cannot_claim_compiled(self): self.case('pretend-runnable')
    def test_legacy_schema_is_not_silently_treated_as_source(self): self.case('legacy-schema')
    def test_embedded_nul_is_rejected(self): self.case('embedded-nul')
    def test_invalid_utf8_is_rejected(self): self.case('invalid-utf8')
    def test_duplicate_capture_keys_are_rejected(self): self.case('duplicate')
    def test_loaded_source_bytes_survive_later_file_mutation(self): self.case('owned-after-mutation')
    def test_v1_still_refuses_source_without_trying_source_compiler(self): self.case('compiled-v1-source-rejected')
    def test_full_package_startup_audit_never_invokes_compiler(self): self.case('audit-valid')
    def test_full_package_audit_propagates_owned_error_after_pool_drains(self): self.case('audit-tamper')
    def test_startup_audit_does_not_cache_whole_game_source_bytes(self): self.case('audit-does-not-retain')
    def test_bundle_publication_checks_full_admitted_source_batch(self): self.case('publication-valid')
    def test_publication_rejects_other_game_profile(self): self.case('publication-profile')
    def test_publication_directory_cannot_escape_bundle(self): self.case('publication-path')
    def test_publication_requires_detached_unchanged_facts(self): self.case('publication-facts')
    def test_publication_rechecks_every_source_digest(self): self.case('publication-source')
    def test_publication_checks_admission_digest(self): self.case('publication-admission')
    def test_publication_failure_preserves_output_path(self): self.case('publication-capacity')
    def test_publication_rejects_redirected_botw_directory(self): self.case('publication-botw-symlink')
    def test_publication_rejects_redirected_shader_root(self): self.case('publication-root-symlink')
    def test_publication_rejects_redirected_facts_directory(self): self.case('publication-facts-symlink')
    def test_publication_rejects_redirected_artifact_directory(self): self.case('publication-package-symlink')
