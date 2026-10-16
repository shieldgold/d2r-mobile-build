/* Owned CPU registry fixture. No guest image, entry or GPU is executed. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define LOG(...) ((void)0)
#define die(...) _exit(77)
#define DM_TM(t) (1u << (t))
#define DM_TITLE_BOTW 2
static int g_dm_title = DM_TITLE_BOTW;
static struct { int imports; } g_flag;
typedef struct { const char *name; void *fn; const char *why; unsigned titles; } hle_force_ent;
typedef struct { char name[16]; } module_t;
static unsigned callback_count;
static void owned_callback(void) { callback_count++; }
static hle_force_ent owned_registry[] = {
#include "registry.inc"
};
static const struct { const char *name; unsigned mask; } owned_references[] = {
#include "references.inc"
};
static const char *const borrowed[] = {
    "nndiagStartup", "_ZN2nn2oe10InitializeEv", "_ZN2nn3aoc10InitializeEv",
    "_ZN2nn3err26InitializeContextWriterApiEv", "_ZN2nn2fs24InitializeForApplicationEv",
    "_ZN2nn4pctl10InitializeEv", "_ZN2nn4diag34InitializeApplicationAbortObserverEv", NULL
};
static struct { const char *name; const char *const *force_also; } title = {"botw", borrowed};
static void dm_title_select(void) {}
static __typeof__(title) *dm_title(void) { return &title; }
static char _mh_execute_header;
static const void *getsectiondata(const void *header, const char *segment, const char *section,
                                 unsigned long *size) {
    assert(header == &_mh_execute_header && !strcmp(segment, "__DATA") && !strcmp(section, "swforce"));
    *size = sizeof owned_registry; return owned_registry;
}
void *hlecount_wrap(const char *category, const char *name, void *function) {
    assert(!strcmp(category, "FORCE") && name && function == (void *)owned_callback);
    return function;
}
#if !defined(FORCE_BASELINE)
#include "base_v0_force.c"
/* Unit fixture supplies owned identity records to the private pure matcher.
 * Separate disk-only tests exercise the real streaming hash entry point.
 * This helper is absent from every production translation unit. */
static void owned_profile_identity(void) {
    const char *names[MODULE_COUNT];
    for (unsigned i = 0; i < MODULE_COUNT; i++) names[i] = modules[i].name;
    assert(base_v0_force_begin(names, MODULE_COUNT) == 0);
    for (unsigned i = 0; i < MODULE_COUNT; i++)
        assert(accept_identity(modules[i].name, &modules[i].layout,
            modules[i].image_bytes, modules[i].image_sha256, 0) == 0);
}
#endif
#include "loader_force.inc"
int main(int argc, char **argv) {
    assert(argc >= 2);
#if defined(FORCE_BASELINE)
    (void)argv;
#endif
#if !defined(FORCE_BASELINE)
    const char *const names[] = {"main", "subsdk0", "subsdk1", "subsdk2", "subsdk3", "sdk"};
    if (!strcmp(argv[1], "manifest")) {
        for (unsigned i = 0; i < MODULE_COUNT; i++) {
            const base_v0_force_module *m = &modules[i];
            printf("%s %s %s %s %s %zu %zu\n", m->name, m->source_sha256, m->build_id,
                m->image_sha256, m->padded_sha256, m->image_bytes, m->padded_bytes);
        }
        return 0;
    }
    if (!strcmp(argv[1], "begin-order") || !strcmp(argv[1], "begin-unknown")) {
        const char *bad[6]; memcpy(bad, names, sizeof bad);
        bad[1] = !strcmp(argv[1], "begin-order") ? "sdk" : "unknown";
        assert(base_v0_force_begin(bad, 6) != 0);
        assert(base_v0_force_begin(names, 6) != 0); return 0;
    }
    if (!strcmp(argv[1], "call-order")) {
        uint8_t out = 0x5a;
        assert(base_v0_force_registry_begin() != 0);
        assert(base_v0_force_registry_entry("x", 4, "x", 1, 1, &out) != 0 && out == 0x5a);
        assert(base_v0_force_begin(names, 6) != 0); return 0;
    }
    if (!strcmp(argv[1], "image-guard")) {
        assert(base_v0_force_begin(names, 6) == 0);
        const void *parts[1] = {(void *)1}; size_t lengths[1] = {1};
        assert(base_v0_force_image("main", &modules[0].layout, parts, lengths, 1, 0) != 0);
        assert(base_v0_force_registry_begin() != 0); return 0;
    }
    if (!strcmp(argv[1], "layout-change") || !strcmp(argv[1], "image-order") ||
        !strcmp(argv[1], "image-repeat")) {
        assert(base_v0_force_begin(names, 6) == 0);
        base_v0_force_layout layout = modules[0].layout;
        const char *name = "main";
        if (!strcmp(argv[1], "layout-change")) layout.key_idx[4] = 5;
        if (!strcmp(argv[1], "image-order")) name = "sdk";
        if (!strcmp(argv[1], "image-repeat"))
            assert(accept_identity(name, &layout, modules[0].image_bytes,
                modules[0].image_sha256, 0) == 0);
        assert(accept_identity(name, &layout, modules[0].image_bytes,
            modules[0].image_sha256, 0) != 0);
        assert(base_v0_force_registry_begin() != 0); return 0;
    }
    if (!strcmp(argv[1], "identity-change")) {
        assert(base_v0_force_begin(names, 6) == 0);
        assert(accept_identity("main", &modules[0].layout, modules[0].image_bytes,
            "unknown-digest", 0) != 0);
        assert(base_v0_force_registry_begin() != 0); return 0;
    }
    if (!strcmp(argv[1], "disk-raw") || !strcmp(argv[1], "disk-padded") ||
        !strcmp(argv[1], "disk-mutated") || !strcmp(argv[1], "disk-bad-padding")) {
        assert(argc == 3 && base_v0_force_begin(names, 6) == 0);
        int mutated = !strcmp(argv[1], "disk-mutated");
        int bad_padding = !strcmp(argv[1], "disk-bad-padding");
        int padded = !strcmp(argv[1], "disk-padded") || bad_padding;
        for (unsigned i = 0; i < MODULE_COUNT; i++) {
            const base_v0_force_module *m = &modules[i]; char path[4096];
            snprintf(path, sizeof path, "%s/%s.patched.bin", argv[2], m->name);
            FILE *file = fopen(path, "rb"); assert(file);
            unsigned char *bytes = calloc(m->padded_bytes, 1); assert(bytes);
            assert(fread(bytes, 1, m->image_bytes, file) == m->image_bytes && fgetc(file) == EOF);
            assert(fclose(file) == 0);
            if (mutated && i == 0) bytes[0] ^= 1;
            if (bad_padding && i == 0) bytes[m->padded_bytes - 1] = 1;
            int result;
            if (padded) {
                size_t text = (size_t)((m->layout.text_end + 0x3fffu) & ~UINT64_C(0x3fff));
                const void *parts[3] = {bytes, bytes + text, bytes + m->layout.patch_off};
                size_t lengths[3] = {text, (size_t)m->layout.patch_off - text,
                    m->padded_bytes - (size_t)m->layout.patch_off};
                result = base_v0_force_image(m->name, &m->layout, parts, lengths, 3, 1);
            } else {
                const void *parts[1] = {bytes}; size_t lengths[1] = {m->image_bytes};
                result = base_v0_force_image(m->name, &m->layout, parts, lengths, 1, 0);
            }
            free(bytes);
            if ((mutated || bad_padding) && i == 0) {
                assert(result != 0 && base_v0_force_registry_begin() != 0); return 0;
            }
            assert(result == 0);
        }
        assert(base_v0_force_registry_begin() == 0); return 0;
    }
    owned_profile_identity();
    if (!strcmp(argv[1], "registry-incomplete")) {
        assert(base_v0_force_registry_begin() == 0);
        assert(base_v0_force_registry_end() != 0); die("owned incomplete registry");
    }
    if (!strcmp(argv[1], "registry-unknown")) owned_registry[0].name = "unknown";
    if (!strcmp(argv[1], "registry-reason")) owned_registry[0].why = "changed";
    if (!strcmp(argv[1], "registry-title")) owned_registry[0].titles = 4;
    if (!strcmp(argv[1], "registry-null")) owned_registry[0].fn = NULL;
    if (!strcmp(argv[1], "registry-duplicate")) owned_registry[1] = owned_registry[0];
    if (!strcmp(argv[1], "registry-candidate")) title.force_also = NULL;
#endif
    force_init();
#if !defined(FORCE_BASELINE)
    if (!strcmp(argv[1], "reference-order")) {
        for (unsigned i = 0; i < sizeof owned_references / sizeof *owned_references; i++)
            if ((owned_references[i].mask & 33u) == 33u) {
                assert(base_v0_force_reference("sdk", owned_references[i].name) == 0);
                assert(base_v0_force_reference("main", owned_references[i].name) != 0);
                assert(base_v0_force_references_end() != 0); return 0;
            }
        return 3;
    }
#endif
#if defined(FORCE_BASELINE)
    const char *const names[] = {"main", "subsdk0", "subsdk1", "subsdk2", "subsdk3", "sdk"};
#endif
    unsigned calls = 0;
    for (unsigned module = 0; module < 6; module++) {
        module_t owner; snprintf(owner.name, sizeof owner.name, "%s", names[module]);
        for (unsigned i = 0; i < sizeof owned_references / sizeof *owned_references; i++) {
            if (!(owned_references[i].mask & (1u << module))) continue;
#if !defined(FORCE_BASELINE)
            if (!strcmp(argv[1], "missing-reference") && calls == 0) { calls++; continue; }
            if (!strcmp(argv[1], "moved-reference") && calls == 0) strcpy(owner.name, "subsdk2");
#endif
#if defined(FORCE_BASELINE)
            void *p = force_lookup(owned_references[i].name);
#else
            void *p = force_lookup(&owner, owned_references[i].name);
#endif
            assert(p == (void *)owned_callback); ((void (*)(void))p)(); calls++;
#if !defined(FORCE_BASELINE)
            if (!strcmp(argv[1], "duplicate-reference") && calls == 1)
                (void)force_lookup(&owner, owned_references[i].name);
#endif
        }
    }
#if !defined(FORCE_BASELINE)
    if (!strcmp(argv[1], "missing-reference")) {
        assert(base_v0_force_references_end() != 0); return 0;
    }
    assert(base_v0_force_references_end() == 0);
#endif
    force_check();
    assert(calls == 142 && callback_count == 142 && g_force_active == 101);
    return 0;
}
