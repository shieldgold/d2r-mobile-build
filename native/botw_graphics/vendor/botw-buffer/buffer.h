/* MIT upstream buffer object model; base-52 builder layout adaptation.
 * SOURCE.json records the fixed source and differences. */
typedef struct { uint32_t magic; unsigned flags; unsigned char *mem; size_t size; } bwu_pool;
struct bwu_builder { NVNdevice *device; bwu_pool *pool; ptrdiff_t offset; size_t size; };
struct bwu_buffer { uint32_t magic,reserved; NVNdevice *device; bwu_pool *pool; ptrdiff_t offset; size_t size; uint64_t serial; };
_Static_assert(sizeof(struct bwu_builder)==0x20,"base-52 buffer builder fields");
_Static_assert(sizeof(struct bwu_buffer)==sizeof(NVNbuffer),"buffer object ABI");
