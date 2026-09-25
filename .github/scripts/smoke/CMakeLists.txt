cmake_minimum_required(VERSION 3.15)
project(TdJsonSmoke LANGUAGES C)
find_package(Td CONFIG REQUIRED)
add_executable(tdjson-smoke main.c)
target_link_libraries(tdjson-smoke PRIVATE Td::TdJson)
if(WIN32)
  add_custom_command(TARGET tdjson-smoke POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
      "$<TARGET_FILE_DIR:Td::tdjson>" "$<TARGET_FILE_DIR:tdjson-smoke>")
endif()
