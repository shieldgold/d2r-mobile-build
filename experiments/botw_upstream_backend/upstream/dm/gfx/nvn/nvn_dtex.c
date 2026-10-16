// Submit-time deferred texture binding snapshot; see nvn_dtex.h.
//
// Walk: iterative, one frame per nested list, so neither long sequential lists nor deep graphs use C stack.
// Every read of guest-owned memory goes through cmd_safe_read: command headers and structural pointers via a
// small read window (one bounded read per window instead of one per record), handle arrays as exact
// 8 * count byte reads. Lists are memoized per submit with the height of their call subtree, so a callee
// first walked shallow and reached again through a deeper path is still checked against the depth limit.
#include "nvn_dtex.h"
#include "nvn_cmd_audit.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DTEX_WIN 16384
#define ADDR_BITS 48

_Static_assert(offsetof(p_bind_handles, stage) == 0 && offsetof(p_bind_handles, first) == 4 && offsetof(p_bind_handles, n) == 8 &&
               offsetof(p_bind_handles, h) == 16, "captured entries are laid out as a p_bind_handles prefix");

static const char *const g_text[DTEX_E_COUNT_] = {
    [DTEX_OK] = "ok",
    [DTEX_E_ARGS] = "invalid submitted handle count or handle array",
    [DTEX_E_NOMEM] = "host allocation failed while capturing deferred texture bindings",
    [DTEX_E_BUDGET] = "command walk exceeded the per-submit record budget",
    [DTEX_E_LISTS] = "command graph exceeded the per-submit list budget",
    [DTEX_E_DEPTH] = "command call nesting exceeds the host limit of 16",
    [DTEX_E_CALL_CYCLE] = "command CALL cycle",
    [DTEX_E_JUMP_CYCLE] = "command JUMP cycle",
    [DTEX_E_UNREADABLE] = "command record or structural pointer unreadable",
    [DTEX_E_MALFORMED] = "malformed command record",
    [DTEX_E_ENTRY] = "command handle does not start with an exact OP_BEGIN, size 8 record",
    [DTEX_E_NULL_TARGET] = "command CALL or JUMP target is NULL",
    [DTEX_E_ADDRESS] = "command or handle-array address range overflows",
    [DTEX_E_RANGE] = "deferred texture binding stage, first or count outside the native range",
    [DTEX_E_NULL_ARRAY] = "deferred texture binding with nonzero count has a NULL handle array",
    [DTEX_E_ARRAY] = "deferred texture binding handle array unreadable at submit",
};
const char *dtex_code_text(int code) { return code >= 0 && code < DTEX_E_COUNT_ ? g_text[code] : "unknown deferred capture status"; }

static atomic_int g_seen;
void dtex_note_recorded(void) { if (!atomic_load_explicit(&g_seen, memory_order_relaxed)) atomic_store_explicit(&g_seen, 1, memory_order_release); }

// ---------- uint64 key -> uint32 value open-addressing map (key 0 = empty) ----------
typedef struct { uint64_t *k; uint32_t *v; size_t cap, n; } u64map;
static size_t map_slot(uint64_t k, size_t cap) { k ^= k >> 29; k *= 0x9e3779b97f4a7c15ull; k ^= k >> 32; return (size_t)k & (cap - 1); }
static int map_get(const u64map *m, uint64_t k, uint32_t *v) {
    if (!m->cap) return 0;
    for (size_t i = map_slot(k, m->cap);; i = (i + 1) & (m->cap - 1)) {
        if (!m->k[i]) return 0;
        if (m->k[i] == k) { if (v) *v = m->v[i]; return 1; }
    }
}
static int map_grow(u64map *m) {
    size_t cap = m->cap ? m->cap * 2 : 64;
    if (cap > SIZE_MAX / sizeof(uint64_t) || cap < m->cap) return 0;
    uint64_t *k = calloc(cap, sizeof *k); uint32_t *v = calloc(cap, sizeof *v);
    if (!k || !v) { free(k); free(v); return 0; }
    for (size_t j = 0; j < m->cap; j++) if (m->k[j]) {
        size_t i = map_slot(m->k[j], cap); while (k[i]) i = (i + 1) & (cap - 1);
        k[i] = m->k[j]; v[i] = m->v[j];
    }
    free(m->k); free(m->v); m->k = k; m->v = v; m->cap = cap; return 1;
}
static int map_put(u64map *m, uint64_t k, uint32_t v) {   //Inserts or overwrites; 0 on allocation failure
    if ((m->n + 1) * 2 > m->cap && !map_grow(m)) return 0;
    size_t i = map_slot(k, m->cap);
    while (m->k[i] && m->k[i] != k) i = (i + 1) & (m->cap - 1);
    if (!m->k[i]) { m->k[i] = k; m->n++; }
    m->v[i] = v; return 1;
}
static void map_free(u64map *m) { free(m->k); free(m->v); memset(m, 0, sizeof *m); }

// ---------- Overlay ----------
// Arena of uint64 words; each entry is a p_bind_handles-shaped block: {stage, first, n, pad} then n handles.
struct dtex_overlay { uint64_t *arena; size_t nw, cap; u64map idx; };
typedef struct { int32_t stage, first, n, pad; } entry_head;

void dtex_free(dtex_overlay *ov) { if (!ov) return; free(ov->arena); map_free(&ov->idx); free(ov); }
const p_bind_handles *dtex_lookup(const dtex_overlay *ov, const uint8_t *rec) {
    uint32_t off; if (!ov || !map_get(&ov->idx, (uint64_t)(uintptr_t)rec, &off)) return NULL;
    return (const p_bind_handles *)(const void *)(ov->arena + off);
}
size_t dtex_entries(const dtex_overlay *ov) { return ov ? ov->idx.n : 0; }

// ---------- Capture ----------
typedef struct { const uint8_t *h; uint32_t height; uint8_t done; } node;
typedef struct { const uint8_t *p; uint32_t node, height; } frame;
typedef struct {
    dtex_overlay *ov; dtex_info *info;
    const uint8_t *wbase; size_t wlen; uint8_t *wbuf;
    node *nodes; size_t nn, ncap; u64map nidx, jumps;
    size_t page;
} cap_ctx;

static int fail(cap_ctx *c, int code, const void *at, uint32_t op, uint32_t size, int depth) {
    dtex_info *i = c->info; i->code = code; i->what = dtex_code_text(code); i->at = at; i->op = op; i->size = size; i->depth = depth; return code;
}

// Windowed fault-safe read. A miss refills from addr: a whole window, then the rest of addr's page (an unreadable
// neighbour must not fail a readable record), then exactly n bytes. The window is invalidated before each
// refill attempt, so a failed read never leaves stale bytes addressable.
static int win_get(cap_ctx *c, const uint8_t *addr, size_t n, void *out) {
    uintptr_t a = (uintptr_t)addr, b = (uintptr_t)c->wbase;
    if (c->wlen >= n && a >= b && a - b <= c->wlen - n) { memcpy(out, c->wbuf + (a - b), n); return 1; }
    if (!a || a > UINTPTR_MAX - n) return 0;
    size_t tries[3] = {DTEX_WIN, c->page - (a & (c->page - 1)), n};
    for (int t = 0; t < 3; t++) {
        size_t len = tries[t];
        if (len < n || len > DTEX_WIN || a > UINTPTR_MAX - len || (t && len == tries[t - 1])) continue;
        c->wlen = 0;
        if (cmd_safe_read(addr, len, c->wbuf)) { c->wbase = addr; c->wlen = len; memcpy(out, c->wbuf, n); return 1; }
    }
    return 0;
}

static int user_addr(const void *p) { return (uintptr_t)p >> ADDR_BITS == 0; }

// New list node with its entry check (exact BEGIN, size 8, read fault-safely).
static int node_new(cap_ctx *c, const uint8_t *h, int depth, uint32_t *idx) {
    if (c->nn >= DTEX_LIST_BUDGET) return fail(c, DTEX_E_LISTS, h, 0, 0, depth);
    if (!user_addr(h)) return fail(c, DTEX_E_ADDRESS, h, 0, 0, depth);
    op_hdr e;
    if (!win_get(c, h, sizeof e, &e)) return fail(c, DTEX_E_UNREADABLE, h, 0, 0, depth);
    if (e.op != OP_BEGIN || e.size != 8) return fail(c, DTEX_E_ENTRY, h, e.op, e.size, depth);
    if (c->nn == c->ncap) {
        size_t cap = c->ncap ? c->ncap * 2 : 64; node *nv = cap > SIZE_MAX / sizeof *nv ? NULL : realloc(c->nodes, cap * sizeof *nv);
        if (!nv) return fail(c, DTEX_E_NOMEM, h, 0, 0, depth);
        c->nodes = nv; c->ncap = cap;
    }
    *idx = (uint32_t)c->nn; c->nodes[c->nn++] = (node){h, 0, 0};
    if (!map_put(&c->nidx, (uint64_t)(uintptr_t)h, *idx)) return fail(c, DTEX_E_NOMEM, h, 0, 0, depth);
    c->info->lists++;
    return DTEX_OK;
}

static int capture_record(cap_ctx *c, const uint8_t *rec, const p_bind_tex_deferred *dp, int depth) {
    p_bind_tex_deferred d = *dp;
    uint32_t size = (uint32_t)(sizeof(op_hdr) + sizeof d);
    if ((uint32_t)d.stage >= 6 || d.first < 0 || d.first > DTEX_SLOTS || d.n < 0 || d.n > DTEX_SLOTS - d.first)
        return fail(c, DTEX_E_RANGE, rec, OP_BIND_TEX_DEFERRED, size, depth);
    if (map_get(&c->ov->idx, (uint64_t)(uintptr_t)rec, NULL)) return DTEX_OK;   //Shared record within this submit: one immutable capture
    size_t bytes = (size_t)d.n * sizeof(uint64_t);
    if (d.n && !d.h) return fail(c, DTEX_E_NULL_ARRAY, rec, OP_BIND_TEX_DEFERRED, size, depth);
    if (d.n && (uintptr_t)d.h > UINTPTR_MAX - bytes) return fail(c, DTEX_E_ADDRESS, d.h, OP_BIND_TEX_DEFERRED, size, depth);
    dtex_overlay *ov = c->ov; size_t words = 2 + (size_t)d.n;
    if (ov->nw > UINT32_MAX - words) return fail(c, DTEX_E_NOMEM, rec, OP_BIND_TEX_DEFERRED, size, depth);
    if (ov->nw + words > ov->cap) {
        size_t cap = ov->cap ? ov->cap : 256; while (cap < ov->nw + words) { if (cap > SIZE_MAX / 2 / sizeof(uint64_t)) return fail(c, DTEX_E_NOMEM, rec, OP_BIND_TEX_DEFERRED, size, depth); cap *= 2; }
        uint64_t *a = realloc(ov->arena, cap * sizeof *a);
        if (!a) return fail(c, DTEX_E_NOMEM, rec, OP_BIND_TEX_DEFERRED, size, depth);
        ov->arena = a; ov->cap = cap;
    }
    uint32_t off = (uint32_t)ov->nw;
    entry_head e = {d.stage, d.first, d.n, 0}; memcpy(ov->arena + off, &e, sizeof e);
    if (d.n) {   //Exactly count handles, never the pointer for count 0
        if (!cmd_safe_read(d.h, bytes, ov->arena + off + 2)) return fail(c, DTEX_E_ARRAY, d.h, OP_BIND_TEX_DEFERRED, size, depth);
        c->info->array_reads++; c->info->array_bytes += bytes;
    }
    if (!map_put(&ov->idx, (uint64_t)(uintptr_t)rec, off)) return fail(c, DTEX_E_NOMEM, rec, OP_BIND_TEX_DEFERRED, size, depth);
    ov->nw += words; c->info->entries++;
    return DTEX_OK;
}

static int capture_deferred(cap_ctx *c, const uint8_t *rec, int depth) {
    p_bind_tex_deferred d;
    if (!win_get(c, rec + sizeof(op_hdr), sizeof d, &d)) return fail(c, DTEX_E_UNREADABLE, rec, OP_BIND_TEX_DEFERRED, 0, depth);
    int r = capture_record(c, rec, &d, depth);
    if (r) { c->info->stage = d.stage; c->info->first = d.first; c->info->count = d.n; }   //Deferred fields describe the failing record only
    return r;
}

static int walk(cap_ctx *c, const uint8_t *top) {
    uint32_t idx;
    if (map_get(&c->nidx, (uint64_t)(uintptr_t)top, &idx)) return DTEX_OK;   //Completed at depth >= 0 with depth + height <= limit
    int r = node_new(c, top, 0, &idx); if (r) return r;
    frame stk[DTEX_MAX_DEPTH + 1]; int sp = 0;
    stk[sp++] = (frame){top, idx, 0};
    while (sp) {
        frame *f = &stk[sp - 1]; int depth = sp - 1;
        if (++c->info->records > DTEX_REC_BUDGET) return fail(c, DTEX_E_BUDGET, f->p, 0, 0, depth);
        op_hdr h;
        if (!user_addr(f->p) || !win_get(c, f->p, sizeof h, &h)) return fail(c, DTEX_E_UNREADABLE, f->p, 0, 0, depth);
        const char *defect = cmd_hdr_defect(h.op, h.size);
        if (defect) { c->info->detail = defect; return fail(c, DTEX_E_MALFORMED, f->p, h.op, h.size, depth); }
        uint8_t prefix[CMD_REC_PREFIX_MAX];
        size_t prefix_size = h.size < sizeof prefix ? h.size : sizeof prefix;
        if (!win_get(c, f->p, prefix_size, prefix)) return fail(c, DTEX_E_UNREADABLE, f->p, h.op, h.size, depth);
        if (memcmp(prefix, &h, sizeof h)) {
            c->info->detail = "command header changed during submit capture";
            return fail(c, DTEX_E_MALFORMED, f->p, h.op, h.size, depth);
        }
        // Deferred fields have dedicated range diagnostics below. Other records reuse the same
        // bounded validator as replay, including variable count/length consistency.
        if (h.op != OP_BIND_TEX_DEFERRED && (defect = cmd_rec_defect(prefix))) {
            c->info->detail = defect; return fail(c, DTEX_E_MALFORMED, f->p, h.op, h.size, depth);
        }
        const uint8_t *p = f->p;
        if (h.op == OP_END) {
            node *nd = &c->nodes[f->node]; nd->done = 1; nd->height = f->height; uint32_t ht = f->height;
            if (--sp) { frame *up = &stk[sp - 1]; if (ht + 1 > up->height) up->height = ht + 1; }
            continue;
        }
        if (h.op == OP_JUMP || h.op == OP_CALL) {
            const uint8_t *t;
            if (!win_get(c, p + sizeof h, sizeof t, &t)) return fail(c, DTEX_E_UNREADABLE, p, h.op, h.size, depth);
            if (!t) return fail(c, DTEX_E_NULL_TARGET, p, h.op, h.size, depth);
            if (!user_addr(t)) return fail(c, DTEX_E_ADDRESS, p, h.op, h.size, depth);
            if (h.op == OP_JUMP) {   //Per-list visited JUMP targets: any endless walk of one list revisits one of them
                uint64_t key = (uint64_t)(uintptr_t)t | (uint64_t)f->node << ADDR_BITS;
                if (map_get(&c->jumps, key, NULL)) return fail(c, DTEX_E_JUMP_CYCLE, p, h.op, h.size, depth);
                if (!map_put(&c->jumps, key, 1)) return fail(c, DTEX_E_NOMEM, p, h.op, h.size, depth);
                f->p = t; continue;
            }
            if ((uintptr_t)p > UINTPTR_MAX - h.size) return fail(c, DTEX_E_ADDRESS, p, h.op, h.size, depth);
            f->p = p + h.size;   //Caller continuation
            uint32_t ci;
            if (map_get(&c->nidx, (uint64_t)(uintptr_t)t, &ci)) {
                const node *cn = &c->nodes[ci];
                if (!cn->done) return fail(c, DTEX_E_CALL_CYCLE, p, h.op, h.size, depth);
                if ((uint32_t)sp + cn->height > DTEX_MAX_DEPTH) return fail(c, DTEX_E_DEPTH, p, h.op, h.size, depth);   //Memoized subtree reached deeper
                if (cn->height + 1 > f->height) f->height = cn->height + 1;
                continue;
            }
            if (sp > DTEX_MAX_DEPTH) return fail(c, DTEX_E_DEPTH, p, h.op, h.size, depth);
            if ((r = node_new(c, t, sp, &ci))) return r;
            stk[sp++] = (frame){t, ci, 0};
            continue;
        }
        if (h.op == OP_BIND_TEX_DEFERRED && (r = capture_deferred(c, p, depth))) return r;
        if ((uintptr_t)p > UINTPTR_MAX - h.size) return fail(c, DTEX_E_ADDRESS, p, h.op, h.size, depth);
        f->p = p + h.size;
    }
    return DTEX_OK;
}

int dtex_capture(const uint64_t *h, int n, dtex_overlay **out, dtex_info *info) {
    dtex_info local; if (!info) info = &local;
    memset(info, 0, sizeof *info); info->what = dtex_code_text(DTEX_OK);
    *out = NULL;
    cap_ctx c = {.info = info};
    if (n < 0 || (n && !h)) return fail(&c, DTEX_E_ARGS, h, 0, 0, 0);
    if (!n || !atomic_load_explicit(&g_seen, memory_order_acquire)) return DTEX_OK;
    long pg = sysconf(_SC_PAGESIZE); c.page = pg > 0 && !((size_t)pg & ((size_t)pg - 1)) ? (size_t)pg : 4096;
    c.ov = calloc(1, sizeof *c.ov); c.wbuf = malloc(DTEX_WIN);
    int r = !c.ov || !c.wbuf ? fail(&c, DTEX_E_NOMEM, NULL, 0, 0, 0) : DTEX_OK;
    for (int i = 0; i < n && !r; i++) r = walk(&c, (const uint8_t *)(uintptr_t)h[i]);
    free(c.wbuf); free(c.nodes); map_free(&c.nidx); map_free(&c.jumps);
    if (r || !c.ov->idx.n) { dtex_free(c.ov); return r; }
    *out = c.ov;
    return DTEX_OK;
}

// ---------- Activation and lookup (translation side) ----------
static _Thread_local const dtex_overlay *t_ov;
static _Thread_local int t_on;
static void stop(const char *msg) { fprintf(stderr, "[dm] [nvn] FATAL deferred texture snapshot: %s\n", msg); abort(); }
void dtex_activate(const dtex_overlay *ov) {
    if (t_on) stop("activation while another submit snapshot is active on this thread");
    t_on = 1; t_ov = ov;
}
void dtex_deactivate(const dtex_overlay *ov) {
    if (!t_on || t_ov != ov) stop("deactivation of a submit snapshot that is not the active one");
    t_on = 0; t_ov = NULL;
}
int dtex_active(void) { return t_on; }
const p_bind_handles *dtex_resolve(const uint8_t *rec, const char **why) {
    if (!t_on) { *why = "no submit snapshot is active on this thread"; return NULL; }
    const p_bind_handles *b = dtex_lookup(t_ov, rec);
    if (!b) *why = "record is missing from the active submit snapshot";
    return b;
}
