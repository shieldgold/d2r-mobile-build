cmake_minimum_required(VERSION 3.24)
project(botw_audio_renderer_engine LANGUAGES C CXX OBJC)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
find_package(Threads REQUIRED)
option(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS "Sanitize owned CPU module fixtures" OFF)
if(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS)
    if(NOT BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE)
        message(FATAL_ERROR "Native sanitizer mode is only for owned CPU fixtures")
    endif()
    add_compile_options(-fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
    add_link_options(-fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
endif()
get_filename_component(BOTW_AUDIO_COMPONENT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
get_filename_component(BOTW_WHOLE_COMPONENT "${BOTW_AUDIO_COMPONENT}/../botw_upstream_host" ABSOLUTE)

execute_process(COMMAND "${Python3_EXECUTABLE}" "${BOTW_AUDIO_COMPONENT}/tools/verify_native.py"
                RESULT_VARIABLE native_identity)
if(NOT native_identity EQUAL 0)
    message(FATAL_ERROR "Native integration or real PCM source/patch identity changed")
endif()
set(native_patches pool-memory.patch native-host.patch native-pcm-sink.patch)
file(SHA256 "${BOTW_AUDIO_COMPONENT}/SOURCE.lock.json" source_signature)
file(SHA256 "${BOTW_AUDIO_COMPONENT}/DEPENDENCIES.lock.json" dependency_signature)
string(APPEND source_signature "${dependency_signature}")
foreach(patch IN LISTS native_patches)
    file(SHA256 "${BOTW_AUDIO_COMPONENT}/patches/${patch}" patch_signature)
    string(APPEND source_signature "${patch_signature}")
endforeach()
string(SHA256 source_signature "${source_signature}")
set(eden "${CMAKE_CURRENT_BINARY_DIR}/eden-${source_signature}")
set(prepare_command "${Python3_EXECUTABLE}" "${BOTW_AUDIO_COMPONENT}/tools/prepare_eden.py" "${eden}" --reuse)
foreach(patch IN LISTS native_patches)
    list(APPEND prepare_command --patch "${patch}")
endforeach()
execute_process(COMMAND ${prepare_command} RESULT_VARIABLE prepared)
if(NOT prepared EQUAL 0)
    message(FATAL_ERROR "Original Eden verification/private native patch preparation failed")
endif()

if(NOT TARGET botw_audio_renderer_platform)
    add_library(botw_audio_renderer_platform STATIC "${BOTW_AUDIO_COMPONENT}/platform/platform.cpp")
    target_include_directories(botw_audio_renderer_platform PUBLIC "${BOTW_AUDIO_COMPONENT}/platform")
    target_compile_features(botw_audio_renderer_platform PUBLIC cxx_std_23)
    target_link_libraries(botw_audio_renderer_platform PUBLIC Threads::Threads)
endif()

# Tests use the exact original PCM/ring/callback with its real platform patches
# and owned AU seam. Production must supply the actual strong PCM target.
option(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE "Build owned CPU Update fixture; no device/guest" OFF)
set(BOTW_AUDIO_RENDERER_NATIVE_PCM_TARGET "" CACHE STRING "Existing real PCM target including observed-write API")
set(BOTW_AUDIO_RENDERER_NATIVE_PCM_INCLUDE "" CACHE PATH "Actual patched botw_pcm.h directory")
if(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE)
    if(NOT APPLE OR CMAKE_SYSTEM_NAME STREQUAL "iOS")
        message(FATAL_ERROR "Owned AU fixture requires the actual macOS SDK; it is never installed")
    endif()
    set(pcm_root "${CMAKE_CURRENT_BINARY_DIR}/owned-pcm")
    file(MAKE_DIRECTORY "${pcm_root}/dm/host")
    foreach(name botw_pcm.c botw_pcm.h)
        configure_file("${BOTW_WHOLE_COMPONENT}/upstream/dm/host/${name}" "${pcm_root}/dm/host/${name}" COPYONLY)
    endforeach()
    foreach(patch ios-audio-output.patch audio-write-observed.patch)
        execute_process(COMMAND patch --fuzz=0 -p1 -i "${BOTW_WHOLE_COMPONENT}/patches/${patch}"
                        WORKING_DIRECTORY "${pcm_root}" RESULT_VARIABLE pcm_prepared)
        if(NOT pcm_prepared EQUAL 0)
            message(FATAL_ERROR "Real original PCM patch failed")
        endif()
    endforeach()
    add_library(botw_renderer_owned_pcm STATIC "${pcm_root}/dm/host/botw_pcm.c"
                "${BOTW_WHOLE_COMPONENT}/adapter/whole_audio_platform.m"
                "${BOTW_AUDIO_COMPONENT}/tests/renderer_pcm_mock.c")
    target_compile_definitions(botw_renderer_owned_pcm PRIVATE BOTW_PCM_MOCK_AU=1)
    target_include_directories(botw_renderer_owned_pcm PUBLIC "${pcm_root}/dm/host"
                                "${BOTW_WHOLE_COMPONENT}/adapter")
    target_link_libraries(botw_renderer_owned_pcm PUBLIC "-framework AudioToolbox" Threads::Threads)
    set(BOTW_AUDIO_RENDERER_NATIVE_PCM_TARGET botw_renderer_owned_pcm)
    set(BOTW_AUDIO_RENDERER_NATIVE_PCM_INCLUDE "${pcm_root}/dm/host")
endif()
if(NOT BOTW_AUDIO_RENDERER_NATIVE_PCM_TARGET OR NOT TARGET "${BOTW_AUDIO_RENDERER_NATIVE_PCM_TARGET}")
    message(FATAL_ERROR "Native renderer requires a real PCM target with observed-write support")
endif()
if(NOT EXISTS "${BOTW_AUDIO_RENDERER_NATIVE_PCM_INCLUDE}/botw_pcm.h")
    message(FATAL_ERROR "Native renderer requires the actual patched botw_pcm.h")
endif()

# Full renderer and ADSP algorithm closure, not a replacement mixer. No emulator
# core, AudioIn/Out/Opus app, SDL/Cubeb backend, CPU execution engine, or runtime JIT.
file(GLOB_RECURSE renderer_sources CONFIGURE_DEPENDS "${eden}/src/audio_core/renderer/*.cpp")
file(GLOB adsp_sources CONFIGURE_DEPENDS "${eden}/src/audio_core/adsp/apps/audio_renderer/*.cpp")
list(LENGTH renderer_sources renderer_count)
list(LENGTH adsp_sources adsp_count)
if(NOT renderer_count EQUAL 67 OR NOT adsp_count EQUAL 2)
    message(FATAL_ERROR "Incomplete fixed renderer/ADSP source closure")
endif()
add_library(botw_audio_renderer_engine STATIC ${renderer_sources} ${adsp_sources}
    "${eden}/src/audio_core/audio_render_manager.cpp" "${eden}/src/audio_core/sink/sink_stream.cpp"
    renderer_memory.cpp renderer_host.cpp renderer_event.cpp common_ports.cpp wire.cpp audren_host.cpp
    "${BOTW_AUDIO_COMPONENT}/adapter/native_sink_stream.cpp")
target_compile_features(botw_audio_renderer_engine PUBLIC cxx_std_23)
target_compile_definitions(botw_audio_renderer_engine PUBLIC FMT_HEADER_ONLY=1)
target_include_directories(botw_audio_renderer_engine PUBLIC "${eden}/src"
    "${BOTW_AUDIO_COMPONENT}/native" "${BOTW_AUDIO_COMPONENT}/adapter"
    "${BOTW_AUDIO_COMPONENT}/vendor/fmt/include" "${BOTW_AUDIO_RENDERER_NATIVE_PCM_INCLUDE}")
target_link_libraries(botw_audio_renderer_engine PUBLIC botw_audio_renderer_platform
    "${BOTW_AUDIO_RENDERER_NATIVE_PCM_TARGET}" Threads::Threads)

if(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS)
    target_compile_options(botw_audio_renderer_engine INTERFACE
        -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
    target_link_options(botw_audio_renderer_engine INTERFACE
        -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
endif()
if(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE)
    add_executable(botw_owned_renderer_update "${BOTW_AUDIO_COMPONENT}/tests/renderer_update_probe.cpp")
    target_link_libraries(botw_owned_renderer_update PRIVATE
        "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>")
    add_executable(botw_owned_renderer_worker "${BOTW_AUDIO_COMPONENT}/tests/renderer_worker_probe.cpp")
    target_link_libraries(botw_owned_renderer_worker PRIVATE
        "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>")
    add_executable(botw_owned_audren_host "${BOTW_AUDIO_COMPONENT}/tests/audren_host_probe.cpp")
    target_link_libraries(botw_owned_audren_host PRIVATE
        "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>")
    add_executable(botw_owned_adsp_pause "${BOTW_AUDIO_COMPONENT}/tests/renderer_adsp_pause_probe.cpp")
    target_link_libraries(botw_owned_adsp_pause PRIVATE
        "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>")
    add_executable(botw_owned_renderer_wire "${BOTW_AUDIO_COMPONENT}/tests/wire_probe.cpp")
    target_link_libraries(botw_owned_renderer_wire PRIVATE
        "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>")
    enable_testing()
    add_test(NAME owned_complete_rev1_update COMMAND botw_owned_renderer_update)
    add_test(NAME owned_renderer_worker COMMAND botw_owned_renderer_worker)
    add_test(NAME owned_audren_host COMMAND botw_owned_audren_host)
    add_test(NAME owned_adsp_pause COMMAND botw_owned_adsp_pause)
    add_test(NAME owned_renderer_wire COMMAND botw_owned_renderer_wire)
    set_tests_properties(owned_complete_rev1_update owned_renderer_worker owned_audren_host
        owned_adsp_pause owned_renderer_wire PROPERTIES TIMEOUT 20)
endif()
