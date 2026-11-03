cmake_minimum_required(VERSION 3.16)
project(BaofangZhanshenSync VERSION 0.1.0 LANGUAGES CXX)

add_library(baofang_sync SHARED native/src/baofang_sync.cpp)
target_include_directories(baofang_sync PUBLIC native/include)
target_compile_features(baofang_sync PUBLIC cxx_std_17)
target_compile_definitions(baofang_sync PRIVATE BF_SYNC_BUILD)
set_target_properties(baofang_sync PROPERTIES
    CXX_EXTENSIONS OFF
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES)

if(MSVC)
    target_compile_options(baofang_sync PRIVATE /W4)
else()
    target_compile_options(baofang_sync PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(APPLE)
    enable_language(C)
    enable_language(OBJCXX)
    add_library(baofang_overlay SHARED native/src/baofang_overlay.mm)
    target_include_directories(baofang_overlay PUBLIC native/include)
    target_compile_features(baofang_overlay PUBLIC cxx_std_17)
    target_compile_options(baofang_overlay PRIVATE -fobjc-arc -Wall -Wextra -Wpedantic)
    target_link_libraries(baofang_overlay PRIVATE "-framework AppKit" "-framework CoreGraphics")
    set_target_properties(baofang_overlay PROPERTIES
        OBJCXX_STANDARD 17
        OBJCXX_EXTENSIONS OFF
        CXX_VISIBILITY_PRESET hidden
        OBJCXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN YES)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(test_sync tests/native/test_sync.cpp)
    target_link_libraries(test_sync PRIVATE baofang_sync)
    add_test(NAME native_sync COMMAND test_sync)
    if(APPLE)
        add_executable(test_overlay tests/native/test_overlay.mm)
        target_compile_options(test_overlay PRIVATE -fobjc-arc)
        target_link_libraries(test_overlay PRIVATE baofang_overlay "-framework AppKit")
        set_target_properties(test_overlay PROPERTIES OBJCXX_STANDARD 17 OBJCXX_EXTENSIONS OFF)
        add_test(NAME native_overlay COMMAND test_overlay)
        add_executable(test_overlay_c tests/native/test_overlay_c.c)
        target_link_libraries(test_overlay_c PRIVATE baofang_overlay)
        target_compile_options(test_overlay_c PRIVATE -Wall -Wextra -Wpedantic)
        set_target_properties(test_overlay_c PROPERTIES C_STANDARD 99 C_EXTENSIONS OFF)
        add_test(NAME native_overlay_c99 COMMAND test_overlay_c)
    endif()
endif()
