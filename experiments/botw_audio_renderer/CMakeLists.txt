cmake_minimum_required(VERSION 3.24)
project(botw_audio_renderer_native_platform LANGUAGES CXX)

# Phase 1 is the real host platform contract. The vendored emulator CMakeLists
# is provenance only; never add_subdirectory(vendor/eden/src/audio_core).
find_package(Threads REQUIRED)
add_library(botw_audio_renderer_platform STATIC platform/platform.cpp)
target_include_directories(botw_audio_renderer_platform PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/platform")
target_compile_features(botw_audio_renderer_platform PUBLIC cxx_std_23)
target_link_libraries(botw_audio_renderer_platform PUBLIC Threads::Threads)
set_target_properties(botw_audio_renderer_platform PROPERTIES CXX_EXTENSIONS OFF)

# Production PCM is supplied by the existing real host target, not a dummy
# implementation or an embedded copy. Missing integration is a configure error.
option(BOTW_AUDIO_RENDERER_ENABLE_PCM "Build the original audible host PCM adapter" OFF)
set(BOTW_AUDIO_RENDERER_PCM_TARGET "" CACHE STRING "Existing real botw_pcm target")
set(BOTW_AUDIO_RENDERER_PCM_INCLUDE_DIR "" CACHE PATH "Directory containing original botw_pcm.h")
if(BOTW_AUDIO_RENDERER_ENABLE_PCM)
    if(NOT BOTW_AUDIO_RENDERER_PCM_TARGET OR NOT TARGET "${BOTW_AUDIO_RENDERER_PCM_TARGET}")
        message(FATAL_ERROR "PCM requires an existing real BOTW_AUDIO_RENDERER_PCM_TARGET")
    endif()
    if(NOT EXISTS "${BOTW_AUDIO_RENDERER_PCM_INCLUDE_DIR}/botw_pcm.h")
        message(FATAL_ERROR "PCM requires the actual host botw_pcm.h")
    endif()
    add_library(botw_audio_renderer_pcm STATIC platform/pcm_output.cpp)
    target_compile_features(botw_audio_renderer_pcm PUBLIC cxx_std_23)
    target_include_directories(botw_audio_renderer_pcm PRIVATE "${BOTW_AUDIO_RENDERER_PCM_INCLUDE_DIR}")
    target_link_libraries(botw_audio_renderer_pcm PUBLIC botw_audio_renderer_platform
                          "${BOTW_AUDIO_RENDERER_PCM_TARGET}")
endif()

option(BOTW_AUDIO_RENDERER_BUILD_OWNED_TESTS "Build CPU-only owned platform fixture" OFF)
if(BOTW_AUDIO_RENDERER_BUILD_OWNED_TESTS)
    enable_testing()
    add_executable(botw_audio_platform_probe tests/platform_probe.cpp)
    target_link_libraries(botw_audio_platform_probe PRIVATE botw_audio_renderer_platform)
    foreach(behavior memory lifetime pool event clock)
        add_test(NAME "owned_${behavior}" COMMAND botw_audio_platform_probe "${behavior}")
    endforeach()
endif()
