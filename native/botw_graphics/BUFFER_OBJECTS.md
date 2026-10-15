# Buffer objects and GPU address lifetime

The complete fixed MIT BotW upstream buffer family is reused through checked
host adapters. `vendor/botw-buffer/SOURCE.json` records the source hash and changes.
The fixed D2R revision has no Buffer object implementation. The newer BotW builder
has a reserved leading qword; the base-52 SDK independently confirms device/pool/
offset/size at +0/+8/+16/+24. Defaults preserve device. The base-52 pool getter
returns a pointer. BuilderGetDevice has no export in that profile and stays
unsupported.

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
