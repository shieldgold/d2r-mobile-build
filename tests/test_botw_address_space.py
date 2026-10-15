"""Pure host address policy checks through a C fixture, without game assets."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include "address_space.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static const uint64_t singleton = UINT64_C(0x106cac28);
static const uint64_t limit = UINT64_C(1) << 39;
static const uint64_t guard = 0x4000;
static const size_t host_page = 0x4000;
static botw_address_space_region alias = {UINT64_C(0x7000000000), 0x20000000};
static botw_address_space_region stack = {UINT64_C(0x7020000000), 0x10000000};
static char error[256];
static int adapt(uint64_t self, uint64_t resource, uint64_t begin, uint64_t end,
                 uint64_t g, uint64_t bound, size_t page, botw_address_space_policy *out) {
    return botw_address_space_adapt_constructor(self, resource, begin, end, g,
        &alias, &stack, bound, page, out, error, sizeof error);
}
static botw_address_space_policy policy(unsigned kind) {
    botw_address_space_policy p;
    assert(!adapt(singleton + (kind == BOTW_ADDRESS_SPACE_ALIAS ? 0x18 : 0x40),
        singleton, 0x08000000, kind == BOTW_ADDRESS_SPACE_ALIAS ? UINT64_C(1) << 36 : 0x80000000,
        guard, limit, host_page, &p));
    return p;
}
static void domains(void) {
    botw_address_space_policy a = policy(BOTW_ADDRESS_SPACE_ALIAS);
    botw_address_space_policy s = policy(BOTW_ADDRESS_SPACE_STACK);
    assert(a.kind == BOTW_ADDRESS_SPACE_ALIAS && a.object == singleton + 0x18);
    assert(s.kind == BOTW_ADDRESS_SPACE_STACK && s.object == singleton + 0x40);
    assert(a.region.base == alias.base && a.region.size == alias.size);
    assert(s.region.base == stack.base && s.region.size == stack.size);
    assert(a.begin == alias.base + guard && a.end == alias.base + alias.size - guard);
    assert(s.begin == stack.base + guard && s.end == stack.base + stack.size - guard);
    assert(a.guard == guard && a.address_limit == limit && a.host_page == host_page);
    assert(!botw_address_space_validate_allocation(&a, alias.base + guard, 0x40000,
        host_page, error, sizeof error));
    alias.base = limit - alias.size;
    a = policy(BOTW_ADDRESS_SPACE_ALIAS); /* exclusive region end may equal limit */
    assert(a.end + guard == limit);
}
static void bad_constructor(void) {
    botw_address_space_policy p, unchanged;
    memset(&p, 0xa5, sizeof p); unchanged = p;
#define BAD(self, resource, begin, end, g, bound, page) do { \
    assert(adapt(self, resource, begin, end, g, bound, page, &p) == -1); \
    assert(error[0] && !memcmp(&p, &unchanged, sizeof p)); \
} while (0)
    BAD(singleton + 0x20, singleton, 0x08000000, UINT64_C(1)<<36, guard, limit, host_page);
    BAD(0x18, 0, 0x08000000, UINT64_C(1)<<36, guard, limit, host_page);
    BAD(UINT64_MAX, UINT64_MAX - 0x17, 0x08000000, UINT64_C(1)<<36, guard, limit, host_page);
    BAD(singleton + 0x18, singleton, 0, UINT64_C(1)<<36, guard, limit, host_page);
    BAD(singleton + 0x18, singleton, 0x08000000, 0x80000000, guard, limit, host_page);
    BAD(singleton + 0x40, singleton, 0x08000000, UINT64_C(1)<<36, guard, limit, host_page);
    BAD(singleton + 0x18, singleton, 0x08000000, UINT64_C(1)<<36, 0x1000, limit, host_page);
    BAD(singleton + 0x18, singleton, 0x08000000, UINT64_C(1)<<36, guard, UINT64_C(1)<<36, host_page);
    BAD(singleton + 0x18, singleton, 0x08000000, UINT64_C(1)<<36, guard, limit, 0);
    BAD(singleton + 0x18, singleton, 0x08000000, UINT64_C(1)<<36, guard, limit, 0x3000);
    BAD(singleton + 0x18, singleton, 0x08000000, UINT64_C(1)<<36, guard, limit, 0x8000);
    botw_address_space_region saved = alias;
    const botw_address_space_region invalid[] = {
        {0, 0x200000}, {limit, 0x200000}, {UINT64_MAX-0x3fff, 0x8000},
        {limit-0x4000, 0x8000}, {saved.base+1, saved.size},
        {saved.base, saved.size-1}, {saved.base, 0}, {saved.base, guard*4},
        {stack.base, stack.size}
    };
    for (size_t i = 0; i < sizeof invalid / sizeof invalid[0]; i++) {
        alias = invalid[i];
        BAD(singleton + 0x18, singleton, 0x08000000, UINT64_C(1)<<36, guard, limit, host_page);
    }
    alias = saved;
    assert(botw_address_space_adapt_constructor(singleton+0x18, singleton, 0x08000000,
        UINT64_C(1)<<36, guard, NULL, &stack, limit, host_page, &p, NULL, 0) == -1);
    assert(adapt(singleton+0x18, singleton, 0x08000000, UINT64_C(1)<<36,
        guard, limit, host_page, NULL) == -1);
}
static void alignments(void) {
    uint64_t out = 0;
    assert(!botw_address_space_normalize_alignment(0x1000, 0x4000, &out, error, sizeof error) && out == 0x4000);
    assert(!botw_address_space_normalize_alignment(0x200000, 0x4000, &out, error, sizeof error) && out == 0x200000);
    assert(!botw_address_space_normalize_alignment(0x1000, 0x1000, &out, error, sizeof error) && out == 0x1000);
    const uint64_t bad[] = {0, 1, 0x800, 0x1800, 0x3000, UINT64_MAX, UINT64_C(1)<<63};
    for (size_t i=0; i<sizeof bad/sizeof bad[0]; i++) {
        out = 0xa5;
        assert(botw_address_space_normalize_alignment(bad[i], host_page, &out, error, sizeof error) == -1);
        assert(out == 0xa5 && error[0]);
    }
    assert(botw_address_space_normalize_alignment(0x1000, 0x3000, &out, error, sizeof error) == -1);
    assert(botw_address_space_normalize_alignment(0x1000, 0x8000, &out, error, sizeof error) == -1);
    assert(botw_address_space_normalize_alignment(0x1000, host_page, NULL, error, sizeof error) == -1);
}
static void allocations(void) {
    for (unsigned kind=BOTW_ADDRESS_SPACE_ALIAS; kind<=BOTW_ADDRESS_SPACE_STACK; kind++) {
        botw_address_space_policy p = policy(kind); uint64_t a=p.region.base+guard, n=0x40000;
#define CHECK(addr, bytes, align) botw_address_space_validate_allocation(&p, addr, bytes, align, error, sizeof error)
        assert(!CHECK(a,n,0x200000)); /* guard start is aligned; payload is +16K */
        assert(!CHECK(p.region.base+p.region.size-guard-n,n,host_page));
        assert(CHECK(0,n,host_page) == -1);
        assert(CHECK(a-host_page,n,host_page) == -1);
        assert(CHECK(p.region.base+p.region.size-guard-n+host_page,n,host_page) == -1);
        assert(CHECK(a+0x1000,n,host_page) == -1);
        assert(CHECK(a,n+0x1000,host_page) == -1);
        assert(CHECK(a,0,host_page) == -1);
        assert(CHECK(a,UINT64_MAX,host_page) == -1);
        assert(CHECK(UINT64_MAX,n,host_page) == -1);
        assert(CHECK(a,n,0x1000) == -1); /* normalize first on a 16K host */
        assert(CHECK(a,n,0x3000) == -1);
        assert(CHECK(a+host_page,n,0x200000) == -1);
        p.guard = 0; assert(CHECK(a,n,host_page) == -1);
        p = policy(kind); p.end++; assert(CHECK(a,n,host_page) == -1);
        p = policy(kind); p.address_limit = UINT64_C(1)<<36; assert(CHECK(a,n,host_page) == -1);
    }
    assert(botw_address_space_validate_allocation(NULL, 0, 0, 0, error, sizeof error) == -1);
}
int main(int argc, char **argv) {
    assert(argc==2);
    if (!strcmp(argv[1],"domains")) domains();
    else if (!strcmp(argv[1],"constructor")) bad_constructor();
    else if (!strcmp(argv[1],"alignment")) alignments();
    else if (!strcmp(argv[1],"allocation")) allocations();
    else assert(0);
    puts("ok"); return 0;
}
'''


class AddressSpaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-address-space-')
        cls.exe = Path(cls.build.name) / 'fixture'
        source = ROOT / 'native/botw_host/address_space.c'
        if not source.exists():
            raise AssertionError('host address policy helper is missing')
        fixture = Path(cls.build.name) / 'fixture.c'
        fixture.write_text(FIXTURE)
        subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                        '-Werror', '-I', str(source.parent), str(fixture), str(source),
                        '-o', str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check_case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'ok')

    def test_alias_and_stack_use_their_real_regions_without_narrowing_pointers(self):
        self.check_case('domains')

    def test_constructor_rejects_unknown_sdk_layout_invalid_ranges_and_overflow(self):
        self.check_case('constructor')

    def test_alignment_normalization_preserves_stronger_legal_alignment(self):
        self.check_case('alignment')

    def test_returned_addresses_preserve_both_guards_and_host_page_boundaries(self):
        self.check_case('allocation')


if __name__ == '__main__':
    unittest.main()
