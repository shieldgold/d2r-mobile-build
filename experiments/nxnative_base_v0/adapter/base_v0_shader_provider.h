/* Base-1.0-only explicit source route. No pool registry or legacy fallback. */
#pragma once
#include <stdint.h>
#include "whole_shader_conditional.h"
#include "botw_shader.h"
#ifdef __cplusplus
extern "C" {
#endif
int nx_base_shader_enabled(void);
/* Called while the original normal shader operands are valid. Only an exact
 * full native digest and lengths can name a previously admitted capture. */
int nx_base_shader_info(const uint8_t native_sha256[_Nonnull 32],uint32_t control_size,
                        uint32_t code_size,botw_shader_info * _Nonnull out);
/* Existing program-owned table only; implemented by the thin native patch. */
int nx_base_program_info(const void * _Nonnull program,unsigned index,botw_shader_info * _Nonnull out);
const void * _Nullable nx_base_program_device(const void * _Nonnull program);
#ifdef __cplusplus
}
#endif
#ifdef __OBJC__
#import "botw_shader_runtime.h"
NS_ASSUME_NONNULL_BEGIN
@interface NxBaseShaderProvider : NSObject
@property(nonatomic,readonly) WholeShaderConditionalManifest *manifest;
+ (nullable instancetype)providerAtBundle:(NSString *)bundle error:(NSError * _Nullable * _Nullable)error;
- (BOOL)infoForNativeDigest:(const uint8_t *)digest controlSize:(uint32_t)control
                  codeSize:(uint32_t)code output:(botw_shader_info *)output;
@end
/* One immutable signed-bundle latch; missing or rejected is terminal for this
 * route, never permission to use the native 1.9 shader_map/builtin libraries. */
NxBaseShaderProvider *_Nullable nx_base_shader_provider(void);
NS_ASSUME_NONNULL_END
#endif
