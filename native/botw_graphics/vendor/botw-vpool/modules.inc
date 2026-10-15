/* Single-TU namespace for the complete vendored algorithm. */
#define phys_t bw_vpool_private_phys_t
#define span_t bw_vpool_private_span_t
#define virt_t bw_vpool_private_virt_t
#define vp_alloc_after bw_vpool_private_vp_alloc_after
#define vp_reserve_after bw_vpool_private_vp_reserve_after
#define vp_alloc_calls bw_vpool_private_vp_alloc_calls
#define vp_alloc_live bw_vpool_private_vp_alloc_live
#define vp_reserve_calls bw_vpool_private_vp_reserve_calls
#define vp_reserve_live bw_vpool_private_vp_reserve_live
#define vp_reserved bw_vpool_private_vp_reserved
#define vp_reserve_floor bw_vpool_private_vp_reserve_floor
#define vp_test_fail bw_vpool_private_vp_test_fail
#define g_phys bw_vpool_private_g_phys
#define g_nphys bw_vpool_private_g_nphys
#define g_cphys bw_vpool_private_g_cphys
#define g_virt bw_vpool_private_g_virt
#define g_nvirt bw_vpool_private_g_nvirt
#define g_cvirt bw_vpool_private_g_cvirt
#define g_phys_ids bw_vpool_private_g_phys_ids
#define g_above_limit bw_vpool_private_g_above_limit
#define g_gen bw_vpool_private_g_gen
#define g_lk bw_vpool_private_g_lk
#define g_cv bw_vpool_private_g_cv
#define g_readers bw_vpool_private_g_readers
#define g_writer bw_vpool_private_g_writer
#define g_wwait bw_vpool_private_g_wwait
#define g_rwait bw_vpool_private_g_rwait
#define t_depth bw_vpool_private_t_depth
#define rd_lock bw_vpool_private_rd_lock
#define rd_unlock bw_vpool_private_rd_unlock
#define wr_lock bw_vpool_private_wr_lock
#define wr_unlock bw_vpool_private_wr_unlock
#define phys_find bw_vpool_private_phys_find
#define virt_find bw_vpool_private_virt_find
#define virt_floor bw_vpool_private_virt_floor
#define virt_overlap bw_vpool_private_virt_overlap
#define span_lower bw_vpool_private_span_lower
#define range_ok bw_vpool_private_range_ok
#define grow bw_vpool_private_grow
#define errf bw_vpool_private_errf
#define reserve_at bw_vpool_private_reserve_at
#define release_reservation bw_vpool_private_release_reservation
#define reserve bw_vpool_private_reserve
#define register_virtual bw_vpool_private_register_virtual
#define punch bw_vpool_private_punch
#define coalesce bw_vpool_private_coalesce
#define check_req bw_vpool_private_check_req
#include "nvn_vpool.c"
#undef check_req
#undef coalesce
#undef punch
#undef register_virtual
#undef reserve
#undef release_reservation
#undef reserve_at
#undef errf
#undef grow
#undef range_ok
#undef span_lower
#undef virt_overlap
#undef virt_floor
#undef virt_find
#undef phys_find
#undef wr_unlock
#undef wr_lock
#undef rd_unlock
#undef rd_lock
#undef t_depth
#undef g_rwait
#undef g_wwait
#undef g_writer
#undef g_readers
#undef g_cv
#undef g_lk
#undef g_gen
#undef g_above_limit
#undef g_phys_ids
#undef g_cvirt
#undef g_nvirt
#undef g_virt
#undef g_cphys
#undef g_nphys
#undef g_phys
#undef vp_test_fail
#undef vp_reserve_floor
#undef vp_reserved
#undef vp_reserve_live
#undef vp_reserve_calls
#undef vp_alloc_live
#undef vp_alloc_calls
#undef vp_reserve_after
#undef vp_alloc_after
#undef virt_t
#undef span_t
#undef phys_t
#undef vp_malloc
#undef vp_calloc
#undef vp_realloc
#undef vp_free
