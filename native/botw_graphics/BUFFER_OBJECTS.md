# Buffer objects and GPU address lifetime

The complete fixed MIT BotW upstream buffer family is reused through checked
host adapters. `vendor/botw-buffer/SOURCE.json` records the source hash and changes.
The fixed D2R revision has no Buffer object implementation. The newer BotW builder
has a reserved leading qword; the base-52 SDK independently confirms device/pool/
offset/size at +0/+8/+16/+24. Defaults preserve device. The base-52 pool getter
returns a pointer. BuilderGetDevice has no export in that profile and stays
unsupported.

Builder setters preserve the original field-write footprint: SetDevice writes
only +0..7; Defaults and SetStorage write +8..31 and preserve device and opaque
tail bytes. SetDevice stores its pointer without consuming a provider; Initialize
still validates the consumed device, pool, range and backing. The complete vendor
setter produces independent host bytes, followed by a protected field write.

The metadata writer separates a primary storage claim from its retained value.
Its initial admitted overlap is an owned, moved 192-byte BWTexture primary; other
classes, selected/unmoved values, unknown identities and exhausted epochs remain
rejected. It checks actual output bytes, queue/recording/callback borrows and all
pending texture-transfer source/destination ranges. Fallible claim-clone work
precedes dirty preparation and output; successful output publishes one claim
snapshot and advances the retired claim epochs. It does not finalize textures,
delete values or release descriptor, recorded, mapping or authority references.
An unchanged opaque token remains usable as a value alias, without primary move
ownership. This does not establish the validity of overwritten original SDK POD
bytes. Preparation may conservatively change dirty state on failure; concurrent
memory changes do not gain a byte-transaction or rollback guarantee.

A host buffer is a retained, bounded slice of a live pool. Map respects CPU access;
GetAddress returns the existing pool CPU alias, which the Metal backend resolves
to a buffer plus offset. No allocation is invented for this slice. Empty slices
are valid, including the one-past-end address; they cannot back nonempty GPU work.
Flush and invalidate check slice and pool ranges, CPU source/read or destination/
write access, and forward to the actual coherent/copy-backed pool implementation.
Labels are bounded host metadata. Base-52 debug-disabled devices return debug ID
zero; debug-enabled devices use stable unique host IDs.

Finalize releases the slice object. It does not invalidate a still-live pool's
numeric address. Every vertex, uniform, storage and GPU-copy command snapshots the
pool identity, generation, offset, device and byte range when recorded. Queue
preflight rejects stale or cross-device consumed references before changing its CPU arena.
Replay and the real Metal command buffer retain referenced resources until GPU
completion. Pool finalize drains all submitted queues before guest no-copy memory
may be released; recreating a pool at the same address cannot redirect old handles.
The copied memory-cache path verifies CPU permissions before copying any bytes.

Physical/virtual pool support follows the contiguous coherent ranges in
[MEMORY_POOLS.md](MEMORY_POOLS.md); direct physical GPU addresses, GPU-inaccessible
nonempty address use, unsupported shader stages and unsupported draw/copy operations remain explicit failures. This family
does not claim texture-buffer views, indexed/indirect draws or compute support.
