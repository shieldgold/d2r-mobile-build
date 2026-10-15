/* Checked host adaptations under real allocation failures. Synthetic data only. */
#include <stdlib.h>
#include <stdio.h>
#include <assert.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>
static int fail_after;
static int fail_next(void) { return fail_after > 0 && --fail_after == 0; }
static void *test_malloc(size_t n) { return fail_next() ? NULL : malloc(n); }
static void *test_calloc(size_t n, size_t s) { return fail_next() ? NULL : calloc(n, s); }
static void *test_realloc(void *p, size_t n) { return fail_next() ? NULL : realloc(p, n); }
#define malloc test_malloc
#define calloc test_calloc
#define realloc test_realloc
#include "../modules.inc"
#undef malloc
#undef calloc
#undef realloc
#include "test_dirty_port.h"
int main(int argc, char **argv) {
    assert(argc == 3); int nth = atoi(argv[2]); size_t pg = (size_t)getpagesize();
    uint8_t *p = mmap(NULL, pg, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(p != MAP_FAILED); memset(p, 0x31, pg);
    if (!strcmp(argv[1], "owner")) {
        fail_after = nth; assert(nvn_texown_claim_fresh(1, p, pg, NULL) == -1);
        assert(!nvn_texown_find(p, pg, NULL) && !nvn_texown_count(0));
        assert(nvn_texown_claim_fresh(1, p, pg, NULL) == 1);
        assert(!nvn_texown_claim_checked(1, p, pg));
        assert(nvn_texown_claim_checked(0, p, pg) == -1);
    } else if (!strcmp(argv[1], "unconfigured")) {
        assert(!nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 1));
        assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
        uint64_t calls, pages, ns; nvn_dirty_mp_stats(&calls, &pages, &ns); assert(!calls && !pages);
        nvn_texflush_reset_quiescent();
    } else if (!strcmp(argv[1], "hmap")) {
        nvn_hmap map; nvn_hm_init(&map, 8); int value = 1;
        for (uint64_t k = 0; k < 48; k++) assert(!nvn_hm_put_checked(&map, &k, &value, NULL));
        uint32_t cap = map.cap; uint8_t *keys = map.keys; void **vals = map.vals;
        uint64_t k = 48; fail_after = nth;
        assert(nvn_hm_put_checked(&map, &k, &value, NULL) == -1);
        assert(map.cap == cap && map.keys == keys && map.vals == vals && map.n == 48);
        fail_after = 0;
        for (k = 0; k < 48; k++) assert(nvn_hm_get(&map, &k) == &value);
        k = 48; assert(!nvn_hm_get(&map, &k));
        assert(!nvn_hm_put_checked(&map, &k, &value, NULL) && map.n == 49);
        nvn_hm_clear(&map, NULL); free(map.keys); free(map.vals);
    } else {
        test_dirty_install(); assert(!nvn_dirty_add_range_checked(p, pg, NULL, 0));
        if (!strcmp(argv[1], "flush")) {
            fail_after = nth;
            assert(nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 1) == -1);
            assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
            assert(!nvn_texflush_records());
            assert(!nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 1));
            assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
        } else if (!strcmp(argv[1], "invalidate")) {
            assert(!nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 1));
            assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
            fail_after = 1; assert(nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 0) == -1);
            assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
        } else if (!strcmp(argv[1], "reset")) {
            assert(!nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 1));
            assert(nvn_texown_claim_fresh(1, p, pg, NULL) == 1);
            nvn_texown_retire(1); assert(nvn_texown_count(1) == 1);
            assert(nvn_texown_claim_fresh(2, p, pg, NULL) == 0);
            nvn_texflush_reset_quiescent(); nvn_texown_reset_quiescent();
            assert(!nvn_texflush_records() && !nvn_texown_count(1) && !nvn_texown_count(0));
            assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
            assert(nvn_texown_claim_fresh(2, p, pg, NULL) == 1);
            assert(!nvn_texflush_note_checked(2, p, pg, 1, 0, 1, 1));
            assert(nvn_texflush_verify(2, p, pg, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
        } else if (!strcmp(argv[1], "verify")) {
            assert(!nvn_texflush_note_checked(1, p, pg, 1, 0, 1, 1));
            fail_after = 1; assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
            assert(nvn_texflush_verify(1, p, pg, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
        } else assert(!"unknown allocation case");
        nvn_texflush_forget(1); dm_guest_write_prepare(p, pg);
        assert(!nvn_dirty_remove_range_checked(p, NULL, 0));
    }
    nvn_texflush_reset_quiescent(); nvn_texown_reset_quiescent();
    assert(!munmap(p, pg)); printf("allocation %s/%d: PASS\n", argv[1], nth); return 0;
}
