#pragma once
#include <stdint.h>

/* Normal host-owned NVN metadata only. No descriptor/guest byte sampling.
 * Origin/depth use the actual NVN enums: lower-left/minus-w=0, upper-left/zero=1.
 * descriptor_epoch is captured under the existing registry mutex. */
typedef struct {
    uint32_t device_known, window_origin_mode, depth_mode;
    uint32_t color0_known, color0_present, color0_format;
    uint64_t descriptor_epoch;
} botw_conditional_draw_state;

#ifdef __cplusplus
extern "C" {
#endif
/* Same lock surrounds BOTH normal device setters and a complete mode snapshot.
 * Lock order when paired with the NVN registry: modes, then registry. */
void botw_conditional_modes_lock(void);
void botw_conditional_modes_unlock(void);
int botw_conditional_state_equal(const botw_conditional_draw_state * _Nullable, const botw_conditional_draw_state * _Nullable);
#ifdef __cplusplus
}
#endif

#ifdef __OBJC__
#import "botw_shader_manifest.h"
#include "botw_shader.h"
NS_ASSUME_NONNULL_BEGIN
@interface WholeShaderConditionalArtifact : BotwShaderArtifact
@property(nonatomic,readonly,copy) NSString *variantSHA256;
@property(nonatomic,readonly,copy) NSString *cacheIdentity;
@property(nonatomic,readonly,copy) NSDictionary *contract;
@end
@interface WholeShaderConditionalManifest : BotwShaderManifest
/* This checks the independent conditional package and owns checked bytes.
 * Detached admission/proof authority is validated by the bundle publication
 * entry, before this path is configured. It never promotes a contract to facts
 * or claims device compilation happened. */
+ (nullable instancetype)conditionalManifestAtAbsolutePath:(NSString *)path error:(NSError * _Nullable * _Nullable)error;
/* No match, unknown required metadata and multiple matches are real failures.
 * The full immutable capture identity is checked before selecting or caching. */
- (nullable WholeShaderConditionalArtifact *)artifactForInfo:(const botw_shader_info *)info
    state:(const botw_conditional_draw_state *)state error:(NSError * _Nullable * _Nullable)error;
@end
NS_ASSUME_NONNULL_END
#endif
