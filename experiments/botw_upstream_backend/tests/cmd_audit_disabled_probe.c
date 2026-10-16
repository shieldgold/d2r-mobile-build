/* Self-owned CPU records only. No renderer, guest, Metal or device linked. */
#include "../upstream/dm/gfx/nvn/nvn_cmd_audit.h"
#include "../upstream/dm/gfx/nvn/nvn_obj.h"
#include "../adapter/host_adapter.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned dynamic_lookups;
void *botw_test_dlsym(void *handle, const char *name) {
    (void)handle; (void)name; dynamic_lookups++; return NULL;
}
int main(void) {
    setenv("DM_NVN_CMD_AUDIT", "1", 1);
    setenv("DM_NVN_CMD_AUDIT_TRACE", "100", 1);
    int enabled=cmda_on();
    printf("audit_enabled=%d dynamic_lookups=%u\n",enabled,dynamic_lookups);
    /* The raw upstream module returns91 here before any bad-pointer checks;
     * no diagnostic read or resolved thread-dump callback is ever executed. */
    if(enabled||dynamic_lookups)return 91;
    assert(botw_backend_compat_diagnostic_enabled()==0);
    /* Every audit API checks the disabled gate before its pointer arguments. */
    const uint8_t *bad=(const uint8_t*)1;
    cmda_cb_init(bad); cmda_cb_final(bad);
    cmda_add(bad,bad,bad+1,0,bad,bad);
    cmda_query(bad,0,1,bad); cmda_callback(bad,bad,bad+1,1);
    cmda_begin(bad,bad); cmda_end(bad,bad); cmda_call_rec(bad,bad,1);
    cmda_submit(1,(const uint64_t*)1); cmda_exec(1,bad,1);
    cmda_bad(bad,0,0,"owned probe",(const uint8_t *const*)1,1);
    assert(dynamic_lookups==0);
    /* Mandatory decoding helpers remain functional; diagnostic shutdown does
     * not turn valid command validation or safe reads into no-op success. */
    op_hdr begin={OP_BEGIN,8}, copied={0};
    assert(cmd_hdr_defect(begin.op,begin.size)==NULL);
    assert(cmd_hdr_defect(OP_COUNT_,8)!=NULL);
    assert(cmd_rec_defect((const uint8_t*)&begin)==NULL);
    assert(cmd_safe_read(&begin,sizeof begin,&copied)&&!memcmp(&begin,&copied,sizeof begin));
    uint32_t op=0,size=0;
    assert(cmd_entry_defect((const uint8_t*)&begin,&op,&size)==NULL && op==OP_BEGIN && size==8);
    assert(dynamic_lookups==0);
    return 0;
}
