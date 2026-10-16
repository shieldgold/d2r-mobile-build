// NVN virtual pool unit tests (nvn_vpool.c). All memory is owned by the test; no game data.
// Deterministic cases cover the MapVirtual contract; a randomized differential test checks
// spans, bytes and generations against a naive per-page model; a stress test races leases
// against remaps and physical pools whose storage is unmapped right after finalize.
#include "../nvn_vpool.h"
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define PG ((uint64_t)NVN_VPOOL_PAGE)
static int g_fail, g_checks;
#define CHECK(c, ...) do { g_checks++; if (!(c)) { g_fail++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static uint64_t rng = 0x9E3779B97F4A7C15ull;
static uint64_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }

// Physical storage deliberately 4 KiB-biased: 4 KiB aligned but never 16 KiB aligned.
typedef struct { char tag; uint8_t *raw, *mem; size_t rawsz, size; } pbuf;
static pbuf pb_new(char tag, size_t size) {
    pbuf b = {tag, NULL, NULL, size + 3 * 16384, size};
    b.raw = mmap(NULL, b.rawsz, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (b.raw == MAP_FAILED) { perror("mmap"); exit(2); }
    b.mem = b.raw + 16384 - ((uintptr_t)b.raw % 16384) + 4096;
    return b;
}
static void pb_free(pbuf *b) { munmap(b->raw, b->rawsz); b->raw = b->mem = NULL; }
// Byte value encodes (pool tag, offset) so any misplaced read is detectable.
static uint8_t pat(char tag, uint64_t off, uint8_t salt) { return (uint8_t)(tag * 31 + (off >> 12) * 7 + off * 13 + salt); }
static void pb_fill(pbuf *b, uint8_t salt) { for (uint64_t i = 0; i < b->size; i++) b->mem[i] = pat(b->tag, i, salt); }

static NVNmappingRequest mreq(const void *phys, uint64_t poff, uint64_t voff, uint64_t size, int cls) {
    return (NVNmappingRequest){(const NVNmemoryPool *)phys, poff, voff, size, cls, 0};
}
static NVNmappingRequest ureq(uint64_t voff, uint64_t size) { return mreq(NULL, 0, voff, size, 0); }

static int map1(const void *v, NVNmappingRequest r) { return nvn_vpool_map(v, &r, 1, NULL, 0); }

// Expected bytes for virtual [voff, voff + n) given a per-page oracle.
typedef struct { const pbuf *p; uint64_t poff; } mpage;
static int bytes_match(const uint8_t *got, const mpage *pages, uint64_t voff, uint64_t n, uint8_t salt) {
    for (uint64_t i = 0; i < n; i++) {
        uint64_t v = voff + i;
        const mpage *m = &pages[v / PG];
        if (!m->p || got[i] != pat(m->p->tag, m->poff + v % PG, salt)) return 0;
    }
    return 1;
}

static vm_prot_t region_prot(uint64_t addr) {
    mach_vm_address_t a = addr; mach_vm_size_t sz = 0;
    vm_region_basic_info_data_64_t info; mach_msg_type_number_t cnt = VM_REGION_BASIC_INFO_COUNT_64; mach_port_t obj;
    if (mach_vm_region(mach_task_self(), &a, &sz, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &cnt, &obj) != KERN_SUCCESS || a > addr) return -1;
    return info.protection;
}

static void test_layout(void) {
    CHECK(sizeof(NVNmappingRequest) == 40, "request size %zu", sizeof(NVNmappingRequest));
    CHECK(offsetof(NVNmappingRequest, physicalOffset) == 8 && offsetof(NVNmappingRequest, virtualOffset) == 16 &&
          offsetof(NVNmappingRequest, size) == 24 && offsetof(NVNmappingRequest, storageClass) == 32, "request offsets");
}

static void test_register(void) {
    static char vo, po, vo2;
    uint64_t base = 0; int above = -1;
    CHECK(nvn_vpool_virt_register(&vo, 0, &base, NULL) == NVN_VPOOL_INVALID, "zero-size virtual pool rejected");
    CHECK(nvn_vpool_virt_register(&vo, PG + 4096, &base, NULL) == NVN_VPOOL_INVALID, "non-page-multiple virtual pool rejected");
    CHECK(nvn_vpool_virt_register(&vo, 8 * PG, &base, &above) == NVN_VPOOL_OK, "virtual register");
    CHECK(base && base % PG == 0, "base %#llx is 64 KiB aligned", (unsigned long long)base);
    CHECK(!above && base + 8 * PG <= NVN_VPOOL_VA_LIMIT, "base %#llx below the 40-bit limit", (unsigned long long)base);
    CHECK(region_prot(base) == VM_PROT_NONE && region_prot(base + 8 * PG - 1) == VM_PROT_NONE, "token range is PROT_NONE");
    CHECK(nvn_vpool_virt_register(&vo, 8 * PG, &(uint64_t){0}, NULL) == NVN_VPOOL_E_DUPLICATE, "duplicate virtual owner");
    pbuf p = pb_new('p', 4 * PG);
    CHECK(nvn_vpool_phys_register(&vo, p.mem, p.size) == NVN_VPOOL_E_DUPLICATE, "owner live as virtual cannot register physical");
    CHECK(nvn_vpool_phys_register(&po, p.mem + 1, p.size) == NVN_VPOOL_INVALID, "physical storage must be 4 KiB aligned");
    CHECK(nvn_vpool_phys_register(&po, p.mem, 0) == NVN_VPOOL_INVALID, "zero-size physical pool rejected");
    CHECK(nvn_vpool_phys_register(&po, (void *)(uintptr_t)(base + PG), PG) == NVN_VPOOL_E_OVERLAP, "physical storage inside a virtual token range rejected");
    CHECK(nvn_vpool_phys_register(&po, p.mem, p.size) == NVN_VPOOL_OK, "physical register (4 KiB-biased)");
    CHECK(nvn_vpool_phys_register(&po, p.mem, p.size) == NVN_VPOOL_E_DUPLICATE, "duplicate physical owner");
    uint64_t base2 = 0;
    CHECK(nvn_vpool_virt_register(&vo2, 2 * PG, &base2, NULL) == NVN_VPOOL_OK && (base2 + 2 * PG <= base || base2 >= base + 8 * PG), "second reservation is disjoint");
    CHECK(nvn_vpool_virt_finalize(&vo) == NVN_VPOOL_OK && nvn_vpool_virt_finalize(&vo2) == NVN_VPOOL_OK, "virtual finalize");
    CHECK(nvn_vpool_phys_finalize(&po, NULL) == NVN_VPOOL_OK, "physical finalize");
    CHECK(nvn_vpool_phys_finalize(&po, NULL) == NVN_VPOOL_E_UNKNOWN_POOL, "double physical finalize");
    CHECK(nvn_vpool_virt_finalize(&vo) == NVN_VPOOL_E_UNKNOWN_POOL, "double virtual finalize");
    pb_free(&p);
}

// 4 KiB-biased storage, 64 KiB mappings, contiguous pointers, cross-pool gathers and
// physical write visibility (inside one lease and across leases).
static void test_resolve_and_read(void) {
    static char vo, po, qo;
    pbuf p = pb_new('P', 8 * PG), q = pb_new('Q', 4 * PG);
    pb_fill(&p, 0); pb_fill(&q, 0);
    CHECK((uintptr_t)p.mem % 4096 == 0 && (uintptr_t)p.mem % 16384 != 0, "physical storage is 4 KiB but not 16 KiB aligned");
    uint64_t base;
    nvn_vpool_virt_register(&vo, 16 * PG, &base, NULL);
    nvn_vpool_phys_register(&po, p.mem, p.size);
    nvn_vpool_phys_register(&qo, q.mem, q.size);

    NVNmappingRequest rs[] = {mreq(&po, 2 * PG, 0, 2 * PG, 0x100), mreq(&qo, PG, 2 * PG, PG, 0), mreq(&po, 0, 3 * PG, PG, 0x100)};
    CHECK(nvn_vpool_map(&vo, rs, 3, NULL, 0) == NVN_VPOOL_OK, "map batch with classes 0 and 0x100");

    nvn_vpool_lease l;
    CHECK(nvn_vpool_lease_acquire(&l, base + 4096, 2 * PG - 8192) == NVN_VPOOL_OK, "contiguous lease");
    CHECK(l.contiguous && l.nspans == 1 && l.phys_owner == &po && l.cpu == p.mem + 2 * PG + 4096, "contiguous lease points at the physical bytes");
    CHECK(nvn_vpool_lease_read(&l, l.len, NULL, 0) == NVN_VPOOL_OK, "empty copy at lease end needs no destination");
    CHECK(nvn_vpool_lease_read(&l, 0, NULL, 1) == NVN_VPOOL_INVALID, "nonempty copy rejects null destination");
    nvn_vpool_lease_release(&l);

    mpage pages[16] = {{&p, 2 * PG}, {&p, 3 * PG}, {&q, PG}, {&p, 0}};
    uint64_t a = PG + 1234, n = 3 * PG - 5000;
    CHECK(nvn_vpool_lease_acquire(&l, base + a, n) == NVN_VPOOL_OK, "cross-span lease");
    CHECK(!l.contiguous && l.nspans == 3, "three pieces, not contiguous (got %u, %d)", l.nspans, l.contiguous);
    uint8_t *buf = malloc(n);
    CHECK(nvn_vpool_lease_read(&l, 0, buf, n) == NVN_VPOOL_OK && bytes_match(buf, pages, a, n, 0), "gather read matches physical bytes");
    nvn_vpool_span s;
    CHECK(nvn_vpool_lease_span(&l, 1, &s) == NVN_VPOOL_OK && s.phys_owner == &qo && s.off == PG - 1234 && s.len == PG && s.cpu == q.mem + PG, "middle span describes Q");
    CHECK(nvn_vpool_lease_span(&l, 3, &s) == NVN_VPOOL_INVALID, "span index past the end");
    // Physical writes while the lease is held must be visible to the next read: no shadow copies.
    // Virtual PG + 2000 is P + 3 * PG + 2000; virtual 2 * PG + 5 is Q + PG + 5.
    p.mem[3 * PG + 2000] ^= 0xFF; q.mem[PG + 5] ^= 0xFF;
    uint8_t x, y;
    nvn_vpool_lease_read(&l, PG + 2000 - a, &x, 1);
    nvn_vpool_lease_read(&l, 2 * PG + 5 - a, &y, 1);
    CHECK(x == p.mem[3 * PG + 2000] && y == q.mem[PG + 5] && x != pat('P', 3 * PG + 2000, 0), "physical writes visible inside one lease");
    CHECK(nvn_vpool_lease_read(&l, n - 1, buf, 2) == NVN_VPOOL_INVALID, "read past the lease is rejected");
    nvn_vpool_lease_release(&l);
    pb_fill(&p, 9); pb_fill(&q, 9);
    CHECK(nvn_vpool_lease_acquire(&l, base + a, n) == NVN_VPOOL_OK && nvn_vpool_lease_read(&l, 0, buf, n) == NVN_VPOOL_OK && bytes_match(buf, pages, a, n, 9), "rewritten physical bytes visible to a new lease");
    nvn_vpool_lease_release(&l);

    // Address classification: no ambiguous physical fallback.
    CHECK(nvn_vpool_lease_acquire(&l, (uint64_t)(uintptr_t)p.mem, 64) == NVN_VPOOL_NOT_VIRTUAL, "physical address is not virtual");
    CHECK(nvn_vpool_lease_acquire(&l, base + 4 * PG, 16) == NVN_VPOOL_UNMAPPED, "unmapped page");
    CHECK(nvn_vpool_lease_acquire(&l, base + 3 * PG, PG + 1) == NVN_VPOOL_UNMAPPED, "range running into a hole");
    CHECK(nvn_vpool_lease_acquire(&l, base - 4096, 8192) == NVN_VPOOL_INVALID, "range straddling the pool start");
    CHECK(nvn_vpool_lease_acquire(&l, base + 16 * PG - 4096, 8192) == NVN_VPOOL_INVALID, "range straddling the pool end");
    CHECK(nvn_vpool_lease_acquire(&l, base, 0) == NVN_VPOOL_INVALID, "zero-length lease");
    CHECK(nvn_vpool_lease_acquire(&l, UINT64_MAX - 10, 100) == NVN_VPOOL_INVALID, "overflowing lease");

    nvn_vpool_virt_finalize(&vo);
    nvn_vpool_phys_finalize(&po, NULL); nvn_vpool_phys_finalize(&qo, NULL);
    free(buf); pb_free(&p); pb_free(&q);
}

// Overlap, last-request-wins, splitting, coalescing and unmapping.
static void test_overlap(void) {
    static char vo, po, qo;
    pbuf p = pb_new('P', 8 * PG), q = pb_new('Q', 4 * PG);
    pb_fill(&p, 0); pb_fill(&q, 0);
    uint64_t base; nvn_vpool_virt_register(&vo, 8 * PG, &base, NULL);
    nvn_vpool_phys_register(&po, p.mem, p.size); nvn_vpool_phys_register(&qo, q.mem, q.size);
    nvn_vpool_stats_t st;

    NVNmappingRequest r1[] = {mreq(&po, 0, 0, 4 * PG, 0x100), mreq(&qo, 2 * PG, PG, PG, 0x100)};
    CHECK(nvn_vpool_map(&vo, r1, 2, NULL, 0) == NVN_VPOOL_OK, "map then overlap in one batch");
    nvn_vpool_stats(&st);
    CHECK(st.spans == 3, "split into three spans (got %llu)", (unsigned long long)st.spans);
    mpage pages[8] = {{&p, 0}, {&q, 2 * PG}, {&p, 2 * PG}, {&p, 3 * PG}};
    uint8_t *buf = malloc(8 * PG);
    nvn_vpool_lease l;
    CHECK(nvn_vpool_lease_acquire(&l, base, 4 * PG) == NVN_VPOOL_OK && nvn_vpool_lease_read(&l, 0, buf, 4 * PG) == 0 && bytes_match(buf, pages, 0, 4 * PG, 0), "later request wins inside the batch");
    nvn_vpool_lease_release(&l);

    // Remap the hole Q left back to P at the matching offset in one batch: spans merge again.
    CHECK(map1(&vo, mreq(&po, PG, PG, PG, 0)) == NVN_VPOOL_OK, "restore P");
    nvn_vpool_stats(&st);
    CHECK(st.spans == 3, "different generations are not merged (got %llu)", (unsigned long long)st.spans);
    CHECK(nvn_vpool_lease_acquire(&l, base, 4 * PG) == NVN_VPOOL_OK && l.contiguous && l.nspans == 3 && l.cpu == p.mem, "contiguous across spans from different batches");
    nvn_vpool_lease_release(&l);
    NVNmappingRequest r2[] = {mreq(&po, 0, 0, 2 * PG, 0), mreq(&po, 2 * PG, 2 * PG, 2 * PG, 0x100)};
    nvn_vpool_map(&vo, r2, 2, NULL, 0);
    nvn_vpool_stats(&st);
    CHECK(st.spans == 1, "contiguous requests of one batch coalesce (got %llu)", (unsigned long long)st.spans);

    // Same range twice in one batch: the second wins. Then map + unmap in one batch leaves a hole.
    NVNmappingRequest r3[] = {mreq(&qo, 0, 5 * PG, PG, 0), mreq(&po, 7 * PG, 5 * PG, PG, 0)};
    nvn_vpool_map(&vo, r3, 2, NULL, 0);
    pages[5] = (mpage){&p, 7 * PG};
    CHECK(nvn_vpool_lease_acquire(&l, base + 5 * PG, PG) == NVN_VPOOL_OK && l.phys_owner == &po && nvn_vpool_lease_read(&l, 0, buf, PG) == 0 && bytes_match(buf, pages, 5 * PG, PG, 0), "identical ranges: last wins");
    nvn_vpool_lease_release(&l);
    NVNmappingRequest r4[] = {mreq(&qo, 0, 6 * PG, 2 * PG, 0), ureq(6 * PG, PG)};
    nvn_vpool_map(&vo, r4, 2, NULL, 0);
    CHECK(nvn_vpool_lease_acquire(&l, base + 6 * PG, 1) == NVN_VPOOL_UNMAPPED, "map then unmap in one batch leaves a hole");
    CHECK(nvn_vpool_lease_acquire(&l, base + 7 * PG, PG) == NVN_VPOOL_OK && l.cpu == q.mem + PG, "remainder of the unmapped span keeps its offset");
    nvn_vpool_lease_release(&l);

    // Unmap ignores physicalOffset and storageClass entirely.
    NVNmappingRequest u = {NULL, 0x123, 2 * PG, PG, 0x7777, 0};
    CHECK(map1(&vo, u) == NVN_VPOOL_OK, "unmap with junk offset and class succeeds");
    CHECK(nvn_vpool_lease_acquire(&l, base + 2 * PG, 1) == NVN_VPOOL_UNMAPPED, "punched hole");
    CHECK(nvn_vpool_lease_acquire(&l, base + PG, PG) == NVN_VPOOL_OK && l.cpu == p.mem + PG, "page before the hole intact");
    nvn_vpool_lease_release(&l);
    CHECK(nvn_vpool_lease_acquire(&l, base + 3 * PG, PG) == NVN_VPOOL_OK && l.cpu == p.mem + 3 * PG, "page after the hole intact");
    nvn_vpool_lease_release(&l);
    CHECK(map1(&vo, ureq(0, 8 * PG)) == NVN_VPOOL_OK, "unmap everything (including holes)");
    nvn_vpool_stats(&st);
    CHECK(st.spans == 0, "no spans left");

    nvn_vpool_virt_finalize(&vo); nvn_vpool_phys_finalize(&po, NULL); nvn_vpool_phys_finalize(&qo, NULL);
    free(buf); pb_free(&p); pb_free(&q);
}

// Every invalid batch must leave the mapping and generations untouched.
static void test_invalid_batches(void) {
    static char vo, po, other;
    pbuf p = pb_new('P', 4 * PG); pb_fill(&p, 0);
    uint64_t base; nvn_vpool_virt_register(&vo, 4 * PG, &base, NULL);
    nvn_vpool_phys_register(&po, p.mem, p.size);
    map1(&vo, mreq(&po, 0, 0, 2 * PG, 0x100));
    nvn_vpool_lease l; nvn_vpool_lease_acquire(&l, base, 2 * PG); nvn_vpool_lease_release(&l);
    uint64_t g0 = nvn_vpool_generation(), vg0 = nvn_vpool_virt_gen(&vo), lg0 = l.gen;
    nvn_vpool_stats_t s0; nvn_vpool_stats(&s0);

    const NVNmappingRequest good = mreq(&po, 2 * PG, 0, PG, 0x100);
    const struct { NVNmappingRequest r; int want; const char *what; } bad[] = {
        {mreq(&po, 0, 0, 0, 0), NVN_VPOOL_INVALID, "zero size"},
        {ureq(0, 0), NVN_VPOOL_INVALID, "zero-size unmap"},
        {mreq(&po, 0, 4096, PG, 0), NVN_VPOOL_INVALID, "misaligned virtual offset"},
        {mreq(&po, 4096, 0, PG, 0), NVN_VPOOL_INVALID, "misaligned physical offset"},
        {mreq(&po, 0, 0, PG + 4096, 0), NVN_VPOOL_INVALID, "misaligned size"},
        {mreq(&po, 0, 3 * PG, 2 * PG, 0), NVN_VPOOL_INVALID, "virtual range past the end"},
        {mreq(&po, 3 * PG, 0, 2 * PG, 0), NVN_VPOOL_INVALID, "physical range past the end"},
        {mreq(&po, 0, UINT64_MAX - PG + 1, 2 * PG, 0), NVN_VPOOL_INVALID, "virtual offset + size wraps"},
        {mreq(&po, UINT64_MAX - PG + 1, 0, 2 * PG, 0), NVN_VPOOL_INVALID, "physical offset + size wraps"},
        {ureq(UINT64_MAX - PG + 1, 2 * PG), NVN_VPOOL_INVALID, "unmap range wraps"},
        {mreq(&other, 0, 0, PG, 0), NVN_VPOOL_E_UNKNOWN_POOL, "unregistered physical pool"},
        {mreq(&vo, 0, 0, PG, 0), NVN_VPOOL_E_UNKNOWN_POOL, "virtual pool used as physical"},
        {mreq(&po, 0, 0, PG, 0x107), NVN_VPOOL_E_CLASS, "storage class 0x107"},
        {mreq(&po, 0, 0, PG, 1), NVN_VPOOL_E_CLASS, "storage class 1"},
        {mreq(&po, 0, 0, PG, -1), NVN_VPOOL_E_CLASS, "storage class -1"},
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        NVNmappingRequest b[3] = {good, bad[i].r, good};
        char err[160] = "";
        int st = nvn_vpool_map(&vo, b, 3, err, sizeof err);
        nvn_vpool_stats_t s1; nvn_vpool_stats(&s1);
        CHECK(st == bad[i].want && err[0] && nvn_vpool_generation() == g0 && nvn_vpool_virt_gen(&vo) == vg0 && s1.spans == s0.spans,
              "%s: status %s (want %s), err '%s', no mutation", bad[i].what, nvn_vpool_status_name(st), nvn_vpool_status_name(bad[i].want), err);
    }
    CHECK(nvn_vpool_map(&vo, NULL, -1, NULL, 0) == NVN_VPOOL_INVALID, "negative count");
    CHECK(nvn_vpool_map(&vo, NULL, 2, NULL, 0) == NVN_VPOOL_INVALID, "NULL requests with a nonzero count");
    CHECK(nvn_vpool_map(&vo, NULL, 0, NULL, 0) == NVN_VPOOL_OK, "zero count with NULL requests succeeds");
    CHECK(nvn_vpool_map(&vo, (const NVNmappingRequest *)(uintptr_t)8, 0, NULL, 0) == NVN_VPOOL_OK, "zero count never dereferences requests");
    CHECK(nvn_vpool_map(&po, NULL, 0, NULL, 0) == NVN_VPOOL_E_UNKNOWN_POOL, "zero count on a physical pool");
    CHECK(nvn_vpool_map(&other, NULL, 0, NULL, 0) == NVN_VPOOL_E_UNKNOWN_POOL, "zero count on an unknown pool");
    CHECK(nvn_vpool_map(&po, &good, 1, NULL, 0) == NVN_VPOOL_E_UNKNOWN_POOL, "mapping into a physical pool");
    CHECK(nvn_vpool_generation() == g0, "rejected calls never advance the generation");
    CHECK(nvn_vpool_lease_acquire(&l, base, 2 * PG) == NVN_VPOOL_OK && l.gen == lg0 && l.cpu == p.mem, "original mapping intact");
    nvn_vpool_lease_release(&l);
    nvn_vpool_virt_finalize(&vo); nvn_vpool_phys_finalize(&po, NULL); pb_free(&p);
}

// Physical and virtual pool lifetimes: no use-after-free, no foreign reused data, stale tokens.
static void test_lifetime(void) {
    static char vo, po, po2, vo2;
    pbuf p = pb_new('P', 4 * PG); pb_fill(&p, 0);
    uint64_t base; nvn_vpool_virt_register(&vo, 4 * PG, &base, NULL);
    nvn_vpool_phys_register(&po, p.mem, p.size);
    map1(&vo, mreq(&po, 0, 0, 2 * PG, 0x100));
    map1(&vo, mreq(&po, 2 * PG, 3 * PG, PG, 0x100));
    nvn_vpool_lease l; nvn_vpool_lease_acquire(&l, base, PG);
    uint64_t id = l.phys_id, g = l.gen;
    nvn_vpool_lease_release(&l);

    uint64_t inv = 0;
    CHECK(nvn_vpool_phys_finalize(&po, &inv) == NVN_VPOOL_OK && inv == 2, "physical finalize removes both referring spans (got %llu)", (unsigned long long)inv);
    pb_free(&p);   // The storage is gone; any stale reference would now fault.
    CHECK(nvn_vpool_lease_acquire(&l, base, PG) == NVN_VPOOL_UNMAPPED, "mapping of a finalized physical pool is gone");
    CHECK(nvn_vpool_virt_gen(&vo) > g, "virtual pool generation advanced");

    // The same owner pointer registered again over new storage must not revive old mappings.
    pbuf p2 = pb_new('R', 4 * PG); pb_fill(&p2, 0);
    nvn_vpool_phys_register(&po, p2.mem, p2.size);
    CHECK(nvn_vpool_lease_acquire(&l, base + 3 * PG, PG) == NVN_VPOOL_UNMAPPED, "re-registered owner does not revive mappings");
    map1(&vo, mreq(&po, 0, 0, PG, 0));
    CHECK(nvn_vpool_lease_acquire(&l, base, PG) == NVN_VPOOL_OK && l.phys_id != id && l.gen > g && l.cpu == p2.mem, "new registration has a new id and generation");
    nvn_vpool_lease_release(&l);

    CHECK(nvn_vpool_virt_finalize(&vo) == NVN_VPOOL_OK, "virtual finalize");
    CHECK(nvn_vpool_lease_acquire(&l, base, PG) == NVN_VPOOL_STALE, "finalized virtual address is stale, not physical");
    CHECK(nvn_vpool_lease_acquire(&l, base + 4 * PG - 1, 1) == NVN_VPOOL_STALE, "whole tombstone is stale");
    CHECK(region_prot(base) == VM_PROT_NONE, "tombstone keeps its PROT_NONE reservation");
    CHECK(nvn_vpool_lease_acquire(&l, base - 1, 2) == NVN_VPOOL_INVALID, "tombstone start straddle never falls back to physical");
    CHECK(nvn_vpool_lease_acquire(&l, base + 4 * PG - 1, 2) == NVN_VPOOL_INVALID, "tombstone end straddle never falls back to physical");
    CHECK(map1(&vo, mreq(&po, 0, 0, PG, 0)) == NVN_VPOOL_E_UNKNOWN_POOL, "map into a finalized pool");
    CHECK(nvn_vpool_phys_register(&po2, (void *)(uintptr_t)base, PG) == NVN_VPOOL_E_OVERLAP, "tombstone addresses cannot become physical storage");
    uint64_t b2; nvn_vpool_virt_register(&vo, 4 * PG, &b2, NULL);
    CHECK(b2 + 4 * PG <= base || b2 >= base + 4 * PG, "reused owner gets a fresh token range");
    nvn_vpool_virt_register(&vo2, 4 * PG, &b2, NULL);
    nvn_vpool_stats_t st; nvn_vpool_stats(&st);
    CHECK(st.tombstones >= 1 && st.tombstone_bytes >= 4 * PG, "tombstones counted");
    nvn_vpool_phys_finalize(&po, &inv);
    CHECK(inv == 0, "finalizing a pool with no remaining mappings");
    nvn_vpool_virt_finalize(&vo); nvn_vpool_virt_finalize(&vo2);
    pb_free(&p2);
}

// Lock rules: writers refuse to run under a lease; nested leases succeed while a writer waits.
static char lk_vo, lk_po;
static _Atomic int lk_started, lk_done;
static void *lk_writer(void *arg) {
    (void)arg;
    atomic_store(&lk_started, 1);
    map1(&lk_vo, ureq(0, PG));
    atomic_store(&lk_done, 1);
    return NULL;
}
static void test_lock_rules(void) {
    pbuf p = pb_new('P', PG); pb_fill(&p, 0);
    uint64_t base; nvn_vpool_virt_register(&lk_vo, PG, &base, NULL);
    nvn_vpool_phys_register(&lk_po, p.mem, p.size);
    map1(&lk_vo, mreq(&lk_po, 0, 0, PG, 0));
    nvn_vpool_lease a, b;
    nvn_vpool_lease_acquire(&a, base, PG);
    uint64_t g = nvn_vpool_generation();
    CHECK(nvn_vpool_map(&lk_vo, NULL, 0, NULL, 0) == NVN_VPOOL_OK && nvn_vpool_generation() == g, "empty map is a nonmutating nested read");
    CHECK(map1(&lk_vo, ureq(0, PG)) == NVN_VPOOL_E_LOCK_ORDER && nvn_vpool_generation() == g, "map under a lease is refused without mutation");
    CHECK(nvn_vpool_phys_finalize(&lk_po, NULL) == NVN_VPOOL_E_LOCK_ORDER, "physical finalize under a lease is refused");
    CHECK(nvn_vpool_virt_register(&lk_po, PG, &(uint64_t){0}, NULL) == NVN_VPOOL_E_LOCK_ORDER, "virtual register under a lease is refused");
    pthread_t t; pthread_create(&t, NULL, lk_writer, NULL);
    while (!atomic_load(&lk_started)) usleep(100);
    nvn_vpool_stats_t lock_stats = {0};
    for (int attempt = 0; attempt < 20000; ++attempt) {
        nvn_vpool_stats(&lock_stats);
        if (lock_stats.waiting_writers) break;
        usleep(100);
    }
    CHECK(lock_stats.waiting_writers == 1, "writer is actually queued before testing nested acquisition");
    CHECK(!atomic_load(&lk_done), "writer waits for the lease");
    CHECK(nvn_vpool_lease_acquire(&b, base, PG) == NVN_VPOOL_OK && b.cpu == p.mem, "nested lease succeeds while a writer waits");
    nvn_vpool_lease_release(&b);
    CHECK(!atomic_load(&lk_done), "writer still waits for the outer lease");
    nvn_vpool_lease_release(&a);
    pthread_join(t, NULL);
    CHECK(atomic_load(&lk_done) && nvn_vpool_lease_acquire(&a, base, 1) == NVN_VPOOL_UNMAPPED, "writer ran after release");
    nvn_vpool_virt_finalize(&lk_vo); nvn_vpool_phys_finalize(&lk_po, NULL); pb_free(&p);
}

// Randomized differential test against a per-page model, including generation semantics.
#define RV 24
#define RP 3
static void test_random_model(int rounds) {
    static char vo, po[RP];
    pbuf pb[RP];
    for (int i = 0; i < RP; i++) { pb[i] = pb_new((char)('a' + i), (size_t)(8 + 4 * i) * PG); pb_fill(&pb[i], 0); }
    uint64_t base; nvn_vpool_virt_register(&vo, RV * PG, &base, NULL);
    for (int i = 0; i < RP; i++) nvn_vpool_phys_register(&po[i], pb[i].mem, pb[i].size);
    mpage model[RV] = {{0}};
    uint64_t stamp[RV] = {0};
    uint8_t *buf = malloc(RV * PG);
    int bad_bytes = 0, bad_status = 0, bad_gen = 0, bad_contig = 0;
    for (int r = 0; r < rounds; r++) {
        // Stamps: current generation of each mapped page.
        for (int v = 0; v < RV; v++) {
            nvn_vpool_lease l;
            stamp[v] = nvn_vpool_lease_acquire(&l, base + v * PG, PG) == NVN_VPOOL_OK ? l.gen : 0;
            nvn_vpool_lease_release(&l);
        }
        int touched[RV] = {0};
        int n = 1 + (int)(rnd() % 4);
        NVNmappingRequest rq[4];
        for (int k = 0; k < n; k++) {
            uint64_t v0 = rnd() % RV, len = 1 + rnd() % (RV - v0 < 6 ? RV - v0 : 6);
            int pi = (int)(rnd() % (RP + 1));
            if (pi == RP) rq[k] = ureq(v0 * PG, len * PG);
            else {
                uint64_t np = pb[pi].size / PG;
                if (len > np) len = np;
                uint64_t p0 = rnd() % (np - len + 1);
                rq[k] = mreq(&po[pi], p0 * PG, v0 * PG, len * PG, rnd() & 1 ? 0x100 : 0);
            }
        }
        if (nvn_vpool_map(&vo, rq, n, NULL, 0) != NVN_VPOOL_OK) { bad_status++; continue; }
        for (int k = 0; k < n; k++) {
            const pbuf *pp = NULL;
            for (int i = 0; i < RP; i++) if (rq[k].physicalPool == (const void *)&po[i]) pp = &pb[i];
            for (uint64_t j = 0; j < rq[k].size / PG; j++) {
                uint64_t v = rq[k].virtualOffset / PG + j;
                model[v] = (mpage){pp, rq[k].physicalOffset + j * PG};
                touched[v] = 1;
            }
        }
        // Occasionally rewrite physical memory; reads must follow.
        uint8_t salt = (uint8_t)(r % 7 == 0 ? r : 0);
        if (salt) for (int i = 0; i < RP; i++) pb_fill(&pb[i], salt);
        // Per-page generations: touched pages advance, untouched pages keep their generation.
        for (int v = 0; v < RV; v++) {
            nvn_vpool_lease l;
            int st = nvn_vpool_lease_acquire(&l, base + v * PG, PG);
            if (st != (model[v].p ? NVN_VPOOL_OK : NVN_VPOOL_UNMAPPED)) { bad_status++; nvn_vpool_lease_release(&l); continue; }
            if (st) continue;
            if (touched[v] ? l.gen <= stamp[v] : l.gen != stamp[v]) bad_gen++;
            nvn_vpool_lease_release(&l);
        }
        // Random ranges: status, contiguity and bytes against the model.
        for (int t = 0; t < 8; t++) {
            uint64_t a = rnd() % (RV * PG), len = 1 + rnd() % (RV * PG - a);
            if (rnd() & 1 && len > 3 * PG) len = 1 + rnd() % (3 * PG);
            uint64_t v0 = a / PG, v1 = (a + len - 1) / PG;
            int mapped = 1, contig = 1;
            for (uint64_t v = v0; v <= v1; v++) {
                if (!model[v].p) mapped = 0;
                else if (v > v0 && (model[v].p != model[v - 1].p || model[v].poff != model[v - 1].poff + PG)) contig = 0;
            }
            nvn_vpool_lease l;
            int st = nvn_vpool_lease_acquire(&l, base + a, len);
            if (st != (mapped ? NVN_VPOOL_OK : NVN_VPOOL_UNMAPPED)) { bad_status++; nvn_vpool_lease_release(&l); continue; }
            if (st) continue;
            if (l.contiguous != contig || (contig && l.cpu != model[v0].p->mem + model[v0].poff + a % PG)) bad_contig++;
            if (nvn_vpool_lease_read(&l, 0, buf, len) || !bytes_match(buf, model, a, len, salt)) bad_bytes++;
            uint64_t sum = 0;
            for (unsigned i = 0; i < l.nspans; i++) { nvn_vpool_span s; nvn_vpool_lease_span(&l, i, &s); if (s.off != sum) bad_contig++; sum += s.len; }
            if (sum != len) bad_contig++;
            nvn_vpool_lease_release(&l);
        }
        if (salt) for (int i = 0; i < RP; i++) pb_fill(&pb[i], 0);
    }
    CHECK(!bad_status && !bad_bytes && !bad_gen && !bad_contig, "%d random batches vs page model: status %d, bytes %d, generation %d, span/contiguity %d mismatches",
          rounds, bad_status, bad_bytes, bad_gen, bad_contig);
    nvn_vpool_virt_finalize(&vo);
    for (int i = 0; i < RP; i++) { nvn_vpool_phys_finalize(&po[i], NULL); pb_free(&pb[i]); }
    free(buf);
}

// Readers race remaps and physical pools whose storage is munmapped right after finalize.
// A lease that failed to pin lifetime would fault; a torn snapshot would mismatch the bytes
// its own spans describe.
#define SV 4
static char sv_vo, sv_po, sv_qo, sv_ro;
static pbuf sv_p, sv_q;
static uint64_t sv_base;
static _Atomic int sv_stop;
static _Atomic uint64_t sv_ok, sv_unmapped, sv_bad;
static uint8_t sv_expect(const nvn_vpool_span *s, uint64_t i) {
    // Every physical pool is filled with pat(tag, off, 0), and R's tag is recovered from its owner.
    char tag = s->phys_owner == &sv_po ? 'P' : s->phys_owner == &sv_qo ? 'Q' : 'R';
    return pat(tag, s->phys_off + i, 0);
}
static void *sv_reader(void *arg) {
    uint64_t seed = (uint64_t)(uintptr_t)arg * 0x9E3779B97F4A7C15ull + 1;
    uint8_t *buf = malloc(SV * PG);
    while (!atomic_load(&sv_stop)) {
        seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
        uint64_t a = seed % (SV * PG), len = 1 + (seed >> 20) % (SV * PG - a);
        nvn_vpool_lease l;
        int st = nvn_vpool_lease_acquire(&l, sv_base + a, len);
        if (st == NVN_VPOOL_UNMAPPED) { atomic_fetch_add(&sv_unmapped, 1); continue; }
        if (st) { atomic_fetch_add(&sv_bad, 1); continue; }
        int bad = nvn_vpool_lease_read(&l, 0, buf, len) != NVN_VPOOL_OK;
        for (unsigned i = 0; i < l.nspans && !bad; i++) {
            nvn_vpool_span s; nvn_vpool_lease_span(&l, i, &s);
            for (uint64_t j = 0; j < s.len; j += 509) bad |= buf[s.off + j] != sv_expect(&s, j) || s.cpu[j] != buf[s.off + j];
            bad |= buf[s.off + s.len - 1] != sv_expect(&s, s.len - 1);
        }
        nvn_vpool_lease_release(&l);
        atomic_fetch_add(bad ? &sv_bad : &sv_ok, 1);
    }
    free(buf);
    return NULL;
}
static void test_stress(int iters) {
    sv_p = pb_new('P', 2 * SV * PG); sv_q = pb_new('Q', 2 * SV * PG);
    pb_fill(&sv_p, 0); pb_fill(&sv_q, 0);
    nvn_vpool_virt_register(&sv_vo, SV * PG, &sv_base, NULL);
    nvn_vpool_phys_register(&sv_po, sv_p.mem, sv_p.size);
    nvn_vpool_phys_register(&sv_qo, sv_q.mem, sv_q.size);
    pthread_t th[4];
    for (uintptr_t i = 0; i < 4; i++) pthread_create(&th[i], NULL, sv_reader, (void *)(i + 1));
    int werr = 0;
    for (int it = 0; it < iters; it++) {
        switch (it % 5) {
        case 0: werr |= map1(&sv_vo, mreq(&sv_po, (it % 3) * PG, 0, SV * PG, 0x100)); break;
        case 1: {
            NVNmappingRequest r[] = {mreq(&sv_qo, PG, 0, 2 * PG, 0), mreq(&sv_po, 5 * PG, 2 * PG, 2 * PG, 0x100)};
            werr |= nvn_vpool_map(&sv_vo, r, 2, NULL, 0);
            break;
        }
        case 2: werr |= map1(&sv_vo, ureq(PG, PG)); break;
        case 3: {
            // A short-lived pool whose storage disappears right after finalize.
            pbuf r = pb_new('R', SV * PG); pb_fill(&r, 0);
            werr |= nvn_vpool_phys_register(&sv_ro, r.mem, r.size);
            werr |= map1(&sv_vo, mreq(&sv_ro, 0, 0, SV * PG, 0));
            usleep(50);
            werr |= nvn_vpool_phys_finalize(&sv_ro, NULL);
            pb_free(&r);
            break;
        }
        case 4: werr |= map1(&sv_vo, mreq(&sv_qo, 4 * PG, PG, 3 * PG, 0)); break;
        }
    }
    atomic_store(&sv_stop, 1);
    for (int i = 0; i < 4; i++) pthread_join(th[i], NULL);
    CHECK(!werr && !atomic_load(&sv_bad) && atomic_load(&sv_ok) > 0,
          "%d remaps vs 4 readers: %llu consistent reads, %llu unmapped, %llu torn or failed, writer errors %d",
          iters, (unsigned long long)atomic_load(&sv_ok), (unsigned long long)atomic_load(&sv_unmapped), (unsigned long long)atomic_load(&sv_bad), werr);
    printf("stress: %d remaps, %llu consistent reads, %llu unmapped\n", iters,
           (unsigned long long)atomic_load(&sv_ok), (unsigned long long)atomic_load(&sv_unmapped));
    nvn_vpool_virt_finalize(&sv_vo);
    nvn_vpool_phys_finalize(&sv_po, NULL); nvn_vpool_phys_finalize(&sv_qo, NULL);
    pb_free(&sv_p); pb_free(&sv_q);
}

int main(int argc, char **argv) {
    int quick = argc > 1 && !strcmp(argv[1], "--quick");
    setvbuf(stdout, NULL, _IOLBF, 0);   // Keep earlier failures visible if a later case crashes
    alarm(120);   // A deadlock fails the run instead of hanging it
    test_layout();
    test_register();
    test_resolve_and_read();
    test_overlap();
    test_invalid_batches();
    test_lifetime();
    test_lock_rules();
    test_random_model(quick ? 300 : 3000);
    test_stress(quick ? 2000 : 20000);
    nvn_vpool_stats_t st; nvn_vpool_stats(&st);
    CHECK(st.phys_live == 0 && st.virt_live == 0 && st.spans == 0, "registry empty at exit (phys %d, virt %d, spans %llu)", st.phys_live, st.virt_live, (unsigned long long)st.spans);
    printf("t_vpool: %d checks, %d failed (tombstones %d, reserved %llu bytes, above 40-bit %llu)\n",
           g_checks, g_fail, st.tombstones, (unsigned long long)st.reserved_bytes, (unsigned long long)st.above_va_limit);
    return g_fail != 0;
}
