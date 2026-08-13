<script setup lang="ts">
/**
 * Replaces the default theme's site title with the application's brand lockup.
 *
 * VitePress renders either `themeConfig.logo` (an image) or `siteTitle` (text),
 * and the lockup is neither — it is text with the mark set inside it as a
 * glyph. `siteTitle: false` removes the stock title and this slot supplies the
 * real one.
 */
import DefaultTheme from "vitepress/theme-without-fonts";
import CographLogo from "./CographLogo.vue";

const { Layout } = DefaultTheme;
</script>

<template>
  <Layout>
    <template #nav-bar-title-before>
      <CographLogo />
    </template>
  </Layout>
</template>
