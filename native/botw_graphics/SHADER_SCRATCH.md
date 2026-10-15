# Metal-owned shader scratch

This backend reports external guest scratch minimum/recommendation **0**. Metal
provides per-invocation thread storage and compiler spills. This is independent
of `MTLStorageModePrivate` and makes no claim about Tegra SM/TPC counts. The NVN
compatibility alignment/granularity remain 4096/131072 bytes.

A zero-capacity memory pool is a real object with device, identity, generation,
flags and the original storage token. It has no MTLBuffer. Initialization never
dereferences that token; Map/GetBufferAddress preserve the token. Empty ranges
are valid; nonempty texture, descriptor, command memory and address bindings
cannot obtain backing from it. Finalize waits for submitted GPU work.

SetShaderScratchMemory uses the bounded command/control recorder. Its immutable
record captures the device, retained pool identity/generation and checked range.
Queue preflight verifies these before writing queue replay storage. Replay keeps
per-queue state; command buffers retain every referenced pool until Metal calls
completion, including error completion. Finalize/reinit cannot redirect an old
record to a new pool at the same guest address. NULL unbind requires (0,0).

The initial shader proof profile accepts graphics SPH low/high/CRS fields all
zero and actual SPIR-V 1.3 or 1.4 with no Private variables. Other versions are
rejected until their instructions and toolchain have been reviewed. The bounded metadata walk
checks entry stage, memory model, instruction spans and complete functions. This
also admits the operand-free fragment-only OpDemoteToHelperInvocationEXT, which
the original fixed MSL compiler lowers to discard_fragment(). Other extension
instructions remain rejected. This
is not a full SPIR-V validator or a substitute for the pinned translator, resource
environment and MSL validation. Zero declared size plus Private storage is rejected
conservatively, including Ryujinx's silent 128-byte LDL/STL fallback. Nonzero local
memory still requires complete access-range/initialization and call-stack proofs.
Compute, geometry and tessellation are not admitted by this policy.

The field arithmetic follows the MIT pinned shader toolchain's ShaderHeader.cs,
Translator.cs and ResourceManager.cs under ../botw_resources/shader_toolchain
(upstream 5f673ae2ea4f90ad46403aa6fbe13537fb557271). Its vendor notice is preserved
there. No compiler fallback is treated as an observed game property.

Private bundles may add exact code-SHA keyed SPIR-V files via
`--scratch-proof-directory`. The loader verifies bounded relative assets and
hashes, recomputes the proof against the original code, then attaches it to the
exact registered identity. The offline caller must use the SPIR-V that produced
that identity's MSL through the pinned SPIRV-Cross command and the bundle stager's
documented specializations; this metadata check cannot prove compiler equivalence
between unrelated SPIR-V and MSL. Legacy registration does not assert this fact: a draw
after a scratch binding fails unless all active stages have validated evidence.
All game input, SPIR-V and derived MSL remain local ignored assets.
