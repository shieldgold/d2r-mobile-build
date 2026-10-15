/* Horizon HIPC/CMIF host services. No Nintendo code or data is included.
 * Host architecture derived from jhaizhou-ops/d2r-mobile (MIT), commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt.
 * Protocol references (layouts, independently implemented with bounds checks):
 * https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/hipc.h
 * https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/cmif.h
 * https://github.com/switchbrew/libnx/blob/master/nx/source/services/sm.c
 * https://switchbrew.org/wiki/Log_services
 */
#include "services.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HANDLE_LIMIT 256u
#define DOMAIN_LIMIT 64u
#define IPC_LIMIT 0x100u
#define LOG_LIMIT (1024u * 1024u)
#define SFCI 0x49434653u
#define SFCO 0x4f434653u

enum ObjectKind { OBJECT_SM, OBJECT_LM, OBJECT_LOGGER };
typedef struct Object {
    enum ObjectKind kind;
    unsigned refs, initialized;
    uint64_t pid;
} Object;
typedef struct Session {
    unsigned refs, domain;
    Object *root;
    Object *objects[DOMAIN_LIMIT];
} Session;
typedef struct { uint32_t id; Session *session; } Handle;
struct botw_services {
    Handle handles[HANDLE_LIMIT];
    uint32_t next_handle, log_destination;
    botw_services_log_fn log;
    botw_services_memory_fn memory;
    void *opaque;
};
typedef struct {
    uint32_t type, command, token;
    unsigned pid, domain, domain_close;
    uint32_t object_id;
    const uint8_t *payload;
    size_t payload_size;
    uint64_t input_address;
    size_t input_size;
    unsigned input_count;
} Request;

static uint32_t load32(const void *p) { uint32_t n; memcpy(&n, p, 4); return n; }
static uint64_t load64(const void *p) { uint64_t n; memcpy(&n, p, 8); return n; }
static void store32(void *p, uint32_t n) { memcpy(p, &n, 4); }
static int problem(char *error, size_t cap, const char *format, ...) {
    if (error && cap) { va_list ap; va_start(ap, format); vsnprintf(error, cap, format, ap); va_end(ap); }
    return -1;
}
static void emit(botw_services *s, const char *format, ...) {
    if (!s->log) return;
    char text[2048]; va_list ap; va_start(ap, format); vsnprintf(text, sizeof text, format, ap); va_end(ap);
    s->log(s->opaque, text);
}
static const char *kind_name(enum ObjectKind kind) {
    return kind == OBJECT_SM ? "sm:" : kind == OBJECT_LM ? "lm" : "lm.ILogger";
}
static Object *new_object(enum ObjectKind kind) {
    Object *o = calloc(1, sizeof *o);
    if (o) { o->kind = kind; o->refs = 1; }
    return o;
}
static void release_object(Object *o) { if (o && !--o->refs) free(o); }
static Session *new_session(Object *o) {
    Session *session = calloc(1, sizeof *session);
    if (session) { session->refs = 1; session->root = o; }
    return session;
}
static void release_session(Session *session) {
    if (!session || --session->refs) return;
    if (session->domain) {
        for (unsigned i = 0; i < DOMAIN_LIMIT; i++) release_object(session->objects[i]);
    } else release_object(session->root);
    free(session);
}
static Handle *find_handle(botw_services *s, uint32_t handle) {
    for (unsigned i = 0; i < HANDLE_LIMIT; i++) if (s->handles[i].session && s->handles[i].id == handle) return &s->handles[i];
    return NULL;
}
static int assign_handle(botw_services *s, Session *session, uint32_t *out, char *error, size_t cap) {
    if (!s->next_handle || s->next_handle == UINT32_MAX) return problem(error, cap, "IPC handle identity space exhausted");
    for (unsigned i = 0; i < HANDLE_LIMIT; i++) if (!s->handles[i].session) {
        s->handles[i].id = s->next_handle++; s->handles[i].session = session; *out = s->handles[i].id;
        return 0;
    }
    return problem(error, cap, "IPC handle table exhausted (%u live sessions)", HANDLE_LIMIT);
}
static int object_handle(botw_services *s, Object *o, uint32_t *handle, char *error, size_t cap) {
    Session *session = new_session(o);
    if (!session) { release_object(o); return problem(error, cap, "allocate IPC session failed"); }
    if (assign_handle(s, session, handle, error, cap)) { release_session(session); return -1; }
    return 0;
}

botw_services *botw_services_create(botw_services_log_fn log, botw_services_memory_fn memory, void *opaque) {
    if (!memory) return NULL;
    botw_services *s = calloc(1, sizeof *s);
    if (s) { s->next_handle = 0x10000; s->log_destination = 0xffff; s->log = log; s->memory = memory; s->opaque = opaque; }
    return s;
}
void botw_services_destroy(botw_services *s) {
    if (!s) return;
    for (unsigned i = 0; i < HANDLE_LIMIT; i++) release_session(s->handles[i].session);
    free(s);
}
int botw_services_connect(botw_services *s, const char *name, size_t length, uint32_t *handle, char *error, size_t cap) {
    if (!s || !name || !handle || length != 3 || memcmp(name, "sm:", 3))
        return problem(error, cap, "unsupported named port %.*s (only sm: is registered)", (int)(length < 32 ? length : 32), name ? name : "");
    Object *o = new_object(OBJECT_SM);
    if (!o) return problem(error, cap, "allocate sm: object failed");
    if (object_handle(s, o, handle, error, cap)) return -1;
    emit(s, "ipc_connect port=sm: handle=0x%x", *handle); return 0;
}
int botw_services_close(botw_services *s, uint32_t id, char *error, size_t cap) {
    Handle *handle = s ? find_handle(s, id) : NULL;
    if (!handle) return problem(error, cap, "CloseHandle invalid or closed IPC handle=0x%x", id);
    Session *session = handle->session; handle->session = NULL; release_session(session);
    emit(s, "ipc_close handle=0x%x", id); return 0;
}
static int checked_span(size_t offset, size_t length, size_t end) { return offset <= end && length <= end - offset; }
static int input_descriptor(botw_services *s, Request *r, uint64_t address, uint64_t length, char *error, size_t cap) {
    if (!length && !address) return 0; /* inactive half of an auto-select buffer */
    if (!length || length > LOG_LIMIT || address > UINT64_MAX - length ||
        !s->memory(s->opaque, address, (size_t)length, 0))
        return problem(error, cap, "IPC input descriptor is not checked guest memory address=0x%llx bytes=%llu", (unsigned long long)address, (unsigned long long)length);
    if (++r->input_count > 1) return problem(error, cap, "IPC multiple nonempty input buffers are unsupported");
    r->input_address = address; r->input_size = (size_t)length; return 0;
}
static int parse_request(botw_services *s, Session *session, const uint8_t *ipc, size_t capacity, Request *r, char *error, size_t cap) {
    uint32_t a = load32(ipc), b = load32(ipc + 4);
    r->type = a & 0xffff;
    if (r->type == 2) {
        if (a != 2 || b) return problem(error, cap, "HIPC Close must not contain descriptors or payload");
        return 0;
    }
    if (r->type != 4 && r->type != 5 && r->type != 6 && r->type != 7)
        return problem(error, cap, "unsupported HIPC type=%u", r->type);
    unsigned statics = (a >> 16) & 15, sends = (a >> 20) & 15;
    unsigned recvs = (a >> 24) & 15, exchanges = a >> 28, recv_mode = (b >> 10) & 15;
    if (recvs || exchanges || recv_mode || (b & 0x7fffc000u))
        return problem(error, cap, "unsupported HIPC receive/exchange descriptors or reserved header bits (word0=0x%x word1=0x%x)", a, b);
    size_t offset = 8;
    if (b >> 31) {
        if (!checked_span(offset, 4, capacity)) return problem(error, cap, "truncated HIPC special header");
        uint32_t special = load32(ipc + offset); offset += 4;
        if (special & ~1u) return problem(error, cap, "IPC input handles/reserved special header unsupported (0x%x)", special);
        r->pid = special & 1;
        if (r->pid) {
            if (!checked_span(offset, 8, capacity)) return problem(error, cap, "truncated HIPC PID");
            offset += 8; /* Kernel supplies host process identity, never trusts placeholder. */
        }
    }
    if (!checked_span(offset, (size_t)statics * 8 + (size_t)sends * 12, capacity))
        return problem(error, cap, "HIPC descriptor list exceeds IPC buffer");
    for (unsigned i = 0; i < statics; i++, offset += 8) {
        uint32_t x = load32(ipc + offset), low = load32(ipc + offset + 4);
        uint64_t address = low | (uint64_t)((x >> 12) & 15) << 32 | (uint64_t)((x >> 6) & 63) << 36;
        if (input_descriptor(s, r, address, x >> 16, error, cap)) return -1;
    }
    for (unsigned i = 0; i < sends; i++, offset += 12) {
        uint32_t size = load32(ipc + offset), low = load32(ipc + offset + 4), high = load32(ipc + offset + 8);
        if ((high & 3) == 2) return problem(error, cap, "HIPC buffer has invalid memory mode=2");
        uint64_t address = low | (uint64_t)(high >> 28) << 32 | (uint64_t)((high >> 2) & 0x3fffff) << 36;
        uint64_t length = size | (uint64_t)((high >> 24) & 15) << 32;
        if (input_descriptor(s, r, address, length, error, cap)) return -1;
    }
    size_t raw_end = offset + (size_t)(b & 0x3ff) * 4;
    if (raw_end > capacity) return problem(error, cap, "HIPC raw data exceeds IPC buffer (end=%zu capacity=%zu)", raw_end, capacity);
    offset = (offset + 15) & ~(size_t)15;
    if (session->domain && (r->type == 4 || r->type == 6)) {
        if (!checked_span(offset, 16, raw_end)) return problem(error, cap, "truncated CMIF domain header");
        unsigned type = ipc[offset], objects = ipc[offset + 1];
        unsigned data_size = ipc[offset + 2] | (unsigned)ipc[offset + 3] << 8;
        r->object_id = load32(ipc + offset + 4); r->domain = 1;
        if (load32(ipc + offset + 8) || objects)
            return problem(error, cap, "CMIF domain input objects/reserved fields unsupported");
        offset += 16;
        if (type == 2) {
            if (data_size || r->input_count || r->pid) return problem(error, cap, "CMIF domain Close has unexpected data");
            r->domain_close = 1; return 0;
        }
        if (type != 1 || data_size < 16 || !checked_span(offset, data_size, raw_end))
            return problem(error, cap, "invalid CMIF domain type=%u data_size=%u", type, data_size);
        raw_end = offset + data_size;
    }
    if (!checked_span(offset, 16, raw_end)) return problem(error, cap, "truncated CMIF input header");
    uint32_t magic = load32(ipc + offset), version = load32(ipc + offset + 4);
    if (magic != SFCI || version > 1 || (version && r->type != 6 && r->type != 7))
        return problem(error, cap, "invalid CMIF header magic=0x%x version=%u", magic, version);
    r->command = load32(ipc + offset + 8); r->token = load32(ipc + offset + 12);
    r->payload = ipc + offset + 16; r->payload_size = raw_end - offset - 16; return 0;
}
static void response(uint8_t *ipc, size_t capacity, const Request *r, const void *data, size_t data_size, uint32_t handle, uint32_t object_id) {
    memset(ipc, 0, capacity);
    size_t raw_start = handle ? 16 : 8, aligned = 16;
    size_t payload = aligned + (r->domain ? 16 : 0);
    size_t end = payload + 16 + data_size + (object_id ? 4 : 0);
    store32(ipc + 4, (uint32_t)((end - raw_start + 3) / 4) | (handle ? 0x80000000u : 0));
    if (handle) { store32(ipc + 8, 1u << 5); store32(ipc + 12, handle); }
    if (r->domain) store32(ipc + aligned, object_id ? 1 : 0);
    store32(ipc + payload, SFCO); store32(ipc + payload + 12, r->token);
    if (data_size) memcpy(ipc + payload + 16, data, data_size);
    if (object_id) store32(ipc + payload + 16 + data_size, object_id);
}
static int return_object(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, int kernel_handle, char *error, size_t cap) {
    uint32_t handle = 0, object_id = 0;
    if (r->domain && !kernel_handle) {
        for (unsigned i = 0; i < DOMAIN_LIMIT; i++) if (!session->objects[i]) { session->objects[i] = o; object_id = i + 1; break; }
        if (!object_id) { release_object(o); return problem(error, cap, "CMIF domain object table exhausted"); }
    } else if (object_handle(s, o, &handle, error, cap)) return -1;
    response(ipc, capacity, r, NULL, 0, handle, object_id); return 0;
}
static int control(botw_services *s, Session *session, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (r->input_count || r->pid) return problem(error, cap, "CMIF control command=%u has unexpected buffers/PID", r->command);
    uint32_t handle = 0, object_id;
    switch (r->command) {
        case 0:
            if (session->domain || !session->root) return problem(error, cap, "CMIF ConvertCurrentObjectToDomain called on domain/empty session");
            session->domain = 1; session->objects[0] = session->root; object_id = 1;
            response(ipc, capacity, r, &object_id, 4, 0, 0); return 0;
        case 1:
            if (!session->domain || r->payload_size < 4) return problem(error, cap, "CMIF CopyFromCurrentDomain requires domain and object ID");
            object_id = load32(r->payload);
            if (!object_id || object_id > DOMAIN_LIMIT || !session->objects[object_id - 1]) return problem(error, cap, "CMIF CopyFromCurrentDomain invalid object=%u", object_id);
            session->objects[object_id - 1]->refs++;
            if (object_handle(s, session->objects[object_id - 1], &handle, error, cap)) return -1;
            response(ipc, capacity, r, NULL, 0, handle, 0); return 0;
        case 2: case 4: /* CloneCurrentObject / CloneCurrentObjectEx(tag). */
            if (r->command == 4 && r->payload_size < 4) return problem(error, cap, "CMIF CloneCurrentObjectEx missing tag");
            if (assign_handle(s, session, &handle, error, cap)) return -1;
            session->refs++; response(ipc, capacity, r, NULL, 0, handle, 0); return 0;
        case 3: {
            /* No server pointer pool is exposed; callers select map-alias input
             * buffers. Incoming explicit pointers remain checked guest memory. */
            uint16_t size = 0; response(ipc, capacity, r, &size, 2, 0, 0); return 0;
        }
        default: return problem(error, cap, "unsupported CMIF control command=%u", r->command);
    }
}
static int read_leb128(const uint8_t *bytes, size_t size, size_t *offset, uint64_t *value) {
    *value = 0;
    for (unsigned shift = 0; shift < 64 && *offset < size; shift += 7) {
        uint8_t byte = bytes[(*offset)++];
        if (shift == 63 && (byte & 0xfe)) return -1;
        *value |= (uint64_t)(byte & 0x7f) << shift;
        if (!(byte & 0x80)) return 0;
    }
    return -1;
}
static void emit_text(botw_services *s, const uint8_t *text, size_t length, unsigned severity) {
    /* Escape control bytes so guest text cannot forge host diagnostic records. */
    char chunk[1024]; size_t used = 0;
    for (size_t i = 0; i < length; i++) {
        uint8_t c = text[i];
        if (used > sizeof chunk - 6) { chunk[used] = 0; emit(s, "guest_log severity=%u text=%s", severity, chunk); used = 0; }
        if (c < 32 || c == 127) { chunk[used++] = '\\'; chunk[used++] = 'x'; const char *hex = "0123456789abcdef"; chunk[used++] = hex[c >> 4]; chunk[used++] = hex[c & 15]; }
        else chunk[used++] = (char)c;
    }
    chunk[used] = 0; emit(s, "guest_log severity=%u text=%s", severity, chunk);
}
static int logger_packet(botw_services *s, const Request *r, char *error, size_t cap) {
    if (r->input_count != 1 || r->input_size < 24) return problem(error, cap, "ILogger.Log requires one checked buffer with 24-byte header");
    const uint8_t *packet = (const uint8_t *)(uintptr_t)r->input_address;
    uint32_t length = load32(packet + 20);
    if (length > r->input_size - 24 || (packet[16] & ~7u) || packet[17] || packet[18] > 4)
        return problem(error, cap, "invalid ILogger packet flags=0x%x severity=%u payload=%u capacity=%zu", packet[16], packet[18], length, r->input_size - 24);
    const uint8_t *chunks = packet + 24; size_t offset = 0;
    /* Validate every chunk before any logging side effect. */
    while (offset < length) {
        uint64_t key, size;
        if (read_leb128(chunks, length, &offset, &key) || read_leb128(chunks, length, &offset, &size) || size > length - offset)
            return problem(error, cap, "invalid ILogger LEB128/chunk bounds at offset=%zu", offset);
        offset += (size_t)size;
    }
    if (!s->log_destination) return 0;
    emit(s, "guest_log_packet pid=%llu tid=%llu flags=0x%x severity=%u verbosity=%u bytes=%u", (unsigned long long)load64(packet), (unsigned long long)load64(packet + 8), packet[16], packet[18], packet[19], length);
    offset = 0;
    while (offset < length) {
        uint64_t key = 0, size = 0;
        (void)read_leb128(chunks, length, &offset, &key); (void)read_leb128(chunks, length, &offset, &size);
        if (key == 2) emit_text(s, chunks + offset, (size_t)size, packet[18]);
        offset += (size_t)size;
    }
    return 0;
}
int botw_services_send(botw_services *s, uint32_t handle_id, void *buffer, size_t capacity, char *error, size_t cap) {
    Handle *handle = s ? find_handle(s, handle_id) : NULL;
    if (!handle) return problem(error, cap, "SendSyncRequest invalid or closed IPC handle=0x%x", handle_id);
    if (!buffer || capacity < 0x40 || capacity > IPC_LIMIT || !s->memory(s->opaque, (uint64_t)(uintptr_t)buffer, capacity, 1))
        return problem(error, cap, "SendSyncRequest IPC buffer is not checked guest writable memory or capacity is outside 0x40..0x100");
    uint8_t *ipc = buffer; Session *session = handle->session; Request r = {0};
    if (parse_request(s, session, ipc, capacity, &r, error, cap)) return -1;
    if (r.type == 2) return botw_services_close(s, handle_id, error, cap);
    if (r.type == 5 || r.type == 7) return control(s, session, ipc, capacity, &r, error, cap);
    Object *o = session->root;
    if (r.domain) {
        if (!r.object_id || r.object_id > DOMAIN_LIMIT || !session->objects[r.object_id - 1])
            return problem(error, cap, "CMIF invalid or closed domain object=%u", r.object_id);
        o = session->objects[r.object_id - 1];
        if (r.domain_close) {
            session->objects[r.object_id - 1] = NULL;
            if (session->root == o) session->root = NULL;
            release_object(o); memset(ipc, 0, capacity); return 0;
        }
    }
    if (!o) return problem(error, cap, "CMIF session has no live root object");
    emit(s, "ipc_request service=%s command=%u handle=0x%x domain=%u object=%u bytes=%zu", kind_name(o->kind), r.command, handle_id, r.domain, r.object_id, r.payload_size);
    if (o->kind == OBJECT_SM) {
        if (r.input_count) return problem(error, cap, "sm: command=%u unexpected input buffer", r.command);
        if (r.command == 0) {
            if (!r.pid || r.payload_size < 8 || o->initialized) return problem(error, cap, "sm: Initialize requires PID descriptor, u64 placeholder and uninitialized session");
            o->initialized = 1; o->pid = 1; response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
        }
        if (r.command == 1) {
            if (!o->initialized || r.payload_size < 8) return problem(error, cap, "sm: GetService requires initialized client and 8-byte service name");
            char name[9]; memcpy(name, r.payload, 8); name[8] = 0;
            if (memcmp(name, "lm\0\0\0\0\0\0", 8)) return problem(error, cap, "unsupported sm: GetService name=%s bytes=%02x%02x%02x%02x%02x%02x%02x%02x", name, r.payload[0], r.payload[1], r.payload[2], r.payload[3], r.payload[4], r.payload[5], r.payload[6], r.payload[7]);
            Object *lm = new_object(OBJECT_LM); if (!lm) return problem(error, cap, "allocate lm object failed");
            return return_object(s, session, lm, ipc, capacity, &r, 1, error, cap);
        }
    } else if (o->kind == OBJECT_LM && r.command == 0) {
        if (r.input_count || !r.pid || r.payload_size < 8) return problem(error, cap, "lm.OpenLogger requires PID descriptor and u64 placeholder");
        Object *logger = new_object(OBJECT_LOGGER); if (!logger) return problem(error, cap, "allocate ILogger failed");
        logger->pid = 1; return return_object(s, session, logger, ipc, capacity, &r, 0, error, cap);
    } else if (o->kind == OBJECT_LOGGER) {
        if (r.command == 0) {
            if (logger_packet(s, &r, error, cap)) return -1;
            response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
        }
        if (r.command == 1) {
            if (r.payload_size < 4 || r.input_count || (load32(r.payload) != 0xffff && (load32(r.payload) & ~7u))) return problem(error, cap, "ILogger.SetDestination requires a valid u32 destination mask");
            s->log_destination = load32(r.payload); emit(s, "guest_log_destination mask=0x%x host_sink=event", s->log_destination);
            response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
        }
    }
    return problem(error, cap, "unsupported IPC service=%s command=%u", kind_name(o->kind), r.command);
}
