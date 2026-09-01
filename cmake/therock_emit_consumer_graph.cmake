# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# therock_emit_consumer_graph(output_file)
#
# Serializes the consumer graph (from the THEROCK_ALL_SUBPROJECTS /
# THEROCK_DIRECT_CONSUMERS_OF_* global properties) to JSON at <output_file>. Call
# at the end of the top-level CMakeLists.txt, after all subprojects are declared.
#
# Schema (reverse-dependency edges, plus a source-subtree map):
#   {
#     "<subproject>": { "consumers": ["<consumer>", ...] }, ...,
#     "subtree_map": { "<category>/<name>": ["<key>", ...], ... }
#   }
#
# `subtree_map` maps an external-repo source subtree (an EXTERNAL_SOURCE_DIR made
# relative to THEROCK_ROCM_SYSTEMS_SOURCE_DIR / THEROCK_ROCM_LIBRARIES_SOURCE_DIR)
# to the one-or-more consumer-graph keys built from it. It lets the selector map
# external-repo CI inputs like `projects/clr` or `shared/rocroller` onto graph
# keys (`hip-clr`+`ocl-clr`, `rocroller`). Subprojects whose source is outside the
# two external monorepos get no entry. Reader side treats `subtree_map` as a
# reserved key, not a subproject node.
#
# The emit writes build/therock_consumer_graph.json, which refreshes the committed
# copy at test_tools/therock_consumer_graph.json. The committed copy is read
# directly by CI (no configure on the hot path) and kept honest by the drift check
# in .github/workflows/test_consumer_graph_drift.yml. It is generated-only; never
# hand-edit it.
function(therock_emit_consumer_graph output_file)
  get_property(_all GLOBAL PROPERTY THEROCK_ALL_SUBPROJECTS)

  set(_json "{\n")
  set(_first_proj TRUE)

  foreach(_proj IN LISTS _all)
    get_property(_consumers GLOBAL PROPERTY "THEROCK_DIRECT_CONSUMERS_OF_${_proj}")
    if(_consumers)
      list(REMOVE_DUPLICATES _consumers)
    endif()

    if(NOT _first_proj)
      string(APPEND _json ",\n")
    endif()
    set(_first_proj FALSE)

    string(TOLOWER "${_proj}" _key)

    # Build JSON array of consumers.
    set(_arr "")
    set(_first_c TRUE)
    foreach(_c IN LISTS _consumers)
      string(TOLOWER "${_c}" _cv)
      if(NOT _first_c)
        string(APPEND _arr ", ")
      endif()
      set(_first_c FALSE)
      string(APPEND _arr "\"${_cv}\"")
    endforeach()

    string(APPEND _json "  \"${_key}\": {\n")
    string(APPEND _json "    \"consumers\": [${_arr}]\n")
    string(APPEND _json "  }")
  endforeach()

  # --- subtree_map: source subtree -> [graph keys] ----------------------------
  # For each subproject target, resolve its EXTERNAL_SOURCE_DIR relative to the
  # external-repo roots. One subtree can map to many keys (hip-clr and ocl-clr
  # both build from projects/clr), so accumulate keys per subtree.
  set(_subtree_list "")
  foreach(_proj IN LISTS _all)
    get_target_property(_src "${_proj}" THEROCK_EXTERNAL_SOURCE_DIR)
    if(NOT _src)
      continue()
    endif()
    set(_subtree "")
    foreach(_root_var THEROCK_ROCM_SYSTEMS_SOURCE_DIR THEROCK_ROCM_LIBRARIES_SOURCE_DIR)
      if(NOT ${_root_var})
        continue()
      endif()
      cmake_path(IS_PREFIX ${_root_var} "${_src}" NORMALIZE _is_under)
      if(_is_under)
        cmake_path(RELATIVE_PATH _src BASE_DIRECTORY "${${_root_var}}" OUTPUT_VARIABLE _rel)
        set(_subtree "${_rel}")
        break()
      endif()
    endforeach()
    if(_subtree STREQUAL "")
      continue()
    endif()
    string(TOLOWER "${_proj}" _key)
    string(MAKE_C_IDENTIFIER "${_subtree}" _san)
    if(NOT DEFINED _subtree_name_${_san})
      set(_subtree_name_${_san} "${_subtree}")
      list(APPEND _subtree_list "${_subtree}")
    endif()
    list(APPEND _keys_for_${_san} "${_key}")
  endforeach()
  list(SORT _subtree_list)

  set(_map_json "")
  set(_first_s TRUE)
  foreach(_st IN LISTS _subtree_list)
    string(MAKE_C_IDENTIFIER "${_st}" _san)
    set(_keys ${_keys_for_${_san}})
    list(REMOVE_DUPLICATES _keys)
    list(SORT _keys)
    set(_karr "")
    set(_first_k TRUE)
    foreach(_k IN LISTS _keys)
      if(NOT _first_k)
        string(APPEND _karr ", ")
      endif()
      set(_first_k FALSE)
      string(APPEND _karr "\"${_k}\"")
    endforeach()
    if(NOT _first_s)
      string(APPEND _map_json ",\n")
    endif()
    set(_first_s FALSE)
    string(APPEND _map_json "    \"${_st}\": [${_karr}]")
  endforeach()

  string(APPEND _json ",\n")
  if(_map_json STREQUAL "")
    string(APPEND _json "  \"subtree_map\": {}")
  else()
    string(APPEND _json "  \"subtree_map\": {\n${_map_json}\n  }")
  endif()

  string(APPEND _json "\n}\n")
  file(WRITE "${output_file}" "${_json}")
  message(STATUS "Wrote consumer graph to ${output_file}")
endfunction()
