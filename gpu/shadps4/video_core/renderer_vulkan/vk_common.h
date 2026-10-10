// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

// Include vulkan-hpp header
#define VK_ENABLE_BETA_EXTENSIONS
#define VK_NO_PROTOTYPES
#define VULKAN_HPP_DISPATCH_LOADER_DYNAMIC 1
#define VULKAN_HPP_NO_CONSTRUCTORS
#define VULKAN_HPP_NO_STRUCT_SETTERS
#define VULKAN_HPP_HAS_SPACESHIP_OPERATOR
#define VULKAN_HPP_NO_EXCEPTIONS
// Define assert-on-result to nothing to instead return the result for our handling.
#define VULKAN_HPP_ASSERT_ON_RESULT

// vulkan.hpp adds fields to DispatchLoaderBase when NDEBUG is undefined, so the layout of
// vk::detail::DispatchLoaderDynamic depends on it. libbbgpu is built with NDEBUG while the
// renderer tests build with -UNDEBUG (their asserts must run); the inline init() copies the
// linker keeps would then read the dispatcher at offsets another translation unit did not write
// (a call through a null vkGetInstanceProcAddr). Include it with NDEBUG defined everywhere.
#ifndef NDEBUG
#define NDEBUG
#define BB_VK_COMMON_RESTORE_ASSERTS
#endif
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-value"
#include <vulkan/vulkan.hpp>
#pragma clang diagnostic pop
#ifdef BB_VK_COMMON_RESTORE_ASSERTS
#undef BB_VK_COMMON_RESTORE_ASSERTS
#undef NDEBUG
#include <cassert> // vulkan.hpp's own include of it just turned assert() off
#endif

#define VMA_STATIC_VULKAN_FUNCTIONS 0
#define VMA_DYNAMIC_VULKAN_FUNCTIONS 1
