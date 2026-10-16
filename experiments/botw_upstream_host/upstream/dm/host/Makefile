DM_ROOT ?= $(HOME)/botw-work
MACOS_MIN ?= 26.0
CFLAGS ?= -O1 -g -Wall -Wno-unused-function -mmacosx-version-min=$(MACOS_MIN)

# BotW host core; graphics and the optional Mac window are linked below.
HOST_CORE=\
	crash.c \
	dm_title.c \
	entry.S \
	hle.c \
	hle_count.c \
	hle_os.c \
	hle_sdkinit.c \
	hle_svc.c \
	hle_table.c \
	hle_thread.c \
	loader.c \
	main.c \
	thread.c
HOST_CORE_H=dm.h dm_title.h front.h hle.h relr.h hle_thread_core.h
SRC=$(HOST_CORE) botw_diag.c botw_account.c botw_audio_renderer.c botw_audio_device.c botw_nfp.c botw_fs.c botw_oe.c botw_pcm.c botw_prepo.c botw_time.c botw_graphics.c botw_front.m botw_hid.c botw_keys.c botw_input.m title_botw.c
GFX=$(sort $(wildcard ../gfx/nvn/*.c ../gfx/nvn/*.m ../gfx/nvn/*.S ../gfx/tex/*.c ../gfx/tex/*.m))
GFX_H=$(sort $(wildcard ../gfx/nvn/*.h ../gfx/nvn/include/*.h ../gfx/tex/*.h))
FRAMEWORKS=-framework Cocoa -framework QuartzCore -framework Metal -framework CoreVideo -framework ImageIO -framework UniformTypeIdentifiers -framework CoreGraphics -framework GameController -framework AudioToolbox

# Override BOTW_OUT to keep the executable in the external workspace.
BOTW_OUT ?= dmhost
.DEFAULT_GOAL := botw
.PHONY: botw print-macos-min
botw: $(BOTW_OUT)
print-macos-min:
	@echo $(MACOS_MIN)
ifneq ($(BOTW_OUT),dmhost)
.PHONY: dmhost
dmhost: $(BOTW_OUT)
endif
$(BOTW_OUT): $(SRC) $(GFX) $(HOST_CORE_H) $(GFX_H) botw_front.h botw_hid.h botw_account.h botw_pcm.h botw_audio_renderer.h Makefile
	@mkdir -p $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. -I../gfx/nvn/include -fobjc-arc -o $@ $(SRC) $(GFX) $(FRAMEWORKS) $(LDFLAGS)
