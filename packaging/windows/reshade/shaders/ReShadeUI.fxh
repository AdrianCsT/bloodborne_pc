/*
 * SPDX-License-Identifier: CC0-1.0
 *
 * The uniform annotation macros the shipped effects use (__UNIFORM_SLIDER_FLOAT1 and so on),
 * written for this package for ReShade 4.0.1 and newer. It replaces the header of the same name
 * that comes with the shader collections, which carries no licence notice.
 */

#pragma once

#if !defined(__RESHADE__) || __RESHADE__ < 40001
#error "ReShade 4.0.1+ is required to use this header file"
#endif

#define __UNIFORM_INPUT_ANY  ui_type = "input";
#define __UNIFORM_DRAG_ANY   ui_type = "drag";
#define __UNIFORM_SLIDER_ANY ui_type = "slider";
#define __UNIFORM_COMBO_ANY  ui_type = "combo";
#define __UNIFORM_COLOR_ANY  ui_type = "color";

#define __UNIFORM_INPUT_INT1   __UNIFORM_INPUT_ANY
#define __UNIFORM_INPUT_FLOAT1 __UNIFORM_INPUT_ANY
#define __UNIFORM_INPUT_FLOAT2 __UNIFORM_INPUT_ANY
#define __UNIFORM_INPUT_FLOAT3 __UNIFORM_INPUT_ANY
#define __UNIFORM_INPUT_FLOAT4 __UNIFORM_INPUT_ANY

#define __UNIFORM_DRAG_INT1   __UNIFORM_DRAG_ANY
#define __UNIFORM_DRAG_FLOAT1 __UNIFORM_DRAG_ANY
#define __UNIFORM_DRAG_FLOAT2 __UNIFORM_DRAG_ANY
#define __UNIFORM_DRAG_FLOAT3 __UNIFORM_DRAG_ANY
#define __UNIFORM_DRAG_FLOAT4 __UNIFORM_DRAG_ANY

#define __UNIFORM_SLIDER_INT1   __UNIFORM_SLIDER_ANY
#define __UNIFORM_SLIDER_INT2   __UNIFORM_SLIDER_ANY
#define __UNIFORM_SLIDER_INT3   __UNIFORM_SLIDER_ANY
#define __UNIFORM_SLIDER_FLOAT1 __UNIFORM_SLIDER_ANY
#define __UNIFORM_SLIDER_FLOAT2 __UNIFORM_SLIDER_ANY
#define __UNIFORM_SLIDER_FLOAT3 __UNIFORM_SLIDER_ANY
#define __UNIFORM_SLIDER_FLOAT4 __UNIFORM_SLIDER_ANY

#define __UNIFORM_COMBO_INT1   __UNIFORM_COMBO_ANY
#define __UNIFORM_COMBO_FLOAT1 __UNIFORM_COMBO_ANY

#define __UNIFORM_COLOR_FLOAT3 __UNIFORM_COLOR_ANY
#define __UNIFORM_COLOR_FLOAT4 __UNIFORM_COLOR_ANY
