cmake_minimum_required(VERSION 3.12)
project(switchboard_lvgl_sim C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -O2 -s USE_SDL=2")

# lv_conf.h lives next to this file -- lvgl's own CMakeLists auto-detects
# it there (env_support/cmake/os_desktop.cmake's default branch, since
# CMAKE_SOURCE_DIR == this directory).
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)

add_subdirectory(lvgl)

add_executable(index main.c ui.c)
target_link_libraries(index lvgl)

set(CMAKE_EXECUTABLE_SUFFIX ".html")
set_target_properties(index PROPERTIES LINK_FLAGS
    "--shell-file ${PROJECT_SOURCE_DIR}/shell.html \
     -s SINGLE_FILE=1 \
     -s ALLOW_MEMORY_GROWTH=1 \
     -s EXPORTED_RUNTIME_METHODS=ccall,cwrap,HEAPU8 \
     -s EXPORTED_FUNCTIONS=_main,_malloc,_sim_clear_personas,_sim_add_persona,_sim_set_status,_sim_set_transcript,_sim_set_llm_text")
