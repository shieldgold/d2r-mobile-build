/* Yaz0 decoder used by botw_shader_extract.py through ctypes.
 *
 * Returns the number of bytes written, or a negative error:
 *   -1 malformed header, -2 output too small, -3 truncated input,
 *   -4 back-reference before the start of the output.
 * The decoded size is the big-endian u32 at offset 4; the caller sizes the
 * output buffer from it and treats any short result as an error. */
#include <stddef.h>
#include <stdint.h>

long botw_yaz0_decode(const uint8_t *src, size_t src_size, uint8_t *dst, size_t dst_size) {
    if (src_size < 16 || src[0] != 'Y' || src[1] != 'a' || src[2] != 'z' || src[3] != '0') return -1;
    size_t want = (size_t)src[4] << 24 | (size_t)src[5] << 16 | (size_t)src[6] << 8 | src[7];
    if (want > dst_size) return -2;
    size_t in = 16, out = 0;
    while (out < want) {
        if (in >= src_size) return -3;
        uint8_t flags = src[in++];
        for (int bit = 7; bit >= 0 && out < want; --bit) {
            if (flags >> bit & 1) {
                if (in >= src_size) return -3;
                dst[out++] = src[in++];
                continue;
            }
            if (in + 2 > src_size) return -3;
            uint8_t b1 = src[in], b2 = src[in + 1];
            in += 2;
            size_t dist = ((size_t)(b1 & 0xf) << 8 | b2) + 1, len = b1 >> 4;
            if (len == 0) {
                if (in >= src_size) return -3;
                len = (size_t)src[in++] + 0x12;
            } else {
                len += 2;
            }
            if (dist > out) return -4;
            if (len > want - out) len = want - out;
            /* Byte-by-byte copy: overlapping references repeat earlier output. */
            for (size_t i = 0; i < len; ++i, ++out) dst[out] = dst[out - dist];
        }
    }
    return (long)out;
}
