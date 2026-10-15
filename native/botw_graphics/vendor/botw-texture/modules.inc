/* Single translation-unit port of the fixed upstream modules.
 * Private names are isolated per module; standalone .c builds remain supported.
 * Include once from graphics.m; do not separately link these .c files as well. */
#pragma once

#define mip nvn_texwrite_private_mip
#define min_u32 nvn_texwrite_private_min_u32
#define fail nvn_texwrite_private_fail
#define descriptor_ok nvn_texwrite_private_descriptor_ok
#define level_bytes nvn_texwrite_private_level_bytes
#define copy_runs nvn_texwrite_private_copy_runs
#include "nvn_texwrite.c"
#undef copy_runs
#undef level_bytes
#undef descriptor_ok
#undef fail
#undef min_u32
#undef mip


#define own_t nvn_texown_private_own_t
#define g_own nvn_texown_private_g_own
#define g_n nvn_texown_private_g_n
#define g_cap nvn_texown_private_g_cap
#define g_mu nvn_texown_private_g_mu
#define range nvn_texown_private_range
#define push nvn_texown_private_push
#define overlap nvn_texown_private_overlap
#define fill_hit nvn_texown_private_fill_hit
#include "nvn_texown.c"
#undef fill_hit
#undef overlap
#undef push
#undef range
#undef g_mu
#undef g_cap
#undef g_n
#undef g_own
#undef own_t

#define fail nvn_texflush_private_fail
#define rotl nvn_texflush_private_rotl
#define lane nvn_texflush_private_lane
#define layer_cert nvn_texflush_private_layer_cert
#define rec_t nvn_texflush_private_rec_t
#define g_recs nvn_texflush_private_g_recs
#define g_mu nvn_texflush_private_g_mu
#define span_ok nvn_texflush_private_span_ok
#define same_record nvn_texflush_private_same_record
#define new_record nvn_texflush_private_new_record
#define layer_ptr nvn_texflush_private_layer_ptr
#include "nvn_texflush.c"
#undef layer_ptr
#undef new_record
#undef same_record
#undef span_ok
#undef g_mu
#undef g_recs
#undef rec_t
#undef layer_cert
#undef lane
#undef rotl
#undef fail

#define page_chunk nvn_dirty_private_page_chunk
#define page_t nvn_dirty_private_page_t
#define range_t nvn_dirty_private_range_t
#define g_r nvn_dirty_private_g_r
#define g_nr nvn_dirty_private_g_nr
#define g_readers nvn_dirty_private_g_readers
#define g_epoch nvn_dirty_private_g_epoch
#define g_pg nvn_dirty_private_g_pg
#define g_shift nvn_dirty_private_g_shift
#define g_tb nvn_dirty_private_g_tb
#define g_once nvn_dirty_private_g_once
#define g_slot_mu nvn_dirty_private_g_slot_mu
#define g_now nvn_dirty_private_g_now
#define g_faults nvn_dirty_private_g_faults
#define g_preps nvn_dirty_private_g_preps
#define g_adds_total nvn_dirty_private_g_adds_total
#define g_mp_calls nvn_dirty_private_g_mp_calls
#define g_mp_pages nvn_dirty_private_g_mp_pages
#define g_mp_ticks nvn_dirty_private_g_mp_ticks
#define g_busy_timeouts nvn_dirty_private_g_busy_timeouts
#define g_live nvn_dirty_private_g_live
#define g_live_peak nvn_dirty_private_g_live_peak
#define g_ready nvn_dirty_private_g_ready
#define g_registration nvn_dirty_private_g_registration
#define g_pages_live nvn_dirty_private_g_pages_live
#define g_pages_shared nvn_dirty_private_g_pages_shared
#define error_out nvn_dirty_private_error_out
#define actual_protection nvn_dirty_private_actual_protection
#define currently_writable nvn_dirty_private_currently_writable
#define currently_owned_readonly nvn_dirty_private_currently_owned_readonly
#define read_enter nvn_dirty_private_read_enter
#define read_leave nvn_dirty_private_read_leave
#define drain_readers nvn_dirty_private_drain_readers
#define covers nvn_dirty_private_covers
#define find_range nvn_dirty_private_find_range
#define find_cover nvn_dirty_private_find_cover
#define page_index nvn_dirty_private_page_index
#define mp nvn_dirty_private_mp
#define busy_wait nvn_dirty_private_busy_wait
#define unprotect_page nvn_dirty_private_unprotect_page
#define unprotect_one nvn_dirty_private_unprotect_one
#define unprotect_range nvn_dirty_private_unprotect_range
#define dirty_init nvn_dirty_private_dirty_init
#define attach_pages nvn_dirty_private_attach_pages
#define detach_pages nvn_dirty_private_detach_pages
#define PG_NONE nvn_dirty_private_PG_NONE
#define PG_PROT nvn_dirty_private_PG_PROT
#define PG_BUSY nvn_dirty_private_PG_BUSY
#define BUSY_WAIT_MS nvn_dirty_private_BUSY_WAIT_MS
#include "nvn_dirty.c"
#undef BUSY_WAIT_MS
#undef PG_BUSY
#undef PG_PROT
#undef PG_NONE
#undef detach_pages
#undef attach_pages
#undef dirty_init
#undef unprotect_range
#undef unprotect_one
#undef unprotect_page
#undef busy_wait
#undef mp
#undef page_index
#undef find_cover
#undef find_range
#undef covers
#undef drain_readers
#undef read_leave
#undef read_enter
#undef currently_owned_readonly
#undef currently_writable
#undef actual_protection
#undef error_out
#undef g_pages_shared
#undef g_pages_live
#undef g_registration
#undef g_ready
#undef g_live_peak
#undef g_live
#undef g_busy_timeouts
#undef g_mp_ticks
#undef g_mp_pages
#undef g_mp_calls
#undef g_adds_total
#undef g_preps
#undef g_faults
#undef g_now
#undef g_slot_mu
#undef g_once
#undef g_tb
#undef g_shift
#undef g_pg
#undef g_epoch
#undef g_readers
#undef g_nr
#undef g_r
#undef range_t
#undef page_t
#undef page_chunk
