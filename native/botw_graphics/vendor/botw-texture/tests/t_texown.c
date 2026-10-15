// Storage claims of separately allocated Metal textures (nvn_texown.c): byte-exact overlap, retirement that keeps
// ranges non-authoritative, merging of retired ranges, atomic fresh claims and invalid ranges. Pure C, no Metal.
#include "../nvn_texown.h"
#include <pthread.h>
#include <stdio.h>

static int checks, failures;
#define CHECK(x) do { checks++; if (!(x)) { failures++; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)

static uint8_t g_mem[1 << 16];
static uint8_t *at(size_t off) { return g_mem + off; }

static void overlap_is_byte_exact(void) {
    nvn_texown_hit h = {0};
    CHECK(!nvn_texown_find(at(0), sizeof g_mem, &h));
    nvn_texown_claim(11, at(0x100), 0x100);
    nvn_texown_claim(11, at(0x100), 0x100);                        //Identical live claims are not duplicated
    CHECK(nvn_texown_count(0) == 1 && nvn_texown_count(1) == 0);
    CHECK(nvn_texown_find(at(0x100), 0x100, &h) && h.serial == 11 && !h.retired &&
          h.lo == (uintptr_t)at(0x100) && h.hi == (uintptr_t)at(0x200));
    CHECK(!nvn_texown_find(at(0), 0x100, NULL));                   //Ends exactly where the claim starts
    CHECK(!nvn_texown_find(at(0x200), 1, NULL));                   //Starts exactly where the claim ends
    CHECK(nvn_texown_find(at(0x1ff), 1, NULL) && nvn_texown_find(at(0xff), 2, NULL));
    CHECK(!nvn_texown_find(at(0x150), 0, NULL));                   //Empty ranges overlap nothing
}

static void fresh_claims(void) {
    nvn_texown_hit h = {0};
    CHECK(nvn_texown_claim_fresh(12, at(0x180), 0x10, &h) == 0 && h.serial == 11);
    CHECK(nvn_texown_count(0) == 1);                               //A refused claim changes nothing
    CHECK(nvn_texown_claim_fresh(12, at(0x200), 0x80, &h) == 1);
    CHECK(nvn_texown_claim_fresh(13, at(0x27f), 1, &h) == 0 && h.serial == 12);
    CHECK(nvn_texown_claim_fresh(0, at(0x1000), 1, NULL) == -1);
    CHECK(nvn_texown_claim_fresh(13, at(0x1000), 0, NULL) == -1);
    CHECK(nvn_texown_claim_fresh(13, (const void *)(UINTPTR_MAX - 4), 16, NULL) == -1);
    nvn_texown_claim(13, (const void *)(UINTPTR_MAX - 4), 16);     //Overflowing and empty claims are ignored
    nvn_texown_claim(13, at(0x1000), 0);
    nvn_texown_claim(0, at(0x1000), 1);
    CHECK(nvn_texown_count(0) == 2);
}

static void retirement_keeps_ranges(void) {
    nvn_texown_hit h = {0};
    nvn_texown_retire(99);                                         //Unknown serial
    CHECK(nvn_texown_count(0) == 2 && nvn_texown_count(1) == 0);
    nvn_texown_retire(11);
    CHECK(nvn_texown_count(0) == 1 && nvn_texown_count(1) == 1);
    CHECK(nvn_texown_find(at(0x100), 1, &h) && h.retired && h.serial == 11);
    CHECK(nvn_texown_claim_fresh(14, at(0x1f0), 0x10, &h) == 0 && h.retired);
    nvn_texown_retire(12);                                         //Touches the retired [0x100, 0x200): merges
    CHECK(nvn_texown_count(0) == 0 && nvn_texown_count(1) == 1);
    CHECK(nvn_texown_find(at(0x150), 0x100, &h) && h.retired && h.lo == (uintptr_t)at(0x100) && h.hi == (uintptr_t)at(0x280));

    nvn_texown_claim(15, at(0x400), 0x40);
    nvn_texown_claim(16, at(0x420), 0x40);                         //Live claims may overlap each other
    nvn_texown_claim(17, at(0x500), 0x40);
    nvn_texown_retire(15); nvn_texown_retire(17);
    CHECK(nvn_texown_count(1) == 3 && nvn_texown_count(0) == 1);   //Separated retired ranges stay apart
    nvn_texown_retire(16);
    CHECK(nvn_texown_count(1) == 3);                               //[0x400, 0x460) merged; 0x500 stays separate
    CHECK(nvn_texown_find(at(0x45f), 1, &h) && h.lo == (uintptr_t)at(0x400) && h.hi == (uintptr_t)at(0x460));
    CHECK(!nvn_texown_find(at(0x460), 0xa0, NULL));
    nvn_texown_claim(18, at(0x460), 0xa0);                         //Bridges both retired ranges once retired
    nvn_texown_retire(18);
    CHECK(nvn_texown_count(1) == 2);
    CHECK(nvn_texown_find(at(0x4ff), 1, &h) && h.lo == (uintptr_t)at(0x400) && h.hi == (uintptr_t)at(0x540));
}

// Concurrent fresh claims of one range: exactly one wins, every loser sees the winner
static struct { int won; uint64_t seen; } g_res[8];
static void *racer(void *arg) {
    int i = (int)(intptr_t)arg; nvn_texown_hit h = {0};
    g_res[i].won = nvn_texown_claim_fresh(100 + (uint64_t)i, at(0x8000), 0x400, &h);
    g_res[i].seen = h.serial;
    return NULL;
}
static void racing_fresh_claims(void) {
    pthread_t th[8];
    for (int i = 0; i < 8; i++) CHECK(!pthread_create(&th[i], NULL, racer, (void *)(intptr_t)i));
    for (int i = 0; i < 8; i++) pthread_join(th[i], NULL);
    int winners = 0; uint64_t winner = 0;
    for (int i = 0; i < 8; i++) if (g_res[i].won == 1) { winners++; winner = 100 + (uint64_t)i; }
    CHECK(winners == 1);
    for (int i = 0; i < 8; i++) if (g_res[i].won != 1) CHECK(g_res[i].won == 0 && g_res[i].seen == winner);
}

int main(void) {
    overlap_is_byte_exact();
    fresh_claims();
    retirement_keeps_ranges();
    racing_fresh_claims();
    printf("TEXOWN: %d checks, %d failed\n", checks, failures);
    return failures != 0;
}
