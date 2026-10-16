// Command-stream record validation and optional provenance audit; see nvn_cmd_audit.h.
// DM_NVN_CMD_AUDIT=1 enables the audit. DM_NVN_CMD_AUDIT_TRACE=N additionally prints the first N metadata events as they happen.
// All tables are fixed size; nothing here allocates per event or copies command payloads.
//
// Fault safety: every audit read of recorded storage (headers, JUMP targets, hashing, frame pointers)
// goes through one bounded safe-read primitive (chunked mach_vm_read_overwrite), so an unmapped or
// protected donation is reported as an explicit "unreadable" state instead of faulting.
//
// Scope: this is bounded provenance debugging for streams the host itself recorded. It does not make
// arbitrary command pointers fault-safe before the interpreter's own payload reads, does not validate
// resource pointers embedded in payloads, and does not detect JUMP cycles; those are pre-existing
// interpreter-hardening concerns outside this diagnostic.
#include "nvn_cmd_audit.h"
#include "nvn_obj.h"
#include <dlfcn.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ALOG(...) do { fprintf(stderr, "[dm] [nvn-audit] " __VA_ARGS__); fputc('\n', stderr); } while (0)

// ---------- One bounded safe-read primitive for every audit access to recorded storage ----------
// Chunked mach_vm_read_overwrite: reports failure for unmapped or protected ranges instead of
// faulting, so an inaccessible donation becomes an explicit "unreadable" state.
#define SAFE_CHUNK 4096
static bool safe_read(const void *addr, size_t n, void *out) {
    const uint8_t *src = addr; uint8_t *dst = out;
    while (n) {
        size_t chunk = n > SAFE_CHUNK ? SAFE_CHUNK : n; mach_vm_size_t got = 0;
        if (mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)(uintptr_t)src, (mach_vm_size_t)chunk,
                                   (mach_vm_address_t)(uintptr_t)dst, &got) != KERN_SUCCESS || got != chunk) return false;
        src += chunk; dst += chunk; n -= chunk;
    }
    return true;
}

// ---------- Per-op record size rules, derived from the host record structs in nvn_obj.h ----------
// Fixed records carry an exact payload; count-derived records carry a small prefix plus n elements
// (BIND_*) or a length field (UPDATE_UB), so only a minimum is checkable from the header alone.
#define AL8(x) (((x) + 7u) & ~(uint32_t)7u)
enum { FK_NONE, FK_EXACT, FK_VAR };
typedef struct { uint8_t kind; uint32_t v; } size_rule;
static const size_rule g_sz[OP_COUNT_] = {
    [OP_BEGIN] = {FK_EXACT, 8}, [OP_END] = {FK_EXACT, 8},
    [OP_JUMP] = {FK_EXACT, 16}, [OP_CALL] = {FK_EXACT, 16},
    [OP_SIGNAL_EVENT] = {FK_EXACT, 24}, [OP_REPORT_COUNTER] = {FK_EXACT, 24},
    [OP_COPY_B2B] = {FK_EXACT, 32}, [OP_RESET_COUNTER] = {FK_EXACT, 16},
    [OP_UPDATE_UB] = {FK_VAR, 24},                     //exact size = AL8(24 + embedded data length)
    [OP_SET_RT] = {FK_EXACT, AL8(8 + sizeof(p_set_rt))},
    [OP_CLEAR_COLOR] = {FK_EXACT, AL8(8 + sizeof(p_clear_color))},
    [OP_CLEAR_DS] = {FK_EXACT, AL8(8 + sizeof(p_clear_ds))},
    [OP_VIEWPORTS] = {FK_EXACT, AL8(8 + sizeof(p_viewports))},
    [OP_SCISSOR] = {FK_EXACT, AL8(8 + sizeof(p_scissor))},
    [OP_DEPTH_RANGE] = {FK_EXACT, AL8(8 + sizeof(p_depth_range))},
    [OP_BIND_PROGRAM] = {FK_EXACT, AL8(8 + sizeof(p_bind_program))},
    [OP_BIND_STATE] = {FK_EXACT, AL8(8 + sizeof(p_bind_state))},
    [OP_BIND_VATTR] = {FK_VAR, AL8(8 + offsetof(p_bind_vattr, st))},
    [OP_BIND_VSTREAM] = {FK_VAR, AL8(8 + offsetof(p_bind_vstream, st))},
    [OP_BIND_VBUF] = {FK_VAR, AL8(8 + offsetof(p_bind_vbuf, r))},
    [OP_BIND_UBO] = {FK_EXACT, AL8(8 + sizeof(p_bind_buf))},
    [OP_BIND_SSBO] = {FK_EXACT, AL8(8 + sizeof(p_bind_buf))},
    [OP_BIND_TEX] = {FK_VAR, AL8(8 + offsetof(p_bind_handles, h))},
    [OP_BIND_SAMP] = {FK_VAR, AL8(8 + offsetof(p_bind_handles, h))},
    [OP_BIND_IMG] = {FK_VAR, AL8(8 + offsetof(p_bind_handles, h))},
    [OP_STENCIL] = {FK_EXACT, AL8(8 + sizeof(p_stencil))},
    [OP_POLY_OFFSET] = {FK_EXACT, AL8(8 + sizeof(p_poly_offset))},
    [OP_DRAW] = {FK_EXACT, AL8(8 + sizeof(p_draw))},
    [OP_DRAW_IDX] = {FK_EXACT, AL8(8 + sizeof(p_draw_idx))},
    [OP_DISPATCH] = {FK_EXACT, AL8(8 + sizeof(p_dispatch))},
    [OP_BARRIER] = {FK_EXACT, AL8(8 + 8)},
    [OP_SET_TEX_POOL] = {FK_EXACT, AL8(8 + sizeof(p_set_pool))},
    [OP_SET_SAMP_POOL] = {FK_EXACT, AL8(8 + sizeof(p_set_pool))},
    [OP_COPY_B2T] = {FK_EXACT, AL8(8 + sizeof(p_copy_b2t))},
    [OP_COPY_T2B] = {FK_EXACT, AL8(8 + sizeof(p_copy_b2t))},
    [OP_COPY_T2T] = {FK_EXACT, AL8(8 + sizeof(p_copy_t2t))},
    [OP_CLEAR_TEX] = {FK_EXACT, AL8(8 + sizeof(p_clear_tex))},
    [OP_COPY_STRIDE] = {FK_EXACT, AL8(8 + sizeof(p_copy_stride))},
    [OP_BLEND_COLOR] = {FK_EXACT, AL8(8 + sizeof(p_blend_color))},
    [OP_DEPTH_CLAMP] = {FK_EXACT, AL8(8 + sizeof(p_depth_clamp))},
    [OP_POINT_SIZE] = {FK_EXACT, AL8(8 + sizeof(p_point_size))},
    [OP_LINE_WIDTH] = {FK_EXACT, AL8(8 + sizeof(p_line_width))},
    [OP_BIND_TEX_DEFERRED] = {FK_EXACT, AL8(8 + sizeof(p_bind_tex_deferred))},
};

const char *cmd_hdr_defect(uint32_t op, uint32_t size) {
    if (op >= OP_COUNT_) return "unknown opcode";
    if (size < sizeof(op_hdr)) return "size smaller than the record header";
    if (size & 7) return "size not 8-byte aligned";
    if (size > CMD_REC_MAX_SIZE) return "size beyond the host record limit";
    const size_rule *r = &g_sz[op];
    if (r->kind == FK_EXACT && size != r->v) return "record size does not match the exact size required by this opcode";
    if (r->kind == FK_VAR && size < r->v) return "record size is below the minimum for this variable-size opcode";
    return NULL;
}

// Count/length fields sit right after the {first,n} or {stage,first,n} prefix; the offsets below are
// byte offsets from the record start. The header check has already run, so each field lies inside the
// declared record and reading it cannot pass the record end.
//
// Variable binding records have two host-produced forms: the compact form the recorder emits today
// (prefix + n elements) and the legacy fixed-length form (the full-capacity struct, e.g. 280-byte
// BIND_TEX; see the RECN comment in hle_nvn.c). Both are count-safe: the replay reads only the
// first n elements. Any other size — undersized for the embedded count, or between the two forms —
// is rejected, so a walker can never read a payload past the record.
_Static_assert(sizeof(op_hdr) == 8, "record header prefix size");
_Static_assert(sizeof(op_hdr) + sizeof(p_bind_tex_deferred) <= CMD_REC_PREFIX_MAX, "deferred validation prefix");
_Static_assert(offsetof(p_bind_handles, n) + sizeof(int32_t) + sizeof(op_hdr) <= CMD_REC_PREFIX_MAX, "binding count prefix");
_Static_assert(24 <= CMD_REC_PREFIX_MAX, "uniform update length prefix");
const char *cmd_rec_defect(const uint8_t *rec) {
    op_hdr h; memcpy(&h, rec, sizeof h);
    const char *d = cmd_hdr_defect(h.op, h.size);
    if (d) return d;
    switch (h.op) {
        case OP_BIND_VATTR: case OP_BIND_VSTREAM: {
            int32_t n; memcpy(&n, rec + 12, 4);
            uint32_t elem = h.op == OP_BIND_VATTR ? 4 : 8;   //sizeof(va_st) / sizeof(vs_st)
            if (n < 0 || n > 16) return "element count outside the host range for this opcode";
            uint32_t compact = AL8(16u + elem * (uint32_t)n), full = AL8(8u + (uint32_t)(h.op == OP_BIND_VATTR ? sizeof(p_bind_vattr) : sizeof(p_bind_vstream)));
            if (h.size != compact && h.size != full) return "record size does not match the embedded element count";
            return NULL; }
        case OP_BIND_VBUF: {
            int32_t n; memcpy(&n, rec + 12, 4);
            if (n < 0 || n > 16) return "element count outside the host range for this opcode";
            if (h.size != AL8(16u + (uint32_t)sizeof(NVNbufferRange) * (uint32_t)n) && h.size != AL8(8u + (uint32_t)sizeof(p_bind_vbuf)))
                return "record size does not match the embedded element count";
            return NULL; }
        case OP_BIND_TEX: case OP_BIND_SAMP: case OP_BIND_IMG: {
            int32_t first, n; memcpy(&first, rec + 12, 4); memcpy(&n, rec + 16, 4);
            int32_t limit = h.op == OP_BIND_IMG ? 32 : 128;   //host slot range for this binding kind
            if (n < 0 || n > 32 || first < 0 || first > limit || n > limit - first) return "binding slot range outside the host range for this opcode";
            if (h.size != AL8((uint32_t)(8 + offsetof(p_bind_handles, h)) + 8u * (uint32_t)n) && h.size != AL8(8u + (uint32_t)sizeof(p_bind_handles)))
                return "record size does not match the embedded element count";
            return NULL; }
        case OP_BIND_TEX_DEFERRED: {
            // Scalar fields only; the recorded handle-array pointer is never dereferenced here.
            p_bind_tex_deferred d; memcpy(&d, rec + 8, sizeof d);
            if ((uint32_t)d.stage >= 6 || d.first < 0 || d.first > 32 || d.n < 0 || d.n > 32 - d.first) return "deferred texture binding range outside the native 32-slot capacity";
            return NULL; }
        case OP_UPDATE_UB: {
            uint64_t n; memcpy(&n, rec + 16, 8);   //data length; exact record size is AL8(24 + n)
            if (n > h.size - 24 || n + 7 < h.size - 24) return "record size does not match the embedded data length";
            return NULL; }
        default: return NULL;
    }
}

bool cmd_safe_read(const void *addr, size_t n, void *out) { return safe_read(addr, n, out); }

const char *cmd_entry_defect(const uint8_t *handle, uint32_t *op_out, uint32_t *size_out) {
    op_hdr h;
    if (op_out) *op_out = 0;
    if (size_out) *size_out = 0;
    if (!safe_read(handle, sizeof h, &h)) return "entry header unreadable (donation unmapped or protected)";
    if (op_out) *op_out = h.op;
    if (size_out) *size_out = h.size;
    if (h.op != OP_BEGIN || h.size != 8) return "command handle does not start with an exact OP_BEGIN, size 8 record";
    return NULL;
}

enum { EV_INIT, EV_FINAL, EV_ADD, EV_QUERY, EV_CALLBACK, EV_END, EV_CALL, EV_SUBMIT, EV_EXEC, EV_STALE };
static const char *const ev_names[] = {"init", "final", "add", "query", "callback", "end", "call", "submit", "exec", "stale"};
#define ABT 6
typedef struct { uint64_t seq, frame; uint32_t kind, tid, gth; const void *cb; uint64_t a, b, c, d; const void *ra; const void *bt[ABT]; } aev;

#define AEV_N 256
#define DON_N 8192
#define REC_N 16384
#define CALL_N 16384
#define RSEG 6
typedef struct { const uint8_t *s, *e; uint64_t sum; op_hdr hs; } aseg;   //hs: header saved at interval start
typedef struct { const uint8_t *h; const void *cb; uint64_t gen, frame, seq, sum_all; uint32_t nseg, segs_total, ncall; int sum_ok; op_hdr h0; aseg seg[RSEG]; } arec;
typedef struct { const uint8_t *rec; uint64_t child, child_gen; } acall;
typedef struct { uint64_t seq, frame; const void *cb; const uint8_t *base, *end; int host; } adon;

static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static aev g_ev[AEV_N]; static uint64_t g_seq, g_gen, g_reports;
static adon g_don[DON_N]; static uint64_t g_don_n;
static arec g_rec[REC_N]; static uint64_t g_rec_live, g_evicted;
static acall g_call[CALL_N]; static uint64_t g_call_live, g_call_evicted;
static long g_trace;
static int g_on;
// Optional host hooks, resolved at run time so unit tests link without the guest thread layer.
typedef struct { char name[16]; uint8_t *base; size_t size; } amod;
static uint32_t (*g_thr_handle)(void);
static const amod *(*g_module_of)(const void *);
static void (*g_thread_dump)(void);
static uint64_t (*g_frame_count)(void);
static uint64_t frame_now(void) { return g_frame_count ? g_frame_count() : 0; }

// Audit configuration is process-start state (environment); pthread_once publishes it and the dlsym
// hook pointers exactly once, so every reader sees the immutable result without further locking.
static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static void cmda_init(void) {
    const char *e = getenv("DM_NVN_CMD_AUDIT"); int on = e && *e && strcmp(e, "0");
    const char *t = getenv("DM_NVN_CMD_AUDIT_TRACE"); g_trace = on && t ? atol(t) : 0;
    if (on) {
        g_thr_handle = (uint32_t (*)(void))dlsym(RTLD_DEFAULT, "thr_cur_handle");
        g_module_of = (const amod *(*)(const void *))dlsym(RTLD_DEFAULT, "module_of");
        g_thread_dump = (void (*)(void))dlsym(RTLD_DEFAULT, "svc_thread_dump");
        g_frame_count = (uint64_t (*)(void))dlsym(RTLD_DEFAULT, "dm_nvn_frame_count");
        ALOG("command-stream provenance audit enabled (metadata only; trace first %ld events)", g_trace);
    }
    g_on = on;
}
int cmda_on(void) { pthread_once(&g_once, cmda_init); return g_on; }

static uint32_t tid(void) { return (uint32_t)pthread_mach_thread_np(pthread_self()); }
static unsigned hslot(const void *p, unsigned n) { uint64_t x = (uint64_t)(uintptr_t)p >> 3; x *= 0x9e3779b97f4a7c15ull; return (unsigned)(x >> 40) & (n - 1); }

#define FNV0 0xcbf29ce484222325ull
// Hash [s, e) without faulting. Two FNV streams run over the same bytes: hi restarts per interval,
// hg continues across intervals (the aggregate checksum over ALL intervals, kept so a recording with
// more intervals than the bounded table still detects a change somewhere). Returns false when unreadable.
static bool hash_span(uint64_t hi_seed, uint64_t hg_seed, const uint8_t *s, const uint8_t *e, uint64_t *hi_out, uint64_t *hg_out) {
    uint64_t hi = hi_seed, hg = hg_seed; uint8_t buf[SAFE_CHUNK];
    while (s < e) {
        size_t chunk = (size_t)(e - s); if (chunk > sizeof buf) chunk = sizeof buf;
        if (!safe_read(s, chunk, buf)) return false;
        for (size_t i = 0; i < chunk; i++) { uint64_t b = buf[i]; hi = (hi ^ b) * 0x100000001b3ull; hg = (hg ^ b) * 0x100000001b3ull; }
        s += chunk;
    }
    *hi_out = hi; *hg_out = hg; return true;
}

static int fmt_addr(char *out, size_t n, const void *a) {
    const amod *m = a && g_module_of ? g_module_of(a) : NULL;
    if (m) return snprintf(out, n, "%.16s+%#llx", m->name, (unsigned long long)((const uint8_t *)a - m->base));
    return snprintf(out, n, "%p", a);
}
static void ev_print(const aev *e) {
    char where[48 + ABT * 32] = ""; size_t k = 0;
    if (e->ra) { k += (size_t)snprintf(where, sizeof where, " ra="); k += (size_t)fmt_addr(where + k, sizeof where - k, e->ra); }
    for (int i = 0; i < ABT && e->bt[i] && k < sizeof where - 32; i++) { k += (size_t)snprintf(where + k, sizeof where - k, i ? " <- " : " guest-bt "); k += (size_t)fmt_addr(where + k, sizeof where - k, e->bt[i]); }
    ALOG("#%llu f%llu t%x g%#x %s cb=%p a=%#llx b=%#llx c=%#llx d=%#llx%s", (unsigned long long)e->seq, (unsigned long long)e->frame, e->tid, e->gth, ev_names[e->kind], e->cb,
         (unsigned long long)e->a, (unsigned long long)e->b, (unsigned long long)e->c, (unsigned long long)e->d, where);
}
// Caller holds g_mu.
static uint64_t ev_push_ra(uint32_t kind, const void *cb, uint64_t a, uint64_t b, uint64_t c, uint64_t d, const void *ra) {
    aev e = {++g_seq, frame_now(), kind, tid(), g_thr_handle ? g_thr_handle() : 0, cb, a, b, c, d, ra, {0}};
    g_ev[e.seq % AEV_N] = e;
    if ((long)e.seq <= g_trace && kind != EV_ADD) ev_print(&e);   //Donations print after their backtrace is attached
    return e.seq;
}
static uint64_t ev_push(uint32_t kind, const void *cb, uint64_t a, uint64_t b, uint64_t c, uint64_t d) { return ev_push_ra(kind, cb, a, b, c, d, NULL); }

static arec *rec_find(const uint8_t *h) { arec *r = &g_rec[hslot(h, REC_N)]; return r->h == h ? r : NULL; }

// Verification outcome for one stored recording. MATCH means every byte of every interval compared
// equal; CHANGED/UNREADABLE name the first offending tracked interval; INCOMPLETE means the bounded
// interval table was exhausted (segs_total > RSEG), so a full match cannot be claimed even when the
// aggregate checksum is unchanged. agg refines INCOMPLETE: 0 aggregate unchanged, 1 aggregate
// changed (a difference exists beyond the tracked intervals), 2 comparison unavailable.
enum { V_MATCH, V_CHANGED, V_UNREADABLE, V_INCOMPLETE };
typedef struct { int status; uint32_t idx; int agg; } vres;

// Aggregate checksum state: walk the current stream with safe reads and hash every interval.
// Returns 0 when the aggregate equals the one saved at EndRecording, 1 when it differs, 2 when the
// walk cannot complete (malformed or unreadable stream, or no aggregate was saved).
static int agg_state(const arec *r) {
    if (!r->sum_ok) return 2;
    const uint8_t *p = r->h, *start = p; uint64_t hg = FNV0;
    for (uint32_t steps = 0; steps <= (1u << 22); steps++) {
        op_hdr h;
        if (!safe_read(p, sizeof h, &h) || cmd_hdr_defect(h.op, h.size)) return 2;
        if (steps == 0 && (h.op != OP_BEGIN || h.size != 8)) return 2;
        if (h.op == OP_JUMP || h.op == OP_END) {
            uint64_t hi, g2;
            if (!hash_span(FNV0, hg, start, p + h.size, &hi, &g2)) return 2;
            hg = g2;
            if (h.op == OP_END) return hg == r->sum_all ? 0 : 1;
            if (!safe_read(p + 8, 8, &p) || !p) return 2;
            start = p; continue;
        }
        p += h.size;
    }
    return 2;
}

// Recompute the stored intervals with safe reads; never touches an unmapped donation.
static vres rec_verify(const arec *r) {
    vres v = {V_MATCH, 0, 0};
    for (uint32_t i = 0; i < r->nseg; i++) {
        uint64_t sum, g;
        if (!hash_span(FNV0, FNV0, r->seg[i].s, r->seg[i].e, &sum, &g)) { v.status = V_UNREADABLE; v.idx = i; return v; }
        if (sum != r->seg[i].sum) { v.status = V_CHANGED; v.idx = i; return v; }
    }
    if (r->segs_total > r->nseg) { v.status = V_INCOMPLETE; v.agg = agg_state(r); }
    return v;
}

// Donations after `after` that overlap [s, e).
static void report_overlaps(const uint8_t *s, const uint8_t *e, uint64_t after) {
    int shown = 0; uint64_t lo = g_don_n > DON_N ? g_don_n - DON_N : 0;
    for (uint64_t i = lo; i < g_don_n && shown < 8; i++) {
        const adon *d = &g_don[i % DON_N];
        if (d->seq <= after || d->end <= s || d->base >= e) continue;
        ALOG("  overlapped by later donation #%llu frame %llu cb=%p [%p,%p) %s", (unsigned long long)d->seq, (unsigned long long)d->frame, d->cb, (const void *)d->base, (const void *)d->end, d->host ? "host" : "guest");
        shown++;
    }
    if (!shown) ALOG("  no later AddCommandMemory donation overlaps it (donation log holds %llu of %llu)", (unsigned long long)(g_don_n - lo), (unsigned long long)g_don_n);
}

static void rec_describe(const arec *r) {
    ALOG("  recording handle=%p gen=%llu cb=%p ended #%llu frame %llu, intervals tracked %u of %u total%s, %u call(s)", (const void *)r->h, (unsigned long long)r->gen, r->cb,
         (unsigned long long)r->seq, (unsigned long long)r->frame, r->nseg, r->segs_total, r->segs_total > r->nseg ? " (bounded audit stores only a prefix)" : "", r->ncall);
    for (uint32_t i = 0; i < r->nseg; i++) ALOG("  interval %u [%p,%p)", i, (const void *)r->seg[i].s, (const void *)r->seg[i].e);
}

// Which tracked recording, if any, contains p.
static const arec *rec_containing(const uint8_t *p) {
    const arec *best = NULL;
    for (unsigned i = 0; i < REC_N; i++) {
        const arec *r = &g_rec[i]; if (!r->h) continue;
        for (uint32_t k = 0; k < r->nseg; k++) if (p >= r->seg[k].s && p < r->seg[k].e && (!best || r->gen > best->gen)) best = r;
    }
    return best;
}

static int may_report(void) { return ++g_reports <= 16; }

// Caller holds g_mu. One bounded, honest verdict per recording: changed / unreadable / incomplete
// coverage. Original headers come from the metadata saved at EndRecording; current headers are
// reread through safe_read only for the diagnostic.
static void tables_note(void);
static void report_verify(const char *where, const arec *r, vres v) {
    if (v.status == V_MATCH || !may_report()) return;
    tables_note();
    if (v.status == V_CHANGED) {
        ALOG("[ERROR] %s: recorded stream changed after EndRecording (interval %u of %u tracked, %u total); saved headers: handle op=%#x size=%#x, interval start op=%#x size=%#x",
             where, v.idx, r->nseg, r->segs_total, r->h0.op, r->h0.size, r->seg[v.idx].hs.op, r->seg[v.idx].hs.size);
        op_hdr c0, cs;
        int r0 = safe_read(r->h, sizeof c0, &c0), rs = safe_read(r->seg[v.idx].s, sizeof cs, &cs);
        if (r0 && rs) ALOG("  headers now: handle op=%#x size=%#x, interval start op=%#x size=%#x", c0.op, c0.size, cs.op, cs.size);
        else ALOG("  current headers unreadable (handle %s, interval start %s)", r0 ? "readable" : "unreadable", rs ? "readable" : "unreadable");
        rec_describe(r);
        report_overlaps(r->seg[v.idx].s, r->seg[v.idx].e, r->seq);
    } else if (v.status == V_UNREADABLE) {
        ALOG("[ERROR] %s: recorded stream interval %u of %u tracked (%u total) is unreadable (donation unmapped or protected after EndRecording); saved interval-start header op=%#x size=%#x; bytes cannot be verified",
             where, v.idx, r->nseg, r->segs_total, r->seg[v.idx].hs.op, r->seg[v.idx].hs.size);
        rec_describe(r);
    } else {   //V_INCOMPLETE: the bounded interval table was exhausted; never claim a full match
        const char *agg = v.agg == 0 ? "unchanged" : v.agg == 1 ? "CHANGED: a difference exists beyond the tracked intervals" : "unavailable (stream unreadable or not hashed at EndRecording)";
        ALOG("%s %s: first %u interval(s) tracked and unchanged, coverage unavailable after interval %u (%u intervals total); aggregate checksum over all intervals: %s",
             v.agg == 1 ? "[ERROR]" : "[audit]", where, r->nseg, r->nseg - 1, r->segs_total, agg);
        rec_describe(r);
    }
}

// Current donation and open recording of each live command buffer. The host keeps its IR (the
// BEGIN/END chain and the command records) in the donated command storage, so a donation that
// overlaps another buffer's open range [recording start, donation end) is host IR capacity/used
// overlap: later writes overwrite the earlier recorder's host IR before its final size is known.
// Native control-only recordings can legally leave COMMAND storage untouched, so this is reported
// as host IR use, never as a native API violation.
#define OWN_N 256
typedef struct { const void *cb; const uint8_t *base, *end, *rec; uint32_t gth; int host, open; } aown;
static aown g_own[OWN_N]; static uint64_t g_own_full;

// Caller holds g_mu. Honest bounded-table state: what is live, what was evicted, where coverage ended.
static void tables_note(void) {
    ALOG("  direct-map tables: %llu recording(s) live of %d slots (%llu evicted), %llu call link(s) live of %d (%llu evicted)%s",
         (unsigned long long)g_rec_live, REC_N, (unsigned long long)g_evicted, (unsigned long long)g_call_live, CALL_N, (unsigned long long)g_call_evicted,
         g_own_full ? "; ownership coverage lost, see next line" : "");
    if (g_own_full) ALOG("  ownership tracking lost %llu entries: open host-IR overlap coverage is incomplete, absence of an overlap report proves nothing", (unsigned long long)g_own_full);
}

// Caller holds g_mu. Returns the entry of cb, creating it when asked.
static aown *own_get(const void *cb, int create) {
    aown *empty = NULL;
    for (int i = 0; i < OWN_N; i++) { if (g_own[i].cb == cb) return &g_own[i]; if (!g_own[i].cb && !empty) empty = &g_own[i]; }
    if (!create) return NULL;
    if (!empty) {
        if (!g_own_full) ALOG("[audit] ownership table full (%d command buffers): further command buffers are untracked, open host-IR overlap coverage is lost", OWN_N);
        g_own_full++;
        return NULL;
    }
    *empty = (aown){.cb = cb}; return empty;
}
void cmda_cb_init(const void *cb) {
    if (!cmda_on()) return; pthread_mutex_lock(&g_mu); ev_push(EV_INIT, cb, 0, 0, 0, 0); aown *o = own_get(cb, 0); if (o) *o = (aown){0}; pthread_mutex_unlock(&g_mu);
}
void cmda_cb_final(const void *cb) {
    if (!cmda_on()) return; pthread_mutex_lock(&g_mu); ev_push(EV_FINAL, cb, 0, 0, 0, 0); aown *o = own_get(cb, 0); if (o) *o = (aown){0}; pthread_mutex_unlock(&g_mu);
}
void cmda_begin(const void *cb, const uint8_t *p) {
    if (!cmda_on()) return;
    pthread_mutex_lock(&g_mu);
    aown *o = own_get(cb, 1); if (o) { o->open = 1; o->rec = p; o->gth = g_thr_handle ? g_thr_handle() : 0; }
    pthread_mutex_unlock(&g_mu);
}

// Bounded frame-pointer walk from the caller's frame: keeps return addresses that lie in guest
// modules. The walk is confined to the current pthread's stack and every frame read goes through
// safe_read, so a corrupt or foreign chain simply ends the walk.
static void guest_bt(const void *fp0, const void *bt[ABT]) {
    pthread_t self = pthread_self();
    uintptr_t hi = (uintptr_t)pthread_get_stackaddr_np(self), lo = hi - pthread_get_stacksize_np(self);
    uintptr_t f = (uintptr_t)fp0; int n = 0;
    if (f < lo || f >= hi) return;
    for (int depth = 0; depth < 32 && n < ABT && f; depth++) {
        if ((f & 15) || f < (uintptr_t)fp0 || f - (uintptr_t)fp0 > (1u << 20)) break;
        uintptr_t w[2];
        if (!safe_read((const void *)f, sizeof w, w)) break;
        const void *lr = (const void *)w[1]; uintptr_t next = w[0];
        if (lr && g_module_of && g_module_of(lr)) bt[n++] = lr;
        if (next <= f || next >= hi) break;
        f = next;
    }
}
void cmda_add(const void *cb, const uint8_t *base, const uint8_t *end, int host, const uint8_t *jump_at, const void *ra) {
    if (!cmda_on()) return;
    const void *bt[ABT] = {0}; guest_bt(__builtin_frame_address(0), bt);
    pthread_mutex_lock(&g_mu);
    uint64_t seq = ev_push_ra(EV_ADD, cb, (uint64_t)(uintptr_t)base, (uint64_t)(end - base), (uint64_t)(uintptr_t)jump_at, (uint64_t)host, ra);
    memcpy(g_ev[seq % AEV_N].bt, bt, sizeof bt);
    if ((long)seq <= g_trace) ev_print(&g_ev[seq % AEV_N]);
    uint32_t gth = g_ev[seq % AEV_N].gth;
    for (int i = 0; i < OWN_N && !host; i++) {
        const aown *y = &g_own[i];
        if (!y->cb || y->cb == cb || !y->open || y->host || end <= y->rec || base >= y->end) continue;
        if (may_report()) {
            ALOG("[audit] host IR donation [%p,%p) to cb %p (guest thread %#x) overlaps the current open host-IR segment [%p,%p) of cb %p (guest thread %#x): the host stores BEGIN/END and its unified IR in donated command storage, so the later recorder overwrites the earlier one's host IR; native control-only recordings may leave COMMAND storage untouched, so this reports host IR capacity/used overlap, not a native API violation",
                 (const void *)base, (const void *)end, cb, gth, (const void *)y->rec, (const void *)y->end, y->cb, y->gth);
            ev_print(&g_ev[seq % AEV_N]);
            if (g_own_full) ALOG("  (ownership table lost %llu entries; other overlaps may be unreported)", (unsigned long long)g_own_full);
        }
    }
    aown *o = own_get(cb, 1);
    if (o) { o->base = base; o->end = end; o->host = host; if (o->open) o->rec = base; }   //A recording that continues into a new segment writes from its start
    g_don[g_don_n++ % DON_N] = (adon){seq, frame_now(), cb, base, end, host};
    pthread_mutex_unlock(&g_mu);
}
void cmda_query(const void *cb, int which, size_t value, const void *ra) {
    if (!cmda_on()) return; pthread_mutex_lock(&g_mu); ev_push_ra(EV_QUERY, cb, (uint64_t)which, value, 0, 0, ra); pthread_mutex_unlock(&g_mu);
}
void cmda_callback(const void *cb, const uint8_t *cur, const uint8_t *end, size_t need) {
    if (!cmda_on()) return; pthread_mutex_lock(&g_mu); ev_push(EV_CALLBACK, cb, (uint64_t)(uintptr_t)cur, (uint64_t)(uintptr_t)end, need, 0); pthread_mutex_unlock(&g_mu);
}

void cmda_end(const void *cb, const uint8_t *handle) {
    if (!cmda_on()) return;
    arec r = {.h = handle, .cb = cb, .sum_ok = 1};
    const uint8_t *p = handle, *start = handle; const char *why = NULL; uint32_t steps = 0; uint64_t hg = FNV0;
    for (;; steps++) {
        op_hdr h;
        if (!safe_read(p, sizeof h, &h)) { why = "stream header unreadable"; r.sum_ok = 0; break; }
        if (p == start && r.nseg < RSEG) r.seg[r.nseg].hs = h;   //Save the original header at each tracked interval start
        if (!steps) r.h0 = h;
        if (steps > (1u << 22)) { why = "stream walk did not reach END"; r.sum_ok = 0; break; }
        if ((why = cmd_hdr_defect(h.op, h.size))) { r.sum_ok = 0; break; }
        if (steps == 0 && (h.op != OP_BEGIN || h.size != 8)) { why = "handle does not start with an exact BEGIN, size 8 record"; r.sum_ok = 0; break; }
        if (h.op == OP_JUMP || h.op == OP_END) {
            const uint8_t *e = p + h.size; uint64_t hi, g2;
            if (!hash_span(FNV0, hg, start, e, &hi, &g2)) { why = "interval unreadable while hashing"; r.sum_ok = 0; break; }
            hg = g2;
            if (r.nseg < RSEG) { r.seg[r.nseg].s = start; r.seg[r.nseg].e = e; r.seg[r.nseg].sum = hi; r.nseg++; }
            r.segs_total++;
            if (h.op == OP_END) break;
            if (!safe_read(p + 8, 8, &p)) { why = "JUMP target unreadable"; r.sum_ok = 0; break; }
            start = p;
            if (!p) { why = "JUMP to NULL"; r.sum_ok = 0; break; }
            continue;
        }
        if (h.op == OP_CALL) r.ncall++;
        p += h.size;
    }
    r.sum_all = hg;
    pthread_mutex_lock(&g_mu);
    aown *o = own_get(cb, 0); if (o) o->open = 0;
    r.gen = ++g_gen; r.frame = frame_now();
    r.seq = ev_push(EV_END, cb, (uint64_t)(uintptr_t)handle, r.gen, (uint64_t)((uint64_t)r.segs_total << 32 | r.nseg), (uint64_t)(uintptr_t)p);
    if (why) { if (may_report()) ALOG("[ERROR] EndRecording produced a malformed or unreadable stream: %s at %p (handle %p, cb %p; intervals walked %u)", why, (const void *)p, (const void *)handle, cb, r.segs_total); }
    else {
        arec *slot = &g_rec[hslot(handle, REC_N)];
        if (slot->h && slot->h != handle) g_evicted++;
        else if (!slot->h) g_rec_live++;
        *slot = r;
    }
    pthread_mutex_unlock(&g_mu);
}

void cmda_call_rec(const void *cb, const uint8_t *rec, uint64_t handle) {
    if (!cmda_on()) return;
    pthread_mutex_lock(&g_mu);
    const arec *r = rec_find((const uint8_t *)(uintptr_t)handle); vres v = r ? rec_verify(r) : (vres){V_MATCH, 0, 0};
    ev_push(EV_CALL, cb, (uint64_t)(uintptr_t)rec, handle, r ? r->gen : 0, (uint64_t)(((unsigned)v.status << 8) | (v.idx & 255)));
    acall *cs = &g_call[hslot(rec, CALL_N)];
    if (cs->rec && cs->rec != rec) g_call_evicted++;
    else if (!cs->rec) g_call_live++;
    *cs = (acall){rec, handle, r ? r->gen : 0};
    if (!r) { if (may_report()) { ALOG("[ERROR] CallCommands handle %#llx was not returned by a tracked EndRecording", (unsigned long long)handle); tables_note(); } }
    else report_verify("CallCommands", r, v);
    pthread_mutex_unlock(&g_mu);
}

void cmda_submit(int n, const uint64_t *h) {
    if (!cmda_on()) return;
    pthread_mutex_lock(&g_mu);
    ev_push(EV_SUBMIT, NULL, (uint64_t)n, n > 0 ? h[0] : 0, n > 1 ? h[1] : 0, 0);
    for (int i = 0; i < n; i++) {
        const arec *r = rec_find((const uint8_t *)(uintptr_t)h[i]);
        if (!r) { if (may_report()) { ALOG("[ERROR] QueueSubmitCommands handle %#llx was not returned by a tracked EndRecording", (unsigned long long)h[i]); tables_note(); } continue; }
        report_verify("QueueSubmitCommands", r, rec_verify(r));
    }
    pthread_mutex_unlock(&g_mu);
}

void cmda_exec(uint64_t handle, const uint8_t *call_rec, int depth) {
    if (!cmda_on()) return;
    pthread_mutex_lock(&g_mu);
    const arec *r = rec_find((const uint8_t *)(uintptr_t)handle);
    const acall *c = call_rec ? &g_call[hslot(call_rec, CALL_N)] : NULL; if (c && c->rec != call_rec) c = NULL;
    vres v = r ? rec_verify(r) : (vres){V_MATCH, 0, 0};
    ev_push(EV_EXEC, NULL, handle, (uint64_t)(uintptr_t)call_rec, r ? r->gen : 0, (uint64_t)(((unsigned)v.status << 8) | (v.idx & 255)));
    if (c && c->child != handle) { if (may_report()) ALOG("[ERROR] replay: CALL record %p now targets %#llx but recorded %#llx", (const void *)call_rec, (unsigned long long)handle, (unsigned long long)c->child); }
    else if (c && r && c->child_gen && c->child_gen != r->gen) {
        ev_push(EV_STALE, NULL, handle, c->child_gen, r->gen, 0);
        if (may_report()) { ALOG("[ERROR] replay depth %d: CALL record %p references handle %#llx generation %llu, but that address was re-recorded as generation %llu",
                                 depth, (const void *)call_rec, (unsigned long long)handle, (unsigned long long)c->child_gen, (unsigned long long)r->gen); rec_describe(r); }
    }
    if (!r) { if (may_report()) { ALOG("[ERROR] replay depth %d: handle %#llx (CALL record %p) was not returned by a tracked EndRecording", depth, (unsigned long long)handle, (const void *)call_rec); tables_note(); } }
    else report_verify("replay", r, v);
    pthread_mutex_unlock(&g_mu);
}

void cmda_bad(const uint8_t *p, uint32_t op, uint32_t size, const char *why, const uint8_t *const *ret, int ret_n) {
    if (!cmda_on()) return;
    pthread_mutex_lock(&g_mu);
    ALOG("malformed record %p op=%#x size=%#x (%s); call return stack depth %d", (const void *)p, op, size, why, ret_n);
    const arec *r = p ? rec_containing(p) : NULL;
    if (!p) ALOG("record pointer is NULL");
    else if (r) {
        vres v = rec_verify(r);
        if (v.status == V_CHANGED) ALOG("record lies inside a tracked recording; interval %u changed since EndRecording", v.idx);
        else if (v.status == V_UNREADABLE) ALOG("record lies inside a tracked recording; interval %u is unreadable", v.idx);
        else if (v.status == V_INCOMPLETE) ALOG("record lies inside a tracked recording; bounded coverage unavailable after interval %u of %u intervals", r->nseg - 1, r->segs_total);
        else { ALOG("record lies inside a tracked recording whose tracked intervals are unchanged"); rec_describe(r); }
        report_verify("malformed record", r, v);
    } else {
        //Bounded tables: the record is outside the stored intervals, not proven outside every recording.
        ALOG("record lies outside the intervals of the %llu tracked recording(s) (%llu evicted; per-recording interval coverage is a bounded prefix)", (unsigned long long)g_rec_live, (unsigned long long)g_evicted);
        report_overlaps(p, p + 8, 0);
    }
    for (int i = ret_n - 1; i >= 0 && i >= ret_n - 16; i--) {
        const arec *o = rec_containing(ret[i]);
        ALOG("  return %d -> %p in recording %p gen %llu", i, (const void *)ret[i], o ? (const void *)o->h : NULL, o ? (unsigned long long)o->gen : 0ull);
    }
    uint64_t lo = g_seq > 64 ? g_seq - 64 : 0;
    ALOG("last %llu metadata events:", (unsigned long long)(g_seq - lo));
    for (uint64_t s = lo + 1; s <= g_seq; s++) ev_print(&g_ev[s % AEV_N]);
    tables_note();
    pthread_mutex_unlock(&g_mu);
    if (g_thread_dump) g_thread_dump();   //Guest thread handles in the events above map to names, ideal cores and affinity masks here
}
