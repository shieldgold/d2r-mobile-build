/* SPDX-License-Identifier: MIT */
#ifndef BOTW_WHOLE_ADDRESS_SPACE_H
#define BOTW_WHOLE_ADDRESS_SPACE_H
#include <stddef.h>
typedef struct {void *base;size_t size;} botw_whole_address_space;
/* One retained PROT_NONE reservation. Only physical_ios==1 enables the fixed
 * 5/4/3/2-GiB sequence. Failure leaves out unchanged and returns actual errno. */
int botw_whole_address_space_reserve(botw_whole_address_space *out,int physical_ios,int *last_errno);
#endif
